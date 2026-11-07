"""Consult colleagues, keep their words by area and learn from corrections."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable
from uuid import uuid4

from . import fm, needs, owners, routing, seed, tasks
from .config import Config
from .lock import vault_lock
from .vault import Vault

WORK_START = 9   # local opening, Monday to Friday
WORK_END = 17    # local closing; waits count only working hours
MAX_TRIES = 3   # no question visits more than three people
RECORD_SHARED_WORDS = 2   # matching prompts a reading; only the intellect judges whether the record covers a question
OUTCOMES = ("changed", "confirmed", "redirected")   # what the person's answer did to the stated direction
TEMPLATE = """---
kind: orientation
tier: warm
pinned: false
---
# Consults

Questions for colleagues, with the default taken when nobody answers.

## Open

## Went ahead

"""
DETAIL = re.compile(r"^[ \t]+-[ \t]+(id|to|asked|until|tried|restricted|default|why|not-covered):[ \t]*(.*?)[ \t]*$")   # a consult's indented fields
WENT_AHEAD = "Went ahead without an answer: "   # the confirm's text, followed by its default


@dataclass
class Consult:
    since: str
    area: str
    text: str
    source: str = ""
    to: str = ""
    asked: str = ""
    until: str = ""
    tried: tuple[str, ...] = ()
    restricted: bool = False
    default: str = ""
    why: str = ""
    kind: str = "ask"
    id: str = ""
    not_covered: str = ""

    def item(self) -> needs.Item:
        item = needs.Item(self.kind, self.text, "consult", self.since, self.source,
                          detail=self.why, context=self.area, default=self.default, area=self.area)
        item.owner = self.to
        item.id = self.id
        return item


def require_enterprise(config: Config) -> None:
    if config.edition != "enterprise":
        raise ValueError("consults need the enterprise edition; personal vaults do not consult colleagues")


def path_for(config: Config) -> Path:
    return config.vault_path / config.agent("Consults.md")


def _bullet(line: str) -> Consult | None:
    if not line.startswith("- "):
        return None
    parts = line.rstrip().removeprefix("- ").split(" · ")
    if len(parts) not in (3, 4) or not parts[2].strip() or not owners.AREA.fullmatch(parts[1]):
        return None
    try:
        date.fromisoformat(parts[0])
    except ValueError:
        return None
    source = ""
    if len(parts) == 4:
        link = re.fullmatch(r"\[\[([^\[\]\n]+)\]\]", parts[3])
        if not link:
            return None
        source = link[1].split("|", 1)[0].strip()
    return Consult(parts[0], needs._plain(parts[1]), parts[2], source)


def parse_section(body: str, title: str = "Open") -> list[Consult]:
    span = needs._section(body, title)
    out, current = [], None
    for line in body[span[0]:span[1]].splitlines() if span else []:
        detail = DETAIL.match(line)
        if detail and current is not None:
            field, value = detail.groups()
            if field == "tried":
                current.tried = tuple(person.strip() for person in value.split(",") if person.strip())
            elif field == "restricted":
                current.restricted = value == "true"
            else:
                setattr(current, field.replace("-", "_"), value)
        elif not line[:1].isspace():
            current = _bullet(line)
            if current:
                current.kind = "confirm" if title == "Went ahead" else "ask"
                if current.kind == "confirm":
                    current.default = current.text.removeprefix(WENT_AHEAD)
                out.append(current)
    return out


def load(config: Config) -> list[Consult]:
    path = path_for(config)
    body = fm.read(path)[1] if path.exists() else ""
    return parse_section(body) + parse_section(body, "Went ahead")


def items(config: Config) -> list[needs.Item]:
    if config.edition != "enterprise":
        return []
    out = [consult.item() for consult in load(config)]
    for item in out:
        item.source = item.source or config.agent("Consults.md")
    return out


def _render(consult: Consult) -> str:
    bullet = f"- {consult.since} · {consult.area} · {consult.text}"
    if consult.source:
        bullet += f" · [[{consult.source}]]"
    bullet += f"\n  - to: {consult.to}"
    if consult.kind == "ask":
        bullet += (f"\n  - asked: {consult.asked}\n  - until: {consult.until}"
                   f"\n  - tried: {', '.join(consult.tried)}\n  - restricted: {str(consult.restricted).lower()}"
                   f"\n  - default: {consult.default}")
    if consult.why:
        bullet += f"\n  - why: {consult.why}"
    if consult.not_covered:
        bullet += f"\n  - not-covered: {consult.not_covered}"
    bullet += f"\n  - id: {consult.id}"
    return bullet


def _append(path: Path, title: str, bullet: str, template: str) -> None:
    data, body = fm.read(path) if path.exists() else fm.split(template)
    span = needs._section(body, title)
    if span is None:
        body = body.rstrip("\n") + f"\n\n## {title}\n\n{bullet}\n"
    else:
        body = body[:span[1]].rstrip("\n") + f"\n\n{bullet}\n\n" + body[span[1]:]
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, body)


def _remove(config: Config, consult: Consult) -> None:
    path = path_for(config)
    data, body = fm.read(path)
    title = "Open" if consult.kind == "ask" else "Went ahead"
    span = needs._section(body, title)
    blocks = re.split(r"(?m)(?=^- )", body[span[0]:span[1]])
    kept = "".join(block for block in blocks if not re.search(r"^  - id: " + re.escape(consult.id) + r"$", block, re.M))
    body = body[:span[0]] + kept + body[span[1]:]
    fm.write(path, data, body)


def working_until(asked: datetime, hours: int) -> datetime:
    """Count a wait through local weekday openings, carrying evenings and weekends forward."""
    moment = asked
    remaining = timedelta(hours=hours)
    while True:
        if moment.weekday() >= 5 or moment.hour >= WORK_END:
            moment = (moment + timedelta(days=1)).replace(hour=WORK_START, minute=0, second=0, microsecond=0)
            continue
        if moment.hour < WORK_START:
            moment = moment.replace(hour=WORK_START, minute=0, second=0, microsecond=0)
        closing = moment.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
        if remaining <= closing - moment:
            return moment + remaining
        remaining -= closing - moment
        moment = closing


def _area_note(config: Config, area: str, title: str, bullet: str) -> None:
    template = f"---\nkind: hub\ntier: warm\n---\n# {area}\n\nJudgment and evidence filed under the work.\n\n"
    _append(routing.area_path(config, area), title, bullet, template)


def candidates(config: Config, about: str, area: str = "", limit: int | None = None) -> list[routing.Candidate]:
    require_enterprise(config)
    return routing.candidates(config, about, area, limit)


def decisions(config: Config, about: str, area: str = "", limit: int | None = None) -> list[dict]:
    """Search relevant area decisions by shared words, then newest first, without writing."""
    require_enterprise(config)
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    areas = routing.relevance(config, about, area)
    words = routing.tokens(about)
    found = [entry for key, body in routing.area_bodies(config).items() if key in areas
             for entry in routing.decisions(body, key)]
    found.sort(key=lambda entry: (-len(words & routing.tokens(entry.text + " " + entry.question)),
                                 -date.fromisoformat(entry.day).toordinal(), entry.area, entry.ident))
    return [dict(id=entry.ident, area=entry.area, date=entry.day, name=entry.name, words=entry.text,
                 question=entry.question, source=entry.source, status=entry.status,
                 overrules=[dict(date=day, name=name, words=text) for day, name, text in entry.overrules],
                 superseded=entry.superseded, link=config.agent(f"Areas/{entry.area}.md"))
            for entry in (found[:limit] if limit is not None else found)]


def _decision(config: Config, ident: str) -> tuple[str, routing.Decision]:
    found = [(body, entry) for area, body in routing.area_bodies(config).items()
             for entry in routing.decisions(body, area) if entry.ident == ident]
    if len(found) != 1:
        raise ValueError("no unique recorded decision matches " + repr(ident))
    return found[0]


def _brief(label: str, text: str) -> str:
    text = tasks.single_line(label, text)
    if not text or len(text.split()) > needs.WHY_WORDS:
        raise ValueError(f"--{label} must be nonempty and at most {needs.WHY_WORDS} words")
    return text


def cite(config: Config, ident: str, for_: str, task: str = "") -> None:
    """Log acting on a decision that still stands and wake its named task."""
    require_enterprise(config)
    for_ = _brief("for", for_)
    with vault_lock(config):
        _, entry = _decision(config, ident)
        if entry.status != "stood":
            replacement = (f"{entry.superseded['id']} by {entry.superseded['name']}" if entry.superseded else
                           "; ".join(f"{day} by {name}: {words}" for day, name, words in entry.overrules))
            raise ValueError(f"{ident} is {entry.status}; replaced by {replacement}")
        if task and not any(row.tid == task and tasks.is_open(row) for row in tasks.all_tasks(config)):
            raise ValueError(f"{task} is not an open task (unknown or closed); choose an open task")
        answerers = {row["id"]: row["person"] for row in routing.responses(config)
                     if row["outcome"] in ("changed", "confirmed")}
        person = routing.decision_person(config, entry, answerers) or entry.name
        consult = Consult(entry.day, entry.area, for_, config.agent("Tasks") + "#" + task if task else "", id=ident)
        now = config.local()
        day = date.fromisoformat(entry.day)
        _wake(config, consult, now.date(), f"Per {entry.name}'s decision of {day:%B} {day.day}: {entry.text}")
        _log(config, consult, now, "cited", person)


def decide(config: Config, area: str, by: str, words: str, source: str, supersedes: str = "") -> str:
    """File a person's stated words and source, marking any decision they replace."""
    require_enterprise(config)
    area = routing.area_name(area)
    _person(by, "--by")
    words = _words("words", words)
    source = tasks.single_line("source", source)
    if not source or " · " in source or any(char in source for char in "[]"):
        raise ValueError("--source must name where it was said, without brackets or the field separator")
    base = source.split("#", 1)[0]
    if not re.match(r"[a-z]+://", source, re.I) and (base.endswith(".md") or Vault(config.vault_path).resolve(base)):
        source = f"[[{source}]]"
    with vault_lock(config):
        old = _decision(config, supersedes) if supersedes else None
        ident, name, today = uuid4().hex[:12], routing.name(config, by), config.today()
        if old:
            body, entry = old
            annotation = f"\n  - superseded {today} by {ident}: {name}"
            path = routing.area_path(config, entry.area)
            fm.write(path, fm.read(path)[0], body[:entry.end] + annotation + body[entry.end:])
        _area_note(config, area, "Decisions", f"- {today} · {name} · {words} · {source} · id:{ident}\n  - by: {by}")
        return ident


