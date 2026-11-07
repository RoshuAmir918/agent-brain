"""The runtime's Library listing, summaries and history in local work checkouts."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from . import fm, runtime, views
from .config import Config

SLUG = re.compile(r"[a-z0-9-]+")
ENTRY = re.compile(r"^[ \t]*-[ \t]+[^|\n]+\|[ \t]*([a-z0-9-]+)[ \t]*\|")


def write_view(slug: str, text: str) -> dict | None:
    """Keep a checked summary in an existing work, atomically; an empty view removes it."""
    if not SLUG.fullmatch(slug):
        raise ValueError("work slug must match [a-z0-9-]+")
    path = runtime.home() / "works" / slug / "view.json"
    if not path.parent.is_dir():
        raise ValueError(f"work folder does not exist: {path.parent}; create it with demiurge works new")
    return views.save(path, text)


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
    fm.write_text(path, body.rstrip() + "\n\n" + "\n".join(lines) + "\n")


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
    goal: str | None = None


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
        goal = next((value.removeprefix("goal ") for value in fields[8:] if value.startswith("goal ")), None)
        out.append(Work(slug, title, form, status, access == "editable", created, opened, goal))
    return out


def goal_for(slug: str) -> str | None:
    home = runtime.configured()
    if home is None or not SLUG.fullmatch(slug):
        return None
    path = home / "works" / slug / "GOAL.md"
    return fm.read(path)[0].get("goal") if path.exists() else None


def set_goal(config: Config, slug: str, goal: str) -> None:
    from .goals import require_active
    require_active(config, goal)
    if not SLUG.fullmatch(slug):
        raise ValueError("work slug must match [a-z0-9-]+")
    path = runtime.home() / "works" / slug / "GOAL.md"
    if not path.parent.is_dir():
        raise ValueError("work folder does not exist; create it with demiurge works new")
    data, body = fm.read(path) if path.exists() else ({}, "")
    fm.write(path, {**data, "goal": goal}, body)
