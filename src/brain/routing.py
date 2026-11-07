"""Rank colleagues from area records; reading evidence never changes it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median

from . import directory, fm, needs, owners, tasks
from .config import Config
from .retrieve import tokens

WEIGHTS = {   # add evidence across relevant areas
    "owner": 12,          # strongest single hint: a person's ownership map
    "backup": 6,          # the map's alternate
    "answered": 3,        # a decision that still stands
    "overruled": -5,      # each correction of their answer
    "overruling": 5,      # someone whose word corrected an answer
    "redirected-to": 4,   # someone else pointed at them
    "redirected-away": -1,  # they suggested asking someone else
    "unanswered": -2,     # silence at the end of a consult
    "delegated": 10,      # an explicit delegation in a person's words
    "approved": 4,        # seeded approvals
    "reviewed": 2,        # seeded reviews
    "changed": 1,         # seeded changes
    "deferred-to": 6,     # seeded deference from others
    "stop-asking": 0,     # keeps muted people visible without changing their score
}
SEED_KINDS = ("approved", "reviewed", "changed", "deferred-to")   # history imported by a seeder
HALF_LIFE_DAYS = 90   # only seeded history decays; explicit decisions and ownership remain on record
DECISION = re.compile(r"^- (\d{4}-\d{2}-\d{2}) · (.+?) · (.*?) · id:([0-9a-f]{12})"
                      r"(?=\n|$)((?:\n[ \t]+-[^\n]*)*)", re.M)   # an answer and its adjacent annotations


def area_name(area: str) -> str:
    area = " ".join(area.casefold().split())
    if not owners.AREA.fullmatch(area):
        raise ValueError("area must use letters, digits, spaces or hyphens")
    return area


def area_path(config: Config, area: str) -> Path:
    return config.vault_path / config.agent(f"Areas/{area_name(area)}.md")


def name(config: Config, person: str) -> str:
    for row in owners.load(config):
        if person == row.owner:
            return row.owner_name
        if person == row.backup:
            return row.backup_name
    entry = directory.lookup(config, person)
    return (entry.get("name") or person) if entry and entry.get("kind") == "person" else person


def area_bodies(config: Config) -> dict[str, str]:
    return {path.stem: fm.read(path)[1] for path in sorted((config.vault_path / config.agent("Areas")).glob("*.md"))
            if owners.AREA.fullmatch(path.stem)}


def section(body: str, title: str) -> str:
    span = needs._section(body, title)
    return body[span[0]:span[1]] if span else ""


def stopped(config: Config, area: str, person: str) -> bool:
    path = area_path(config, area)
    body = fm.read(path)[1] if path.exists() else ""
    return bool(re.search(r"^- \d{4}-\d{2}-\d{2} · " + re.escape(person) + r" · stop-asking$",
                          section(body, "Not asked"), re.M))


@dataclass
class Decision:
    area: str
    day: str
    name: str
    text: str
    ident: str
    source: str
    question: str
    overrules: list[tuple[str, str, str]]
    end: int


def decisions(body: str, area: str) -> list[Decision]:
    out = []
    span = needs._section(body, "Decisions")
    for match in DECISION.finditer(body, *span) if span else []:
        day, author, text, ident, details = match.groups()
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        source = re.search(r" · \[\[([^\[\]\n]+)\]\]$", text)
        question = re.search(r"^  - asked: (.*)$", details, re.M)
        overrules = re.findall(r"^  - overruled (\d{4}-\d{2}-\d{2}) by (.+?): (.*)$", details, re.M)
        out.append(Decision(area, day, author, text[:source.start()] if source else text, ident,
                            source[1] if source else "", question[1] if question else "", overrules, match.end()))
    return out


def responses(config: Config) -> list[dict]:
    """Read consult outcomes; redirect words stay beside their Answered entry."""
    out = []
    for path in sorted((config.vault_path / config.agent("Answered")).glob("*.md")):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("  - answer: ") and current is not None:
                current["answer"] = line.removeprefix("  - answer: ")
                continue
            current = None
            parts = line.removeprefix("- ").split(" · ")
            if len(parts) not in (7, 8) or parts[1] not in (
                    "consult changed", "consult confirmed", "consult redirected", "consult overruled", "consult unanswered"):
                continue
            try:
                date.fromisoformat(parts[0])
                hours = None if parts[5] == "-" else float(parts[5].removesuffix("h"))
            except ValueError:
                continue
            person = parts[3].removeprefix("by ")
            if not tasks.PERSON.fullmatch(person) or not re.fullmatch(r"id:[0-9a-f]{12}", parts[-1]):
                continue
            current = dict(date=parts[0], outcome=parts[1].removeprefix("consult "), text=parts[2],
                           person=person, area=parts[4], hours=hours, to=parts[6].removeprefix("to ") if len(parts) == 8 else "",
                           id=parts[-1][3:], answer="", link=path.relative_to(config.vault_path).as_posix())
            out.append(current)
    return out


def delegations(body: str) -> list[dict]:
    out = []
    current = None
    for line in section(body, "Delegated").splitlines():
        if line.startswith("  - consult: ") and current is not None:
            current["consult"] = line.removeprefix("  - consult: ").split(" · for: ", 1)[0]
            continue
        current = None
        parts = line.removeprefix("- ").split(" · ", 3)
        if len(parts) != 4 or not parts[2].startswith("to ") or not tasks.PERSON.fullmatch(parts[2][3:]):
            continue
        try:
            date.fromisoformat(parts[0])
        except ValueError:
            continue
        current = dict(date=parts[0], by=parts[1], person=parts[2][3:], text=parts[3], consult="")
        out.append(current)
    return out


def allowed(config: Config, area: str, consult: str = "") -> set[str]:
    row = next((row for row in owners.load(config) if row.area == area), None)
    people = {row.owner, row.backup} - {""} if row else set()
    path = area_path(config, area)
    body = fm.read(path)[1] if path.exists() else ""
    return people | {entry["person"] for entry in delegations(body)
                     if not entry["consult"] or consult and entry["consult"] == consult}


def relevant(config: Config, about: str, area: str = "") -> list[str]:
    """Areas the question touches, best match first."""
    return list(relevance(config, about, area))


def relevance(config: Config, about: str, area: str = "") -> dict[str, float]:
    """Match significant words against area names, covers and recorded questions and answers. Each area's weight is
    its shared words over the best match's, so an area touched by one word counts for less than one touched by three;
    a named area counts in full."""
    words = tokens(about.casefold())
    texts = {row.area: row.area + " " + row.covers for row in owners.load(config)}
    for key, body in area_bodies(config).items():
        texts[key] = texts.get(key, key) + " " + " ".join(
            entry.question + " " + entry.text + " " + " ".join(words for _, _, words in entry.overrules)
            for entry in decisions(body, key))
    for response in responses(config):
        key = response["area"]
        texts[key] = texts.get(key, key) + " " + response["text"] + " " + response["answer"]
    shared = {key: len(words & tokens(text.casefold())) for key, text in texts.items()}
    best = max(shared.values(), default=0)
    ranked = {key: count / best for key, count in sorted(shared.items(), key=lambda pair: (-pair[1], pair[0])) if count}
    if area:
        explicit = area_name(area)
        ranked = {explicit: 1.0} | {key: weight for key, weight in ranked.items() if key != explicit}
    return ranked


@dataclass
class Candidate:
    person: str
    name: str
    score: float = 0
    median_hours: float | None = None
    muted: bool = False
    evidence: list[dict] = field(default_factory=list)


def candidates(config: Config, about: str, area: str = "", limit: int | None = None) -> list[Candidate]:
    """Rank people, retain their evidence and put opted-out colleagues last."""
    if config.edition != "enterprise":
        raise ValueError("consults need the enterprise edition")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    relevant_areas = relevance(config, about, area)
    target = area_name(area) if area else next(iter(relevant_areas), "general")
    people = {}
    hours = {}

    def add(person: str, key: str, kind: str, day: str, text: str, link: str, factor: float = 1) -> None:
        candidate = people.setdefault(person, Candidate(person, name(config, person)))
        candidate.score += WEIGHTS[kind] * factor * relevant_areas[key]
        candidate.evidence.append(dict(area=key, kind=kind, date=day, text=text, link=link))

    for row in owners.load(config):
        if row.area in relevant_areas:
            for person, kind in ((row.owner, "owner"), (row.backup, "backup")):
                if person:
                    add(person, row.area, kind, "", row.covers + ("; " + row.source if row.source else ""), config.agent("Owners.md"))
    logs = responses(config)
    answerers = {entry["id"]: entry["person"] for entry in logs if entry["outcome"] in ("changed", "confirmed")}
    for key, body in area_bodies(config).items():
        if key not in relevant_areas:
            continue
        link = config.agent(f"Areas/{key}.md")
        for decision in decisions(body, key):
            person = answerers.get(decision.ident)
            if not person:
                matches = {identity for row in owners.load(config)
                           for identity, label in ((row.owner, row.owner_name), (row.backup, row.backup_name))
                           if identity and label == decision.name}
                person = next(iter(matches)) if len(matches) == 1 else decision.name if tasks.PERSON.fullmatch(decision.name) else ""
            if person:
                if not decision.overrules:
                    add(person, key, "answered", decision.day, decision.text, decision.source or link)
                for day, _, words in decision.overrules:
                    add(person, key, "overruled", day, words, link)
        for entry in delegations(body):
            add(entry["person"], key, "delegated", entry["date"], entry["text"], link)
        for line in section(body, "Evidence").splitlines():
            source = re.search(r" · \[\[([^\[\]\n]+)\]\]$", line)
            parts = (line[:source.start()] if source else line).removeprefix("- ").split(" · ", 3)
            if len(parts) < 4 or not tasks.PERSON.fullmatch(parts[1]) or parts[2] not in SEED_KINDS:
                continue
            try:
                age = max(0, (config.today() - date.fromisoformat(parts[0])).days)
            except ValueError:
                continue
            add(parts[1], key, parts[2], parts[0], parts[3], source[1] if source else link, 0.5 ** (age / HALF_LIFE_DAYS))
        for day, person in re.findall(r"^- (\d{4}-\d{2}-\d{2}) · (\S+) · stop-asking$", section(body, "Not asked"), re.M):
            if tasks.PERSON.fullmatch(person):
                add(person, key, "stop-asking", day, "Asked not to be consulted in this area", link)
    for entry in logs:
        key, outcome, person = entry["area"], entry["outcome"], entry["person"]
        if key not in relevant_areas:
            continue
        kind = {"redirected": "redirected-away", "overruled": "overruling", "unanswered": "unanswered"}.get(outcome)
        if kind:
            add(person, key, kind, entry["date"], entry["answer"] or entry["text"], entry["link"])
        if outcome == "redirected" and tasks.PERSON.fullmatch(entry["to"]):
            add(entry["to"], key, "redirected-to", entry["date"], entry["answer"] or entry["text"], entry["link"])
        if outcome in ("changed", "confirmed", "redirected") and entry["hours"] is not None:
            hours.setdefault(person, []).append(entry["hours"])
    for person, candidate in people.items():
        candidate.muted = stopped(config, target, person)
        candidate.median_hours = median(hours[person]) if person in hours else None
        candidate.evidence.sort(key=lambda item: (-abs(WEIGHTS[item["kind"]]), item["area"], item["date"]))
    ranked = sorted(people.values(), key=lambda entry: (entry.muted, -entry.score,
                    entry.median_hours if entry.median_hours is not None else float("inf"), entry.person))
    return ranked[:limit] if limit is not None else ranked
