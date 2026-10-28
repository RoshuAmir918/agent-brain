"""Collect a Pacific day's user/assistant turns from Hermes session exports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import install, session_tools
from .attribute import ROLE_RE, fallback_date, last_timestamp
from .config import Config

PACIFIC = ZoneInfo("America/Los_Angeles")
WORD_LIMIT = 25000
TIME_KEYS = ("timestamp", "created_at", "created", "ts", "time", "sent_at",
             "started_at", "start_time", "start", "ended_at", "end_time", "end")
FRONTMATTER = "---\nkind: log\ntier: cold\npinned: false\nuses: []\nuses_inferred: []\n---\n"


@dataclass
class Turn:
    role: str
    text: str
    timestamp: datetime
    reads: tuple[str, ...] = ()
    retrievals: tuple[str, ...] = ()
    speaker: str = ""


@dataclass
class Session:
    title: str
    source: str
    turns: list[Turn]
    note: str = ""


def parse_timestamp(value, zone: ZoneInfo = PACIFIC) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        try:
            number = float(value)
        except ValueError:
            stamp = datetime.fromisoformat(value.strip().upper().replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=zone)
        else:
            seconds = number / 1000 if abs(number) >= 100_000_000_000 else number
            stamp = datetime.fromtimestamp(seconds, timezone.utc)
        return stamp.astimezone(zone)
    except (ValueError, OverflowError, OSError):
        return None


def timestamp_in(fields: dict, keys=TIME_KEYS, zone: ZoneInfo = PACIFIC) -> datetime | None:
    timings = fields.get("timings")
    for container in (fields, timings if isinstance(timings, dict) else {}):
        for key in keys:
            stamp = parse_timestamp(container.get(key), zone)
            if stamp is not None:
                return stamp
    return None


def session_maps(record: dict) -> list[dict]:
    containers = [record]
    for key in ("session", "metadata", "data", "conversation"):
        if isinstance(record.get(key), dict):
            containers.append(record[key])
    for container in containers[1:]:
        if isinstance(container.get("metadata"), dict):
            containers.append(container["metadata"])
    return containers


def text_field(containers: list[dict], keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        for container in containers:
            value = container.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                return " ".join(str(value).split())
    return default


def message_list(containers: list[dict]) -> list[dict]:
    for container in containers:
        for key in ("messages", "history", "turns", "conversation"):
            if isinstance(container.get(key), list):
                return [message for message in container[key] if isinstance(message, dict)]
    return []


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type", "text") in ("text", "input_text", "output_text"):
            text = block.get("text", "")
            if isinstance(text, dict):
                text = text.get("value", "")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def session_fallback(containers: list[dict], day: date, zone: ZoneInfo = PACIFIC) -> tuple[datetime | None, str]:
    starts = ("started_at", "start_time", "start", "created_at", "created", "timestamp", "ts", "time")
    ends = ("ended_at", "end_time", "end", "updated_at")
    start = next((stamp for fields in containers if (stamp := timestamp_in(fields, starts, zone))), None)
    end = next((stamp for fields in containers if (stamp := timestamp_in(fields, ends, zone))), None)
    start, end = start or end, end or start
    if start is None or end.timestamp() < start.timestamp() or not start.date() <= day <= end.date():
        return None, ""
    stamp = start if start.date() == day else datetime.combine(day, time(), zone)
    note = (f"Note: no usable message timestamps; session range {start.isoformat()} to "
            f"{end.isoformat()} covers {day}. Turn times are approximate.")
    return stamp, note


SPEAKER_KEYS = ("user_name", "username", "display_name", "author", "sender", "from", "user", "user_id")
# In a session shared by several people (a group room or thread), Hermes writes the speaker in front of each
# message: "[Arslan Amir] are we still on for friday". When every user message carries one, it names the speaker.
SPEAKER_PREFIX = re.compile(r"^\[([^\]\n]{1,80})\][ \t]+")


def shared_session(containers: list[dict], messages: list[dict]) -> bool:
    """A group session (Hermes records the chat type), or, without that, every one of several user messages prefixed."""
    if text_field(containers, ("chat_type",), "").lower() in ("group", "channel", "thread"):
        return True
    said = [content_text(m.get("content", m.get("text", ""))) for m in messages if str(m.get("role", "")).strip().lower() == "user"]
    said = [text for text in said if text]
    return len(said) >= 2 and all(SPEAKER_PREFIX.match(text) for text in said)


def select_session(record: dict, source: str, day: date, config: Config,
                   fallback_note: str = "") -> Session:
    containers = session_maps(record)
    messages = message_list(containers)
    timestamps = [timestamp_in(message, zone=config.zone) for message in messages]
    previous, note = (None, "") if any(timestamps) else session_fallback(containers, day, config.zone)
    if note and fallback_note:
        note = fallback_note
    turns, pending_reads, pending_retrievals = [], [], []
    shared = shared_session(containers, messages)
    for message, stamp in zip(messages, timestamps):
        previous = stamp or previous
        role = str(message.get("role", "")).strip().lower()
        compacted = str(message.get("compacted", False)).lower() in ("true", "1")
        reads, retrievals = session_tools.activity(message, config)
        pending_reads.extend(reads)
        pending_retrievals.extend(retrievals)
        text = content_text(message.get("content", message.get("text", "")))
        reads, retrievals = (), ()
        if role == "assistant" and text:
            reads, retrievals = tuple(dict.fromkeys(pending_reads)), tuple(pending_retrievals)
            pending_reads, pending_retrievals = [], []
        if previous is None or previous.date() != day or role not in ("user", "assistant") or compacted:
            continue
        if text:
            speaker = text_field([message], SPEAKER_KEYS, "") if role == "user" else ""
            prefix = SPEAKER_PREFIX.match(text) if role == "user" and shared else None
            if prefix:
                speaker, text = speaker or prefix.group(1).strip(), text[prefix.end():]
            turns.append(Turn(role, text, previous, reads, retrievals, speaker))
    title = text_field(containers, ("title", "name", "session_title", "chat_name", "id", "session_id"), source)
    source = text_field(containers, ("source", "platform", "channel"), source)
    turns.sort(key=lambda turn: turn.timestamp.timestamp())
    return Session(title, source, turns, note)


def record_id(record: dict) -> str:
    return text_field(session_maps(record), ("session_id", "id", "sessionId"), "")


def session_ids(path: Path) -> list[str]:
    """Distinct session ids in a JSONL export, in file order, for per-session Markdown exports."""
    return [session_id for session_id, _ in session_kinds(path)]


def session_kinds(path: Path) -> list[tuple[str, str]]:
    """Each session id once with its kind: conversation, routine or evaluation."""
    kinds: dict[str, str] = {}
    for _, record in json_records(path):
        value = record_id(record)
        if value and value not in kinds:
            containers = session_maps(record)
            kinds[value] = session_tools.session_kind(containers, message_list(containers))
    return list(kinds.items())


def default_sessions_dir(config: Config) -> Path:
    """This profile's records folder; the vault's Sessions folder only while an older export still writes there."""
    records = install.sessions_records_path()
    legacy = config.vault_path / "Conversations/Sessions"
    return records if records.is_dir() or not legacy.is_dir() else legacy


def jsonl_sources(directory: Path) -> list[Path]:
    """Per-session shards under jsonl/, then a legacy all.jsonl if one is still present."""
    shards = sorted((directory / "jsonl").glob("*.jsonl")) if (directory / "jsonl").is_dir() else []
    legacy = directory / "all.jsonl"
    return shards + ([legacy] if legacy.exists() else [])


def shard(export: Path, directory: Path) -> tuple[int, int]:
    """Split a full JSONL export into jsonl/<id>.jsonl, rewriting only shards whose bytes changed.

    Unchanged sessions then leave git untouched, so a night's push carries only the sessions that moved.
    Returns (written, unchanged)."""
    target = directory / "jsonl"
    target.mkdir(parents=True, exist_ok=True)
    written = unchanged = 0
    for number, record in json_records(export):
        name = re.sub(r"[^\w.-]", "_", record_id(record) or f"record-{number}") + ".jsonl"
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = target / name
        if path.exists() and path.read_bytes() == encoded:
            unchanged += 1
            continue
        path.write_bytes(encoded)
        written += 1
    return written, unchanged


def json_records(path: Path):
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as error:
                raise ValueError(f"{path}:{number}: invalid session JSON: {error}") from error
            if isinstance(record, dict):
                yield number, record


def json_sessions(paths: list[Path], day: date, config: Config, include_routines: bool = False) -> tuple[list[Session], dict]:
    sessions, skipped = [], {"evaluation": 0, "routine": 0}
    for path, number, record in ((path, number, record) for path in paths for number, record in json_records(path)):
        containers = session_maps(record)
        kind = session_tools.session_kind(containers, message_list(containers))
        if kind == "evaluation" or (kind == "routine" and not include_routines):
            skipped[kind] += 1
            continue
        sessions.append(select_session(record, f"{path.name}:{number}", day, config=config))
    return sessions, skipped


def markdown_marker(line: str) -> tuple[str, str, str | None] | None:
    marker = ROLE_RE.match(line)
    if not marker:
        return None
    role = marker[1].lower()
    stamp = last_timestamp(line)
    tail = re.sub(r"^(?:\*\*)?:?(?:\*\*)?[ \t]*", "", line[marker.end():])
    if re.match(r"^\s*(?:-\s+)?role\s*:", line, re.I):
        tail = ""
    elif stamp and tail.replace(stamp, "", 1).strip(" \t\r\n*`()[]:—-|") == "":
        tail = ""
    elif not tail.strip():
        tail = ""
    return ("user" if role == "human" else role), tail, stamp


def markdown_timestamp(line: str) -> str | None:
    cleaned = re.sub(r"^\s*(?:[-*]\s+)?(?:\*\*)?(?:timestamp|created_at|created|ts|time|sent_at)"
                     r"(?:\*\*)?\s*:(?:\*\*)?[ \t]*", "", line, flags=re.I)
    stamp = last_timestamp(cleaned)
    return stamp if stamp and cleaned.strip().strip("*`()[] ") == stamp else None


def markdown_fence(line: str, fence: str) -> str:
    if fence:
        closing = r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\r?\n?"
        return "" if re.fullmatch(closing, line) else fence
    opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
    if opening and not (opening[1][0] == "`" and "`" in opening[2]):
        return opening[1]
    return ""


def markdown_messages(text: str) -> list[dict]:
    messages, current, timestamp, fence = [], None, None, ""
    for line in text.splitlines(keepends=True):
        following = markdown_fence(line, fence)
        if fence or following:
            fence = following
            if current is not None:
                current["content"] += line
            continue
        marker = markdown_marker(line)
        if marker:
            if current is not None:
                messages.append(current)
            role, tail, stamp = marker
            timestamp = stamp or timestamp
            current = {"role": role, "content": tail, "timestamp": timestamp}
        elif (current is None or not current["content"].strip()) and (stamp := markdown_timestamp(line)):
            timestamp = stamp
            if current is not None and not current["content"].strip():
                current["timestamp"] = stamp
        elif current is not None:
            current["content"] += line
    if current is not None:
        messages.append(current)
    return messages


def markdown_session(path: Path, directory: Path, day: date, config: Config) -> Session:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    preamble = []
    for line in text.splitlines():
        if ROLE_RE.match(line):
            break
        preamble.append(line)
    heading = re.search(r"^# (.+)$", "\n".join(preamble), re.M)
    record = {"title": heading[1] if heading else path.stem,
              "messages": markdown_messages(text), "started_at": fallback_date(path)}
    note = ("Note: no usable message timestamps; included using the filename, dated parent "
            "or file mtime date. Turn times are approximate.")
    return select_session(record, path.relative_to(directory).as_posix(), day, config, note)


def markdown_kind(path: Path) -> str:
    head = path.read_text(encoding="utf-8-sig", errors="replace")[:4000]
    if session_tools.EVALUATION_RE.search(path.stem) or "evaluation fixture:" in head.lower():
        return "evaluation"
    return "routine" if session_tools.ROUTINE_RE.search(path.stem) else "conversation"


def load_sessions(directory: Path, day: date, config: Config, include_routines: bool = False) -> tuple[list[Session], dict]:
    if not directory.is_dir():
        raise ValueError(f"sessions directory does not exist: {directory}")
    sources = jsonl_sources(directory)
    if sources:
        return json_sessions(sources, day, config, include_routines)
    sessions, skipped = [], {"evaluation": 0, "routine": 0}
    for file in sorted(directory.rglob("*.md")):
        kind = markdown_kind(file)
        if kind == "evaluation" or (kind == "routine" and not include_routines):
            skipped[kind] += 1
            continue
        sessions.append(markdown_session(file, directory, day, config))
    return sessions, skipped


def limit_turns(sessions: list[Session], max_words: int) -> tuple[list[Session], int, int]:
    if max_words < 0:
        raise ValueError("--max-words must be nonnegative")
    ordered = [(turn.timestamp.timestamp(), number, position, turn)
               for number, session in enumerate(sessions) for position, turn in enumerate(session.turns)]
    ordered.sort(key=lambda item: item[:3])
    kept, words, count = {}, 0, 0
    for _, number, _, turn in ordered:
        size = len(turn.text.split())
        if words + size > max_words:
            break
        kept.setdefault(number, []).append(turn)
        words += size
        count += 1
    selected = [replace(sessions[number], turns=turns) for number, turns in kept.items()]
    selected.sort(key=lambda session: session.turns[0].timestamp.timestamp())
    return selected, words, len(ordered) - count


def build(config: Config, day: date, sessions_dir: Path | None = None,
          max_words: int = WORD_LIMIT, today: date | None = None, include_routines: bool = False) -> tuple[str, int]:
    directory = sessions_dir if sessions_dir is not None else default_sessions_dir(config)
    loaded, skipped = load_sessions(directory, day, config, include_routines)
    sessions, words, omitted = limit_turns(loaded, max_words)
    generated = today if today is not None else config.today()
    sections = [FRONTMATTER, f"Digest for {day}, generated {generated} by brain "
                f"from {len(sessions)} sessions, {words} words",
                f"coverage: {len(sessions)} sessions included, {skipped['routine']} routine sessions and "
                f"{skipped['evaluation']} evaluation sessions excluded, {omitted} turns omitted by the word cap"]
    for session in sessions:
        sections.append(f"## {session.title} ({session.source})")
        if session.note:
            sections.append(session.note)
        for turn in session.turns:
            speaker = turn.speaker or config.name_for(turn.role)   # a platform user id or name when the export has one
            text = f"**{speaker}** {turn.timestamp:%H:%M}\n{turn.text}"
            if turn.reads:
                text += "\nreads: " + ", ".join(turn.reads)
            for query in turn.retrievals:
                text += "\nretrieve: " + json.dumps(query, ensure_ascii=False)
            sections.append(text)
    if omitted:
        sections.append(f"truncated: {omitted} further turns omitted; read the session export for the rest")
    return "\n\n".join(sections) + "\n", words


def write(config: Config, day: date, text: str) -> Path:
    path = config.vault_path / "Conversations/Digests" / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if not path.exists() or path.read_bytes() != encoded:
        path.write_bytes(encoded)
    return path
