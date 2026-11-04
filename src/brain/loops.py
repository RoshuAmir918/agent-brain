"""What the agent is holding: promises, owed answers and waits on other people."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fm, needs
from .config import Config
from .lock import vault_lock
from .needs import _plain

KINDS = ("promised", "owed", "waiting")
OUTCOMES = ("done", "dropped")
STALE_HOURS = 4
WAITING_HOURS = 24
CLOSED_KEEP = 50
TEMPLATE = """---
kind: orientation
tier: warm
pinned: false
---
# Loops

What I am holding for you: promises, answers owed and waits on others.

## Open

## Closed

"""
DETAIL = re.compile(r"^[ \t]+-[ \t]+(with|due|closed):[ \t]*(.+?)[ \t]*$")
STAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"


@dataclass
class Item:
    kind: str
    text: str
    since: str
    source: str
    with_: str = ""
    due: str = ""
    closed: str = ""
    outcome: str = ""
    because: str = ""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        key = json.dumps([self.kind, _plain(self.text)], ensure_ascii=False)
        self.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _clean(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def _time(value: str) -> datetime:
    if not re.fullmatch(STAMP + r"|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD or YYYY-MM-DD HH:MM")
    return datetime.fromisoformat(value)


def _local(config: Config, at: datetime) -> datetime:
    return at.replace(tzinfo=config.zone) if at.tzinfo is None else at.astimezone(config.zone)


def path_for(config: Config) -> Path:
    return config.vault_path / config.agent("Loops.md")


def _bullet(line: str) -> Item | None:
    match = re.fullmatch(r"- (" + STAMP + r") · (promised|owed|waiting) · (.+?) · \[\[([^\[\]\n]+)\]\]",
                         line.strip())
    if not match or len(line.split(" · ")) != 4 or not match[3].strip():
        return None
    try:
        _time(match[1])
    except ValueError:
        return None
    source = match[4].split("|", 1)[0].strip()
    return Item(match[2], match[3].strip(), match[1], source) if source else None


def parse_section(body: str, title: str = "Open") -> list[Item]:
    span = needs._section(body, title)
    out = []
    current = None
    for line in body[span[0]:span[1]].splitlines() if span else []:
        detail = DETAIL.match(line)
        if detail and current is not None:
            if detail[1] == "with":
                current.with_ = detail[2]
            elif detail[1] == "due":
                try:
                    _time(detail[2])
                except ValueError:
                    continue
                current.due = detail[2]
            else:
                closed = re.fullmatch(r"(" + STAMP + r") · (done|dropped) · (.+)", detail[2])
                if closed:
                    try:
                        _time(closed[1])
                    except ValueError:
                        continue
                    current.closed, current.outcome, current.because = closed.groups()
        elif not line[:1].isspace():
            current = _bullet(line)
            if current:
                out.append(current)
    return out


def render(item: Item) -> str:
    bullet = f"- {item.since} · {item.kind} · {item.text} · [[{item.source}]]"
    for label, value in (("with", item.with_), ("due", item.due)):
        if value:
            bullet += f"\n  - {label}: {value}"
    if item.closed:
        bullet += f"\n  - closed: {item.closed} · {item.outcome} · {item.because}"
    return bullet


def as_dict(item: Item) -> dict:
    out = {"id": item.id, "kind": item.kind, "text": item.text, "with": item.with_, "due": item.due,
           "since": item.since, "source": item.source}
    if item.closed:
        out.update(closed=item.closed, outcome=item.outcome, because=item.because)
    return out


def open_loops(config: Config) -> list[Item]:
    path = path_for(config)
    return parse_section(fm.read(path)[1]) if path.exists() else []


def closed_recent(config: Config) -> list[Item]:
    path = path_for(config)
    items = parse_section(fm.read(path)[1], "Closed") if path.exists() else []
    return sorted(reversed(items), key=lambda item: item.closed, reverse=True)


def _insert(body: str, title: str, text: str) -> str:
    span = needs._section(body, title)
    if span is None:
        return body.rstrip("\n") + f"\n\n## {title}\n\n{text}\n"
    return body[:span[1]].rstrip("\n") + "\n\n" + text + "\n\n" + body[span[1]:]


def add(config: Config, at: datetime, kind: str, text: str, source: str, with_: str = "", due: str = "") -> bool:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    text, source = _clean(text), source.strip()
    if not text:
        raise ValueError("loop text must not be empty")
    if not source or any(char in source for char in "[]\r\n"):
        raise ValueError("source must name a note, without wikilink brackets or newlines")
    due = " ".join(due.split())
    if due:
        _time(due)
    with vault_lock(config):
        path = path_for(config)
        data, body = fm.read(path) if path.exists() else fm.split(TEMPLATE)
        if any(_plain(item.text) == _plain(text) for item in parse_section(body)):
            return False
        item = Item(kind, text, _local(config, at).strftime("%Y-%m-%d %H:%M"), source, _clean(with_), due)
        body = _insert(body, "Open", render(item))
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, data, body)
    return True


def close(config: Config, which: str, at: datetime, outcome: str, because: str) -> Item:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    because = _clean(because)
    if not because:
        raise ValueError("closing a loop needs a reason")
    with vault_lock(config):
        candidates = open_loops(config)
        chosen = [item for item in candidates if item.id == which]
        needle = _plain(which)
        if not chosen:
            chosen = [item for item in candidates if needle and needle == _plain(item.text)]
        if not chosen:
            chosen = [item for item in candidates if needle and needle in _plain(item.text)]
        if len(chosen) != 1:
            raise ValueError(("no open loop matches " if not chosen else f"{len(chosen)} open loops match ")
                             + repr(which) + "; use an id from `brain loops --json`")
        item = chosen[0]
        path = path_for(config)
        data, body = fm.read(path)
        body, _ = needs._without(body, needs._section(body, "Open"), lambda bullet: bullet.id == item.id,
                                 bullet=_bullet, detail=DETAIL)
        item.closed = _local(config, at).strftime("%Y-%m-%d %H:%M")
        item.outcome, item.because = outcome, because
        body = _insert(body, "Closed", render(item))
        closed = sorted(parse_section(body, "Closed"), key=lambda loop: loop.closed)
        if len(closed) > CLOSED_KEEP:
            for old in closed[:-CLOSED_KEEP]:
                archive = config.vault_path / config.agent("Loops") / f"{old.closed[:7]}.md"
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    archive.write_text("---\nkind: log\ntier: cold\n---\n\n# Closed loops\n\n", encoding="utf-8")
                with archive.open("a", encoding="utf-8") as stream:
                    stream.write(render(old) + "\n\n")
            span = needs._section(body, "Closed")
            kept = "\n\n".join(render(old) for old in closed[-CLOSED_KEEP:])
            body = body[:span[0]] + "\n\n" + kept + "\n\n" + body[span[1]:]
        fm.write(path, data, body)
        return item


def due(config: Config, now: datetime) -> list[Item]:
    now = _local(config, now).astimezone(timezone.utc)
    out = []
    for item in open_loops(config):
        opened = _local(config, _time(item.since)).astimezone(timezone.utc)
        age = now - opened
        if item.kind == "waiting" and age < timedelta(hours=WAITING_HOURS):
            continue
        deadline = _local(config, _time(item.due)).astimezone(timezone.utc) if item.due else None
        hours = WAITING_HOURS if item.kind == "waiting" else STALE_HOURS
        if (deadline is not None and now >= deadline) or (deadline is None and age >= timedelta(hours=hours)):
            out.append(item)
    return out
