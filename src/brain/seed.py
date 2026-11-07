"""The seed pass's arithmetic for an organisation's portrait: which review corrections to read, grouped so the ones
that repeat stand out. The judgment (what a correction teaches, worded as a Taste line) is the seed skill's."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import fm, routing, tasks

DAYS = 90
PLAN_DAYS = 30   # effort is about now: a month of finished work
COMMENT = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2}) · (?P<by>[^·]+?) · (?P<path>[^·]*?) · (?P<text>.+?)\s*$")
BOT = re.compile(r"\[bot\]$|-bot$|^bot-", re.I)   # GitHub's REST API marks app accounts `<name>[bot]`
AGENT_POSTED = re.compile(r"\b(?:posted|written|generated) (?:by|with) (?:codex|claude|copilot|cursor|an? (?:ai )?agent)\b",
                          re.I)   # an agent's comment under a person's account is the agent's, not their review


@dataclass
class Review:
    number: int
    title: str
    author: str
    merged: str
    file: Path
    comments: list[tuple[str, str, str, str]] = field(default_factory=list)   # at, by, path, text


def area(path: str) -> str:
    """Where in the code a correction landed: the first two folders, so `apps/api/src/x.ts` is `apps/api`."""
    parts = [part for part in path.strip().split("/") if part][:-1]
    return "/".join(parts[:2]) or "(top level)" if path.strip() else "(whole PR)"


def load(folder: Path, today: date, days: int = DAYS) -> tuple[list[Review], list[str]]:
    """Merged PRs within the window, with the comments their author did not write, and what could not be read."""
    reviews, problems, floor = [], [], (today - timedelta(days=days)).isoformat()
    for path in sorted(folder.glob("*.md")):
        data, body = fm.read(path)
        try:
            review = Review(int(data["pr"]), str(data.get("title", "")), str(data["author"]), str(data["merged"]), path)
            date.fromisoformat(review.merged)
        except (KeyError, TypeError, ValueError):
            problems.append(f"{path.name}: needs frontmatter pr, author and merged (YYYY-MM-DD)")
            continue
        if not floor <= review.merged <= today.isoformat():
            continue
        for line in body.splitlines():
            match = COMMENT.match(line)
            if line.startswith("- ") and match is None:
                problems.append(f"{path.name}: malformed comment {line[:60]!r}")
            elif match and match["by"].strip() != review.author:
                review.comments.append((match["at"], match["by"].strip(), match["path"].strip(), match["text"]))
        reviews.append(review)
    return reviews, problems


@dataclass
class Row:
    area: str
    day: str
    person: str
    kind: str
    text: str
    link: str = ""


def code_area(path: str) -> str:
    """Turn the first two code folders into a consult area."""
    name = " ".join(re.sub(r"[^a-z0-9 -]", " ", area(path).casefold()).split())
    return routing.area_name(name.lstrip(" -") or "top level")


def _github(login: str) -> str:
    person = "github:" + login
    return person if tasks.PERSON.fullmatch(person) and not BOT.search(login) else ""


def routing_evidence(folder: Path, today: date, days: int = DAYS) -> tuple[list[Row], list[str]]:
    """Read who changed, reviewed and approved each code area, with its PR as evidence."""
    reviews, problems = load(folder, today, days)
    rows = []
    for review in reviews:
        data, _ = fm.read(review.file)
        link, merged_by = "", ""
        if "url" in data:
            try:
                if not isinstance(data["url"], str):
                    raise ValueError
                link = tasks.single_line("url", data["url"])
                if any(char in link for char in "[]"):
                    raise ValueError
            except ValueError:
                problems.append(f"{review.file.name}: url must be single-line text without brackets")
                link = ""
        if "merged_by" in data:
            if isinstance(data["merged_by"], str):
                merged_by = data["merged_by"]
            else:
                problems.append(f"{review.file.name}: merged_by must be a login")
        lists = {}
        for key in ("approved", "files"):
            values = data.get(key, [])
            if not isinstance(values, list):
                problems.append(f"{review.file.name}: {key} must be a list of strings")
                values = []
            elif any(not isinstance(value, str) or not value.strip() for value in values):
                problems.append(f"{review.file.name}: {key} must contain non-empty strings")
                values = [value for value in values if isinstance(value, str) and value.strip()]
            lists[key] = values
        paths = lists["files"] if "files" in data else [path for _, _, path, _ in review.comments if path]
        areas = sorted({code_area(path) for path in paths})
        author = _github(review.author)
        text = f"#{review.number} " + " ".join(review.title.replace("·", "-").split()[:12])
        text = text.rstrip()
        for key in areas:
            if author:
                rows.append(Row(key, review.merged, author, "changed", text, link))
        approvers = {_github(login) for login in lists["approved"] + [merged_by]} - {"", author}
        for person in sorted(approvers):
            for key in areas:
                rows.append(Row(key, review.merged, person, "approved", text, link))
        comments = {}
        for at, by, path, words in review.comments:
            person = _github(by)
            if not person or AGENT_POSTED.search(words):
                continue
            try:
                date.fromisoformat(at)
            except ValueError:
                problems.append(f"{review.file.name}: invalid comment date {at!r}")
                continue
            for key in [code_area(path)] if path else areas:
                pair = key, person
                comments[pair] = max(at, comments.get(pair, at))
        rows.extend(Row(key, at, person, "reviewed", text, link) for (key, person), at in comments.items())
    return sorted(rows, key=lambda row: (row.area, row.day, row.person, row.kind, row.text, row.link)), problems


def reading(folder: Path, today: date, days: int = DAYS) -> dict:
    reviews, problems = load(folder, today, days)
    comments = [(review, *comment) for review in reviews for comment in review.comments]
    reviewers = Counter(by for _, _, by, _, _ in comments)
    areas = Counter(area(path) for _, _, _, path, _ in comments)
    people = lambda review: sum(not BOT.search(by) for _, by, _, _ in review.comments)
    # a person's correction is the strongest evidence of taste; where review bots write most comments, they would bury it
    ranked = sorted((review for review in reviews if review.comments), key=lambda r: (-people(r), -len(r.comments), -r.number))
    return {
        "window": {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
        "prs": len(reviews), "withComments": len(ranked), "comments": len(comments),
        "byPeople": sum(not BOT.search(by) for _, _, by, _, _ in comments),
        "reviewers": [{"who": who, "comments": n, "bot": bool(BOT.search(who))} for who, n in reviewers.most_common()],
        "areas": [{"area": name, "comments": n,
                   "prs": len({review.number for review, _, _, path, _ in comments if area(path) == name})}
                  for name, n in areas.most_common()],
        "read": [{"pr": review.number, "title": review.title, "comments": len(review.comments), "byPeople": people(review),
                  "file": review.file.as_posix()} for review in ranked],
        "problems": problems,
    }


def render(data: dict) -> str:
    out = [f"{data['window']['from']} to {data['window']['to']}: {data['prs']} merged PRs, {data['withComments']} with "
           f"review comments, {data['comments']} comments by someone other than the PR's author, {data['byPeople']} of them "
           f"by people", ""]
    if data["reviewers"]:
        out.append("Reviewers: " + ", ".join(f"{r['who']} {r['comments']}" + (" (bot)" if r["bot"] else "")
                                             for r in data["reviewers"]))
        out.append("Areas: " + ", ".join(f"{a['area']} {a['comments']} in {a['prs']} PRs" for a in data["areas"]))
        out += ["", "Read, most corrected by people first:"]
        out += [f"- #{r['pr']} {r['title']} · {r['byPeople']} by people, {r['comments']} in all · {r['file']}" for r in data["read"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)


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
        data, body = fm.read(path)
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


PUBLIC = ("site", "writing", "founders", "press", "jobs", "customers", "market")
INTERNAL = ("goals", "meetings", "docs", "plans", "chat", "mail", "crm", "reviews")   # the company's own statements of direction first
STALE_DAYS = 90


def coverage(vault, today: date) -> dict:
    """How much the portrait knows and from where: each section's lines, each kind of source's last reading (from the
    `source` and `read` keys of the Seeds notes), and the connected channels no seed has read yet."""
    from . import channels, portrait
    config = vault.config
    lines = portrait.load(config)
    sections = [{"section": name, "lines": len(found := [line for line in lines if line.section == name]),
                 "said": sum(line.basis == "said" for line in found), "seen": sum(line.basis == "seen" for line in found),
                 "guess": sum(line.basis == "guess" for line in found),
                 "newest": max((line.evidence for line in found), default="")} for name in portrait.SECTIONS]
    read: dict[str, str] = {}
    covered: set[str] = set()
    problems = []
    prefix = config.agent("Seeds/")
    for rel, note in sorted(vault.notes.items()):
        if not rel.startswith(prefix):
            continue
        kind, when = str(note.data.get("source") or ""), str(note.data.get("read") or "")
        if kind not in PUBLIC + INTERNAL or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            problems.append(f"{rel}: needs frontmatter source (one of {', '.join(PUBLIC + INTERNAL)}) and read (YYYY-MM-DD)")
            continue
        read[kind] = max(read.get(kind, ""), when)
        if note.data.get("channel"):
            covered.add(str(note.data["channel"]).casefold())
    floor = (today - timedelta(days=STALE_DAYS)).isoformat()
    sources = [{"source": kind, "where": "public" if kind in PUBLIC else "internal", "read": read.get(kind, ""),
                "status": "never" if kind not in read else "stale" if read[kind] < floor else "fresh"}
               for kind in PUBLIC + INTERNAL]
    unread = [row.channel for row in channels.live(channels.load(vault)) if row.channel.casefold() not in covered]
    return {"sections": sections, "sources": sources, "unreadChannels": unread, "problems": problems}


def render_coverage(data: dict) -> str:
    out = ["Portrait:"]
    out += [f"- {s['section']}: " + (f"{s['lines']} lines ({s['said']} said, {s['seen']} seen, {s['guess']} guess), newest {s['newest']}"
                                     if s["lines"] else "empty") for s in data["sections"]]
    for where in ("public", "internal"):
        out += ["", f"Sources, {where}:"]
        out += [f"- {s['source']}: " + (f"read {s['read']}" + (", stale" if s["status"] == "stale" else "") if s["read"] else "never read")
                for s in data["sources"] if s["where"] == where]
    if data["unreadChannels"]:
        out += ["", "Connected and never read for the portrait: " + ", ".join(data["unreadChannels"])]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)
