"""Prospective triggers: parse the table, fire date commitments, retire intentions."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .config import Config

ROW_RE = re.compile(r"^\|\s*(TR-\d{3})\s*\|(.*)\|\s*$")
COLS = ["id", "class", "when", "do", "source", "status", "created"]
DATE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})$")

TEMPLATE = """# Triggers

Pinned. Prospective memory: "when X, do Y." See the Memory Spec, "Prospective triggers". A **commitment** never retires by decay; an **intention** retires when its source goes cold, after one morning mention. A trigger grants no permission.

| id | class | when | do | source | status | created |
| --- | --- | --- | --- | --- | --- | --- |

## Fired

## Retired
"""


@dataclass
class Trigger:
    id: str
    cls: str
    when: str
    do: str
    source: str
    status: str
    created: str

    def row(self) -> str:
        return f"| {self.id} | {self.cls} | {self.when} | {self.do} | {self.source} | {self.status} | {self.created} |"


def parse(text: str) -> list[Trigger]:
    out = []
    for ln in text.splitlines():
        m = ROW_RE.match(ln)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        cells = [m.group(1)] + cells
        cells += [""] * (len(COLS) - len(cells))
        out.append(Trigger(*cells[:len(COLS)]))
    return out


def date_of(when: str) -> date | None:
    m = DATE_RE.match(when.strip())
    return date.fromisoformat(m.group(1)) if m else None


def source_rel(trig: Trigger) -> str:
    m = re.search(r"\[\[([^\]|#]+)", trig.source)
    return m.group(1).strip() if m else trig.source.strip()


def process(text: str, today: date, tier_of: dict[str, str], config: Config) -> tuple[str, dict]:
    """Return rewritten Triggers text and a report of fired/mentioned/retired."""
    trigs = parse(text)
    report = {"fired": [], "mentioned": [], "retired": []}
    for t in trigs:
        if status_section(t.status) != "active":
            continue
        d = date_of(t.when)
        if d and d <= today and t.status in ("", "active"):
            t.status = f"fired {today.isoformat()}"
            report["fired"].append(t)
            continue
        if t.cls == "intention":
            src = source_rel(t)
            relative = src.removeprefix(config.vault + "/")
            src_tier = tier_of.get(src) or tier_of.get(src + ".md") or tier_of.get(relative) or tier_of.get(relative + ".md")
            if src_tier == "cold":
                if t.status in ("", "active"):
                    t.status = f"mentioned {today.isoformat()}"
                    report["mentioned"].append(t)
                elif t.status.startswith("mentioned") and t.status[10:] < today.isoformat():
                    t.status = f"retired {today.isoformat()}"
                    report["retired"].append(t)
    return render(text, trigs), report


def status_section(status: str) -> str:
    status = status.strip().casefold()
    if status.startswith("retired"):
        return "retired"
    if status.startswith(("fired", "fulfilled")):
        return "fired"
    return "active"


def render(text: str, trigs: list[Trigger]) -> str:
    """Update rows in place; move their evidence with them, retaining prose."""
    lines = (text or TEMPLATE).splitlines(keepends=True)
    targets = {t.id: t for t in trigs}
    moved = {section: [] for section in ("active", "fired", "retired")}
    seen, out = set(), []
    section, i = "active", 0
    while i < len(lines):
        line = lines[i]
        heading = re.fullmatch(r"## (Fired|Retired)\s*", line)
        if heading:
            section = heading[1].lower()
        row = ROW_RE.match(line.rstrip("\r\n"))
        if not row or row[1] not in targets:
            out.append(line); i += 1
            continue
        target = targets[row[1]]
        seen.add(target.id)
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        rendered = line if parse(line)[0] == target else target.row() + ending
        destination = status_section(target.status)
        if destination == section:
            out.append(rendered); i += 1
            continue
        # Evidence following a row belongs to that row, up to another row or
        # heading. Never reconstruct the document from table cells alone.
        end = i + 1
        while end < len(lines) and not ROW_RE.match(lines[end].rstrip("\r\n")) and not re.match(r"^#{1,6} ", lines[end]):
            end += 1
        moved[destination].append(rendered + "".join(lines[i + 1:end]))
        i = end
    for t in trigs:
        if t.id not in seen:
            moved[status_section(t.status)].append(t.row() + "\n")
    for destination, blocks in moved.items():
        if not blocks:
            continue
        if destination == "active":
            position = next((i for i, line in enumerate(out) if re.fullmatch(r"## (Fired|Retired)\s*", line)), len(out))
        else:
            heading = "## " + destination.title()
            start = next((i for i, line in enumerate(out) if line.strip() == heading), None)
            if start is None:
                out.append("\n" + heading + "\n\n")
                position = len(out)
            else:
                position = next((i for i in range(start + 1, len(out)) if re.match(r"^## ", out[i])), len(out))
        block = "".join(blocks)
        if position and not out[position - 1].endswith("\n"):
            block = "\n" + block
        if not block.endswith("\n"):
            block += "\n"
        out.insert(position, block)
    return "".join(out)


def next_id(trigs: list[Trigger]) -> str:
    n = max([int(t.id[3:]) for t in trigs] + [0]) + 1
    return f"TR-{n:03d}"
