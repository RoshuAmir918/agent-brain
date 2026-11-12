"""The `brain` command line: argparse and one cmd_* function per subcommand."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
from contextlib import nullcontext
import time
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from . import access, attribute, compile_context, consults, digest, evidence, fm, health, identity, ideas, image, items, journal, ledger, loops, needs, owners, portrait, reception, retrieve, seed, session_context, state, tellable, validate, views, works
from . import tasks as tasks_mod
from . import goals, responsibilities, roles
from .tasks import load_tasks, quiet
from .vault import Vault
from .config import FORMS, SECTIONS, Config
from .lock import repo_lock, vault_lock
from .nightly import STAGES, preview, run
from . import install
from . import signals
from . import coverage, record, routing, runtime, schedule, sessions
from .sources import chats as chats_source, mail as mail_source, plans as plan_source, reviews as review_source


SHELL_EXPANDED = re.compile(r"(?:^|[\s(]),\d{3}\b")   # what "$3,450" leaves once the shell takes "$3"

# ---------- git

def sh(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(root: Path, message: str, paths: list[str] | None = None, *, locked: bool = False) -> str:
    """Stage and commit under the repository lock: the given paths, or else the agent's notes and session context.
    Only those paths are committed; anything else already staged stays staged. Returns the short SHA, or '' when there
    was nothing to commit."""
    config = Config.load(root)
    repo = config.repo
    if paths:
        spec = [str(Path(p).resolve()) for p in paths]
    else:
        tree = root.relative_to(repo).as_posix()
        context = [".context.md"] if (repo / ".context.md").exists() else []   # a fresh vault has none until compiled
        spec = [tree, f":(exclude){tree}/.brain", *context]
    with nullcontext() if locked else repo_lock(config):   # locked: the caller holds the repository lock
        sh(repo, "add", "-A", "--", *spec)
        staged = [name for name in sh(repo, "diff", "--cached", "--name-only", "--no-renames", "-z", "--", *spec).split("\0") if name]
        if not staged:
            return ""
        # The staged names, not the spec: a pathspec git does not know (an empty folder) would fail the commit.
        author = ("-c", f"user.email={config.agent_email}", "-c", f"user.name={config.agent_name}") if config.agent_email else ()
        sh(repo, *author, "commit", "-q", "-m", message, "--", *(f":(literal){name}" for name in staged))
        return sh(repo, "rev-parse", "--short", "HEAD")


def kept_path(config: Config) -> Path:
    """Where a pull records each file whose clash it settled for the pushed side; never committed."""
    return roles.scratch(config) / "kept.jsonl"


def _rebasing(repo: Path) -> bool:
    return any(Path(sh(repo, "rev-parse", "--path-format=absolute", "--git-path", name)).exists()
               for name in ("rebase-merge", "rebase-apply"))


def _pull(repo: Path, suffix: str = "") -> None:
    """Pull with rebase; caller holds the repository lock. On a conflict the pulled side wins: what was pushed is a
    person's edit (the agent pushes only from here), and a person's edit is never overwritten. The agent's side of each
    clashing hunk is dropped and recorded, so nothing is lost. Anything else (no credentials, no network) is git's own
    error, already printed, and is said as such."""
    try:
        sh(repo, "pull", "--rebase", "--autostash", "-q", "origin", "main")
        return
    except subprocess.CalledProcessError:
        if not _rebasing(repo):
            sys.exit(f"pull failed (git's error is above: credentials or network, not a conflict){suffix}")
        subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)
    # Every file both sides changed, each with the agent's last commit to it: where they clashed, that is what is dropped.
    # Every file any local commit touched (a change and its undo can still clash), each with the agent's last commit
    # to it; matched after the retry against everything it pulled, which may be more than the first attempt saw.
    base = sh(repo, "merge-base", "HEAD", "FETCH_HEAD")
    mine = {name: sh(repo, "log", "-1", "--abbrev=12", "--format=%h", f"{base}..HEAD", "--", name)
            for name in set(sh(repo, "log", "--format=", "--name-only", f"{base}..HEAD").splitlines()) if name}
    try:
        # During a rebase "ours" is the branch being rebased onto: the pulled commits.
        sh(repo, "pull", "--rebase", "--autostash", "-q", "-X", "ours", "origin", "main")
    except subprocess.CalledProcessError:
        if _rebasing(repo):
            subprocess.run(["git", "-C", str(repo), "rebase", "--abort"], capture_output=True)
        sys.exit(f"pull --rebase conflicted even keeping the pulled side; resolve it by hand{suffix}")
    theirs = set(sh(repo, "log", "--format=", "--name-only", f"{base}..FETCH_HEAD").splitlines())
    clashed = {name: sha for name, sha in mine.items() if name in theirs}
    if not clashed:
        return
    record = kept_path(Config.load(repo))
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open("a", encoding="utf-8") as stream:
        for name, sha in sorted(clashed.items()):
            stream.write(json.dumps({"at": date.today().isoformat(), "file": name, "agent": sha}) + "\n")
    print(f"pull: kept the pushed edit over the agent's in {', '.join(sorted(clashed))}; the agent's versions: "
          + ", ".join(f"{name} in {sha}" for name, sha in sorted(clashed.items())), file=sys.stderr)


def push(root: Path) -> None:
    config = Config.load(root)
    repo = config.repo
    with repo_lock(config):
        _pull(repo, ", nothing was pushed")   # rows written after the commit wait
        # Large pushes have hit HTTP 400: retry with a bigger post buffer, then over HTTP/1.1, before giving up.
        for wait, options in ((0, ()), (5, ("-c", "http.postBuffer=52428800")),
                              (15, ("-c", "http.version=HTTP/1.1", "-c", "http.postBuffer=104857600"))):
            time.sleep(wait)
            try:
                sh(repo, *options, "push", "-q", "origin", "HEAD:main")
                return
            except subprocess.CalledProcessError:
                pass
        sys.exit("push failed (git's error is above); the commit is kept locally")


def cmd_pull(a):
    """`brain pull`: bring the vault up to date under the repository lock."""
    with repo_lock(a.config):
        _pull(a.config.repo)
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
    report = run(root, today, sessions_dir, stage=a.stage) if a.write else preview(root, today, sessions_dir, a.stage)
    for ln in report:
        print(ln)
    if not a.write:
        print("(report only; nothing written)")
        return
    if a.commit:
        summary = "; ".join(ln for ln in report if not ln.startswith("frontmatter"))[:900]
        label = "sleep1" if a.stage == "all" else f"night-{a.stage}"
        sha = commit(root, f"{label}({today.isoformat()}): {summary or 'no changes'}")
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
    """Reads one after-session record as JSON on stdin."""
    try:
        at = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else args.config.now(datetime.now(args.config.zone))
        record = json.load(sys.stdin)
        if not isinstance(record, dict):
            raise ValueError("needs one JSON object")
        session = args.session or (os.environ.get("DEMIURGE_CONVERSATION", "").strip() if args.platform == "demiurge" else "")
        written = journal.append_entry(args.config, record, args.platform, at,
                                       user_id=args.user_id, user_id_alt=args.user_id_alt, session=session)
        print(written or "skipped: nothing worth recording")
    except (OSError, ValueError) as error:
        sys.exit(f"journal append: {error}")


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
    try:
        if a.name == "adopt" and not a.skill:
            if any(getattr(a, key) is not None for key in ("records", "when", "script", "prompt", "delivers", "body")):
                raise ValueError("adopt takes routines with --into or an optional --title and reason, or --hub")
            for change in responsibilities.adopt(a.config, ([a.title] if a.title else []) + a.routines,
                                                 hub=a.hub, title=a.adopt_title, into=a.into,
                                                 asked_by=a.asked_by, goal=a.goal, role=a.role):
                print(change)
            return
        if a.routines or a.hub is not None or a.adopt_title is not None or a.into is not None:
            raise ValueError("extra routines, --hub, --title and --into are only supported by adopt")
        if a.name == "sync" and not a.skill:
            if a.title:
                raise ValueError("sync takes no title")
            for change in responsibilities.sync(Vault(a.config.vault_path)):
                print(change)
            return
        if a.name != "add" or a.title is None:
            if not a.skill or a.title:
                raise ValueError('use add "<Title>" --when "<when>", sync, or "<Name>" --skill <name>')
            records = a.records or a.skill.removeprefix(a.config.skill_prefix)
            for path in install.responsibility(a.config, a.name, a.skill, records):
                print(path)
            print(install.records_path() / records)
            return
        if not any(value and value.strip() for value in (a.goal, a.role, a.asked_by)):
            raise ValueError("a responsibility names its reason: --goal, --role or --asked-by")
        if not a.when:
            raise ValueError("add requires --when")
        if a.body not in (None, "-"):
            raise ValueError("use --body - to read the wake-up document from stdin")
        ident = re.sub(r"[^a-z0-9]+", "-", a.title.lower()).strip("-")[:63].rstrip("-")
        if not re.fullmatch(responsibilities.ID_PATTERN, ident):
            raise ValueError(f"responsibility id must match ^{responsibilities.ID_PATTERN}$")
        data = {"version": 1, "title": a.title,
                "triggers": [{"kind": "schedule", "when": a.when,
                              **{key: getattr(a, key) for key in ("script", "prompt") if getattr(a, key) is not None}}],
                **{key: getattr(a, key) for key in ("goal", "role", "asked_by", "delivers", "skill") if getattr(a, key) is not None}}
        target = a.config.vault_path / "Responsibilities" / ident / "RESPONSIBILITY.md"
        body = sys.stdin.read() if a.body == "-" else f"# {a.title}\n\nWrite what this responsibility is for, how to do it, and what each run leaves visible.\n"
        with vault_lock(a.config):
            if target.parent.exists():
                raise ValueError(f"responsibility folder already exists: Responsibilities/{ident}")
            if a.goal and a.goal not in {goal.gid for goal in goals.load(a.config) + goals.archived(a.config)}:
                raise ValueError(f"unknown goal {a.goal!r}; use `brain goals list`")
            if a.role:
                known = [fm.read(roles.folder(a.config, name) / roles.ROLE_NOTE)[0].get("id") for name in roles.names(a.config)]
                if not re.fullmatch(r"R-[0-9]+", a.role) or a.role not in known:
                    raise ValueError(f"unknown role {a.role!r}; use `brain roles list`")
            responsibilities.derive(a.config, {**data, "id": ident, "hub": target.relative_to(a.config.vault_path).as_posix(),
                                              "records": f"Responsibilities/{ident}/records"})
            target.parent.mkdir(parents=True)
            fm.write(target, data, body)
        print(target)
        for change in responsibilities.sync(Vault(a.config.vault_path)):
            print(change)
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(f"responsibility: {error}")


def cmd_attribute(a):
    directory = Path(a.sessions_dir).resolve() if a.sessions_dir else install.sessions_records_path()
    for use in attribute.sessions(Vault(a.config.vault_path), directory, a.write):
        print(use.line())


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
    with vault_lock(a.config):
        events = goals.capture(a.config)["app"]
        if a.write:
            result = compile_context.write(vault, Path(a.out) if a.out else None)
        else:
            text = compile_context.build(vault, only)
            print(text, end="")
            result = compile_context.summary(text, a.config.context.hot_words)
        if not a.write and events and a.config.edition == "personal" and "goals" in a.config.context.sections and (only is None or "goals" in only):
            goals.acknowledge(a.config, events)
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


def in_conversation() -> bool:
    """A turn in a Demiurge conversation, not a background worker's: the only agent turn whose edits are engagement."""
    return bool(os.environ.get("DEMIURGE_CONVERSATION", "").strip()) and not os.environ.get("DEMIURGE_TASK", "").strip()


