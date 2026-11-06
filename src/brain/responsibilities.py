"""Responsibility identities and ownership of declared routines."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .config import Config
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
        if vault.kind_for(source) != "hub" or note.data.get("kind", "hub") != "hub":
            continue
        if not (note.data.get("skill") or note.data.get("records") or note.data.get("kind") == "hub"):
            continue
        out.append({"id": identity(vault.config, note), "title": note.stem, "hub": source,
                    "skill": note.data.get("skill"), "records": note.data.get("records"),
                    "routines": [], "needs": [], "work": note.data.get("work"), "delivers": note.data.get("delivers")})
    return out


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
            entry["needs"] = sorted(set(entry["needs"]) | set(routine.needs))
    return owners
