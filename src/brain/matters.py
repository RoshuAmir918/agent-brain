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


SIGHTED = timedelta(days=30)  # a new name becomes a matter when it comes up again within this
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')


def _responsibility_hubs(vault) -> set[str]:
    from . import responsibilities
    return {entry["hub"] for entry in responsibilities.discover(vault)}


def _is_matter(vault, source: str, standing: set[str]) -> bool:
    from .state import _public
    note = vault.notes.get(source)
    return (note is not None and source not in standing and vault.kind_for(source) == "hub"
            and note.data.get("kind", "hub") == "hub" and not (note.data.get("skill") and note.data.get("records"))
            and _public(vault, source))


def listed(vault, today: date) -> list[tuple[str, str]]:
    """The matters the record is shown by name, (title, source), the most recently touched first: hubs that keep a
    `## Now` and have not faded."""
    standing = _responsibility_hubs(vault)
    shown = []
    for source, note in vault.notes.items():
        if fm.section(note.body, "Now") is None or not _is_matter(vault, source, standing):
            continue
        state_ = state(note.body, today)
        if phase(state_, today, False) == "fading":
            continue
        latest = max((state_[key]["date"] for key in ("now", "last", "soon") if state_[key]), default="")
        shown.append((latest, note.stem, source))
    return [(title, source) for _, title, source in sorted(shown, reverse=True)][:20]


def find(vault, name: str) -> str | None:
    """The matter a record names: a hub with that exact title, case aside, or the note the name resolves to."""
    standing = _responsibility_hubs(vault)
    wanted = fm.plain(name)
    for source, note in sorted(vault.notes.items()):
        if fm.plain(note.stem) == wanted and _is_matter(vault, source, standing):
            return source
    source = vault.resolve(name)
    return source if source and _is_matter(vault, source, standing) else None


def write(path, today: date, *, now: str | None = None, last: str | None = None, soon: tuple[str, str] | None = None):
    """Rewrite the hub's `## Now` lines that are given, keeping the others; the section goes after the title when the
    hub has none. The caller holds the vault lock."""
    data, body = fm.read(path)
    span = fm.section(body, "Now")
    lines = {key: f"- **{key.title()}:** {value}" for key, value in (
        ("now", now and f"{_flat(now)} · {today.isoformat()}"), ("soon", soon and f"{soon[0]} · {_flat(soon[1])}"),
        ("last", last and f"{today.isoformat()} · {_flat(last)}")) if value}
    for kept in LINE.finditer(body[span[0]:span[1]]) if span else []:
        lines.setdefault(kept[1].lower(), kept[0].strip())
    section = "\n" + "\n".join(lines[key] for key in ("now", "soon", "last") if key in lines) + "\n"
    if span:
        rest = body[span[1]:]
        body = body[:span[0]] + section + ("\n" + rest if rest else "")
    else:
        following = re.compile(r"^## ", re.M).search(body)
        at = following.start() if following else len(body)
        head = body[:at].rstrip("\n")
        body = head + ("\n\n" if head else "") + "## Now" + section + ("\n" + body[at:] if following else "")
    fm.write(path, data, body)


def _flat(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def _sightings_path():
    from . import runtime
    home = runtime.configured()
    return home / "records" / "matters.json" if home else None


def record(config, vault, today: date, about: list[dict], headline: str, opens: bool) -> str | None:
    """Apply a conversation record's `about` to the owner's matters: each named matter gets its Now (when the record
    gave one), its Soon (when it named a date sooner than the one kept) and a Last of what happened. A new name becomes
    a matter on its second sighting within SIGHTED, or on its first when it names a date or the same record opens a loop
    or an ask; until then it is only remembered. Returns the first matter's path, for the loops and asks this record
    opens. The caller holds the vault lock."""
    import json
    store = _sightings_path()
    seen = json.loads(store.read_text(encoding="utf-8")) if store and store.is_file() else {}
    first = None
    for entry in about[:2]:
        given = _flat(entry.get("name") or "")
        name = _flat(UNSAFE.sub(" ", given))[:60].strip(" .")
        if not name:
            continue
        soon = _dated("soon", " ".join((entry.get("soon") or "").split()))
        source = find(vault, given) or find(vault, name)
        if source is None:
            key = fm.plain(name)
            recent = [day for day in seen.get(key, []) if date.fromisoformat(day) >= today - SIGHTED]
            if not (opens or soon or recent):
                seen[key] = [today.isoformat()]
                continue
            path = config.vault_path / f"{name}.md"
            if path.exists():
                continue   # a note of that name that is not a matter (private, a responsibility) is left alone
            fm.write(path, {"kind": "hub", "tier": "warm"}, f"# {name}\n")
            vault.load()
            seen.pop(key, None)
            source = path.relative_to(config.vault_path).as_posix()
        kept = state(fm.read(config.vault_path / source)[1], today)["soon"]
        if soon and kept and today.isoformat() <= kept["date"] < soon["date"]:
            soon = None   # the sooner thing stays next
        write(config.vault_path / source, today, now=entry.get("now") or None, last=headline,
              soon=(soon["date"], soon["text"]) if soon else None)
        first = first or source
    if store:
        store.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(store, json.dumps({key: days for key, days in seen.items()
                                         if any(date.fromisoformat(day) >= today - SIGHTED for day in days)}, indent=1))
    return first


def task_moved(config, task, today: date, what: str) -> None:
    """A task serving a matter moves its Last (`finished <title>`, `handed off <title>`) and, while it is open with a
    due day ahead, its Soon. No model: the task already says what happened. The caller holds the vault lock."""
    if not task.hub:
        return
    from .vault import Vault
    vault = Vault(config.vault_path)
    source = vault.resolve(task.hub)
    if not source or not _is_matter(vault, source, _responsibility_hubs(vault)):
        return
    path = config.vault_path / source
    due, soon = fm.field(task.body, "Due") or "", None
    if what.startswith("handed off") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) and due >= today.isoformat():
        kept = state(fm.read(path)[1], today)["soon"]
        if not (kept and today.isoformat() <= kept["date"] < due):
            soon = (due, task.title)
    write(path, today, last=what, soon=soon)