def _person(person: str, flag: str) -> None:
    if not tasks.PERSON.fullmatch(person):
        raise ValueError(f"{flag} must name a person as <platform>:<id>")


def _words(label: str, words: str) -> str:
    words = tasks.single_line(label, words)
    if not words:
        raise ValueError(f"give the person's own words with --{label} TEXT; never record an inference")
    return words


def _gate(config: Config, area: str, person: str, ident: str = "") -> None:
    permitted = routing.allowed(config, area, ident)
    if person not in permitted:
        names = ", ".join(sorted(permitted)) or "Owners.md names nobody for the area"
        raise ValueError(f"restricted consult in {area}: may answer: {names}")


def _unmuted(config: Config, area: str, person: str, text: str) -> None:
    if routing.stopped(config, area, person):
        next_person = next((entry.person for entry in candidates(config, text, area) if not entry.muted), "")
        raise ValueError(f"{person} said stop-asking for {area}"
                         + (f"; try {next_person}" if next_person else "; no unmuted candidate on record"))


def add_evidence(config: Config, area: str, day: date | str, person: str, kind: str, text: str, link: str = "") -> bool:
    """Append one deduplicated history line under its area."""
    return bool(record_evidence(config, [seed.Row(area, str(day), person, kind, text, link)]))


def record_evidence(config: Config, rows: Iterable[seed.Row]) -> int:
    """Append new history lines in one write per area under one vault lock."""
    require_enterprise(config)
    grouped = {}
    for row in rows:
        area = routing.area_name(row.area)
        day = date.fromisoformat(str(row.day))
        _person(row.person, "person")
        if row.kind not in routing.SEED_KINDS:
            raise ValueError("evidence kind must be approved, reviewed, changed or deferred-to")
        text = _words("text", row.text)
        link = tasks.single_line("link", row.link)
        if any(char in link for char in "[]"):
            raise ValueError("link must omit wikilink brackets")
        bullet = f"- {day} · {row.person} · {row.kind} · {text}" + (f" · [[{link}]]" if link else "")
        grouped.setdefault(area, []).append(bullet)
    if not grouped:
        return 0
    added = 0
    with vault_lock(config):
        for area, bullets in sorted(grouped.items()):
            path = routing.area_path(config, area)
            body = fm.read(path)[1] if path.exists() else ""
            existing = set(routing.section(body, "Evidence").splitlines())
            new = list(dict.fromkeys(bullet for bullet in bullets if bullet not in existing))
            if new:
                _area_note(config, area, "Evidence", "\n".join(new))
                added += len(new)
    return added


