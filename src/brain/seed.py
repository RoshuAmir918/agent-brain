"""The seed pass's reading lists. The judgment about what they teach is the seed skill's."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from . import channel_files, fm, routing, tasks

FORWARDED = re.compile(r"^\s*(?:>\s*)*(?:-+\s*Forwarded message\s*-+|Begin forwarded message:)", re.M | re.I)
PERSONAL_CAPS = {"sent": 100, "transactional": 150, "events": 100, "person": 100}


def _mail(folder: Path) -> list[Path]:
    """An account's synced mail, the rolling copy first, then the one-time twelve-month backfill."""
    return sorted((folder / "mail").glob("*/*.md")) + sorted((folder / "backfill/mail").glob("*/*.md"))


def personal(vault, today: date, account: str = "all", days: int | None = None) -> dict:
    """Classify the owner's synced headers and calendar, without exposing mail bodies."""
    if days is not None and days < 0:
        raise SystemExit("brain portrait seed: --days must be zero or more")
    owners = set(channel_files.owner_accounts(vault))
    account = account.casefold()
    if account != "all" and account not in owners:
        raise SystemExit(f"brain portrait seed: {account} is not an owner account")
    folders = [(name, folder) for name, folder in channel_files.synced_accounts(vault)
               if account == "all" or account == name]
    backfill = [name for name, folder in folders
                if any((folder / "backfill").glob("mail/*/*.md")) or any((folder / "backfill").glob("calendar/*.md"))]
    files = [(name, _mail(folder),
              sorted((folder / "calendar").glob("*.md")) + sorted((folder / "backfill/calendar").glob("*.md")))
             for name, folder in folders]
    files = [(name, mail, calendar) for name, mail, calendar in files if mail or calendar]
    if not files:
        raise SystemExit("brain portrait seed: no owner account has synced mail or calendar files"
                         + (f" for {account}" if account != "all" else ""))
    floor = today - timedelta(days=days) if days is not None else None
    dates, sent, transactional, people, events, problems = [], [], {}, [], {}, []
    other = dict(forwarded=0, cc=0, group=0)

    def addresses(value: str) -> set[str]:
        return {email.casefold() for _, email in getaddresses([value]) if email}

    def inside(day: date) -> bool:
        dates.append(day.isoformat())
        return floor is None or floor <= day <= today

    for name, mail, calendar in files:
        seen = set()
        for path in mail:
            if path.stem in seen:
                continue
            seen.add(path.stem)   # the filename is the mail id; the rolling copy comes first
            try:
                data, body = fm.read(path)
                raw = str(data.get("date", ""))
                try:
                    received = channel_files.local_datetime(raw, vault.config)
                except ValueError:
                    received = vault.config.local(parsedate_to_datetime(raw))
                labels = data.get("labels", [])
                if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
                    raise ValueError
                if any(not isinstance(data.get(key, ""), str) for key in ("from", "to", "cc", "subject")):
                    raise ValueError
                sender = data.get("from", "")
                senders = addresses(sender)
                if not senders:
                    raise ValueError
            except (OSError, ValueError, TypeError, OverflowError):
                problems.append(f"{path}: unreadable mail headers (date, from, to, cc, subject, labels)")
                continue
            if not inside(received.date()) or channel_files.EXCLUDED_LABELS.intersection(labels):
                continue
            to, cc = addresses(data.get("to", "")), addresses(data.get("cc", ""))
            subject = data.get("subject", "")
            if "SENT" in labels or owners & senders:
                kind = "sent"
            elif re.match(r"^\s*fw(?:d)?:", subject, re.I) or FORWARDED.search(body):
                kind = "forwarded"
            elif owners & cc and not owners & to:
                kind = "cc"
            elif owners & to and ("CATEGORY_UPDATES" in labels or channel_files.AUTOMATED.search(sender)):
                kind = "transactional"
            elif len((to | cc) - owners) > 1:
                kind = "group"
            else:
                kind = "person"
            item = {"kind": kind, "whose": "own" if kind in ("sent", "transactional") else "other",
                    "date": received.isoformat(), "from": sender, "subject": subject, "path": str(path), "account": name}
            if kind == "sent":
                sent.append(item | {"to": data.get("to", ""), "cc": data.get("cc", "")})
            elif kind == "transactional":
                transactional.setdefault(min(senders), []).append(item)
            elif kind == "person":
                people.append(item)
            else:
                other[kind] += 1
        for path in calendar:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                problems.append(f"{path}: unreadable calendar")
                continue
            for block in re.split(r"^## ", text, flags=re.M)[1:]:
                parsed = channel_files.parse_calendar("## " + block, name, vault.config)
                for event in parsed:
                    if not inside(event.start.date()):
                        continue
                    if event.response.casefold() != "accepted" and re.search(r"^attendee:[ \t]*\S", block, re.M):
                        continue
                    key = (event.id or event.title, event.start, event.end)
                    events.setdefault(key, (event, path, name))
    newest = lambda item: (datetime.fromisoformat(item["date"]), item["path"])
    notices = []
    for sender, items in transactional.items():
        items.sort(key=newest, reverse=True)
        notices.append(items[0] | {"from": sender, "count": len(items),
                                   "subjects": list(dict.fromkeys(item["subject"] for item in items))[:3]})
    recurring = {}
    for event, path, name in sorted(events.values(), key=lambda value: value[0].start, reverse=True):
        day = event.start.date().isoformat()
        group = recurring.setdefault(event.title, {"kind": "event", "whose": "own", "title": event.title,
                                                  "date": event.start.isoformat(), "count": 0, "next": "", "last": "",
                                                  "location": event.location, "path": str(path), "account": name})
        group["count"] += 1
        if day >= today.isoformat():
            group["next"] = min(group["next"] or day, day)
        else:
            group["last"] = max(group["last"], day)
    lists = {"sent": sent, "transactional": notices, "events": list(recurring.values()), "person": people}
    omitted = {}
    for kind, items in lists.items():
        items.sort(key=newest, reverse=True)
        omitted[kind] = max(0, len(items) - PERSONAL_CAPS[kind])
        lists[kind] = items[:PERSONAL_CAPS[kind]]
    return {"window": {"from": floor.isoformat() if floor else min(dates, default=today.isoformat()),
                       "to": today.isoformat() if floor else max(dates, default=today.isoformat())},
            "accounts": [name for name, _, _ in files],
            "backfillAccounts": backfill,
            "own": {kind: lists[kind] for kind in ("sent", "transactional", "events")},
            "other": other | {"person": lists["person"]}, "omitted": omitted, "problems": problems}


