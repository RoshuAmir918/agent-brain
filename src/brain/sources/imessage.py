"""The owner's iMessage history as evidence (docs/image.md section 3). The reading is `imessage_read`, standard library
only, so the same code runs here, on the owner's own Mac with `brain source imessage extract`, or carried to their Mac
by `brain source imessage script` and run there through the intellect's hands, which reads back only the rows."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..evidence import Evidence, from_json
from . import Context, Source
from .imessage_read import (DATABASE, EPOCH, body_text, clean, contacts, default_books, export, records,  # noqa: F401
                            when)

SOURCE = Source(id="imessage", editions=("personal",), runs="owner-mac", where="own", rank=8,
                facets=("people", "places", "music", "play", "reading & ideas"), sensitive=True,
                about="who the owner texts and how closely, their group chats, and the links shared", input=DATABASE)


def rows(data: dict, today) -> list[Evidence]:
    return [from_json(record) for record in records(data, today)]


def extract(ctx: Context) -> list[Evidence]:
    db = ctx.input or SOURCE.input
    if not db.is_file():
        raise ValueError(f"no Messages database at {db}")
    now = datetime.combine(ctx.today, datetime.min.time(), timezone.utc) + timedelta(days=1)
    days = (ctx.today - ctx.since).days + 1 if ctx.since else None
    try:
        data = export(db, default_books(), now, days)
    except sqlite3.Error as error:
        raise ValueError(f"could not read {db} ({error}); the terminal needs Full Disk Access in System Settings, "
                         "Privacy & Security") from error
    return rows(data, ctx.today)


def script() -> str:
    """One file the owner's Mac can run with its own python3: the hints, then the reader, with the package's one
    relative import removed."""
    here = Path(__file__).parent
    hinting = (here / "hinting.py").read_text(encoding="utf-8")
    reader = (here / "imessage_read.py").read_text(encoding="utf-8").replace("from .hinting import hints\n", "")
    body = hinting.split("from __future__ import annotations\n", 1)[1]
    head, rest = reader.split("from __future__ import annotations\n", 1)
    return f"#!/usr/bin/env python3\n{head}from __future__ import annotations\n{body}\n{rest}"
