"""The portrait's line: one ranked, sourced claim about who the owner is, a person or an organisation, guesses
included and labelled (docs/portrait.md), and the one note, `Portrait.md`, that every session reads. Where lines live
and how they get there is the image's (`image.py`, docs/image.md)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import fm
from .config import Config

SECTIONS = ("After", "Cares about", "Taste", "How they decide", "Energy", "Not")

HEADINGS = {"How it decides": "How they decide"}   # what an organisation's portrait may call a section instead

BASES = ("said", "seen", "guess")

CONFIDENCES = ("high", "medium", "low")

HORIZONS = ("now", "year", "longer")

WORDS = 700

SECTION_LINES = 8

STALE_GUESS_DAYS = 30
SKETCH = "Sketch"   # a note's prose picture of its area, before its lines: never parsed as lines
SKETCH_WORDS = 120

LINE = re.compile(r"^- (?P<text>.+?) · (?P<basis>[^ ·]+) · (?P<confidence>[^ ·]+) · (?P<evidence>[^ ·]+) · "
                  r"(?P<source>.+?)(?: \^(?P<id>[0-9a-f]{4}))?[ \t]*$")

DETAIL = re.compile(r"^[ \t]+- (?P<key>[a-z]+):[ \t]*(?P<value>.*?)[ \t]*$")

ID = re.compile(r"\^([0-9a-f]{4})\b")

SEEDED = re.compile(r"\bSeeds/(\d{4}-\d{2}-\d{2}) ")   # a Seeds note is named for the day it was read

AFTER_ONLY = ("for", "horizon", "goal")   # goal: the person's G-n this line reflects, from `brain goals`


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
    goal: str = ""
    because: str = ""   # what was seen or said that the claim rests on; the line itself says who they are
    facet: str = ""

    def public(self) -> dict:
        """What leaves the vault: everything but the counterevidence."""
        return {"id": self.id, "section": self.section, "rank": self.rank, "text": self.text, "basis": self.basis,
                "confidence": self.confidence, "evidence": self.evidence, "source": self.source,
                "horizon": self.horizon, "for": self.for_, "test": self.test, "goal": self.goal, "because": self.because,
                "facet": self.facet}


def parse(body: str) -> tuple[list[Line], list[str]]:
    """The lines in their sections, in rank order, and what is malformed about the note."""
    lines: list[Line] = []
    problems: list[str] = []
    section = None
    for raw in body.splitlines():
        if raw.startswith("## "):
            section = HEADINGS.get(raw[3:].strip(), raw[3:].strip())
            if section not in SECTIONS and section != SKETCH:
                problems.append(f"unknown section {section!r}; sections are {', '.join(SECTIONS)}")
            continue
        if section not in SECTIONS or not raw.strip():
            continue
        if detail := DETAIL.match(raw):
            key, value = detail["key"], detail["value"]
            if not lines or lines[-1].section != section:
                problems.append(f"{section}: {key}: line with no claim above it")
            elif key not in ("for", "test", "against", "horizon", "goal", "because"):
                problems.append(f"{section} · {lines[-1].text}: unknown field {key!r}")
            elif key in AFTER_ONLY and section != "After":
                problems.append(f"{section} · {lines[-1].text}: {key} belongs to After lines only")
            elif key == "goal" and not re.fullmatch(r"G-\d+", value):
                problems.append(f"{section} · {lines[-1].text}: goal {value!r}; a goal id such as G-3")
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


def sketch(body: str) -> str:
    """A note's prose picture, the paragraph under its `## Sketch` heading, joined into one."""
    found = re.search(r"^## Sketch[ \t]*\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    return " ".join(found[1].split()) if found else ""


def portrait_path(config: Config) -> Path:
    return config.vault_path / config.human_portrait


def load(config: Config) -> list[Line]:
    """The portrait's lines: the compiled summary once there are facets."""
    path = portrait_path(config)
    try:
        return parse(fm.read(path)[1])[0] if path.is_file() else []
    except fm.Malformed:
        return []   # validate names it


def heading(config: Config, section: str) -> str:
    return "How it decides" if section == "How they decide" and config.edition == "enterprise" else section


def shown(lines: list[Line]) -> list[Line]:
    """What everyone in an organisation may see: what was stated and what was seen, never a guess about direction."""
    return [line for line in lines if line.basis in ("said", "seen")]


def top(lines: list[Line], n: int) -> list[Line]:
    return [line for line in lines if line.section == "After"][:n]


def hygiene(body: str, today: date, where: str, company: bool = False, cap: int = WORDS) -> list[str]:
    lines, problems = parse(body)
    out = [f"{where}: {problem}" for problem in problems]
    words = len(body.split())
    if words > cap:
        out.append(f"{where}: {words} words; keep it under {cap}, older versions of a line in History.md")
    for section in SECTIONS:
        count = sum(line.section == section for line in lines)
        if count > SECTION_LINES:
            out.append(f"{where} · {section}: {count} lines; at most {SECTION_LINES}, ranked, the rest retired to History.md")
    if len(sketch(body).split()) > SKETCH_WORDS:
        out.append(f"{where} · Sketch: {len(sketch(body).split())} words; keep the picture under {SKETCH_WORDS}")
    seen: set[str] = set()
    floor = (today - timedelta(days=STALE_GUESS_DAYS)).isoformat()
    for line in lines:
        at = f"{where} · {line.section} · {line.text}"
        if line.id and line.id in seen:
            out.append(f"{at}: id ^{line.id} is used twice; give the newer claim no id and the night assigns one")
        seen.add(line.id)
        if line.section == "After" and not line.horizon:
            out.append(f"{at}: no horizon line; one of {', '.join(HORIZONS)}")
        if company and line.section == "After" and not line.for_:
            out.append(f"{at}: no for line; say where you can help the company get there")
        # A seed reads a year of mail at once, so a guess it wrote waits thirty days from the seed, not from its evidence.
        if line.basis == "guess" and max([line.evidence, *SEEDED.findall(line.source)]) < floor:
            out.append(f"{at}: a guess with no evidence since {line.evidence}; retire it to History.md")
    return out


def block(raw: list[str], ident: str) -> tuple[int, int]:
    """Where a line and its detail lines sit in a note's lines."""
    start = next(i for i, text in enumerate(raw) if LINE.match(text.rstrip("\r\n")) and text.rstrip().endswith(f"^{ident}"))
    end = start + 1
    while True:   # parse reads details across blank lines, so a move or an answer takes them along
        gap = end
        while gap < len(raw) and not raw[gap].strip():
            gap += 1
        if gap == len(raw) or not DETAIL.match(raw[gap].rstrip("\r\n")):
            return start, end
        end = gap + 1


def insert(body: str, title: str, text: str) -> str:
    """Add text at the end of a section, creating the section at the end of the note when it is missing."""
    found = re.search(rf"^## {re.escape(title)}[ \t]*$", body, re.M)
    if not found:
        return body.rstrip("\n") + f"\n\n## {title}\n" + text
    following = re.compile(r"^## ", re.M).search(body, found.end())
    at = following.start() if following else len(body)
    return body[:at].rstrip("\n") + "\n" + text + ("\n" + body[at:] if following else "")


VERDICTS = ("right", "wrong", "not-this")

