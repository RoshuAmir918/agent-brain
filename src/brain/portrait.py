"""The portrait: the agent's ranked, sourced understanding of who its owner is, a person or an organisation, guesses
included and labelled."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import date, timedelta

from . import fm
from .config import Config
from .lock import vault_lock

SECTIONS = ("After", "Cares about", "Taste", "How they decide", "Energy", "Not")
HEADINGS = {"How it decides": "How they decide"}   # what an organisation's portrait may call a section instead
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

    def public(self) -> dict:
        """What leaves the vault: everything but the counterevidence."""
        return {"id": self.id, "section": self.section, "rank": self.rank, "text": self.text, "basis": self.basis,
                "confidence": self.confidence, "evidence": self.evidence, "source": self.source,
                "horizon": self.horizon, "for": self.for_, "test": self.test, "goal": self.goal}


def parse(body: str) -> tuple[list[Line], list[str]]:
    """The lines in their sections, in rank order, and what is malformed about the note."""
    lines: list[Line] = []
    problems: list[str] = []
    section = None
    for raw in body.splitlines():
        if raw.startswith("## "):
            section = HEADINGS.get(raw[3:].strip(), raw[3:].strip())
            if section not in SECTIONS:
                problems.append(f"unknown section {section!r}; sections are {', '.join(SECTIONS)}")
            continue
        if section not in SECTIONS or not raw.strip():
            continue
        if detail := DETAIL.match(raw):
            key, value = detail["key"], detail["value"]
            if not lines or lines[-1].section != section:
                problems.append(f"{section}: {key}: line with no claim above it")
            elif key not in ("for", "test", "against", "horizon", "goal"):
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


def load(config: Config) -> list[Line]:
    path = config.vault_path / config.human_portrait
    try:
        return parse(fm.read(path)[1])[0] if path.is_file() else []
    except fm.Malformed:
        return []   # validate names it


def shown(lines: list[Line]) -> list[Line]:
    """What everyone in an organisation may see: what was stated and what was seen, never a guess about direction."""
    return [line for line in lines if line.basis in ("said", "seen")]


def top(lines: list[Line], n: int) -> list[Line]:
    return [line for line in lines if line.section == "After"][:n]


def hygiene(body: str, today: date, where: str, company: bool = False) -> list[str]:
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
        if company and line.section == "After" and not line.for_:
            out.append(f"{at}: no for line; say where you can help the company get there")
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
        try:
            data, body = fm.read(path)
        except fm.Malformed:
            return 0   # validate names it
        history = path.with_name("History.md")
        taken = set(ID.findall(body)) | (set(ID.findall(history.read_text(encoding="utf-8"))) if history.is_file() else set())
        section, count, out = None, 0, []
        for raw in body.splitlines(keepends=True):
            if raw.startswith("## "):
                section = HEADINGS.get(raw[3:].strip(), raw[3:].strip())
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


VERDICTS = ("right", "wrong", "not-this")


def mark(config: Config, ident: str, verdict: str, words: str, today: date) -> Line:
    """The owner's answer to one line. Right: it becomes `said`, high, dated today. Wrong: it leaves for History, and
    their words, when they give them, become a `Not` line. Not this: it leaves for History and the subject becomes a `Not`
    line, so the agent stops drawing on it. The caller holds the vault lock."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    ident = ident.lstrip("^")
    path = config.vault_path / config.human_portrait
    if not path.is_file():
        raise ValueError("no portrait yet")
    data, body = fm.read(path)
    lines, _ = parse(body)
    found = [line for line in lines if line.id == ident]
    if not found:
        raise ValueError(f"no portrait line ^{ident}; a line gets its id when the night or `brain portrait stamp` runs")
    line = found[0]
    raw = body.splitlines(keepends=True)
    start = next(i for i, text in enumerate(raw) if LINE.match(text.rstrip("\r\n")) and text.rstrip().endswith(f"^{ident}"))
    end = start + 1
    while end < len(raw) and DETAIL.match(raw[end].rstrip("\r\n")):
        end += 1
    words = " ".join(words.replace("·", "-").split())
    if verdict == "right":
        kept = [text for text in raw[start + 1:end] if not text.lstrip().startswith("- test:")]
        head = f"- {line.text} · said · high · {today.isoformat()} · {line.source} ^{ident}\n"
        raw[start:end] = [head, *kept]
        fm.write(path, data, "".join(raw))
        line.basis, line.confidence, line.evidence = "said", "high", today.isoformat()
        return line
    removed = "".join(raw[start:end]).rstrip("\n")
    del raw[start:end]
    history = path.with_name("History.md")
    hdata, hbody = fm.read(history) if history.is_file() else ({"kind": "log", "tier": "cold"}, "# History\n")
    said = f" · their words: {words}" if words else ""
    hbody = hbody.rstrip("\n") + f"\n- {today.isoformat()} · portrait line marked {verdict} by the owner{said}\n" + \
        "".join(f"  {text}\n" for text in removed.splitlines()) 
    fm.write(history, hdata, hbody)
    text = words if verdict == "wrong" else f"Not to draw on: {words or line.text}"
    body = "".join(raw)
    if text:
        link = f"[[{config.link(history.relative_to(config.vault_path).as_posix()[:-3])}]]"
        bullet = f"- {text} · said · high · {today.isoformat()} · {link}\n"
        heading = re.search(r"^## Not[ \t]*$", body, re.M)
        if heading:
            following = re.compile(r"^## ", re.M).search(body, heading.end())
            at = following.start() if following else len(body)
            body = body[:at].rstrip("\n") + "\n" + bullet + ("\n" + body[at:] if following else "")
        else:
            body = body.rstrip("\n") + "\n\n## Not\n" + bullet
    fm.write(path, data, body)
    return line
