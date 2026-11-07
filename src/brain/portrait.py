"""The portrait: the agent's ranked, sourced understanding of who its owner is, a person or an organisation, guesses
included and labelled."""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import evidence, fm
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
FACET_WORDS = 1500
FACET_LINES = 40
SENSITIVITIES = ("open", "said-only")
SAID_ONLY = ("Health", "Money")   # starting facets that take only what the owner said


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
    facet: str = ""

    def public(self) -> dict:
        """What leaves the vault: everything but the counterevidence."""
        return {"id": self.id, "section": self.section, "rank": self.rank, "text": self.text, "basis": self.basis,
                "confidence": self.confidence, "evidence": self.evidence, "source": self.source,
                "horizon": self.horizon, "for": self.for_, "test": self.test, "goal": self.goal, "facet": self.facet}


@dataclass
class Facet:
    """One area of the owner's life or of the organisation, one note in the Portrait folder: the image, in depth."""
    name: str
    rank: int
    sensitivity: str
    rel: str
    lines: list[Line] = field(default_factory=list)


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


def portrait_path(config: Config) -> Path:
    return config.vault_path / config.human_portrait


def folder(config: Config) -> Path:
    """The facets live beside the portrait, in a folder of its name."""
    return portrait_path(config).with_suffix("")


def load(config: Config) -> list[Line]:
    """The portrait's lines: the compiled summary once there are facets."""
    path = portrait_path(config)
    try:
        return parse(fm.read(path)[1])[0] if path.is_file() else []
    except fm.Malformed:
        return []   # validate names it


def facets(config: Config) -> list[Facet]:
    """The facet notes, most central first, each with its lines."""
    out = []
    for path in sorted(folder(config).glob("*.md")) if folder(config).is_dir() else []:
        try:
            data, body = fm.read(path)
        except fm.Malformed:
            continue   # validate names it
        name = str(data.get("facet") or path.stem)
        lines = parse(body)[0]
        for line in lines:
            line.facet = name
        rank = data.get("rank")
        out.append(Facet(name, rank if isinstance(rank, int) and not isinstance(rank, bool) else 99,
                         str(data.get("sensitivity") or "open"), path.relative_to(config.vault_path).as_posix(), lines))
    return sorted(out, key=lambda facet: (facet.rank, facet.name))


def image(config: Config) -> list[Line]:
    """Everything the agent understands of the owner: every facet's lines, or the portrait's while there are none."""
    found = facets(config)
    return [line for facet in found for line in facet.lines] if found else load(config)


def digest(body: str) -> str:
    """A compiled portrait's fingerprint, blind to whitespace, so a hand edit shows and a rewrap does not."""
    return hashlib.sha256(" ".join(body.split()).encode()).hexdigest()[:12]


def heading(config: Config, section: str) -> str:
    return "How it decides" if section == "How they decide" and config.edition == "enterprise" else section


def render(line: Line, link: str) -> str:
    head = f"- {line.text} · {line.basis} · {line.confidence} · {line.evidence} · [[{link}]] ^{line.id}"
    details = (("horizon", line.horizon), ("for", line.for_), ("goal", line.goal), ("test", line.test))
    return "\n".join([head, *(f"  - {key}: {value}" for key, value in details if value)])


def compile(config: Config) -> int | None:
    """Write the portrait from the facets: per section, each facet's best lines first (line rank, then facet rank,
    then confidence), to the section and word caps, each line pointing at its facet under the facet line's own id.
    None when there are no facets. Refuses while the portrait still holds a line no facet has, so nothing is dropped
    halfway through sorting it into facets. The caller holds the vault lock."""
    found = facets(config)
    if not found:
        return None
    path = portrait_path(config)
    data, body = fm.read(path) if path.is_file() else ({"kind": "orientation", "pinned": True, "tier": "hot"}, "")
    if not data.get("compiled"):
        held = {line.id for facet in found for line in facet.lines}
        loose = [line for line in parse(body)[0] if not line.id or line.id not in held]
        if loose:
            raise ValueError(f"{len(loose)} line(s) in {config.human_portrait} are in no facet yet; move each with "
                             f"`brain portrait move ID FACET` first (one: {loose[0].text!r})")
    rank = {facet.name: facet.rank for facet in found}
    link = {facet.name: config.link(facet.rel[:-3]) for facet in found}
    order = {confidence: i for i, confidence in enumerate(CONFIDENCES)}
    out, count = ["# Portrait"], 0
    for section in SECTIONS:
        pool = sorted((line for facet in found for line in facet.lines if line.section == section and line.id),
                      key=lambda line: (line.rank, rank[line.facet], order.get(line.confidence, len(order))))
        chosen: list[str] = []
        for line in pool:
            if len(chosen) == SECTION_LINES:
                break
            block = render(line, link[line.facet])
            if len("\n".join([*out, f"## {section}", *chosen, block]).split()) <= WORDS:
                chosen.append(block)
        if chosen:
            out += ["", f"## {heading(config, section)}", *chosen]
            count += len(chosen)
    new = "\n".join(out) + "\n"
    if data.get("compiled") != digest(new) or digest(body) != digest(new):
        fm.write(path, {**data, "compiled": digest(new)}, new)
    return count


