"""Read-only, deterministic work snapshots for external consumers."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from . import fm, goals, health, ideas, learned, reception, ledger, loops, image, matters, needs, portrait, responsibilities, runtime, schedule, seed, tasks, views, works
from .config import Config
from .vault import Vault


def _public(vault: Vault, source: str) -> bool:
    path = PurePosixPath(source.split("#", 1)[0])
    config = vault.config
    excluded = (config.sources_dir, PurePosixPath(config.human_profile).parent.as_posix(),
                config.people_dir, config.agents_dir, config.runtime_dir)
    if path.is_absolute() or ".." in path.parts or any(path.is_relative_to(folder) for folder in excluded):
        return False
    if path.as_posix() in vault.malformed or path.as_posix() + ".md" in vault.malformed:
        return False   # its tier is unreadable, so it may be private
    note = vault.notes.get(path.as_posix()) or vault.notes.get(path.as_posix() + ".md")
    return note is None or (note.data.get("tier") != "private" and note.data.get("kind") != "conversation")


def _source(vault: Vault, source: str) -> str | None:
    target, separator, anchor = source.partition("#")
    target = target.removeprefix(vault.config.vault + "/")
    relative = vault.resolve(target) or PurePosixPath(target).as_posix()
    return relative + separator + anchor if _public(vault, relative) else None


def _tasks(vault: Vault) -> list[dict]:
    config = vault.config
    register = config.agent("Tasks.md")
    registered = tasks.load_tasks(config)[0] if (config.vault_path / register).exists() else []
    entries = [(task, False, register + "#" + task.tid) for task in registered]
    seen = {task.tid for task in registered}
    archived = []
    for path in sorted((config.vault_path / config.agent("Tasks Archive")).glob("T-*.md")):
        task = tasks.read_archived_task(config, path)
        if task is not None and task.tid not in seen:
            seen.add(task.tid)
            archived.append((task, True, path.relative_to(config.vault_path).as_posix() + "#" + task.tid))
    entries.extend(sorted(archived, key=lambda entry: (len(entry[0].tid), entry[0].tid)))
    from . import continuation
    worked, chunks = continuation.last_worked(config), continuation.chunks_today(config)
    limits = continuation.budgets(config, [task for task, *_ in entries if task.handoff and tasks.is_open(task)])
    # A regular task that spent today's budget rests until the owner's midnight, like one resting to a set time.
    midnight = f"{config.local().date() + timedelta(days=1)} 00:00"
    spent = {tid for tid, limit in limits.items() if chunks.get(tid, 0) >= limit}
    current = vault.notes.get("CURRENT.md")
    questions = {item.task: item for item in needs.open_asks(current.body) if item.task} if current else {}
    return [{"id": task.tid, "title": task.title, "state": task.task_state, "open": tasks.is_open(task),
             "detail": fm.field(task.body, "Status"), "next": fm.field(task.body, "Next", "Next action"),
             "blockers": fm.field(task.body, "Blocker", "Blockers"), "hub": _public_link(vault, task.hub), "work": task.work,
             "goal": task.goal if config.edition == "personal" else None,
             "after": task.after,
             "from": task.from_task,
             "delivers": task.delivers, "deliversProposed": tasks.proposed_delivers(task),
             "ask": questions[task.tid].text if task.tid in questions else None,
             "why": (questions[task.tid].detail or None) if task.tid in questions else None,
             "evidence": fm.field(task.body, "Latest evidence"),
             "resume": midnight if task.tid in spent and tasks.pace(task) == "regular" and (task.resume or "") < midnight
                       else task.resume,
             "handoff": task.handoff[0] if task.handoff else None,
             "report": task.report, "pace": tasks.pace(task), "later": task.later,
             "lastChunk": worked.get(task.tid), "chunksToday": chunks.get(task.tid, 0) if task.handoff else None,
             "closed": tasks.closing(task)[0], "outcome": tasks.closing(task)[1],
             "left": fm.field(task.body, "Left"), "due": fm.field(task.body, "Due"),
             "then": [m[1].strip() for m in re.finditer(r"^[ \t]*-[ \t]+\*\*Then:\*\*[ \t]*([^\r\n]*)", task.body, re.M)],
             "archived": archived, "source": source, "view": views.read(config, task.tid),
             "said": _said(fm.field(task.body, "Source")), "owner": task.owner}
            for task, archived, source in entries if _public(vault, source)]


def _said(source: str | None) -> str | None:
    """The owner's own words a task began from, when its Source quotes them: `"Rohan in Home, Nov 5: Yes add ledger"`."""
    match = re.search(r'"([^"]{3,400})"', source or "")
    return match[1].strip() if match else None


