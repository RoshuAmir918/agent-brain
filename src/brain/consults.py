"""Consult colleagues, keep their words by area and learn from corrections."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import evidence, fm, identity, needs, owners, routing, tasks
from . import items as grammar
from .config import Config
from .lock import vault_lock
from .vault import Vault

MAX_TRIES = 3   # no question visits more than three people
RECORD_SHARED_WORDS = 2   # matching prompts a reading; only the intellect judges whether the record covers a question
OUTCOMES = ("changed", "confirmed", "redirected")   # what the person's answer did to the stated direction
RUNGS = ("ask", "ask-on-big", "act-confirm", "act-report")
CLIMB_WINDOW = 10   # require a full window of consequential choices before earning another rung
CLIMB_CONFIRMED = 8   # at least eight directions must stand in that window; changes remain counterevidence
DEFAULT_CEILING = "act-confirm"   # reporting without a confirm needs a person's explicit ceiling
WHY_WORDS = 60   # a consult's why cites the evidence for choosing this colleague, so it runs longer than an owner's Why line
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
FIELDS = ("id", "to", "asked", "until", "tried", "restricted", "default", "why", "not-covered")   # a consult's indented fields
WENT_AHEAD = "Went ahead without an answer: "   # the confirm's text, followed by its default
ACTED = "Acted without asking: "   # an autonomous act's confirm, distinct from an unanswered consult


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

    def item(self, config: Config | None = None, hours_of=None) -> needs.Item:
        item = needs.Item(self.kind, self.text, "consult", self.since, self.source,
                          detail=self.why, context=self.area, default=self.default, area=self.area)
        item.owner = item.on = self.to
        item.id = self.id
        # An ask moves on at its working-hours deadline; a went-ahead confirm is accepted as an owner's confirm is.
        item.wake = self.until if self.kind == "ask" else needs.wake_for("confirm", date.fromisoformat(self.since))
        if config is not None:
            hours = (hours_of or owners.hours_lookup(config))(self.to)
            item.hours = hours.schedule()
            item.away = hours.away_on(config.local())
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
    return Consult(parts[0], fm.plain(parts[1]), parts[2], source)


def read(block: grammar.Block, kind: str = "ask") -> Consult | None:
    current = _bullet(block.line)
    if current is None:
        return None
    for name, value in block.details:
        if name not in FIELDS:
            continue
        if name == "tried":
            current.tried = tuple(person.strip() for person in value.split(",") if person.strip())
        elif name == "restricted":
            current.restricted = value == "true"
        else:
            setattr(current, name.replace("-", "_"), value)
    current.kind = kind
    if kind == "confirm":
        current.default = current.text.removeprefix(WENT_AHEAD).removeprefix(ACTED)
    return current


def parse_section(body: str, title: str = "Open") -> list[Consult]:
    kind = "confirm" if title == "Went ahead" else "ask"
    return grammar.parse(body, title, lambda block: read(block, kind))


def load(config: Config) -> list[Consult]:
    path = path_for(config)
    body = fm.read(path)[1] if path.exists() else ""
    return parse_section(body) + parse_section(body, "Went ahead")


def items(config: Config) -> list[needs.Item]:
    if config.edition != "enterprise":
        return []
    hours_of = owners.hours_lookup(config)
    out = [consult.item(config, hours_of) for consult in load(config)]
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
    span = fm.section(body, title)
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
    body, _ = grammar.without(body, title, lambda block: read(block, consult.kind), lambda entry: entry.id == consult.id)
    fm.write(path, data, body)


def working_until(asked: datetime, hours: int, schedule: owners.Hours | None = None) -> datetime:
    """Count through the recipient's openings and absences; return in the asking zone."""
    schedule = schedule or owners.Hours()
    moment = asked
    if schedule.zone:
        zone = ZoneInfo(schedule.zone)
        moment = asked.astimezone(zone) if asked.tzinfo else asked.replace(tzinfo=zone)
    start_hour, start_minute = map(int, schedule.start.split(":"))
    end_hour, end_minute = map(int, schedule.end.split(":"))
    remaining = timedelta(hours=hours)
    while True:
        opening = moment.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        closing = moment.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if moment.isoweekday() not in schedule.days or moment >= closing or schedule.away_on(moment):
            moment = opening + timedelta(days=1)
            continue
        moment = max(moment, opening)
        if remaining <= closing - moment:
            until = moment + remaining
            return until.astimezone(asked.tzinfo) if asked.tzinfo else until.replace(tzinfo=None)
        remaining -= closing - moment
        moment = closing


