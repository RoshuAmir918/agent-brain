"""The image (docs/image.md): the owner understood in depth, one facet note per area of their life or of the
organisation, each in the portrait's line grammar, with the portrait compiled from them. Everything that spans notes
lives here: ids unique across the folder, the owner's answers landing where a line lives, lines moving between facets,
and which evidence each facet's judging pass reads."""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import evidence, fm
from .config import Config
from .lock import vault_lock
from .portrait import (CONFIDENCES, HEADINGS, ID, LINE, SECTION_LINES, SECTIONS, VERDICTS, WORDS, Line, block,
                       heading, hygiene, insert, load, parse, portrait_path)

FACET_WORDS = 1500

FACET_LINES = 40

SENSITIVITIES = ("open", "said-only")

SAID_ONLY = ("Health", "Money")   # starting facets that take only what the owner said


@dataclass
class Facet:
    """One area of the owner's life or of the organisation, one note in the Portrait folder: the image, in depth."""
    name: str
    rank: int
    sensitivity: str
    rel: str
    lines: list[Line] = field(default_factory=list)
    hints: tuple[str, ...] = ()   # what it claims beyond its name: rows naming these come here before a broader facet
    judged: str = ""   # the newest evidence its last judging pass read


def folder(config: Config) -> Path:
    """The facets live beside the portrait, in a folder of its name."""
    return portrait_path(config).with_suffix("")


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
        rank, hints = data.get("rank"), data.get("hints")
        out.append(Facet(name, rank if isinstance(rank, int) and not isinstance(rank, bool) else 99,
                         str(data.get("sensitivity") or "open"), path.relative_to(config.vault_path).as_posix(), lines,
                         tuple(str(hint).casefold() for hint in hints) if isinstance(hints, list) else (),
                         str(data.get("judged") or "")))
    return sorted(out, key=lambda facet: (facet.rank, facet.name))


def lines(config: Config) -> list[Line]:
    """Everything the agent understands of the owner: every facet's lines, or the portrait's while there are none."""
    found = facets(config)
    return [line for facet in found for line in facet.lines] if found else load(config)


def digest(body: str) -> str:
    """A compiled portrait's fingerprint, blind to whitespace, so a hand edit shows and a rewrap does not."""
    return hashlib.sha256(" ".join(body.split()).encode()).hexdigest()[:12]


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


def owner_lines(config: Config) -> list[dict]:
    """The image for its owner's eyes: every facet's lines, most central facet first, each saying whether it is in the
    compiled portrait (with its rank there) and its rank within its facet. Without facets, the portrait's own lines."""
    compiled = {line.id: line for line in load(config) if line.id}
    found = facets(config)
    if not found:
        return [line.public() | {"inPortrait": True} for line in load(config)]
    out = []
    for facet in found:
        for line in facet.lines:
            entry = line.public() | {"inPortrait": line.id in compiled, "facetRank": line.rank}
            if line.id in compiled:
                entry["rank"] = compiled[line.id].rank
            out.append(entry)
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


def named(hint: str) -> str:
    """The facet a hint names when no facet has it yet: the hint with its first letter raised."""
    return hint[:1].upper() + hint[1:]


def route(rows: list[evidence.Evidence], found: list[Facet]) -> dict[str, list[evidence.Evidence]]:
    """Which facet each row goes to. A facet that claims one of the row's hints, or a word in its fact, by its own
    `hints` takes it, and a broader facet does not, so a Climbing facet takes the climbing rows Play would otherwise
    hold. Otherwise each hint goes to the facet of that name, existing or still to be made. A said-only facet, or a
    hint naming one, takes nothing from evidence."""
    by_name = {facet.name.casefold(): facet for facet in found}
    claims = [(facet, [re.compile(rf"\b{re.escape(hint)}", re.I) for hint in facet.hints])
              for facet in found if facet.hints and facet.sensitivity == "open"]
    out: dict[str, list[evidence.Evidence]] = {}
    for row in rows:
        hints = {hint.casefold() for hint in row.hints}
        specific = [facet.name for facet, words in claims
                    if hints & set(facet.hints) or any(word.search(row.fact) for word in words)]
        general = []
        for hint in sorted(hints):
            facet = by_name.get(hint)
            if facet:
                general += [facet.name] if facet.sensitivity == "open" else []
            elif named(hint) not in SAID_ONLY:
                general.append(named(hint))
        for name in dict.fromkeys(specific or general):
            out.setdefault(name, []).append(row)
    return out


def evidence_for(config: Config, name: str, since: str | None = None, home: Path | None = None) -> list[evidence.Evidence]:
    """The rows a facet's judging pass reads: those routed to it, newer than its last pass unless `since` says
    otherwise ("" for all of them), newest first."""
    found = facets(config)
    facet = next((facet for facet in found if facet.name.casefold() == name.casefold()), None)
    floor = facet.judged if facet and since is None else since or ""
    key = facet.name if facet else named(name)
    return [row for row in route(evidence.read(home=home), found).get(key, []) if row.date.isoformat() > floor]


def due(config: Config, home: Path | None = None) -> list[dict]:
    """The facets with evidence newer than their last judging pass, most new evidence first: the facet when it exists,
    and the area a source's rows name when none covers it yet."""
    found = facets(config)
    judged = {facet.name: facet.judged for facet in found}
    out = []
    for name, rows in route(evidence.read(home=home), found).items():
        new = [row for row in rows if row.date.isoformat() > judged.get(name, "")]
        if new:
            out.append({"facet": name, "exists": name in judged, "since": judged.get(name, ""), "new": len(new),
                        "newest": max(row.date for row in new).isoformat(), "sources": sorted({row.source for row in new})})
    return sorted(out, key=lambda entry: (-entry["new"], entry["facet"]))


def judged(config: Config, name: str, through: date) -> Path:
    """Record that a facet's pass has read its evidence through this date, creating the facet when it is new.
    The caller holds the vault lock."""
    name = " ".join(name.split())
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"facet name {name!r}; a plain name such as Music")
    root, name = folder(config), named(name)
    path = next((p for p in root.glob("*.md") if p.stem.lower() == name.lower()), root / f"{name}.md") \
        if root.is_dir() else root / f"{name}.md"
    data, body = fm.read(path) if path.is_file() else (new_facet(config, name), f"# {name}\n")
    root.mkdir(parents=True, exist_ok=True)
    fm.write(path, {**data, "judged": through.isoformat()}, body)
    return path