def cmd_needs_you(args):
    if args.source and not (args.ask or args.confirm):
        raise SystemExit("brain needs-you: --source goes with --ask or --confirm")
    if args.why and not (args.ask or args.confirm or args.yours is not None):
        raise SystemExit("brain needs-you: --why goes with --ask, --confirm or --resolve --yours")
    if args.yours is not None and not args.resolve:
        raise SystemExit("brain needs-you: --yours goes with --resolve")
    if args.delivers is not None and args.yours is None:
        raise SystemExit("brain needs-you: --delivers goes with --resolve --yours")
    if args.because and not args.resolve:
        raise SystemExit("brain needs-you: --because goes with --resolve")
    if args.confirm:
        with vault_lock(args.config):
            journal.add_ask(args.config, args.config.today(), args.confirm, args.source, why=args.why or "", kind="confirm")
        return
    if args.ask:
        from .needs import vague_ask
        if (reason := vague_ask(args.ask)):
            print(f"ask refused: {reason}", file=sys.stderr)
            sys.exit(2)
        with vault_lock(args.config):
            here = os.environ.get("DEMIURGE_CONVERSATION", "").strip()
            journal.add_ask(args.config, args.config.today(), args.ask, args.source, why=args.why or "",
                            session=f"demiurge:{here}" if here else "", hub=args.hub or "")
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
        if not args.because and args.yours is None:
            raise SystemExit("brain needs-you: --resolve needs --because \"<the evidence>\"")
        with vault_lock(args.config):
            try:
                item = needs.resolve(args.config, args.resolve, args.because or "", args.config.today(),
                                     owner_engaged=in_conversation(), by=speaker(), yours=args.yours,
                                     delivers=args.delivers, why=args.why)
            except ValueError as error:
                print(error, file=sys.stderr)
                sys.exit(1)
        print(f"{item.tid} created: {item.title}" if isinstance(item, tasks_mod.Task) else f"resolved {item.kind}: {item.text}")
        return
    if args.dismiss:
        with vault_lock(args.config):
            try:
                item = needs.dismiss(args.config, args.dismiss, args.config.today(), args.reason, by=speaker() or "")
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
            added = loops.add(config, now, args.kind, args.add, args.source, args.with_ or "", args.due or "", args.task or "",
                              args.condition or "", hub=args.hub or "")
            print(f"{'added' if added else 'already open'} loop: {args.add}")
            return
        if args.kind or args.source is not None or args.with_ is not None or isinstance(args.due, str) or args.task or args.condition:
            raise ValueError("--kind, --source, --with, --task, --if and --due DATE go with --add")
        if args.close is not None:
            if not args.outcome or not args.because:
                raise ValueError("--close needs --outcome done|dropped and --because TEXT")
            item = loops.close(config, args.close, now, args.outcome, args.because)
            print(f"closed ({item.outcome}): {item.text}" + (f"; the answer is on {item.task}, which resumes if it was blocked on it" if item.outcome == "done" and item.task else ""))
            return
        if args.outcome or args.because is not None:
            raise ValueError("--outcome and --because go with --close")
    except ValueError as error:
        raise SystemExit(f"brain loops: {error}")
    due = loops.stale(config, now) if args.stale else loops.due(config, now)
    if args.quiet:
        raise SystemExit(0 if due else 1)
    if args.json:
        print(json.dumps({"open": [loops.as_dict(item) for item in loops.open_loops(config)],
                          "due": [loops.as_dict(item) for item in loops.due(config, now)],
                          "stale": [loops.as_dict(item) for item in loops.stale(config, now)],
                          "closed_recent": [loops.as_dict(item) for item in loops.closed_recent(config)]},
                         ensure_ascii=False, indent=2))
    else:
        for item in due if args.due or args.stale else loops.open_loops(config):
            details = ((f" · with: {item.with_}" if item.with_ else "") + (f" · due: {item.due}" if item.due else "")
                       + (f" · if: {item.if_}" if item.if_ else "") + (f" · wake: {item.wake}" if item.wake else "")
                       + (f" · ready: {item.ready}" if item.ready else ""))
            print(f"{item.id} · {item.since} · {item.kind} · {item.text} · [[{item.source}]]{details}")


def cmd_item(args):
    try:
        done = items.answer(args.config, args.id, args.answer, args.words or "", args.by or "")
    except ValueError as error:
        raise SystemExit(f"brain item: {error}")
    print(f"{args.answer}: {getattr(done, 'text', None) or getattr(done, 'title', '')}")


def cmd_owners(args):
    rows = owners.load(args.config)
    people = owners.people(args.config)
    for problem in owners.problems(args.config):
        print(f"skipped: {problem}", file=sys.stderr)
    if args.json:
        print(json.dumps([asdict(row) for row in rows] + [dict(person=person, name=hours.name, hours=hours.schedule(),
                         away=hours.away, away_start=hours.away_start) for person, hours in people.items()],
                         ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.area} · {row.owner_name} ({row.owner}) · backup: {row.backup or '-'} · "
                  f"{row.covers} · {row.wait_hours}h · {row.source}")
        for person, hours in people.items():
            days = ",".join(owners.DAYS[day - 1] for day in hours.days)
            away = f"{hours.away_start} to {hours.away}" if hours.away_start else f"until {hours.away}"
            print(f"{hours.name} ({person}) · {days} {hours.start}-{hours.end} {hours.zone}"
                  + (f" · away: {away}" if hours.away else ""))


def cmd_evidence(args):
    if runtime.configured() is None:
        raise SystemExit("brain evidence: AGENT_HOME is not set")
    if args.action == "import":
        if not args.source:
            raise SystemExit("brain evidence import: --source NAME")
        from . import sources
        declared = sources.registry().get(args.source)
        if declared and args.config.edition not in declared.source.editions:
            raise SystemExit(f"brain evidence import: {args.source} is read for {' and '.join(declared.source.editions)} "
                             f"brains, and this one is {args.config.edition}; nothing imported")
        rows = []
        for number, line in enumerate(sys.stdin, 1):
            if not line.strip():
                continue
            try:
                rows.append(evidence.from_json(json.loads(line)))
            except (ValueError, TypeError) as error:   # json.JSONDecodeError is a ValueError
                raise SystemExit(f"brain evidence import: line {number}: {error}; nothing imported")
        try:
            added = evidence.append(args.source, rows)
        except ValueError as error:
            raise SystemExit(f"brain evidence import: {error}")
        print(json.dumps({"source": args.source, "rows": len(rows), "new": added}) if args.json
              else f"{args.source}: {len(rows)} rows, {added} new")
        return
    rows = evidence.read(args.source or "", args.hint or "", date.fromisoformat(args.since) if args.since else None)
    if args.about is not None:
        recalled = evidence.recall(args.about, rows, args.config.today(), args.limit)
        if args.json:
            print(json.dumps([dict(asdict(entry.row), date=entry.row.date.isoformat(), hints=list(entry.row.hints),
                                   score=entry.score, shared=list(entry.shared), outcomes=list(entry.outcomes))
                              for entry in recalled], ensure_ascii=False, indent=2))
            return
        for entry in recalled:
            print(f"{entry.score:g} · {entry.row.source}: {evidence.render(entry.row)[2:]}"
                  + (f" · outcomes: {' > '.join(entry.outcomes)}" if entry.outcomes else "")
                  + f" · shares: {', '.join(entry.shared)}")
        return
    if args.json:
        print(json.dumps([dict(asdict(row), date=row.date.isoformat(), hints=list(row.hints)) for row in rows],
                         ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.source}: {evidence.render(row)[2:]}")


def cmd_source(args):
    from . import sources
    registry = sources.registry()
    if args.target == "list":
        counts: dict[str, tuple[int, str]] = {}
        for row in evidence.read():
            n, newest = counts.get(row.source, (0, ""))
            counts[row.source] = (n + 1, max(newest, row.date.isoformat()))
        config = getattr(args, "config", None)
        mine = {source.id for source in sources.declared(config.edition)} if config else set(registry)
        names = sorted(mine | set(counts))   # this edition's sources, and any whose evidence is already here
        listed = [{"id": name, "registered": name in registry, "rows": counts.get(name, (0, ""))[0],
                   "newest": counts.get(name, (0, ""))[1],
                   **({"runs": registry[name].source.runs, "editions": list(registry[name].source.editions),
                       "where": registry[name].source.where, "needs": registry[name].source.needs,
                       "facets": list(registry[name].source.facets), "sensitive": registry[name].source.sensitive,
                       "extracts": registry[name].extract is not None,
                       "about": registry[name].source.about} if name in registry else {})} for name in names]
        if args.json:
            print(json.dumps(listed, ensure_ascii=False, indent=2))
            return
        for entry in listed:
            how = (f"{entry['runs']}{', sensitive' if entry['sensitive'] else ''} · {entry['about']}" if entry["registered"]
                   else "written as it happens")
            print(f"{entry['id']} · {entry['rows']} rows" + (f", newest {entry['newest']}" if entry["rows"] else "") + f" · {how}")
        return
    if args.target not in registry:
        raise SystemExit(f"brain source: no source {args.target!r}; `brain source list` shows them")
    if args.action == "script":
        module = importlib.import_module(f"brain.sources.{args.target}")
        if not hasattr(module, "script"):
            raise SystemExit(f"brain source {args.target}: reads where the vault is; it has no script to carry")
        print(module.script(), end="")
        return
    if args.action != "extract":
        raise SystemExit(f"brain source {args.target}: say extract or script")
    if registry[args.target].extract is None:
        raise SystemExit(f"brain source {args.target}: the agent reads this one with its own tools and the seed skill")
    home = Path(args.home).expanduser() if args.home else runtime.configured()
    if home is None and not args.jsonl:
        raise SystemExit("brain source: AGENT_HOME is not set; pass --home DIR to keep this evidence somewhere")
    config = getattr(args, "config", None)
    today = config.today() if config else date.today()
    ctx = sources.Context(today, date.fromisoformat(args.since) if args.since else None,
                          Path(args.input).expanduser() if args.input else None, config)
    try:
        rows = registry[args.target].extract(ctx)
    except sources.Nothing as error:
        raise SystemExit(f"brain source {args.target}: nothing to read ({error})")
    except ValueError as error:
        raise SystemExit(f"brain source {args.target}: {error}")
    if args.jsonl:   # for a machine that holds no evidence: carried elsewhere and read with `brain evidence import`
        for row in rows:
            print(json.dumps(evidence.to_json(row), ensure_ascii=False))
        return
    added = evidence.append(args.target, rows, home)
    print(f"{args.target}: {len(rows)} rows read, {added} new → {evidence.folder(home) / args.target}")


def cmd_consult(args):
    config = args.config
    try:
        consults.require_enterprise(config)
        if args.consult_cmd == "autonomy":
            entries = consults.autonomy(config, args.area)
            if args.json:
                print(json.dumps(entries, ensure_ascii=False, indent=2))
            else:
                for entry in entries:
                    print(f"{entry['area']} · {entry['rung']} · ceiling {entry['ceiling']}"
                          + (" · restricted" if entry["restricted"] else "") + f"\n  {entry['needed']}")
                    for row in entry["window"]:
                        print(f"  {row['date']} · {row['outcome']} · {row['id']} · [[{row['link']}]]")
                    for event in entry["events"]:
                        print(f"  {event['date']} · {event['kind']} · {event['from']} -> {event['to']}"
                              + (f" · ceiling {event['ceiling']}" if event["kind"] == "ceiling" else "")
                              + (f" · {event['id']}" if "id" in event else "") + f" · [[{event['link']}]]")
                        for row in event.get("window", []):
                            print(f"    {row['date']} · {row['outcome']} · {row['id']} · [[{row['link']}]]")
        elif args.consult_cmd == "act":
            print(consults.act(config, args.area, args.for_, args.task or "").id)
        elif args.consult_cmd == "reverse":
            consults.reverse(config, args.which, args.by, args.answer)
            print(f"{args.which}: reversal recorded")
        elif args.consult_cmd == "lower":
            consults.lower(config, args.area, args.by, args.rung)
            print(f"{args.area}: lower recorded")
        elif args.consult_cmd == "ceiling":
            consults.ceiling(config, args.area, args.rung, args.by)
            print(f"{args.area}: ceiling recorded")
        elif args.consult_cmd == "ask":
            entry = consults.ask(config, args.to, args.ask, args.default, args.why, args.area or "", args.task or "",
                                 args.restricted, args.not_covered)
            print(entry.id)
        elif args.consult_cmd == "decisions":
            entries = consults.decisions(config, args.about, args.area or "", args.limit)
            if args.json:
                print(json.dumps(entries, ensure_ascii=False, indent=2))
            else:
                for entry in entries:
                    print(f"{entry['id']} · {entry['area']} · {entry['date']} · {entry['name']} · {entry['status']}\n"
                          f"  {entry['words']}")
                    if entry["question"]:
                        print(f"  asked: {entry['question']}")
                    if entry["source"]:
                        print(f"  source: {entry['source']}")
                    for overrule in entry["overrules"]:
                        print(f"  overruled {overrule['date']} by {overrule['name']}: {overrule['words']}")
                    if replacement := entry["superseded"]:
                        print(f"  superseded {replacement['date']} by {replacement['id']}: {replacement['name']}")
                    print(f"  [[{entry['link']}]]\n")
        elif args.consult_cmd == "cite":
            consults.cite(config, args.which, args.for_, args.task or "")
            print(f"{args.which}: citation recorded")
        elif args.consult_cmd == "decide":
            print(consults.decide(config, args.area, args.by, args.words, args.source, args.supersedes or ""))
        elif args.consult_cmd == "answer":
            entry = consults.answer(config, args.which, args.outcome, args.answer, args.by, args.to or "")
            result = f"moved to {entry.to}" if args.outcome == "redirected" else "decision recorded"
            print(f"{entry.id}: {args.outcome}; {result}")
        elif args.consult_cmd == "overrule":
            consults.overrule(config, args.which, args.answer, args.by)
            print(f"{args.which}: overrule recorded")
        elif args.consult_cmd == "delegate":
            consults.delegate(config, args.area, args.to, args.by, args.words)
            print(f"{args.area}: delegation recorded for {args.to}")
        elif args.consult_cmd == "seed":
            if args.account is not None and not args.mail:
                raise ValueError("--account goes with --mail")
            if args.mail and args.dry_run:
                raise ValueError("--dry-run goes with --reviews or --claims; --mail writes nothing")
            if args.claims and args.days is not None:
                raise ValueError("--days goes with --reviews or --mail")
            days = routing.DAYS if args.days is None else args.days
            if days < 0:
                raise ValueError("--days must be non-negative")
            today = config.today()
            if args.mail:
                data = mail_source.deference_reading(Vault(config.vault_path), today, args.account or "all", days)
                print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else mail_source.render_deference(data))
                return
            if args.reviews:
                folder = Path(args.reviews).expanduser()
                if not folder.is_dir():
                    raise ValueError(f"no folder {folder}")
                rows, problems = review_source.routing_evidence(folder, today, days)
            else:
                path = Path(args.claims).expanduser()
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, ValueError):
                    raise ValueError(f"unreadable claims file {path}") from None
                rows, problems = mail_source.deference_claims(text)
            areas = {}
            for row in rows:
                areas.setdefault(row.area, dict(changed=0, reviewed=0, approved=0) if args.reviews
                                 else {"deferred-to": 0})[row.kind] += 1
            result = dict(found=len(rows),
                          added=0 if args.dry_run else consults.record_evidence(config, rows),
                          areas=areas, problems=problems)
            if args.reviews:
                result["prs"] = len(review_source.load(folder, today, days)[0])
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print((f"{result['prs']} PRs read, " if args.reviews else "")
                      + f"{result['found']} lines found, {result['added']} lines added")
                for area, counts in areas.items():
                    print(f"{area} · " + " · ".join(f"{kind} {count}" for kind, count in counts.items()))
                for problem in problems:
                    print(f"problem: {problem}")
        elif args.consult_cmd == "candidates":
            ranked = consults.candidates(config, args.about, args.area or "", args.limit)
            if args.json:
                print(json.dumps([asdict(entry) for entry in ranked], ensure_ascii=False, indent=2))
            else:
                for entry in ranked:
                    hours = f"{entry.median_hours:g}h" if entry.median_hours is not None else "-"
                    evidence = "; ".join(f"{item['area']}: {item['kind']} ({item['text']})" for item in entry.evidence[:3])
                    print(f"{entry.person} · {entry.name} · {entry.score:.2f} · median {hours}"
                          + (" · muted" if entry.muted else "")
                          + (f" · away: {entry.away}" if entry.away else "") + f" · {evidence}")
        elif args.consult_cmd == "lapse":
            for action in consults.lapse(config, dry_run=args.dry_run):
                print(action)
        elif args.consult_cmd == "list":
            entries = [entry for entry in consults.load(config) if not args.for_ or entry.to == args.for_]
            if args.json:
                hours_of = owners.hours_lookup(config)
                print(json.dumps([{**asdict(entry.item(config, hours_of)), "asked": entry.asked, "until": entry.until,
                                                      "tried": entry.tried, "restricted": entry.restricted}
                                  for entry in entries], ensure_ascii=False, indent=2))
            else:
                for entry in entries:
                    print(f"{entry.id} · {entry.kind} · {entry.area} · {entry.to} · {entry.text}")
        elif args.consult_cmd == "stats":
            result = consults.stats(config)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for dimension in ("areas", "people"):
                    rows = result[dimension]
                    for key, counts in rows.items():
                        share = f"{counts['changed_share']:.0%}" if counts['changed_share'] is not None else "-"
                        hours = f"{counts['median_hours']:g}h" if counts['median_hours'] is not None else "-"
                        print(f"{dimension} · {key} · asked {counts['asked']} · changed {counts['changed']} · "
                              f"confirmed {counts['confirmed']} · redirected {counts['redirected']} · "
                              f"overruled {counts['overruled']} · unanswered {counts['unanswered']} · "
                              f"changed share {share} · median {hours}")
                for week in result["weekly"]:
                    print(f"{week['week']} · resolved {week['resolved']} · redirected {week['redirected']} · {week['rate']:.0%}")
                for week in result["record_weekly"]:
                    print(f"{week['week']} · cited {week['cited']} · asked {week['asked']} · record share {week['record_share']:.0%}")
    except ValueError as error:
        raise SystemExit(f"brain consult: {error}")


