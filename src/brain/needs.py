"""The owner's computed queue: current asks, waiting tasks and access requests."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass, field
from datetime import date
from pathlib import PurePosixPath

from . import access, access_log, fm
from .config import Config
from .tasks import archive_path, load_tasks
from .vault import Vault

KINDS = ("ask", "confirm")
OPEN_ASKS = 5        # asks shown at once; past it, the evening decides the lowest-ranked down to it
STALE_DAYS = 3       # an ask unanswered this long is the evening's to decide or retire
RETIRE_DAYS = 5      # an ask unanswered this long leaves the list; silence is an answer. A task's ask settles with it.
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
    label: InitVar[str | None] = None   # what the id is keyed on, when the text is built from changing fields
    task: str = field(init=False)       # the task it stands for (T-nnn), when its source names one; else ""
    owner: str = field(init=False, default="")   # who it is for: its task's owner, in an organisation's intellect; else ""
    default: str = ""   # a consult's stated default when nobody answers
    area: str = ""    # the ownership area a consult concerns

    def __post_init__(self, label: str | None) -> None:
        key = json.dumps([self.origin, self.source, label if label is not None else self.text], ensure_ascii=False)
        self.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        self.task = task_of(self.source) or ""


def task_of(source: str) -> str | None:
    """The task a source names: the register's `Tasks#T-nnn` anchor or a task's archive note."""
    match = re.search(r"(?:#|Tasks Archive/)(T-\d{3})(?:\.md)?$", source)
    return match[1] if match else None


def _section(body: str, title: str) -> tuple[int, int] | None:
    heading = re.search(r"^## " + re.escape(title) + r"[ \t]*\r?$", body, re.M)
    if not heading:
        return None
    following = re.compile(r"^## ", re.M).search(body, heading.end())
    return heading.end(), following.start() if following else len(body)


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


WHY = re.compile(r"^[ \t]+-[ \t]+why:[ \t]*(.+?)[ \t]*$")   # an indented line under a bullet: why it needs the owner


def parse_section(body: str) -> list[Item]:
    span = _section(body, "Needs you")
    if span is None:
        return []
    out = []
    for line in body[span[0]:span[1]].splitlines():
        why = WHY.match(line)
        if why and out:
            out[-1].detail = _cut((out[-1].detail + " " + why[1]).strip(), WHY_WORDS)
        elif (item := _bullet(line)) is not None:
            out.append(item)
    return out


def _without(body: str, span: tuple[int, int], drop, *, bullet=_bullet, detail=WHY) -> tuple[str, list[Item]]:
    """The section's lines without the bullets `drop(item)` selects and their indented detail lines."""
    kept, removed, dropping = [], [], False
    for line in body[span[0]:span[1]].splitlines(keepends=True):
        if detail.match(line.rstrip("\r\n")):
            if not dropping:
                kept.append(line)
            continue
        item = bullet(line)
        dropping = bool(item and drop(item))
        if dropping:
            removed.append(item)
        else:
            kept.append(line)
    return body[:span[0]] + "".join(kept) + body[span[1]:], removed


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


def _cut(text: str, words: int) -> str:
    parts = text.split()
    return text if len(parts) <= words else " ".join(parts[:words]).rstrip(",;:") + "…"


def _owner_lines(body: str) -> tuple[str | None, str]:
    """The owner-facing Ask and Why lines of a task or proposal, cut to their caps. Working notes (Blocker, Status,
    Gap, Change) are the agent's own records and are never shown to the owner."""
    ask = fm.field(body, "Ask")
    why = fm.field(body, "Why") or ""
    return (_cut(ask, ASK_WORDS) if ask else None), _cut(why, WHY_WORDS)


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


def _improvements(body: str, source: str, today: date | None = None) -> list[Item]:
    span = _section(body, "Improvements")
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
                        detail=why, context=hub, label="Adopt? " + title))
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
        key = json.dumps(["access", *pair], ensure_ascii=False)
        item.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        out.append(item)
    return out


