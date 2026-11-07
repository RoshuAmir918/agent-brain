"""Ownership hints and the areas whose actions require a named person."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import fm
from .config import Config
from .tasks import PERSON

WAIT_HOURS = 4   # working hours before trying another person or going ahead
AREA = re.compile(r"[A-Za-z0-9][A-Za-z0-9 -]*")   # safe area names, also used for area filenames
COLUMNS = ("area", "owner", "backup", "covers", "wait", "source")   # the operator's table, in order
PEOPLE_COLUMNS = ("person", "hours", "away")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


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


@dataclass
class Hours:
    days: tuple[int, ...] = (1, 2, 3, 4, 5)
    start: str = "09:00"
    end: str = "17:00"
    zone: str = ""
    away: str = ""
    away_start: str = ""
    name: str = ""

    def schedule(self) -> dict:
        return dict(days=list(self.days), start=self.start, end=self.end, zone=self.zone)

    def away_on(self, moment: datetime) -> str:
        if not self.away:
            return ""
        day = moment.astimezone(ZoneInfo(self.zone)).date().isoformat()
        return self.away if self.away_start <= day <= self.away else ""


def _person(cell: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)\s+\(([^()]+)\)", cell)
    if not match or not PERSON.fullmatch(match[2]):
        raise ValueError("use Name (<platform>:<id>) for each owner and backup")
    return match[2], match[1].strip()


def _table(config: Config, *, people: bool = False) -> list[tuple[int, list[str]]]:
    """Read only the first table outside People, or the first table in People. Other tables are never area rows."""
    path = config.vault_path / config.agent("Owners.md")
    rows, in_people = [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines() if path.exists() else [], 1):
        if line.startswith("## "):
            in_people = line.strip() == "## People"
        if in_people == people and line.strip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]
            rows.append((number, cells))
        elif rows:
            break
    return rows


def _read(config: Config) -> tuple[list[Owner], list[str]]:
    rel = config.agent("Owners.md")
    rows, problems, seen = [], [], set()
    for number, cells in _table(config):
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


def _hours(cell: str, zone: str) -> Hours:
    if not cell:
        return Hours(zone=zone)
    match = re.fullmatch(r"([A-Za-z,-]+) ([0-2][0-9]:[0-5][0-9])-([0-2][0-9]:[0-5][0-9])(?: (\S+))?", cell)
    if not match:
        raise ValueError("hours must be <days> HH:MM-HH:MM [IANA zone], or blank")
    days, start, end, zone = match[1], match[2], match[3], match[4] or zone
    weekdays = {day.casefold(): number for number, day in enumerate(DAYS, 1)}
    try:
        if "-" in days:
            first, last = (weekdays[day.casefold()] for day in days.split("-"))
            chosen = {(first - 1 + offset) % 7 + 1 for offset in range((last - first) % 7 + 1)}
        else:
            chosen = {weekdays[day.casefold()] for day in days.split(",")}
    except (KeyError, ValueError):
        raise ValueError("days must be Mon-Fri, a weekday range or a comma list such as Mon,Tue,Thu") from None
    if start >= end or end >= "24:00":
        raise ValueError("hours must open before closing on the same day, between 00:00 and 23:59")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"hours zone is not a known IANA zone: {zone}") from None
    return Hours(tuple(sorted(chosen)), start, end, zone)


def _away(cell: str) -> tuple[str, str]:
    if not cell:
        return "", ""
    match = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2}) to |until )(\d{4}-\d{2}-\d{2})", cell)
    try:
        if match:
            start = date.fromisoformat(match[1]).isoformat() if match[1] else ""
            end = date.fromisoformat(match[2]).isoformat()
            if start <= end:
                return start, end
    except ValueError:
        pass
    raise ValueError("away must be YYYY-MM-DD to YYYY-MM-DD (in order), until YYYY-MM-DD, or blank")


def _read_people(config: Config, aliases: dict[str, str] | None = None) -> tuple[dict[str, Hours], list[str]]:
    from . import routing

    rows, problems = {}, []
    for number, cells in _table(config, people=True):
        if tuple(cell.casefold() for cell in cells) == PEOPLE_COLUMNS or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        prefix = f"{config.agent('Owners.md')}:{number}"
        try:
            if len(cells) != len(PEOPLE_COLUMNS):
                raise ValueError(f"use the columns {' | '.join(PEOPLE_COLUMNS)}")
            person, name = _person(cells[0])
            hours = _hours(cells[1], config.timezone)
            hours.away_start, hours.away = _away(cells[2])
            hours.name = name
            if aliases is None:
                aliases = routing.identities(config)
            person = aliases.get(person, person)
            if person in rows:
                raise ValueError(f"duplicate person {person!r}; keep one row per person")
            rows[person] = hours
        except ValueError as error:
            problems.append(f"{prefix}: {error}")
    return rows, problems


def people(config: Config, aliases: dict[str, str] | None = None) -> dict[str, Hours]:
    """People's stated hours and absences, keyed by their canonical identity."""
    return _read_people(config, aliases)[0]


def hours_lookup(config: Config):
    """Each person's hours, read once: an export resolves every consult's recipient without rereading every profile."""
    from . import routing

    aliases = routing.identities(config)
    table = people(config, aliases)
    return lambda person: table.get(aliases.get(person, person), Hours(zone=config.timezone))


def hours_for(config: Config, person: str) -> Hours:
    return hours_lookup(config)(person)


def load(config: Config) -> list[Owner]:
    """The rows that parse. The map is a hint, so a bad row is skipped rather than stopping every consult; validation
    names it. A restricted area whose row is bad has no owner on record, so its gate refuses."""
    return _read(config)[0]


def problems(config: Config) -> list[str]:
    return _read(config)[1] + _read_people(config)[1]


def restricted(config: Config) -> set[str]:
    """Read restricted areas from the optional Restricted section."""
    path = config.vault_path / config.agent("Owners.md")
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    span = fm.section(body, "Restricted")
    return {" ".join(line[2:].casefold().split())
            for line in body[span[0]:span[1]].splitlines() if line.startswith("- ")
            and AREA.fullmatch(" ".join(line[2:].casefold().split()))} if span else set()
