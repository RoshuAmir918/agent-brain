"""Read the runtime's Library listing; works themselves stay in the agent workspace."""
from __future__ import annotations

import re

from . import runtime

SLUG = re.compile(r"[a-z0-9-]+")
ENTRY = re.compile(r"^[ \t]*-[ \t]+[^|\n]+\|[ \t]*([a-z0-9-]+)[ \t]*\|")


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
