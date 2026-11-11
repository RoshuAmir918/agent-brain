"""Corrections on a role's own work (docs/roles.md section 7, the strongest evidence): each event the template declares
(a review comment, changes requested, what changed before merge, closed unmerged, a revert) is recorded once with its
source and weight, then filed into the role's notes as a sourced candidate line or as one more source on a line that
says it already. Consolidation is kept simple and done here, not by the model: a line backed by a second piece of work,
or stated as a rule by someone with authority, stops being a candidate. The model only judges what an event teaches.

The role's own work, its events and what was filed live beside the screen's labels in `$AGENT_HOME/roles/<role>/`."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from . import fm, portrait, roles, screen
from .config import Config
from .lock import evidence_lock, vault_lock
from .sources import Exchange

WORK_SOURCE = re.compile(r"\bon ([\w./-]*#\d+)\b")   # the piece of work a source names: `review on acme-app#812, jo, …`
EVENT_WORDS = {"review.changes_requested": "changes requested", "review.comment": "review",
               "merge.diff_from_opened": "changed before merge", "pr.closed_unmerged": "closed unmerged",
               "revert": "revert"}


@dataclass
class Work:
    id: str
    title: str
    link: str
    opened: str
    process: dict = field(default_factory=dict)   # which steps of the skill were on, so the account can compare


@dataclass
class Event:
    id: str
    work: str
    event: str
    weight: float
    by: str
    speaker: str
    at: str
    text: str
    where: str = ""
    link: str = ""


def _folder(config: Config, role: str):
    return screen.folder(config, role)


def works(config: Config, role: str) -> dict[str, Work]:
    return {row["id"]: Work(**row) for row in screen._rows(_folder(config, role) / "work.jsonl")}


def add_work(config: Config, role: str, ident: str, title: str = "", link: str = "", opened: str = "") -> bool:
    """Register a piece of the role's own work, so corrections on it can be told from the team's on each other's."""
    definition = roles.load(config, role)
    if not ident.strip():
        raise ValueError("a piece of work needs an id, such as reviews:acme-app#40")
    with evidence_lock(_folder(config, role)):
        if ident in works(config, role):
            return False
        screen._append(_folder(config, role) / "work.jsonl",
                       [asdict(Work(ident, title, link, opened or config.now().isoformat(timespec="seconds"), dict(definition.process)))])
    return True


def events(config: Config, role: str) -> dict[str, Event]:
    return {row["id"]: Event(**row) for row in screen._rows(_folder(config, role) / "corrections.jsonl")}


def filed(config: Config, role: str) -> dict[str, dict]:
    return {row["id"]: row for row in screen._rows(_folder(config, role) / "filed.jsonl")}


def _platform(role: roles.Role) -> str:
    from .sources import registry
    for source in role.sources.get("corrections", []):
        entry = registry().get(source)
        if entry and entry.source.needs:
            return entry.source.needs
    return "github"


def add_event(config: Config, role: str, work: str, event: str, by: str, at: str, text: str, *, source_id: str = "",
              where: str = "", link: str = "") -> Event | None:
    """Record one correction on the role's own work. The same source item recorded twice is kept once."""
    definition = roles.load(config, role)
    weights = {entry["event"]: entry["weight"] for entry in definition.corrections}
    if event not in weights:
        raise ValueError(f"{event!r} is not a correction this role's template counts; it counts {', '.join(weights)}")
    if work not in works(config, role):
        raise ValueError(f"{work!r} is not the role's own work; register it with `brain roles work {role} add`")
    date.fromisoformat(at)
    # Scoped to the work: an id on the source (a comment's number) is unique only within it.
    key = f"{work}|{source_id}" if source_id else f"{work}|{event}|{by}|{at}|{where}|{text}"
    ident = hashlib.sha256(key.encode()).hexdigest()[:12]
    speaker = screen.speakers(config)(_platform(definition), by, text, Exchange(work, "", "", "", ()))
    with evidence_lock(_folder(config, role)):
        if ident in events(config, role):
            return None
        row = Event(ident, work, event, float(weights[event]), by, speaker, at, text, where, link)
        screen._append(_folder(config, role) / "corrections.jsonl", [asdict(row)])
    return row


def pending(config: Config, role: str) -> list[Event]:
    """Events not yet filed, heaviest first, then oldest."""
    done = filed(config, role)
    return sorted((event for event in events(config, role).values() if event.id not in done),
                  key=lambda event: (-event.weight, event.at, event.id))


def source_text(event: Event, work: Work | None) -> str:
    """`review on acme-app#40, kim, 2026-11-04`: the work named whole enough that two repositories' #40 differ."""
    name = event.work.split(":", 1)[-1]
    return f"{EVENT_WORDS.get(event.event, event.event)} on {name}, {event.by}, {event.at}"