def _delegation(config: Config, area: str, to: str, by: str, words: str, ident: str = "", question: str = "") -> None:
    bullet = f"- {config.today()} · {routing.name(config, by)} · to {to} · {words}"
    if ident:
        bullet += f"\n  - consult: {ident} · for: {question}"
    _area_note(config, area, "Delegated", bullet)


def delegate(config: Config, area: str, to: str, by: str, words: str) -> None:
    """Record an owner or backup's delegation in their exact words."""
    require_enterprise(config)
    area = routing.area_name(area)
    _person(to, "--to")
    _person(by, "--by")
    words = _words("words", words)
    with vault_lock(config):
        row = next((row for row in owners.load(config) if row.area == area), None)
        aliases = routing.identities(config)
        if row is None or aliases.get(by, by) not in {aliases.get(person, person) for person in (row.owner, row.backup)}:
            raise ValueError("--by must be the area's owner or backup in Owners.md")
        _delegation(config, area, to, by, words)


def _wake(config: Config, consult: Consult, today: date, evidence: str) -> None:
    tid = needs.task_of(consult.source)
    if tid and any(task.tid == tid and tasks.is_open(task) for task in tasks.all_tasks(config)):
        tasks.set_fields(config, tid, today, values={"evidence": f"{today} — {evidence}"}, clear=["resume"])


