"""Nightly memory mechanics: one idempotent pass over the vault. Git is the ledger.

Usage:
  brain nightly [--vault <path>] [--date YYYY-MM-DD] [--write] [--commit] [--push]
  brain validate [--vault <path>]
  brain attribute [--vault <path>] --file <record.md> --ts <iso> [--reason <text>] [--write]
  brain attribute [--vault <path>] [--sessions-dir <dir>] [--write]
  brain digest [--vault <path>] --date YYYY-MM-DD [--sessions-dir <dir>] [--write] [--max-words 25000]
  (sessions default to $AGENT_HOME/records/sessions, the profile's own export)
  brain audit [--vault <path>] [--date YYYY-MM-DD] [--days 14] [--sessions-dir <dir>] [--json]
  brain compile-context [--vault <path>] [--out <path>] [--write] [--sections start,current,...]
  brain retrieve [--vault <path>] <query> [--limit 12] [--json [--form names|snippet|full]]
  brain needs-you [--vault <path>] [--json]
  brain ideas [--vault <path>] [--all] [--json]
  brain loops [--vault <path>] [--json | --due] [--quiet]
  brain watch-signal [--vault <path>]
  brain continue-signal [--vault <path>]
  brain access <who> [--vault <path>] [--json]
  brain state [--vault <path>] [--json]
  brain session-context [--vault <path>] [--platform <name>] [--user-id <id>] [--user-id-alt <id>] [--user-name <name>] [--chat-id <id>]
  brain simulate [--h 7 --w 28]
  brain install-skills [--vault <path>]
  brain install-scripts
  brain export-sessions [--date YYYY-MM-DD]
  brain version
  brain paths <soul|skills|scripts|vault|repo> [--vault <path>]

Without --write the pass runs on a temporary copy of the agent tree and prints
what it would change. Every step is a function of the vault's current files, so
rerunning a night, or rerunning after an interrupted run, converges on the same
result. The only things that advance are the dates in frontmatter and the log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

from . import activation as act
from . import access, attribute, audit, compile_context, digest, fm, health, ideas, journal, ledger, loops, needs, retrieve, session_context, state, triggers, validate
from . import tasks as tasks_mod
from .tasks import Task, bounded_hot_body, is_open, load_tasks, parse_register, quiet, task_activation, write_archive
from .vault import DAILY, Vault
from .config import FORMS, SECTIONS, Config
from .lock import vault_lock
from . import install
from . import signals
from . import record, runtime, schedule, sessions

MARK_START = "<!-- brain:active-threads start -->"
MARK_END = "<!-- brain:active-threads end -->"


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
        if kind == "episode" and rel.startswith("Conversations/") and not n.data.get("consolidated"):
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

def fire_triggers(vault: Vault, tasks: list[Task], today: date, report: list[str]) -> set[str]:
    note = vault.notes.get(vault.config.agent("Triggers.md"))
    if note is None:
        return set()
    new, rep = triggers.process(note.body, today, {rel: n.data.get("tier") for rel, n in vault.notes.items()}, vault.config)
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
    if new == note.body:
        return set()
    note.body = new
    return {vault.config.agent("Triggers.md")}


# ---------- tasks

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

def run(root: Path, today: date, sessions_dir: Path | None = None) -> list[str]:
    """Apply the whole night to the vault at root. Returns the report; exits 1 on validation failure."""
    report: list[str] = []
    vault = Vault(root)
    config = vault.config
    for name in ("Hot Set.md", "Warm Index.md"):
        path = root / name
        if path.exists():
            path.unlink()
            report.append(f"index: removed generated {name}")
    rollover(config, today, report)
    vault.load()
    changed = ensure_frontmatter(vault, today, report)
    tasks, archived = load_tasks(config, report)
    if sessions_dir is not None:
        report.extend(f"attribute: {use.line()}" for use in attribute.sessions(vault, sessions_dir, write=True))
    changed |= fold_uses(vault, tasks + archived, today, report)
    changed |= retier(vault, today, report)
    changed |= fire_triggers(vault, tasks + archived, today, report)
    # Persist task uses before consuming the triggers that supplied them. If
    # task processing is interrupted, the still-active trigger can safely retry.
    with vault_lock(config):
        # Until here the night changes only frontmatter (uses, tiers). Other writers change bodies at any hour (an ask
        # reaching CURRENT, a worker's task write): keep each body as it is on disk now, save our frontmatter.
        for rel in changed - {vault.config.agent("Triggers.md")}:
            fm.write(vault.root / rel, vault.notes[rel].data, fm.read(vault.root / rel)[1])
    with vault_lock(config):
        # Workers write tasks day and night: process the register as it is now, carrying the uses credited above.
        credited = {t.tid: t.state for t in tasks + archived}
        tasks, archived = load_tasks(config)
        for t in tasks + archived:
            for key in ("uses", "inferred"):
                if credited.get(t.tid, {}).get(key):
                    t.state[key] = credited[t.tid][key]
        open_tasks, _ = process_tasks(config, today, tasks, archived, report)
    if vault.config.agent("Triggers.md") in changed:
        vault.notes[vault.config.agent("Triggers.md")].save()
    with vault_lock(config):   # the after-session writer adds asks to CURRENT at any hour
        rebuild_current(config, open_tasks, today, report)
        removed = needs.expire_confirms(config, today)
        retired = needs.retire_asks(config, today)
    if removed or retired:
        changed.add("CURRENT.md")
        report.extend(f"needs-you: confirm accepted after 7 days: {text}" for text in removed)
        report.extend(f"needs-you: ask unanswered {needs.RETIRE_DAYS} days, retired: {text}" for text in retired)
    with vault_lock(config):
        collapsed = ledger.collapse(config, today)
    for path in collapsed:
        rel = path.relative_to(config.vault_path).as_posix()
        changed.add(rel)
        report.append(f"ledger: collapsed nothing rows in {rel}")
    vault.load()
    report.append(compile_context.write(vault))
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
    sleep_log(config, today, report, "write")
    return report


def preview(root: Path, today: date, sessions_dir: Path | None = None) -> list[str]:
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
        return run(shadow, today, sessions_dir)


# ---------- git

def sh(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(root: Path, message: str, paths: list[str] | None = None) -> str:
    """Stage and commit under the repository lock: the given paths, or else the agent's notes and session context.
    Returns the short SHA, or '' when there was nothing to commit."""
    from .lock import repo_lock
    config = Config.load(root)
    repo = config.repo
    with repo_lock(config):
        if paths:
            sh(repo, "add", "-A", "--", *[str(Path(p).resolve()) for p in paths])
        else:
            tree = root.relative_to(repo).as_posix()
            sh(repo, "add", "-A", "--", tree, f":(exclude){tree}/.brain", ".context.md")
        if not sh(repo, "diff", "--cached", "--name-only"):
            return ""
        sh(repo, "commit", "-q", "-m", message)
        return sh(repo, "rev-parse", "--short", "HEAD")


def push(root: Path) -> None:
    from .lock import repo_lock
    config = Config.load(root)
    repo = config.repo
    with repo_lock(config):
        try:
            sh(repo, "pull", "--rebase", "--autostash", "-q", "origin", "main")   # rows written after the commit wait
        except subprocess.CalledProcessError:
            # Only a rebase that stopped is a conflict; anything else (no credentials, no network) is git's own error,
            # already printed above, and is said as such.
            git_dir = repo / ".git"
            if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
                subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)
                sys.exit("pull --rebase conflicted; resolve it by hand, nothing was pushed")
            sys.exit("pull failed (git's error is above: credentials or network, not a conflict); nothing was pushed")
        try:
            sh(repo, "push", "-q", "origin", "HEAD:main")
        except subprocess.CalledProcessError:
            sys.exit("push failed (git's error is above); the commit is kept locally")


def cmd_pull(a):
    """`brain pull`: bring the vault up to date under the repository lock."""
    from .lock import repo_lock
    repo = a.config.repo
    with repo_lock(a.config):
        try:
            sh(repo, "pull", "--rebase", "--autostash", "-q", "origin", "main")
        except subprocess.CalledProcessError:
            git_dir = repo / ".git"
            if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
                subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)
                sys.exit("pull --rebase conflicted; resolve it by hand")
            sys.exit("pull failed (git's error is above: credentials or network, not a conflict)")
    print("up to date")


def cmd_commit(a):
    """`brain commit -m MESSAGE [--push] [PATH ...]`: the one way a run commits the vault, so parallel runs take turns."""
    try:
        sha = commit(a.config.vault_path, a.message, a.paths or None)
    except subprocess.CalledProcessError as error:
        print(f"commit failed: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"committed {sha}" if sha else "nothing to commit")
    if a.push:
        push(a.config.vault_path)
        print("pushed")


# ---------- commands

def cmd_nightly(a):
    root = a.config.vault_path
    today = date.fromisoformat(a.date) if a.date else a.config.today()
    sessions_dir = Path(a.sessions_dir).resolve() if a.sessions_dir else None
    report = run(root, today, sessions_dir) if a.write else preview(root, today, sessions_dir)
    for ln in report:
        print(ln)
    if not a.write:
        print("(report only; nothing written)")
        return
    if a.commit:
        summary = "; ".join(ln for ln in report if not ln.startswith("frontmatter"))[:900]
        sha = commit(root, f"sleep1({today.isoformat()}): {summary or 'no changes'}")
        print(f"commit {sha}" if sha else "nothing to commit")
        if a.push and sha:
            push(root)
            print("pushed")


def cmd_ledger_append(args):
    try:
        if args.json:
            if any(value is not None for value in (args.routine, args.skill, args.outcome, args.account, args.next, args.at)):
                raise ValueError("--json cannot be combined with row arguments")
            values = json.load(sys.stdin)
            if not isinstance(values, dict):
                raise ValueError("--json needs one JSON object")
        else:
            values = {name: getattr(args, name) for name in ("routine", "skill", "outcome", "account", "next", "at")
                      if getattr(args, name) is not None}
        if "routine" not in values and isinstance(values.get("skill"), str):
            values["routine"] = ledger.routine_for_skill(args.config, values["skill"])
            if values["routine"].casefold() in ledger.NIGHT_STAGES:
                print(f"skipped: {values['routine']} is a night stage; git is its ledger")
                return
        for name in ("routine", "outcome", "account"):
            if name not in values:
                raise ValueError(f"missing required field: {name}")
        for name in ("routine", "outcome", "account", "next", "at"):
            if name in values and not isinstance(values[name], str):
                raise ValueError(f"{name} must be a string")
        at = datetime.strptime(values["at"], "%Y-%m-%d %H:%M") if "at" in values else args.config.now(datetime.now(args.config.zone))
        print(ledger.append(args.config, values["routine"], values["outcome"], values["account"], values.get("next", ""), at))
    except (OSError, ValueError) as error:
        sys.exit(f"ledger append: {error}")


def cmd_journal_append(args):
    """Reads one after-session record as JSON on stdin, or with --synthesis the catch-up's text."""
    try:
        at = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else args.config.now(datetime.now(args.config.zone))
        if args.synthesis:
            text = sys.stdin.read()
            if not text.strip():
                raise ValueError("empty synthesis")
            print(journal.append_synthesis(args.config, text, at))
            return
        record = json.load(sys.stdin)
        if not isinstance(record, dict):
            raise ValueError("needs one JSON object")
        print(journal.append_entry(args.config, record, args.platform, at,
                                   user_id=args.user_id, user_id_alt=args.user_id_alt))
    except (OSError, ValueError) as error:
        sys.exit(f"journal append: {error}")


