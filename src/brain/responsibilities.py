"""Responsibility identities and ownership of declared routines."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .config import Config
from .lock import vault_lock
from .vault import Note, Vault

if TYPE_CHECKING:
    from .schedule import Routine

ID_PATTERN = r"[a-z0-9][a-z0-9-]{0,62}"


def identity(config: Config, note: Note) -> str:
    value = note.data.get("records") or (note.data.get("skill") or "").removeprefix(config.skill_prefix) or note.stem
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:63].rstrip("-")


def discover(vault: Vault) -> list[dict]:
    out = []
    for source, note in sorted(vault.notes.items()):
        path = PurePosixPath(source)
        if len(path.parts) == 3 and path.parts[0] == "Responsibilities" and path.name == "RESPONSIBILITY.md":
            ident = path.parent.name
            out.append({"id": ident, "title": note.data.get("title") or ident.replace("-", " ").capitalize(),
                        "hub": source, "skill": note.data.get("skill"), "records": f"Responsibilities/{ident}/records",
                        "routines": [], "needs": note.data.get("needs", []), "work": note.data.get("work"),
                        "delivers": note.data.get("delivers"), "folder": True,
                        **{key: note.data.get(key) for key in ("triggers", "asked_by", "goal", "role", "version")}})
            continue
        if vault.kind_for(source) != "hub" or note.data.get("kind", "hub") != "hub":
            continue
        # A responsibility is a hub with its own skill (the engine's definition everywhere else); a hub without one is
        # a folder index or a context note, never something the intellect keeps up.
        if not note.data.get("skill"):
            continue
        out.append({"id": identity(vault.config, note), "title": note.stem, "hub": source,
                    "skill": note.data.get("skill"), "records": note.data.get("records"),
                    "routines": [], "needs": [], "work": note.data.get("work"), "delivers": note.data.get("delivers"),
                    "folder": False})
    return out


def derive(config: Config, entry: dict) -> dict[str, Routine]:
    from . import schedule
    ident = entry["id"]
    if not re.fullmatch(ID_PATTERN, ident):
        raise ValueError(f"responsibility id must match ^{ID_PATTERN}$")
    if type(entry.get("version")) is not int or entry["version"] != 1:
        raise ValueError("version: required and must be 1")
    triggers = entry.get("triggers")
    if not isinstance(triggers, list):
        raise ValueError("triggers must be a list of maps with a kind")
    needs = entry.get("needs", [])
    if not isinstance(needs, list) or not all(isinstance(need, str) for need in needs):
        raise ValueError("needs must be a list of strings")
    skill = entry.get("skill")
    if skill is not None and (not isinstance(skill, str) or not skill.strip()):
        raise ValueError("skill must be a nonempty string")
    out = {}
    for index, trigger in enumerate(triggers, 1):
        where = f"triggers[{index}]"
        if not isinstance(trigger, dict) or not isinstance(trigger.get("kind"), str) or not trigger["kind"].strip():
            raise ValueError(f"{where}: must be a map with a kind")
        if trigger["kind"] != "schedule":
            continue
        when = trigger.get("when")
        timing = {}
        if isinstance(when, str):
            if schedule._cron(when):
                timing = {"cron": when}
            elif re.fullmatch(r"every [1-9][0-9]*[mh]", when):
                timing = {"every": when.removeprefix("every ")}
            elif when.startswith("once "):
                value = when.removeprefix("once ")
                try:
                    moment = datetime.fromisoformat(value)
                    if not re.match(r"\d{4}-\d{2}-\d{2}[Tt ]", value):
                        raise ValueError
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=config.zone)
                    timing = {"at": moment.isoformat()}
                except ValueError:
                    pass
        if not timing:
            raise ValueError(f"{where}.when: use a five-field cron expression, every <n>m/<n>h or once <ISO datetime>")
        script = trigger.get("script")
        if script is not None:
            if (not isinstance(script, str) or not script.strip() or PurePosixPath(script).is_absolute()
                    or ".." in PurePosixPath(script).parts or PurePosixPath(script) == PurePosixPath(".")):
                raise ValueError(f"{where}.script: must be a path relative to the responsibility folder")
            script = (PurePosixPath(entry["hub"]).parent / script).as_posix()
        prompt = f"Your responsibility {entry['title']}: read {entry['hub']} and do what it says. Its state between runs lives in {entry['records']}."
        if "prompt" in trigger:
            if not isinstance(trigger["prompt"], str):
                raise ValueError(f"{where}.prompt: must be a string")
            if trigger["prompt"]:
                prompt += "\n\n" + trigger["prompt"]
        extra_needs = trigger.get("needs", [])
        if not isinstance(extra_needs, list) or not all(isinstance(need, str) for need in extra_needs):
            raise ValueError(f"{where}.needs: must be a list of strings")
        deliver = trigger.get("deliver", "demiurge")
        if not isinstance(deliver, str):
            raise ValueError(f"{where}.deliver: must be a string")
        name = ident if not out else f"{ident}-{len(out) + 1}"
        out[name] = schedule.Routine(**timing, responsibility=ident, skills=[skill] if skill else [],
                                     prompt=prompt, deliver=deliver,
                                     needs=sorted(set(needs + extra_needs)), script=script, agent=trigger.get("agent", True))
    schedule.render(out)
    return out


def sync(vault: Vault) -> list[str]:
    from . import schedule
    with vault_lock(vault.config):
        vault.load()
        entries = discover(vault)
        folders = {entry["id"]: entry for entry in entries if entry["folder"]}
        known = {entry["id"] for entry in entries}
        derived, changes = {}, []
        for ident, entry in list(folders.items()):
            # One broken wake-up document must not stop every other responsibility's schedule: it keeps the routines
            # it last synced to, and validate names the problem.
            try:
                routines = derive(vault.config, entry)
                if routines.keys() & derived.keys():
                    raise ValueError("derived routine name is already used by another responsibility")
            except ValueError as error:
                del folders[ident]
                changes.append(f"kept {ident} as it was: {entry['hub']}: {error}")
                continue
            derived.update(routines)
        routines = schedule.load(vault.config)
        for name, routine in list(routines.items()):
            ident = routine.responsibility
            if ident is None:
                continue
            removed = (ident not in known and not (vault.root / "Responsibilities" / ident).exists()
                       and re.fullmatch(re.escape(ident) + r"(?:-(?:[2-9]|[1-9][0-9]+))?", name))
            if ident in folders and (name not in derived or derived[name].responsibility != ident) or removed:
                del routines[name]
                changes.append(f"removed routine {name}")
        for name, routine in derived.items():
            old = routines.get(name)
            if old is not None:
                routine = replace(routine, enabled=old.enabled)
            if routine != old:
                routines[name] = routine
                changes.append(f"{'updated' if old is not None else 'added'} routine {name}")
        if any(not change.startswith("kept ") for change in changes):
            schedule.save(vault.config, routines)
        return changes


def link(entries: list[dict], routines: dict[str, Routine]) -> dict[str, str]:
    """Explicit ownership wins; fallback names precede skills, then hub path breaks ties."""
    by_id = {}
    for entry in entries:
        by_id.setdefault(entry["id"], entry)
    explicit = {routine.responsibility for routine in routines.values() if routine.responsibility is not None}
    owners = {}
    for name, routine in sorted(routines.items()):
        if routine.responsibility is not None:
            entry = by_id.get(routine.responsibility)
        else:
            candidates = [entry for entry in by_id.values() if entry["id"] not in explicit]
            entry = next((entry for entry in candidates if entry["id"] == name), None)
            if entry is None:
                entry = next((entry for entry in candidates if entry["skill"] and entry["skill"] in routine.skills), None)
        if entry is not None:
            owners[name] = entry["id"]
            entry["routines"].append(name)
            declared = entry["needs"] if isinstance(entry["needs"], list) else []
            entry["needs"] = sorted({need for need in declared if isinstance(need, str)} | set(routine.needs))
    return owners