def _area_note(config: Config, area: str, title: str, bullet: str) -> None:
    template = f"---\nkind: hub\ntier: warm\n---\n# {area}\n\nJudgment and evidence filed under the work.\n\n"
    _append(routing.area_path(config, area), title, bullet, template)


def _autonomy_lines(body: str, link: str) -> list[dict]:
    out = []
    for line in routing.section(body, "Autonomy").splitlines():
        parts = line.removeprefix("- ").split(" · ")
        if not line.startswith("- ") or len(parts) != 4 or not parts[1] or not parts[3].startswith("by:"):
            continue
        kind, separator, rung = parts[2].partition(": ")
        if not separator or kind not in ("lower", "ceiling") or rung not in RUNGS or not tasks.PERSON.fullmatch(parts[3][3:]):
            continue
        try:
            date.fromisoformat(parts[0])
        except ValueError:
            continue
        out.append(dict(date=parts[0], kind=kind, rung=rung, link=link))
    return out


def _climb_needed(window: list[dict], rung: int, ceiling: int, restricted: bool) -> str:
    if restricted:
        return "restricted area; always ask a permitted person"
    if rung >= ceiling:
        return f"at ceiling {RUNGS[ceiling]}; a higher ceiling is needed to climb" if ceiling < len(RUNGS) - 1 else "at the highest rung"
    for following in range(max(1, CLIMB_WINDOW - len(window)), CLIMB_WINDOW + 1):
        retained = window[-(CLIMB_WINDOW - following):] if following < CLIMB_WINDOW else []
        confirmations = max(0, CLIMB_CONFIRMED - sum(row["outcome"] == "confirmed" for row in retained))
        if confirmations <= following:
            return f"{confirmations} more confirmations in the next {following} outcomes to reach {RUNGS[rung + 1]}"
    raise AssertionError("a fresh confirmed window always permits a climb")


def autonomy(config: Config, area: str | None = None) -> list[dict]:
    """Replay the intellect's calibration by area, without storing a rung or counting people."""
    require_enterprise(config)
    logs = routing.responses(config)
    bodies = routing.area_bodies(config)
    controls = {key: _autonomy_lines(body, config.agent(f"Areas/{key}.md")) for key, body in bodies.items()}
    areas = {row.area for row in owners.load(config)} | {row["area"] for row in logs} | {key for key, rows in controls.items() if rows}
    restricted_areas = owners.restricted(config)
    # An explicitly requested area has a useful initial rung even before its first event.
    selected = [routing.area_name(area)] if area is not None else sorted(areas)
    out = []
    for key in selected:
        restricted = key in restricted_areas
        rung, ceiling, window, moves = 0, RUNGS.index(DEFAULT_CEILING), [], []
        events = [dict(date=row["date"], kind=row["outcome"], id=row["id"], link=row["link"])
                  for row in logs if row["area"] == key and row["outcome"] in ("confirmed", "changed", "reversed")]
        # Logs retain file order within a date; person controls follow that day's logs, in note order.
        events += controls.get(key, [])
        events.sort(key=lambda row: row["date"])
        for event in events:
            kind, before = event["kind"], rung
            if kind in ("confirmed", "changed"):
                window = (window + [dict(date=event["date"], outcome=kind, id=event["id"], link=event["link"])])[-CLIMB_WINDOW:]
            elif kind == "reversed":
                rung, window = max(0, rung - 1), []
            elif kind == "lower":
                rung, window = min(rung, RUNGS.index(event["rung"])), []
            else:
                ceiling = RUNGS.index(event["rung"])
                rung = min(rung, ceiling)
            if kind not in ("confirmed", "changed"):
                moves.append({**event, "from": RUNGS[before], "to": RUNGS[rung], "ceiling": RUNGS[ceiling]})
            # A higher ceiling can admit a window earned at the old cap; the ceiling alone earns nothing.
            if (not restricted and rung < ceiling and len(window) == CLIMB_WINDOW
                    and sum(row["outcome"] == "confirmed" for row in window) >= CLIMB_CONFIRMED):
                before, rung = rung, rung + 1
                moves.append(dict(date=event["date"], kind="climb", **{"from": RUNGS[before], "to": RUNGS[rung]},
                                  id=window[-1]["id"], link=event["link"], window=window))
                window = []
        out.append(dict(area=key, rung=RUNGS[rung], ceiling=RUNGS[ceiling], restricted=restricted,
                        window=window, needed=_climb_needed(window, rung, ceiling, restricted), events=moves))
    return out


