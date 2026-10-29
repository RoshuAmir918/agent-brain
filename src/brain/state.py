"""Read-only, deterministic work snapshots for external consumers."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from . import access, fm, health, journal, ledger, needs, tasks, triggers
from .config import Config
from .session_context import legacy, profile_ids
from .vault import Vault


def _public(vault: Vault, source: str) -> bool:
    path = PurePosixPath(source.split("#", 1)[0])
    config = vault.config
    excluded = (config.sources_dir, PurePosixPath(config.human_profile).parent.as_posix(),
                config.people_dir, config.agents_dir, config.hermes_dir)
    if path.is_absolute() or ".." in path.parts or any(path.is_relative_to(folder) for folder in excluded):
        return False
    note = vault.notes.get(path.as_posix()) or vault.notes.get(path.as_posix() + ".md")
    return note is None or (note.data.get("tier") != "private" and note.data.get("kind") != "conversation")


def _source(vault: Vault, source: str) -> str | None:
    target, separator, anchor = source.partition("#")
    target = target.removeprefix(vault.config.vault + "/")
    relative = vault.resolve(target) or PurePosixPath(target).as_posix()
    return relative + separator + anchor if _public(vault, relative) else None


def _field(body: str, *names: str) -> str | None:
    match = re.search(r"^[ \t]*(?:-[ \t]+)?\*\*(?:" + "|".join(re.escape(name) for name in names) +
                      r"):\*\*[ \t]*([^\r\n]*)", body, re.M | re.I)
    return match[1].replace("**", "").strip() if match else None


def _tasks(vault: Vault) -> list[dict]:
    config = vault.config
    register = config.agent("Tasks.md")
    registered = tasks.load_tasks(config)[0] if (config.vault_path / register).exists() else []
    entries = [(task, False, register + "#" + task.tid) for task in registered]
    seen = {task.tid for task in registered}
    archived = []
    for path in sorted((config.vault_path / config.agent("Tasks Archive")).glob("T-*.md")):
        task = tasks.read_archived_task(config, path)
        if task is None:
            body = re.sub(r"^#{1,2} (?=T-\d{3} — )", "### ", fm.read(path)[1], flags=re.M)
            parsed = tasks.parse_register(body)[1]
            task = parsed[0] if parsed else None
        if task is not None and task.tid not in seen:
            seen.add(task.tid)
            archived.append((task, True, path.relative_to(config.vault_path).as_posix() + "#" + task.tid))
    entries.extend(sorted(archived, key=lambda entry: entry[0].tid))
    return [{"id": task.tid, "title": task.title, "state": task.task_state, "open": tasks.is_open(task),
             "detail": _field(task.body, "Status"), "next": _field(task.body, "Next", "Next action"),
             "blockers": _field(task.body, "Blockers"), "hub": _public_link(vault, task.hub),
             "tier": task.state.get("tier"), "archived": archived, "source": source}
            for task, archived, source in entries if _public(vault, source)]


def _public_link(vault: Vault, title: str | None) -> str | None:
    """A link names its target only when the target may be exported; an unresolved name is kept as written."""
    if not title:
        return None
    target = vault.resolve(title)
    return title if target is None or _public(vault, target) else None


def _improvements(body: str, source: str) -> list[dict]:
    span = needs._section(body, "Improvements")
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


def _hubs(vault: Vault, task_entries: list[dict]) -> list[dict]:
    out = []
    for source, note in sorted(vault.notes.items()):
        if not _public(vault, source) or vault.kind_for(source) != "hub" or note.data.get("kind", "hub") != "hub":
            continue
        skill, records = note.data.get("skill"), note.data.get("records")
        responsibility = {"skill": skill, "records": records} if skill and records else None
        span = needs._section(note.body, "Open questions")
        questions = [match[1].strip() for line in note.body[span[0]:span[1]].splitlines()
                     if (match := re.match(r"^[ \t]*[-*+] (.+)", line))] if span else []
        targets = {note.stem.casefold(), source.removesuffix(".md").casefold()}
        linked = [task for task in task_entries if task["hub"] and task["hub"].casefold() in targets]
        progress = {"done": sum(task["state"] == "completed" for task in linked),
                    "total": sum(task["state"] != "cancelled" for task in linked)} if linked else None
        out.append({"title": note.stem, "source": source, "responsibility": responsibility,
                    "openQuestions": questions, "improvements": _improvements(note.body, source), "progress": progress})
    return out


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
    return name.casefold() in (responsibility["records"].casefold(),
                               responsibility["skill"].removeprefix(config.skill_prefix).casefold())


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
        if row.outcome is None and skill is None:
            continue   # a legacy row's second column is not always a routine (a person, a subject); only five-column rows promise one
        out[name.casefold()] = {"name": name, "skill": skill, "hub": hub["title"] if hub else None,
                                "ledger": PurePosixPath(source).parent.as_posix()}
    for hub in responsibilities:
        if any(_matches_routine(config, hub, routine["name"]) for routine in out.values()):
            continue
        responsibility = hub["responsibility"]
        name = responsibility["records"]
        out[name.casefold()] = {"name": name, "skill": responsibility["skill"], "hub": hub["title"], "ledger": None}
    return sorted(out.values(), key=lambda routine: (routine["name"].casefold(), routine["name"]))


def _shareable(vault: Vault, hubs: list[dict], recent: list[dict]) -> dict:
    """What each audience may see. A vault still on the old table shows nothing: its deny-wins rows are prose."""
    config = vault.config
    unmigrated = legacy(vault, config)
    hub_subjects = {hub["source"]: access.hub_subject(vault, config, hub["source"]) for hub in hubs}
    titles = [{"title": hub["title"], "subject": hub_subjects[hub["source"]]} for hub in hubs]
    # Every responsibility, exported or not: a private one's receipts stay private rather than passing as plans.
    responsibilities = [{"source": source, "responsibility": {"skill": note.data["skill"], "records": note.data["records"]}}
                        for source, note in sorted(vault.notes.items())
                        if note.data.get("skill") and note.data.get("records")]
    receipts = []
    for receipt in recent:
        hub = next((hub for hub in responsibilities if _matches_routine(config, hub, receipt["routine"])), None)
        if hub and hub["source"] not in hub_subjects:
            continue
        subject = hub_subjects[hub["source"]] if hub else "plans"
        receipts.append((subject, {key: receipt[key] for key in ("at", "routine", "account")}))
    groups = access.groups(vault, config)
    audiences = [("group:" + PurePosixPath(rel).stem, access.Audience("group", rel)) for rel in groups]
    audiences.extend((kind, access.Audience(kind)) for kind in ("known", "anyone"))
    out = {}
    for key, audience in audiences:
        effective = access.resolve(vault, config, audience=audience)
        # A grant narrowed by scope prose cannot be honoured unattended, so it is not exported at all.
        tell = {} if unmigrated else {subject: value.tell for subject, value in effective.subjects.items()
                                      if value.tell != "none" and value.tell_rule not in effective.scope}
        entry = {"tell": tell,
                 "hubs": [hub for hub in titles if tell.get(hub["subject"]) in access.TELL_LEVELS[1:]][:30],
                 "receipts": [receipt for subject, receipt in receipts if tell.get(subject) == "details"][:20]}
        if audience.kind == "group":
            entry["members"] = [{"profile": rel, "ids": profile_ids(vault.notes[rel].body)}
                                for rel in groups[audience.target]]
        out[key] = entry
    return out


def _outputs(vault: Vault) -> list[dict]:
    out = []
    for source, note in sorted(vault.notes.items()):
        kind = note.data.get("kind")
        if kind not in ("draft", "reflection") or not _public(vault, source):
            continue
        leading = re.match(r"^\d{4}-\d{2}-\d{2}", note.stem)
        created = note.data.get("created") if "created" in note.data else leading[0] if leading else None
        created = created.isoformat() if isinstance(created, date) else str(created) if created is not None else None
        out.append({"title": note.stem, "kind": kind, "created": created, "source": source})
    out.sort(key=lambda output: output["created"] or "", reverse=True)
    return out


def _journal(vault: Vault, today: date) -> list[dict]:
    config = vault.config
    floor = (today - timedelta(days=29)).isoformat()
    out = []
    for path in sorted((config.vault_path / config.agent("Journal")).glob("*.md")):
        source = path.relative_to(config.vault_path).as_posix()
        for position, entry in enumerate(journal.parse(path.read_text(encoding="utf-8"))):
            at = _at(entry.when)
            if at is None or not floor <= at[:10] <= today.isoformat():
                continue
            out.append((at, source, position, {
                "id": hashlib.sha256((source + "\n" + entry.line).encode("utf-8")).hexdigest()[:12], "at": at,
                "platform": entry.platform, "outcome": entry.outcome, "headline": entry.headline,
                "next": entry.next or None, "needsYou": entry.needs_you,
                "touched": [title for title in entry.touched if _public_link(vault, title)],
                "thought": entry.thought, "source": source}))
    out.sort(key=lambda item: (item[1], item[2]))
    out.sort(key=lambda item: item[0], reverse=True)
    return [item[3] for item in out]


def build(config: Config, today: date) -> dict:
    vault = Vault(config.vault_path)
    if vault.config != config:
        vault.config = config
        vault.load()
    task_entries = _tasks(vault)
    hubs = _hubs(vault, task_entries)
    rows = _rows(vault)
    floor = (today - timedelta(days=29)).isoformat()
    recent = [{"id": hashlib.sha256((source + "\n" + line).encode("utf-8")).hexdigest()[:12],
               "at": at, "routine": row.routine, "outcome": row.outcome, "account": row.account,
               "next": row.next, "source": source}
              for at, source, _, line, row in rows if floor <= at[:10] <= today.isoformat()]
    trigger_path = config.vault_path / config.agent("Triggers.md")
    parsed = triggers.parse(trigger_path.read_text(encoding="utf-8")) if trigger_path.exists() else []
    trigger_entries = [{"id": trigger.id, "class": trigger.cls, "when": trigger.when, "do": trigger.do,
                        "status": trigger.status, "created": trigger.created, "source": config.agent("Triggers.md")}
                       for trigger in parsed] if _public(vault, config.agent("Triggers.md")) else []
    owner_items = []
    for item in needs.items(vault, today):
        source = item.source if item.origin == "access" else _source(vault, item.source)
        if source is None and item.origin == "current":
            source = "CURRENT.md"
        if source is not None:
            owner_items.append({**asdict(item), "source": source})
    try:
        engine = health.installed_version("agent-brain")
    except health.PackageNotFoundError:
        engine = "unknown"
    version = health.git(config.vault_path, "rev-parse", "--short", "HEAD")
    if version and health.git(config.vault_path, "--no-optional-locks", "status", "--porcelain"):
        version += "+dirty"
    return {"schema": 1, "engine": engine, "version": version, "tasks": task_entries, "hubs": hubs,
            "routines": _routines(config, hubs, rows), "triggers": trigger_entries, "ledger": recent,
            "journal": _journal(vault, today), "needsYou": owner_items, "outputs": _outputs(vault),
            "shareable": _shareable(vault, hubs, recent)}