def refresh(config: Config) -> None:
    """Recompile after a facet changed, unless the portrait is still being sorted into facets. New lines get their ids
    first, so a `Not` line written by an answer reaches the portrait at once. The caller holds the vault lock."""
    if not facets(config):
        return
    try:
        stamp_notes(config)
        compile(config)
    except (ValueError, fm.Malformed):
        pass   # validate and `brain portrait compile` name it


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


def facet_hygiene(data: dict, body: str, today: date, where: str, company: bool = False) -> list[str]:
    """A facet keeps the portrait's rules, with room for depth: more words and lines, still eight a section."""
    out = hygiene(body, today, where, company, FACET_WORDS)
    lines = parse(body)[0]
    if len(lines) > FACET_LINES:
        out.append(f"{where}: {len(lines)} lines; at most {FACET_LINES}, the rest retired to History.md")
    rank = data.get("rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        out.append(f"{where}: rank {rank!r}; a whole number, 1 for the most central facet")
    sensitivity = data.get("sensitivity", "open")
    if sensitivity not in SENSITIVITIES:
        out.append(f"{where}: sensitivity {sensitivity!r}; one of {', '.join(SENSITIVITIES)}")
    elif sensitivity == "said-only":
        out += [f"{where} · {line.section} · {line.text}: a said-only facet takes only what was said, not {line.basis}"
                for line in lines if line.basis != "said"]
    return out


def notes(config: Config) -> list[Path]:
    """Every note that holds lines of its own: the facets, and the portrait until it is compiled from them."""
    paths = sorted(folder(config).glob("*.md")) if folder(config).is_dir() else []
    path = portrait_path(config)
    try:
        if path.is_file() and not fm.read(path)[0].get("compiled"):
            paths.append(path)
    except fm.Malformed:
        pass   # validate names it
    return paths


def stamp(config: Config) -> int:
    """Give each claim without one a short id, written on the line, so reception rows and explore accounts that point
    at it survive the line being reworded, re-ranked or moved to another facet. Ids are unique across the portrait and
    its facets and never reused, including those retired to History."""
    with vault_lock(config):
        count = stamp_notes(config)
        if count:
            refresh(config)
    return count


def stamp_notes(config: Config) -> int:
    """`stamp`, for a caller that holds the vault lock."""
    found = []
    for path in notes(config):
        try:
            found.append((path, *fm.read(path)))
        except fm.Malformed:
            continue   # validate names it
    history = portrait_path(config).with_name("History.md")
    taken = set(ID.findall(history.read_text(encoding="utf-8"))) if history.is_file() else set()
    for _, _, body in found:
        taken |= set(ID.findall(body))
    count = 0
    for path, data, body in found:
        section, stamped, out = None, 0, []
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
                stamped += 1
            out.append(raw)
        if stamped:
            fm.write(path, data, "".join(out))
            count += stamped
    return count


def holder(config: Config, ident: str) -> Path | None:
    """The note that holds the line with this id: its facet, or the portrait before it is compiled."""
    for path in notes(config):
        try:
            if any(line.id == ident for line in parse(fm.read(path)[1])[0]):
                return path
        except fm.Malformed:
            continue
    return None


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


def new_facet(config: Config, name: str) -> dict:
    portrait = portrait_path(config)
    audience = fm.read(portrait)[0].get("audience") if portrait.is_file() else None
    data = {"kind": "hub", "tier": "warm", "facet": name,
            "rank": max((facet.rank for facet in facets(config) if facet.rank != 99), default=0) + 1,
            "sensitivity": "said-only" if name in SAID_ONLY else "open", "subjects": [],
            "index_line": f"{name}: what the agent understands of {config.human_name} here, line by line"}
    return {**data, "audience": audience} if audience else data


def move(config: Config, ident: str, name: str) -> Line:
    """Move a line, with its details and id, into the named facet, created when missing. This is how the portrait is
    first sorted into facets, and how a line finds a better one. The caller holds the vault lock."""
    ident, name = ident.lstrip("^"), " ".join(name.split())
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"facet name {name!r}; a plain name such as Music")
    source = holder(config, ident)
    if source is None:
        raise ValueError(f"no portrait line ^{ident} in the portrait or its facets; `brain portrait stamp` gives new lines ids")
    target = folder(config) / f"{name}.md"
    # a facet named again in other letter case is the same facet, and on macOS the same file
    target = next((path for path in folder(config).glob("*.md") if path.stem.lower() == name.lower()), target) \
        if folder(config).is_dir() else target
    name = target.stem
    if source == target:
        raise ValueError(f"^{ident} is already in {name}")
    data, body = fm.read(source)
    line = next(line for line in parse(body)[0] if line.id == ident)
    raw = body.splitlines(keepends=True)
    start, end = block(raw, ident)
    moved = "".join(raw[start:end]).rstrip("\n") + "\n"
    del raw[start:end]
    tdata, tbody = fm.read(target) if target.is_file() else (new_facet(config, name), f"# {name}\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    fm.write(target, tdata, insert(tbody, heading(config, line.section), moved))   # the copy lands before the original goes
    fm.write(source, data, "".join(raw))
    refresh(config)
    line.facet = name
    return line


VERDICTS = ("right", "wrong", "not-this")


def mark(config: Config, ident: str, verdict: str, words: str, today: date) -> Line:
    """The owner's answer to one line. Right: it becomes `said`, high, dated today. Wrong: it leaves for History, and
    their words, when they give them, become a `Not` line. Not this: it leaves for History and the subject becomes a `Not`
    line, so the agent stops drawing on it. The caller holds the vault lock."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    ident = ident.lstrip("^")
    if not portrait_path(config).is_file() and not facets(config):
        raise ValueError("no portrait yet")
    path = holder(config, ident)
    if path is None:
        raise ValueError(f"no portrait line ^{ident}; a line gets its id when the night or `brain portrait stamp` runs")
    data, body = fm.read(path)
    line = next(line for line in parse(body)[0] if line.id == ident)
    raw = body.splitlines(keepends=True)
    start, end = block(raw, ident)
    words = " ".join(words.replace("·", "-").split())
    if verdict == "right":
        kept = [text for text in raw[start + 1:end] if not text.lstrip().startswith("- test:")]
        head = f"- {line.text} · said · high · {today.isoformat()} · {line.source} ^{ident}\n"
        raw[start:end] = [head, *kept]
        fm.write(path, data, "".join(raw))
        refresh(config)
        line.basis, line.confidence, line.evidence = "said", "high", today.isoformat()
        return line
    removed = "".join(raw[start:end]).rstrip("\n")
    del raw[start:end]
    history = portrait_path(config).with_name("History.md")
    hdata, hbody = fm.read(history) if history.is_file() else ({"kind": "log", "tier": "cold"}, "# History\n")
    said = f" · their words: {words}" if words else ""
    hbody = hbody.rstrip("\n") + f"\n- {today.isoformat()} · portrait line marked {verdict} by the owner{said}\n" + \
        "".join(f"  {text}\n" for text in removed.splitlines()) 
    fm.write(history, hdata, hbody)
    text = words if verdict == "wrong" else f"Not to draw on: {words or line.text}"
    body = "".join(raw)
    if text:
        link = f"[[{config.link(history.relative_to(config.vault_path).as_posix()[:-3])}]]"
        body = insert(body, "Not", f"- {text} · said · high · {today.isoformat()} · {link}\n")
    fm.write(path, data, body)
    refresh(config)
    return line


PASSES = 3   # facet passes a night; the rest wait for the next


def facet_name(hint: str) -> str:
    """The facet a hint names: an existing facet in any letter case, or the hint with its first letter raised."""
    return hint[:1].upper() + hint[1:]


def due(config: Config, home: Path | None = None) -> list[dict]:
    """The facets with evidence newer than their last judging pass, most new evidence first: the facet when it
    exists, and the area a source's rows name when none covers it yet. A said-only facet takes no evidence."""
    found = {facet.name.casefold(): facet for facet in facets(config)}
    judged = {}
    for key, facet in found.items():
        try:
            judged[key] = str(fm.read(config.vault_path / facet.rel)[0].get("judged") or "")
        except fm.Malformed:
            judged[key] = ""
    counts: dict[str, dict] = {}
    for row in evidence.read(home=home):
        for hint in row.hints:
            key = hint.casefold()
            facet = found.get(key)
            said_only = facet.sensitivity != "open" if facet else facet_name(hint) in SAID_ONLY
            if said_only or row.date.isoformat() <= judged.get(key, ""):
                continue
            entry = counts.setdefault(key, {"facet": facet.name if facet else facet_name(hint), "exists": bool(facet),
                                            "since": judged.get(key, ""), "new": 0, "newest": "", "sources": set()})
            entry["new"] += 1
            entry["newest"] = max(entry["newest"], row.date.isoformat())
            entry["sources"].add(row.source)
    out = sorted(counts.values(), key=lambda entry: (-entry["new"], entry["facet"]))
    return [entry | {"sources": sorted(entry["sources"])} for entry in out]


def judged(config: Config, name: str, through: date) -> Path:
    """Record that a facet's pass has read its evidence through this date, creating the facet when it is new.
    The caller holds the vault lock."""
    name = " ".join(name.split())
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"facet name {name!r}; a plain name such as Music")
    root, name = folder(config), facet_name(name)
    path = next((p for p in root.glob("*.md") if p.stem.lower() == name.lower()), root / f"{name}.md") \
        if root.is_dir() else root / f"{name}.md"
    data, body = fm.read(path) if path.is_file() else (new_facet(config, name), f"# {name}\n")
    root.mkdir(parents=True, exist_ok=True)
    fm.write(path, {**data, "judged": through.isoformat()}, body)
    return path