def render_personal(data: dict) -> str:
    def line(value):
        return " ".join(str(value).split())
    out = [f"{data['window']['from']} to {data['window']['to']} · {', '.join(data['accounts'])}"]
    if data["backfillAccounts"]:
        out.append("Backfill read: " + ", ".join(data["backfillAccounts"]))
    for kind, items in data["own"].items():
        out += ["", f"own · {kind}:"]
        for item in items:
            if kind == "events":
                detail = f"{item['title']} ×{item['count']} · next {item['next'] or 'none'} · last {item['last'] or 'none'} · {item['location']}"
            elif kind == "transactional":
                detail = f"{item['from']} ×{item['count']} · " + "; ".join(item["subjects"])
            else:
                detail = f"{item['from']} → {item['to']} · {item['subject']}"
            out.append(line(f"- {item['date']} · {detail} · {item['path']}"))
    out += ["", "other (never tellable): " + ", ".join(f"{data['other'][kind]} {kind}" for kind in ("forwarded", "cc", "group"))]
    out += [line(f"- {item['date']} · {item['from']} · {item['subject']} · {item['path']}") for item in data["other"]["person"]]
    out += ["Omitted over caps: " + ", ".join(f"{count} {kind}" for kind, count in data["omitted"].items())]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)


def clear_backfill(vault, account: str = "all") -> dict:
    """Remove only synced backfill mail and calendar files, keeping the completion marker."""
    account = account.casefold()
    if account != "all" and account not in channel_files.owner_accounts(vault):
        raise SystemExit(f"brain portrait seed: {account} is not an owner account")
    removed = {}
    for name, folder in channel_files.synced_accounts(vault):
        if account != "all" and account != name:
            continue
        counts = removed[name] = dict(mail=0, calendar=0)
        backfill = folder / "backfill"
        if folder.is_symlink() or backfill.is_symlink():
            continue
        for kind, glob, shape in (("mail", "*/*.md", r"\d{4}-\d{2}-\d{2}/[^/]+\.md"),
                                  ("calendar", "*.md", r"\d{4}-\d{2}-\d{2}\.md")):
            root = backfill / kind
            if root.is_symlink():
                continue
            for path in sorted(root.glob(glob)):
                if (re.fullmatch(shape, path.relative_to(root).as_posix()) and path.is_file()
                        and not path.is_symlink() and not path.parent.is_symlink()):
                    path.unlink()
                    counts[kind] += 1
            if kind == "mail":
                for day in sorted(root.glob("*")):
                    if (re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) and not day.is_symlink()
                            and day.is_dir() and not any(day.iterdir())):
                        day.rmdir()
    return removed


