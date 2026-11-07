"""The owner's accepted calendar as evidence, from the synced account files: each recurring or one-off event by its
title, how often, when last and when next, and where (docs/image.md section 3)."""
from __future__ import annotations

import hashlib
from datetime import date

from ..evidence import Evidence
from . import Context, Source, hints

SOURCE = Source(id="calendar", editions=("personal",), runs="runtime", facets=("play", "places", "food & drink", "home & routine"),
                about="the events the owner accepted, by title: how often, when last and next, and where")


def rows(data: dict) -> list[Evidence]:
    """An event is `went` once it has happened and `plans` while it is only ahead, dated by its last or next day, so a
    rerun with nothing new writes the same rows."""
    out = []
    for event in data["own"]["events"]:
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
    from .. import seed
    from ..vault import Vault
    try:
        data = seed.personal(ctx.vault or Vault(ctx.config.vault_path), ctx.today)
    except SystemExit as error:
        if "has synced" in str(error):
            return []   # nothing connected yet is not a failure
        raise ValueError(str(error)) from None
    return rows(data)
