"""Defensive tool-call observations shared by session digests and attribution."""
from __future__ import annotations

import json
import re
import shlex

from .config import Config
ARGUMENT_KEYS = ("arguments", "args", "input", "parameters")
EVALUATION_RE = re.compile(r"evaluation fixture|retrieval[- ](check|test)|\bRC-\d{2}\b", re.I)
ROUTINE_RE = re.compile(r"^cron\b|^cron_", re.I)


def session_kind(containers: list[dict], messages: list[dict]) -> str:
    """conversation, routine (a cron run: the agent working alone) or evaluation (a retrieval check: earns nothing)."""
    def field(keys):
        for key in keys:
            for container in containers:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return ""
    title, source, session_id = field(("title", "name", "session_title")), field(("source", "platform", "channel")), field(("id", "session_id"))
    first_user = next((message for message in messages if str(message.get("role", "")).lower() == "user"), None)
    first_text = first_user.get("content", first_user.get("text", "")) if first_user else ""
    first_text = first_text if isinstance(first_text, str) else ""
    if EVALUATION_RE.search(title) or EVALUATION_RE.search(source) or first_text.lstrip().lower().startswith("evaluation fixture:"):
        return "evaluation"
    if ROUTINE_RE.search(source) or ROUTINE_RE.search(title) or ROUTINE_RE.search(session_id):
        return "routine"
    return "conversation"


def call_arguments(value):
    if isinstance(value, dict):
        name = value.get("tool_name", value.get("name", value.get("tool", "")))
        for key in ARGUMENT_KEYS:
            if key in value:
                yield name, value[key]
                break
        for key, child in value.items():
            if key not in ARGUMENT_KEYS:
                yield from call_arguments(child)
    elif isinstance(value, list):
        for child in value:
            yield from call_arguments(child)


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
            if token in (";", "&&", "||", "|", "&", "<", ">", ">>"):
                break
            if token in ("--vault", "--limit"):
                next(tokens, None)
            elif token == "--":
                query.extend(tokens)
                break
            elif token != "--json" and not token.startswith(("--vault=", "--limit=")):
                query.append(token)
        return " ".join(query)
    except ValueError:
        return ""


def activity(message: dict, config: Config) -> tuple[list[str], list[str]]:
    if str(message.get("role", "")).strip().lower() != "assistant":
        return [], []
    if str(message.get("compacted", False)).lower() in ("true", "1"):
        return [], []
    prefix = re.escape(config.vault + "/")
    note_pattern = re.compile(prefix + r'''(?:(?!''' + prefix + r''')[^"'\r\n])*?\.md''')
    reads, retrievals = [], []
    for name, arguments in call_arguments(message):
        strings = list(argument_strings(arguments))
        queries = [retrieval_query(text) for text in strings] if name == "terminal" else []
        if any(query is not None for query in queries):
            retrievals.extend(query for query in queries if query is not None)
            continue
        for text in strings:
            reads.extend(match[0] for match in note_pattern.finditer(text))
    return list(dict.fromkeys(reads)), retrievals
