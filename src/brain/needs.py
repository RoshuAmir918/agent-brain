"""The owner's queue: current asks and access requests."""
from __future__ import annotations

import re
from dataclasses import InitVar, dataclass, field
from datetime import date, timedelta
from pathlib import PurePosixPath

from . import access, access_log, fm, tasks as task_mod
from . import items as grammar
from .config import Config
from .tasks import load_tasks
from .vault import Vault

KINDS = ("ask", "confirm")
OPEN_ASKS = 5        # asks shown at once; past it, the evening decides the lowest-ranked down to it
STALE_DAYS = 3       # an ask unanswered this long is the evening's to decide or retire
CONFIRM_DAYS = 5     # a confirm unanswered this long is taken as accepted
ASK_WORDS = 16       # an owner-facing Ask line: a proposition, read cold on a phone
WHY_WORDS = 40       # its Why line: what the owner gets, one or two plain sentences
DATED_HEADING_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December) \d{1,2}(?:, \d{4})?) — (.+)$")


@dataclass
class Item:
    """One thing on the owner's list. `text` is the question they answer; `detail` is why it needs them and what a yes
    or a no means, in two or three plain sentences; `context` names what it is about (a task, a responsibility).
    An idea's optional `needs` names what accepting it requires."""
    id: str = field(init=False)
    kind: str
    text: str
    origin: str
    since: str | None
    source: str
    detail: str = ""
    context: str = ""
    needs: str = ""
    goal: str | None = None
    hub: str = ""       # the matter it belongs to, as a note path, when the ask named one
    session: str = ""   # the conversation it was asked in (`demiurge:<id>`), so what settles it can be said there
    label: InitVar[str | None] = None   # what the id is keyed on, when the text is built from changing fields
    task: str = field(init=False)       # the task it stands for (T-nnn), from its detail or source; else ""
    owner: str = field(init=False, default="")   # who it is for: its task's owner, in an organisation's intellect; else ""
    default: str = ""   # a consult's stated default when nobody answers
    area: str = ""    # the ownership area a consult concerns
    hours: dict = field(default_factory=dict)   # a consult recipient's current working hours
    away: str = ""    # the last local date of their current absence, else empty
    on: str = "owner"   # who it waits on: the owner, or for a consult the colleague asked (platform:id)
    wake: str = ""    # when it acts unprompted: an ask goes to the evening, a confirm is accepted, an idea lapses, a consult moves on
    snoozed: str = ""   # an idea put off until this date

    def __post_init__(self, label: str | None) -> None:
        self.id = grammar.derived(self.origin, self.source, label if label is not None else self.text)
        self.task = task_of(self.source) or ""


def task_of(source: str) -> str | None:
    """The task a source names: the register's `Tasks#T-nnn` anchor or a task's archive note."""
    match = re.search(r"(?:#|Tasks Archive/)(T-\d{3,})(?:\.md)?$", source)
    return match[1] if match else None


def _bullet(line: str) -> Item | None:
    line = line.strip()
    if not line.startswith("- "):
        return None
    parts = line[2:].split(" · ")
    if len(parts) not in (3, 4):
        return None
    since, kind, text = (part.strip() for part in parts[:3])
    if kind not in KINDS or not text or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        return None
    try:
        date.fromisoformat(since)
    except ValueError:
        return None
    source = "CURRENT.md"
    if len(parts) == 4:
        link = re.fullmatch(r"\[\[([^\[\]\n]+)\]\]", parts[3].strip())
        if not link:
            return None
        source = link[1].split("|", 1)[0].strip()
        if not source:
            return None
    return Item(kind, text, "current", since, source)


def read(block: grammar.Block) -> Item | None:
    """A Needs you bullet with its joined `why` lines and the session it was asked in."""
    item = _bullet(block.line)
    if item is None:
        return None
    why = " ".join(value for name, value in block.details if name == "why" and value)
    item.detail = why
    item.session = block.get("session") or ""
    if (task := block.get("task")) is not None:
        item.task = task
    item.wake = day_of(block.get("wake"))
    item.hub = block.get("hub") or ""
    return item


def day_of(value: str | None) -> str:
    try:
        return date.fromisoformat(value).isoformat() if value else ""
    except ValueError:
        return ""


def wake_for(kind: str, day: date) -> str:
    """The wake a new ask or confirm is written with: the evening decides an ask, a confirm is taken as accepted."""
    return (day + timedelta(days=CONFIRM_DAYS if kind == "confirm" else STALE_DAYS)).isoformat()