def _log(config: Config, consult: Consult, now: datetime, outcome: str, person: str,
         to: str = "", answer: str = "") -> None:
    asked = config.local(datetime.fromisoformat(consult.asked)) if consult.asked else now
    hours = "-" if outcome in ("overruled", "asked", "cited") else f"{(now.timestamp() - asked.timestamp()) / 3600:.1f}h"
    text = consult.text.replace("·", "-")
    bullet = (f"- {now.date()} · consult {outcome} · {text} · by {person} · {consult.area} · {hours}"
              + (f" · to {to}" if to else "") + f" · id:{consult.id}")
    if answer:
        bullet += f"\n  - answer: {answer}"
    needs._answered(config, now.date(), bullet)


def _go_ahead(config: Config, consult: Consult, now: datetime, owner: str) -> Consult:
    name = routing.name(config, consult.to)
    _wake(config, consult, now.date(), f"No answer from {name}; went ahead on the default: {consult.default}")
    _remove(config, consult)
    went = Consult(now.date().isoformat(), consult.area, WENT_AHEAD + consult.default, consult.source,
                   to=owner, default=consult.default, why=consult.why, kind="confirm", id=consult.id,
                   not_covered=consult.not_covered)
    _append(path_for(config), "Went ahead", _render(went), TEMPLATE)
    _log(config, consult, now, "unanswered", consult.to)
    return went