def items(vault_or_config: Vault | Config, today: date) -> list[Item]:
    vault = vault_or_config if isinstance(vault_or_config, Vault) else Vault(vault_or_config.vault_path)
    config = vault_or_config if isinstance(vault_or_config, Config) else vault.config
    current = vault.notes.get("CURRENT.md")
    out = parse_section(current.body) if current else []
    waiting = []
    register = config.agent("Tasks.md")
    if (config.vault_path / register).exists():
        tasks, archived = load_tasks(config)
        registered = {task.tid for task in tasks}
        for task in tasks + archived:
            if task.task_state != "waiting_for_owner":
                continue
            source = (register + "#" + task.tid if task.tid in registered else
                      archive_path(config, task.tid).relative_to(config.vault_path).as_posix())
            ask, why = _owner_lines(task.body)
            waiting.append((task.tid, Item("ask", ask or task.title, "task", task.last_evidence_date() or None, source,
                                           detail=why, context=task.title if ask else "", label=task.title)))
    out = [item for item in out if duplicates(item, waiting) is None] + [item for _, item in waiting]
    out.extend(_access_items(vault, config, today))
    owners = {task.tid: task.owner for task in tasks + archived if task.owner} if (config.vault_path / register).exists() else {}
    for item in out:
        item.owner = owners.get(item.task, "")
    from . import consults
    out.extend(consults.items(config))
    gone = dismissed_ids(config)
    return sorted((item for item in out if item.id not in gone), key=lambda item: rank(item, config, today))


def _plain(text: str) -> str:
    return " ".join(text.casefold().split())


def duplicates(item: Item, waiting: list[tuple[str, Item]]) -> str | None:
    """The waiting task a CURRENT bullet repeats, by the same words or a source naming the task; the task is the one
    the owner sees, since it carries the state."""
    if item.origin != "current":
        return None
    named = set(re.findall(r"\bT-\d{3}\b", item.source))
    return next((tid for tid, task in waiting if tid in named or _plain(task.text) == _plain(item.text)), None)


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


def stale(config: Config, today: date, days: int = STALE_DAYS) -> list[Item]:
    """Asks in CURRENT unanswered for `days` or more, oldest first; a task's ask waits with its task instead."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    old = [item for item in open_asks(body) if not item.task and (today - date.fromisoformat(item.since)).days >= days]
    return sorted(old, key=lambda item: item.since)


def retire_asks(config: Config, today: date, days: int = RETIRE_DAYS) -> list[str]:
    """Remove asks unanswered for `days` or more: the backstop when no routine decided them. A task's ask stays while
    the task waits; it settles with the task. Returns their texts."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    if span is None:
        return []
    new, removed = _without(body, span, lambda item: item.kind == "ask" and not item.task
                            and (today - date.fromisoformat(item.since)).days >= days)
    if removed:
        fm.write(path, {}, new)
    return [item.text for item in removed]


ANSWERED_HEADER = "---\nkind: log\ntier: cold\n---\n# Answered\n\nAsks and confirms resolved by evidence, one line each: date, kind, what it was, the evidence.\n\n"


DISMISSED = re.compile(r"· dismissed(?: [a-z-]+)? · .* · id:([0-9a-f]{12})\b")
REASONS = ("no", "done", "stop-asking")   # what the owner said by dismissing: never inferred, only chosen


def dismissed_ids(config: Config) -> set[str]:
    """Items the owner dismissed, from the Answered logs: computed items (an access request) would otherwise return."""
    folder = config.vault_path / config.agent("Answered")
    if not folder.is_dir():
        return set()
    return {m[1] for path in folder.glob("*.md") for m in DISMISSED.finditer(path.read_text(encoding="utf-8"))}


def _pick(config: Config, which: str, today: date, prefer_current: bool = False) -> Item:
    """One open item by id, or by text matching exactly one. A CURRENT bullet hidden because it repeats a waiting task
    is still reachable here, so the duplicate `brain validate` names can be removed."""
    candidates = items(config, today)
    current = Vault(config.vault_path).notes.get("CURRENT.md")
    shown = {item.id for item in candidates}
    candidates += [item for item in (parse_section(current.body) if current else []) if item.id not in shown]
    chosen = [item for item in candidates if item.id == which]
    if not chosen:
        needle = " ".join(which.split()).casefold()
        chosen = [item for item in candidates if needle and needle in item.text.casefold()]
    if len(chosen) > 1 and prefer_current and len(narrowed := [item for item in chosen if item.origin == "current"]) == 1:
        chosen = narrowed
    if len(chosen) != 1:
        raise ValueError(("no open item matches " if not chosen else f"{len(chosen)} open items match ") + repr(which)
                         + "; use an id from `brain needs-you --json`")
    return chosen[0]