def cmd_ideas(args):
    config, today = args.config, args.config.today()
    if (args.why is not None or args.needs is not None or args.goal is not None) and args.add is None:
        raise SystemExit("brain ideas: --why, --needs and --goal go with --add")
    if args.source is not None and args.add is None and args.respond is None:
        raise SystemExit("brain ideas: --source goes with --add or --respond")
    if args.answer and args.respond is None:
        raise SystemExit("brain ideas: --answer goes with --respond")
    if (args.because is not None) != (args.withdraw is not None):
        raise SystemExit("brain ideas: --withdraw and --because go together")
    try:
        if args.withdraw is not None:
            item = ideas.withdraw(config, args.withdraw, today, args.because)
            print(f"withdrawn: {item.text}")
            return
        if args.add is not None:
            if not args.source:
                goals.guard(config, args.goal, "")
                raise ValueError("--add needs --source NOTE")
            ideas.add(config, today, args.add, args.source, args.why or "", args.needs or "", args.goal)
            print(f"added idea: {args.add}")
            return
        if args.respond is not None:
            if not args.answer:
                raise ValueError("--respond needs --answer yes|not-now|no|stop-asking")
            item = ideas.respond(config, args.respond, today, args.answer, args.source or "")
            print(f"{args.answer}: {item.text}")
            return
    except ValueError as error:
        raise SystemExit(f"brain ideas: {error}")
    opened = ideas._open(Vault(config.vault_path), today)
    snoozed = ideas._snoozed(config, today)
    until = ideas.cooldown_until(config, today)
    item = None if until else next((idea for idea in opened if idea.id not in snoozed), None)
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
    audience = args.who in ("known", "anyone")
    matches = [] if audience else access.matching_people(vault, config, args.who)
    if len(matches) > 1:
        raise SystemExit("brain access: ambiguous person; matching profiles: "
                         + ", ".join(config.link(note.rel) for note in matches))
    result = (access.resolve(vault, config, audience=access.Audience(args.who)) if audience
              else access.resolve(vault, config, matches[0].rel if matches else None))
    if args.json:
        if not audience and not matches:
            print(f"brain access: no profile matches {args.who!r}; resolving as stranger.", file=sys.stderr)
        data = {"person": result.person, "groups": result.groups,
                "subjects": {subject: {"tell": value.tell, "tellRule": value.tell_rule,
                                       "do": value.do, "doRule": value.do_rule}
                             for subject, value in result.subjects.items()}, "scope": result.scope}
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    person = config.link(result.person) if result.person else (
        args.who if audience else f"stranger (no profile matches {args.who!r})")
    print(f"Person: {person} · Groups: {', '.join(config.link(rel) for rel in result.groups) or 'none'}")
    for subject, value in result.subjects.items():
        if value.tell != "none" or value.do != "no":
            print(f"{subject}: {value.tell} ({value.tell_rule or 'default'}) / {value.do} ({value.do_rule or 'default'})")
    print("Everything else: none / no")
    for text in dict.fromkeys(result.scope.values()):
        print(f"Scope: {text}")


def cmd_people(args):
    from . import directory
    if args.action == "friends":
        data = access.friends(Vault(args.config.vault_path), args.config.today())
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return
        print("Close friends group: " + ("exists" if data["exists"] else "none"))
        for item in data["candidates"]:
            print(f"- {item['name']} · {item['access']} access requests · {item['intellects']} intellects · "
                  f"{item['noted']} Noted lines · {args.config.link(item['profile'])}")
        return
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
    profiles = [note.rel for note in identity.profiles(Vault(args.config.vault_path))
                if f"demiurge:{entry['id']}" in identity.profile_ids(note.body)]
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
        with vault_lock(args.config):
            added = registry.sync(args.config, out.splitlines(), args.profile or None)
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


def cmd_roles(args):
    """`brain roles`: the organisation's roles, their templates and what each has learned (docs/roles.md)."""
    config, action = args.config, args.roles_cmd or "list"
    try:
        if action == "list":
            for name in roles.names(config):
                index = roles.read_index(config, name)
                print(f"{name} · {roles.load(config, name).template} · {len(index.lessons)} lines · {len(index.struck)} struck")
            for problem in roles.problems(config):
                print(f"problem: {problem}", file=sys.stderr)
        elif action == "add":
            with vault_lock(config):
                written = roles.add(config, args.name, args.template)
            for path in written:
                print(path.relative_to(config.vault_path).as_posix())
        elif action == "rebuild":
            for name in [args.name] if args.name else roles.names(config):
                # Under both locks, so a pull can't land a person's edit between reading a note and tagging it; and
                # committed at once, so the ids are history before the rebuild reads blame.
                with repo_lock(config), vault_lock(config):
                    tagged = roles.assign_ids(config, name)
                    if tagged:
                        commit(config.vault_path, f"{roles.IDS_COMMIT} ({name})",
                               [str(config.vault_path / rel) for rel in tagged], locked=True)
                index = roles.rebuild(config, name)
                print(f"{name}: {len(index.lessons)} lines, {sum(l.human for l in index.lessons)} by people, "
                      f"{len(index.struck)} struck" + (f", {len(tagged)} note(s) given ids" if tagged else ""))
                for problem in index.problems:
                    print(f"problem: {problem}", file=sys.stderr)
        elif action == "show":
            index = roles.read_index(config, args.name)
            if args.json:
                print(json.dumps(asdict(index), ensure_ascii=False, indent=2))
            else:
                for lesson in index.lessons:
                    print(f"^{lesson.id} · {lesson.status} · {'person' if lesson.human else 'learned'} · {lesson.note} · "
                          f"{fm.cut(lesson.text, 20)}")
                for entry in index.struck:
                    print(f"^{entry.id} · struck ({entry.how}) by {entry.by} on {entry.at} · {fm.cut(entry.text, 20)}")
        elif action == "may-learn":
            reason = roles.refusal(roles.read_index(config, args.name), args.claim, args.evidence)
            if reason:
                sys.exit(f"refused: {reason}")
            print("ok")
        elif action == "screen":
            from . import screen
            if args.op == "next":
                since = date.fromisoformat(args.since) if args.since else None
                print(json.dumps(screen.requests(config, args.name, since, args.limit), ensure_ascii=False))
            elif args.op == "record":
                answered = json.loads(sys.stdin.read() or "[]")
                if not isinstance(answered, list):
                    sys.exit("record takes a JSON list of answers on stdin")
                print(f"{screen.record(config, args.name, answered)} item(s) labelled")
            else:
                if not args.sample:
                    sys.exit("calibrate takes --sample FILE (JSON lines: id, kind, kept)")
                sample = [json.loads(line) for line in Path(args.sample).read_text(encoding="utf-8").splitlines() if line.strip()]
                print(json.dumps(screen.calibrate(config, args.name, sample)))
        elif action == "study":
            from . import screen
            if args.op == "next":
                text = screen.next_digest(config, args.name, args.limit)
                print(text or "nothing new to study")
            else:
                print(f"{screen.done(config, args.name)} item(s) marked studied")
        elif action == "work":
            from . import corrections
            if args.op == "add":
                if not args.id:
                    sys.exit(f"name the work: brain roles work {args.name} add ID")
                added = corrections.add_work(config, args.name, args.id, args.title or "", args.link or "")
                print(f"registered {args.id}" if added else f"{args.id} was already registered")
            else:
                for work in corrections.works(config, args.name).values():
                    print(f"{work.id} · {work.opened} · {work.title}" + (f" · {work.link}" if work.link else ""))
        elif action == "correction":
            from . import corrections
            if args.op == "add":
                missing = [flag for flag in ("work", "event", "by", "at", "text") if not getattr(args, flag)]
                if missing:
                    sys.exit("add needs " + ", ".join(f"--{flag}" for flag in missing))
                row = corrections.add_event(config, args.name, args.work, args.event, args.by, args.at, args.text,
                                            source_id=args.source_id or "", where=args.where or "", link=args.link or "")
                print(f"recorded {row.id} ({row.speaker}, weight {row.weight:g})" if row else "already recorded")
            elif args.op == "next":
                waiting = corrections.pending(config, args.name)
                if args.json:
                    print(json.dumps([asdict(event) for event in waiting], ensure_ascii=False, indent=2))
                for event in [] if args.json else waiting:
                    print(f"{event.id} · {event.event} ({event.weight:g}) · {event.work} · {event.at} · {event.by} "
                          f"({event.speaker})" + (f" on {event.where}" if event.where else "") + f": {fm.cut(event.text, 60)}")
                if not waiting and not args.json:
                    print("no corrections waiting")
            elif args.op == "file":
                if not args.event_id:
                    sys.exit("file takes the correction's id")
                print(corrections.file(config, args.name, args.event_id, note=args.note or "", section=args.section or "",
                                       claim=args.claim or "", to=(args.to or "").lstrip("^"), rule=args.rule,
                                       decides=args.decides or "", person=args.person or "", always=args.always,
                                       miss=args.miss or ""))
            else:
                if not args.event_id or not args.why:
                    sys.exit("dismiss takes the correction's id and --why")
                corrections.dismiss(config, args.name, args.event_id, args.why)
                print(f"dismissed {args.event_id}")
        elif action == "gate":
            from . import gate
            decisions = json.loads(sys.stdin.read() or "[]")
            if not isinstance(decisions, list):
                sys.exit("gate takes the plan's decisions as a JSON list on stdin")
            verdicts = gate.sort(config, args.name, decisions)
            gate.save(config, args.name, args.work or "", verdicts)
            print(json.dumps([asdict(v) for v in verdicts], ensure_ascii=False, indent=2))
        elif action == "actions":
            try:
                listed = json.loads(subprocess.run(["demiurge", "connections", "list", "--json"], capture_output=True,
                                                   text=True, check=True, timeout=60).stdout or "[]")
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                sys.exit(f"couldn't list the connections (demiurge connections list): {error}")
            resolved = roles.actions(config, args.name, listed if isinstance(listed, list) else [])
            if args.json:
                print(json.dumps(resolved, ensure_ascii=False, indent=2))
            else:
                for entry in resolved:
                    how = "; ".join(f"use {s['connection']} {s['use']}" + ("" if s["ready"] else " (switched off)") for s in entry["by"])
                    print(f"{entry['action']} · {entry['status']}" + (f" · {how}" if how else
                          " · no connection does this: connect one that does, such as a git host for git actions"))
            if any(entry["status"] != "ready" for entry in resolved):
                sys.exit(1)
        elif action == "lenses":
            role = roles.load(config, args.name)
            notes = [str(roles.folder(config, args.name) / f"{shape}.md") for shape in role.shapes()
                     if (roles.folder(config, args.name) / f"{shape}.md").is_file()]
            print(json.dumps({"lenses": role.lenses, "decisions": role.decisions, "notes": notes, "process": role.process},
                             ensure_ascii=False, indent=2))
        elif action == "routines":
            with vault_lock(config):
                added = roles.ensure_routines(config)
            print("added " + ", ".join(added) if added else "every role has its routines")
        elif action == "predict":
            from . import account
            print(f"{account.predict(config, args.name, args.work, args.expect or [])} prediction(s) kept for {args.work}")
        elif action == "asked":
            from . import account
            account.asked(config, args.name, args.work, args.verdict, args.about or "")
            print(f"recorded: the ask on {args.work} was {args.verdict}")
        elif action == "account":
            from . import account
            report = account.account(config, args.name, args.days)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else account.render(report))
        elif action == "template":
            if args.op == "check":
                with vault_lock(config):
                    found = roles.template_check(config)
                for name, path in found:
                    print(f"{name}: proposed {path.relative_to(config.vault_path).as_posix()}")
                if not found:
                    print("every role's template is current")
            elif not args.name:
                sys.exit(f"name the role: brain roles template {args.op} NAME")
            elif args.op == "accept":
                with vault_lock(config):
                    print(f"merged into {roles.template_accept(config, args.name).relative_to(config.vault_path)}")
            else:
                with vault_lock(config):
                    roles.template_decline(config, args.name)
                print(f"kept {args.name}'s template as it is")
    except ValueError as error:
        sys.exit(str(error))


