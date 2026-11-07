"""Projects and the work done on them, from files the seed skill writes per project
(`$AGENT_HOME/channels/<workspace>/plans/<project>.md`): where an organisation is heading and where its effort goes,
for the portrait's After and Energy (docs/image.md section 3)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .. import fm
from . import Source

SOURCE = Source(id="plans", editions=("enterprise",), runs="agent", where="internal", rank=4, needs="tracker", files=("plans/*.md",),
                facets=("direction",), about="projects, their targets and the issues done on them")

PLAN_DAYS = 30   # effort is about now: a month of finished work

PLAN_LINE = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2}) · (?P<what>done|opened) · (?P<key>[^·]+?) · (?P<title>.+?)\s*$")

OPEN_STATES = ("planned", "started", "paused", "backlog")

@dataclass
class Project:
    name: str
    initiative: str
    state: str
    target: str
    lead: str
    file: Path
    done: list[tuple[str, str, str]] = field(default_factory=list)   # at, key, title

def horizon(target: str, today: date) -> str:
    """When a target falls: `now` within this quarter, `year` within twelve months, `longer` past that; no target says
    nothing about when, so the skill decides."""
    if not target:
        return ""
    when = date.fromisoformat(target)
    quarter_end = date(today.year + (today.month > 9), (((today.month - 1) // 3 + 1) * 3) % 12 + 1, 1) - timedelta(days=1)
    return "now" if when <= quarter_end else "year" if when <= today + timedelta(days=365) else "longer"

def load_plans(folder: Path, today: date, days: int) -> tuple[list[Project], list[tuple[str, str, str]], list[str]]:
    """Open projects with the work finished on them in the window, the work finished outside any project, and what
    could not be read. `_unplanned.md` holds issues done in no project."""
    projects, unplanned, problems, floor = [], [], [], (today - timedelta(days=days)).isoformat()
    for path in sorted(folder.glob("*.md")):
        try:
            data, body = fm.read(path)
        except fm.Malformed as error:
            problems.append(f"{path.name}: frontmatter is not valid YAML ({error})")
            continue
        done = []
        for line in body.splitlines():
            if not line.startswith("- "):
                continue
            match = PLAN_LINE.match(line)
            if match is None:
                problems.append(f"{path.name}: malformed issue line {line[:60]!r}")
            elif match["what"] == "done" and floor <= match["at"] <= today.isoformat():
                done.append((match["at"], match["key"].strip(), match["title"]))
        if path.name == "_unplanned.md":
            unplanned += done
            continue
        target = str(data.get("target") or "")
        try:
            if target:
                date.fromisoformat(target)
            project = Project(str(data["project"]), str(data.get("initiative") or ""), str(data["state"]).lower(), target,
                              str(data.get("lead") or ""), path, done)
        except (KeyError, ValueError):
            problems.append(f"{path.name}: needs frontmatter project and state, and target as YYYY-MM-DD when set")
            continue
        if project.state in OPEN_STATES:
            projects.append(project)
    return projects, unplanned, problems

def direction(folder: Path, today: date, days: int = 30) -> dict:
    projects, unplanned, problems = load_plans(folder, today, days)
    total = sum(len(p.done) for p in projects) + len(unplanned)
    order = {"now": 0, "year": 1, "longer": 2, "": 3}
    ranked = sorted(projects, key=lambda p: (order[horizon(p.target, today)], p.target or "9999", p.name))
    share = lambda n: round(100 * n / total) if total else 0
    return {
        "window": {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
        "after": [{"project": p.name, "initiative": p.initiative, "state": p.state, "target": p.target,
                   "horizon": horizon(p.target, today), "lead": p.lead, "file": p.file.as_posix()} for p in ranked],
        "energy": sorted([{"project": p.name, "done": len(p.done), "share": share(len(p.done))} for p in projects if p.done]
                         + ([{"project": "(no project)", "done": len(unplanned), "share": share(len(unplanned))}] if unplanned else []),
                         key=lambda e: -e["done"]),
        "unattended": [p.name for p in ranked if not p.done and p.state != "paused" and horizon(p.target, today) == "now"],
        "done": total,
        "problems": problems,
    }

def render_direction(data: dict) -> str:
    out = [f"{data['window']['from']} to {data['window']['to']}: {len(data['after'])} open projects, {data['done']} issues done", "",
           "Heading, soonest first:"]
    out += [f"- {a['project']}" + (f" ({a['initiative']})" if a["initiative"] else "") + f" · {a['state']} · "
            + (f"target {a['target']} · {a['horizon']}" if a["target"] else "no target") + (f" · lead {a['lead']}" if a["lead"] else "")
            for a in data["after"]] or ["- nothing open"]
    out += ["", "Effort, most first:"] + ([f"- {e['project']} · {e['done']} done · {e['share']}%" for e in data["energy"]]
                                          or ["- nothing done in the window"])
    if data["unattended"]:
        out += ["", "Due this quarter with nothing done in the window: " + ", ".join(data["unattended"])]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)
