"""What the agent is holding: promises, owed answers, waits on other people, and what it will do when something happens."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import directory, fm, items, needs, tasks
from .config import Config
from .identity import is_owner, profile_ids
from .lock import vault_lock
from .vault import TASK_ID_RE

KINDS = ("promised", "owed", "waiting", "when")
OUTCOMES = ("done", "dropped")
STALE_HOURS = 4
WAITING_HOURS = 24
STALE_DAYS = 2      # past due this long, watch has had its chances: the evening decides the loop
TEMPLATE = """---
kind: orientation
tier: warm
pinned: false
---
# Loops

What I am holding for you: promises, answers owed and waits on others.

## Open

"""
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
    wake: str = ""      # when it next acts on its own, written when it opens; a `when` with only a condition has none
    if_: str = ""       # a `when` loop's condition: what has to happen before it acts
    hub: str = ""       # the matter it belongs to, as a note path; its source is often another note
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = items.derived(self.kind, fm.plain(self.text))


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
    match = re.fullmatch(r"- (" + STAMP + r") · (" + "|".join(KINDS) + r") · (.+?) · \[\[([^\[\]\n]+)\]\]",
                         line.strip())
    if not match or len(line.split(" · ")) != 4 or not match[3].strip():
        return None
    try:
        _time(match[1])
    except ValueError:
        return None
    source = match[4].split("|", 1)[0].strip()
    return Item(match[2], match[3].strip(), match[1], source) if source else None


def read(block: items.Block) -> Item | None:
    current = _bullet(block.line)
    if current is None:
        return None
    for name, value in block.details:
        if not value:
            continue
        if name == "with":
            current.with_ = value
        elif name == "task":
            current.task = value
        elif name == "if":
            current.if_ = value
        elif name == "hub":
            current.hub = value
        elif name in ("due", "ready", "wake"):
            try:
                _time(value)
            except ValueError:
                continue
            setattr(current, name, value)
        elif name == "closed":
            closed = re.fullmatch(r"(" + STAMP + r") · (done|dropped) · (.+)", value)
            if closed:
                try:
                    _time(closed[1])
                except ValueError:
                    continue
                current.closed, current.outcome, current.because = closed.groups()
    return current


def parse_section(body: str, title: str = "Open") -> list[Item]:
    return items.parse(body, title, read)


def render(item: Item) -> str:
    bullet = f"- {item.since} · {item.kind} · {item.text} · [[{item.source}]]"
    for label, value in (("with", item.with_), ("due", item.due), ("if", item.if_), ("wake", item.wake),
                         ("ready", item.ready), ("task", item.task), ("hub", item.hub)):
        if value:
            bullet += f"\n  - {label}: {value}"
    if item.closed:
        bullet += f"\n  - closed: {item.closed} · {item.outcome} · {item.because}"
    return bullet + f"\n  - id: {item.id}"


def as_dict(item: Item) -> dict:
    out = {"id": item.id, "kind": item.kind, "text": item.text, "with": item.with_, "due": item.due,
           "since": item.since, "source": item.source, "ready": item.ready, "task": item.task,
           "wake": item.wake, "if": item.if_, "hub": item.hub}
    if item.closed:
        out.update(closed=item.closed, outcome=item.outcome, because=item.because)
    return out


def open_loops(config: Config) -> list[Item]:
    path = path_for(config)
    return parse_section(fm.read(path)[1]) if path.exists() else []


def _read_answered(block: items.Block) -> Item | None:
    match = re.fullmatch(r"- (" + STAMP + r") · loop (done|dropped) · (" + "|".join(KINDS)
                         + r") · ([^·\n]+?) · because ([^·\n]+?) · id:([0-9a-f]{12}) · \[\[([^\[\]\n]+)\]\]",
                         block.line)
    if not match or not match[4].strip() or not match[5].strip():
        return None
    try:
        _time(match[1])
    except ValueError:
        return None
    source = match[7].split("|", 1)[0].strip()
    if not source:
        return None
    current = Item(match[3], match[4].strip(), "", source, closed=match[1], outcome=match[2], because=match[5].strip())
    current.id = match[6]
    for name, attr in (("since", "since"), ("with", "with_"), ("due", "due"), ("if", "if_"), ("task", "task"), ("hub", "hub")):
        if value := block.get(name):
            if name in ("since", "due"):
                try:
                    _time(value)
                except ValueError:
                    continue
            setattr(current, attr, value)
    return current


def closed_recent(config: Config) -> list[Item]:
    recent = []
    for path in sorted((config.vault_path / config.agent("Answered")).glob("*.md"), reverse=True):
        body = path.read_text(encoding="utf-8")
        recent.extend(item for block in reversed(items.blocks(body, (0, len(body))))
                      if (item := _read_answered(block)) is not None)
        if len({item.id for item in recent}) >= 50:
            break
    path = path_for(config)
    if path.exists():
        recent.extend(reversed(parse_section(fm.read(path)[1], "Closed")))
    unique = {}
    for item in sorted(recent, key=lambda item: item.closed, reverse=True):
        unique.setdefault(item.id, item)
    return list(unique.values())[:50]


def _insert(body: str, title: str, text: str) -> str:
    span = fm.section(body, title)
    if span is None:
        return body.rstrip("\n") + f"\n\n## {title}\n\n{text}\n"
    return body[:span[1]].rstrip("\n") + "\n\n" + text + "\n\n" + body[span[1]:]


def add(config: Config, at: datetime, kind: str, text: str, source: str, with_: str = "", due: str = "",
        task: str = "", condition: str = "", hub: str = "") -> bool:
    """Open a loop. A `when` loop is what to do once a date comes (`due`) or something happens (`condition`): it needs
    one of them, never goes stale on its own, and is loaded into every session so a message that meets it is seen."""
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
    with_, condition = _clean(with_), _clean(condition)
    if kind == "when" and not (due or condition):
        raise ValueError("a when loop needs --due DATE, --if CONDITION, or both")
    if condition and kind != "when":
        raise ValueError("--if goes with --kind when")
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
        item = Item(kind, text, config.local(at).strftime("%Y-%m-%d %H:%M"), source, with_, due, task=task, if_=condition,
                    hub=hub.strip())
        if (deadline := _deadline(config, item)) is not None:
            item.wake = config.local(deadline).strftime("%Y-%m-%d %H:%M")
        body = _insert(body, "Open", render(item))
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, data, body)
    return True


def _pick(config: Config, which: str) -> Item:
    return items.pick(open_loops(config), which, "loop", "brain loops --json")


def ready(config: Config, which: str, at: datetime) -> Item:
    """The answer is in hand: watch can act now, regardless of the loop's age or deadline."""
    with vault_lock(config):
        item = _pick(config, which)
        if item.ready:
            return item
        path = path_for(config)
        data, body = fm.read(path)
        body, _ = items.without(body, "Open", read, lambda loop: loop.id == item.id)
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
        item = _close(config, _pick(config, which), at, outcome, because)
        if outcome == "done" and item.task:
            _wake(config, item, config.local(at).date())
        return item