def cmd_journal_recent(args):
    entries = journal.recent(args.config, args.config.today(), args.count)
    for entry in entries:
        print(entry.line)
    unread = journal.unread_lines(args.config, args.config.today())
    if not entries:
        print(f"0 rows: no journal rows in {args.config.agent('Journal')}/ for this month or last"
              + (f"; {unread} line(s) there are in an older format the engine does not read" if unread else ""))
    elif unread:
        print(f"note: {unread} journal line(s) are in an older format the engine does not read")


def cmd_redact(args):
    from . import redact
    changed = redact.redact_paths([Path(p).expanduser() for p in args.paths])
    for path, n in changed.items():
        print(f"redacted {n} credential(s) in {path}")
    print(f"{sum(changed.values())} credential(s) redacted in {len(changed)} file(s)")


def cmd_validate(a):
    vault = Vault(a.config.vault_path)
    problems = validate.checks(vault)
    for p in problems:
        print(p)
    warnings = validate.work_warnings(vault) + validate.deliverable_warnings(vault)
    for warning in warnings:
        print(f"warning: {warning}")
    print(f"{len(problems)} problems")
    if warnings:
        print(f"{len(warnings)} warnings")
    sys.exit(1 if problems else 0)


def cmd_responsibility(a):
    records = a.records or a.skill.removeprefix(a.config.skill_prefix)
    for path in install.responsibility(a.config, a.name, a.skill, records):
        print(path)
    print(install.records_path() / records)


def cmd_attribute(a):
    root = a.config.vault_path
    if not a.file:
        directory = Path(a.sessions_dir).resolve() if a.sessions_dir else digest.default_sessions_dir(a.config)
        for use in attribute.sessions(Vault(root), directory, a.write):
            print(use.line())
        return
    text = Path(a.file).read_text(encoding="utf-8")
    uses = attribute.explicit_uses(Vault(root), text, a.ts, a.reason or f"from {Path(a.file).name}")
    for u in uses:
        print(u.line())
    if a.write and uses:
        attribute.append_log(root / a.config.agent("Use Log") / f"{a.ts[:7]}.md", uses)


