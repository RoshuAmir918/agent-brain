"""Nightly memory mechanics: one idempotent pass over the vault. Git is the ledger.

Without --write the pass runs on a temporary copy of the agent tree and prints
what it would change. Every step is a function of the vault's current files, so
rerunning a night, or rerunning after an interrupted run, converges on the same
result. The only things that advance are the dates in frontmatter and the log.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import activation as act
from . import attribute, compile_context, consults, fm, image, journal, ledger, needs, reception, triggers, validate
from .tasks import Task, bounded_hot_body, is_open, load_tasks, parse_register, task_activation, write_archive
from .vault import DAILY, Vault
from .config import Config
from .lock import vault_lock
from . import runtime, works

MARK_START = "<!-- brain:active-threads start -->"
MARK_END = "<!-- brain:active-threads end -->"


NIGHT_KEYS = ("uses", "uses_inferred", "tier", "activation")   # the frontmatter the night owns on every note


def mechanical(vault: Vault, rel: str) -> bool:
    """Notes whose frontmatter the script owns: not logs, dreams or immutable daily archives."""
    return not DAILY.fullmatch(rel) and vault.kind_for(rel) not in ("log", "dream")


# ---------- frontmatter defaults

def ensure_frontmatter(vault: Vault, today: date, report: list[str]) -> set[str]:
    changed = set()
    for rel, n in vault.notes.items():
        if not mechanical(vault, rel):
            continue
        kind = vault.kind_for(rel)
        d = dict(n.data)
        before = dict(d)
        d.setdefault("kind", kind if kind != "hub" else d.get("kind", "hub"))
        if kind == "orientation":
            d.setdefault("pinned", True)
            d.setdefault("tier", "hot")
        else:
            d.setdefault("pinned", False)
            d.setdefault("tier", "warm")
        if "uses" not in d and kind in ("hub", "profile", "reflection", "episode", "primed", "draft"):
            d["uses"] = [today.isoformat()]  # a note's creation is its first use
        d.setdefault("uses", [])
        d.setdefault("uses_inferred", [])
        if kind == "episode" and rel.startswith("Conversations/"):
            d.setdefault("consolidated", None)
        if d != before:
            n.data = d
            changed.add(rel)
            report.append(f"frontmatter: {rel} += {sorted(set(d) - set(before))}")
    if vault.config.edition == "personal":   # who the owner is: for the owner's eyes, never an admin's view of the brain
        facets = image.folder(vault.config).relative_to(vault.config.vault_path).as_posix() + "/"
        for rel in (vault.config.human_profile, vault.config.human_portrait, vault.config.human_goals, vault.config.human_tellable,
                    *sorted(rel for rel in vault.notes if rel.startswith(facets))):
            n = vault.notes.get(rel)
            if n is not None and "audience" not in n.data:
                n.data = {**n.data, "audience": "owner"}
                changed.add(rel)
                report.append(f"frontmatter: {rel} += ['audience']")
    return changed


# ---------- use log

def fold_note_use(vault: Vault, use: attribute.Use, explicit: set,
                  inferred_dates: set, changed: set[str]) -> bool:
    note = vault.notes.get(use.target)
    if not note or not mechanical(vault, use.target):
        return False
    day = use.ts[:10]
    if use.source == "observed" and (day, use.target) in explicit:
        current = [str(value) for value in note.data.get("uses_inferred", [])]
        if day in current and (day, use.target) not in inferred_dates:
            note.data["uses_inferred"] = [value for value in current if value != day]
            changed.add(use.target)
        return False
    key = "uses_inferred" if use.source in ("inferred", "observed") else "uses"
    current = [str(value) for value in note.data.get(key, [])]
    if day not in current:
        note.data[key] = sorted(set(current + [day]), reverse=True)[:30]
        changed.add(use.target)
    return True


def fold_uses(vault: Vault, tasks: list[Task], today: date, report: list[str]) -> set[str]:
    """Credit every eligible log row to its target. Date sets make this idempotent."""
    changed, credited, ignored = set(), 0, 0
    by_id = {t.tid: t for t in tasks}
    log_dir = vault.root / vault.config.agent("Use Log")
    rows = [use for path in sorted(log_dir.glob("*.md")) for use in attribute.read_log(path)]
    explicit = {(use.ts[:10], use.target) for use in rows if use.source == "explicit"}
    inferred_dates = {(use.ts[:10], use.target) for use in rows if use.source == "inferred"}
    for use in rows:
        day = use.ts[:10]
        inferred = use.source == "inferred"
        if (day > today.isoformat() or use.target == "MISS" or use.target.startswith("TR-")
                or (use.source == "live" and use.status == "unconfirmed")
                or (use.source == "observed" and use.target.startswith("T-"))):
            ignored += 1
            continue
        if use.target.startswith("T-"):
            if use.target in by_id:
                by_id[use.target].add_use(day, inferred)
                credited += 1
            else:
                ignored += 1
            continue
        if fold_note_use(vault, use, explicit, inferred_dates, changed):
            credited += 1
        else:
            ignored += 1
    report.append(f"fold: {credited} rows credited, {ignored} ignored")
    return changed


# ---------- activation and tiers

def retier(vault: Vault, today: date, report: list[str]) -> set[str]:
    changed = set()
    h, w = vault.config.context.hot_threshold, vault.config.context.warm_threshold
    for rel, n in vault.notes.items():
        kind = vault.kind_for(rel)
        if not mechanical(vault, rel) or kind == "orientation":
            continue
        ages = act.ages_from_dates([str(x) for x in n.data.get("uses", [])], today)
        iages = act.ages_from_dates([str(x) for x in n.data.get("uses_inferred", [])], today)
        b = act.activation(ages, iages)
        if n.data.get("tier") == "private":
            tier = "private"   # the export's only privacy marker: activation still moves, the tier never does
        elif kind == "episode" and rel.startswith("Conversations/") and not n.data.get("consolidated"):
            tier = "hot" if act.tier_for(b, h=h, w=w) == "hot" else "warm"   # unconsolidated records cannot go cold
        elif kind == "primed" and not n.data.get("pinned"):
            try:
                expired = date.fromisoformat(Path(rel).stem) < today
            except ValueError:
                expired = False
            tier = "cold" if expired else act.tier_for(b, h=h, w=w)
        else:
            tier = act.tier_for(b, bool(n.data.get("pinned")), h, w)
        newb = round(b, 2) if b != float("-inf") else -99.0
        if n.data.get("tier") != tier or n.data.get("activation") != newb:
            if n.data.get("tier") != tier:
                report.append(f"tier: {rel} {n.data.get('tier')} -> {tier} (B={newb})")
            n.data["tier"] = tier
            n.data["activation"] = newb
            changed.add(rel)
    return changed


# ---------- triggers

def fire_triggers(vault: Vault, tasks: list[Task], today: date, report: list[str]) -> str | None:
    """Process Triggers.md as it is on disk now, crediting fired commitments to tasks. The caller holds the vault lock
    and writes the new body only after the tasks, so an interrupted night retries a trigger instead of losing its use.
    Returns the new body, or None when nothing changed."""
    path = vault.root / vault.config.agent("Triggers.md")
    if not path.exists():
        return None
    body = fm.read(path)[1]
    new, rep = triggers.process(body, today, {rel: n.data.get("tier") for rel, n in vault.notes.items()}, vault.config)
    for fired in rep["fired"]:
        if fired.cls == "commitment":
            ids = set(re.findall(r"\bT-\d{3}\b", fired.source))
            for task in tasks:
                if task.tid in ids:
                    task.add_use(today.isoformat())
        report.append(f"trigger fired: {fired.id} ({fired.do})")
    for t in rep["mentioned"]:
        report.append(f"trigger mentioned once, source cold: {t.id}")
    for t in rep["retired"]:
        report.append(f"trigger retired: {t.id}")
    return None if new == body else new


def place(config: Config, t: Task, today: date, cold_open: list, report: list[str]) -> str:
    """Tier one open task and return its register text: full entry, one line, or nothing."""
    prev = t.state.get("tier")
    if prev is None and not t.uses and not t.uses_inferred:
        t.add_use(today.isoformat())   # a task the night has never placed is new: it starts warm, not cold
    b = task_activation(t, today)
    # A handoff being worked is active work whatever its uses say: it stays in the register where the owner reads it.
    pinned = t.state.get("pinned") == "true" or bool(t.handoff and t.task_state != "set_aside")
    tier = act.tier_for(b, pinned, config.context.hot_threshold, config.context.warm_threshold)
    t.state["tier"] = tier
    t.state["activation"] = f"{b:.2f}" if b != float("-inf") else "-99"
    if tier == "hot":
        bounded = bounded_hot_body(t, config)   # raises when it would have to guess; process_tasks leaves the entry as is
        if bounded != t.body:
            write_archive(config, t, "hot but over the register budget", today)
            t.body = bounded
            report.append(f"task: {t.tid} split, older evidence archived")
        rendered = t.render()
    elif tier == "warm":
        write_archive(config, t, "warm", today)
        rendered = t.render_line(config)
    else:
        write_archive(config, t, "cold", today)
        cold_open.append((t.tid, t.title))
        rendered = ""
    if prev != tier:
        report.append(f"task: {t.tid} {prev or 'untiered'} -> {tier}")
    return rendered


def process_tasks(config: Config, today: date, tasks: list[Task], archived: list[Task], report: list[str]) -> tuple[list[Task], list[tuple[str, str]]]:
    reg = config.vault_path / config.agent("Tasks.md")
    data, body = fm.read(reg)
    spans, _ = parse_register(body)
    by_id = {t.tid: t for t in tasks}
    first = {}
    for i, (tid, _, _) in enumerate(spans):
        first.setdefault(tid, i)
    cold_open, closed, out = [], [], body
    for i, (tid, s, e) in sorted(enumerate(spans), key=lambda x: -x[1][1]):
        t = by_id[tid]
        if first[tid] != i:
            rendered = ""
            report.append(f"task: {tid} duplicate register entry removed")
        elif not is_open(t):
            rendered = ""   # a closed task leaves the register: its entry to its archive file, a line to the month's
            write_archive(config, t, t.task_state or "closed", today)
            closed.append(t)
        else:
            try:
                rendered = place(config, t, today, cold_open, report)
            except ValueError as error:
                # Bounding it would mean guessing: the entry stays exactly as it is for Sleep 1, and the night goes on.
                report.append(f"task: needs judgment, left as is: {error}")
                continue
        chunk = rendered + ("\n" if rendered else "")
        if out[s:e] != chunk:
            out = out[:s] + chunk + out[e:]
    restored = []
    for t in archived:
        try:
            rendered = place(config, t, today, cold_open, report)
        except ValueError as error:
            if not (t.handoff and t.task_state != "set_aside"):
                report.append(f"task: needs judgment, left in the archive: {error}")
                continue
            # A handoff being worked is listed in the register even when its entry needs trimming: as a warm line.
            report.append(f"task: needs judgment, listed warm: {error}")
            t.state["tier"] = "warm"
            write_archive(config, t, "warm", today)
            rendered = t.render_line(config)
        if rendered:
            restored.append(rendered)
            report.append(f"task: {t.tid} restored to the register ({t.state['tier']})")
    if restored:
        anchor = re.search(r"^## Active work\s*$", out, re.M)
        if anchor:
            nxt = re.compile(r"^## ", re.M).search(out, anchor.end())
            pos = nxt.start() if nxt else len(out)
            out = out[:pos].rstrip("\n") + "\n\n" + "\n".join(restored) + "\n" + out[pos:]
        else:
            out = out.rstrip("\n") + "\n\n" + "\n".join(restored) + "\n"
    if out != body:
        fm.write(reg, data, out)
    if closed:
        month = config.vault_path / config.agent("Tasks Archive") / f"{today.strftime('%Y-%m')}.md"
        with month.open("a", encoding="utf-8") as stream:
            for t in sorted(closed, key=lambda t: t.tid):
                end = re.search(r"^[ \t]*-[ \t]+\*\*(?:Completed|Dropped):\*\*[ \t]*(.+?)[ \t]*$", t.body, re.M)
                stream.write(f"- **{t.tid} — {t.title}** · {t.task_state}" + (f" {end[1]}" if end else "")
                             + f" · [[{config.link(config.agent('Tasks Archive/' + t.tid))}|entry]]\n")
                report.append(f"task: {t.tid} {t.task_state}, moved to the archive")
        swept = needs.settle_tasks(config, today, [(t.tid, t.body) for t in closed], "the task closed")
        report.extend(f"needs-you: resolved, its task closed: {text}" for text in swept)
    return [t for t in tasks + archived if is_open(t)], sorted(cold_open)


def rebuild_current(config: Config, tasks: list[Task], today: date, report: list[str]) -> None:
    cur = config.vault_path / "CURRENT.md"
    data, body = fm.read(cur)
    if MARK_START not in body or MARK_END not in body:
        report.append("current: markers missing; active-threads section not rebuilt")
        return
    hot = [t for t in tasks if t.state.get("tier") == "hot"]
    lines = [MARK_START, f"<!-- generated {today.isoformat()} from the open-task register; edits here are overwritten nightly -->"]
    for t in hot:
        lines.append(f"- **{t.tid} — {t.title}:** {t.first_status()} [[{config.link(config.agent('Tasks'))}#{t.tid} — {t.title}|{t.tid}]]")
    lines.append(MARK_END)
    pre, rest = body.split(MARK_START, 1)
    _, post = rest.split(MARK_END, 1)
    out = pre + "\n".join(lines) + post
    if out != body:
        fm.write(cur, data, out)
        report.append(f"current: active threads rebuilt ({len(hot)} hot tasks)")


# ---------- files

def rollover(config: Config, today: date, report: list[str]) -> None:
    root = config.vault_path
    ym = today.strftime("%Y-%m")
    files = {
        root / config.agent("Use Log") / f"{ym}.md": attribute.HEADER.format(month=ym),
        root / config.agent("Sleep Log") / f"{ym}.md": f"# Sleep log — {ym}\n\nOne entry per nightly run. Written by the script and the sleep routines.\n\n",
        root / config.agent("Tasks Archive") / f"{ym}.md": f"# Completed tasks — {ym}\n\nTasks with verified completion evidence, moved from the register. Per-task full entries live beside this file as `T-nnn.md`.\n\n",
    }
    for p, tmpl in files.items():
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(tmpl, encoding="utf-8")
            report.append(f"rollover: created {p.relative_to(root).as_posix()}")


def sleep_log(config: Config, today: date, lines: list[str], mode: str) -> None:
    p = config.vault_path / config.agent("Sleep Log") / f"{today.strftime('%Y-%m')}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(f"# Sleep log — {today.strftime('%Y-%m')}\n\n", encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n## {today.isoformat()} nightly script ({mode})\n\n")
        for ln in lines:
            f.write(f"- {ln}\n")


# ---------- the pass


def ask_library(config: Config, today: date, report: list[str], records_path: Path | None = None) -> bool:
    """Ask once per unopened draft. Caller holds the vault lock, including for the records write."""
    home = runtime.configured()
    if home is None:
        return False
    eligible = [work for work in works.parse(works.library() or "")
                if work.status == "draft" and work.editable and work.opened == "never"
                and work.created is not None and (today - work.created).days >= 7]
    if not eligible:
        return False
    path = records_path if records_path is not None else home / "records/library/asked.json"
    try:
        asked = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(asked, dict):
            raise ValueError("expected an object keyed by work slug")
    except FileNotFoundError:
        asked = {}
    except (OSError, ValueError) as error:
        report.append(f"library: asked record unreadable, skipped asks: {error}")
        return False
    current = config.vault_path / "CURRENT.md"
    existing = {item.source: item.since for item in needs.open_asks(fm.read(current)[1])} if current.exists() else {}
    changed = False
    for work in eligible:
        if work.slug in asked:
            continue
        # CURRENT is a real vault note; the Library anchor identifies the work without creating a broken link.
        source = f"CURRENT#Library/{work.slug}"
        if source not in existing:
            text = f"Still want {work.title} in your Library? I can keep it, keep it updated, or put it away."
            journal.add_ask(config, today, text, source, distinct_source=True)
            report.append(f"needs-you: Library ask: {work.slug}")
            changed = True
        # Recover an interruption after CURRENT was saved but before its record was written.
        asked[work.slug] = existing.get(source) or today.isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(path, json.dumps(asked, indent=2, sort_keys=True) + "\n")
    return changed


STAGES = ("all", "before", "after")


def run(root: Path, today: date, sessions_dir: Path | None = None, *, library_records: Path | None = None,
        evidence_home: Path | None = None, stage: str = "all") -> list[str]:
    """Apply the night to the vault at root. Returns the report; exits 1 on validation failure.

    `before` is everything the model passes read (uses, tiers, triggers, tasks, expiry, the sources' evidence);
    `after` is everything compiled from what they wrote (the portrait, CURRENT's threads, the context)."""
    vault = Vault(root)
    config = vault.config
    report: list[str] = []
    changed: set[str] = set()
    if stage != "after":
        changed |= _before(vault, today, sessions_dir, library_records, evidence_home, report)
    if stage != "before":
        changed |= _after(vault, today, report)
    vault.load()
    problems = validate.run(vault, changed | vault.config.generated_views)
    if problems:
        report += [f"VALIDATION: {p}" for p in problems]
        sleep_log(config, today, report, "write, validation failed, not committed")
        for ln in report:
            print(ln)
        sys.exit(1)
    hygiene = validate.hygiene(vault)
    if hygiene:
        report.append(f"checks: {len(hygiene)} problem(s) beyond structure; `brain validate` names them, and the evening fixes the Needs you ones")
    sleep_log(config, today, report, "write" if stage == "all" else f"write, {stage}")
    return report


def _before(vault: Vault, today: date, sessions_dir: Path | None, library_records: Path | None,
            evidence_home: Path | None, report: list[str]) -> set[str]:
    config = vault.config
    rollover(config, today, report)
    vault.load()
    changed = ensure_frontmatter(vault, today, report)
    tasks, archived = load_tasks(config, report)
    if sessions_dir is not None:
        report.extend(f"attribute: {use.line()}" for use in attribute.sessions(vault, sessions_dir, write=True))
    changed |= fold_uses(vault, tasks + archived, today, report)
    changed |= retier(vault, today, report)
    with vault_lock(config):
        # Until here the night changes only frontmatter (uses, tiers). Other writers change bodies and other keys at any
        # hour (an ask reaching CURRENT, a worker's task write): keep each note as it is on disk now, set the night's keys.
        for rel in changed:
            path = vault.root / rel
            if not path.exists():
                continue
            data, body = fm.read(path)
            ours = vault.notes[rel].data
            night = {key: value for key, value in ours.items() if key in NIGHT_KEYS or key not in data}
            if data.get("tier") == "private":
                night.pop("tier", None)
            fm.write(path, {**data, **night}, body)
    with vault_lock(config):
        # Workers write tasks day and night: process the register as it is now, adding the uses credited above.
        credited = {t.tid: t.state for t in tasks + archived}
        tasks, archived = load_tasks(config)
        for t in tasks + archived:
            for key in ("uses", "inferred"):
                days = {x for value in (t.state.get(key, ""), credited.get(t.tid, {}).get(key, "")) for x in value.split(",") if x}
                if days:
                    t.state[key] = ",".join(sorted(days, reverse=True)[:30])
        fired = fire_triggers(vault, tasks + archived, today, report)
        process_tasks(config, today, tasks, archived, report)
        if fired is not None:   # consumed only after the task uses it supplied are saved
            rel = config.agent("Triggers.md")
            fm.write(vault.root / rel, fm.read(vault.root / rel)[0], fired)
            changed.add(rel)
    with vault_lock(config):   # the after-session writer adds asks to CURRENT at any hour
        from . import goals
        try:
            expired_goals = goals.decay(config, today, report, vault=vault)
        except Exception as error:
            expired_goals = []
            report.append(f"goal: decay skipped: {error}")
        if expired_goals:
            changed.add(config.human_goals)
            report.extend(f"goal: {gid} set aside after 30 days without evidence" for gid in expired_goals)
        if ask_library(config, today, report, library_records):
            changed.add("CURRENT.md")
        removed = needs.expire_confirms(config, today)
        expired_consults = consults.expire_confirms(config, today)
        retired = needs.retire_asks(config, today)
        settled = reception.settle(config, today)
    report.extend(f"reception: nothing heard in {reception.SETTLE_DAYS} days, {row.outcome}: {row.kind} {row.what}"
                  for row in settled)
    if expired_consults:
        changed.add(config.agent("Consults.md"))
        report.extend(f"consult: confirm accepted after {needs.CONFIRM_DAYS} days: {text}" for text in expired_consults)
    if removed or retired:
        changed.add("CURRENT.md")
        report.extend(f"needs-you: confirm accepted after {needs.CONFIRM_DAYS} days: {text}" for text in removed)
        report.extend(f"needs-you: ask unanswered {needs.RETIRE_DAYS} days, retired: {text}" for text in retired)
    with vault_lock(config):
        collapsed = ledger.collapse(config, today)
    for path in collapsed:
        rel = path.relative_to(config.vault_path).as_posix()
        changed.add(rel)
        report.append(f"ledger: collapsed nothing rows in {rel}")
    from . import sources
    report.extend(sources.nightly(vault, today, evidence_home))
    return changed


def _after(vault: Vault, today: date, report: list[str]) -> set[str]:
    config = vault.config
    changed: set[str] = set()
    with vault_lock(config):   # the model passes and workers change the register: thread what is open now
        rebuild_current(config, [t for t in load_tasks(config)[0] if is_open(t)], today, report)
    changed.add("CURRENT.md")
    if stamped := image.stamp(config):
        changed.add(config.human_portrait)
        report.append(f"portrait: {stamped} new line id(s)")
    with vault_lock(config):
        try:
            if (compiled := image.compile(config)) is not None:
                changed.add(config.human_portrait)
                report.append(f"portrait: compiled {compiled} line(s) from the facets")
        except ValueError as error:
            report.append(f"portrait: not compiled: {error}")
    vault.load()
    report.append(compile_context.write(vault))
    return changed


def preview(root: Path, today: date, sessions_dir: Path | None = None, stage: str = "all") -> list[str]:
    """Run the full pass on a disposable copy of the agent tree; human notes are linked for resolution only."""
    with tempfile.TemporaryDirectory(prefix="brain-preview-") as tmp:
        config = Config.load(root)
        shadow = Path(tmp) / config.vault
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, shadow, ignore=shutil.ignore_patterns(".git", ".brain", ".obsidian", "__pycache__", ".pytest_cache"))
        config_path = config.repo / "brain.toml"
        if config_path.exists():
            (Path(tmp) / "brain.toml").write_bytes(config_path.read_bytes())
        else:
            (Path(tmp) / "brain.toml").write_text("[brain]\n", encoding="utf-8")
        for sibling in (config.sources_dir, config.archive_dir):
            src = config.repo / sibling
            if src.exists():
                target = Path(tmp) / sibling
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(src, target_is_directory=True)
        # Preview reads the real listing but only marks asks in its disposable records copy.
        library_records = Path(tmp) / "library/asked.json"
        if (home := runtime.configured()) is not None:
            try:
                saved = (home / "records/library/asked.json").read_bytes()
            except FileNotFoundError:
                pass
            else:
                library_records.parent.mkdir(parents=True, exist_ok=True)
                library_records.write_bytes(saved)
        # and keeps what the sources find in the copy, never the agent's evidence
        return run(shadow, today, sessions_dir, library_records=library_records, evidence_home=Path(tmp) / "agent", stage=stage)