def cmd_goals(args):
    config = args.config
    which, action = args.which, args.action
    operations = ("edit", "evidence", "reached", "superseded-by", "set-aside", "reopen", "nudge", "asked-missed", "link", "keep", "not-a-goal", "to-task", "account", "weight", "correct")
    if which in operations and action and goals.ID.fullmatch(action):
        which, action = action, which
    try:
        operation = action if goals.ID.fullmatch(which) else which
        if args.json and which not in ("list", "show", "due", "refused", "activity"):
            raise ValueError("--json goes with list, show, due, refused or activity")
        if args.from_app and operation not in ("new", "edit", "reached", "superseded-by", "set-aside", "reopen", "keep", "not-a-goal", "to-task", "weight", "correct"):
            raise ValueError("--from-app goes with new, edit, reached, superseded-by, set-aside, reopen, keep, not-a-goal, to-task, weight or correct")
        if args.weeks is not None and which != "activity":
            raise ValueError("--weeks goes with activity")
        if args.week is not None and operation != "account":
            raise ValueError("--week goes with account")
        allowed = {"new": {"words", "reach", "by", "said", "inferred", "evidence"}, "edit": {"title", "words", "kind", "by", "clear_by", "said"},
                   "evidence": {"text"}, "link": {"text"}, "reached": {"why", "evidence"},
                   "superseded-by": {"text", "why", "evidence"}, "set-aside": {"why"}, "reopen": {"why"}, "not-a-goal": {"why"},
                   "account": {"text"}, "weight": {"text"}, "correct": {"text", "why"}}
        supplied = {name for name in ("words", "title", "why", "reach", "kind", "by", "clear_by", "said", "text", "inferred", "evidence")
                    if getattr(args, name) is not None and getattr(args, name) is not False}
        if supplied - allowed.get(operation, set()):
            raise ValueError(f"{operation} does not take " + ", ".join(sorted(supplied - allowed.get(operation, set()))))
        if which in ("list", "due", "refused") and action is not None:
            raise ValueError(f"{which} takes no positional arguments")
        if which == "activity":
            entries = goals.activity(config, action, weeks=args.weeks if args.weeks is not None else 2)
            print(json.dumps(entries, ensure_ascii=False, indent=2) if args.json else
                  "\n".join(f"{gid} · {item['at'] or 'undated'} · {item['state']} · {item['text']}"
                            for gid, items in entries.items() for item in items) or "no goal activity")
            return
        if which == "refused":
            entries = goals.capture(config)["refused"]
            print(json.dumps(entries, ensure_ascii=False, indent=2) if args.json else
                  "\n".join(f"{entry['title']} — {entry['words']} ({entry['why']})" for entry in entries) or "no refused goals")
            return
        if which in ("list", "show", "due"):
            entries = goals.due(config) if which == "due" else goals.load(config)
            if which == "show":
                entries = [entry for entry in entries if entry.gid == action]
                if not entries:
                    raise ValueError(f"{action} is not a goal; use `brain goals list`")
            if args.json:
                corrections = goals.capture(config)["corrections"]
                print(json.dumps([{**entry.public(), **({"stale": entry.state.get("stale"),
                                                       "reasons": goals.due_reasons(config, entry)} if which == "due" else {}),
                                   "corrections": [{"id": ident, **value}
                                   for ident, value in corrections.get(entry.gid, {}).items()]}
                                  for entry in entries], ensure_ascii=False, indent=2))
            else:
                for entry in entries:
                    print(entry.render().rstrip() if which == "show" else f"{entry.gid} · {entry.state.get('state')} · {entry.title}"
                          + (" · " + "; ".join(goals.due_reasons(config, entry)) if which == "due" else ""))
                if not entries:
                    print("no goals")
            return
        with vault_lock(config):
            if which == "new":
                entry = goals.new(config, action or "", args.words or "", reach=args.reach, by=args.by, said=args.said,
                                  inferred=args.inferred, evidence=args.evidence, from_app=args.from_app)
            elif goals.ID.fullmatch(which) and action in operations:
                if action in ("account", "weight", "correct"):
                    entry = goals.steer(config, which, action, args.text or "", why=args.why, from_app=args.from_app,
                                        account_week=args.week)
                    print(f"{entry.gid} · {action}: {args.text}")
                    return
                if action == "link":
                    goals.link_note(config, which, args.text or "")
                    print(f"{which} linked: {args.text}")
                    return
                entry = goals.change(config, which, action, text=args.text, title=args.title, words=args.words,
                                     by=args.by, clear_by=args.clear_by, kind=args.kind, why=args.why, said=args.said,
                                     evidence=args.evidence, from_app=args.from_app)
                if action == "to-task":
                    print(f"{which} · became {entry.tid}")
                    return
            else:
                raise ValueError("use new, list, show G-n, activity [G-n], due, refused, or G-n edit|evidence|reached|superseded-by|set-aside|reopen|nudge|asked-missed|link|keep|not-a-goal|to-task|account|weight|correct")
        print(f"{entry.gid} · {entry.state['state']} · {entry.title}")
    except ValueError as error:
        raise SystemExit(f"brain goals: {error}")