def _public_link(vault: Vault, title: str | None) -> str | None:
    """A link names its target only when the target may be exported; an unresolved name is kept as written."""
    if not title:
        return None
    target = vault.resolve(title)
    return title if target is None or _public(vault, target) else None


def _improvements(body: str, source: str) -> list[dict]:
    span = fm.section(body, "Improvements")
    if span is None:
        return []
    section = body[span[0]:span[1]]
    headings = list(re.finditer(r"^### ([^\r\n]+)", section, re.M))
    out = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        status = needs.improvement_status(section[heading.end():end])
        if status is None:
            continue
        anchor = heading[1].rstrip()
        dated = needs.DATED_HEADING_RE.fullmatch(anchor)
        out.append({"title": dated[2] if dated else anchor, "status": status,
                    "date": dated[1] if dated else None, "source": source + "#" + anchor})
    return out


def _hubs(vault: Vault, task_entries: list[dict], responsibility_entries: list[dict], waiting: list[dict],
          today: date) -> list[dict]:
    out = []
    hubbed = {task["id"] for task in task_entries if task["hub"]}
    folders = {entry["hub"]: entry for entry in responsibility_entries if entry["folder"]}
    for source, note in sorted(vault.notes.items()):
        folder = folders.get(source)
        if not _public(vault, source) or (folder is None and
                (vault.kind_for(source) != "hub" or note.data.get("kind", "hub") != "hub")):
            continue
        skill, records = note.data.get("skill"), note.data.get("records")
        responsibility = {"skill": skill, "records": records} if skill and records else None
        title = note.stem
        if folder is not None:
            title = folder["title"]
            responsibility = {key: folder[key] for key in ("id", "skill", "records", "routines")}
        span = fm.section(note.body, "Open questions")
        questions = [match[1].strip() for line in note.body[span[0]:span[1]].splitlines()
                     if (match := re.match(r"^[ \t]*[-*+] (.+)", line))] if span else []
        targets = {note.stem.casefold(), source.removesuffix(".md").casefold()}
        linked = [task for task in task_entries if task["hub"] and task["hub"].casefold() in targets]
        progress = {"done": sum(task["state"] == "completed" for task in linked),
                    "total": sum(task["state"] != "cancelled" for task in linked)} if linked else None
        entry = {"title": title, "source": source, "responsibility": responsibility, "work": note.data.get("work"),
                 "delivers": note.data.get("delivers"), "openQuestions": questions,
                 "improvements": _improvements(note.body, source), "progress": progress}
        if responsibility is None:
            hub_tasks = {task["id"] for task in linked}
            kept = matters.state(note.body, today)
            entry["waiting"] = [item["id"] for item in waiting if _belongs(item, source, hub_tasks, hubbed)]
            # A set-aside task, or the owner's work put off for later, is decided for now: it keeps nothing current.
            live = any(task["open"] and task["state"] != "set_aside" and not task["later"] for task in linked)
            entry.update(kept, phase=matters.phase(kept, today, bool(entry["waiting"]) or live))
        out.append(entry)
    return out


def _belongs(item: dict, source: str, hub_tasks: set[str], hubbed: set[str]) -> bool:
    """A loop or ask is a matter's by its own hub line, else its task's hub, else its source when that is the hub:
    a source is only what the conversation touched first, so it decides nothing when it is another note."""
    if item["hub"]:
        return item["hub"] == source
    if item.get("task") in hubbed:
        return item["task"] in hub_tasks
    return item["source"].split("#", 1)[0] == source


def _at(when: str) -> str | None:
    if not (ledger.DATED.fullmatch(when) or ledger.TIMED.fullmatch(when)):
        return None
    at = when[:16].replace(" ", "T")
    try:
        datetime.fromisoformat(at)
    except ValueError:
        return None
    return at