def parse_section(body: str) -> list[Item]:
    return grammar.parse(body, "Needs you", read)


STATUS_FIELD = re.compile(r"^[ \t]*-[ \t]+\*\*Status:\*\*[ \t]*([^\r\n]*)", re.M | re.I)
STATUS_INLINE = re.compile(r"`?\bstatus:[ \t]*`?[ \t]*`?([A-Za-z]+)`?", re.I)


def improvement_status(entry: str) -> str | None:
    """The `**Status:**` field's first backticked word, else its first word; else an inline `status: <word>`,
    the form the improve routine writes. Lower-cased."""
    field = STATUS_FIELD.search(entry)
    if field and field[1].strip():
        quoted = re.search(r"`([^`\s]+)`", field[1])
        word = quoted[1] if quoted else (re.search(r"\b\w+\b", field[1]) or [None])[0]
        return word.lower() if word else None
    inline = STATUS_INLINE.search(entry)
    return inline[1].lower() if inline else None


def _owner_lines(body: str) -> tuple[str | None, str]:
    """The owner-facing Ask and Why lines of a proposal. Working notes (Blocker, Status,
    Gap, Change) are the agent's own records and are never shown to the owner."""
    ask = fm.field(body, "Ask")
    why = fm.field(body, "Why") or ""
    return ask or None, why


def _heading_date(text: str, today: date | None) -> str | None:
    """An ISO date, `October 26, 2026`, or `October 26` (the likeliest year: this one, or last if that is ahead)."""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    match = re.fullmatch(r"([A-Za-z]+) (\d{1,2})(?:, (\d{4}))?", text)
    if not match:
        return None
    months = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
              "november", "december")
    try:
        month = months.index(match[1].lower()) + 1
        if match[3]:
            return date(int(match[3]), month, int(match[2])).isoformat()
        if today is None:
            return None
        when = date(today.year, month, int(match[2]))
        return (when if when <= today else date(today.year - 1, month, int(match[2]))).isoformat()
    except ValueError:
        return None


def improvements(body: str, source: str, today: date | None = None) -> list[Item]:
    span = fm.section(body, "Improvements")
    if span is None:
        return []
    section = body[span[0]:span[1]]
    headings = list(re.finditer(r"^### ([^\r\n]+)", section, re.M))
    out = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        if improvement_status(section[heading.end():end]) != "proposed":
            continue
        anchor = heading[1].rstrip()
        dated = DATED_HEADING_RE.fullmatch(anchor)
        title, since = anchor, None
        if dated:
            title, since = dated[2], _heading_date(dated[1], today)
        entry = section[heading.end():end]
        hub = source.rsplit("/", 1)[-1].removesuffix(".md")
        ask, why = _owner_lines(entry)
        out.append(Item("ask", ask or "Adopt? " + title, "improvement", since, source + "#" + anchor,
                        detail=why, context=hub, label="Adopt? " + title,
                        goal=fm.field(entry, "Goal") or fm.field(body[:span[0]], "Goal")))
    return out


def _access_items(vault: Vault, config: Config, today: date) -> list[Item]:
    rules = access.rules(vault, config)
    out, seen = [], set()
    for entry in access_log.recent(config, today, 14):
        if entry.outcome != "declined" or entry.person.startswith("stranger ("):
            continue
        people = vault.links_in(entry.person)
        if len(people) != 1:
            continue
        resolved = access.resolve(vault, config, people[0])
        decision = resolved.subjects.get(entry.subject)
        if resolved.person is None or decision is None:
            continue
        # Any rule of the owner's for this person or one of their groups settles it, whatever it says.
        if any(rule.audience is not None and rule.subject in (entry.subject, "anything")
               and (rule.audience == access.Audience("person", resolved.person)
                    or rule.audience.kind == "group" and rule.audience.target in resolved.groups)
               for rule in rules):
            continue
        pair = (resolved.person, entry.subject)
        if pair in seen:
            continue
        seen.add(pair)
        name = PurePosixPath(resolved.person).parent.name.replace("_", " ")
        audience = PurePosixPath(resolved.groups[0]).stem.replace("_", " ") if resolved.groups else "them"
        text = (f"{name} asked {entry.asked.rstrip('.?!')}. "
                f"Let {audience} see your {entry.subject} from now on?")
        item = Item("ask", text, "access", entry.when[:10], entry.source)
        item.id = grammar.derived("access", *pair)
        out.append(item)
    return out


