"""Harness tool-call observations shared by session digests and attribution."""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime
from itertools import takewhile
from pathlib import Path

from .config import Config
from .runtime import ROUTINE_PLATFORMS
EVALUATION_RE = re.compile(r"evaluation fixture|retrieval[- ](check|test)|\bRC-\d{2}\b", re.I)
SHELL_OPERATORS = (";", "&&", "||", "|", "&", "<", ">", ">>")
WRITERS = ("write_file", "patch", "computer_write_file")   # a path in what they write is not a read


def _harness_unanswered(folder: Path, now: datetime) -> list[tuple[str, str]]:
    """The harness keeps each conversation as `harness/sessions/<day>/<id>.json`, entries timed in ISO."""
    floor = now.timestamp() // 60 * 60
    found = []
    for day in sorted(path for path in folder.iterdir() if path.is_dir())[-2:]:
        for path in sorted(day.glob("*.json")):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
                platform = str((session.get("speaker") or {}).get("platform") or "")
                said = [entry for entry in session.get("entries") or [] if (entry.get("item") or {}).get("type") == "message"]
                if platform in ROUTINE_PLATFORMS or not said or said[-1]["item"].get("role") != "user":
                    continue
                at = datetime.fromisoformat(str(said[-1]["at"]).replace("Z", "+00:00")).timestamp()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
            if floor - 24 * 60 * 60 < at < floor - 10 * 60:
                found.append((str(session.get("id") or path.stem), platform))
    return sorted(found)


def unanswered(home: Path, now: datetime) -> list[tuple[str, str]]:
    """Recent conversations whose last stored message is an unattended user turn. The store is optional."""
    harness = home / "harness" / "sessions"
    return _harness_unanswered(harness, now) if harness.is_dir() else []


def session_kind(record: dict) -> str:
    """Retrieval evaluations earn nothing; only cron sessions are routine runs."""
    first = next((entry.get("said", entry["item"].get("text", "")) for entry in record["entries"]
                  if entry["item"]["type"] == "message" and entry["item"].get("role") == "user"), "")
    if EVALUATION_RE.search(record["id"]) or first.lstrip().lower().startswith("evaluation fixture:"):
        return "evaluation"
    return "routine" if record["speaker"]["platform"] == "cron" else "conversation"


def argument_strings(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from argument_strings(key)
            yield from argument_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from argument_strings(child)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if isinstance(decoded, (dict, list, str)) and decoded != value:
            yield from argument_strings(decoded)
        else:
            yield value


def retrieval_query(command: str) -> str | None:
    match = re.search(r"\bbrain\s+retrieve\b", command)
    if not match:
        return None
    try:
        lexer = shlex.shlex(command[match.end():], posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = iter(lexer)
        query = []
        for token in tokens:
            if token in SHELL_OPERATORS:
                break
            if token in ("--vault", "--limit", "--form"):
                next(tokens, None)
            elif token == "--":
                query.extend(takewhile(lambda rest: rest not in SHELL_OPERATORS, tokens))
                break
            elif token != "--json" and not token.startswith(("--vault=", "--limit=", "--form=")):
                query.append(token)
        return " ".join(query)
    except ValueError:
        return ""


def activity(item: dict, config: Config) -> tuple[list[str], list[str]]:
    if item["type"] != "tool_call" or item["name"] in WRITERS or item["name"].startswith("demiurge_"):
        return [], []
    strings = list(argument_strings(item["arguments"]))
    queries = [retrieval_query(text) for text in strings] if item["name"] in ("terminal", "exec_command") else []
    if any(query is not None for query in queries):
        return [], [query for query in queries if query is not None]
    prefix = re.escape(config.vault + "/")
    note_pattern = re.compile(prefix + r"(?:(?!" + prefix + r")[^\"'\r\n])*?\.md")
    reads = [match[0] for text in strings for match in note_pattern.finditer(text)]
    return list(dict.fromkeys(reads)), []