def _rows(vault: Vault) -> list[tuple[str, str, int, str, ledger.Row]]:
    out = []
    for path in ledger.discover(vault.config):
        source = path.relative_to(vault.root).as_posix()
        if not _public(vault, source):
            continue
        for position, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            row = ledger.parse_row(line)
            at = _at(row.when) if row else None
            if at is not None:
                out.append((at, source, position, line, row))
    out.sort(key=lambda entry: (entry[1], entry[2]))
    out.sort(key=lambda entry: entry[0], reverse=True)
    return out


def _matches_routine(config: Config, hub: dict, name: str) -> bool:
    responsibility = hub["responsibility"]
    if "id" in responsibility:
        return name.casefold() in {routine.casefold() for routine in responsibility["routines"]}
    return name.casefold() in (responsibility["records"].casefold(),
                               (responsibility["skill"] or "").removeprefix(config.skill_prefix).casefold())


def _routines(config: Config, hubs: list[dict], rows: list[tuple[str, str, int, str, ledger.Row]]) -> list[dict]:
    responsibilities = [hub for hub in hubs if hub["responsibility"]]

    out = {}
    for _, source, _, _, row in rows:
        name = row.routine
        if name.casefold() in out:
            continue
        hub = next((hub for hub in responsibilities if _matches_routine(config, hub, name)), None)
        fallback = config.skill_prefix + name
        skill = hub["responsibility"]["skill"] if hub else (
            fallback if (config.skills_path / fallback).is_dir() else None)
        if row.outcome is None and skill is None and hub is None:
            continue   # a legacy row's second column is not always a routine (a person, a subject); only five-column rows promise one
        out[name.casefold()] = {"name": name, "skill": skill, "hub": hub["title"] if hub else None,
                                "ledger": PurePosixPath(source).parent.as_posix()}
    for hub in responsibilities:
        responsibility = hub["responsibility"]
        if "id" not in responsibility and any(_matches_routine(config, hub, routine["name"]) for routine in out.values()):
            continue
        for name in responsibility.get("routines", [responsibility["records"]]):
            if name.casefold() not in out:
                out[name.casefold()] = {"name": name, "skill": responsibility["skill"], "hub": hub["title"], "ledger": None}
    return sorted(out.values(), key=lambda routine: (routine["name"].casefold(), routine["name"]))


def _outputs(vault: Vault) -> list[dict]:
    out = []
    for source, note in sorted(vault.notes.items()):
        kind = note.data.get("kind")
        if kind not in ("draft", "reflection") or not _public(vault, source):
            continue
        leading = re.match(r"^\d{4}-\d{2}-\d{2}", note.stem)
        created = note.data.get("created") if "created" in note.data else leading[0] if leading else None
        created = created.isoformat() if isinstance(created, date) else str(created) if created is not None else None
        out.append({"id": hashlib.sha256(source.encode("utf-8")).hexdigest()[:12],
                    "title": note.stem, "kind": kind, "created": created, "source": source,
                    "task": note.data.get("task")})
    out.sort(key=lambda output: output["created"] or "", reverse=True)
    return out


def _prepared(vault: Vault, matters: list[str]) -> list[dict]:
    """What explore made unasked and holds in Prepared, not yet used or set aside, with how its offer stands. A draft
    made for a task is that task's output, so it shows there instead. Its hub is the matter its `hub:` names, else the
    matter that links to it, as a draft is filed with a wikilink from the note it serves."""
    folder = vault.config.agent("Prepared/")
    served = {}
    for hub in matters:
        for target in vault.links_in(vault.notes[hub].body):
            served.setdefault(target, hub)
    latest = {}
    for _, row in reception.rows(vault.config):
        key = (_source(vault, row.source) or "").split("#", 1)[0].removesuffix(".md").casefold()
        if row.kind == "draft" and key and (key not in latest or (row.at, row.id) >= (latest[key].at, latest[key].id)):
            latest[key] = row
    out = []
    for source, note in sorted(vault.notes.items()):
        if not source.startswith(folder) or note.data.get("task") or not _public(vault, source):
            continue
        row = latest.get(source.removesuffix(".md").casefold())
        if row is None or row.outcome not in ("pending", "delivered", "opened"):
            continue
        leading = re.match(r"\d{4}-\d{2}-\d{2}", note.stem)
        created = note.data.get("created") or (leading[0] if leading else row.at[:10])
        out.append({"id": row.id, "title": str(note.data.get("index_line") or re.sub(r"^\d{4}-\d{2}-\d{2} ", "", note.stem)),
                    "source": source, "created": created.isoformat() if isinstance(created, date) else str(created),
                    "goal": note.data.get("goal") or fm.field(note.body, "Goal"), "for": str(note.data["for"]) if note.data.get("for") else None,
                    "outcome": row.outcome, "hub": _matter(_hub(vault, str(note.data.get("hub") or "")), matters) or served.get(source)})
    out.sort(key=lambda draft: draft["created"], reverse=True)
    return out