def _close(config: Config, item: Item, at: datetime, outcome: str, because: str) -> Item:
    """Close one open loop into the Answered log, the one history of what closed and why; the caller holds the vault lock."""
    path = path_for(config)
    data, body = fm.read(path)
    body, _ = items.without(body, "Open", read, lambda loop: loop.id == item.id)
    item.closed = config.local(at).strftime("%Y-%m-%d %H:%M")
    item.outcome, item.because = outcome, because
    fm.write(path, data, body)
    entry = (f"- {item.closed} · loop {outcome} · {item.kind} · {item.text.replace('·', '-')}"
             f" · because {because.replace('·', '-')} · id:{item.id} · [[{item.source}]]")
    for name, value in (("since", item.since), ("with", item.with_), ("due", item.due), ("if", item.if_), ("task", item.task), ("hub", item.hub)):
        if value:
            entry += f"\n  - {name}: {value}"
    needs.answered(config, config.local(at).date(), entry)
    return item


def _wake(config: Config, item: Item, today: date) -> None:
    """The answer a task was blocked on is in: the task goes back to its worker instead of waiting for someone to notice
    (Nameless, 2026-11-11: Orphan's answers reached it at 14:09 and the task sat blocked until Arslan asked at 15:52)."""
    _resume(config, item.task, today, {"evidence": f"{today} — {item.with_ or 'the answer'} answered: {item.text}"})