def items(vault_or_config: Vault | Config, today: date) -> list[Item]:
    vault = vault_or_config if isinstance(vault_or_config, Vault) else Vault(vault_or_config.vault_path)
    config = vault_or_config if isinstance(vault_or_config, Config) else vault.config
    current = vault.notes.get("CURRENT.md")
    out = parse_section(current.body) if current else []
    register = config.agent("Tasks.md")
    tasks, archived = [], []
    if (config.vault_path / register).exists():
        tasks, archived = load_tasks(config)
    out.extend(_access_items(vault, config, today))
    owners = {task.tid: task.owner for task in tasks + archived if task.owner}
    for tid in {item.task for item in out if item.kind == "confirm" and item.task and item.task not in owners}:
        if task := task_mod.find(config, tid, archived_closed=True):
            owners[tid] = task.owner or ""
    for item in out:
        item.owner = owners.get(item.task, "")
    from . import consults
    out.extend(consults.items(config))
    gone = dismissed_ids(config)
    return sorted((item for item in out if item.id not in gone), key=lambda item: rank(item, config, today))


def rank(item: Item, config: Config, today: date) -> tuple:
    """Asks before confirms; among asks, one naming a date in the coming week first, then what blocks work (a task
    waiting on the owner, a request relayed by another intellect), then the rest newest first."""
    from .compile_context import DATE, mentioned
    if item.kind == "confirm":
        return (1, 0, "")
    soon = any((when := mentioned(m, today)) and today <= when <= date.fromordinal(today.toordinal() + 7)
               for m in DATE.finditer(item.text))
    blocking = item.origin in ("task", "consult") or bool(item.task) or item.source.startswith(config.agents_dir + "/")
    newest_first = "".join(chr(0x10FFFF - ord(c)) for c in (item.since or "0000-00-00"))
    return (0, 0 if soon else 1 if blocking else 2, newest_first)


def overflow(config: Config, today: date, ceiling: int = OPEN_ASKS) -> list[Item]:
    """The asks in CURRENT past the ceiling, lowest-ranked first: what the evening decides tonight."""
    asks = [item for item in items(config, today) if item.kind == "ask"]
    extra = asks[ceiling:]
    return [item for item in reversed(extra) if item.origin == "current"]


def open_asks(body: str) -> list[Item]:
    return [item for item in parse_section(body) if item.kind == "ask"]


def task_ask(vault_or_config: Vault | Config, tid: str) -> Item | None:
    """The task's open question in CURRENT, including older bullets linked only by their source."""
    if isinstance(vault_or_config, Vault):
        note = vault_or_config.notes.get("CURRENT.md")
        body = note.body if note else ""
    else:
        path = vault_or_config.vault_path / "CURRENT.md"
        body = path.read_text(encoding="utf-8") if path.exists() else ""
    return next((item for item in open_asks(body) if item.task == tid), None)


def migrate_task_asks(config: Config, report: list[str]) -> None:
    """Move waiting tasks' old questions into CURRENT, keeping ids already held by the app."""
    from . import journal
    from .lock import vault_lock
    marker = config.vault_path / config.runtime_dir / "task-asks-0.171.0"
    with vault_lock(config):
        if marker.exists():
            return
        registered, archived = load_tasks(config)
        in_register = {task.tid for task in registered}
        current = config.vault_path / "CURRENT.md"
        for task in registered + archived:
            ask = fm.field(task.body, "Ask")
            if task.task_state != "waiting_for_owner" or not ask:
                continue
            data, body = fm.read(current) if current.exists() else ({}, "")
            existing = next((item for item in open_asks(body) if item.task == task.tid), None)
            if existing is None:
                existing = next((item for item in open_asks(body) if not item.task and
                                 (task.tid in re.findall(r"\bT-\d{3,}\b", item.source)
                                  or fm.plain(item.text) == fm.plain(ask))), None)
            if existing:
                fm.write(current, data, grammar.set_details(body, "Needs you", read, existing.id, {"task": task.tid}))
            else:
                source = (config.agent("Tasks.md") + "#" + task.tid if task.tid in in_register else
                          task_mod.archive_path(config, task.tid).relative_to(config.vault_path).as_posix())
                old_id = grammar.derived("task", source, task.title)
                journal.add_ask(config, config.today(), ask, config.agent(f"Tasks#{task.tid}"),
                                fm.field(task.body, "Why") or "", session=task.report or "", task=task.tid)
                data, body = fm.read(current)
                question = task_ask(config, task.tid)
                fm.write(current, data, grammar.set_details(body, "Needs you", read, question.id, {"id": old_id}))
            for name in ("ask", "why"):
                task.body = task_mod._replace_field(task.body, name, None)
            task_mod.save_task(config, task, config.today())
            report.append(f"task ask: {task.tid}: moved the question to Needs you")
        marker.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(marker, config.today().isoformat() + "\n")


