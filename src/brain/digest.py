"""Collect a Pacific day's user/assistant turns from Session exports."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import fm, install, session_tools, sessions
from .config import Config

WORD_LIMIT = 25000
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


def parse_timestamp(value, zone: ZoneInfo) -> datetime | None:
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


def select_session(record: dict, source: str, day: date, config: Config) -> Session:
    turns, pending_reads, pending_retrievals = [], [], []
    for entry in record["entries"]:
        item = entry["item"]
        reads, retrievals = session_tools.activity(item, config)
        pending_reads.extend(reads)
        pending_retrievals.extend(retrievals)
        if item["type"] != "message":
            continue
        role, text = item["role"], entry.get("said", item["text"])
        stamp = parse_timestamp(entry["at"], config.zone)
        reads, retrievals = (), ()
        if role == "assistant" and text:
            reads, retrievals = tuple(dict.fromkeys(pending_reads)), tuple(pending_retrievals)
            pending_reads, pending_retrievals = [], []
        if stamp and stamp.date() == day and role in ("user", "assistant") and text:
            speaker = ""
            if role == "user" and not record.get("owner"):   # never the owner's name for someone else's words
                who = record["speaker"]
                speaker = who.get("userName") or (who.get("userId") and f"{who['platform']}:{who['userId']}") or "unknown speaker"
            turns.append(Turn(role, text, stamp, reads, retrievals, speaker))
    turns.sort(key=lambda turn: turn.timestamp.timestamp())
    return Session(record["id"], record["speaker"]["platform"], turns)


def json_sources(directory: Path) -> list[Path]:
    """The harness's unchanged session JSON, archived one shard per session."""
    return sorted((directory / "jsonl").glob("*.json"))


def json_sessions(paths: list[Path], day: date, config: Config, include_routines: bool = False) -> tuple[list[Session], dict]:
    found, skipped = [], {"evaluation": 0, "routine": 0}
    for path in paths:
        try:
            record = sessions.read(path)
        except ValueError as error:
            print(f"digest: skipped {error}", file=sys.stderr)
            continue
        kind = session_tools.session_kind(record)
        if kind == "evaluation" or (kind == "routine" and not include_routines):
            skipped[kind] += 1
            continue
        found.append(select_session(record, path.name, day, config))
    return found, skipped


def load_sessions(directory: Path, day: date, config: Config, include_routines: bool = False) -> tuple[list[Session], dict]:
    if not directory.is_dir():
        raise ValueError(f"sessions directory does not exist: {directory}")
    return json_sessions(json_sources(directory), day, config, include_routines)


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
    directory = sessions_dir if sessions_dir is not None else install.sessions_records_path()
    loaded, skipped = load_sessions(directory, day, config, include_routines)
    sessions, words, omitted = limit_turns(loaded, max_words)
    generated = today if today is not None else config.today()
    sections = [FRONTMATTER, f"Digest for {day}, generated {generated} by brain "
                f"from {len(sessions)} sessions, {words} words",
                f"coverage: {len(sessions)} sessions included, {skipped['routine']} routine sessions and "
                f"{skipped['evaluation']} evaluation sessions excluded, {omitted} turns omitted by the word cap"]
    for session in sessions:
        sections.append(f"## {session.title} ({session.source})")
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
        fm.write_text(path, encoded)
    return path