def works_in(sources: str) -> set[str]:
    """The distinct pieces of work a line's sources name. A source naming none (a person's word, a doc) backs the line
    but isn't another piece of work."""
    return {match[1] for part in sources.split(";") if (match := WORK_SOURCE.search(part))}


def authority(config: Config, event: Event) -> bool:
    """Whether the speaker may state a rule for the team: an area's owner or backup, or the organisation's owner."""
    if event.speaker in screen.MACHINES:
        return False   # an agent posting under an owner's account speaks for the agent, not the owner
    if event.speaker.startswith(("owner of", "backup owner of")):
        return True
    from . import routing
    aliases = routing.identities(config)
    person = aliases.get(f"github:{event.by}", f"github:{event.by}")
    return person in {aliases.get(owner, owner) for owner in config.owner_user_ids}


MISSES = ("knowledge", "retrieval", "execution", "decision", "new taste")   # docs/roles.md section 7, Diagnosing every miss


def _record(config: Config, role: str, event: Event, how: str, line: str, miss: str = "") -> None:
    with evidence_lock(_folder(config, role)):
        if event.id not in filed(config, role):
            screen._append(_folder(config, role) / "filed.jsonl", [{"id": event.id, "how": how, "line": line,
                           "at": config.today().isoformat(), **({"miss": miss} if miss else {})}])


def _find(config: Config, role: str, ident: str):
    for shape in roles.load(config, role).shapes():
        path = roles.folder(config, role) / f"{shape}.md"
        if path.is_file() and re.search(rf" \^{re.escape(ident)}[ \t]*$", path.read_text(encoding="utf-8"), re.M):
            return path
    return None


def file(config: Config, role: str, event_id: str, *, note: str = "", section: str = "", claim: str = "",
         to: str = "", rule: bool = False, decides: str = "", person: str = "", always: bool = False, miss: str = "") -> str:
    """File an event: a new line (`note`, `section`, `claim`), or one more source on the line `to` names. Refused when a
    person struck the claim; a person's own line is never edited, the event only confirms it. Returns what was done."""
    definition = roles.load(config, role)
    event = events(config, role).get(event_id)
    if event is None:
        raise ValueError(f"no correction {event_id!r}; `brain roles correction {role} next` lists them")
    if miss and miss not in MISSES:
        raise ValueError(f"a miss is one of {', '.join(MISSES)}")
    if decides:
        return _decision_right(config, role, event, decides, person, always, miss)
    if rule and not authority(config, event):
        raise ValueError(f"{event.by} ({event.speaker}) can't make it a rule on their word alone; file it as a candidate")
    source = source_text(event, works(config, role).get(event.work)) + (", stated as a rule" if rule else "")
    # Checked, written and recorded under one lock, against the notes as they are now (a pull may have just landed a
    # person's edit). A note that already carries this event's source was written by a run that stopped before
    # recording it: it is recorded, not written twice.
    with vault_lock(config):
        if event_id in filed(config, role):
            return "already filed"
        index = roles.rebuild(config, role)
        written = next((line for path in roles.folder(config, role).glob("*.md")
                        for line in path.read_text(encoding="utf-8").splitlines() if source in line), None)
        if written is not None:   # the lesson it landed on, so the record counts it with the others on that lesson
            _record(config, role, event, "merged" if to else "new", to or written.strip(), miss)
            return "already in the notes; recorded"
        if to:
            lesson = next((item for item in index.lessons if item.id == to), None)
            path = _find(config, role, to)
            if lesson is None or path is None:
                raise ValueError(f"no live line ^{to} in {role}'s notes (a struck line takes no evidence)")
            if lesson.human:
                _record(config, role, event, f"confirms ^{to}", to, miss)
                return f"confirms ^{to}, a person's line, left as they wrote it"
            data, body = fm.read(path)
            lines = body.splitlines(keepends=True)
            at = next(i for i, raw in enumerate(lines) if raw.rstrip().endswith(f"^{to}"))
            merged = _with_source(lines[at], source, rule)
            fm.write(path, data, "".join(lines[:at] + [merged] + lines[at + 1:]))
            _record(config, role, event, "merged", to, miss)
            promoted = "*(candidate)*" in lines[at] and "*(candidate)*" not in merged
            return f"added to ^{to}" + (", now active" if promoted else "")
        if note not in definition.shapes():
            raise ValueError(f"note must be one of the role's shapes: {', '.join(definition.shapes())}")
        if not section.strip() or section.strip() in (roles.STRUCK, portrait.SKETCH) or not claim.strip():
            raise ValueError("a new line needs a section (not Struck) and a claim")
        claim = " ".join(claim.replace("·", "-").split()).rstrip(".")
        if roles.STATUS.search(claim) or re.search(r"\^[0-9a-f]{4}\b", claim):
            raise ValueError("a claim carries no status or id; the engine writes those (use --rule for a rule)")
        if reason := roles.refusal(index, claim, event.at):
            raise ValueError(f"refused: {reason}")
        taken = roles._ids_in_history(config, role) | {item.id for item in index.lessons} | {item.id for item in index.struck}
        for other in roles.folder(config, role).glob("*.md"):
            taken.update(re.findall(r" \^([0-9a-f]{4})[ \t]*$", other.read_text(encoding="utf-8"), re.M))
        ident = roles.line_id(claim, taken)   # given now, so another correction tonight can be filed to it
        line = f"- {claim} ({source})" + ("" if rule else " *(candidate)*") + f" ^{ident}"
        path = roles.folder(config, role) / f"{note}.md"
        data, body = fm.read(path) if path.is_file() else ({"tier": "warm"}, f"# {note}\n")
        fm.write(path, data, portrait.insert(body, section.strip(), line + "\n"))
        _record(config, role, event, "new", line, miss)
        kind = "rule" if rule else "candidate"
        return f"new {kind} ^{ident} in {note} · {section.strip()}"