def stale(config: Config, today: date, days: int = STALE_DAYS) -> list[Item]:
    """Asks in CURRENT unanswered for `days` or more, oldest first; a task's ask waits with its task instead."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    # A written wake decides; an ask written before wakes were stored keeps its age. A task's ask waits with its task.
    old = [item for item in open_asks(body) if not item.task
           and (item.wake <= today.isoformat() if item.wake else (today - date.fromisoformat(item.since)).days >= days)]
    return sorted(old, key=lambda item: item.since)


def _prune(config: Config, drop) -> list[Item]:
    """Take the bullets `drop(item)` selects out of CURRENT's Needs you, with their why lines; returns them."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    new, removed = grammar.without(body, "Needs you", read, drop)
    if removed:
        fm.write_text(path, new)
    return removed


ANSWERED_HEADER = "---\nkind: log\ntier: cold\n---\n# Answered\n\nAnswers, decisions and closed loops, with dates and evidence.\n\n"


DISMISSED = re.compile(r"^- \d{4}-\d{2}-\d{2} · dismissed(?: [a-z-]+)? · .* · id:([0-9a-f]{12})\b", re.M)
REASONS = ("no", "done", "stop-asking")   # what the owner said by dismissing: never inferred, only chosen


def dismissed_ids(config: Config) -> set[str]:
    """Items the owner dismissed, from the Answered logs: computed items (an access request) would otherwise return."""
    folder = config.vault_path / config.agent("Answered")
    if not folder.is_dir():
        return set()
    return {m[1] for path in folder.glob("*.md") for m in DISMISSED.finditer(path.read_text(encoding="utf-8"))}


def _pick(config: Config, which: str, today: date, prefer_current: bool = False) -> Item:
    """One open item by id, or by text matching exactly one."""
    candidates = items(config, today)
    narrow = (lambda chosen: [item for item in chosen if item.origin == "current"]) if prefer_current else None
    return grammar.pick(candidates, which, "item", "brain needs-you --json", narrow=narrow)


def _remove_bullet(config: Config, item: Item) -> None:
    taken = []
    def first(bullet: Item) -> bool:
        if not taken and bullet.id == item.id:
            taken.append(bullet)
            return True
        return False
    _prune(config, first)


def _answered_line(today: date, item: Item, because: str) -> str:
    return (f"- {today.isoformat()} · {item.kind} · {item.text} · because {because}"
            + (f" · [[{item.source}]]" if item.source != "CURRENT.md" else ""))


def answered(config: Config, today: date, line: str) -> None:
    log = config.vault_path / config.agent("Answered") / f"{today:%Y-%m}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    body = log.read_text(encoding="utf-8") if log.exists() else ANSWERED_HEADER
    fm.write(log, {}, body + line + "\n")


def dismiss(config: Config, which: str, today: date, reason: str = "no", *, by: str = "") -> Item:
    """The owner closed an item without answering it, and chose why. The item leaves the list and is never raised
    again; what happens to its source depends on the reason, never on a guess:
    - `no`: nothing is done. An ask's or a waiting task's task is set aside, an access request is
      declined with no grant written, a confirm is accepted (an autonomous act's confirm is reversed).
    - `done`: the owner did it themselves. Its task is completed, with that as evidence.
    - `stop-asking`: as `no`, and the owner wants no questions like it; the People review turns it into a dated line in
      Working together. For ordinary items, only this reason changes future suggestions.
    Each is logged in Answered with its reason."""
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")
    item = _pick(config, which, today)
    if item.origin == "consult":
        from . import consults
        consults.dismiss(config, item, today, reason, by)
        return item
    if item.origin == "current":
        _remove_bullet(config, item)
    tid = item.task if item.origin == "task" or (item.origin == "current" and item.kind == "ask") else None
    if tid:
        try:
            if reason == "done":
                task_mod.close(config, tid, today, True, "the owner said they did it, in their app", by_owner=True)
            else:
                task_mod.set_aside(config, tid, today, "dismissed by the owner in their app")
        except ValueError:
            pass
    text = item.text.replace("·", "-")
    answered(config, today, f"- {today.isoformat()} · dismissed {reason} · {item.kind} · {text} · id:{item.id}"
              + (f" · [[{item.source}]]" if item.source != "CURRENT.md" else ""))
    if item.kind == "ask":
        from . import reception
        reception.answered(config, "ask", item.text, f"declined:{reason}")
    return item