def ask(config: Config, to: str, text: str, default: str, why: str, area: str = "", task: str = "",
        restricted: bool = False, not_covered: str | None = None) -> Consult:
    """Ask any colleague, recording the evidence for choosing them and the stated default."""
    require_enterprise(config)
    _person(to, "--to")
    text, default, why = (" ".join(value.replace("·", "-").split()) for value in (text, default, why))
    if reason := needs.vague_ask(text):
        raise ValueError(f"ask refused: {reason}")
    for label, value, limit in (("ask", text, needs.ASK_WORDS), ("default", default, needs.WHY_WORDS),
                                ("why", why, needs.WHY_WORDS)):
        if len(value.split()) > limit:
            raise ValueError(f"{label} must be at most {limit} words; say it shorter")
    if not default:
        raise ValueError("give a default: --default TEXT says what you will do without an answer")
    if not why:
        raise ValueError("give --why TEXT: why this person is likely to answer right and fast")
    if not_covered is not None:
        not_covered = _brief("not-covered", not_covered)
    with vault_lock(config):
        area = routing.area_name(area) if area else next(iter(routing.relevant(config, text)), "general")
        matches = [entry for entry in decisions(config, text, area)
                   if len(routing.tokens(text) & routing.tokens(entry["words"] + " " + entry["question"])) >= RECORD_SHARED_WORDS]
        if matches and not not_covered:
            listing = "\n".join(f"{entry['id']} · {entry['date']} · {entry['name']} · {entry['words']}"
                                + ("" if entry["status"] == "stood" else f" ({entry['status']})") for entry in matches)
            raise ValueError("read the matching decisions before asking:\n" + listing
                             + '\nCite one with `brain consult cite` if it covers the question, or pass '
                             '--not-covered "<why the record does not answer this>"')
        _unmuted(config, area, to, text)
        existing = next((entry for entry in load(config) if entry.kind == "ask" and entry.area == area
                         and needs._plain(entry.text) == needs._plain(text)), None)
        restricted = restricted or area in owners.restricted(config)
        if restricted:
            _gate(config, area, to, existing.id if existing else "")
            if existing:
                _gate(config, area, existing.to, existing.id)
        if task and not any(entry.tid == task and tasks.is_open(entry) for entry in tasks.all_tasks(config)):
            raise ValueError(f"{task} is not an open task (unknown or closed); choose an open task")
        if existing:
            if restricted and not existing.restricted:
                _remove(config, existing)
                existing.restricted = True
                _append(path_for(config), "Open", _render(existing), TEMPLATE)
            return existing
        now = config.local().replace(second=0, microsecond=0)
        consult = Consult(now.date().isoformat(), area, text,
                          config.agent("Tasks") + "#" + task if task else "", to=to, tried=(to,),
                          restricted=restricted, default=default, why=why, id=uuid4().hex[:12], not_covered=not_covered or "")
        _wait(config, consult, now)
        _append(path_for(config), "Open", _render(consult), TEMPLATE)
        _log(config, consult, now, "asked", to)
        return consult


def _wait(config: Config, consult: Consult, now: datetime) -> None:
    row = next((row for row in owners.load(config) if row.area == consult.area), None)
    consult.asked = now.strftime("%Y-%m-%d %H:%M")
    consult.until = working_until(now, row.wait_hours if row else owners.WAIT_HOURS).strftime("%Y-%m-%d %H:%M")


def _open(config: Config, which: str) -> Consult:
    entries = [entry for entry in load(config) if entry.kind == "ask"]
    chosen = [entry for entry in entries if entry.id == which]
    if not chosen:
        chosen = [entry for entry in entries if needs._plain(which) and needs._plain(which) in needs._plain(entry.text)]
    if len(chosen) != 1:
        raise ValueError(("no open consult matches " if not chosen else f"{len(chosen)} open consults match ")
                         + repr(which) + "; use an id from `brain consult list --json`")
    return chosen[0]


