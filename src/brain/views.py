"""Views: the data an owner sees above a task's thread or a work, in the pieces the app knows how to draw.

The intellect writes data, never UI: which pieces, and what is in each. The app draws them in its own style, and it
draws the frame (status, the question, gates of the task itself, the chunk timeline) from the task without any of this.
A view is checked here against the kit, so whatever reaches the app can be drawn. Task views are kept beside the task
as `<agent>/Task Views/T-nnn.json`; `brain state --json` carries them. Work summaries live in the work's `view.json`.
"""
import json
import math
import re

from .config import Config

MAX_BYTES = 16_000
MAX_PIECES = 6
MAX_TEXT = 200
ROW_STATES = ("good", "new", "held", "waiting", "blocked", "out")
ITEM_STATES = ("done", "open", "waiting", "blocked")

KINDS = ("numbers", "progress", "table", "checklist", "places", "links", "gallery", "steps", "schedule")
# What each piece holds. Every string is plain text (no markup), at most MAX_TEXT characters.
KIT = """pieces, at most 6, each one of the kinds below; any piece may add "from": where its data came from
  ("from Zillow, 41 listings read"), said plainly. Each thing appears in one piece: the same candidates in a gallery or a
  table, not both, and a count in numbers matches the rows or items it counts.
  {"kind": "numbers", "title"?, "items": [{"label", "value", "detail"?, "trend"?: [n, ...], "since"?}]}   2-4 items; trend 2-30
       numbers, oldest first; since says when the trend starts ("since Nov 2")
  {"kind": "progress", "title", "done": n, "total": n, "unit"?, "detail"?}
  {"kind": "table", "title", "columns": [..] (1-6), "rows": [{"cells": [..], "state"?, "link"?}], "note"?}   up to 40 rows
       state: good | new | held | waiting (sent, awaiting a reply) | blocked | out
  {"kind": "checklist", "title", "items": [{"text", "state", "detail"?}]}                       up to 20 items
       state: done | open | waiting | blocked
  {"kind": "places", "title", "items": [{"name", "detail"?, "query", "value"?, "state"?, "link"?}]}   up to 20; query is what a
       map searches; value, state (as a table row's) and link let candidates with a location be one piece, not a table and a map
  {"kind": "links", "title"?, "items": [{"label", "href"}]}                                       up to 10; https URL or a vault note path
  {"kind": "gallery", "title", "items": [{"image", "label", "value"?, "state"?, "link"?}]}         up to 24; image an https URL
  {"kind": "steps", "title", "items": [{"label", "detail"?}], "current": i}                       2-8 stages; current is the stage now (0-based)
  {"kind": "schedule", "title", "items": [{"time", "text", "detail"?}]}                            up to 12; time as people say it ("2:30", "Thu 9am")
"""


def path(config: Config, tid: str):
    return config.vault_path / config.agent(f"Task Views/{tid}.json")


def read(config: Config, tid: str) -> dict | None:
    """The task's view as the kit allows it; a hand-edited file outside the kit is no view."""
    p = path(config, tid)
    try:
        return validate(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def write(config: Config, tid: str, text: str) -> dict | None:
    """Check `text` against the kit and keep it; an empty view (`{}` or `{"pieces": []}`) removes the task's view."""
    clean = validate(text)
    p = path(config, tid)
    if clean is None:
        p.unlink(missing_ok=True)
        return None
    out = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(out + "\n", encoding="utf-8")
    return clean


def validate(text: str) -> dict | None:
    """Check and clean a task or work view against the kit; an empty view returns None."""
    try:
        view = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"the view is not JSON: {e}") from None
    if not isinstance(view, dict) or set(view) - {"pieces"}:
        raise ValueError('a view is {"pieces": [...]}\n' + KIT)
    pieces = view.get("pieces", [])
    if isinstance(pieces, list) and not pieces:
        return None
    if not isinstance(pieces, list) or len(pieces) > MAX_PIECES:
        raise ValueError(f"pieces must be a list of at most {MAX_PIECES}\n" + KIT)
    clean = {"pieces": [_piece(i, piece) for i, piece in enumerate(pieces)]}
    out = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(out.encode()) > MAX_BYTES:
        raise ValueError(f"the view is {len(out.encode())} bytes; the limit is {MAX_BYTES}: show less, link the rest")
    return clean