def _goals(vault: Vault, task_entries: list[dict]) -> list[dict]:
    if vault.config.edition != "personal":
        return []
    loaded = goals.load(vault.config)
    entries = [goal.public() for goal in loaded]
    activity = goals.activity(vault.config, vault=vault)
    for entry, goal in zip(entries, loaded):
        entry["account"] = goals.account(goal, vault.config.today())
        entry["activity"] = [{**item, "detail": item["detail"][:12]} for item in activity.get(goal.gid, [])[:20]]
    by_id = {entry["id"]: entry["links"] for entry in entries}
    def link(gid, kind, ident):
        if gid in by_id and ident not in by_id[gid][kind]:
            by_id[gid][kind].append(ident)
    for task in task_entries:
        link(task["goal"], "tasks", task["id"])
        if task["work"]:
            link(task["goal"], "works", task["work"])
    for source, note in sorted(vault.notes.items()):
        if not _public(vault, source):
            continue
        if note.data.get("kind") == "draft" or vault.kind_for(source) == "draft":
            gid = note.data.get("goal") or fm.field(note.body, "Goal")
            link(gid, "drafts", source.removesuffix(".md"))
        elif vault.kind_for(source) == "hub":
            gid = fm.hub_goal(note.body)
            link(gid, "hubs", note.stem)
            if note.data.get("work"):
                link(gid, "works", note.data["work"])
    for idea in ideas._open(vault, vault.config.today()):
        if _source(vault, idea.source) is not None:
            link(idea.goal, "ideas", idea.id)
    for work in works.parse(works.library() or ""):
        link(works.goal_for(work.slug) or work.goal, "works", work.slug)
    return entries


def _loops(vault: Vault, items: list[loops.Item]) -> list[dict]:
    return [{**loops.as_dict(item), "source": source, "hub": _hub(vault, item.hub)} for item in items
            if (source := _source(vault, item.source)) is not None]


def _hub(vault: Vault, link: str) -> str:
    """An item's or a draft's hub line as the hub's exported path; a private or empty one is dropped, so the item falls back."""
    link = link.strip().removeprefix("[[").removesuffix("]]").split("|", 1)[0].strip()
    return (_source(vault, link) or "") if link else ""


def _matter(source: str, matters: list[str]) -> str | None:
    """The matter an idea came from, when it came from one: its source, a section of it included."""
    note = source.split("#", 1)[0]
    return note if note in matters else None


def _engine() -> str:
    try:
        return health.installed_version("agent-brain")
    except health.PackageNotFoundError:
        return "unknown"


