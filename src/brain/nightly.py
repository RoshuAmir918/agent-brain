"""Nightly memory mechanics: one idempotent pass over the vault. Git is the ledger.

Usage:
  brain nightly [--vault <path>] [--date YYYY-MM-DD] [--write] [--commit] [--push]
  brain validate [--vault <path>]
  brain attribute [--vault <path>] --file <record.md> --ts <iso> [--reason <text>] [--write]
  brain attribute [--vault <path>] [--sessions-dir <dir>] [--write]
  brain digest [--vault <path>] --date YYYY-MM-DD [--sessions-dir <dir>] [--write] [--max-words 25000]
  (sessions default to $HERMES_HOME/records/sessions, the profile's own export)
  brain audit [--vault <path>] [--date YYYY-MM-DD] [--days 14] [--sessions-dir <dir>] [--json]
  brain compile-context [--vault <path>] [--out <path>] [--write]
  brain retrieve [--vault <path>] <query> [--limit 12] [--json]
  brain session-context [--vault <path>] [--platform <name>] [--user-id <id>] [--user-id-alt <id>] [--user-name <name>] [--chat-id <id>]
  brain simulate [--h 7 --w 28]
  brain install-skills [--vault <path>]
  brain install-scripts
  brain install-provider
  brain paths <soul|skills|scripts|provider|vault|repo> [--vault <path>]

Without --write the pass runs on a temporary copy of the agent tree and prints
what it would change. Every step is a function of the vault's current files, so
rerunning a night, or rerunning after an interrupted run, converges on the same
result. The only things that advance are the dates in frontmatter and the log.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import activation as act
from . import attribute, audit, compile_context, digest, fm, health, index, retrieve, session_context, triggers, validate
from .tasks import Task, bounded_hot_body, is_open, load_tasks, parse_register, task_activation, write_archive
from .vault import DAILY, Vault
from .config import Config
from . import install

HOT_BUDGET_WORDS = 6000
MARK_START = "<!-- brain:active-threads start -->"
MARK_END = "<!-- brain:active-threads end -->"


def mechanical(vault: Vault, rel: str) -> bool:
    """Notes whose frontmatter the script owns: not logs, dreams or immutable daily archives."""
    return not DAILY.fullmatch(rel) and vault.kind_for(rel) not in ("log", "dream")


# ---------- frontmatter defaults

def ensure_frontmatter(vault: Vault, today: date, report: list[str]) -> set[str]:
    changed = set()
    for rel, n in vault.notes.items():
        if not mechanical(vault, rel) or rel in ("Hot Set.md", "Warm Index.md"):
            continue  # generated views carry no frontmatter
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
        if "uses" not in d and kind in ("hub", "profile", "reflection", "episode", "primed"):
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
    for rel, n in vault.notes.items():
        kind = vault.kind_for(rel)
        if not mechanical(vault, rel) or kind == "orientation":
            continue
        ages = act.ages_from_dates([str(x) for x in n.data.get("uses", [])], today)
        iages = act.ages_from_dates([str(x) for x in n.data.get("uses_inferred", [])], today)
        b = act.activation(ages, iages)
        if kind == "episode" and rel.startswith("Conversations/") and not n.data.get("consolidated"):
            tier = "hot" if act.tier_for(b) == "hot" else "warm"   # unconsolidated records cannot go cold
        elif kind == "primed" and not n.data.get("pinned"):
            try:
                expired = date.fromisoformat(Path(rel).stem) < today
            except ValueError:
                expired = False
            tier = "cold" if expired else act.tier_for(b)
        else:
            tier = act.tier_for(b, bool(n.data.get("pinned")))
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
    b = task_activation(t, today)
    tier = act.tier_for(b, t.state.get("pinned") == "true")
    t.state["tier"] = tier
    t.state["activation"] = f"{b:.2f}" if b != float("-inf") else "-99"
    if tier == "hot":
        bounded = bounded_hot_body(t, config)   # raises when it would have to guess; the night stops and Sleep 1 fixes the entry
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
    cold_open, out = [], body
    for i, (tid, s, e) in sorted(enumerate(spans), key=lambda x: -x[1][1]):
        t = by_id[tid]
        if first[tid] != i:
            rendered = ""
            report.append(f"task: {tid} duplicate register entry removed")
        elif not is_open(t):
            continue
        else:
            rendered = place(config, t, today, cold_open, report)
        chunk = rendered + ("\n" if rendered else "")
        if out[s:e] != chunk:
            out = out[:s] + chunk + out[e:]
    restored = []
    for t in archived:
        rendered = place(config, t, today, cold_open, report)
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
    for rel in changed - {vault.config.agent("Triggers.md")}:
        vault.notes[rel].save()
    open_tasks, cold_open = process_tasks(config, today, tasks, archived, report)
    if vault.config.agent("Triggers.md") in changed:
        vault.notes[vault.config.agent("Triggers.md")].save()
    rebuild_current(config, open_tasks, today, report)
    vault.load()
    (root / "Hot Set.md").write_text(index.build_hot_set(vault, today, HOT_BUDGET_WORDS), encoding="utf-8")
    (root / "Warm Index.md").write_text(index.build_warm_index(vault, today, cold_open, HOT_BUDGET_WORDS), encoding="utf-8")
    report.append(compile_context.write(vault))
    vault.load()
    problems = validate.run(vault, changed | vault.config.generated_views)
    if problems:
        report += [f"VALIDATION: {p}" for p in problems]
        sleep_log(config, today, report, "write, validation failed, not committed")
        for ln in report:
            print(ln)
        sys.exit(1)
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


def commit(root: Path, message: str) -> str:
    """Stage agent notes and Hermes context. Returns the short SHA, or '' when clean."""
    repo = Config.load(root).repo
    tree = root.relative_to(repo).as_posix()
    sh(repo, "add", "-A", "--", tree, f":(exclude){tree}/.brain", ".hermes.md")
    if not sh(repo, "diff", "--cached", "--name-only"):
        return ""
    sh(repo, "commit", "-q", "-m", message)
    return sh(repo, "rev-parse", "--short", "HEAD")


def push(root: Path) -> None:
    repo = Config.load(root).repo
    try:
        sh(repo, "pull", "--rebase", "-q", "origin", "main")
    except subprocess.CalledProcessError:
        subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)
        sys.exit("pull --rebase conflicted; resolve it by hand, nothing was pushed")
    sh(repo, "push", "-q", "origin", "HEAD:main")


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


def cmd_validate(a):
    vault = Vault(a.config.vault_path)
    problems = validate.run(vault, set(vault.notes)) + validate.responsibilities(vault)
    for p in problems:
        print(p)
    print(f"{len(problems)} problems")
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
    if a.write:
        result = compile_context.write(vault, Path(a.out) if a.out else None)
    else:
        text = compile_context.build(vault)
        print(text, end="")
        result = compile_context.summary(text)
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
    candidates = retrieve.search(Vault(a.config.vault_path), a.query, a.limit)
    if a.json:
        print(json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False, indent=2))
    elif not candidates:
        print("no candidates")
    else:
        for candidate in candidates:
            print(f"{candidate.score:g}  {candidate.tier}  {candidate.path}  — {candidate.reason}")


def cmd_simulate(a):
    print(act.simulate_table(a.h, a.w))


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


def cmd_shard_sessions(args):
    written, unchanged = digest.shard(Path(args.jsonl), Path(args.directory))
    print(f"sharded: {written} written, {unchanged} unchanged")


def cmd_session_ids(args):
    if args.with_kind:
        for session_id, kind in digest.session_kinds(Path(args.jsonl)):
            print(f"{session_id}\t{kind}")
        return
    for session_id in digest.session_ids(Path(args.jsonl)):
        print(session_id)


def cmd_install_skills(args):
    print(install.skills(args.config, force=args.force))


def cmd_update(args):
    installed = install.update(args.config)
    print("update disabled (brain.update_source is empty)" if not installed else
          "updated; reinstalled " + ", ".join(path.name for path in installed))


def cmd_migrate_check(args):
    pending = install.migrate_check(args.config)
    print(f"migration pending: {pending}" if pending else "engine version recorded; no migration needed")


def cmd_migrate_done(args):
    print(f"migrated to {install.migrate_done(args.config)}")


def cmd_install_scripts(args):
    for path in install.scripts():
        print(f"installed {path}")


def cmd_install_provider(args):
    install.provider()
    print(install.provider_path())
    print("hermes config set memory.provider brain")
    print("restart the gateway")


def cmd_timezone(args):
    print(args.config.timezone)


def cmd_paths(args):
    paths = {"soul": args.config.soul_path, "skills": args.config.skills_path, "scripts": install.scripts_path(),
             "provider": install.provider_path(), "records": install.records_path(),
             "vault": args.config.vault_path, "repo": args.config.repo, "python": Path(sys.executable)}
    print(paths[args.path].resolve())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brain")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("nightly"); s.add_argument("--vault"); s.add_argument("--date")
    s.add_argument("--write", action="store_true"); s.add_argument("--commit", action="store_true"); s.add_argument("--push", action="store_true")
    s.add_argument("--sessions-dir")
    s.set_defaults(fn=cmd_nightly)
    s = sub.add_parser("validate"); s.add_argument("--vault"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("attribute"); s.add_argument("--vault")
    source = s.add_mutually_exclusive_group()
    source.add_argument("--file"); source.add_argument("--sessions-dir", help="default: $HERMES_HOME/records/sessions")
    s.add_argument("--ts"); s.add_argument("--reason"); s.add_argument("--write", action="store_true")
    s.set_defaults(fn=cmd_attribute)
    s = sub.add_parser("compile-context"); s.add_argument("--vault"); s.add_argument("--out")
    s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_compile_context)
    s = sub.add_parser("digest"); s.add_argument("--vault")
    s.add_argument("--date", type=date.fromisoformat, required=True); s.add_argument("--sessions-dir")
    s.add_argument("--max-words", type=int, default=digest.WORD_LIMIT)
    s.add_argument("--include-routines", action="store_true", help="also digest the agent's own cron sessions")
    s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_digest)
    s = sub.add_parser("retrieve"); s.add_argument("--vault"); s.add_argument("query")
    s.add_argument("--limit", type=int, default=12); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_retrieve)
    s = sub.add_parser("health"); s.add_argument("--vault"); s.add_argument("--date")
    s.add_argument("--json", action="store_true"); s.add_argument("--write", action="store_true", help="write <hermes_dir>/health.md")
    s.set_defaults(fn=cmd_health)
    s = sub.add_parser("audit"); s.add_argument("--vault"); s.add_argument("--date"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--sessions-dir"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("session-context"); s.add_argument("--vault")
    for option in ("platform", "user-id", "user-id-alt", "user-name", "chat-id"):
        s.add_argument("--" + option, default="")
    s.set_defaults(fn=cmd_session_context)
    s = sub.add_parser("simulate"); s.add_argument("--h", type=int, default=act.H_DAYS); s.add_argument("--w", type=int, default=act.W_DAYS)
    s.add_argument("--vault")
    s.set_defaults(fn=cmd_simulate)
    s = sub.add_parser("shard-sessions"); s.add_argument("jsonl"); s.add_argument("directory"); s.add_argument("--vault"); s.set_defaults(fn=cmd_shard_sessions)
    s = sub.add_parser("session-ids"); s.add_argument("jsonl"); s.add_argument("--vault")
    s.add_argument("--with-kind", action="store_true", help="print id<TAB>kind: conversation, routine or evaluation")
    s.set_defaults(fn=cmd_session_ids)
    s = sub.add_parser("responsibility", help="build a hub, an own skill and a records folder for standing work")
    s.add_argument("name"); s.add_argument("--skill", required=True); s.add_argument("--records"); s.add_argument("--vault")
    s.set_defaults(fn=cmd_responsibility)
    s = sub.add_parser("timezone", help="print the owner's timezone from brain.toml"); s.add_argument("--vault"); s.set_defaults(fn=cmd_timezone)
    s = sub.add_parser("install-skills"); s.add_argument("--vault"); s.add_argument("--force", action="store_true", help="also overwrite the day routines")
    s.set_defaults(fn=cmd_install_skills)
    s = sub.add_parser("update"); s.add_argument("--vault"); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("migrate-check"); s.add_argument("--vault"); s.set_defaults(fn=cmd_migrate_check)
    s = sub.add_parser("migrate-done"); s.add_argument("--vault"); s.set_defaults(fn=cmd_migrate_done)
    s = sub.add_parser("install-scripts"); s.add_argument("--vault"); s.set_defaults(fn=cmd_install_scripts)
    s = sub.add_parser("install-provider"); s.add_argument("--vault"); s.set_defaults(fn=cmd_install_provider)
    s = sub.add_parser("paths"); s.add_argument("--vault")
    s.add_argument("path", choices=("soul", "skills", "scripts", "provider", "records", "vault", "repo", "python")); s.set_defaults(fn=cmd_paths)
    a = ap.parse_args(argv)
    if a.cmd == "attribute" and a.file and not a.ts:
        ap.error("--ts is required with --file")
    try:
        a.config = Config.load(a.vault)
    except (OSError, ValueError) as error:
        ap.error(str(error))
    a.fn(a)


if __name__ == "__main__":
    main()