def cmd_compile_context(a):
    vault = Vault(a.config.vault_path)
    only = None
    if a.sections is not None:
        only = tuple(name.strip() for name in a.sections.split(",") if name.strip())
        unknown = sorted(set(only) - set(SECTIONS))
        if unknown or not only:
            raise SystemExit(f"--sections takes a comma-separated list of {', '.join(SECTIONS)}; got {a.sections!r}")
        if a.write:
            raise SystemExit("--sections prints a run's context; .context.md is always the whole of it")
    if a.write:
        result = compile_context.write(vault, Path(a.out) if a.out else None)
    else:
        text = compile_context.build(vault, only)
        print(text, end="")
        result = compile_context.summary(text, a.config.context.hot_words)
    print(result, file=sys.stderr)


def cmd_digest(args):
    directory = Path(args.sessions_dir).resolve() if args.sessions_dir else None
    try:
        text, words = digest.build(args.config, args.date, directory, args.max_words, include_routines=args.include_routines)
        if args.write:
            path = digest.write(args.config, args.date, text)
            print(f"{path}: {words} words")
        else:
            print(text, end="")
    except (OSError, ValueError) as error:
        sys.exit(str(error))


def cmd_retrieve(a):
    vault = Vault(a.config.vault_path)
    candidates = retrieve.search(vault, a.query, a.limit)
    if a.json:
        print(json.dumps([asdict(candidate) | ({"text": retrieve.text(vault, candidate, a.form)} if a.form != "names" else {})
                          for candidate in candidates], ensure_ascii=False, indent=2))
    elif not candidates:
        print("no candidates")
    else:
        for candidate in candidates:
            print(f"{candidate.score:g}  {candidate.tier}  {candidate.path}  — {candidate.reason}")


def cmd_needs_you(args):
    if args.source and not args.ask:
        raise SystemExit("brain needs-you: --source goes with --ask")
    if args.ask:
        from .needs import vague_ask
        if (reason := vague_ask(args.ask)):
            print(f"ask refused: {reason}", file=sys.stderr)
            sys.exit(2)
        journal.add_ask(args.config, args.config.today(), args.ask, args.source, why=args.why or "")
        count = len(needs.open_asks((args.config.vault_path / "CURRENT.md").read_bytes().decode("utf-8")))
        if count > needs.OPEN_ASKS:
            print(f"Needs you now holds {count} asks; tonight's evening decides the lowest-ranked down to {needs.OPEN_ASKS}.",
                  file=sys.stderr)
        return
    if args.overflow:
        for item in needs.overflow(args.config, args.config.today()):
            print(f"{item.since} · {item.text} · {item.source}")
        return
    if args.resolve:
        if not args.because:
            raise SystemExit("brain needs-you: --resolve needs --because \"<the evidence>\"")
        with vault_lock(args.config):
            try:
                item = needs.resolve(args.config, args.resolve, args.because, args.config.today())
            except ValueError as error:
                print(error, file=sys.stderr)
                sys.exit(1)
        print(f"resolved {item.kind}: {item.text}")
        return
    if args.dismiss:
        with vault_lock(args.config):
            try:
                item = needs.dismiss(args.config, args.dismiss, args.config.today(), args.reason)
            except ValueError as error:
                print(error, file=sys.stderr)
                sys.exit(1)
        print(f"dismissed ({args.reason}) {item.kind}: {item.text}")
        return
    if args.stale:
        for item in needs.stale(args.config, args.config.today()):
            print(f"{item.since} · {item.text} · {item.source}")
        return
    items = needs.items(args.config, args.config.today())
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{item.kind} · {item.text} · {item.source}")


def cmd_loops(args):
    config, now = args.config, args.config.now()
    try:
        if args.add is not None:
            if not args.kind or not args.source:
                raise ValueError("--add needs --kind K and --source NOTE")
            if args.due is True:
                raise ValueError("--add --due needs a date")
            added = loops.add(config, now, args.kind, args.add, args.source, args.with_ or "", args.due or "")
            print(f"{'added' if added else 'already open'} loop: {args.add}")
            return
        if args.kind or args.source is not None or args.with_ is not None or isinstance(args.due, str):
            raise ValueError("--kind, --source, --with and --due DATE go with --add")
        if args.close is not None:
            if not args.outcome or not args.because:
                raise ValueError("--close needs --outcome done|dropped and --because TEXT")
            item = loops.close(config, args.close, now, args.outcome, args.because)
            print(f"closed ({item.outcome}): {item.text}")
            return
        if args.outcome or args.because is not None:
            raise ValueError("--outcome and --because go with --close")
    except ValueError as error:
        raise SystemExit(f"brain loops: {error}")
    due = loops.due(config, now)
    if args.quiet:
        raise SystemExit(0 if due else 1)
    if args.json:
        print(json.dumps({"open": [loops.as_dict(item) for item in loops.open_loops(config)],
                          "due": [loops.as_dict(item) for item in due],
                          "closed_recent": [loops.as_dict(item) for item in loops.closed_recent(config)]},
                         ensure_ascii=False, indent=2))
    else:
        for item in due if args.due else loops.open_loops(config):
            details = ((f" · with: {item.with_}" if item.with_ else "") + (f" · due: {item.due}" if item.due else "")
                       + (f" · ready: {item.ready}" if item.ready else ""))
            print(f"{item.id} · {item.since} · {item.kind} · {item.text} · [[{item.source}]]{details}")


def cmd_ideas(args):
    config, today = args.config, args.config.today()
    if (args.source is not None or args.why is not None or args.needs is not None) and args.add is None:
        raise SystemExit("brain ideas: --source, --why and --needs go with --add")
    if args.answer and args.respond is None:
        raise SystemExit("brain ideas: --answer goes with --respond")
    try:
        if args.add is not None:
            if not args.source:
                raise ValueError("--add needs --source NOTE")
            if not ideas.add(config, today, args.add, args.source, args.why or "", args.needs or ""):
                raise ValueError(ideas.add_refusal(config, today, args.add) or "idea could not be added")
            print(f"added idea: {args.add}")
            return
        if args.respond is not None:
            if not args.answer:
                raise ValueError("--respond needs --answer yes|not-now|no|stop-asking")
            item = ideas.respond(config, args.respond, today, args.answer)
            print(f"{args.answer}: {item.text}")
            return
    except ValueError as error:
        raise SystemExit(f"brain ideas: {error}")
    opened = ideas._open(config, today)
    snoozed = ideas._snoozed(config, today)
    item = ideas.shown(config, today)
    until = ideas.cooldown_until(config, today)
    result = {"shown": asdict(item) if item else None, "open": len(opened),
              "snoozed": sum(idea.id in snoozed for idea in opened), "newOwner": ideas.new_owner(config, today),
              "cooldownUntil": until.isoformat() if until else None}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for idea in opened if args.all else ([item] if item else []):
        print(f"{idea.id} · {idea.text} · {idea.source}")
    print(f"{result['open']} open, {result['snoozed']} snoozed; "
          + ("new owner" if result["newOwner"] else "established owner")
          + (f"; next idea can show {until.isoformat()}" if until else ""))


