"""Ownership hints and the areas whose actions require a named person."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config
from .tasks import PERSON

WAIT_HOURS = 4   # working hours before trying another person or going ahead
AREA = re.compile(r"[A-Za-z0-9][A-Za-z0-9 -]*")   # safe area names, also used for area filenames
COLUMNS = ("area", "owner", "backup", "covers", "wait", "source")   # the operator's table, in order


@dataclass
class Owner:
    area: str
    owner: str
    owner_name: str
    backup: str
    backup_name: str
    covers: str
    wait_hours: int
    source: str


def _person(cell: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)\s+\(([^()]+)\)", cell)
    if not match or not PERSON.fullmatch(match[2]):
        raise ValueError("use Name (<platform>:<id>) for each owner and backup")
    return match[2], match[1].strip()


def _read(config: Config) -> tuple[list[Owner], list[str]]:
    rel = config.agent("Owners.md")
    path = config.vault_path / rel
    rows, problems, seen = [], [], set()
    if not path.exists():
        return rows, problems
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]
        if tuple(cell.casefold() for cell in cells) == COLUMNS or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        prefix = f"{rel}:{number}"
        if len(cells) != len(COLUMNS):
            problems.append(f"{prefix}: use the columns {' | '.join(COLUMNS)}")
            continue
        area, owner, backup, covers, wait, source = cells
        area = " ".join(area.casefold().split())
        errors = []
        if not AREA.fullmatch(area):
            errors.append("area must use letters, digits, spaces or hyphens")
        if area in seen:
            errors.append(f"duplicate area {area!r}; keep one row per area")
        seen.add(area)
        try:
            person, name = _person(owner)
            other, other_name = _person(backup) if backup else ("", "")
        except ValueError as error:
            errors.append(str(error))
        if wait and not re.fullmatch(r"[0-9]+", wait):
            errors.append("wait must be a non-negative integer of working hours, or blank for 4")
        if errors:
            problems.extend(f"{prefix}: {error}" for error in errors)
        else:
            rows.append(Owner(area, person, name, other, other_name, covers, int(wait) if wait else WAIT_HOURS, source))
    return rows, problems


def load(config: Config) -> list[Owner]:
    """The rows that parse. The map is a hint, so a bad row is skipped rather than stopping every consult; validation
    names it. A restricted area whose row is bad has no owner on record, so its gate refuses."""
    return _read(config)[0]


def problems(config: Config) -> list[str]:
    return _read(config)[1]


def restricted(config: Config) -> set[str]:
    """Read restricted areas from the optional Restricted section."""
    from . import needs

    path = config.vault_path / config.agent("Owners.md")
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    span = needs._section(body, "Restricted")
    return {" ".join(line[2:].casefold().split())
            for line in body[span[0]:span[1]].splitlines() if line.startswith("- ")
            and AREA.fullmatch(" ".join(line[2:].casefold().split()))} if span else set()