def cmd_task(args):
    try:
        values = {name: getattr(args, name) for name in tasks_mod.FIELDS if getattr(args, name) is not None
                  and not (name == "work" and args.tid == "new")}
        if args.tid == "new":
            if not args.source:
                goals.guard(args.config, args.goal, "")
                raise ValueError("task new needs --source")
            if args.clear or args.title is not None or args.reviewed is not None:
                raise ValueError("--clear, --title and --reviewed go with task T-nnn set")
            if args.from_ is not None and not args.from_.strip():
                raise ValueError("--from must name an existing task")
            if args.handoff and args.state not in (None, "ready", "in_progress"):
                raise ValueError("--handoff is for your own work; owner work (--state yours) is never handed off")
        elif args.action not in ("done", "drop", "yours", "set", "reopen", "view"):
            raise ValueError("task ACTION must be done, drop, yours, reopen, set or view")
        elif args.source is not None or args.json or args.handoff:
            raise ValueError("--source, --json and --handoff go with task new")
        elif args.from_ is not None:
            raise ValueError("--from goes with task new")
        elif (args.later or args.now) and args.action != "set":
            raise ValueError("--later and --now go with task new or task T-nnn set")
        elif (args.left is not None or args.then) and args.action != "done":
            raise ValueError("--left and --then go with task T-nnn done")
        if args.later and args.now:
            raise ValueError("--later or --now, not both")
        if args.tid == "new" and args.now:
            raise ValueError("--now goes with task T-nnn set; new owner work is now unless --later")
        if args.action != "set" and args.tid != "new" and (set(values) - {"why"} or args.state is not None or args.clear
                                       or args.reviewed is not None or args.report is not None or args.title is not None):
            raise ValueError("field flags, --state, --title, --reviewed, --report and --clear go with task T-nnn set")
        if args.said is not None and (args.tid != "new" and args.action != "set" or args.delivers is None):
            raise ValueError("--said goes with task new|set --delivers")
        if args.action == "view":
            if values or args.why:
                raise ValueError("task T-nnn view takes its view as JSON on stdin, nothing else")
            if tasks_mod.find(args.config, args.tid, open_only=True) is None:
                raise ValueError(f"{args.tid} is not an open task")
            view = views.write(args.config, args.tid, sys.stdin.read())
            print(f"{args.tid} view saved: {len(view['pieces'])} pieces" if view else f"{args.tid} view removed")
            return
        if args.by_owner and (args.tid == "new" or args.action not in ("done", "drop")):
            raise ValueError("--by-owner goes with task T-nnn done or drop")
        if args.action in ("done", "drop", "reopen") and not args.why and not args.by_owner:
            # Without --why only the owner's own Done or Drop in the app closes a task, and it says so.
            raise ValueError(f"task {args.action} needs --why \"<the evidence or the reason>\"")
        with vault_lock(args.config):
            if args.tid == "new":
                task = tasks_mod.new_task(args.config, args.action, args.source, state="ready" if args.state is None else args.state,
                                          values=values, work=args.work, agent_delivers=not in_conversation(), said=args.said,
                                          from_task=args.from_, report=None if args.handoff else args.report, later=args.later)
                handed = hand_off(args, task.tid) if args.handoff else None
            elif args.action == "reopen":
                task = tasks_mod.reopen(args.config, args.tid, args.config.today(), args.why)
            elif args.action in ("set", "yours"):
                fields = values if args.action == "set" else ({"status": args.why} if args.why else None)
                task = tasks_mod.set_fields(args.config, args.tid, args.config.today(),
                                            state="yours" if args.action == "yours" else args.state,
                                            values=fields, clear=args.clear or (),
                                            reviewed=args.reviewed, report=args.report, title=args.title,
                                            agent_delivers=not in_conversation(), said=args.said,
                                            later=True if args.later else False if args.now else None)
            else:
                task = tasks_mod.close(args.config, args.tid, args.config.today(), args.action == "done",
                                       args.why or "the owner closed it in their app", args.left, args.then or (),
                                       by_owner=args.by_owner)
            # A conversation's task edit is engagement; worker writes never are.
            if in_conversation():
                from . import continuation
                continuation.mark_engaged(args.config, task.tid, args.source if args.tid == "new" else
                                          args.next or args.delivers or args.status or args.why, speaker())
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    if args.tid == "new":
        print(json.dumps({"id": task.tid, "title": task.title, **({"handoff": handed} if handed else {})}, ensure_ascii=False)
              if args.json else f"{task.tid} created: {task.title}" + (f"\n{handed}" if handed else ""))
    elif args.action == "set":
        fields = ((["state"] if args.state is not None else []) + list(values) + [f"cleared {name}" for name in args.clear or ()]
                  + (["reviewed"] if args.reviewed else []) + (["report"] if args.report is not None else [])
                  + (["later"] if args.later else ["now"] if args.now else []))
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
        if args.action == "goal":
            with vault_lock(args.config):
                works.set_goal(args.config, args.slug, args.goal)
            print(f"{args.slug} toward {args.goal}")
            return
        view = works.write_view(args.slug, sys.stdin.read())
    except (ValueError, RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        if views.KIT not in str(error):
            print(views.KIT, file=sys.stderr)
        sys.exit(1)
    print(f"{args.slug} view saved: {len(view['pieces'])} pieces" if view else f"{args.slug} view removed")


def hand_off(args, tid: str) -> str:
    """Hands `tid` to a background worker under the vault lock the caller holds, and says what happens next."""
    # On Demiurge the runtime names the conversation this turn is in: the report goes back where the work was asked for.
    here = os.environ.get("DEMIURGE_CONVERSATION", "").strip()
    report = f"demiurge:{here}" if here and not (args.report or "").startswith("demiurge:") else (args.report or "")
    # In an organisation's intellect a task is the person's who handed it off: the runtime names the speaker.
    owner = getattr(args, "for_", None) or (speaker() if args.config.edition == "enterprise" else None)
    task = tasks_mod.hand_off(args.config, tid, args.config.today(), report, work=args.work, owner=owner)
    from . import continuation
    continuation.mark_engaged(args.config, task.tid, by=speaker())
    where = task.handoff[1] if task.handoff else ""
    if os.environ.get("DEMIURGE_WORKERS", "").strip() == "1":
        # The runtime starts a background worker for the task within a minute, and it works until done; this turn ends so
        # the two do not work the same task at once.
        return (f"{task.tid} handed off {task.handoff[0] if task.handoff else ''}: a background worker starts on it within "
                "a minute and works it until it is done, blocked or needs them; its steps show live in the task's thread. "
                "Tell them in one line that you are on it now and they will hear when it is done or needs them, then end "
                "your reply.")
    return (f"{task.tid} handed off {task.handoff[0] if task.handoff else ''}: worked by a background worker until done or "
            f"blocked{', reported in ' + where if where else ''}.")


def cmd_handoff(args):
    try:
        with vault_lock(args.config):
            said = hand_off(args, args.tid)
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    print(said)


def cmd_continue(args):
    from . import continuation
    try:
        if (args.text is not None) != bool(args.checkpoint):
            raise ValueError("--checkpoint TASK requires --text; --text goes with --checkpoint")
        if args.tid and (args.due or args.chunk or args.pace or args.checkpoint or args.engaged or args.extend
                         or args.review or args.review_context or args.wake is not None):
            raise ValueError("TASK cannot be combined with another continue mode")
        if (args.chunk or args.pace or args.checkpoint or args.engaged or args.extend or args.review) and args.json:
            raise ValueError("--json goes with --due, TASK, --review-context or --wake")
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
        if args.extend:
            by = args.by or speaker()
            with vault_lock(args.config):
                limit = continuation.extend(args.config, args.extend, by)
            print(f"{args.extend} not extended: {by} is not its owner" if limit is None else
                  f"{args.extend} extended: today's budget is {limit} chunks")
            return
        if args.engaged:
            by = args.by or speaker()
            with vault_lock(args.config):
                engaged = continuation.mark_engaged(args.config, args.engaged, args.instruction, by)
            if engaged:
                print(f"{args.engaged} engaged")
            else:
                print(f"{args.engaged} not engaged: {by} is not its owner")
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
        finish_report = []
        task_list = continuation.finish_lines(args.config, finish_report)
        for line in finish_report:
            print(line, file=sys.stderr)
        worked = continuation.last_worked(args.config)
        chunks = continuation.chunks_today(args.config)
        caps = continuation.budget_caps(args.config)
        limits = continuation.budgets(args.config, task_list, caps)
        if args.tid:
            task = next((t for t in task_list if t.tid == args.tid and t.handoff and tasks_mod.is_open(t)), None)
            if task is None:
                raise ValueError(f"{args.tid} needs an open handed-off task")
            ready = [task]
        else:
            ready = tasks_mod.due(task_list, worked=worked, chunks=chunks, daily_chunks=args.config.daily_chunks,
                                 now=tasks_mod.local_now(args.config), budgets=limits, caps=caps)
        output = []
        stage_entries = tasks_mod.all_tasks(args.config)
        due_now = ({t.tid for t in tasks_mod.due(ready, worked=worked, chunks=chunks, daily_chunks=args.config.daily_chunks,
                                                 now=tasks_mod.local_now(args.config), budgets=limits, caps=caps)}
                   if args.tid else {t.tid for t in ready})
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
                           "status": continuation.status(args.config, task, chunks.get(task.tid, 0), limit, task.tid in due_now),
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
            if args.tid:
                print(f"Status: {row['status']}.")
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(2)


def cmd_watch_signal(args):
    print(signals.watch(args.config))


def cmd_explore_signal(args):
    print(signals.explore(args.config))


def cmd_stale_tasks(args):
    tasks, archived = load_tasks(args.config)
    from . import continuation
    for task, touched in quiet(tasks + archived, args.config.today(), continuation.last_worked(args.config)):
        line = f"{task.tid} · {task.task_state or 'open'} · {task.state.get('tier', 'untiered')} · last touched {touched or 'never'} · {task.title}"
        print(line + (f" · {task.first_status()}" if task.first_status() else ""))


def cmd_state(args):
    if args.stamp:
        print(state.stamp(args.config))
        return
    data = state.build(args.config, args.config.today())
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(" · ".join(f"{len(data[key])} {key}" for key in
                         ("tasks", "hubs", "goals", "routines", "ledger", "needsYou", "outputs", "prepared", "loops", "loopsClosed")))


def cmd_reception(args):
    config, today = args.config, args.config.today()
    try:
        if args.action == "add":
            if not (args.kind and args.what and args.source):
                raise ValueError("add needs --kind, --what and --source")
            with vault_lock(config):
                at = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=config.zone) if args.at else None
                print(reception.record(config, args.kind, args.what, args.source, args.portrait or "", at))
        elif args.action == "set":
            if not (args.id and args.outcome):
                raise ValueError("set needs ID and OUTCOME")
            with vault_lock(config):
                row = reception.set_outcome(config, args.id, args.outcome)
            print(f"{row.id} {row.outcome}: {row.kind} {row.what}")
        elif args.action == "burden":
            if not (args.reviews and args.author):
                raise ValueError("burden needs --reviews DIR and --author LOGIN")
            data = reception.burden(Path(args.reviews).expanduser(), args.author, today, args.weeks)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else reception.render_burden(data))
        else:
            data = reception.summary(config, today, args.days)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else reception.render_summary(data))
    except ValueError as error:
        raise SystemExit(f"brain reception: {error}")


def cmd_tellable(args):
    config = args.config
    vault = Vault(config.vault_path)
    try:
        result = tellable.resolve(vault, config, args.who)
    except ValueError as error:
        raise SystemExit(f"brain tellable: {error}")
    audience = args.who in ("known", "anyone")
    if args.json:
        if not audience and result["person"] is None:
            print(f"brain tellable: no profile matches {args.who!r}; resolving as stranger.", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    person = config.link(result["person"]) if result["person"] else (
        args.who if audience else f"stranger (no profile matches {args.who!r})")
    print(f"Person: {person} · Groups: {', '.join(config.link(rel) for rel in result['groups']) or 'none'}")
    print(tellable.render(result))


def cmd_portrait(args):
    if args.action != "seed" and (args.reviews or args.plans or args.account or args.chats or args.days is not None
                                  or args.clear_backfill or args.hand_off or args.clear):
        raise SystemExit("brain portrait: --reviews, --plans, --account, --chats, --days, --clear-backfill, --hand-off and "
                         "--clear go with seed")
    if args.action == "stamp":
        print(f"{image.stamp(args.config)} new line id(s)")
        return
    if args.action == "compile":
        try:
            with vault_lock(args.config):
                count = image.compile(args.config)
        except ValueError as error:
            raise SystemExit(f"brain portrait compile: {error}")
        print("no facets yet; the portrait is written by hand until its lines are moved into facets" if count is None
              else f"compiled {count} line(s) from {len(image.facets(args.config))} facet(s)")
        return
    if args.action == "facets":
        found = image.facets(args.config)
        if args.json:
            print(json.dumps([{"facet": f.name, "rank": f.rank, "sensitivity": f.sensitivity, "note": f.rel,
                               "lines": [line.public() for line in f.lines]} for f in found], ensure_ascii=False, indent=2))
            return
        for f in found:
            newest = max((line.evidence for line in f.lines), default="none")
            print(f"{f.rank} · {f.name} · {len(f.lines)} lines · {f.sensitivity} · newest {newest}")
        if not found:
            print("no facets yet")
        return
    if args.action == "due":
        found = image.due(args.config)
        if args.json:
            print(json.dumps(found, ensure_ascii=False, indent=2))
            return
        for entry in found:
            print(f"{entry['facet']}{'' if entry['exists'] else ' (new)'} · {entry['new']} rows since "
                  f"{entry['since'] or 'the start'} · newest {entry['newest']} · {', '.join(entry['sources'])}")
        if not found:
            print("no facet has new evidence")
        return
    if args.action == "evidence":
        if not args.id:
            raise SystemExit("brain portrait evidence: FACET [--since YYYY-MM-DD | --all]")
        rows = image.evidence_for(args.config, args.id, "" if args.all else args.since)
        if args.json:
            print(json.dumps([evidence.to_json(row) | {"source": row.source} for row in rows], ensure_ascii=False, indent=2))
            return
        for row in rows:
            print(f"{row.source}: {evidence.render(row)[2:]}")
        if not rows:
            print(f"no new evidence for {args.id}")
        return
    if args.action == "judged":
        if not args.id:
            raise SystemExit("brain portrait judged: FACET [--through YYYY-MM-DD]")
        try:
            with vault_lock(args.config):
                path = image.judged(args.config, args.id, date.fromisoformat(args.through) if args.through
                                       else args.config.today(), args.empty or "")
        except ValueError as error:
            raise SystemExit(f"brain portrait judged: {error}")
        print(f"{path.stem}: judged through {args.through or args.config.today().isoformat()}")
        return
    if args.action == "open":
        if not args.id:
            raise SystemExit("brain portrait open: FACET")
        try:
            with vault_lock(args.config):
                path = image.open_facet(args.config, args.id)
        except ValueError as error:
            raise SystemExit(f"brain portrait open: {error}")
        print(f"{path.stem}: {path.relative_to(args.config.vault_path).as_posix()}")
        return
    if args.action == "move":
        if not (args.id and args.verdict):
            raise SystemExit("brain portrait move: ID and FACET")
        try:
            with vault_lock(args.config):
                line = image.move(args.config, args.id, args.verdict)
        except ValueError as error:
            raise SystemExit(f"brain portrait move: {error}")
        print(f"^{line.id} {line.text} -> {line.facet}")
        return
    if args.action == "mark":
        if not (args.id and args.verdict):
            raise SystemExit("brain portrait mark: ID and one of " + ", ".join(portrait.VERDICTS))
        try:
            with vault_lock(args.config):
                line = image.mark(args.config, args.id, args.verdict, args.words or "", args.config.today())
        except ValueError as error:
            raise SystemExit(f"brain portrait mark: {error}")
        print(f"{args.verdict}: ^{line.id} {line.text}")
        return
    if args.action == "coverage":
        data = coverage.coverage(Vault(args.config.vault_path), args.config.today())
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else coverage.render_coverage(data))
        return
    if args.action == "seed":
        if args.hand_off and args.clear_backfill:
            raise SystemExit("brain portrait seed: --hand-off and --clear-backfill are separate runs")
        if args.clear_backfill and not args.account:
            raise SystemExit("brain portrait seed: --clear-backfill requires --account ADDRESS|all")
        if sum(bool(value) for value in (args.reviews, args.plans, args.account, args.chats)) != 1:
            raise SystemExit("brain portrait seed: one of --reviews DIR (merged PRs' review comments, for Taste) or "
                             "--plans DIR (projects and the issues done on them, for After and Energy) or --account ADDRESS|all "
                             "or --chats FILE (what the owner asked ChatGPT or Claude)")
        if args.clear and not args.chats:
            raise SystemExit("brain portrait seed: --clear goes with --chats")
        if args.chats:
            if args.clear_backfill or args.days is not None or (args.hand_off and args.clear):
                raise SystemExit("brain portrait seed --chats: --hand-off, --clear or neither")
            path = Path(args.chats).expanduser()
            try:
                if args.hand_off:
                    with vault_lock(args.config):
                        data = seed.hand_off_chats(Vault(args.config.vault_path), path, args.config.today())
                    print(json.dumps(data, ensure_ascii=False) if args.json else
                          f"{data['account']}: {data['status']}" + (f" · {data['task']}" if data["task"] else ""))
                elif args.clear:
                    print(f"{path.name}: {'removed' if chats_source.clear(path) else 'already gone'}")
                elif not path.is_file():
                    raise SystemExit(f"brain portrait seed: no chat export at {path}")
                else:
                    data = chats_source.reading(path)
                    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else chats_source.render(data))
            except ValueError as error:
                raise SystemExit(f"brain portrait seed: {error}")
            return
        if args.account:
            if args.hand_off:
                vault = Vault(args.config.vault_path)
                try:
                    with vault_lock(args.config):
                        data = seed.hand_off_seed(vault, args.account, args.config.today())
                except ValueError as error:
                    raise SystemExit(f"brain portrait seed: {error}")
                print(json.dumps(data, ensure_ascii=False) if args.json else
                      f"{data['account']}: {data['status']}" + (f" · {data['task']}" if data["task"] else ""))
                return
            if args.clear_backfill:
                data = seed.clear_backfill(Vault(args.config.vault_path), args.account)
                print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else "\n".join(
                    f"{name}: removed {counts['mail']} backfill mail files, {counts['calendar']} calendar files"
                    for name, counts in data.items()) or "removed 0 backfill mail files, 0 calendar files")
                return
            data = seed.personal(Vault(args.config.vault_path), args.config.today(), args.account, args.days)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else seed.render_personal(data))
            return
        folder = Path(args.reviews or args.plans).expanduser()
        if not folder.is_dir():
            raise SystemExit(f"brain portrait seed: no folder {folder}")
        today = args.config.today()
        if args.reviews:
            data = review_source.reading(folder, today, args.days or routing.DAYS)
        else:
            data = plan_source.direction(folder, today, args.days or plan_source.PLAN_DAYS)
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json
              else review_source.render(data) if args.reviews else plan_source.render_direction(data))
        return
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


