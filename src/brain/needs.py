"""The owner's computed queue: current asks, waiting tasks and proposals."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath

from . import access, access_log
from .config import Config
from .tasks import archive_path, load_tasks
from .vault import Vault

KINDS = ("ask", "confirm")
OPEN_ASKS = 5        # asks open in CURRENT at once: a sixth means deciding or retiring one first
STALE_DAYS = 14      # an ask unanswered this long is the evening's to decide or retire
RETIRE_DAYS = 28     # an ask unanswered this long leaves the list; silence is an answer
LISTED_PROPOSALS = 2 # proposed improvements shown to the owner at once: the newest, from the last RETIRE_DAYS days
DATED_HEADING_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December) \d{1,2}(?:, \d{4})?) — (.+)$")


@dataclass
class Item:
    id: str = field(init=False)
    kind: str
    text: str
    origin: str
    since: str | None
    source: str

    def __post_init__(self) -> None:
        key = json.dumps([self.origin, self.source, self.text], ensure_ascii=False)
        self.id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


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


def parse_section(body: str) -> list[Item]:
    span = _section(body, "Needs you")
    if span is None:
        return []
    return [item for line in body[span[0]:span[1]].splitlines()
            if (item := _bullet(line)) is not None]


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
        out.append(Item("ask", "Adopt? " + title, "improvement", since, source + "#" + anchor))
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
    register = config.agent("Tasks.md")
    if (config.vault_path / register).exists():
        tasks, archived = load_tasks(config)
        registered = {task.tid for task in tasks}
        for task in tasks + archived:
            if task.task_state != "waiting_for_owner":
                continue
            source = (register + "#" + task.tid if task.tid in registered else
                      archive_path(config, task.tid).relative_to(config.vault_path).as_posix())
            out.append(Item("ask", task.title, "task", task.last_evidence_date() or None, source))
    proposals = [item for rel, note in vault.notes.items() if vault.kind_for(rel) == "hub"
                 for item in _improvements(note.body, rel, today)]
    # A proposal stays in its hub as long as it stands; the owner is shown the newest few, none past four weeks, and an
    # undated one only when nothing newer fills the room.
    recent = [item for item in proposals
              if item.since is None or (today - date.fromisoformat(item.since)).days < RETIRE_DAYS]
    shown = {id(item) for item in sorted(recent, key=lambda item: item.since or "", reverse=True)[:LISTED_PROPOSALS]}
    out.extend(item for item in recent if id(item) in shown)
    out.extend(_access_items(vault, config, today))
    gone = dismissed_ids(config)
    return sorted((item for item in out if item.id not in gone), key=lambda item: KINDS.index(item.kind))


def open_asks(body: str) -> list[Item]:
    return [item for item in parse_section(body) if item.kind == "ask"]


def stale(config: Config, today: date, days: int = STALE_DAYS) -> list[Item]:
    """Asks in CURRENT unanswered for `days` or more, oldest first."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    old = [item for item in open_asks(body) if (today - date.fromisoformat(item.since)).days >= days]
    return sorted(old, key=lambda item: item.since)


def retire_asks(config: Config, today: date, days: int = RETIRE_DAYS) -> list[str]:
    """Remove asks unanswered for `days` or more: the backstop when no routine decided them. Returns their texts."""
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    if span is None:
        return []
    kept, removed = [], []
    for line in body[span[0]:span[1]].splitlines(keepends=True):
        item = _bullet(line)
        if item and item.kind == "ask" and (today - date.fromisoformat(item.since)).days >= days:
            removed.append(item.text)
        else:
            kept.append(line)
    if removed:
        path.write_bytes((body[:span[0]] + "".join(kept) + body[span[1]:]).encode("utf-8"))
    return removed


ANSWERED_HEADER = "---\nkind: log\ntier: cold\n---\n# Answered\n\nAsks and confirms resolved by evidence, one line each: date, kind, what it was, the evidence.\n\n"


DISMISSED = re.compile(r"· dismissed(?: [a-z-]+)? · .* · id:([0-9a-f]{12})\b")
REASONS = ("no", "done", "stop-asking")   # what the owner said by dismissing: never inferred, only chosen


def dismissed_ids(config: Config) -> set[str]:
    """Items the owner dismissed, from the Answered logs: computed items (an access request) would otherwise return."""
    folder = config.vault_path / config.agent("Answered")
    if not folder.is_dir():
        return set()
    return {m[1] for path in folder.glob("*.md") for m in DISMISSED.finditer(path.read_text(encoding="utf-8"))}


def _pick(config: Config, which: str, today: date) -> Item:
    candidates = items(config, today)
    chosen = [item for item in candidates if item.id == which]
    if not chosen:
        needle = " ".join(which.split()).casefold()
        chosen = [item for item in candidates if needle and needle in item.text.casefold()]
    if len(chosen) != 1:
        raise ValueError(("no open item matches " if not chosen else f"{len(chosen)} open items match ") + repr(which)
                         + "; use an id from `brain needs-you --json`")
    return chosen[0]


def _remove_bullet(config: Config, item: Item) -> None:
    path = config.vault_path / "CURRENT.md"
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    kept, removed = [], False
    for line in body[span[0]:span[1]].splitlines(keepends=True):
        bullet = _bullet(line)
        if not removed and bullet and bullet.id == item.id:
            removed = True
            continue
        kept.append(line)
    path.write_bytes((body[:span[0]] + "".join(kept) + body[span[1]:]).encode("utf-8"))


def _answered(config: Config, today: date, line: str) -> None:
    log = config.vault_path / config.agent("Answered") / f"{today:%Y-%m}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text(ANSWERED_HEADER, encoding="utf-8")
    with log.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


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


def resolve(config: Config, which: str, because: str, today: date) -> Item:
    """Remove one item from CURRENT's Needs you because something answered it, and record what did, in
    `<agent_dir>/Answered/YYYY-MM.md`. `which` is an item id from `brain needs-you --json`, or text that matches exactly
    one open item. Tasks, proposals and access requests are resolved where they live, not here."""
    because = " ".join(because.replace("|", "/").split())
    if not because:
        raise ValueError("say what answered it: --because \"<the evidence>\"")
    item = _pick(config, which, today)
    if item.origin != "current":
        where = {"task": "change the task's state", "improvement": "set the proposal's status in its hub",
                 "access": "write or decline the grant (setup)"}[item.origin]
        raise ValueError(f"this item lives elsewhere: {where}")
    _remove_bullet(config, item)
    line = f"- {today.isoformat()} · {item.kind} · {item.text} · because {because}"
    if item.source != "CURRENT.md":
        line += f" · [[{item.source}]]"
    _answered(config, today, line)
    return item


def expire_confirms(config: Config, today: date) -> list[str]:
    path = config.vault_path / "CURRENT.md"
    if not path.exists():
        return []
    body = path.read_bytes().decode("utf-8")
    span = _section(body, "Needs you")
    if span is None:
        return []
    kept, removed = [], []
    for line in body[span[0]:span[1]].splitlines(keepends=True):
        item = _bullet(line)
        if item and item.kind == "confirm" and (today - date.fromisoformat(item.since)).days >= 7:
            removed.append(item.text)
        else:
            kept.append(line)
    if removed:
        path.write_bytes((body[:span[0]] + "".join(kept) + body[span[1]:]).encode("utf-8"))
    return removed
