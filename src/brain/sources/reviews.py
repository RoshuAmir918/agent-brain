"""Merged pull requests and the review comments on them, from files the seed skill writes per PR
(`$AGENT_HOME/channels/<owner>-<repo>/reviews/<number>.md`): a reading list for the portrait's Taste, the routing
history a consult seeder imports, and what reception counts as a review (docs/image.md section 3)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .. import fm, routing, tasks
from ..routing import DAYS, Row
from ..evidence import Evidence
from . import Context, Nothing, Source, globbed

SOURCE = Source(id="reviews", editions=("enterprise",), runs="runtime", where="internal", rank=8, needs="github", files=("reviews/*.md",),
                facets=("engineering",), about="merged pull requests and the corrections reviews keep making")

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
        try:
            data, body = fm.read(path)
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


WORDS = 30   # a correction is kept as its first words: enough to say what the team insists on


def rows(reviews: list[Review]) -> list[Evidence]:
    """Each correction a person made on a merged PR, dated the day they made it: how the organisation likes work done.
    A bot's comment, or an agent's under a person's account, is not a person's taste."""
    out = []
    for review in reviews:
        for at, by, path, text in review.comments:
            if BOT.search(by) or AGENT_POSTED.search(text):
                continue
            words = text.split()
            said = " ".join(words[:WORDS]) + (" …" if len(words) > WORDS else "")
            out.append(Evidence(date.fromisoformat(at), "corrected", f"On #{review.number} {review.title}: {said}",
                                f"other:{by}", ("engineering",), f"reviews:{review.file.parent.parent.name}#{review.number}",
                                {"area": area(path)}))
    return out


def extract(ctx: Context) -> list[Evidence]:
    from .. import runtime
    home = runtime.configured()
    folders = sorted(path for path in (home / "channels").glob("*") if path.is_dir()) if home else []
    days = (ctx.today - ctx.since).days if ctx.since else DAYS
    found = []
    for folder in folders:
        if globbed(SOURCE, folder):
            found += load(folder / "reviews", ctx.today, days)[0]
    if not found:
        raise Nothing("no review files")
    return rows(found)