def cmd_access(args):
    config = args.config
    vault = Vault(config.vault_path)
    matches = access.matching_people(vault, config, args.who)
    if len(matches) > 1:
        raise SystemExit("brain access: ambiguous person; matching profiles: "
                         + ", ".join(config.link(note.rel) for note in matches))
    result = access.resolve(vault, config, matches[0].rel if matches else None)
    if args.json:
        if not matches:
            print(f"brain access: no profile matches {args.who!r}; resolving as stranger.", file=sys.stderr)
        data = {"person": result.person, "groups": result.groups,
                "subjects": {subject: {"tell": value.tell, "tellRule": value.tell_rule,
                                       "do": value.do, "doRule": value.do_rule}
                             for subject, value in result.subjects.items()}, "scope": result.scope}
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    person = config.link(result.person) if result.person else f"stranger (no profile matches {args.who!r})"
    print(f"Person: {person} · Groups: {', '.join(config.link(rel) for rel in result.groups) or 'none'}")
    for subject, value in result.subjects.items():
        if value.tell != "none" or value.do != "no":
            print(f"{subject}: {value.tell} ({value.tell_rule or 'default'}) / {value.do} ({value.do_rule or 'default'})")
    print("Everything else: none / no")
    for text in result.scope.values():
        print(f"Scope: {text}")


def cmd_people(args):
    from . import directory
    if args.action == "sync":
        try:
            if args.run:
                try:
                    out = subprocess.run(["demiurge", "intellects", "--json"], capture_output=True,
                                         text=True, check=True, timeout=20).stdout
                except FileNotFoundError:
                    return
                payload = json.loads(out)
            else:
                payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise ValueError("needs one JSON object")
            for line in directory.sync(args.config, payload, args.config.today()):
                print(line)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            sys.exit(f"people sync: {error}")
        return
    entry = directory.lookup(args.config, args.ident)
    if entry is None:
        print("not in the directory")
        sys.exit(1)
    profiles = [note.rel for note in directory.profiles(Vault(args.config.vault_path))
                if f"demiurge:{entry['id']}" in session_context.profile_ids(note.body)]
    if args.json:
        print(json.dumps(dict(entry, profiles=profiles), ensure_ascii=False, indent=2))
        return
    print(f"{directory.describe(entry)}; id {entry['id']}")
    for rel in profiles:
        print(f"Profile: {args.config.link(rel)}")


