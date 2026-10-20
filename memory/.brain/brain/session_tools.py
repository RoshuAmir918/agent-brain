"""Defensive tool-call observations shared by session digests and attribution."""
from __future__ import annotations

import json
import re
import shlex

NOTE_RE = re.compile(r'''memory/(?:(?!memory/)[^"'\r\n])*?\.md''')
ARGUMENT_KEYS = ("arguments", "args", "input", "parameters")


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


def activity(message: dict) -> tuple[list[str], list[str]]:
    if str(message.get("role", "")).strip().lower() != "assistant":
        return [], []
    if str(message.get("compacted", False)).lower() in ("true", "1"):
        return [], []
    reads, retrievals = [], []
    for name, arguments in call_arguments(message):
        strings = list(argument_strings(arguments))
        queries = [retrieval_query(text) for text in strings] if name == "terminal" else []
        if any(query is not None for query in queries):
            retrievals.extend(query for query in queries if query is not None)
            continue
        for text in strings:
            reads.extend(match[0] for match in NOTE_RE.finditer(text))
    return list(dict.fromkeys(reads)), retrievals