def _remove_bullet(config: Config, item: Item) -> None:
    path = config.vault_path / "CURRENT.md"
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    taken = []
    def first(bullet: Item) -> bool:
        if not taken and bullet.id == item.id:
            taken.append(bullet)
            return True
        return False
    new, _ = _without(body, span, first)
    fm.write(path, {}, new)


def _answered(config: Config, today: date, line: str) -> None:
    log = config.vault_path / config.agent("Answered") / f"{today:%Y-%m}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    body = log.read_text(encoding="utf-8") if log.exists() else ANSWERED_HEADER
    fm.write(log, {}, body + line + "\n")


TASK_SOURCE = re.compile(r"#(T-\d{3})$")


def dismiss(config: Config, which: str, today: date, reason: str = "no") -> Item:
    """The owner closed an item without answering it, and chose why. The item leaves the list and is never raised
    again; what happens to its source depends on the reason, never on a guess:
    - `no`: nothing is done. An ask's or a waiting task's task is set aside, a proposal is dropped, an access request is
      declined with no grant written, a confirm is accepted.
    - `done`: the owner did it themselves. Its task is completed, with that as evidence.
    - `stop-asking`: as `no`, and the owner wants no questions like it; the People review turns it into a dated line in
      Working together. Only this reason teaches the agent anything beyond the one item.
    Each is logged in Answered with its reason."""
    from . import tasks as task_mod
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")
    item = _pick(config, which, today)
    if item.origin == "consult":
        from . import consults
        consults.dismiss(config, item, today, reason)
        return item
    tid = None
    if item.origin == "current":
        _remove_bullet(config, item)
        source_task = TASK_SOURCE.search(item.source)
        tid = source_task[1] if item.kind == "ask" and source_task else None
    elif item.origin == "task":
        tid = item.source.rsplit("#", 1)[-1] if "#" in item.source else pathlib_stem(item.source)
    elif item.origin == "improvement":
        _drop_proposal(config, item, today)
    if tid:
        try:
            if reason == "done":
                task_mod.complete(config, tid, today, "the owner said they did it, in their app")
            else:
                task_mod.set_aside(config, tid, today, "dismissed by the owner in their app")
        except ValueError:
            pass
    text = item.text.replace("·", "-")
    _answered(config, today, f"- {today.isoformat()} · dismissed {reason} · {item.kind} · {text} · id:{item.id}"
              + (f" · [[{item.source}]]" if item.source != "CURRENT.md" else ""))
    return item


def pathlib_stem(source: str) -> str:
    return source.rsplit("/", 1)[-1].removesuffix(".md")


def _drop_proposal(config: Config, item: Item, today: date) -> None:
    rel, _, anchor = item.source.partition("#")
    path = config.vault_path / rel
    body = path.read_text(encoding="utf-8")
    heading = body.find("### " + anchor)
    if heading < 0:
        return
    following = re.compile(r"^#{2,3} ", re.M).search(body, heading + 4)
    end = following.start() if following else len(body)
    entry = body[heading:end]
    changed = STATUS_FIELD.sub(lambda m: m[0].replace(m[1], f"`dropped`, dismissed by the owner {today.isoformat()}"), entry, count=1)
    if changed == entry:
        changed = entry.rstrip("\n") + f"\n- **Status:** `dropped`, dismissed by the owner {today.isoformat()}\n\n"
    path.write_text(body[:heading] + changed + body[end:], encoding="utf-8")