def _rung(rung: str) -> str:
    if rung not in RUNGS:
        raise ValueError("rung must be " + ", ".join(RUNGS))
    return rung


def lower(config: Config, area: str, by: str, rung: str | None = None) -> None:
    """Anyone can ask the intellect to consult more; lowering never grants autonomy."""
    require_enterprise(config)
    area = routing.area_name(area)
    _person(by, "--by")
    if rung is not None:
        _rung(rung)
    with vault_lock(config):
        current = autonomy(config, area)[0]["rung"]
        target = rung if rung is not None else RUNGS[max(0, RUNGS.index(current) - 1)]
        _area_note(config, area, "Autonomy", f"- {config.today()} · {routing.name(config, by)} · lower: {target} · by:{by}")
        _evidence(config.today(), "lowered", f"{routing.name(config, by)} lowered the intellect's room to act to {target}",
                  by, area, f"area:{area}")


def ceiling(config: Config, area: str, rung: str, by: str) -> None:
    """Only an area's owner, backup or an operator can change its autonomy ceiling."""
    require_enterprise(config)
    area, rung = routing.area_name(area), _rung(rung)
    _person(by, "--by")
    with vault_lock(config):
        row = next((row for row in owners.load(config) if row.area == area), None)
        aliases = routing.identities(config)
        permitted = list(config.owner_user_ids) + ([row.owner, row.backup] if row else [])
        platform, ident = by.split(":", 1)
        if (aliases.get(by, by) not in {aliases.get(person, person) for person in permitted if person}
                and not identity.listed(config.owner_user_ids, platform, (ident,))):
            raise ValueError("--by must be the area's owner or backup in Owners.md, or an operator in owner_user_ids")
        _area_note(config, area, "Autonomy", f"- {config.today()} · {routing.name(config, by)} · ceiling: {rung} · by:{by}")
        _evidence(config.today(), "capped", f"{routing.name(config, by)} set the intellect's ceiling to {rung}",
                  by, area, f"area:{area}")


def act(config: Config, area: str, for_: str, task: str = "") -> Consult:
    """Record the intellect acting on its own judgment, leaving a confirm when earned autonomy requires one."""
    require_enterprise(config)
    area, for_ = routing.area_name(area), _brief("for", for_).replace("·", "-")
    with vault_lock(config):
        derived = autonomy(config, area)[0]
        rung = derived["rung"]
        if rung not in ("act-confirm", "act-report"):
            raise ValueError(f"{area}: rung {rung}; {derived['needed']}")
        if task and not any(row.tid == task and tasks.is_open(row) for row in tasks.all_tasks(config)):
            raise ValueError(f"{task} is not an open task (unknown or closed); choose an open task")
        row = next((row for row in owners.load(config) if row.area == area), None)
        recipient = row.owner if row else next(iter(config.owner_user_ids), "")
        if rung == "act-confirm" and not recipient:
            raise ValueError("act-confirm needs an area owner in Owners.md or an operator in owner_user_ids")
        now = config.local()
        consult = Consult(str(now.date()), area, for_, config.agent("Tasks") + "#" + task if task else "",
                          to=recipient, kind="confirm", id=uuid4().hex[:12])
        _wake(config, consult, now.date(), f"Acted on its own in {area} (rung {rung}): {for_}")
        _log(config, consult, now, "acted", "-")
        if rung == "act-confirm":
            consult.text, consult.default = ACTED + for_, for_
            _append(path_for(config), "Went ahead", _render(consult), TEMPLATE)
        return consult


