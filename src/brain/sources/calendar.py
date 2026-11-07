"""The owner's calendar, read from the synced account files: the events they accepted, grouped by title (how often,
when last and next, where), as evidence. The seed's reading list is built on `read` and `grouped`."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from .. import channel_files
from ..evidence import Evidence
from . import Context, Nothing, Source, hints

SOURCE = Source(id="calendar", editions=("personal",), runs="runtime", facets=("play", "places", "food & drink", "home & routine"),
                about="the events the owner accepted, by title: how often, when last and next, and where")


def files(folder: Path) -> list[Path]:
    return sorted((folder / "calendar").glob("*.md")) + sorted((folder / "backfill/calendar").glob("*.md"))


def read(vault, accounts: list[tuple[str, Path]]) -> tuple[list[tuple[object, bool, Path, str]], list[str]]:
    """Every event the accounts hold, each with whether the owner accepted it (an event with attendees and no
    acceptance is someone else's), its file and its account, and what could not be read."""
    out, problems = [], []
    for name, folder in accounts:
        for path in files(folder):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                problems.append(f"{path}: unreadable calendar")
                continue
            for block in re.split(r"^## ", text, flags=re.M)[1:]:
                attendees = bool(re.search(r"^attendee:[ \t]*\S", block, re.M))
                for event in channel_files.parse_calendar("## " + block, name, vault.config):
                    out.append((event, event.response.casefold() == "accepted" or not attendees, path, name))
    return out, problems


def grouped(events: list[tuple[object, Path, str]], today: date) -> list[dict]:
    """Occurrences once each, then by title: how often, the next and the last, newest first."""
    once = {}
    for event, path, name in events:
        once.setdefault((event.id or event.title, event.start, event.end), (event, path, name))
    titles: dict[str, dict] = {}
    for event, path, name in sorted(once.values(), key=lambda value: value[0].start, reverse=True):
        day = event.start.date().isoformat()
        group = titles.setdefault(event.title, {"kind": "event", "whose": "own", "title": event.title,
                                                "date": event.start.isoformat(), "count": 0, "next": "", "last": "",
                                                "location": event.location, "path": str(path), "account": name})
        group["count"] += 1
        if day >= today.isoformat():
            group["next"] = min(group["next"] or day, day)
        else:
            group["last"] = max(group["last"], day)
    return list(titles.values())


def rows(groups: list[dict]) -> list[Evidence]:
    """An event is `went` once it has happened and `plans` while it is only ahead, dated by its last or next day, so a
    rerun with nothing new writes the same rows."""
    out = []
    for event in groups:
        title, count, last, upcoming = event["title"], event["count"], event["last"], event["next"]
        where = f" at {event['location']}" if event.get("location") else ""
        times = "once" if count == 1 else f"{count} times"
        fact = f"{title}{where}, {times}" + (f", last {last}" if last else "") + (f", next {upcoming}" if upcoming else "")
        out.append(Evidence(date.fromisoformat(last or upcoming), "went" if last else "plans", fact, "own",
                            hints(f"{title} {event.get('location') or ''}", "home & routine"),
                            f"calendar:{hashlib.sha256(title.encode()).hexdigest()[:8]}"))
    return out


def extract(ctx: Context) -> list[Evidence]:
    if ctx.config is None:
        raise ValueError("calendar reads the runtime's synced accounts; run it where the vault is")
    from ..vault import Vault
    vault = ctx.vault or Vault(ctx.config.vault_path)
    events, _ = read(vault, channel_files.synced_accounts(vault))
    if not events:
        raise Nothing("no synced calendar")
    accepted = [(event, path, name) for event, ok, path, name in events
                if ok and (not ctx.since or event.start.date() >= ctx.since)]
    return rows(grouped(accepted, ctx.today))