def resolve(config: Config, which: str, because: str, today: date, *, record_answer: bool = True,
            owner_engaged: bool = False) -> Item:
    """Remove one item from CURRENT's Needs you because something answered it, and record what did, in
    `<agent_dir>/Answered/YYYY-MM.md`. `which` is an item id from `brain needs-you --json`, or text that matches exactly
    one open item. Tasks, proposals and access requests are resolved where they live, not here."""
    because = " ".join(because.replace("|", "/").split())
    if not because:
        raise ValueError("say what answered it: --because \"<the evidence>\"")
    item = _pick(config, which, today, prefer_current=True)
    if item.origin == "consult":
        raise ValueError("answer it with brain consult answer")
    if item.origin != "current":
        where = {"task": "change the task's state", "improvement": "set the proposal's status in its hub",
                 "access": "write or decline the grant (setup)"}[item.origin]
        raise ValueError(f"this item lives elsewhere: {where}")
    _remove_bullet(config, item)
    line = f"- {today.isoformat()} · {item.kind} · {item.text} · because {because}"
    if item.source != "CURRENT.md":
        line += f" · [[{item.source}]]"
    _answered(config, today, line)
    if record_answer:
        from . import continuation
        continuation.answered(config, item.source, item.text, because, owner_engaged)
    return item


def settle_tasks(config: Config, today: date, settled: list[tuple[str, str]], because: str) -> list[str]:
    """Take out of CURRENT's Needs you the asks that stood for tasks no longer waiting on the owner (answered, moved on
    or closed): a bullet whose source names the task, or that repeats its Ask. `settled` is (task id, the entry as it
    was when it waited). The caller holds the vault lock; each one is recorded as answered."""
    path = config.vault_path / "CURRENT.md"
    if not settled or not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    if span is None:
        return []
    asks = {tid: _plain(ask) for tid, entry in settled if (ask := _owner_lines(entry)[0])}
    def stood_for(item: Item) -> bool:
        named = set(re.findall(r"\bT-\d{3}\b", item.source))
        return any(tid in named or asks.get(tid) == _plain(item.text) for tid, _ in settled)
    new, removed = _without(body, span, stood_for)
    if removed:
        fm.write(path, {}, new)
        for item in removed:
            _answered(config, today, f"- {today.isoformat()} · {item.kind} · {item.text} · because {because}"
                      + (f" · [[{item.source}]]" if item.source != "CURRENT.md" else ""))
    return [item.text for item in removed]


def expire_confirms(config: Config, today: date) -> list[str]:
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    if span is None:
        return []
    new, removed = _without(body, span, lambda item: item.kind == "confirm"
                            and (today - date.fromisoformat(item.since)).days >= CONFIRM_DAYS)
    if removed:
        fm.write(path, {}, new)
    return [item.text for item in removed]


GENERIC_ASK = re.compile(
    r"^(?:do you |can you |could you |should i |shall i |want me to |is it ok(?:ay)? (?:if i |to )?)?"
    r"(?:approve|okay|ok|confirm|accept|allow|proceed(?: with)?|go ahead(?: with)?|do|continue(?: with)?)"
    r"(?:(?: with)? (?:the |this |that |my )?(?:proposed |suggested |pending |planned |above |next )?"
    r"(?:action|plan|change|step|request|option|proposal|item|task|idea|it|one)s?)?\s*\??$", re.I)


def vague_ask(text: str) -> str | None:
    """Why an ask cannot stand alone, read cold on a phone, or None when it can. An ask names its subject and the
    choice ("Want me to publish the 86 verified offers to In The Block now?"), never only "approve the proposed action"."""
    words = re.findall(r"[\w'’-]+", text)
    if GENERIC_ASK.match(" ".join(text.split())):
        return "it does not say what is being approved; name the subject and the choice"
    if len(words) < 2:
        return "it is too short to stand alone; name the subject and the choice"
    return None


def replace_task_asks(config: Config, body: str, tid: str, today: date) -> str:
    """CURRENT's body without the open asks that stand for `tid`, each recorded as replaced: a task carries one question
    at a time, its newest. The caller holds the vault lock and writes the body."""
    span = _section(body, "Needs you")
    if span is None:
        return body
    new, removed = _without(body, span, lambda item: item.kind == "ask" and item.task == tid)
    for item in removed:
        _answered(config, today, f"- {today.isoformat()} · {item.kind} · {item.text} · because a newer question about "
                  f"{tid} replaced it · [[{item.source}]]")
    return new
