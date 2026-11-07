"""Export the harness's sessions without changing its records."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from . import fm, install, runtime
from .config import Config


def read(path: Path, content: bytes | None = None) -> dict:
    try:
        record = json.loads(path.read_bytes() if content is None else content)
    except ValueError as error:
        raise ValueError(f"{path}: invalid session JSON: {error}") from error
    if not isinstance(record, dict) or not isinstance(record.get("entries"), list):
        raise ValueError(f"{path}: expected a harness session with entries")
    speaker = record.get("speaker")
    if not all(isinstance(record.get(key), str) for key in ("id", "day", "startedAt")) \
            or not isinstance(speaker, dict) or not isinstance(speaker.get("platform"), str):
        raise ValueError(f"{path}: a harness session needs id, day, startedAt and speaker.platform")
    for entry in record["entries"]:
        item = entry.get("item") if isinstance(entry, dict) else None
        if not isinstance(entry.get("at") if isinstance(entry, dict) else None, str) \
                or not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError(f"{path}: every session entry needs at and item.type")
    return record


def transcript(record: dict) -> str:
    """The harness transcript shape, with role and full timestamps for the night's Markdown readers."""
    speaker = record["speaker"]
    who = speaker.get("userName") or speaker.get("userId") or ("owner" if record.get("owner") else "unknown")
    lines = [f"# {record['id']}", "", f"- platform: {speaker['platform']}",
             f"- speaker: {who}" + (" (owner)" if record.get("owner") else ""),
             f"- started: {record['startedAt']}", ""]
    for entry in record["entries"]:
        item, at = entry["item"], entry["at"]
        if item["type"] == "message":
            lines += [f"## {item['role']} · {at}", "", entry.get("said", item["text"]), ""]
        elif item["type"] == "tool_call":
            lines += [f"## tool · {at}", "", f"- {at} `{item['name']}` {item['arguments'][:500]}", ""]
    return "\n".join(lines)


def export(config: Config, day: date | None = None) -> tuple[int, int]:
    from .digest import parse_timestamp
    source = runtime.home() / "harness/sessions"
    records = install.sessions_records_path()
    planned = []
    for path in sorted(source.glob("*/*.json")):
        content = path.read_bytes()
        try:
            record = read(path, content)
        except ValueError as error:
            print(f"export-sessions: skipped {error}", file=sys.stderr)
            continue
        if day is not None and record["day"] != day.isoformat() and not any(
                (stamp := parse_timestamp(entry["at"], config.zone)) and stamp.date() == day
                for entry in record["entries"]):
            continue
        name = record["id"]
        if not re.fullmatch(r"[\w.-]+", name) or name in (".", ".."):
            print(f"export-sessions: skipped {path}: invalid session id: {name!r}", file=sys.stderr)
            continue
        target = records if record["speaker"]["platform"] == "cron" else config.vault_path / "Conversations/Sessions"
        planned += [(target / f"{name}.md", transcript(record).encode("utf-8")),
                    (records / "jsonl" / f"{name}.json", content)]
    written = unchanged = 0
    for target, content in planned:
        if target.exists() and target.read_bytes() == content:
            unchanged += 1
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            fm.write_text(target, content)
            written += 1
    return written, unchanged
