"""What the agent is holding: promises, owed answers and waits on other people."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import directory, fm, tasks
from .config import Config
from .identity import is_owner, profile_ids
from .lock import vault_lock
from .vault import TASK_ID_RE

KINDS = ("promised", "owed", "waiting")
OUTCOMES = ("done", "dropped")
STALE_HOURS = 4
WAITING_HOURS = 24
STALE_DAYS = 2      # past due this long, watch has had its chances: the evening decides the loop
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
DETAIL = re.compile(r"^[ \t]+-[ \t]+(with|due|ready|task|closed):[ \t]*(.+?)[ \t]*$")
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
    ready: str = ""
    task: str = ""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        key = json.dumps([self.kind, fm.plain(self.text)], ensure_ascii=False)
        self.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _clean(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def _name(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").casefold().split())


def _profile_names(path: Path, data: dict, body: str) -> tuple[set[str], set[str]]:
    """Folder and title names, plus exact aliases; aliases do not supply guessed first names."""
    names = {path.parent.name}
    title = re.search(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", body, re.M)
    if title:
        names.add(title[1])
    if isinstance(data.get("title"), str):
        names.add(data["title"])
    names = {_name(name) for name in names if _name(name)}
    first = {name.split()[0] for name in names}
    aliases = data.get("aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, list):
        names.update(_name(alias) for alias in aliases if isinstance(alias, str) and _name(alias))
    return names, first


def _counterpart(config: Config, kind: str, who: str) -> None:
    if not who:
        if kind in ("owed", "waiting"):
            raise ValueError(f"{kind} loops need a counterpart (with)")
        return
    owner_path = config.vault_path / config.human_profile
    data, body = fm.read(owner_path) if owner_path.is_file() else ({}, "")
    owner_names, owner_first = _profile_names(owner_path, data, body)
    human = _name(config.human_name)
    owner_names.update(owner_first | {human, human.split()[0]})
    known = set()
    first_names: dict[str, set[Path]] = {}
    for root in (config.people_dir, config.agents_dir):
        for path in (config.vault_path / root).rglob("Profile.md"):
            try:
                data, body = fm.read(path)
            except fm.Malformed:
                continue   # skipped as the vault skips it
            if data.get("kind") == "log":
                continue   # a moved or retired stub is not an identity
            names, first = _profile_names(path, data, body)
            if path == owner_path or any(is_owner(config, platform, (ident,))
                                         for platform, ident in (value.split(":", 1) for value in profile_ids(body))):
                owner_names.update(names | first)
                continue
            known.update(names)
            for name in first:
                first_names.setdefault(name, set()).add(path)
    if _name(who) in owner_names:
        # A promise to the owner is a loop; waiting on them is an ask.
        if kind == "waiting":
            raise ValueError(f"{who} is your owner: put it in Needs you as an ask, not a loop")
        return
    if not (config.vault_path / config.people_dir).is_dir():
        return   # bare vaults have no directory to validate against
    known.update(name for name, profiles in first_names.items() if len(profiles) == 1)
    # Demiurge keeps names of intellects without vault profiles in its runtime directory cache.
    listed = directory.load(config) or {}
    known.update(_name(row["name"]) for row in listed.get("intellects", []) if row.get("name"))
    if _name(who) not in known:
        raise ValueError(f"{who} is not a person or intellect you know; name who you asked or who asked you")


def _time(value: str) -> datetime:
    if not re.fullmatch(STAMP + r"|\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be YYYY-MM-DD or YYYY-MM-DD HH:MM")
    return datetime.fromisoformat(value)


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
    span = fm.section(body, title)
    out = []
    current = None
    for line in body[span[0]:span[1]].splitlines() if span else []:
        detail = DETAIL.match(line)
        if detail and current is not None:
            if detail[1] == "with":
                current.with_ = detail[2]
            elif detail[1] == "task":
                current.task = detail[2]
            elif detail[1] in ("due", "ready"):
                try:
                    _time(detail[2])
                except ValueError:
                    continue
                setattr(current, detail[1], detail[2])
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
    for label, value in (("with", item.with_), ("due", item.due), ("ready", item.ready), ("task", item.task)):
        if value:
            bullet += f"\n  - {label}: {value}"
    if item.closed:
        bullet += f"\n  - closed: {item.closed} · {item.outcome} · {item.because}"
    return bullet


def as_dict(item: Item) -> dict:
    out = {"id": item.id, "kind": item.kind, "text": item.text, "with": item.with_, "due": item.due,
           "since": item.since, "source": item.source, "ready": item.ready, "task": item.task}
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
    span = fm.section(body, title)
    if span is None:
        return body.rstrip("\n") + f"\n\n## {title}\n\n{text}\n"
    return body[:span[1]].rstrip("\n") + "\n\n" + text + "\n\n" + body[span[1]:]


def add(config: Config, at: datetime, kind: str, text: str, source: str, with_: str = "", due: str = "",
        task: str = "") -> bool:
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
    with_ = _clean(with_)
    _counterpart(config, kind, with_)
    task = task.strip()
    if task and kind != "waiting":
        raise ValueError("--task goes with a waiting loop: the answer it waits for resumes that task")
    with vault_lock(config):
        if task and tasks.find(config, task, open_only=True) is None:
            raise ValueError(f"{task} is not an open task")
        path = path_for(config)
        data, body = fm.read(path) if path.exists() else fm.split(TEMPLATE)
        if any(fm.plain(item.text) == fm.plain(text) for item in parse_section(body)):
            return False
        item = Item(kind, text, config.local(at).strftime("%Y-%m-%d %H:%M"), source, with_, due, task=task)
        body = _insert(body, "Open", render(item))
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, data, body)
    return True


def _pick(config: Config, which: str) -> Item:
    candidates = open_loops(config)
    chosen = [item for item in candidates if item.id == which]
    needle = fm.plain(which)
    if not chosen:
        chosen = [item for item in candidates if needle and needle == fm.plain(item.text)]
    if not chosen:
        chosen = [item for item in candidates if needle and needle in fm.plain(item.text)]
    if len(chosen) != 1:
        raise ValueError(("no open loop matches " if not chosen else f"{len(chosen)} open loops match ")
                         + repr(which) + "; use an id from `brain loops --json`")
    return chosen[0]


def ready(config: Config, which: str, at: datetime) -> Item:
    """The answer is in hand: watch can act now, regardless of the loop's age or deadline."""
    with vault_lock(config):
        item = _pick(config, which)
        if item.ready:
            return item
        path = path_for(config)
        data, body = fm.read(path)
        body, _ = fm.without(body, fm.section(body, "Open"), lambda loop: loop.id == item.id,
                                 bullet=_bullet, detail=DETAIL)
        item.ready = config.local(at).strftime("%Y-%m-%d %H:%M")
        body = _insert(body, "Open", render(item))
        fm.write(path, data, body)
        return item