def build(config: Config, today: date) -> dict:
    vault = Vault(config.vault_path)
    if vault.config != config:
        vault.config = config
        vault.load()
    task_entries = _tasks(vault)
    responsibility_entries = responsibilities.discover(vault)
    routine_owners = responsibilities.link(responsibility_entries, schedule.load(config))
    owner_items = []
    for item in needs.items(vault, today):
        source = item.source if item.origin == "access" else _source(vault, item.source)
        if source is None and item.origin == "current":
            source = "CURRENT.md"
        if source is not None:
            owner_items.append({**asdict(item), "source": source, "hub": _hub(vault, item.hub)})
    open_loops = _loops(vault, loops.open_loops(config))
    hubs = _hubs(vault, task_entries, responsibility_entries,
                 open_loops + [item for item in owner_items if item["kind"] == "ask"], today)
    public_ids = {entry["id"] for entry in responsibility_entries if _public(vault, entry["hub"])}
    responsibility_entries = [entry for entry in responsibility_entries if _public(vault, entry["hub"])]
    rows = _rows(vault)
    routine_entries = _routines(config, hubs, rows)
    for routine in routine_entries:
        owner = routine_owners.get(routine["name"].casefold())
        routine["responsibility"] = owner if owner in public_ids else None
    floor = (today - timedelta(days=29)).isoformat()
    recent = [{"id": hashlib.sha256((source + "\n" + line).encode("utf-8")).hexdigest()[:12],
               "at": at, "routine": row.routine, "outcome": row.outcome, "account": row.account,
               "next": row.next, "source": source}
              for at, source, _, line, row in rows if floor <= at[:10] <= today.isoformat()]
    # A matter is a hub that is not a responsibility; what was made or thought of for one shows inside it.
    matters = [hub["source"] for hub in hubs if hub["responsibility"] is None]
    idea = ideas.shown(config, today)
    source = _source(vault, idea.source) if idea else None
    owner_idea = {**asdict(idea), "source": source, "hub": _matter(source, matters)} if source is not None else None
    if owner_idea and config.edition == "enterprise":
        owner_idea["goal"] = None
    # Every open idea, for the owner to read when they choose; Today still offers only the shown one, at its pace.
    open_ideas = [{**asdict(item), "source": source, "hub": _matter(source, matters), **({"goal": None} if config.edition == "enterprise" else {})}
                  for item in ideas.items(config, today) if (source := _source(vault, item.source)) is not None]
    version = health.git(config.vault_path, "rev-parse", "--short", "HEAD")
    if version and health.git(config.vault_path, "--no-optional-locks", "status", "--porcelain"):
        version += "+dirty"
    return {"schema": 3, "engine": _engine(), "version": version, "tasks": task_entries, "hubs": hubs,
            "goals": _goals(vault, task_entries),
            "routines": routine_entries, "ledger": recent,
            "needsYou": owner_items, "outputs": _outputs(vault),
            "prepared": _prepared(vault, matters),
            "loops": open_loops,
            "loopsClosed": _loops(vault, loops.closed_recent(config))[:10],
            "idea": owner_idea,
            "ideas": open_ideas,
            "responsibilities": responsibility_entries,
            # an organisation's stage is the whole team's, so it shows what was stated and seen
            "portrait": [line.public() for line in portrait.shown(portrait.load(config))]
            if config.edition == "enterprise" else None,
            # a person's portrait, guesses included, for their eyes only: the backend never serves it to anyone else
            "ownerPortrait": image.owner_lines(config) if config.edition == "personal" else None,
            # how to do each kind of work, for the owner to read, strike or confirm; the week says whether it reached the work
            "learned": {"fields": learned.public(config), "week": learned.week(config, today.isoformat())}
            if config.edition == "personal" else None,
            # the owner's accounts a seed has read or is reading, so their app can say the first portrait is coming
            "seeds": seed.seeds(vault) if config.edition == "personal" else None}


def stamp(config: Config) -> str:
    """A cheap fingerprint of what `build` reads, from file metadata alone: every file in the vault by path (so a rename
    counts), the runtime records it reads, the engine version, the vault's HEAD and the owner's day."""
    digest = hashlib.sha256()

    def add(path: str, info: os.stat_result | None) -> None:
        digest.update(f"{path}\0{info.st_mtime_ns if info else ''}\0{info.st_size if info else ''}\n".encode())

    for folder, dirs, files in os.walk(config.vault_path):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if not name.startswith("."):
                path = os.path.join(folder, name)
                add(path, os.lstat(path))
    paths = [goals._state_path(config)]
    if (home := runtime.configured()) is not None:
        paths += [home / "records" / "continue" / "progress.json", home / "works" / "LIBRARY.md",
                  *sorted((home / "works").glob("*/GOAL.md")), *sorted((home / "works").glob("*/HISTORY.md")),
                  *sorted((home / "records" / "continue" / "tasks").glob("T-*.json"))]
    for path in paths:
        try:
            add(str(path), path.lstat())
        except OSError:
            add(str(path), None)
    digest.update(f"{_engine()}\0{health.git(config.vault_path, 'rev-parse', 'HEAD')}\0{config.today()}".encode())
    return digest.hexdigest()[:16]