def cmd_health(args):
    data = health.report(args.config, date.fromisoformat(args.date) if args.date else None)
    if args.write:
        print(health.write(args.config, data))
    elif args.json:
        print(health.to_json(data))
    else:
        print(health.render(data), end="")


def cmd_task_answer(args):
    vault = Vault(args.config.vault_path)
    user_ids = tuple(value for value in (args.user_id, args.user_id_alt) if value)
    if identity.role(vault, args.config, args.platform, user_ids) != "intellect":
        sys.exit("task-answer records an answer from another intellect, and this speaker is not one")
    agent = identity.matching_agents(vault, args.config, user_ids, args.platform)[0]
    name = Path(agent.rel).parent.name.replace("_", " ")
    try:
        print(loops.answer(args.config, args.tid, agent.rel, name, args.config.now(), args.answer, args.next))
    except ValueError as error:
        sys.exit(str(error))


def cmd_session_context(args):
    vault = Vault(args.config.vault_path)
    text = session_context.build(vault, args.config,
                                 platform=args.platform, user_id=args.user_id,
                                 user_id_alt=args.user_id_alt, user_name=args.user_name,
                                 chat_id=args.chat_id)
    owner = identity.is_owner(args.config, args.platform, tuple(value for value in (args.user_id, args.user_id_alt) if value))
    routine = args.platform == "cron" and not (args.user_id or args.user_id_alt)
    if args.config.edition == "personal" and "goals" in args.config.context.sections and (owner or routine):
        with vault_lock(args.config) if args.ack_notices else nullcontext():
            events = goals.capture(args.config)["app"]
            if events:
                text = (text + "\n\n" if text else "") + "## Goals changed in the app\n\n" + goals.notices(args.config)
                if args.ack_notices:
                    goals.acknowledge(args.config, events)
    if args.json:
        who = identity.role(vault, args.config, args.platform,
                            tuple(value for value in (args.user_id, args.user_id_alt) if value))
        print(json.dumps({"block": text, "owner": who == "owner", "member": who == "member",
                          "answering": session_context.answering(text, args.config)}, ensure_ascii=False))
    elif text:
        print(text)


def cmd_prefetch(args):
    found = session_context.prefetch(Vault(args.config.vault_path), args.config, args.message, owner=args.owner, seen=set(args.seen))
    if args.json:
        print(json.dumps(found, ensure_ascii=False))
    elif found["text"]:
        print(found["text"])


def cmd_export_sessions(args):
    written, unchanged = sessions.export(args.config, args.date)
    print(f"exported: {written} files written, {unchanged} unchanged")


def cmd_install_skills(args):
    print(install.skills(args.config))


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
    print(f"migrated to {install.migrate_done(args.config)}")


def cmd_install_scripts(args):
    for path in install.scripts():
        print(f"installed {path}")


def cmd_timezone(args):
    print(args.config.timezone)


def cmd_record_spec(args):
    print(json.dumps({"system": record.SYSTEM, "conversation": record.CONVERSATION, "schema": record.RECORD_SCHEMA,
                      "transcriptCap": record.TRANSCRIPT_CAP, "routine": record.ROUTINE,
                      "routineSchema": record.ROUTINE_SCHEMA, "nightStages": sorted(record.NIGHT_STAGES),
                      "held": record.held(args.config)}))


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
                print(json.dumps({name: asdict(routine) | {"prompt": f"Run {name}." if routine.prompt is None else routine.prompt}
                                  for name, routine in sorted(routines.items())}))
            else:
                for name, routine in sorted(routines.items()):
                    timing = next(f"{key} {getattr(routine, key)}" for key in schedule.TIMING if getattr(routine, key) is not None)
                    print(f"{name}: {timing}" + (" (paused)" if not routine.enabled else ""))
            return
        with vault_lock(config):
            if action == "prune":
                routines = schedule.load(config)
                cutoff = config.local() - timedelta(days=1)
                dead = [name for name, routine in routines.items()
                        if name in ("demiurge-whoami", "brain-migration")
                        or routine.at is not None and not name.startswith(install.MIGRATION_ROUTINE)
                        and datetime.fromisoformat(routine.at) < cutoff]
                if dead and not args.dry_run:
                    schedule.save(config, {name: routine for name, routine in routines.items() if name not in dead})
                for name in dead:
                    print(f"{'would remove' if args.dry_run else 'removed'} routine {name} in {schedule.path(config)}")
                return
            if action == "init":
                routines = schedule.load(config)
                defaults = schedule.defaults(config)
                if args.reset_night:
                    changes = []
                    for name in ("session-export", "attribution", "night-before", "sleep1", "people", "image",
                                 "night-after", "improve", "prime", "morning", "upgrade"):
                        if name not in defaults:
                            continue
                        routine = defaults[name]
                        if name in ("session-export", "morning", "improve") and name in routines and routines[name].cron:
                            routine = replace(routine, cron=routines[name].cron)
                        if routines.get(name) != routine:
                            changes.append(f"{'reset' if name in routines else 'added'} {name}")
                            routines[name] = routine
                    for name in ("sleep2", "rem", "brain-attribute", "continue", "continue-daily"):
                        if name in routines:
                            del routines[name]
                            changes.append(f"removed {name}")
                    changed = "; ".join(changes) or "night routines already match defaults"
                else:
                    added = [name for name in defaults if name not in routines]
                    routines |= {name: defaults[name] for name in added}
                    changed = (f"added {len(added)} {config.edition} routine(s): {', '.join(added)}" if added
                               else f"every {config.edition} routine is already scheduled")
            else:
                routines = schedule.load(config)
                name = args.name
                if action in ("set", "remove"):
                    folders = {entry["id"]: entry for entry in responsibilities.discover(Vault(config.vault_path)) if entry["folder"]}
                    owned = routines[name].responsibility if name in routines else None
                    requested = getattr(args, "responsibility", None)
                    owner = folders.get(owned) or folders.get(requested)
                    # A one-off run for a responsibility (a game window, a follow-up) is scheduled here, not in its
                    # document: it is spent once it runs, and prune removes it.
                    one_off = (routines[name].at is not None if name in routines else True) and (
                        action == "remove" or getattr(args, "at", None) is not None
                        or name in routines and not any(getattr(args, key, None) for key in schedule.TIMING))
                    if owner is not None and not one_off:
                        raise ValueError(f"edit {owner['hub']} and run `brain responsibility sync`")
                if action == "set":
                    changes = {key: getattr(args, key) for key in schedule.Routine.__dataclass_fields__
                               if getattr(args, key, None) is not None}
                    for key in ("skills", "needs"):
                        if key in changes:
                            changes[key] = [value.strip() for value in changes[key].split(",") if value.strip()]
                    for key in ("responsibility", "monitor", "script", "workdir"):
                        if key in changes and not changes[key].strip():
                            changes[key] = None   # an empty value removes the field
                    if unknown := [need for need in changes.get("needs", []) if need != "computer"]:
                        raise ValueError(f"unknown need {unknown[0]!r}; only computer is supported")
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
            schedule.save(config, routines)
        print(changed)
    except (OSError, ValueError) as error:
        sys.exit(f"schedule {action}: {error}")