def _reverse(config: Config, consult: Consult, now: datetime, by: str, answer: str = "") -> None:
    """Record one reversal per act under the caller's lock, including task evidence after confirm expiry."""
    if any(row["id"] == consult.id and row["outcome"] == "reversed" for row in routing.responses(config)):
        raise ValueError(f"{consult.id}: act already reversed")
    consult.text = consult.text.removeprefix(ACTED)
    _wake(config, consult, now.date(), f"{routing.name(config, by) if by != '-' else 'Someone'} reversed the autonomous act in "
          f"{consult.area}: {consult.text}" + (f"; {answer}" if answer else ""))
    _log(config, consult, now, "reversed", by, answer=answer)
    if any(entry.id == consult.id and entry.kind == "confirm" for entry in load(config)):
        _remove(config, consult)


def reverse(config: Config, ident: str, by: str, answer: str) -> None:
    """Anyone may reject an autonomous act in their own words, even after its confirm has gone."""
    require_enterprise(config)
    _person(by, "--by")
    answer = _words("answer", answer)
    with vault_lock(config):
        found = [row for row in routing.responses(config) if row["id"] == ident and row["outcome"] == "acted"]
        if len(found) != 1:
            raise ValueError("no unique autonomous act matches " + repr(ident))
        row = found[0]
        consult = Consult(row["date"], row["area"], row["text"], row.get("source", ""), kind="confirm", id=ident)
        _reverse(config, consult, config.local(), by, answer)


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
        _evidence(today, "decided", f"{name} decided: {words}", by, area, f"decision:{ident}", id=ident, supersedes=supersedes)
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


def _available(config: Config, area: str, person: str, text: str) -> None:
    away = owners.hours_for(config, person).away_on(config.local())
    if routing.stopped(config, area, person) or away:
        next_person = next((entry.person for entry in candidates(config, text, area) if not entry.muted and not entry.away), "")
        reason = (f"{person} is away through {away}; back {date.fromisoformat(away) + timedelta(days=1)}" if away else
                  f"{person} said stop-asking for {area}")
        raise ValueError(reason + (f"; try {next_person}" if next_person else "; no available unmuted candidate on record"))


def add_evidence(config: Config, area: str, day: date | str, person: str, kind: str, text: str, link: str = "") -> bool:
    """Append one deduplicated history line under its area."""
    return bool(record_evidence(config, [routing.Row(area, str(day), person, kind, text, link)]))


def record_evidence(config: Config, rows: Iterable[routing.Row]) -> int:
    """Append new history lines in one write per area under one vault lock."""
    require_enterprise(config)
    grouped, facts = {}, []
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
        facts.append(evidence.Evidence(day, row.kind, text, f"other:{row.person}", (area,), link or f"area:{area}"))
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
        evidence.append("consults", facts)
    return added


def _delegation(config: Config, area: str, to: str, by: str, words: str, ident: str = "", question: str = "") -> None:
    bullet = f"- {config.today()} · {routing.name(config, by)} · to {to} · {words}"
    if ident:
        bullet += f"\n  - consult: {ident} · for: {question}"
    _area_note(config, area, "Delegated", bullet)
    _evidence(config.today(), "delegated", f"{routing.name(config, by)} delegated to {routing.name(config, to)}: {words}",
              by, area, f"consult:{ident}" if ident else f"area:{area}", id=ident, to=to)


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


def _evidence(day: date, verb: str, fact: str, person: str, area: str, pointer: str, **extra: str) -> None:
    """Every consult event is also a row in the shared evidence contract (docs/knowledge.md section 3)."""
    whose = f"other:{person}" if person and person != "-" else "own"
    evidence.append("consults", [evidence.Evidence(day, verb, fact, whose, (area,), pointer,
                                                   {key: value for key, value in extra.items() if value})])


FACTS = {"asked": "Asked {name}: {text}", "confirmed": "{name} confirmed: {text}", "changed": "{name} changed: {text}",
         "redirected": "{name} redirected to {to}: {text}", "unanswered": "{name} did not answer: {text}",
         "overruled": "{name} overruled the decision on: {text}", "cited": "Cited {name}'s decision for: {text}",
         "acted": "Acted on its own: {text}", "reversed": "{name} reversed the act: {text}"}