DAYS = 90   # the default window for routing evidence
DEFERENCE_CUES = {   # body-only hints for the operator's reading list; matches never leave the scanner
    "per": re.compile(r"\bper [A-Z][a-z]+\b"),
    "signed-off": re.compile(r"\bsigned off\b|\bsign-off from\b"),
    "check-with": re.compile(r"\bcheck(?:ed)? with [A-Z][a-z]+\b"),
    "ask": re.compile(r"\bask [A-Z][a-z]+\b"),
    "approved-by": re.compile(r"\bapproved by [A-Z][a-z]+\b"),
    "decides": re.compile(r"\b[A-Z][a-z]+ (?:decides|owns|approves|signs off)\b"),
}
CLAIM_WORDS = 16   # derived routing statements, never copied messages
CLAIM_URL = re.compile(r"\b[a-z][a-z0-9+.-]*:\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.I)   # URLs, including bare domains
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


@dataclass
class Row:
    area: str
    day: str
    person: str
    kind: str
    text: str
    link: str = ""


def deference_reading(vault, today: date, account: str = "all", days: int = DAYS) -> dict:
    """List company mail with deference cues, exposing only addresses, dates and pointers."""
    if vault.config.edition != "enterprise":
        raise SystemExit("brain consult seed: mail needs the enterprise edition")
    if days < 0:
        raise SystemExit("brain consult seed: --days must be non-negative")
    account = account.casefold()
    if account != "all" and account not in channel_files.owner_accounts(vault):
        raise SystemExit(f"brain consult seed: {account} is not an owner account")
    files = [(name, _mail(folder)) for name, folder in channel_files.synced_accounts(vault)
             if account == "all" or account == name]
    files = [(name, mail) for name, mail in files if mail]
    if not files:
        raise SystemExit("brain consult seed: no owner account has synced mail files"
                         + (f" for {account}" if account != "all" else ""))
    floor = today - timedelta(days=days)
    messages, problems = [], []
    for name, mail in files:
        for path in mail:
            try:
                data, body = fm.read(path)
                raw = str(data.get("date", ""))
                try:
                    received = channel_files.local_datetime(raw, vault.config)
                except ValueError:
                    received = vault.config.local(parsedate_to_datetime(raw))
                labels = data.get("labels", [])
                sender = data.get("from", "")
                if (not isinstance(labels, list) or any(not isinstance(label, str) for label in labels)
                        or not isinstance(sender, str)):
                    raise ValueError
                senders = [address.casefold() for _, address in getaddresses([sender])
                           if channel_files.EMAIL.fullmatch(address)]
                if not senders:
                    raise ValueError
            except (OSError, ValueError, TypeError, OverflowError):
                problems.append(f"{path}: unreadable mail headers (date, from, labels)")
                continue
            if not floor <= received.date() <= today or channel_files.EXCLUDED_LABELS.intersection(labels):
                continue
            cues = [cue for cue, pattern in DEFERENCE_CUES.items() if pattern.search(body)]
            if cues:
                messages.append({"path": str(path), "date": received.isoformat(), "from": senders[0],
                                 "account": name, "cues": cues})
    messages.sort(key=lambda item: (datetime.fromisoformat(item["date"]), item["path"]), reverse=True)
    return {"window": {"from": floor.isoformat(), "to": today.isoformat()},
            "accounts": [name for name, _ in files], "messages": messages, "problems": problems}


def render_deference(data: dict) -> str:
    """Render only the mail reading list's metadata and problems."""
    out = [f"{data['window']['from']} to {data['window']['to']} · {', '.join(data['accounts'])}"]
    out += [f"- {item['date']} · {item['from']} · {item['account']} · {', '.join(item['cues'])} · {item['path']}"
            for item in data["messages"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)


def deference_claims(text: str) -> tuple[list[Row], list[str]]:
    """Parse derived deference lines, reporting refusals without repeating their contents."""
    frontmatter = fm.FM_RE.match(text)
    start = len(text[:frontmatter.end()].splitlines()) if frontmatter else 0
    rows, problems = [], []
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if number <= start or not line or re.match(r"^#{1,6}(?:\s|$)", line):
            continue
        try:
            if not line.startswith("- "):
                raise ValueError("expected a dated claim bullet")
            parts = line[2:].split(" · ")
            if len(parts) > 5:
                raise ValueError("claim must omit the field separator")
            if len(parts) == 4:
                raise ValueError("link is required")
            if len(parts) != 5:
                raise ValueError("expected date, person, area, claim and link")
            day, person, area, claim, link = (part.strip() for part in parts)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError("date must be YYYY-MM-DD")
            try:
                date.fromisoformat(day)
            except ValueError:
                raise ValueError("date must be a valid YYYY-MM-DD") from None
            if channel_files.EMAIL.fullmatch(person):
                person = "email:" + person.casefold()
            elif person.startswith("email:"):
                person = "email:" + person[6:].casefold()
            if not tasks.PERSON.fullmatch(person):
                raise ValueError("an email address may use only letters, digits, dots, underscores and hyphens before @"
                                 if person.startswith("email:") else "person must be <platform>:<id> or a bare email address")
            area = routing.area_name(tasks.single_line("area", area))
            claim = tasks.single_line("claim", claim)
            if not claim:
                raise ValueError("claim is required")
            if len(claim.split()) > CLAIM_WORDS:
                raise ValueError("claim must be at most 16 words")
            if any(char in claim for char in '\"“”‘’`'):
                raise ValueError("claim must omit quotation marks and backticks")
            if channel_files.EMAIL.search(claim):
                raise ValueError("claim must omit email addresses")
            if re.search(r"\d{5,}", claim):
                raise ValueError("claim must omit runs of five or more digits")
            if CLAIM_URL.search(claim):
                raise ValueError("claim must omit URLs")
            link = tasks.single_line("link", link)
            if not link:
                raise ValueError("link is required")
            if any(char in link for char in "[]"):
                raise ValueError("link must omit brackets")
            rows.append(Row(area, day, person, "deferred-to", claim, link))
        except ValueError as error:
            problems.append(f"line {number}: {error}")
    return rows, problems


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


PUBLIC = ("site", "writing", "founders", "press", "jobs", "customers", "market")
INTERNAL = ("goals", "meetings", "docs", "plans", "chat", "mail", "crm", "reviews")   # the company's own statements of direction first
STALE_DAYS = 90


def coverage(vault, today: date) -> dict:
    """How much the portrait knows and from where: each section's lines, each kind of source's last reading (from the
    `source` and `read` keys of the Seeds notes), and the connected channels no seed has read yet."""
    from . import channels, portrait
    config = vault.config
    lines = portrait.image(config)
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
    unread = []
    for row in channels.live(channels.load(vault)):
        identity, reach = channel_files.EMAIL.search(row.identity), channel_files.FOLDER.search(row.reach)
        account = identity[0] if identity else reach[1] if reach else ""
        if row.channel.casefold() not in covered and not (row.whose == "owner" and account.casefold() in covered):
            unread.append(row.channel)
    backfill = [name for name, folder in channel_files.synced_accounts(vault) if (folder / "backfill/.done").is_file()
                and (any(path.is_file() for path in (folder / "backfill/mail").glob("*/*.md"))
                     or any(path.is_file() for path in (folder / "backfill/calendar").glob("*.md")))]
    return {"sections": sections, "sources": sources, "unreadChannels": unread, "unreadBackfill": backfill,
            "attention": attention(config, portrait.load(config), today), "problems": problems,
            "facets": [{"facet": facet.name, "rank": facet.rank, "lines": len(facet.lines),
                        "newest": max((line.evidence for line in facet.lines), default="")} for facet in portrait.facets(config)]}


def attention(config, lines, today: date, days: int = 7) -> list[dict]:
    """How often each After line was served in the last week: explore runs whose account names `portrait:<id>`, and
    offers recorded against it. A top line with none is a direction nobody is working toward."""
    from . import ledger, reception
    floor = (today - timedelta(days=days)).isoformat()
    runs: Counter = Counter()
    for path in ledger.discover(config):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = ledger.parse_row(line)
            if row and row.when[:10] >= floor:
                runs.update(set(re.findall(r"portrait:\^?([0-9a-f]{4})", row.account)))
    offers = Counter(row.portrait for _, row in reception.rows(config, today - timedelta(days=days)) if row.portrait)
    return [{"id": line.id, "text": line.text, "rank": line.rank, "runs": runs[line.id], "offers": offers[line.id]}
            for line in lines if line.section == "After" and line.id]


def render_coverage(data: dict) -> str:
    out = ["Portrait:"]
    out += [f"- {s['section']}: " + (f"{s['lines']} lines ({s['said']} said, {s['seen']} seen, {s['guess']} guess), newest {s['newest']}"
                                     if s["lines"] else "empty") for s in data["sections"]]
    if data["facets"]:
        out += ["", "Facets:"] + [f"- {f['facet']}: {f['lines']} lines, newest {f['newest'] or 'none'}" for f in data["facets"]]
    for where in ("public", "internal"):
        out += ["", f"Sources, {where}:"]
        out += [f"- {s['source']}: " + (f"read {s['read']}" + (", stale" if s["status"] == "stale" else "") if s["read"] else "never read")
                for s in data["sources"] if s["where"] == where]
    if data["unreadChannels"]:
        out += ["", "Connected and never read for the portrait: " + ", ".join(data["unreadChannels"])]
    if data["unreadBackfill"]:
        out += ["", "Unread backfill: " + ", ".join(data["unreadBackfill"])]
    if data["attention"]:
        out += ["", "After lines served this week (explore runs, offers):"]
        out += [f"- ^{a['id']} {a['text']}: {a['runs']} runs, {a['offers']} offers" + (" · nothing" if not a["runs"] + a["offers"] else "")
                for a in data["attention"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)


SEED_SOURCE = re.compile(r'^- \*\*Source:\*\* "seed:([^"\s]+)"$', re.M)


def seeds(vault) -> list[dict]:
    """Each owner account a seed has read or is reading: the last day a Seeds note recorded reading it, and the open
    task reading it now, so the owner's app can say the first portrait is on its way."""
    config = vault.config
    read: dict[str, str] = {}
    prefix = config.agent("Seeds/")
    for rel, note in vault.notes.items():
        account, when = str(note.data.get("channel") or "").casefold(), str(note.data.get("read") or "")
        if rel.startswith(prefix) and account and re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            read[account] = max(read.get(account, ""), when)
    reading = _reading(config)
    return [{"account": account, "read": read.get(account), "task": reading.get(account)}
            for account in sorted(read.keys() | reading.keys())]


def _reading(config) -> dict[str, str]:
    if not (config.vault_path / config.agent("Tasks.md")).exists():
        return {}
    registered, archived = tasks.load_tasks(config)
    found: dict[str, str] = {}
    for task in registered + archived:
        if tasks.is_open(task) and (match := SEED_SOURCE.search(task.body)):
            found.setdefault(match[1].casefold(), task.tid)
    return found


def hand_off_seed(vault, account: str, today: date) -> dict:
    """A newly connected owner account gets one handed-off task that runs the seed for it, unless a seed has read it or
    is reading it. The caller holds the vault lock."""
    config = vault.config
    if config.edition != "personal":
        raise ValueError("a seed task is for a person's own accounts; an organisation seeds with brain consult seed")
    account = account.strip().casefold()
    if not channel_files.EMAIL.fullmatch(account):
        raise ValueError("--hand-off needs one account address, not all")
    known = next((row for row in seeds(vault) if row["account"] == account), None)
    if known and known["task"]:
        return {"account": account, "task": known["task"], "status": "reading"}
    if known and known["read"]:
        return {"account": account, "task": None, "status": "read"}
    task = tasks.new_task(config, f"Learn {config.human_name} from {account}", f"seed:{account}", values={
        "delivers": f"the portrait and tellable facts from {account}, a Seeds note for it, and the reveal confirm",
        "next": f"until $AGENT_HOME/channels/{account}/backfill/.done exists the first sync is still running: set "
                "--resume an hour out and end the chunk. Then register the account in Channels.md if it is not there, "
                f"load the seed skill and run its personal pass for --account {account}"})
    tasks.hand_off(config, task.tid, today)
    return {"account": account, "task": task.tid, "status": "opened"}