def _decision_right(config: Config, role: str, event: Event, area: str, person: str, always: bool, miss: str = "") -> str:
    """"Check with Kim before changing the schema": a fact about who decides, so it goes to the people's records every
    role and intellect reads (`Owners.md`), not to this role's notes. Stated by someone with authority, or by the person
    about themselves; never over a row the table already has."""
    from . import owners, routing
    if not person:
        raise ValueError("--decides needs --person <platform>:<id>: who decides it")
    aliases = routing.identities(config)
    canonical = lambda who: aliases.get(who, who)
    platform = _platform(roles.load(config, role))
    self_named = event.speaker not in screen.MACHINES and canonical(f"{platform}:{event.by}") == canonical(person)
    if not (authority(config, event) or self_named):
        raise ValueError(f"{event.by} ({event.speaker}) can't assign who decides {area}; only an owner or the person "
                         "themselves can. File it as a candidate line instead")
    source = source_text(event, works(config, role).get(event.work))
    with vault_lock(config):
        if event.id in filed(config, role):
            return "already filed"
        area = " ".join(area.casefold().split())
        row = next((row for row in owners.load(config) if row.area == area), None)
        if row and canonical(row.owner) != canonical(person):
            raise ValueError(f"Owners.md already names {row.owner_name} for {area}; a person's table outranks what was "
                             "learned: ask them to change it")
        if not row:
            owners.add_area(config, area, person, routing.name(config, person) or person, event.text[:120], source)
        if always:
            owners.restrict(config, area)
        _record(config, role, event, "decision right", f"{area} -> {person}" + (" (always ask)" if always else ""), miss)
    return f"{area}: {routing.name(config, person) or person} decides" + (", always asked" if always else "")


def _with_source(raw: str, source: str, rule: bool) -> str:
    """A line with one more source in its parentheses; no longer a candidate once its sources span two pieces of work
    or someone with authority stated it as a rule."""
    newline = raw[len(raw.rstrip("\r\n")):]
    text = raw.rstrip("\r\n")
    tail = re.search(r" \^[0-9a-f]{4}[ \t]*$", text)
    ident = tail[0] if tail else ""
    text = text[:tail.start()] if tail else text
    marker = roles.STATUS.search(text)
    candidate = bool(marker) and marker[1] == "candidate"
    kept = marker[0] if marker and not candidate else ""   # a rule's marker, with its scope, stays as written
    if marker:
        text = (text[:marker.start()] + text[marker.end():]).rstrip()
    found = roles.SOURCES.search(text)
    if found:
        sources = f"{found[1]}; {source}"
        text = text[:found.start()] + f"({sources})"
    else:
        sources = source
        text = f"{text} ({source})"
    if kept:
        text += f" {kept}"
    elif candidate and not rule and len(works_in(sources)) < 2:
        text += " *(candidate)*"
    return text + ident + newline


def dismiss(config: Config, role: str, event_id: str, why: str) -> None:
    """An event that teaches nothing beyond its own work (a typo, a one-off question), kept out of the notes with why."""
    event = events(config, role).get(event_id)
    if event is None:
        raise ValueError(f"no correction {event_id!r}")
    if not why.strip():
        raise ValueError("say why it teaches nothing beyond its own work")
    _record(config, role, event, "dismissed", " ".join(why.split()))