def _log(config: Config, consult: Consult, now: datetime, outcome: str, person: str,
         to: str = "", answer: str = "", said: str = "") -> None:
    """`said` reaches only the evidence row: a decision's words already live in the area note, not the Answered log."""
    asked = config.local(datetime.fromisoformat(consult.asked)) if consult.asked else now
    hours = "-" if outcome in ("overruled", "asked", "cited", "acted", "reversed") else f"{(now.timestamp() - asked.timestamp()) / 3600:.1f}h"
    text = consult.text.replace("·", "-")
    bullet = (f"- {now.date()} · consult {outcome} · {text} · by {person} · {consult.area} · {hours}"
              + (f" · to {to}" if to else "") + f" · id:{consult.id}")
    if answer:
        bullet += f"\n  - answer: {answer}"
    if outcome == "acted" and consult.source:
        bullet += f"\n  - source: [[{consult.source}]]"
    needs.answered(config, now.date(), bullet)
    fact = FACTS[outcome].format(name=routing.name(config, person) if person != "-" else "Someone",
                                 to=routing.name(config, to) if to else "", text=consult.text.removeprefix(ACTED))
    _evidence(now.date(), outcome, fact + (f"; {answer or said}" if answer or said else ""), person, consult.area, f"consult:{consult.id}",
              id=consult.id, to=to, hours="" if hours == "-" else hours)


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
                                ("why", why, WHY_WORDS)):
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
        _available(config, area, to, text)
        existing = next((entry for entry in load(config) if entry.kind == "ask" and entry.area == area
                         and fm.plain(entry.text) == fm.plain(text)), None)
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
    consult.until = working_until(now, row.wait_hours if row else owners.WAIT_HOURS,
                                  owners.hours_for(config, consult.to)).strftime("%Y-%m-%d %H:%M")


def _open(config: Config, which: str) -> Consult:
    return grammar.pick((entry for entry in load(config) if entry.kind == "ask"), which, "consult", "brain consult list --json")


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
            _available(config, consult.area, to, consult.text)
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
            _log(config, consult, now, outcome, by, said=answer)
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
                              if not entry.muted and not entry.away and entry.person not in tried
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
                if owners.hours_for(config, holder).away_on(now):
                    holder = consult.to
                actions.append(f"{consult.id}: restricted, held for {holder} without taking the default")
                if not dry_run:
                    _hold(config, consult, now, holder)
            else:
                actions.append(f"{consult.id}: went ahead on the default and left a confirm")
                if not dry_run:
                    row = routes.get(consult.area)
                    holder = row.owner if row else consult.tried[0]
                    if owners.hours_for(config, holder).away_on(now):
                        holder = consult.to
                    _go_ahead(config, consult, now, holder)
    return actions


def _hold(config: Config, consult: Consult, now: datetime, holder: str) -> None:
    """Rule 6: silence never authorizes a restricted action. The consult waits with the area's owner, no longer lapsing,
    and the task is told not to act on the default."""
    _wake(config, consult, now.date(), f"No answer from {routing.name(config, consult.to)}; restricted, so the default "
          f"is not taken: {consult.default}. Waiting for {routing.name(config, holder)}")
    _remove(config, consult)
    consult.to, consult.until = holder, ""
    _append(path_for(config), "Open", _render(consult), TEMPLATE)


def dismiss(config: Config, item: needs.Item, today: date, reason: str, by: str = "") -> None:
    """Dismiss under the caller's lock; rejecting an autonomous act corrects its area's calibration."""
    consult = next(entry for entry in load(config) if entry.id == item.id)
    if consult.kind == "confirm":
        if consult.text.startswith(ACTED) and reason == "no":
            now = config.local().replace(year=today.year, month=today.month, day=today.day)
            _reverse(config, consult, now, by if tasks.PERSON.fullmatch(by) else "-")
        else:
            _remove(config, consult)
        return
    if reason == "done":
        raise ValueError("a consult needs the person's words; answer it with brain consult answer")
    if reason == "stop-asking":
        _area_note(config, consult.area, "Not asked", f"- {today} · {consult.to} · stop-asking")
        _evidence(today, "stopped", f"{routing.name(config, consult.to)} asked not to be consulted on questions like: "
                  f"{consult.text}", consult.to, consult.area, f"consult:{consult.id}", id=consult.id)
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
        if entry["outcome"] in ("acted", "reversed"):
            continue
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
