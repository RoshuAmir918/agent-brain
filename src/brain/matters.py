"""A matter is a hub that is not a responsibility: something the owner's life holds now or soon. Its `## Now` section
keeps where it stands (`**Now:** <text> · <date>`), the next dated thing and the latest thing that happened
(`**Soon:**` and `**Last:**`, each `<date> · <text>`). See docs/matters.md in the demiurge repo."""
import re
from datetime import date, timedelta

from . import fm

FRESH = timedelta(days=14)    # a Now or Last this recent keeps the matter current
AHEAD = timedelta(days=21)    # a Soon this close makes it soon

LINE = re.compile(r"^[ \t]*-[ \t]+\*\*(Now|Soon|Last):\*\*[ \t]*(.+?)[ \t]*$", re.M | re.I)
DATE = r"(\d{4}-\d{2}-\d{2})"


def _dated(field: str, value: str) -> dict | None:
    match = (re.fullmatch(r"(.+?)[ \t]*·[ \t]*" + DATE, value) if field == "now" else
             re.fullmatch(DATE + r"[ \t]*·[ \t]*(.+)", value))
    if not match:
        return None
    text, when = (match[1], match[2]) if field == "now" else (match[2], match[1])
    try:
        date.fromisoformat(when)
    except ValueError:
        return None
    return {"text": text.strip(), "date": when}


def state(body: str, today: date) -> dict:
    """The hub's Now, Soon and Last, each `{text, date}` or None. A Now older than FRESH is no longer where the matter
    stands, so it is given as its Last when nothing later happened."""
    span = fm.section(body, "Now")
    out: dict = {"now": None, "soon": None, "last": None}
    for field, value in LINE.findall(body[span[0]:span[1]]) if span else []:
        if out[field.lower()] is None:
            out[field.lower()] = _dated(field.lower(), value)
    now = out["now"]
    if now and date.fromisoformat(now["date"]) < today - FRESH:
        if out["last"] is None or out["last"]["date"] < now["date"]:
            out["last"] = now
        out["now"] = None
    return out


def phase(kept: dict, today: date, open_work: bool) -> str:
    """current: open work on it, or something happened lately (a past Soon counts as having happened);
    soon: a Soon within AHEAD; else fading, which the app does not show."""
    recent = [kept[key]["date"] for key in ("now", "last", "soon") if kept[key]]
    if open_work or any(today - FRESH <= date.fromisoformat(when) <= today for when in recent):
        return "current"
    if kept["soon"] and today < date.fromisoformat(kept["soon"]["date"]) <= today + AHEAD:
        return "soon"
    return "fading"