def _text(where: str, value, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{where} must be text")
    value = re.sub(r"\s+", " ", str(value)).strip()
    if required and not value:
        raise ValueError(f"{where} must not be empty")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{where} is {len(value)} characters; at most {MAX_TEXT}")
    return value


def _count(where: str, value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where} must be a whole number, zero or more")
    return value


def _list(where: str, value, low: int, high: int) -> list:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f"{where} must be a list of {low} to {high}")
    return value


def _href(where: str, value) -> str:
    value = _text(where, value)
    if not (value.startswith("https://") or (not re.match(r"^[a-z]+:", value) and not value.startswith("/"))):
        raise ValueError(f"{where} must be an https URL or a vault note path")
    return value


def _state(where: str, value, states: tuple[str, ...], required: bool) -> str | None:
    if value is None and not required:
        return None
    if value not in states:
        raise ValueError(f"{where} must be one of {', '.join(states)}")
    return value


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _piece(i: int, piece) -> dict:
    at = f"piece {i + 1}"
    if not isinstance(piece, dict) or piece.get("kind") not in KINDS:
        raise ValueError(f"{at}: kind must be {', '.join(KINDS[:-1])} or {KINDS[-1]}\n" + KIT)
    clean = _kind(at, piece)
    source = _text(f"{at} from", piece.get("from"), False)
    return {**clean, "from": source} if source else clean


