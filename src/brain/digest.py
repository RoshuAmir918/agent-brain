"""Collect a Pacific day's user/assistant turns from Hermes session exports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import session_tools
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


@dataclass
class Session:
    title: str
    source: str
    turns: list[Turn]
    note: str = ""


def parse_timestamp(value) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        try:
            number = float(value)
        except ValueError:
            stamp = datetime.fromisoformat(value.strip().upper().replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=PACIFIC)
        else:
            seconds = number / 1000 if abs(number) >= 100_000_000_000 else number
            stamp = datetime.fromtimestamp(seconds, timezone.utc)
        return stamp.astimezone(PACIFIC)
    except (ValueError, OverflowError, OSError):
        return None


def timestamp_in(fields: dict, keys=TIME_KEYS) -> datetime | None:
    timings = fields.get("timings")
    for container in (fields, timings if isinstance(timings, dict) else {}):
        for key in keys:
            stamp = parse_timestamp(container.get(key))
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


def session_fallback(containers: list[dict], day: date) -> tuple[datetime | None, str]:
    starts = ("started_at", "start_time", "start", "created_at", "created", "timestamp", "ts", "time")
    ends = ("ended_at", "end_time", "end", "updated_at")
    start = next((stamp for fields in containers if (stamp := timestamp_in(fields, starts))), None)
    end = next((stamp for fields in containers if (stamp := timestamp_in(fields, ends))), None)
    start, end = start or end, end or start
    if start is None or end.timestamp() < start.timestamp() or not start.date() <= day <= end.date():
        return None, ""
    stamp = start if start.date() == day else datetime.combine(day, time(), PACIFIC)
    note = (f"Note: no usable message timestamps; session range {start.isoformat()} to "
            f"{end.isoformat()} covers {day}. Turn times are approximate.")
    return stamp, note


def select_session(record: dict, source: str, day: date, config: Config,
                   fallback_note: str = "") -> Session:
    containers = session_maps(record)
    messages = message_list(containers)
    timestamps = [timestamp_in(message) for message in messages]
    previous, note = (None, "") if any(timestamps) else session_fallback(containers, day)
    if note and fallback_note:
        note = fallback_note
    turns, pending_reads, pending_retrievals = [], [], []
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
            turns.append(Turn(role, text, previous, reads, retrievals))
    title = text_field(containers, ("title", "name", "session_title", "id", "session_id"), source)
    source = text_field(containers, ("source", "platform", "channel"), source)
    turns.sort(key=lambda turn: turn.timestamp.timestamp())
    return Session(title, source, turns, note)


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


def json_sessions(path: Path, day: date, config: Config) -> list[Session]:
    return [select_session(record, f"{path.name}:{number}", day, config=config)
            for number, record in json_records(path)]


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


def load_sessions(directory: Path, day: date, config: Config) -> list[Session]:
    if not directory.is_dir():
        raise ValueError(f"sessions directory does not exist: {directory}")
    path = directory / "all.jsonl"
    if path.exists():
        return json_sessions(path, day, config)
    return [markdown_session(path, directory, day, config) for path in sorted(directory.rglob("*.md"))]


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
          max_words: int = WORD_LIMIT, today: date | None = None) -> tuple[str, int]:
    directory = sessions_dir if sessions_dir is not None else config.vault_path / "Conversations/Sessions"
    sessions, words, omitted = limit_turns(load_sessions(directory, day, config), max_words)
    generated = today if today is not None else datetime.now(PACIFIC).date()
    sections = [FRONTMATTER, f"Digest for {day}, generated {generated} by brain "
                f"from {len(sessions)} sessions, {words} words"]
    for session in sessions:
        sections.append(f"## {session.title} ({session.source})")
        if session.note:
            sections.append(session.note)
        for turn in session.turns:
            speaker = config.name_for(turn.role)
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