def close(config: Config, which: str, at: datetime, outcome: str, because: str) -> Item:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    because = _clean(because)
    if not because:
        raise ValueError("closing a loop needs a reason")
    with vault_lock(config):
        item = _pick(config, which)
        path = path_for(config)
        data, body = fm.read(path)
        body, _ = fm.without(body, fm.section(body, "Open"), lambda bullet: bullet.id == item.id,
                                 bullet=_bullet, detail=DETAIL)
        item.closed = config.local(at).strftime("%Y-%m-%d %H:%M")
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
            span = fm.section(body, "Closed")
            kept = "\n\n".join(render(old) for old in closed[-CLOSED_KEEP:])
            body = body[:span[0]] + "\n\n" + kept + "\n\n" + body[span[1]:]
        fm.write(path, data, body)
        if outcome == "done" and item.task:
            _wake(config, item, config.local(at).date())
        return item


def _wake(config: Config, item: Item, today: date) -> None:
    """The answer a task was blocked on is in: the task goes back to its worker instead of waiting for someone to notice
    (Nameless, 2026-11-11: Orphan's answers reached it at 14:09 and the task sat blocked until Arslan asked at 15:52).
    Its Blocker stays as the last evidence, as when a child closes; a task blocked on its owner's own work (a yours
    child) or waiting on them stays where it is."""
    task = tasks.find(config, item.task, open_only=True)
    if task is None:
        return
    note = {"evidence": f"{today} — {item.with_ or 'the answer'} answered: {item.text}"}
    blocker = fm.field(task.body, "Blocker") or ""
    child = TASK_ID_RE.match(blocker)
    if task.task_state == "blocked" and not (child and tasks.find(config, child[0], open_only=True)):
        tasks.set_fields(config, task.tid, today, state="in_progress", values=note, clear=["resume"] if task.resume else [])
    else:
        tasks.set_fields(config, task.tid, today, values=note)


def _deadline(config: Config, item: Item) -> datetime:
    """When a loop becomes due: its named date, else a few hours (a day for a wait) after it opened. A wait is never due
    before its day has passed, whatever date it names."""
    opened = config.local(_time(item.since)).astimezone(timezone.utc)
    hours = WAITING_HOURS if item.kind == "waiting" else STALE_HOURS
    if item.due:
        deadline = config.local(_time(item.due)).astimezone(timezone.utc)
        return max(deadline, opened + timedelta(hours=WAITING_HOURS)) if item.kind == "waiting" else deadline
    return opened + timedelta(hours=hours)


def due(config: Config, now: datetime) -> list[Item]:
    now = config.local(now).astimezone(timezone.utc)
    return [item for item in open_loops(config) if item.ready or now >= _deadline(config, item)]


def stale(config: Config, now: datetime, days: int = STALE_DAYS) -> list[Item]:
    """Open loops due `days` or more ago that nothing has moved, oldest first: watch has done what it can, so the evening
    closes, converts or drops each. A ready loop is watch's to relay and is never stale."""
    now = config.local(now).astimezone(timezone.utc)
    old = [item for item in open_loops(config) if not item.ready and now - _deadline(config, item) >= timedelta(days=days)]
    return sorted(old, key=lambda item: _deadline(config, item))
