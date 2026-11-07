"""The owner's portrait: the agent's ranked, sourced understanding of who they are, guesses included and labelled."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import date, timedelta

from . import fm
from .config import Config
from .lock import vault_lock

SECTIONS = ("After", "Cares about", "Taste", "How they decide", "Energy", "Not")
BASES = ("said", "seen", "guess")
CONFIDENCES = ("high", "medium", "low")
HORIZONS = ("now", "year", "longer")
WORDS = 700
SECTION_LINES = 8
STALE_GUESS_DAYS = 30
LINE = re.compile(r"^- (?P<text>.+?) · (?P<basis>[^ ·]+) · (?P<confidence>[^ ·]+) · (?P<evidence>[^ ·]+) · "
                  r"(?P<source>.+?)(?: \^(?P<id>[0-9a-f]{4}))?[ \t]*$")
DETAIL = re.compile(r"^[ \t]+- (?P<key>[a-z]+):[ \t]*(?P<value>.*?)[ \t]*$")
ID = re.compile(r"\^([0-9a-f]{4})\b")
AFTER_ONLY = ("for", "horizon")


@dataclass
class Line:
    id: str
    section: str
    rank: int
    text: str
    basis: str
    confidence: str
    evidence: str
    source: str
    horizon: str = ""
    for_: str = ""
    test: str = ""
    against: str = ""

    def public(self) -> dict:
        """What leaves the vault: everything but the counterevidence."""
        return {"id": self.id, "section": self.section, "rank": self.rank, "text": self.text, "basis": self.basis,
                "confidence": self.confidence, "evidence": self.evidence, "source": self.source,
                "horizon": self.horizon, "for": self.for_, "test": self.test}


def parse(body: str) -> tuple[list[Line], list[str]]:
    """The lines in their sections, in rank order, and what is malformed about the note."""
    lines: list[Line] = []
    problems: list[str] = []
    section = None
    for raw in body.splitlines():
        if raw.startswith("## "):
            section = raw[3:].strip()
            if section not in SECTIONS:
                problems.append(f"unknown section {section!r}; sections are {', '.join(SECTIONS)}")
            continue
        if section not in SECTIONS or not raw.strip():
            continue
        if detail := DETAIL.match(raw):
            key, value = detail["key"], detail["value"]
            if not lines or lines[-1].section != section:
                problems.append(f"{section}: {key}: line with no claim above it")
            elif key not in ("for", "test", "against", "horizon"):
                problems.append(f"{section} · {lines[-1].text}: unknown field {key!r}")
            elif key in AFTER_ONLY and section != "After":
                problems.append(f"{section} · {lines[-1].text}: {key} belongs to After lines only")
            elif key == "horizon" and value not in HORIZONS:
                problems.append(f"{section} · {lines[-1].text}: horizon {value!r}; one of {', '.join(HORIZONS)}")
            else:
                attr = "for_" if key == "for" else key
                setattr(lines[-1], attr, (getattr(lines[-1], attr) + " " + value).strip())
            continue
        match = LINE.match(raw)
        if not match:
            problems.append(f"{section}: malformed line {raw.strip()[:80]!r}; "
                            "write `- text · basis · confidence · YYYY-MM-DD · [[source]]`")
            continue
        where = f"{section} · {match['text']}"
        if match["basis"] not in BASES:
            problems.append(f"{where}: basis {match['basis']!r}; one of {', '.join(BASES)}")
        if match["confidence"] not in CONFIDENCES:
            problems.append(f"{where}: confidence {match['confidence']!r}; one of {', '.join(CONFIDENCES)}")
        try:
            date.fromisoformat(match["evidence"])
        except ValueError:
            problems.append(f"{where}: evidence date {match['evidence']!r} is not YYYY-MM-DD")
        rank = sum(line.section == section for line in lines) + 1
        lines.append(Line(match["id"] or "", section, rank, match["text"], match["basis"], match["confidence"],
                          match["evidence"], match["source"]))
    return lines, problems


def load(config: Config) -> list[Line]:
    path = config.vault_path / config.human_portrait
    return parse(fm.read(path)[1])[0] if path.is_file() else []


def top(lines: list[Line], n: int) -> list[Line]:
    return [line for line in lines if line.section == "After"][:n]


def hygiene(body: str, today: date, where: str) -> list[str]:
    lines, problems = parse(body)
    out = [f"{where}: {problem}" for problem in problems]
    words = len(body.split())
    if words > WORDS:
        out.append(f"{where}: {words} words; keep it under {WORDS}, older versions of a line in History.md")
    for section in SECTIONS:
        count = sum(line.section == section for line in lines)
        if count > SECTION_LINES:
            out.append(f"{where} · {section}: {count} lines; at most {SECTION_LINES}, ranked, the rest retired to History.md")
    seen: set[str] = set()
    floor = (today - timedelta(days=STALE_GUESS_DAYS)).isoformat()
    for line in lines:
        at = f"{where} · {line.section} · {line.text}"
        if line.id and line.id in seen:
            out.append(f"{at}: id ^{line.id} is used twice; give the newer claim no id and the night assigns one")
        seen.add(line.id)
        if line.section == "After" and not line.horizon:
            out.append(f"{at}: no horizon line; one of {', '.join(HORIZONS)}")
        if line.basis == "guess" and line.evidence < floor:
            out.append(f"{at}: a guess with no evidence since {line.evidence}; retire it to History.md")
    return out


def stamp(config: Config) -> int:
    """Give each claim without one a short id, written on the line, so reception rows and explore accounts that point
    at it survive the line being reworded or re-ranked. Ids are never reused, including those retired to History."""
    path = config.vault_path / config.human_portrait
    if not path.is_file():
        return 0
    with vault_lock(config):
        data, body = fm.read(path)
        history = path.with_name("History.md")
        taken = set(ID.findall(body)) | (set(ID.findall(history.read_text(encoding="utf-8"))) if history.is_file() else set())
        section, count, out = None, 0, []
        for raw in body.splitlines(keepends=True):
            if raw.startswith("## "):
                section = raw[3:].strip()
            match = LINE.match(raw.rstrip("\r\n")) if section in SECTIONS else None
            if match and not match["id"]:
                new = secrets.token_hex(2)
                while new in taken:
                    new = secrets.token_hex(2)
                taken.add(new)
                ending = raw[len(raw.rstrip("\r\n")):]
                raw = raw.rstrip("\r\n").rstrip() + f" ^{new}" + ending
                count += 1
            out.append(raw)
        if count:
            fm.write(path, data, "".join(out))
    return count
