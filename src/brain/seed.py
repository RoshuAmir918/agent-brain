"""The seed pass's arithmetic for an organisation's portrait: which review corrections to read, grouped so the ones
that repeat stand out. The judgment (what a correction teaches, worded as a Taste line) is the seed skill's."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import fm

DAYS = 90
COMMENT = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2}) · (?P<by>[^·]+?) · (?P<path>[^·]*?) · (?P<text>.+?)\s*$")
BOT = re.compile(r"\[bot\]$|-bot$|^bot-", re.I)


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
            if line.startswith("- ") and (match := COMMENT.match(line)) is None:
                problems.append(f"{path.name}: malformed comment {line[:60]!r}")
            elif match and match["by"].strip() != review.author:
                review.comments.append((match["at"], match["by"].strip(), match["path"].strip(), match["text"]))
        reviews.append(review)
    return reviews, problems


def reading(folder: Path, today: date, days: int = DAYS) -> dict:
    reviews, problems = load(folder, today, days)
    comments = [(review, *comment) for review in reviews for comment in review.comments]
    reviewers = Counter(by for _, _, by, _, _ in comments)
    areas = Counter(area(path) for _, _, _, path, _ in comments)
    ranked = sorted((review for review in reviews if review.comments), key=lambda r: (-len(r.comments), -r.number))
    return {
        "window": {"from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
        "prs": len(reviews), "withComments": len(ranked), "comments": len(comments),
        "reviewers": [{"who": who, "comments": n, "bot": bool(BOT.search(who))} for who, n in reviewers.most_common()],
        "areas": [{"area": name, "comments": n,
                   "prs": len({review.number for review, _, _, path, _ in comments if area(path) == name})}
                  for name, n in areas.most_common()],
        "read": [{"pr": review.number, "title": review.title, "comments": len(review.comments),
                  "file": review.file.as_posix()} for review in ranked],
        "problems": problems,
    }


def render(data: dict) -> str:
    out = [f"{data['window']['from']} to {data['window']['to']}: {data['prs']} merged PRs, {data['withComments']} with "
           f"review comments, {data['comments']} comments by someone other than the PR's author", ""]
    if data["reviewers"]:
        out.append("Reviewers: " + ", ".join(f"{r['who']} {r['comments']}" + (" (bot)" if r["bot"] else "")
                                             for r in data["reviewers"]))
        out.append("Areas: " + ", ".join(f"{a['area']} {a['comments']} in {a['prs']} PRs" for a in data["areas"]))
        out += ["", "Read, most corrected first:"]
        out += [f"- #{r['pr']} {r['title']} · {r['comments']} · {r['file']}" for r in data["read"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)