def answer(config: Config, which: str, outcome: str, answer: str, by: str, to: str = "") -> Consult:
    """Keep a person's decision or redirect in their exact words."""
    require_enterprise(config)
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be changed, confirmed or redirected")
    _person(by, "--by")
    answer = _words("answer", answer)
    if outcome == "redirected":
        _person(to, "--to (required for redirected)")
    elif to:
        raise ValueError("--to is only used with --outcome redirected")
    with vault_lock(config):
        consult = _open(config, which)
        now, name = config.local(), routing.name(config, by)
        if consult.restricted:
            _gate(config, consult.area, by, consult.id)
        if outcome == "redirected":
            _unmuted(config, consult.area, to, consult.text)
            aliases = routing.identities(config)
            if aliases.get(to, to) in {aliases.get(person, person) for person in consult.tried}:
                raise ValueError(f"{to} already received this consult; choose someone not in tried")
            if len(consult.tried) >= MAX_TRIES:
                raise ValueError(f"consult already tried {MAX_TRIES} people; use brain consult lapse")
            if consult.restricted and to not in routing.allowed(config, consult.area, consult.id):
                _delegation(config, consult.area, to, by, answer, consult.id, consult.text)
            _wake(config, consult, now.date(), f"{name} redirected the consult to {to}: {answer}")
            _log(config, consult, now, outcome, by, to, answer)
            _remove(config, consult)
            consult.to, consult.tried = to, (*consult.tried, to)
            _wait(config, consult, now)
            _append(path_for(config), "Open", _render(consult), TEMPLATE)
        else:
            _wake(config, consult, now.date(), f"{name} answered the consult: {answer}")
            bullet = f"- {now.date()} · {name} · {answer}"
            if consult.source:
                bullet += f" · [[{consult.source}]]"
            _area_note(config, consult.area, "Decisions", bullet + f" · id:{consult.id}\n  - asked: {consult.text}")
            _log(config, consult, now, outcome, by)
            _remove(config, consult)
        return consult


def overrule(config: Config, ident: str, answer: str, by: str) -> None:
    """Place anyone's overrule beside the decision it corrects."""
    require_enterprise(config)
    _person(by, "--by")
    answer = _words("answer", answer)
    with vault_lock(config):
        found = [(area, body, entry) for area, body in routing.area_bodies(config).items()
                 for entry in routing.decisions(body, area) if entry.ident == ident]
        if len(found) != 1:
            raise ValueError("no unique recorded decision matches " + repr(ident))
        area, body, entry = found[0]
        now, name = config.local(), routing.name(config, by)
        annotation = f"\n  - overruled {now.date()} by {name}: {answer}"
        path = routing.area_path(config, area)
        fm.write(path, fm.read(path)[0], body[:entry.end] + annotation + body[entry.end:])
        consult = Consult(entry.day, area, entry.question, entry.source, id=ident)
        _log(config, consult, now, "overruled", by, answer=answer)
        # Rule 6: anyone may overrule, but in a restricted area only a permitted person's word lets the work act on it.
        permitted = routing.allowed(config, area, ident) if area in owners.restricted(config) else {by}
        _wake(config, consult, now.date(), f"{name} overruled the consult: {answer}" + (
            "" if by in permitted else f"; restricted area, so act on it only once one of "
            f"{', '.join(sorted(routing.name(config, person) for person in permitted)) or 'its owners'} agrees"))


def lapse(config: Config, *, dry_run: bool = False) -> list[str]:
    """Try the next eligible colleague, then leave a confirm on the stated default."""
    require_enterprise(config)
    actions = []
    with vault_lock(config):
        now = config.local()
        routes = {row.area: row for row in owners.load(config)}
        for consult in load(config):
            if consult.kind != "ask" or not consult.until or config.local(datetime.fromisoformat(consult.until)) > now:
                continue
            permitted = routing.allowed(config, consult.area, consult.id) if consult.restricted else None
            aliases = routing.identities(config)
            tried = {aliases.get(person, person) for person in consult.tried}
            recipient = next((entry.person for entry in candidates(config, consult.text, consult.area)
                              if not entry.muted and entry.person not in tried
                              and (permitted is None or entry.person in permitted)), "") if len(consult.tried) < MAX_TRIES else ""
            if recipient:
                actions.append(f"{consult.id}: reassigned to {recipient}")
                if not dry_run:
                    _remove(config, consult)
                    consult.to, consult.tried = recipient, (*consult.tried, recipient)
                    _wait(config, consult, now)
                    _append(path_for(config), "Open", _render(consult), TEMPLATE)
            elif consult.restricted:
                row = routes.get(consult.area)
                holder = row.owner if row else consult.tried[0]
                actions.append(f"{consult.id}: restricted, held for {holder} without taking the default")
                if not dry_run:
                    _hold(config, consult, now, holder)
            else:
                actions.append(f"{consult.id}: went ahead on the default and left a confirm")
                if not dry_run:
                    row = routes.get(consult.area)
                    _go_ahead(config, consult, now, row.owner if row else consult.tried[0])
    return actions