def resolve(config: Config, which: str, because: str, today: date, *, record_answer: bool = True,
            owner_engaged: bool = False, by: str | None = None, yours: str | None = None,
            delivers: str | None = None, why: str | None = None) -> Item | task_mod.Task:
    """Remove one item from CURRENT's Needs you because something answered it, and record what did, in
    `<agent_dir>/Answered/YYYY-MM.md`. `which` is an item id from `brain needs-you --json`, or text that matches exactly
    one open item. A yours conversion also accepts a waiting task's ask; other computed items resolve where they live."""
    answer = " ".join(because.split())
    because = " ".join(because.replace("|", "/").split()) or (f"became owner work: {yours}" if yours else "")
    if not because:
        raise ValueError("say what answered it: --because \"<the evidence>\"")
    item = _pick(config, which, today, prefer_current=True)
    if yours is not None and item.kind != "ask":
        raise ValueError("--yours is only for an ask, not a confirm")
    if item.origin == "consult":
        raise ValueError("answer it with brain consult answer")
    if item.origin != "current" and not (yours is not None and item.origin == "task"):
        where = {"task": "change the task's state", "access": "write or decline the grant (setup)"}[item.origin]
        raise ValueError(f"this item lives elsewhere: {where}")
    child = None
    if yours is not None:
        source = f'Ask: "{item.text}"' + (f' Answer: "{answer}"' if answer else "")
        values = {name: value for name, value in (("why", why if why is not None else item.detail),
                                                  ("delivers", delivers)) if value}
        # Keep the ask open until creation validates; blocking its parent would otherwise settle it before the hooks.
        child = task_mod.new_task(config, yours, source, state="yours", values=values,
                                  from_task=item.task, report=item.session, block_parent=False)
    _remove_bullet(config, item)
    answered(config, today, _answered_line(today, item, because))
    if item.kind == "ask":
        from . import reception
        reception.answered(config, "ask", item.text, "used")
    if record_answer:
        from . import continuation
        continuation.answered(config, config.agent(f"Tasks#{item.task}") if item.task else item.source,
                              item.text, because, owner_engaged, by, kind=item.kind)
    if child:
        if item.task:
            parent = task_mod.find(config, item.task, archived_closed=True)
            task_mod.block_on_owner(config, parent, child, today)
        return child
    if item.kind == "ask" and item.task:
        task = task_mod.find(config, item.task, open_only=True)
        if task and task.task_state == "waiting_for_owner":
            task_mod.set_fields(config, task.tid, today, state="in_progress",
                                values={"evidence": f"{today} — the owner answered: {because}"},
                                clear=["blocker", "resume"])
    return item


def settle_tasks(config: Config, today: date, settled: list[tuple[str, str]], because: str) -> list[str]:
    """Take out of CURRENT's Needs you the asks that stood for tasks no longer waiting on the owner (answered, moved on
    or closed): a bullet whose source or task detail names the task. `settled` is (task id, the entry as it
    was when it waited). The caller holds the vault lock; each one is recorded as answered."""
    if not settled:
        return []
    def stood_for(item: Item) -> bool:
        if item.kind != "ask":
            return False
        named = set(re.findall(r"\bT-\d{3,}\b", item.source))
        return any(item.task == tid or (not item.task and tid in named) for tid, _ in settled)
    removed = _prune(config, stood_for)
    for item in removed:
        answered(config, today, _answered_line(today, item, because))
    return [item.text for item in removed]


def expire_confirms(config: Config, today: date) -> list[str]:
    return [item.text for item in _prune(config, lambda item: item.kind == "confirm"
                                         and (item.wake <= today.isoformat() if item.wake
                                              else (today - date.fromisoformat(item.since)).days >= CONFIRM_DAYS))]


def replace_task_asks(config: Config, body: str, tid: str, today: date) -> str:
    """CURRENT's body without the open asks that stand for `tid`, each recorded as replaced: a task carries one question
    at a time, its newest. The caller holds the vault lock and writes the body."""
    new, removed = grammar.without(body, "Needs you", read, lambda item: item.kind == "ask" and item.task == tid)
    for item in removed:
        answered(config, today, _answered_line(today, item, f"a newer question about {tid} replaced it"))
    return new