def cmd_channels(args):
    from . import channels as registry
    vault = Vault(args.config.vault_path)
    if args.action == "mail":
        from . import channel_files
        try:
            since = channel_files.local_datetime(args.since, args.config)
        except ValueError:
            sys.exit("channels mail: --since must be an ISO datetime or YYYY-MM-DD")
        data = channel_files.mail(vault, since)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else channel_files.render_mail(data))
        return
    if args.action == "sync":
        if args.run:
            try:
                out = subprocess.run(["connect", "list", "--limit", "50"], capture_output=True, text=True, check=True).stdout
            except (OSError, subprocess.CalledProcessError) as error:
                print(f"connect list failed: {error}", file=sys.stderr)
                sys.exit(1)
        else:
            out = sys.stdin.read()
        added = registry.sync(args.config, vault, out.splitlines(), args.config.today(), args.profile or None)
        for row in added:
            print(f"added {row.channel}: {row.reach} ({row.granted})")
        if not added:
            print("nothing new to register")
        return
    rows = registry.load(vault)
    if args.json:
        print(json.dumps([row.__dict__ for row in rows], ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(f"{row.channel} · {row.whose} · {row.status}" + (f" · {row.identity}" if row.identity else "")
              + (f" · reach {row.reach}" if row.reach else "") + (f" · read: {row.read}" if row.read else "")
              + (f" · write: {row.write}" if row.write else ""))
    if not rows:
        print("no channels registered")


def cmd_task(args):
    try:
        values = {name: getattr(args, name) for name in tasks_mod.FIELDS if getattr(args, name) is not None
                  and not (name == "work" and args.tid == "new")}
        if args.tid == "new":
            if not args.source:
                raise ValueError("task new needs --source")
            if args.clear:
                raise ValueError("--clear goes with task T-nnn set")
        elif args.action not in ("done", "drop", "yours", "set", "reopen"):
            raise ValueError("task ACTION must be done, drop, yours, reopen or set")
        elif args.source is not None or args.json:
            raise ValueError("--source and --json go with task new")
        elif args.action != "set" and (set(values) - {"why"} or args.state is not None or args.clear
                                       or args.reviewed is not None or args.report is not None or args.title is not None):
            raise ValueError("field flags, --state, --title, --reviewed, --report and --clear go with task T-nnn set")
        in_turn = bool(os.environ.get("DEMIURGE_CONVERSATION", "").strip() or os.environ.get("DEMIURGE_TASK", "").strip())
        if args.action in ("done", "drop", "reopen") and not args.why and (in_turn or args.action == "reopen"):
            # Without --why only the owner's own Done or Drop in the app closes a task, and it says so.
            raise ValueError(f"task {args.action} needs --why \"<the evidence or the reason>\"")
        with vault_lock(args.config):
            before = None
            if args.tid != "new":
                registered, archived = load_tasks(args.config)
                before = next((t for t in registered + archived if t.tid == args.tid), None)
            if args.tid == "new":
                task = tasks_mod.new_task(args.config, args.action, args.source, state="ready" if args.state is None else args.state,
                                          values=values, work=args.work)
            elif args.action == "reopen":
                task = tasks_mod.reopen(args.config, args.tid, args.config.today(), args.why)
            elif args.action in ("set", "yours"):
                fields = values if args.action == "set" else ({"status": args.why} if args.why else None)
                task = tasks_mod.set_fields(args.config, args.tid, args.config.today(),
                                            state="yours" if args.action == "yours" else args.state,
                                            values=fields, clear=args.clear or (),
                                            reviewed=args.reviewed, report=args.report, title=args.title)
            else:
                task = tasks_mod.close(args.config, args.tid, args.config.today(), args.action == "done",
                                       args.why or "the owner closed it in their app")
            if before is not None and before.task_state == "waiting_for_owner" and task.task_state != "waiting_for_owner":
                # The ask it stood for is answered or moot: it leaves Needs you with it.
                needs.settle_tasks(args.config, args.config.today(), [(task.tid, before.body)],
                                   f"{task.tid} is {task.task_state.replace('_', ' ')}")
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    # A task created, changed or closed in a conversation (not by a background worker) is its owner engaging with it.
    if os.environ.get("DEMIURGE_CONVERSATION", "").strip() and not os.environ.get("DEMIURGE_TASK", "").strip():
        from . import continuation
        continuation.mark_engaged(args.config, task.tid)
    if args.tid == "new":
        print(json.dumps({"id": task.tid, "title": task.title}, ensure_ascii=False) if args.json
              else f"{task.tid} created: {task.title}")
    elif args.action == "set":
        fields = ((["state"] if args.state is not None else []) + list(values) + [f"cleared {name}" for name in args.clear or ()]
                  + (["reviewed"] if args.reviewed else []) + (["report"] if args.report is not None else []))
        print(f"{task.tid} updated: {', '.join(fields)}")
    else:
        print(f"{task.tid} {task.task_state}: {task.title}")


def cmd_completed(args):
    """`brain completed [--days N] [--json]`: tasks finished recently, for the follow-on check."""
    rows = tasks_mod.completed(args.config, args.config.today(), args.days)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print(f"nothing completed in the last {args.days} days")
        return
    for row in rows:
        print(f"{row['id']} · completed {row['completed']} · {row['title']}"
              + (f" · {row['evidence'][:120]}" if row["evidence"] else "")
              + (f" · hub: {row['hub']}" if row["hub"] else ""))


def cmd_handoff(args):
    try:
        # On Demiurge the runtime names the conversation this turn is in: the report goes back where the work was asked for.
        here = os.environ.get("DEMIURGE_CONVERSATION", "").strip()
        report = f"demiurge:{here}" if here and not (args.report or "").startswith("demiurge:") else (args.report or "")
        with vault_lock(args.config):
            task = tasks_mod.hand_off(args.config, args.tid, args.config.today(), report, work=args.work)
        from . import continuation
        continuation.mark_engaged(args.config, task.tid)
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    where = task.handoff[1] if task.handoff else ""
    if os.environ.get("DEMIURGE_WORKERS", "").strip() == "1":
        # The runtime starts a background worker for the task within a minute; this turn should answer and end.
        print(f"{task.tid} handed off {task.handoff[0] if task.handoff else ''}: a background worker starts on it within "
              "a minute and reports back here. Tell them so in one line and end your reply; do not start the work in "
              "this turn.")
        return
    print(f"{task.tid} handed off {task.handoff[0] if task.handoff else ''}: worked by the continue routine until done or "
          f"blocked{', reported in ' + where if where else ''}.")


def cmd_continue(args):
    from . import continuation
    if (args.text is not None) != bool(args.checkpoint):
        print("--checkpoint TASK requires --text; --text goes with --checkpoint", file=sys.stderr)
        sys.exit(2)
    if (args.chunk or args.pace or args.checkpoint) and (args.json or args.daily):
        print("--json and --daily go with --due", file=sys.stderr)
        sys.exit(2)
    if args.engaged:
        continuation.mark_engaged(args.config, args.engaged)
        print(f"{args.engaged} engaged")
        return
    if args.chunk or args.pace or args.checkpoint:
        try:
            if args.chunk:
                print(continuation.chunk(args.config, args.chunk))
            elif args.checkpoint:
                continuation.checkpoint(args.config, args.checkpoint, args.text)
                print(f"{args.checkpoint} checkpoint saved")
            else:
                tid, pace = args.pace
                with vault_lock(args.config):
                    tasks_mod.set_pace(args.config, tid, pace, args.config.today())
                print(f"{tid} pace: {pace}")
        except ValueError as error:
            print(error, file=sys.stderr)
            sys.exit(2)
        return
    registered, archived = load_tasks(args.config)
    worked = continuation.last_worked(args.config)
    chunks = continuation.chunks_today(args.config)
    limits = continuation.budgets(args.config, registered + archived)
    ready = tasks_mod.due(registered + archived, pace="daily" if args.daily else "regular",
                          worked=worked, chunks=chunks, daily_chunks=args.config.daily_chunks,
                          now=tasks_mod.local_now(args.config), budgets=limits)
    if args.json:
        print(json.dumps([{"id": task.tid, "title": task.title, "state": task.task_state, "pace": tasks_mod.pace(task),
                           "handedOff": task.handoff[0], "report": task.handoff[1], "work": task.work,
                           "delivers": task.delivers, "next": state._field(task.body, "Next", "Next action"),
                           "lastChunk": worked.get(task.tid), "checkpoint": continuation.checkpoint_text(task.tid),
                           "chunksToday": chunks.get(task.tid, 0),
                           "budget": None if args.daily else limits.get(task.tid, args.config.daily_chunks),
                           "budgetLeft": None if args.daily else max(0, limits.get(task.tid, args.config.daily_chunks) - chunks.get(task.tid, 0))}
                          for task in ready], ensure_ascii=False, indent=2))
        return
    if not ready:
        print("nothing handed off is due")
        sys.exit(1)
    for task in ready:
        nxt = state._field(task.body, "Next", "Next action") or ""
        print(f"{task.tid} · handed off {task.handoff[0]} · {task.title}" + (f" · next: {nxt[:160]}" if nxt else "")
              + (f" · work: {task.work}" if task.work else "")
              + (f" · report in {task.handoff[1]}" if task.handoff[1] else "")
              + (f" · checkpoint: {checkpoint}" if (checkpoint := continuation.checkpoint_text(task.tid)) is not None else ""))


def cmd_watch_signal(args):
    print(signals.watch(args.config))


def cmd_continue_signal(args):
    print(signals.continue_(args.config, daily=args.daily))


def cmd_stale_tasks(args):
    tasks, archived = load_tasks(args.config)
    from . import continuation
    for task, touched in quiet(tasks + archived, args.config.today(), continuation.last_worked(args.config)):
        line = f"{task.tid} · {task.task_state or 'open'} · {task.state.get('tier', 'untiered')} · last touched {touched or 'never'} · {task.title}"
        print(line + (f" · {task.first_status()}" if task.first_status() else ""))


def cmd_state(args):
    data = state.build(args.config, args.config.today())
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(" · ".join(f"{len(data[key])} {key}" for key in
                         ("tasks", "hubs", "routines", "triggers", "ledger", "journal", "needsYou", "outputs", "loops", "loopsClosed")))


def cmd_simulate(a):
    print(act.simulate_table(a.config.context.hot_threshold if a.h is None else a.h,
                             a.config.context.warm_threshold if a.w is None else a.w))


def cmd_audit(args):
    directory = Path(args.sessions_dir).resolve() if args.sessions_dir else None
    data = audit.report(args.config, date.fromisoformat(args.date) if args.date else None, args.days, directory)
    print(audit.to_json(data) if args.json else audit.render(data), end="" if not args.json else "\n")


def cmd_health(args):
    data = health.report(args.config, date.fromisoformat(args.date) if args.date else None)
    if args.write:
        print(health.write(args.config, data))
    elif args.json:
        print(health.to_json(data))
    else:
        print(health.render(data), end="")


def cmd_session_context(args):
    text = session_context.build(Vault(args.config.vault_path), args.config,
                                 platform=args.platform, user_id=args.user_id,
                                 user_id_alt=args.user_id_alt, user_name=args.user_name,
                                 chat_id=args.chat_id)
    if text:
        print(text)


def cmd_show_skill(args):
    try:
        print(install.render_skill(args.config, args.name), end="")
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)


def cmd_people_mentioned(args):
    text = session_context.mentioned(Vault(args.config.vault_path), args.config, args.text, args.form)
    if text:
        print(text)


def cmd_export_sessions(args):
    written, unchanged = sessions.export(args.config, args.date)
    print(f"exported: {written} files written, {unchanged} unchanged")


def cmd_install_skills(args):
    print(install.skills(args.config, force=args.force))


def cmd_update(args):
    installed = install.update(args.config)
    if not args.config.update_source:
        print("update disabled (brain.update_source is empty)")
    elif runtime.configured() is None:
        print("scripts not reinstalled: AGENT_HOME is not set")
    else:
        print("updated; reinstalled " + ", ".join(path.name for path in installed))


def cmd_version(args):
    print(install.installed_version("agent-brain"))


def cmd_migrate_check(args):
    pending = install.migrate_check(args.config)
    print(f"migration pending: {pending}" if pending else "engine version recorded; no migration needed")


def cmd_migrate_done(args):
    print(f"migrated to {install.migrate_done(args.config, args.defer)}"
          + (f"; {len(args.defer)} step(s) left for the owner in Needs you" if args.defer else ""))


def cmd_install_scripts(args):
    for path in install.scripts():
        print(f"installed {path}")


def cmd_timezone(args):
    print(args.config.timezone)


def cmd_record_spec(args):
    print(json.dumps({"system": record.SYSTEM, "conversation": record.CONVERSATION, "schema": record.RECORD_SCHEMA,
                      "transcriptCap": record.TRANSCRIPT_CAP, "routine": record.ROUTINE,
                      "routineSchema": record.ROUTINE_SCHEMA, "nightStages": sorted(record.NIGHT_STAGES)}))


def cmd_schedule(args):
    config, action = args.config, args.schedule_cmd
    if action == "check":
        errors = schedule.problems(config)
        for error in errors:
            print(error)
        print(f"{len(errors)} problems")
        sys.exit(1 if errors else 0)
    try:
        if action == "list":
            routines = schedule.load(config)
            if args.json:
                print(json.dumps({name: asdict(routines[name]) for name in sorted(routines)}))
            else:
                for name, routine in sorted(routines.items()):
                    timing = next(f"{key} {getattr(routine, key)}" for key in schedule.TIMING if getattr(routine, key) is not None)
                    print(f"{name}: {timing}" + (" (paused)" if not routine.enabled else ""))
            return
        with vault_lock(config):
            if action == "import":
                if schedule.path(config).exists() and not args.force:
                    raise ValueError(f"{schedule.path(config)} exists; use --force to replace it")
                routines = schedule.from_jobs(config, runtime.jobs(runtime.home()))
                changed = f"imported {len(routines)} routine(s) ({', '.join(sorted(routines))}) into {schedule.path(config)}"
            else:
                routines = schedule.load(config)
                name = args.name
                if action == "set":
                    changes = {key: getattr(args, key) for key in schedule.Routine.__dataclass_fields__
                               if getattr(args, key, None) is not None}
                    if "skills" in changes:
                        changes["skills"] = [skill.strip() for skill in changes["skills"].split(",") if skill.strip()]
                    if any(key in changes for key in schedule.TIMING):
                        changes = {**dict.fromkeys(schedule.TIMING), **changes}
                    existed = name in routines
                    routines[name] = replace(routines.get(name, schedule.Routine()), **changes)
                    changed = f"{'updated' if existed else 'created'} routine {name} in {schedule.path(config)}"
                else:
                    if name not in routines:
                        raise ValueError(f"no such routine: {name}")
                    if action == "remove":
                        del routines[name]
                    else:
                        routines[name] = replace(routines[name], enabled=action == "resume")
                    verb = {"remove": "removed", "pause": "paused", "resume": "resumed"}[action]
                    changed = f"{verb} routine {name} in {schedule.path(config)}"
            schedule._save(config, routines)
            errors = schedule.problems(config)
            if errors:
                raise ValueError("; ".join(errors))
        print(changed)
    except (OSError, ValueError) as error:
        sys.exit(f"schedule {action}: {error}")


def cmd_paths(args):
    if args.path in ("scripts", "records") and runtime.configured() is None:
        sys.exit("AGENT_HOME is not set")
    paths = {"soul": lambda: args.config.soul_path, "skills": lambda: args.config.skills_path,
             "scripts": install.scripts_path, "records": install.records_path,
             "vault": lambda: args.config.vault_path, "repo": lambda: args.config.repo,
             "python": lambda: Path(sys.executable), "schedule": lambda: schedule.path(args.config)}
    print(paths[args.path]().resolve())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("export-sessions"); s.add_argument("--vault")
    s.add_argument("--date", type=date.fromisoformat); s.set_defaults(fn=cmd_export_sessions)
    s = sub.add_parser("version"); s.add_argument("--vault"); s.set_defaults(fn=cmd_version)
    s = sub.add_parser("record-spec"); s.add_argument("--vault")
    s.add_argument("--json", action="store_true", required=True); s.set_defaults(fn=cmd_record_spec)
    s = sub.add_parser("schedule")
    actions = s.add_subparsers(dest="schedule_cmd", required=True)
    for action in ("list", "set", "remove", "pause", "resume", "check", "import"):
        s = actions.add_parser(action); s.add_argument("--vault"); s.set_defaults(fn=cmd_schedule)
        if action in ("set", "remove", "pause", "resume"):
            s.add_argument("name")
        if action == "list":
            s.add_argument("--json", action="store_true")
        if action == "import":
            s.add_argument("--force", action="store_true")
        if action == "set":
            timing = s.add_mutually_exclusive_group()
            for key in schedule.TIMING:
                timing.add_argument("--" + key)
            for key in ("skills", "prompt", "deliver", "monitor", "script", "workdir"):
                s.add_argument("--" + key)
            agent = s.add_mutually_exclusive_group()
            agent.add_argument("--agent", action="store_true", default=None)
            agent.add_argument("--no-agent", dest="agent", action="store_false")
            enabled = s.add_mutually_exclusive_group()
            enabled.add_argument("--enabled", action="store_true", default=None)
            enabled.add_argument("--disabled", dest="enabled", action="store_false")
            s.add_argument("--owner", action="store_true", default=None)
    s = sub.add_parser("pull", help="bring the vault's repository up to date under the repository lock")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("commit", help="commit the vault under the repository lock (scoped to PATHs when given), optionally pushing")
    s.add_argument("-m", "--message", required=True); s.add_argument("--push", action="store_true")
    s.add_argument("paths", nargs="*", metavar="PATH"); s.add_argument("--vault"); s.set_defaults(fn=cmd_commit)
    s = sub.add_parser("nightly"); s.add_argument("--vault"); s.add_argument("--date")
    s.add_argument("--write", action="store_true"); s.add_argument("--commit", action="store_true"); s.add_argument("--push", action="store_true")
    s.add_argument("--sessions-dir")
    s.set_defaults(fn=cmd_nightly)
    s = sub.add_parser("ledger")
    ledger_sub = s.add_subparsers(dest="ledger_cmd", required=True)
    s = ledger_sub.add_parser("append"); s.add_argument("routine", nargs="?"); s.add_argument("--vault")
    s.add_argument("--skill"); s.add_argument("--outcome"); s.add_argument("--account"); s.add_argument("--next")
    s.add_argument("--at"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_ledger_append)
    s = sub.add_parser("journal")
    journal_sub = s.add_subparsers(dest="journal_cmd", required=True)
    s = journal_sub.add_parser("append"); s.add_argument("--vault"); s.add_argument("--platform", default="demiurge")
    s.add_argument("--user-id", default=""); s.add_argument("--user-id-alt", default="")
    s.add_argument("--at"); s.add_argument("--synthesis", action="store_true"); s.set_defaults(fn=cmd_journal_append)
    s = journal_sub.add_parser("recent"); s.add_argument("--vault"); s.add_argument("--count", type=int, default=30)
    s.set_defaults(fn=cmd_journal_recent)
    s = sub.add_parser("validate"); s.add_argument("--vault"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("access"); s.add_argument("who"); s.add_argument("--vault")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_access)
    s = sub.add_parser("attribute"); s.add_argument("--vault")
    source = s.add_mutually_exclusive_group()
    source.add_argument("--file"); source.add_argument("--sessions-dir", help="default: $AGENT_HOME/records/sessions")
    s.add_argument("--ts"); s.add_argument("--reason"); s.add_argument("--write", action="store_true")
    s.set_defaults(fn=cmd_attribute)
    s = sub.add_parser("compile-context"); s.add_argument("--vault"); s.add_argument("--out")
    s.add_argument("--write", action="store_true"); s.add_argument("--sections")
    s.set_defaults(fn=cmd_compile_context)
    s = sub.add_parser("digest"); s.add_argument("--vault")
    s.add_argument("--date", type=date.fromisoformat, required=True); s.add_argument("--sessions-dir")
    s.add_argument("--max-words", type=int, default=digest.WORD_LIMIT)
    s.add_argument("--include-routines", action="store_true", help="also digest the agent's own cron sessions")
    s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_digest)
    s = sub.add_parser("retrieve"); s.add_argument("--vault"); s.add_argument("query")
    s.add_argument("--limit", type=int, default=12); s.add_argument("--json", action="store_true")
    s.add_argument("--form", choices=FORMS, default="names", help="with --json: add each note's index line or body as text")
    s.set_defaults(fn=cmd_retrieve)
    s = sub.add_parser("needs-you"); s.add_argument("--vault")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--ask", metavar="TEXT", help="add an ask to CURRENT's Needs you (a repeat of an open ask is ignored; never refused)")
    mode.add_argument("--stale", action="store_true", help="asks unanswered for 14 days or more, for the evening to decide or retire")
    mode.add_argument("--overflow", action="store_true", help="asks past the five shown, lowest-ranked first, for the evening to decide tonight")
    mode.add_argument("--resolve", metavar="ID_OR_TEXT", help="remove an answered item from CURRENT's Needs you and record the evidence")
    mode.add_argument("--dismiss", metavar="ID_OR_TEXT", help="the owner said no: drop the item, act on nothing, never raise it again")
    s.add_argument("--because", metavar="EVIDENCE", help="with --resolve: what answered it, in a sentence")
    s.add_argument("--reason", choices=("no", "done", "stop-asking"), default="no",
                   help="with --dismiss: what the owner chose (no, done: they did it, stop-asking: no questions like it)")
    s.add_argument("--source", metavar="NOTE", help="with --ask: the note it comes from, e.g. Orphan/Tasks#T-001")
    s.add_argument("--why", metavar="TEXT", help="with --ask: why it needs the owner and what a yes or a no means, in plain words")
    s.set_defaults(fn=cmd_needs_you)
    s = sub.add_parser("loops", help="promises, answers owed and waits on others")
    s.add_argument("--vault")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--close", metavar="ID_OR_TEXT")
    s.add_argument("--due", nargs="?", const=True, metavar="DATE", help="only due loops, or a due date with --add")
    s.add_argument("--quiet", action="store_true", help="print nothing; exit 1 when nothing is due, 0 otherwise")
    s.add_argument("--kind", choices=loops.KINDS); s.add_argument("--source", metavar="NOTE")
    s.add_argument("--with", dest="with_", metavar="WHO"); s.add_argument("--outcome", choices=loops.OUTCOMES)
    s.add_argument("--because", metavar="TEXT"); s.set_defaults(fn=cmd_loops)
    s = sub.add_parser("ideas", help="proposals for new ways to help, shown one at a time")
    s.add_argument("--vault"); s.add_argument("--all", action="store_true")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--respond", metavar="ID_OR_TEXT")
    s.add_argument("--source", metavar="NOTE"); s.add_argument("--why", metavar="TEXT")
    s.add_argument("--needs", metavar="TEXT"); s.add_argument("--answer", choices=ideas.ANSWERS)
    s.set_defaults(fn=cmd_ideas)
    s = sub.add_parser("channels", help="the channel registry; `sync` registers connect grants; `mail --since` lists new owner mail headers")
    s.add_argument("action", nargs="?", choices=("sync", "mail")); s.add_argument("--run", action="store_true"); s.add_argument("--profile", default="")
    s.add_argument("--since", help="with mail: ISO datetime or YYYY-MM-DD, in the owner's zone unless offset is given")
    s.add_argument("--json", action="store_true"); s.add_argument("--vault"); s.set_defaults(fn=cmd_channels)
    s = sub.add_parser("people", help="Demiurge's identity directory")
    actions = s.add_subparsers(dest="action", required=True)
    p = actions.add_parser("sync", help="sync stdin JSON, or --run for `demiurge intellects --json`")
    p.add_argument("--run", action="store_true"); p.add_argument("--vault"); p.set_defaults(fn=cmd_people)
    p = actions.add_parser("who", help="identify a person or intellect and find their vault profile")
    p.add_argument("ident"); p.add_argument("--json", action="store_true"); p.add_argument("--vault"); p.set_defaults(fn=cmd_people)
    s = sub.add_parser("task", help="create a task or change one: done, drop, yours, reopen, set",
                       description="brain task TID {done,drop,yours,reopen,set} [fields]; brain task new TITLE --source SOURCE")
    s.add_argument("tid", metavar="TID|new", help="task id, or new to create a task")
    s.add_argument("action", metavar="ACTION|TITLE", help="done, drop, yours, reopen, set; with new, the task title")
    s.add_argument("--state", metavar="S", help="open task state; new accepts ready (default) or in_progress")
    for name in tasks_mod.FIELDS:
        if name == "work":
            continue   # --work below: with new or set
        s.add_argument("--" + name, metavar="NOTE" if name == "hub" else "TEXT",
                       help="existing note name, without brackets" if name == "hub"
                       else "replace Latest evidence: a dated line with a link to it" if name == "evidence"
                       else f"replace {name}; with --clear, remove it")
    s.add_argument("--clear", nargs="+", choices=(*tasks_mod.FIELDS, "handoff"), metavar="FIELD", action="extend",
                   help="remove fields (" + ", ".join(tasks_mod.FIELDS) + "); handoff takes the task back from its worker")
    s.add_argument("--title", help="with set: rename the task")
    s.add_argument("--report", metavar="WHERE", help="with set: where a handed-off task reports (demiurge:<conversation id>, or Home)")
    s.add_argument("--reviewed", metavar="TEXT", help="with set: append a dated Reviewed line (the evening's decision and why)")
    s.add_argument("--source", help="with new: the owner's words or a note name")
    s.add_argument("--work", metavar="SLUG", help="with new or set: Library work slug")
    s.add_argument("--json", action="store_true", help="with new: print {id, title}")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_task)
    s = sub.add_parser("completed", help="tasks completed recently, newest first, for the follow-on check")
    s.add_argument("--days", type=int, default=7); s.add_argument("--json", action="store_true")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_completed)
    s = sub.add_parser("handoff", help="mark a task the owner handed off: the continue routine works it until done or blocked")
    s.add_argument("tid"); s.add_argument("--report", default="", help="where to report, e.g. 'Home' or the room"); s.add_argument("--vault")
    s.add_argument("--work", help="Library work slug, e.g. 'trip-plan'")
    s.set_defaults(fn=cmd_handoff)
    s = sub.add_parser("continue", help="handed-off tasks due now, least recently worked first; text exits 1 when none")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--due", action="store_true")
    mode.add_argument("--chunk", metavar="TASK")
    mode.add_argument("--pace", nargs=2, metavar=("TASK", "regular|daily"))
    mode.add_argument("--checkpoint", metavar="TASK", help="save a per-task continue checkpoint")
    mode.add_argument("--engaged", metavar="TASK", help="record that the owner engaged with the task now (its daily budget is whole again)")
    s.add_argument("--text", help="with --checkpoint: where the chunk stopped")
    s.add_argument("--json", action="store_true", help="with --due: JSON array; empty array exits 0")
    s.add_argument("--daily", action="store_true", help="list daily-pace tasks")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_continue)
    s = sub.add_parser("watch-signal", help="stable follow-through monitor output, without a model")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_watch_signal)
    s = sub.add_parser("continue-signal", help="stable due-handoff monitor output, without a model")
    s.add_argument("--daily", action="store_true"); s.add_argument("--vault"); s.set_defaults(fn=cmd_continue_signal)
    s = sub.add_parser("stale-tasks", help="open tasks gone quiet long enough for the evening review")
    s.add_argument("--vault"); s.set_defaults(fn=cmd_stale_tasks)
    s = sub.add_parser("state"); s.add_argument("--vault")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_state)
    s = sub.add_parser("health"); s.add_argument("--vault"); s.add_argument("--date")
    s.add_argument("--json", action="store_true"); s.add_argument("--write", action="store_true", help="write <runtime_dir>/health.md")
    s.set_defaults(fn=cmd_health)
    s = sub.add_parser("audit"); s.add_argument("--vault"); s.add_argument("--date"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--sessions-dir"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("show-skill", help="print the package's current text of one routine, rendered for this vault")
    s.add_argument("name"); s.add_argument("--vault"); s.set_defaults(fn=cmd_show_skill)
    s = sub.add_parser("people-mentioned"); s.add_argument("--vault"); s.add_argument("text")
    s.add_argument("--form", choices=("line", "card"), default="line")
    s.set_defaults(fn=cmd_people_mentioned)
    s = sub.add_parser("session-context"); s.add_argument("--vault")
    for option in ("platform", "user-id", "user-id-alt", "user-name", "chat-id"):
        s.add_argument("--" + option, default="")
    s.set_defaults(fn=cmd_session_context)
    s = sub.add_parser("simulate"); s.add_argument("--h", type=int); s.add_argument("--w", type=int)
    s.add_argument("--vault")
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("responsibility", help="build a hub, an own skill and a records folder for standing work")
    s.add_argument("name"); s.add_argument("--skill", required=True); s.add_argument("--records"); s.add_argument("--vault")
    s.set_defaults(fn=cmd_responsibility)
    s = sub.add_parser("timezone", help="print the owner's timezone from brain.toml"); s.add_argument("--vault"); s.set_defaults(fn=cmd_timezone)
    s = sub.add_parser("install-skills"); s.add_argument("--vault"); s.add_argument("--force", action="store_true", help="also overwrite the day routines")
    s.set_defaults(fn=cmd_install_skills)
    s = sub.add_parser("update"); s.add_argument("--vault"); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("migrate-check"); s.add_argument("--vault"); s.set_defaults(fn=cmd_migrate_check)
    s = sub.add_parser("redact"); s.add_argument("paths", nargs="+"); s.add_argument("--vault"); s.set_defaults(fn=cmd_redact)
    s = sub.add_parser("migrate-done"); s.add_argument("--vault")
    s.add_argument("--defer", action="append", default=[], metavar="STEP",
                   help="a step left for the owner (repeatable); it becomes an ask in CURRENT's Needs you")
    s.set_defaults(fn=cmd_migrate_done)
    s = sub.add_parser("install-scripts"); s.add_argument("--vault"); s.set_defaults(fn=cmd_install_scripts)
    s = sub.add_parser("paths"); s.add_argument("--vault")
    s.add_argument("path", choices=("soul", "skills", "scripts", "records", "vault", "repo", "python", "schedule")); s.set_defaults(fn=cmd_paths)
    a = ap.parse_args(argv)
    if a.cmd == "channels" and a.action == "mail" and not a.since:
        ap.error("channels mail requires --since")
    if a.cmd == "attribute" and a.file and not a.ts:
        ap.error("--ts is required with --file")
    try:
        a.config = Config.load(a.vault)
    except (OSError, ValueError) as error:
        ap.error(str(error))
    a.fn(a)


if __name__ == "__main__":
    main()
