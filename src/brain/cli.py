"""The `brain` command line: argparse and one cmd_* function per subcommand.

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
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path

from . import activation as act
from . import access, attribute, audit, compile_context, digest, fm, health, ideas, journal, ledger, loops, needs, portrait, retrieve, seed, session_context, state, validate, views, works
from . import tasks as tasks_mod
from .tasks import load_tasks, quiet
from .vault import Vault
from .config import FORMS, SECTIONS, Config
from .lock import vault_lock
from .nightly import preview, run
from . import install
from . import signals
from . import record, runtime, schedule, sessions


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
    warnings = validate.work_warnings(vault) + validate.deliverable_warnings(vault) + validate.schedule_warnings(vault)
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


def speaker() -> str | None:
    """The person this turn is with, as the runtime names them (BRAIN_SPEAKER=<platform>:<id>), when it names one."""
    value = os.environ.get("BRAIN_SPEAKER", "").strip()
    return value if tasks_mod.PERSON.fullmatch(value) else None


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
                owner = bool(os.environ.get("DEMIURGE_CONVERSATION", "").strip()
                             and not os.environ.get("DEMIURGE_TASK", "").strip())
                item = needs.resolve(args.config, args.resolve, args.because, args.config.today(), owner_engaged=owner)
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
    if args.for_:
        items = [item for item in items if item.owner == args.for_]
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
        elif args.action not in ("done", "drop", "yours", "set", "reopen", "view"):
            raise ValueError("task ACTION must be done, drop, yours, reopen, set or view")
        elif args.source is not None or args.json:
            raise ValueError("--source and --json go with task new")
        elif args.action != "set" and (set(values) - {"why"} or args.state is not None or args.clear
                                       or args.reviewed is not None or args.report is not None or args.title is not None):
            raise ValueError("field flags, --state, --title, --reviewed, --report and --clear go with task T-nnn set")
        if args.action == "view":
            if values or args.why:
                raise ValueError("task T-nnn view takes its view as JSON on stdin, nothing else")
            registered, archived = load_tasks(args.config)
            if not any(t.tid == args.tid and tasks_mod.is_open(t) for t in registered + archived):
                raise ValueError(f"{args.tid} is not an open task")
            view = views.write(args.config, args.tid, sys.stdin.read())
            print(f"{args.tid} view saved: {len(view['pieces'])} pieces" if view else f"{args.tid} view removed")
            return
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
            # A conversation's task edit is engagement; worker writes never are.
            if os.environ.get("DEMIURGE_CONVERSATION", "").strip() and not os.environ.get("DEMIURGE_TASK", "").strip():
                from . import continuation
                continuation._mark_engaged(args.config, task.tid, args.source if args.tid == "new" else
                                           args.next or args.delivers or args.status or args.why)
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
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


def cmd_works(args):
    try:
        view = works.write_view(args.slug, sys.stdin.read())
    except (ValueError, RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        if views.KIT not in str(error):
            print(views.KIT, file=sys.stderr)
        sys.exit(1)
    print(f"{args.slug} view saved: {len(view['pieces'])} pieces" if view else f"{args.slug} view removed")


def cmd_handoff(args):
    try:
        # On Demiurge the runtime names the conversation this turn is in: the report goes back where the work was asked for.
        here = os.environ.get("DEMIURGE_CONVERSATION", "").strip()
        report = f"demiurge:{here}" if here and not (args.report or "").startswith("demiurge:") else (args.report or "")
        # In an organisation's intellect a task is the person's who handed it off: the runtime names the speaker.
        owner = args.for_ or (speaker() if args.config.edition == "enterprise" else None)
        with vault_lock(args.config):
            task = tasks_mod.hand_off(args.config, args.tid, args.config.today(), report, work=args.work, owner=owner)
            from . import continuation
            continuation._mark_engaged(args.config, task.tid)
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
    print(f"{task.tid} handed off {task.handoff[0] if task.handoff else ''}: worked by a background worker until done or "
          f"blocked{', reported in ' + where if where else ''}.")


def cmd_continue(args):
    from . import continuation
    try:
        if (args.text is not None) != bool(args.checkpoint):
            raise ValueError("--checkpoint TASK requires --text; --text goes with --checkpoint")
        if args.tid and (args.due or args.chunk or args.pace or args.checkpoint or args.engaged
                         or args.review or args.review_context or args.wake is not None):
            raise ValueError("TASK cannot be combined with another continue mode")
        if (args.chunk or args.pace or args.checkpoint or args.engaged or args.review) and (args.json or args.daily):
            raise ValueError("--json goes with --due, TASK, --review-context or --wake; --daily goes with --due")
        if args.daily and (args.tid or args.review_context or args.wake is not None):
            raise ValueError("--daily goes with --due")
        if not args.review and any(value is not None for value in (args.verdict, args.reason, args.ask, args.resume)):
            raise ValueError("--verdict, --reason, --ask and --resume go with --review")
        if args.instruction is not None and not args.engaged:
            raise ValueError("--instruction goes with --engaged")
        if args.wake is not None:
            woken = continuation.wake(args.config, args.wake)
            if args.json:
                print(json.dumps([task.tid for task in woken]))
            else:
                for task in woken:
                    print(f"{task.tid} woken: {task.title}")
            if not woken:
                sys.exit(1)
            return
        if args.review:
            if not args.verdict or args.reason is None:
                raise ValueError("--review requires --verdict and --reason")
            result = continuation.review(args.config, args.review, args.verdict, args.reason, ask=args.ask, resume=args.resume)
            print(f"{args.review} review {result['verdict']}: {result['reason']}")
            return
        if args.review_context:
            result = continuation.review_context(args.config, args.review_context)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.engaged:
            # A task's budget follows its own owner's engagement: in an organisation, someone else asking about it
            # is interest, not the owner steering it.
            by = args.by or speaker()
            task = next((t for t in sum(load_tasks(args.config), []) if t.tid == args.engaged), None)
            if task is not None and task.owner and by and by != task.owner:
                print(f"{args.engaged} not engaged: {by} is not its owner ({task.owner})")
                return
            continuation.mark_engaged(args.config, args.engaged, args.instruction)
            print(f"{args.engaged} engaged")
            return
        if args.chunk or args.pace or args.checkpoint:
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
            return
        task_list = continuation.finish_lines(args.config)
        worked = continuation.last_worked(args.config)
        chunks = continuation.chunks_today(args.config)
        limits = continuation.budgets(args.config, task_list)
        caps = continuation.budget_caps(args.config)
        if args.tid:
            task = next((t for t in task_list if t.tid == args.tid and t.handoff and tasks_mod.is_open(t)), None)
            if task is None or not task.delivers:
                raise ValueError(f"{args.tid} needs an open handed-off task with a Delivers line")
            ready = [task]
        else:
            ready = tasks_mod.due(task_list, pace="daily" if args.daily else None,
                                 worked=worked, chunks=chunks, daily_chunks=args.config.daily_chunks,
                                 now=tasks_mod.local_now(args.config), budgets=limits, caps=caps)
        output = []
        stage_entries = tasks_mod.all_tasks(args.config)
        for task in ready:
            limit = caps.get(task.tid) if tasks_mod.pace(task) == "daily" else limits.get(task.tid, args.config.daily_chunks)
            earlier = continuation.stages(args.config, task, stage_entries)
            output.append({"id": task.tid, "title": task.title, "state": task.task_state, "pace": tasks_mod.pace(task),
                           "handedOff": task.handoff[0], "report": task.handoff[1], "work": task.work,
                           "after": task.after, "stages": earlier,
                           "delivers": task.delivers, "next": fm.field(task.body, "Next", "Next action"),
                           "lastChunk": worked.get(task.tid), "checkpoint": continuation.instructions(args.config, task, earlier),
                           "chunksToday": chunks.get(task.tid, 0), "budget": limit,
                           "budgetLeft": None if limit is None else max(0, limit - chunks.get(task.tid, 0)),
                           **continuation.review_status(args.config, task.tid)})
        if args.json:
            print(json.dumps(output[0] if args.tid else output, ensure_ascii=False, indent=2))
            return
        if not ready:
            print("nothing handed off is due")
            sys.exit(1)
        for task, row in zip(ready, output):
            if args.tid and row["checkpoint"]:
                print(row["checkpoint"])
            nxt = row["next"] or ""
            print(f"{task.tid} · handed off {task.handoff[0]} · {task.title}" + (f" · next: {nxt[:160]}" if nxt else "")
                  + (f" · work: {task.work}" if task.work else "")
                  + (f" · report in {task.handoff[1]}" if task.handoff[1] else "")
                  + (f" · checkpoint: {row['checkpoint']}" if row["checkpoint"] is not None and not args.tid else ""))
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(2)


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


def cmd_portrait(args):
    if args.action == "seed":
        if not args.reviews:
            raise SystemExit("brain portrait seed: --reviews DIR, the folder of merged PRs' review comments")
        folder = Path(args.reviews).expanduser()
        if not folder.is_dir():
            raise SystemExit(f"brain portrait seed: no folder {folder}")
        data = seed.reading(folder, args.config.today(), args.days)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else seed.render(data))
        return
    if args.reviews or args.days != seed.DAYS:
        raise SystemExit("brain portrait: --reviews and --days go with seed")
    lines = portrait.load(args.config)
    if args.top is not None:
        lines = portrait.top(lines, args.top)
    if args.json:
        print(json.dumps([line.public() for line in lines], ensure_ascii=False, indent=2))
        return
    if not lines:
        print(f"no portrait yet: {args.config.link(args.config.human_portrait)}")
        return
    for line in lines:
        print(" · ".join(part for part in (line.id and f"^{line.id}", line.section, line.text, line.basis, line.confidence,
                                           line.evidence, line.horizon) if part))
        if args.top is not None and line.for_:
            print(f"  for: {line.for_}")


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
          + (f"; {len(args.defer)} step(s) left for a maintainer in {install.DEFERRED}" if args.defer else ""))


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
            if action == "init":
                # Adds what this edition starts with; a routine the vault already has is never touched.
                routines = schedule.load(config)
                added = [name for name in schedule.defaults(config) if name not in routines]
                routines |= {name: schedule.defaults(config)[name] for name in added}
                changed = (f"added {len(added)} {config.edition} routine(s): {', '.join(added)}" if added
                           else f"every {config.edition} routine is already scheduled")
            elif action == "import":
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
                    for key in ("skills", "needs"):
                        if key in changes:
                            changes[key] = [value.strip() for value in changes[key].split(",") if value.strip()]
                    if changes.get("responsibility") == "":
                        changes["responsibility"] = None
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
    vault = argparse.ArgumentParser(add_help=False)
    vault.add_argument("--vault")
    s = sub.add_parser("export-sessions", parents=[vault])
    s.add_argument("--date", type=date.fromisoformat); s.set_defaults(fn=cmd_export_sessions)
    s = sub.add_parser("version", parents=[vault]); s.set_defaults(fn=cmd_version)
    s = sub.add_parser("record-spec", parents=[vault])
    s.add_argument("--json", action="store_true", required=True); s.set_defaults(fn=cmd_record_spec)
    s = sub.add_parser("schedule")
    actions = s.add_subparsers(dest="schedule_cmd", required=True)
    for action in ("list", "set", "remove", "pause", "resume", "check", "import", "init"):
        s = actions.add_parser(action, parents=[vault]); s.set_defaults(fn=cmd_schedule)
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
            for key in ("skills", "prompt", "deliver", "monitor", "script", "workdir", "responsibility", "needs"):
                s.add_argument("--" + key)
            agent = s.add_mutually_exclusive_group()
            agent.add_argument("--agent", action="store_true", default=None)
            agent.add_argument("--no-agent", dest="agent", action="store_false")
            enabled = s.add_mutually_exclusive_group()
            enabled.add_argument("--enabled", action="store_true", default=None)
            enabled.add_argument("--disabled", dest="enabled", action="store_false")
            s.add_argument("--owner", action="store_true", default=None)
    s = sub.add_parser("pull", help="bring the vault's repository up to date under the repository lock", parents=[vault])
    s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("commit", help="commit the vault under the repository lock (scoped to PATHs when given), optionally pushing", parents=[vault])
    s.add_argument("-m", "--message", required=True); s.add_argument("--push", action="store_true")
    s.add_argument("paths", nargs="*", metavar="PATH"); s.set_defaults(fn=cmd_commit)
    s = sub.add_parser("nightly", parents=[vault]); s.add_argument("--date")
    s.add_argument("--write", action="store_true"); s.add_argument("--commit", action="store_true"); s.add_argument("--push", action="store_true")
    s.add_argument("--sessions-dir")
    s.set_defaults(fn=cmd_nightly)
    s = sub.add_parser("ledger")
    ledger_sub = s.add_subparsers(dest="ledger_cmd", required=True)
    s = ledger_sub.add_parser("append", parents=[vault]); s.add_argument("routine", nargs="?")
    s.add_argument("--skill"); s.add_argument("--outcome"); s.add_argument("--account"); s.add_argument("--next")
    s.add_argument("--at"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_ledger_append)
    s = sub.add_parser("journal")
    journal_sub = s.add_subparsers(dest="journal_cmd", required=True)
    s = journal_sub.add_parser("append", parents=[vault]); s.add_argument("--platform", default="demiurge")
    s.add_argument("--user-id", default=""); s.add_argument("--user-id-alt", default="")
    s.add_argument("--at"); s.add_argument("--synthesis", action="store_true"); s.set_defaults(fn=cmd_journal_append)
    s = journal_sub.add_parser("recent", parents=[vault]); s.add_argument("--count", type=int, default=30)
    s.set_defaults(fn=cmd_journal_recent)
    s = sub.add_parser("validate", parents=[vault]); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("access", parents=[vault]); s.add_argument("who")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_access)
    s = sub.add_parser("attribute", parents=[vault])
    source = s.add_mutually_exclusive_group()
    source.add_argument("--file"); source.add_argument("--sessions-dir", help="default: $AGENT_HOME/records/sessions")
    s.add_argument("--ts"); s.add_argument("--reason"); s.add_argument("--write", action="store_true")
    s.set_defaults(fn=cmd_attribute)
    s = sub.add_parser("compile-context", parents=[vault]); s.add_argument("--out")
    s.add_argument("--write", action="store_true"); s.add_argument("--sections")
    s.set_defaults(fn=cmd_compile_context)
    s = sub.add_parser("digest", parents=[vault])
    s.add_argument("--date", type=date.fromisoformat, required=True); s.add_argument("--sessions-dir")
    s.add_argument("--max-words", type=int, default=digest.WORD_LIMIT)
    s.add_argument("--include-routines", action="store_true", help="also digest the agent's own cron sessions")
    s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_digest)
    s = sub.add_parser("retrieve", parents=[vault]); s.add_argument("query")
    s.add_argument("--limit", type=int, default=12); s.add_argument("--json", action="store_true")
    s.add_argument("--form", choices=FORMS, default="names", help="with --json: add each note's index line or body as text")
    s.set_defaults(fn=cmd_retrieve)
    s = sub.add_parser("needs-you", parents=[vault])
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
    s.add_argument("--for", dest="for_", metavar="PERSON", help="list only what is for this person (<platform>:<id>): the asks of tasks they own")
    s.add_argument("--why", metavar="TEXT", help="with --ask: why it needs the owner and what a yes or a no means, in plain words")
    s.set_defaults(fn=cmd_needs_you)
    s = sub.add_parser("loops", help="promises, answers owed and waits on others", parents=[vault])
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--close", metavar="ID_OR_TEXT")
    s.add_argument("--due", nargs="?", const=True, metavar="DATE", help="only due loops, or a due date with --add")
    s.add_argument("--quiet", action="store_true", help="print nothing; exit 1 when nothing is due, 0 otherwise")
    s.add_argument("--kind", choices=loops.KINDS); s.add_argument("--source", metavar="NOTE")
    s.add_argument("--with", dest="with_", metavar="WHO"); s.add_argument("--outcome", choices=loops.OUTCOMES)
    s.add_argument("--because", metavar="TEXT"); s.set_defaults(fn=cmd_loops)
    s = sub.add_parser("ideas", help="proposals for new ways to help, shown one at a time", parents=[vault])
    s.add_argument("--all", action="store_true")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--respond", metavar="ID_OR_TEXT")
    s.add_argument("--source", metavar="NOTE"); s.add_argument("--why", metavar="TEXT")
    s.add_argument("--needs", metavar="TEXT"); s.add_argument("--answer", choices=ideas.ANSWERS)
    s.set_defaults(fn=cmd_ideas)
    s = sub.add_parser("channels", help="the channel registry; `sync` registers connect grants; `mail --since` lists new owner mail headers", parents=[vault])
    s.add_argument("action", nargs="?", choices=("sync", "mail")); s.add_argument("--run", action="store_true"); s.add_argument("--profile", default="")
    s.add_argument("--since", help="with mail: ISO datetime or YYYY-MM-DD, in the owner's zone unless offset is given")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_channels)
    s = sub.add_parser("people", help="Demiurge's identity directory")
    actions = s.add_subparsers(dest="action", required=True)
    p = actions.add_parser("sync", help="sync stdin JSON, or --run for `demiurge intellects --json`", parents=[vault])
    p.add_argument("--run", action="store_true"); p.set_defaults(fn=cmd_people)
    p = actions.add_parser("who", help="identify a person or intellect and find their vault profile", parents=[vault])
    p.add_argument("ident"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_people)
    s = sub.add_parser("task", help="create a task or change one: done, drop, yours, reopen, set",
                       description="brain task TID {done,drop,yours,reopen,set} [fields]; brain task new TITLE --source SOURCE", parents=[vault])
    s.add_argument("tid", metavar="TID|new", help="task id, or new to create a task")
    s.add_argument("action", metavar="ACTION|TITLE", help="done, drop, yours, reopen, set, view (JSON on stdin); with new, the task title")
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
    s.set_defaults(fn=cmd_task)
    s = sub.add_parser("completed", help="tasks completed recently, newest first, for the follow-on check", parents=[vault])
    s.add_argument("--days", type=int, default=7); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_completed)
    s = sub.add_parser("handoff", help="mark a task the owner handed off: a background worker works it until done or blocked", parents=[vault])
    s.add_argument("tid"); s.add_argument("--report", default="", help="where to report, e.g. 'Home' or the room")
    s.add_argument("--work", help="Library work slug, e.g. 'trip-plan'")
    s.add_argument("--for", dest="for_", metavar="PERSON", help="who the task is for, as <platform>:<id> (enterprise: the speaker by default)")
    s.set_defaults(fn=cmd_handoff)
    s = sub.add_parser("works", help="summaries in local Library works")
    actions = s.add_subparsers(dest="action", required=True)
    p = actions.add_parser("view", help="save a work's summary from JSON on stdin; an empty view removes it", parents=[vault])
    p.add_argument("slug"); p.set_defaults(fn=cmd_works)
    s = sub.add_parser("continue", help="handed-off tasks due now, least recently worked first; text exits 1 when none", parents=[vault])
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--due", action="store_true")
    s.add_argument("tid", nargs="?", metavar="TASK", help="show one task and its next-chunk instructions")
    mode.add_argument("--chunk", metavar="TASK")
    mode.add_argument("--pace", nargs=2, metavar=("TASK", "regular|daily"))
    mode.add_argument("--checkpoint", metavar="TASK", help="save a per-task continue checkpoint")
    mode.add_argument("--engaged", metavar="TASK", help="record that the owner engaged with the task now (its daily budget is whole again)")
    s.add_argument("--by", metavar="PERSON", help="with --engaged: who engaged, as <platform>:<id> (default: the speaker); only the task's owner counts")
    mode.add_argument("--review-context", metavar="TASK", help="reviewer context as JSON")
    mode.add_argument("--review", metavar="TASK", help="apply a direction review")
    mode.add_argument("--wake", metavar="REASON", help="end open tasks' rests on computer")
    s.add_argument("--verdict", choices=("converging", "met", "circling", "stuck"))
    s.add_argument("--reason", help="one-line review reason")
    s.add_argument("--ask", help="with a circling review: a concrete steering question")
    s.add_argument("--resume", help="with a stuck review: computer or YYYY-MM-DD[ HH:MM], local time")
    s.add_argument("--instruction", help="with --engaged: the owner's latest words for the reviewer")
    s.add_argument("--text", help="with --checkpoint: where the chunk stopped")
    s.add_argument("--json", action="store_true", help="due array (empty exits 0), TASK/review-context object, or woken ids (empty exits 1)")
    s.add_argument("--daily", action="store_true", help="list daily-pace tasks")
    s.set_defaults(fn=cmd_continue)
    s = sub.add_parser("watch-signal", help="stable follow-through monitor output, without a model", parents=[vault])
    s.set_defaults(fn=cmd_watch_signal)
    s = sub.add_parser("continue-signal", help="stable due-handoff monitor output, without a model", parents=[vault])
    s.add_argument("--daily", action="store_true"); s.set_defaults(fn=cmd_continue_signal)
    s = sub.add_parser("stale-tasks", help="open tasks gone quiet long enough for the evening review", parents=[vault])
    s.set_defaults(fn=cmd_stale_tasks)
    s = sub.add_parser("portrait", help="the agent's ranked understanding of the owner, with line ids", parents=[vault])
    s.add_argument("action", nargs="?", choices=["seed"], help="seed: what to read to seed an organisation's Taste")
    s.add_argument("--reviews", metavar="DIR", help="seed: one file per merged PR with its review comments")
    s.add_argument("--days", type=int, default=seed.DAYS, help=f"seed: PRs merged in the last N days (default {seed.DAYS})")
    s.add_argument("--top", type=int, metavar="N", help="only the top N After lines, with what each asks attention to watch")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_portrait)
    s = sub.add_parser("state", parents=[vault])
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_state)
    s = sub.add_parser("health", parents=[vault]); s.add_argument("--date")
    s.add_argument("--json", action="store_true"); s.add_argument("--write", action="store_true", help="write <runtime_dir>/health.md")
    s.set_defaults(fn=cmd_health)
    s = sub.add_parser("audit", parents=[vault]); s.add_argument("--date"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--sessions-dir"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("show-skill", help="print the package's current text of one routine, rendered for this vault", parents=[vault])
    s.add_argument("name"); s.set_defaults(fn=cmd_show_skill)
    s = sub.add_parser("people-mentioned", parents=[vault]); s.add_argument("text")
    s.add_argument("--form", choices=("line", "card"), default="line")
    s.set_defaults(fn=cmd_people_mentioned)
    s = sub.add_parser("session-context", parents=[vault])
    for option in ("platform", "user-id", "user-id-alt", "user-name", "chat-id"):
        s.add_argument("--" + option, default="")
    s.set_defaults(fn=cmd_session_context)
    s = sub.add_parser("simulate", parents=[vault]); s.add_argument("--h", type=int); s.add_argument("--w", type=int)
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("responsibility", help="build a hub, an own skill and a records folder for standing work", parents=[vault])
    s.add_argument("name"); s.add_argument("--skill", required=True); s.add_argument("--records")
    s.set_defaults(fn=cmd_responsibility)
    s = sub.add_parser("timezone", help="print the owner's timezone from brain.toml", parents=[vault]); s.set_defaults(fn=cmd_timezone)
    s = sub.add_parser("install-skills", parents=[vault]); s.add_argument("--force", action="store_true", help="also overwrite the day routines")
    s.set_defaults(fn=cmd_install_skills)
    s = sub.add_parser("update", parents=[vault]); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("migrate-check", parents=[vault]); s.set_defaults(fn=cmd_migrate_check)
    s = sub.add_parser("redact", parents=[vault]); s.add_argument("paths", nargs="+"); s.set_defaults(fn=cmd_redact)
    s = sub.add_parser("migrate-done", parents=[vault])
    s.add_argument("--defer", action="append", default=[], metavar="STEP",
                   help="a step left for a maintainer (repeatable); it goes to <runtime_dir>/deferred-steps.md, never to the owner's Needs you")
    s.set_defaults(fn=cmd_migrate_done)
    s = sub.add_parser("install-scripts", parents=[vault]); s.set_defaults(fn=cmd_install_scripts)
    s = sub.add_parser("paths", parents=[vault])
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