def _kind(at: str, piece: dict) -> dict:
    kind = piece["kind"]
    allowed = {"numbers": {"title", "items"}, "progress": {"title", "done", "total", "unit", "detail"},
               "table": {"title", "columns", "rows", "note"}, "checklist": {"title", "items"},
               "places": {"title", "items"}, "links": {"title", "items"}, "gallery": {"title", "items"},
               "steps": {"title", "items", "current"}, "schedule": {"title", "items"}}[kind] | {"kind", "from"}
    if extra := set(piece) - allowed:
        raise ValueError(f"{at} ({kind}): unknown {', '.join(sorted(extra))}\n" + KIT)
    title = _text(f"{at} title", piece.get("title"), required=kind not in ("numbers", "links"))
    if kind == "numbers":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 2, 4):
            if not isinstance(x, dict) or set(x) - {"label", "value", "detail", "trend", "since"}:
                raise ValueError(f"{at} items are {{\"label\", \"value\", \"detail\"?, \"trend\"?, \"since\"?}}")
            trend = x.get("trend")
            if trend is not None and not (isinstance(trend, list) and 2 <= len(trend) <= 30
                                          and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in trend)):
                raise ValueError(f"{at} trend must be 2 to 30 numbers, oldest first")
            items.append(_drop_none({"label": _text(f"{at} label", x.get("label")), "value": _text(f"{at} value", x.get("value")),
                                     "detail": _text(f"{at} detail", x.get("detail"), False), "trend": trend,
                                     "since": _text(f"{at} since", x.get("since"), False) if trend is not None else None}))
        return _drop_none({"kind": kind, "title": title, "items": items})
    if kind == "gallery":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 1, 24):
            if not isinstance(x, dict) or set(x) - {"image", "label", "value", "state", "link"}:
                raise ValueError(f"{at} items are {{\"image\", \"label\", \"value\"?, \"state\"?, \"link\"?}}")
            image = _text(f"{at} image", x.get("image"))
            if not image.startswith("https://"):
                raise ValueError(f"{at} image must be an https URL")
            items.append(_drop_none({"image": image, "label": _text(f"{at} label", x.get("label")),
                                     "value": _text(f"{at} value", x.get("value"), False),
                                     "state": _state(f"{at} state", x.get("state"), ROW_STATES, False),
                                     "link": _href(f"{at} link", x["link"]) if x.get("link") else None}))
        return {"kind": kind, "title": title, "items": items}
    if kind == "steps":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 2, 8):
            if not isinstance(x, dict) or set(x) - {"label", "detail"}:
                raise ValueError(f"{at} items are {{\"label\", \"detail\"?}}")
            items.append(_drop_none({"label": _text(f"{at} label", x.get("label")), "detail": _text(f"{at} detail", x.get("detail"), False)}))
        current = _count(f"{at} current", piece.get("current"))
        if current >= len(items):
            raise ValueError(f"{at} current must be the index of one of its {len(items)} stages (0-based)")
        return {"kind": kind, "title": title, "items": items, "current": current}
    if kind == "schedule":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 1, 12):
            if not isinstance(x, dict) or set(x) - {"time", "text", "detail"}:
                raise ValueError(f"{at} items are {{\"time\", \"text\", \"detail\"?}}")
            time = _text(f"{at} time", x.get("time"))
            if len(time) > 24:
                raise ValueError(f"{at} time is long; say it as people do (\"2:30\", \"Thu 9am\")")
            items.append(_drop_none({"time": time, "text": _text(f"{at} text", x.get("text")), "detail": _text(f"{at} detail", x.get("detail"), False)}))
        return {"kind": kind, "title": title, "items": items}
    if kind == "progress":
        done, total = _count(f"{at} done", piece.get("done")), _count(f"{at} total", piece.get("total"))
        if total == 0 or done > total:
            raise ValueError(f"{at}: done must be between 0 and total, and total more than 0")
        return _drop_none({"kind": kind, "title": title, "done": done, "total": total,
                           "unit": _text(f"{at} unit", piece.get("unit"), False),
                           "detail": _text(f"{at} detail", piece.get("detail"), False)})
    if kind == "table":
        columns = [_text(f"{at} column", c) for c in _list(f"{at} columns", piece.get("columns"), 1, 6)]
        rows = []
        for r, row in enumerate(_list(f"{at} rows", piece.get("rows"), 0, 40)):
            if not isinstance(row, dict) or set(row) - {"cells", "state", "link"}:
                raise ValueError(f"{at} row {r + 1} is {{\"cells\": [..], \"state\"?, \"link\"?}}")
            cells = [_text(f"{at} row {r + 1} cell", c, False) or "" for c in _list(f"{at} row {r + 1} cells", row.get("cells"), 1, 6)]
            if len(cells) != len(columns):
                raise ValueError(f"{at} row {r + 1} has {len(cells)} cells for {len(columns)} columns")
            rows.append(_drop_none({"cells": cells, "state": _state(f"{at} row {r + 1} state", row.get("state"), ROW_STATES, False),
                                    "link": _href(f"{at} row {r + 1} link", row["link"]) if row.get("link") else None}))
        return _drop_none({"kind": kind, "title": title, "columns": columns, "rows": rows,
                           "note": _text(f"{at} note", piece.get("note"), False)})
    if kind == "checklist":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 1, 20):
            if not isinstance(x, dict) or set(x) - {"text", "state", "detail"}:
                raise ValueError(f"{at} items are {{\"text\", \"state\", \"detail\"?}}")
            items.append(_drop_none({"text": _text(f"{at} item", x.get("text")),
                                     "state": _state(f"{at} item state", x.get("state"), ITEM_STATES, True),
                                     "detail": _text(f"{at} item detail", x.get("detail"), False)}))
        return {"kind": kind, "title": title, "items": items}
    if kind == "places":
        items = []
        for x in _list(f"{at} items", piece.get("items"), 1, 20):
            if not isinstance(x, dict) or set(x) - {"name", "detail", "query", "value", "state", "link"}:
                raise ValueError(f"{at} items are {{\"name\", \"detail\"?, \"query\", \"value\"?, \"state\"?, \"link\"?}}")
            items.append(_drop_none({"name": _text(f"{at} name", x.get("name")), "detail": _text(f"{at} detail", x.get("detail"), False),
                                     "query": _text(f"{at} query", x.get("query")), "value": _text(f"{at} value", x.get("value"), False),
                                     "state": _state(f"{at} state", x.get("state"), ROW_STATES, False),
                                     "link": _href(f"{at} link", x["link"]) if x.get("link") else None}))
        return {"kind": kind, "title": title, "items": items}
    items = []
    for x in _list(f"{at} items", piece.get("items"), 1, 10):
        if not isinstance(x, dict) or set(x) - {"label", "href"}:
            raise ValueError(f"{at} items are {{\"label\", \"href\"}}")
        items.append({"label": _text(f"{at} label", x.get("label")), "href": _href(f"{at} href", x.get("href"))})
    return _drop_none({"kind": kind, "title": title, "items": items})
