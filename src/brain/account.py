"""A role's record (docs/roles.md sections 7 and 11): per piece of its own work, what it predicted reviewers would say
against what they did, corrections on a lesson it had already learned, misses by kind, and its asks answered "you could
have found this"; and the weekly account built from them. A report, not where learning happens: the corrections are
already filed by the time they are counted.

A correction counts once per lesson per piece of work: a reviewer saying the same thing on three lines is one."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import corrections, roles, screen
from .config import Config
from .lock import evidence_lock

VERDICTS = ("needed", "findable", "unneeded")   # an ask was needed; "you could have found this"; "your call"
ID_TAIL = re.compile(r"\^([0-9a-f]{4})\s*$")


@dataclass
class Item:
    work: str
    title: str
    opened: str
    process: dict
    predicted: list[str] = field(default_factory=list)
    corrections: int = 0
    weighted: float = 0.0
    repeated: int = 0
    burden: int = 0
    misses: dict[str, int] = field(default_factory=dict)
    asks: dict[str, int] = field(default_factory=dict)


def _folder(config: Config, role: str):
    return screen.folder(config, role)


def predict(config: Config, role: str, work: str, expected: list[str]) -> int:
    """What the role expects reviewers to raise on a draft, written when it opens it: the review findings it left, the
    calls it flagged. Kept once per work; a later call replaces nothing."""
    if work not in corrections.works(config, role):
        raise ValueError(f"{work!r} is not the role's own work; register it first")
    expected = [" ".join(text.split()) for text in expected if text.strip()]
    with evidence_lock(_folder(config, role)):
        if any(row["work"] == work for row in screen._rows(_folder(config, role) / "predicted.jsonl")):
            return 0
        screen._append(_folder(config, role) / "predicted.jsonl",
                       [{"work": work, "expected": expected, "at": config.today().isoformat()}])
    return len(expected)


def asked(config: Config, role: str, work: str, verdict: str, about: str = "") -> None:
    """How an ask on a piece of work was answered: needed, or an over-ask ("you could have found this", "your call")."""
    if verdict not in VERDICTS:
        raise ValueError(f"a verdict is one of {', '.join(VERDICTS)}")
    if work not in corrections.works(config, role):
        raise ValueError(f"{work!r} is not the role's own work; register it first")
    with evidence_lock(_folder(config, role)):
        screen._append(_folder(config, role) / "asked.jsonl",
                       [{"work": work, "verdict": verdict, "about": " ".join(about.split()), "at": config.today().isoformat()}])


def _line_of(row: dict) -> str:
    """The lesson a filing landed on: the line it went to, or the id at the end of the line it wrote."""
    if row.get("how") in ("merged",) or str(row.get("how", "")).startswith("confirms"):
        return str(row.get("line", "")).lstrip("^")
    if row.get("how") == "new":
        match = ID_TAIL.search(str(row.get("line", "")))
        return match[1] if match else ""
    return ""


def _first_seen(config: Config, role: str, ident: str) -> str:
    """When a lesson's id first appeared in the role's notes, from their history on the main line, merges included;
    "" when unknown."""
    rel = roles.folder(config, role).relative_to(config.repo).as_posix()
    out = roles._git(config, "log", "--reverse", "--first-parent", "--diff-merges=first-parent", "--format=%aI",
                     "-S", f"^{ident}", "--", rel).split()
    return out[0] if out else ""


def _before(seen: str, opened: str) -> bool:
    """Whether a lesson was in the notes before the work was opened: by the moment when both have one (a lesson the
    01:30 study wrote is known to work opened at 09:00), else by the day."""
    if not seen or not opened:
        return False
    try:
        a, b = datetime.fromisoformat(seen), datetime.fromisoformat(opened)
    except ValueError:
        return seen[:10] < opened[:10]
    if len(opened) <= 10:
        return a.date() < b.date()
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    return a < b


def items(config: Config, role: str) -> list[Item]:
    definition = roles.load(config, role)
    burden_events = {"review.comment", "review.changes_requested"}
    works, events, done = corrections.works(config, role), corrections.events(config, role), corrections.filed(config, role)
    predicted = {row["work"]: row.get("expected", []) for row in screen._rows(_folder(config, role) / "predicted.jsonl")}
    # Work recorded before switches existed says so, rather than borrowing today's settings.
    out = {ident: Item(ident, work.title, work.opened, work.process or {"process": "unknown"}, list(predicted.get(ident, [])))
           for ident, work in works.items()}
    seen: dict[str, str] = {}
    counted: dict[tuple[str, str], tuple[float, str, bool]] = {}
    for event in events.values():
        row = done.get(event.id, {})
        if row.get("how") == "dismissed" or event.work not in out:
            continue
        line = _line_of(row)
        key = (event.work, line or event.id)
        if line and line not in seen:
            seen[line] = _first_seen(config, role, line)
        before = bool(line) and _before(seen[line], out[event.work].opened)
        if row.get("how") == "decision right":
            miss = "decision"
        elif row.get("miss"):
            miss = row["miss"]
        elif not row:
            miss = "unfiled"
        else:
            miss = "execution" if before else "knowledge"
        if event.event in burden_events:   # every comment a reviewer had to write, before any is merged into a lesson
            out[event.work].burden += 1
        weight, kept, explicit = counted.get(key, (0.0, "", False))
        # One lesson on one piece of work has one kind of miss: one the role named outranks one worked out.
        named = bool(row.get("miss"))
        if kept and (explicit or not named):
            miss, named = kept, explicit
        counted[key] = (max(weight, event.weight), miss, named)
    for (work, key), (weight, miss, _) in counted.items():
        item = out[work]
        item.corrections += 1
        item.weighted += weight
        item.misses[miss] = item.misses.get(miss, 0) + 1
        if key in seen and _before(seen[key], item.opened):
            item.repeated += 1
    for row in screen._rows(_folder(config, role) / "asked.jsonl"):
        if row.get("work") in out:
            out[row["work"]].asks[row["verdict"]] = out[row["work"]].asks.get(row["verdict"], 0) + 1
    return sorted(out.values(), key=lambda item: (item.opened, item.work))


def account(config: Config, role: str, days: int = 7, today: date | None = None) -> dict:
    """The week: each piece of work opened in it, and the totals a person reads to see whether the role is getting
    better at the job (fewer corrections per piece, fewer repeats, fewer over-asks)."""
    today = today or config.today()
    since, until = (today - timedelta(days=days)).isoformat(), today.isoformat()   # [since, until): weeks don't overlap
    within = lambda at: since <= str(at)[:10] < until
    week = [item for item in items(config, role) if within(item.opened)]
    learned = [row for row in corrections.filed(config, role).values() if within(row.get("at", "")) and row.get("how") == "new"]
    struck = [entry for entry in roles.read_index(config, role).struck if within(entry.at)]
    totals = {
        "work": len(week),
        "corrections": sum(item.corrections for item in week),
        "weighted": round(sum(item.weighted for item in week), 1),
        "repeated": sum(item.repeated for item in week),
        "predicted": sum(len(item.predicted) for item in week),
        "burden_per_work": round(sum(item.burden for item in week) / len(week), 2) if week else None,
        "misses": {kind: sum(item.misses.get(kind, 0) for item in week) for kind in sorted({k for i in week for k in i.misses})},
        "asks": {verdict: sum(item.asks.get(verdict, 0) for item in week) for verdict in VERDICTS},
        "learned": len(learned),
        "struck": len(struck),
    }
    by_process: dict[str, list[Item]] = {}
    for item in week:
        label = "process unknown" if "process" in item.process else ", ".join(
            f"{step} {'on' if on else 'off'}" for step, on in sorted(item.process.items()))
        by_process.setdefault(label, []).append(item)
    compare = {label: {"work": len(group), "corrections_per_work": round(sum(i.corrections for i in group) / len(group), 2)}
               for label, group in by_process.items()} if len(by_process) > 1 else {}
    return {"role": role, "since": since, "until": until, "totals": totals, "compare": compare,
            "items": [asdict(item) for item in week]}


def render(report: dict) -> str:
    t = report["totals"]
    lines = [f"# {report['role']}: {report['since']} up to {report['until']}", ""]
    if not t["work"]:
        return "\n".join(lines + ["No work opened this week.", f"Learned {t['learned']} line(s); {t['struck']} struck by people."])
    over = t["asks"]["findable"] + t["asks"]["unneeded"]
    lines += [
        f"- {t['work']} piece(s) of work, {t['corrections']} correction(s) ({t['weighted']} weighted), "
        f"{t['repeated']} on a lesson it had already learned.",
        f"- Predicted {t['predicted']} thing(s) reviewers would raise; review comments per piece: {t['burden_per_work']}.",
        "- Misses: " + (", ".join(f"{n} {kind}" for kind, n in t["misses"].items()) or "none") + ".",
        f"- Asks: {t['asks']['needed']} needed, {over} it could have answered itself.",
        f"- Learned {t['learned']} line(s); {t['struck']} struck by people.",
    ]
    if report["compare"]:
        lines += ["", "By process: " + "; ".join(f"{label}: {v['corrections_per_work']} corrections per piece over {v['work']}"
                                                 for label, v in report["compare"].items()) + "."]
    lines += ["", "| work | corrections | repeated | predicted | misses |", "| --- | --- | --- | --- | --- |"]
    for item in report["items"]:
        misses = ", ".join(f"{n} {k}" for k, n in item["misses"].items()) or "-"
        lines.append(f"| {item['title'] or item['work']} | {item['corrections']} | {item['repeated']} | {len(item['predicted'])} | {misses} |")
    return "\n".join(lines)