def cmd_paths(args):
    if args.path in ("scripts", "records") and runtime.configured() is None:
        sys.exit("AGENT_HOME is not set")
    paths = {"soul": lambda: args.config.soul_path, "skills": lambda: args.config.skills_path,
             "scripts": install.scripts_path, "records": install.records_path,
             "vault": lambda: args.config.vault_path, "repo": lambda: args.config.repo,
             "agent": lambda: args.config.vault_path / args.config.agent_dir,
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
    for action in ("list", "set", "remove", "pause", "resume", "check", "init", "prune"):
        s = actions.add_parser(action, parents=[vault]); s.set_defaults(fn=cmd_schedule)
        if action in ("set", "remove", "pause", "resume"):
            s.add_argument("name")
        if action == "list":
            s.add_argument("--json", action="store_true")
        if action == "init":
            s.add_argument("--reset-night", action="store_true")
        if action == "prune":
            s.add_argument("--dry-run", action="store_true")
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
            owner = s.add_mutually_exclusive_group()
            owner.add_argument("--owner", action="store_true", default=None)
            owner.add_argument("--no-owner", dest="owner", action="store_false")
    s = sub.add_parser("pull", help="bring the vault's repository up to date under the repository lock", parents=[vault])
    s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("commit", help="commit the vault under the repository lock (scoped to PATHs when given), optionally pushing", parents=[vault])
    s.add_argument("-m", "--message", required=True); s.add_argument("--push", action="store_true")
    s.add_argument("paths", nargs="*", metavar="PATH"); s.set_defaults(fn=cmd_commit)
    s = sub.add_parser("nightly", parents=[vault]); s.add_argument("--date")
    s.add_argument("--write", action="store_true"); s.add_argument("--commit", action="store_true"); s.add_argument("--push", action="store_true")
    s.add_argument("--sessions-dir")
    s.add_argument("--stage", choices=STAGES, default="all",
                   help="before: what the night's model passes read; after: what is compiled from what they wrote")
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
    s.add_argument("--session", default="", help="the conversation the record is of; an ask it raises names it")
    s.add_argument("--at"); s.set_defaults(fn=cmd_journal_append)
    s = sub.add_parser("validate", parents=[vault]); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("access", parents=[vault]); s.add_argument("who")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_access)
    s = sub.add_parser("tellable", parents=[vault]); s.add_argument("who")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_tellable)
    s = sub.add_parser("attribute", parents=[vault])
    s.add_argument("--sessions-dir", help="default: $AGENT_HOME/records/sessions")
    s.add_argument("--write", action="store_true")
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
    mode.add_argument("--confirm", metavar="TEXT", help="record what you went ahead with, for the person to correct")
    mode.add_argument("--stale", action="store_true", help=f"asks unanswered for {needs.STALE_DAYS} days or more, for the evening to decide or retire")
    mode.add_argument("--overflow", action="store_true", help="asks past the five shown, lowest-ranked first, for the evening to decide tonight")
    mode.add_argument("--resolve", metavar="ID_OR_TEXT", help="remove an answered item from CURRENT's Needs you and record the evidence")
    mode.add_argument("--dismiss", metavar="ID_OR_TEXT", help="the owner said no: drop the item, act on nothing, never raise it again")
    s.add_argument("--because", metavar="EVIDENCE", help="with --resolve: what answered it, in a sentence")
    s.add_argument("--yours", metavar="TITLE", help="with --resolve: turn an answered ask into a task for the owner")
    s.add_argument("--delivers", metavar="TEXT", help="with --resolve --yours: what counts as done")
    s.add_argument("--reason", choices=("no", "done", "stop-asking"), default="no",
                   help="with --dismiss: what the owner chose (no, done: they did it, stop-asking: no questions like it)")
    s.add_argument("--source", metavar="NOTE", help="with --ask: the note it comes from, e.g. Orphan/Tasks#T-001")
    s.add_argument("--for", dest="for_", metavar="PERSON", help="list only what is for this person (<platform>:<id>): the asks of tasks they own")
    s.add_argument("--why", metavar="TEXT", help="with --ask or --resolve --yours: why it needs the owner, in plain words")
    s.add_argument("--hub", metavar="NOTE", help="with --ask: the matter in the owner's life it is about, e.g. Lisbon trip.md")
    s.set_defaults(fn=cmd_needs_you)
    s = sub.add_parser("loops", help="promises, answers owed, waits on others, and what to do when something happens",
                       parents=[vault])
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--close", metavar="ID_OR_TEXT")
    s.add_argument("--due", nargs="?", const=True, metavar="DATE", help="only due loops, or a due date with --add")
    s.add_argument("--quiet", action="store_true", help="print nothing; exit 1 when nothing is due, 0 otherwise")
    s.add_argument("--stale", action="store_true", help=f"only loops due {loops.STALE_DAYS} days or more ago, for the evening")
    s.add_argument("--kind", choices=loops.KINDS); s.add_argument("--source", metavar="NOTE")
    s.add_argument("--with", dest="with_", metavar="WHO"); s.add_argument("--outcome", choices=loops.OUTCOMES)
    s.add_argument("--task", metavar="T-nnn", help="with --add --kind waiting: the task the answer is for; closing the loop done resumes it")
    s.add_argument("--if", dest="condition", metavar="CONDITION",
                   help="with --add --kind when: what has to happen before you act (with or instead of --due DATE)")
    s.add_argument("--hub", metavar="NOTE", help="with --add: the matter in the owner's life it is about, e.g. Lisbon trip.md")
    s.add_argument("--because", metavar="TEXT"); s.set_defaults(fn=cmd_loops)
    s = sub.add_parser("item", help="the owner's answer to one open item (loop, idea, ask, confirm, consult)", parents=[vault])
    s.add_argument("id"); s.add_argument("--answer", required=True, metavar="VALUE",
                                         help="loop: drop; idea: yes|not-now|no|stop-asking; ask or confirm: no|done|stop-asking; "
                                              "consult: confirmed|no|stop-asking")
    s.add_argument("--words", metavar="TEXT", help="the owner's words (an idea's yes, a dropped loop's reason) or a consult's answer")
    s.add_argument("--by", metavar="PLATFORM:ID", help="who answered a consult")
    s.set_defaults(fn=cmd_item)
    s = sub.add_parser("owners", help="the area's ownership hints", parents=[vault])
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_owners)
    s = sub.add_parser("evidence", help="read the evidence sources wrote, newest first", parents=[vault])
    s.add_argument("action", nargs="?", choices=["import"],
                   help="import: rows as JSON lines on stdin, from a source run on another machine; repeats are skipped")
    s.add_argument("--source"); s.add_argument("--hint", help="a portrait facet or a knowledge area")
    s.add_argument("--since", metavar="YYYY-MM-DD"); s.add_argument("--json", action="store_true")
    s.add_argument("--about", metavar="TEXT", help="recall: the facts sharing the names in a choice, rarest and newest first")
    s.add_argument("--limit", type=int, default=20, help="with --about: how many facts (default 20)")
    s.set_defaults(fn=cmd_evidence)
    s = sub.add_parser("source", help="the evidence sources: list them, or extract one", parents=[vault])
    s.add_argument("target", metavar="list|ID"); s.add_argument("action", nargs="?", choices=["extract", "script"],
                   help="extract: read it into evidence here; script: print a script that reads it on the owner's own computer")
    s.add_argument("--since", metavar="YYYY-MM-DD", help="extract: only what is this recent")
    s.add_argument("--input", metavar="PATH", help="extract: where the raw input is, when not the source's default")
    s.add_argument("--home", metavar="DIR", help="extract: the agent workspace to keep evidence in (default AGENT_HOME)")
    s.add_argument("--jsonl", action="store_true", help="extract: print the rows as JSON lines instead of keeping them")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_source)
    s = sub.add_parser("consult", help="consult a colleague and keep their words by area (enterprise)", parents=[vault])
    actions = s.add_subparsers(dest="consult_cmd", required=True)
    for action in ("autonomy", "act", "reverse", "lower", "ceiling", "decisions", "cite", "decide", "candidates", "seed", "ask", "answer", "overrule", "delegate", "lapse", "list", "stats"):
        p = actions.add_parser(action); p.set_defaults(fn=cmd_consult)
        p.add_argument("--vault", default=argparse.SUPPRESS)
        if action == "autonomy":
            p.add_argument("--area", metavar="AREA"); p.add_argument("--json", action="store_true")
        elif action in ("lower", "ceiling"):
            p.add_argument("--area", required=True, metavar="AREA")
            p.add_argument("--rung", choices=consults.RUNGS, required=action == "ceiling")
            p.add_argument("--by", required=True, metavar="PERSON")
        elif action == "ask":
            for field in ("to", "why", "ask", "default"):
                p.add_argument("--" + field, required=True, metavar="PERSON" if field == "to" else "TEXT")
            p.add_argument("--area", metavar="AREA"); p.add_argument("--task", metavar="T-nnn")
            p.add_argument("--not-covered", metavar="TEXT", help="why matching decisions do not answer this question (at most 40 words)")
            p.add_argument("--restricted", action="store_true", help="the answer would authorize production, money or another restricted action")
        elif action in ("cite", "act"):
            if action == "act":
                p.add_argument("--area", required=True, metavar="AREA")
            else:
                p.add_argument("which", metavar="ID")
            p.add_argument("--task", metavar="T-nnn")
            p.add_argument("--for", dest="for_", required=True, metavar="TEXT", help="what you are going ahead with (at most 40 words)")
        elif action == "decide":
            p.add_argument("--area", required=True, metavar="AREA")
            p.add_argument("--by", required=True, metavar="PERSON", help="<platform>:<id> of the person speaking")
            p.add_argument("--words", required=True, metavar="TEXT", help="the person's exact words; never the intellect's inference")
            p.add_argument("--source", required=True, metavar="TEXT", help="where it was said: a vault note, link, or channel and date")
            p.add_argument("--supersedes", metavar="ID")
        elif action in ("answer", "overrule", "reverse"):
            p.add_argument("which", metavar="ID_OR_TEXT" if action == "answer" else "ID")
            if action == "answer":
                p.add_argument("--outcome", choices=consults.OUTCOMES, required=True)
                p.add_argument("--to", metavar="PERSON", help="required for a redirect")
            p.add_argument("--answer", required=True, metavar="TEXT",
                           help="the person's exact words; never the intellect's inference")
            p.add_argument("--by", required=True, metavar="PERSON", help="<platform>:<id> of the person speaking")
        elif action == "delegate":
            for field in ("area", "to", "by", "words"):
                p.add_argument("--" + field, required=True,
                               help="the delegator's exact words" if field == "words" else None)
        elif action == "lapse":
            p.add_argument("--dry-run", action="store_true")
        elif action == "seed":
            mode = p.add_mutually_exclusive_group(required=True)
            mode.add_argument("--reviews", metavar="DIR")
            mode.add_argument("--mail", action="store_true", help="list company mail with deference cues, without content")
            mode.add_argument("--claims", metavar="FILE", help="import derived statements of who decides what")
            p.add_argument("--account", metavar="ADDR", help="with --mail: one connected owner account (default all)")
            p.add_argument("--days", type=int, metavar="N", help=f"with --reviews or --mail: days back (default {routing.DAYS})")
            p.add_argument("--dry-run", action="store_true"); p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--json", action="store_true")
            if action == "list":
                p.add_argument("--for", dest="for_", metavar="PERSON")
            elif action in ("candidates", "decisions"):
                p.add_argument("--about", required=True, metavar="TEXT")
                p.add_argument("--area", metavar="AREA"); p.add_argument("--limit", type=int, metavar="N")
    s = sub.add_parser("ideas", help="proposals for new ways to help, shown one at a time", parents=[vault])
    s.add_argument("--all", action="store_true")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true"); mode.add_argument("--add", metavar="TEXT")
    mode.add_argument("--respond", metavar="ID_OR_TEXT")
    mode.add_argument("--withdraw", metavar="ID_OR_TEXT", help="take back your own idea; not the owner's answer")
    s.add_argument("--because", metavar="TEXT", help="with --withdraw: what changed")
    s.add_argument("--source", metavar="NOTE", help="with --add: the note it comes from; with --respond yes: the owner's own words")
    s.add_argument("--why", metavar="TEXT")
    s.add_argument("--needs", metavar="TEXT"); s.add_argument("--answer", choices=ideas.ANSWERS)
    s.add_argument("--goal", metavar="G-n")
    s.set_defaults(fn=cmd_ideas)
    s = sub.add_parser("roles", help="roles: templates and what each has learned (enterprise)", parents=[vault])
    s.set_defaults(fn=cmd_roles, roles_cmd=None)
    actions = s.add_subparsers(dest="roles_cmd")
    for action in ("list", "add", "rebuild", "show", "may-learn", "template", "screen", "study", "work", "correction",
                   "gate", "lenses", "actions", "predict", "asked", "account", "routines"):
        p = actions.add_parser(action); p.set_defaults(fn=cmd_roles)
        p.add_argument("--vault", default=argparse.SUPPRESS)
        if action == "add":
            p.add_argument("name"); p.add_argument("--template", required=True, choices=roles.shipped())
        elif action == "rebuild":
            p.add_argument("name", nargs="?")
        elif action == "show":
            p.add_argument("name"); p.add_argument("--json", action="store_true")
        elif action == "may-learn":
            p.add_argument("name"); p.add_argument("--claim", required=True)
            p.add_argument("--evidence", required=True, metavar="YYYY-MM-DD", help="the date of the newest evidence for it")
        elif action == "template":
            p.add_argument("op", choices=["check", "accept", "decline"]); p.add_argument("name", nargs="?")
        elif action == "screen":
            p.add_argument("name"); p.add_argument("op", choices=["next", "record", "calibrate"])
            p.add_argument("--since", metavar="YYYY-MM-DD", help="next: how far back to read (default 30 days)")
            p.add_argument("--limit", type=int, default=20, help="next: exchanges per batch")
            p.add_argument("--sample", help="calibrate: labelled items as JSON lines")
        elif action == "work":
            p.add_argument("name"); p.add_argument("op", choices=["add", "list"]); p.add_argument("id", nargs="?")
            p.add_argument("--title"); p.add_argument("--link")
        elif action == "correction":
            p.add_argument("name"); p.add_argument("op", choices=["add", "next", "file", "dismiss"])
            p.add_argument("event_id", nargs="?", help="file, dismiss: the correction's id from next")
            for flag in ("work", "event", "by", "at", "text", "where", "link", "note", "section", "claim", "to", "why",
                         "decides", "person"):
                p.add_argument(f"--{flag}")
            p.add_argument("--always", action="store_true", help="file --decides: always ask them (a restricted area)")
            p.add_argument("--miss", help="file: the kind of miss, when the default doesn't fit (retrieval, new taste, ...)")
            p.add_argument("--source-id", help="add: the item's id on its source, so a repeat is kept once")
            p.add_argument("--rule", action="store_true", help="file: someone with authority stated it as a rule")
            p.add_argument("--json", action="store_true")
        elif action == "gate":
            p.add_argument("name"); p.add_argument("--work", help="the work the plan is for: its decisions are kept with it")
        elif action == "lenses":
            p.add_argument("name")
        elif action == "actions":
            p.add_argument("name"); p.add_argument("--json", action="store_true")
        elif action == "predict":
            p.add_argument("name"); p.add_argument("--work", required=True)
            p.add_argument("--expect", action="append", help="one thing you expect reviewers to raise; repeatable")
        elif action == "asked":
            p.add_argument("name"); p.add_argument("--work", required=True)
            p.add_argument("--verdict", required=True, choices=["needed", "findable", "unneeded"],
                           help="needed; findable: 'you could have found this'; unneeded: 'your call'")
            p.add_argument("--about")
        elif action == "account":
            p.add_argument("name"); p.add_argument("--days", type=int, default=7); p.add_argument("--json", action="store_true")
        elif action == "study":
            p.add_argument("name"); p.add_argument("op", choices=["next", "done"])
            p.add_argument("--limit", type=int, default=40, help="next: exchanges to show")
    s = sub.add_parser("goals", help="the person's goals; only this command writes goal blocks", parents=[vault])
    s.add_argument("which", help="new, list, show, activity, due, refused, or G-n")
    s.add_argument("action", nargs="?", help="title with new; id with show; operation with G-n")
    s.add_argument("text", nargs="?", help="dated evidence, a note with link, or the active goal with superseded-by")
    s.add_argument("--words"); s.add_argument("--title"); s.add_argument("--why")
    s.add_argument("--inferred", action="store_true")
    s.add_argument("--evidence", action="append", help="dated evidence; repeatable, required with --inferred or intellect reached/superseded-by")
    s.add_argument("--said", help="YYYY-MM-DD: when the person said their words, if not today")
    s.add_argument("--reach", action="store_true"); s.add_argument("--kind", choices=goals.KINDS)
    by = s.add_mutually_exclusive_group()
    by.add_argument("--by"); by.add_argument("--clear-by", action="store_true")
    s.add_argument("--from-app", action="store_true"); s.add_argument("--json", action="store_true")
    s.add_argument("--weeks", type=int, help="activity window in weeks (default 2)")
    s.add_argument("--week", help="account's ISO week, YYYY-Www (before noon: yesterday's week; otherwise today's)")
    s.set_defaults(fn=cmd_goals)
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
    p = actions.add_parser("friends", help="rank close-friends candidates from recorded contact, read only", parents=[vault])
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_people)
    s = sub.add_parser("task", help="create a task or change one: done, drop, yours, reopen, set",
                       description="brain task TID {done,drop,yours,reopen,set} [fields]; brain task new TITLE --source SOURCE", parents=[vault])
    s.add_argument("tid", metavar="TID|new", help="task id, or new to create a task")
    s.add_argument("action", metavar="ACTION|TITLE", help="done, drop, yours, reopen, set, view (JSON on stdin); with new, the task title")
    s.add_argument("--state", metavar="S", help="open task state; new accepts ready (default), in_progress or yours")
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
    s.add_argument("--left", metavar="TEXT", help="with done: what is not done, or only attempted")
    s.add_argument("--then", metavar="STAGE", action="append",
                   help=f"with done: a next stage the owner could start, as its title; up to {tasks_mod.MAX_THEN}")
    s.add_argument("--report", metavar="WHERE", help="with new --state yours or set: origin conversation or where to report (demiurge:<id>, or Home)")
    s.add_argument("--from", dest="from_", metavar="TID", help="with new: the existing task this work came from; defaults to the worker's task")
    s.add_argument("--reviewed", metavar="TEXT", help="with set: append a dated Reviewed line (the evening's decision and why)")
    s.add_argument("--source", help="with new: the owner's words or a note name")
    s.add_argument("--said", help="with --delivers: at least four exact words the owner said, checked against Source or recent conversations")
    s.add_argument("--work", metavar="SLUG", help="with new or set: Library work slug")
    s.add_argument("--json", action="store_true", help="with new: print {id, title}")
    s.add_argument("--handoff", action="store_true",
                   help="with new: hand it off in the same call, as `brain handoff` does; --report and --work go to the handoff")
    s.add_argument("--later", action="store_true",
                   help="with new --state yours or set: owner work for some day, kept out of what needs them today")
    s.add_argument("--now", action="store_true", help="with set: bring later owner work back to what needs them")
    s.add_argument("--by-owner", action="store_true",
                   help="with done or drop: the owner's own close, including in conversation; allows closing while waiting on them")
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
    p = actions.add_parser("goal", help="link a local work to an active goal", parents=[vault])
    p.add_argument("slug"); p.add_argument("--goal", required=True); p.set_defaults(fn=cmd_works)
    s = sub.add_parser("continue", help="handed-off tasks due now, least recently worked first; text exits 1 when none", parents=[vault])
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--due", action="store_true")
    s.add_argument("tid", nargs="?", metavar="TASK", help="show one task and its next-chunk instructions")
    mode.add_argument("--chunk", metavar="TASK")
    mode.add_argument("--pace", nargs=2, metavar=("TASK", "regular|daily"))
    mode.add_argument("--checkpoint", metavar="TASK", help="save a per-task continue checkpoint")
    mode.add_argument("--engaged", metavar="TASK", help="record that the owner engaged with the task now (its daily budget is whole again)")
    mode.add_argument("--extend", metavar="TASK", help="the owner asked the task to keep going today: another whole day's chunks until midnight")
    s.add_argument("--by", metavar="PERSON", help="with --engaged or --extend: who engaged, as <platform>:<id> (default: the speaker); only the task's owner counts")
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
    s.set_defaults(fn=cmd_continue)
    s = sub.add_parser("watch-signal", help="stable follow-through monitor output, without a model", parents=[vault])
    s.set_defaults(fn=cmd_watch_signal)
    s = sub.add_parser("explore-signal", help="explore's change gate: the first tick each day, then additions, without a model", parents=[vault])
    s.set_defaults(fn=cmd_explore_signal)
    s = sub.add_parser("stale-tasks", help="open tasks gone quiet long enough for the evening review", parents=[vault])
    s.set_defaults(fn=cmd_stale_tasks)
    s = sub.add_parser("reception", help="how each thing offered landed, and correction burden on the intellect's work",
                       parents=[vault])
    s.add_argument("action", nargs="?", choices=["summary", "add", "set", "burden"], default="summary")
    s.add_argument("id", nargs="?", help="set: a row id, or the note a held draft lives in")
    s.add_argument("outcome", nargs="?")
    s.add_argument("--kind", choices=reception.KINDS); s.add_argument("--what"); s.add_argument("--source")
    s.add_argument("--portrait", metavar="ID", help="the portrait line this offer served")
    s.add_argument("--at", metavar="'YYYY-MM-DD HH:MM'", help="add: when it was offered, for a row written after the fact")
    s.add_argument("--days", type=int, default=30); s.add_argument("--weeks", type=int, default=8)
    s.add_argument("--reviews", metavar="DIR"); s.add_argument("--author", metavar="LOGIN")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_reception)
    s = sub.add_parser("portrait", help="the agent's ranked understanding of the owner, with line ids", parents=[vault])
    s.add_argument("action", nargs="?", choices=["seed", "coverage", "stamp", "mark", "compile", "facets", "move", "due", "evidence", "judged", "open"],
                   help="seed: what to read to seed the portrait; coverage: what it knows and from where; "
                        "stamp: give new lines their ids now; mark ID right|wrong|not-this: the owner's answer to a line; "
                        "compile: write the portrait from its facets; facets: list them; move ID FACET: a line to a facet; "
                        "due: facets with evidence newer than their last pass; evidence FACET: the rows its pass reads; "
                        "judged FACET: record a facet's pass; open FACET: make a new facet's note")
    s.add_argument("id", nargs="?"); s.add_argument("verdict", nargs="?", metavar="VERDICT|FACET")
    s.add_argument("--words", metavar="TEXT", help="mark: the owner's own words, kept when a line is wrong")
    s.add_argument("--through", metavar="YYYY-MM-DD", help="judged: the newest evidence the pass read (default today)")
    s.add_argument("--since", metavar="YYYY-MM-DD", help="evidence: rows after this date instead of after the facet's last pass")
    s.add_argument("--all", action="store_true", help="evidence: every row routed to the facet")
    s.add_argument("--empty", metavar="WHY", help="judged: why nothing in a facet's new rows makes a line")
    s.add_argument("--reviews", metavar="DIR", help="seed: one file per merged PR with its review comments, for Taste")
    s.add_argument("--plans", metavar="DIR", help="seed: one file per project with the issues done on it, for After and Energy")
    s.add_argument("--account", metavar="ADDRESS|all", help="seed: the owner's synced mail and calendar")
    s.add_argument("--chats", metavar="FILE", help="seed: a chat export the owner handed over, channels/chats/<chatgpt|claude>.jsonl")
    s.add_argument("--clear", action="store_true", help="seed: with --chats, remove the export once its Seeds note is written")
    s.add_argument("--clear-backfill", action="store_true", help="seed: remove the account's backfill mail and calendar files")
    s.add_argument("--hand-off", action="store_true",
                   help="seed: hand off one task that seeds this newly connected account or chat export, unless one is")
    s.add_argument("--days", type=int, help=f"seed: days back through today (default all synced for accounts, {routing.DAYS} for reviews, {plan_source.PLAN_DAYS} for plans)")
    s.add_argument("--top", type=int, metavar="N", help="only the top N After lines, with what each asks attention to watch")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_portrait)
    s = sub.add_parser("state", parents=[vault])
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--stamp", action="store_true", help="a fingerprint of what the state is built from, without building it")
    s.set_defaults(fn=cmd_state)
    s = sub.add_parser("health", parents=[vault]); s.add_argument("--date")
    s.add_argument("--json", action="store_true"); s.add_argument("--write", action="store_true", help="write <runtime_dir>/health.md")
    s.set_defaults(fn=cmd_health)
    s = sub.add_parser("prefetch", help="what goes before an owner's message: the people it names and the notes that match, by [context]",
                       parents=[vault])
    s.add_argument("message"); s.add_argument("--owner", action="store_true", help="the owner's own session: name the people it mentions")
    s.add_argument("--seen", action="append", default=[], metavar="NAME", help="a person already shown this session")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_prefetch)
    s = sub.add_parser("task-answer", parents=[vault],
                       help="record another intellect's answer on a task waiting on it, from the conversation with it; resumes the task")
    s.add_argument("tid", metavar="T-nnn"); s.add_argument("--answer", required=True)
    s.add_argument("--next", default="", help="what the answer lets the task do now")
    for option in ("platform", "user-id", "user-id-alt"):
        s.add_argument("--" + option, default="")
    s.set_defaults(fn=cmd_task_answer)
    s = sub.add_parser("session-context", parents=[vault])
    for option in ("platform", "user-id", "user-id-alt", "user-name", "chat-id"):
        s.add_argument("--" + option, default="")
    s.add_argument("--ack-notices", action="store_true", help="acknowledge displayed app-change notices for this owner or routine session")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_session_context)

    s = sub.add_parser("responsibility", help="add, adopt or sync standing work, or build a legacy hub with --skill", parents=[vault])
    s.add_argument("name", metavar="add|adopt|sync|NAME"); s.add_argument("title", nargs="?")
    s.add_argument("routines", nargs="*")
    s.add_argument("--title", dest="adopt_title"); s.add_argument("--hub"); s.add_argument("--into")
    for key in ("skill", "records", "when", "script", "prompt", "asked-by", "delivers", "body"):
        s.add_argument("--" + key)
    reason = s.add_mutually_exclusive_group()
    reason.add_argument("--goal"); reason.add_argument("--role")
    s.set_defaults(fn=cmd_responsibility)
    s = sub.add_parser("timezone", help="print the owner's timezone from brain.toml", parents=[vault]); s.set_defaults(fn=cmd_timezone)
    s = sub.add_parser("install-skills", parents=[vault])
    s.set_defaults(fn=cmd_install_skills)
    s = sub.add_parser("update", parents=[vault]); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("migrate-check", parents=[vault]); s.set_defaults(fn=cmd_migrate_check)
    s = sub.add_parser("redact", parents=[vault]); s.add_argument("paths", nargs="+"); s.set_defaults(fn=cmd_redact)
    s = sub.add_parser("migrate-done", parents=[vault]); s.set_defaults(fn=cmd_migrate_done)
    s = sub.add_parser("install-scripts", parents=[vault]); s.set_defaults(fn=cmd_install_scripts)
    s = sub.add_parser("paths", parents=[vault])
    s.add_argument("path", choices=("soul", "skills", "scripts", "records", "vault", "repo", "agent", "python", "schedule")); s.set_defaults(fn=cmd_paths)
    a = ap.parse_args(argv)
    # Inside double quotes the shell expands "$3,450" to ",450" before brain sees it; refuse rather than save a lost amount.
    for value in vars(a).values():
        for text in value if isinstance(value, list) else [value]:
            if isinstance(text, str) and (lost := SHELL_EXPANDED.search(text)):
                ap.error(f"{lost[0].strip()!r} looks like an amount whose $ the shell expanded away; "
                         "pass the text in single quotes, or write \\$, so the $ survives")
    if a.cmd == "channels" and a.action == "mail" and not a.since:
        ap.error("channels mail requires --since")
    try:
        a.config = Config.load(a.vault)
    except (OSError, ValueError) as error:
        if a.cmd != "source":   # a source can run on the owner's own Mac, where there is no vault
            ap.error(str(error))
        a.config = None
    a.fn(a)


if __name__ == "__main__":
    main()
