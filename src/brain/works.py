"""The runtime's Library listing and history in local work checkouts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from . import fm, runtime

SLUG = re.compile(r"[a-z0-9-]+")
ENTRY = re.compile(r"^[ \t]*-[ \t]+[^|\n]+\|[ \t]*([a-z0-9-]+)[ \t]*\|")


def append_history(slug: str, tid: str, title: str, today: date, outcome: str,
                   delivers: str | None, next_step: str | None) -> None:
    """Append a closed stage to a local work, atomically; the caller holds the vault lock."""
    home = runtime.configured()
    if home is None or not SLUG.fullmatch(slug) or not (home / "works" / slug).is_dir():
        return
    path = home / "works" / slug / "HISTORY.md"
    body = path.read_text(encoding="utf-8") if path.exists() else "# History\n"
    lines = [f"## {today.isoformat()} · {tid} · {title}", f"- Outcome: {outcome}"]
    if delivers:
        lines.append(f"- Delivers: {delivers}")
    if next_step:
        lines.append(f"- Next it left: {next_step}")
    fm.write(path, {}, body.rstrip() + "\n\n" + "\n".join(lines) + "\n")


def library() -> str | None:
    """The listing and notices without their title, or None when unavailable."""
    home = runtime.configured()
    if home is None:
        return None
    try:
        body = (home / "works" / "LIBRARY.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return re.sub(r"^# Library[ \t]*$", "", body, count=1, flags=re.M).strip()


def slugs(body: str) -> set[str]:
    return {match[1] for line in body.splitlines() if (match := ENTRY.match(line))}


@dataclass(frozen=True)
class Work:
    slug: str
    title: str
    form: str
    status: str
    editable: bool
    created: date | None
    opened: date | Literal["never"] | None


def _date(field: str, prefix: str) -> date | None:
    value = field.removeprefix(prefix)
    if not field.startswith(prefix) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse(body: str) -> list[Work]:
    """Read listing rows; legacy or malformed dates remain unknown, never inferred from updatedAt."""
    out = []
    for line in body.splitlines():
        if not ENTRY.match(line):
            continue
        fields = [part.strip() for part in line.strip()[1:].split("|")]
        if len(fields) < 6:
            continue
        title, slug, form, status, access = fields[:5]
        created = _date(fields[6], "created ") if len(fields) > 6 else None
        opened = ("never" if fields[7] == "opened never" else _date(fields[7], "opened ")) if len(fields) > 7 else None
        out.append(Work(slug, title, form, status, access == "editable", created, opened))
    return out