def _hold(config: Config, consult: Consult, now: datetime, holder: str) -> None:
    """Rule 6: silence never authorizes a restricted action. The consult waits with the area's owner, no longer lapsing,
    and the task is told not to act on the default."""
    _wake(config, consult, now.date(), f"No answer from {routing.name(config, consult.to)}; restricted, so the default "
          f"is not taken: {consult.default}. Waiting for {routing.name(config, holder)}")
    _remove(config, consult)
    consult.to, consult.until = holder, ""
    _append(path_for(config), "Open", _render(consult), TEMPLATE)


def dismiss(config: Config, item: needs.Item, today: date, reason: str) -> None:
    """Dismiss a consult under the caller's vault lock; only stop-asking changes future routing."""
    consult = next(entry for entry in load(config) if entry.id == item.id)
    if consult.kind == "confirm":
        _remove(config, consult)
        return
    if reason == "done":
        raise ValueError("a consult needs the person's words; answer it with brain consult answer")
    if reason == "stop-asking":
        _area_note(config, consult.area, "Not asked", f"- {today} · {consult.to} · stop-asking")
    now = config.local()
    if consult.restricted:   # rule 6: a dismissal is not a person's word for a restricted action
        _wake(config, consult, today, f"{routing.name(config, consult.to)} closed the consult; restricted, so the "
              f"default is not taken: {consult.default}")
        _remove(config, consult)
        _log(config, consult, now, "unanswered", consult.to)
        return
    row = next((row for row in owners.load(config) if row.area == consult.area), None)
    _go_ahead(config, consult, now, row.owner if row else consult.tried[0])


def expire_confirms(config: Config, today: date) -> list[str]:
    """Expire Went ahead entries after the same acceptance window as other confirms; caller holds the lock."""
    removed = []
    for consult in load(config):
        if consult.kind == "confirm" and (today - date.fromisoformat(consult.since)).days >= needs.CONFIRM_DAYS:
            _remove(config, consult)
            removed.append(consult.text)
    return removed


def stats(config: Config) -> dict:
    """Count recipient outcomes, weekly routing corrections and the record's share."""
    require_enterprise(config)
    groups = {"areas": {}, "people": {}}
    weeks = {}
    record_weeks = {}
    responses = routing.responses(config)
    redirected = {entry["id"] for entry in responses if entry["outcome"] == "redirected"}
    for entry in responses:
        if entry["outcome"] in ("asked", "cited"):
            year, week, _ = date.fromisoformat(entry["date"]).isocalendar()
            record = record_weeks.setdefault(f"{year}-W{week:02}", dict(asked=0, cited=0))
            record[entry["outcome"]] += 1
            continue
        for dimension, key in (("areas", entry["area"]), ("people", entry["person"])):
            groups[dimension].setdefault(key, []).append(entry)
        if entry["outcome"] in ("changed", "confirmed", "unanswered"):
            year, week, _ = date.fromisoformat(entry["date"]).isocalendar()
            series = weeks.setdefault(f"{year}-W{week:02}", {"resolved": set(), "redirected": set()})
            series["resolved"].add(entry["id"])
            if entry["id"] in redirected:
                series["redirected"].add(entry["id"])
    for dimension in groups.values():
        for key, rows in dimension.items():
            counts = {outcome: sum(row["outcome"] == outcome for row in rows)
                      for outcome in (*OUTCOMES, "overruled", "unanswered")}
            hours = [row["hours"] for row in rows if row["outcome"] in OUTCOMES and row["hours"] is not None]
            decisions = counts["changed"] + counts["confirmed"]
            dimension[key] = {"asked": len(rows) - counts["overruled"], **counts,
                              "changed_share": counts["changed"] / decisions if decisions else None,
                              "median_hours": median(hours) if hours else None}
    groups["weekly"] = [dict(week=week, resolved=len(rows["resolved"]), redirected=len(rows["redirected"]),
                             rate=len(rows["redirected"]) / len(rows["resolved"])) for week, rows in sorted(weeks.items())]
    groups["record_weekly"] = [dict(week=week, **counts, record_share=counts["cited"] / (counts["cited"] + counts["asked"]))
                               for week, counts in sorted(record_weeks.items())]
    return groups