def _resume(config: Config, tid: str, today: date, values: dict[str, str], answered: bool = False) -> bool:
    """Record an answer on an open task and resume it if it was blocked. Its Blocker stays as the last evidence, as when
    a child closes, unless the answer itself was recorded (answered), when a Blocker left behind would read as still
    open. A task blocked on its owner's own work (a yours child) or waiting on them stays where it is. The caller holds
    the vault lock. True when the task went back to its worker."""
    task = tasks.find(config, tid, open_only=True)
    if task is None:
        return False
    blocker = fm.field(task.body, "Blocker") or ""
    child = TASK_ID_RE.match(blocker)
    if task.task_state == "blocked" and not (child and tasks.find(config, child[0], open_only=True)):
        tasks.set_fields(config, task.tid, today, state="in_progress", values=values, clear=[*(["blocker"] if answered else []), *(["resume"] if task.resume else [])])
        return True
    tasks.set_fields(config, task.tid, today, values=values)
    return False


def waiting_on(config: Config, agent: str, name: str) -> list[tasks.Task]:
    """This agent's own open tasks that wait on an intellect: linked by a waiting loop with it, or blocked and naming it
    (by name or its profile) anywhere in the entry."""
    named = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.I)
    profile = agent.removesuffix(".md")
    linked = {loop.task for loop in open_loops(config) if loop.task and named.search(loop.with_)}
    return [task for task in tasks.all_tasks(config)
            if tasks.is_open(task) and (task.tid in linked or task.task_state == "blocked"
                                        and (named.search(task.body) or profile in task.body))]


def answer(config: Config, tid: str, agent: str, name: str, at: datetime, text: str, next_: str = "") -> str:
    """An intellect's answer to a task that waits on it, recorded from the conversation with that intellect. That
    conversation cannot write the vault, so this is its one write, and only to a task waiting on the intellect speaking
    (Nameless, 2026-11-11: told to resume T-059 from Orphan's room, it could not, and the task sat blocked)."""
    text, next_ = _clean(text), _clean(next_)
    if not text:
        raise ValueError("the answer must not be empty")
    today = config.local(at).date()
    with vault_lock(config):
        if tid not in {task.tid for task in waiting_on(config, agent, name)}:
            raise ValueError(f"{tid} is not one of your tasks waiting on {name}")
        values = {"evidence": f"{today} — {name}: {text}", **({"next": next_} if next_ else {})}
        resumed = _resume(config, tid, today, values, answered=True)
        named = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.I)
        for loop in open_loops(config):
            if loop.task == tid and named.search(loop.with_):
                _close(config, loop, at, "done", f"{name} answered")
    return (f"{tid}: the answer is recorded" + (" and the task is back with its worker" if resumed else
            "; the task stays where it is (it waits on something else too)"))


def _deadline(config: Config, item: Item) -> datetime | None:
    """When a loop becomes due: its written wake; else (a loop opened before wakes were written) its named date, else a
    few hours (a day for a wait) after it opened. A wait is never due before its day has passed, whatever date it names.
    A `when` loop with only a condition is never due by the clock."""
    if item.wake:
        return config.local(_time(item.wake)).astimezone(timezone.utc)
    if item.kind == "when":   # a bare date acts that morning, not at midnight
        when = item.due + " 09:00" if len(item.due) == 10 else item.due
        return config.local(_time(when)).astimezone(timezone.utc) if item.due else None
    opened = config.local(_time(item.since)).astimezone(timezone.utc)
    hours = WAITING_HOURS if item.kind == "waiting" else STALE_HOURS
    if item.due:
        deadline = config.local(_time(item.due)).astimezone(timezone.utc)
        return max(deadline, opened + timedelta(hours=WAITING_HOURS)) if item.kind == "waiting" else deadline
    return opened + timedelta(hours=hours)


def due(config: Config, now: datetime) -> list[Item]:
    now = config.local(now).astimezone(timezone.utc)
    return [item for item in open_loops(config)
            if item.ready or ((deadline := _deadline(config, item)) is not None and now >= deadline)]


def stale(config: Config, now: datetime, days: int = STALE_DAYS) -> list[Item]:
    """Open loops due `days` or more ago that nothing has moved, oldest first: watch has done what it can, so the evening
    closes, converts or drops each. A ready loop is watch's to relay and is never stale."""
    now = config.local(now).astimezone(timezone.utc)
    old = [item for item in open_loops(config) if not item.ready
           and (deadline := _deadline(config, item)) is not None and now - deadline >= timedelta(days=days)]
    return sorted(old, key=lambda item: _deadline(config, item))
