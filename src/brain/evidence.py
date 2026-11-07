"""The engine's one evidence contract (docs/image.md section 4): dated facts each source writes outside the vault."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from . import runtime
from .lock import evidence_lock

SEP = " · "
SOURCE = re.compile(r"[a-z][a-z0-9-]*")
MONTH = re.compile(r"\d{4}-\d{2}\.md")


@dataclass
class Evidence:
    date: date
    verb: str
    fact: str
    whose: str
    hints: tuple[str, ...]
    pointer: str
    extra: dict[str, str] = field(default_factory=dict)
    source: str = ""


def _field(label: str, value: str) -> str:
    value = " ".join(str(value).replace(SEP.strip(), "-").split())
    if not value:
        raise ValueError(f"evidence {label} is empty")
    return value


def render(row: Evidence) -> str:
    """One line; separators are stripped from every field so parsing stays positional."""
    if not re.fullmatch(r"[a-z][a-z-]*", row.verb):
        raise ValueError(f"evidence verb must be one lowercase word: {row.verb!r}")
    if row.whose != "own" and not row.whose.startswith("other:"):
        raise ValueError("evidence whose must be own or other:<who>")
    if any("=" in key or " " in key for key in row.extra):
        raise ValueError("evidence keys are single words")
    hints = ", ".join(_field("hint", hint).replace(",", " ") for hint in row.hints)
    extra = [f"{_field('key', key)}={_field(key, value)}" for key, value in row.extra.items()]
    return "- " + SEP.join([row.date.isoformat(), row.verb, _field("fact", row.fact), _field("whose", row.whose),
                            _field("hints", hints), _field("pointer", row.pointer), *extra])


def parse(line: str, source: str = "") -> Evidence | None:
    if not line.startswith("- "):
        return None
    parts = line[2:].rstrip("\n").split(SEP)
    if len(parts) < 6:
        return None
    try:
        day = date.fromisoformat(parts[0])
    except ValueError:
        return None
    extra = dict(part.split("=", 1) for part in parts[6:] if "=" in part)
    return Evidence(day, parts[1], parts[2], parts[3], tuple(hint.strip() for hint in parts[4].split(",") if hint.strip()),
                    parts[5], extra, source)


def folder(home: Path | None = None) -> Path | None:
    home = home or runtime.configured()
    return home / "evidence" if home else None


def append(source: str, rows: Iterable[Evidence], home: Path | None = None) -> int:
    """Append new rows to each month's file, skipping exact repeats. Without an agent workspace there is nowhere to
    keep evidence, so nothing is written: the vault stays the record and evidence is rebuilt by extracting again."""
    if not SOURCE.fullmatch(source):
        raise ValueError(f"evidence source must be a lowercase name: {source!r}")
    root = folder(home)
    lines = [(row.date, render(row)) for row in rows]
    if root is None or not lines:
        return 0
    added = 0
    with evidence_lock(root):
        for month in sorted({day.strftime("%Y-%m") for day, _ in lines}):
            path = root / source / f"{month}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()
            new = list(dict.fromkeys(line for day, line in lines if day.strftime("%Y-%m") == month and line not in existing))
            if new:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("".join(line + "\n" for line in new))
                added += len(new)
    return added


def read(source: str = "", hint: str = "", since: date | None = None, home: Path | None = None) -> list[Evidence]:
    """Rows newest first, filtered by source, hint (case-insensitive) and earliest date."""
    root = folder(home)
    if root is None or not root.is_dir():
        return []
    found = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or (source and directory.name != source):
            continue
        for path in sorted(directory.iterdir()):
            if not MONTH.fullmatch(path.name) or (since and path.stem < since.strftime("%Y-%m")):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                row = parse(line, directory.name)
                if row and (not since or row.date >= since) and (
                        not hint or hint.casefold() in (each.casefold() for each in row.hints)):
                    found.append(row)
    return sorted(reversed(found), key=lambda row: row.date, reverse=True)
