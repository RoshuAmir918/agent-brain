"""Read the owner's optional Demiurge sync, without fetching channels or changing files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from . import channels, fm, runtime
from .config import Config
from .vault import Vault

EMAIL = re.compile(r"[\w.!#$%&'*+=?^{}~-]+@[\w.-]+\.[a-zA-Z]{2,}")
FOLDER = re.compile(r"(?:^|/)channels/([^/\s`\"'<>()]+)")
EXCLUDED_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM", "TRASH"}
NOTICE_LIMIT = 30
# Mail no person wrote: bots, no-reply and notification senders. Gmail files many of them in the primary inbox. They
# are kept, because a payment due, a return deadline or a security alert is in them, but grouped by sender, so a flood
# from one service is one line.
AUTOMATED = re.compile(r'\[bot\]|(^|[<\s"._+-])(no-?reply|do-?not-?reply|notifications?|notify|mailer-daemon|postmaster|alerts?)@',
                       re.I)
CALENDAR_LIMIT = 12
CANVAS_LIMIT = 8
CANVAS_GRADED = timedelta(days=3)       # a grade counts as new for this long after Canvas records it
CANVAS_NEWS = timedelta(days=2)         # and an announcement for this long after it was posted
MAIL_LIMIT = 40


def accounts(vault: Vault) -> dict[str, str]:
    """Every live registry row that names an account, its own, its owner's or shared, with whose it is."""
    found: dict[str, str] = {}
    for row in channels.live(channels.load(vault)):
        identity, reach = EMAIL.search(row.identity), FOLDER.search(row.reach)
        account = identity[0].lower() if identity else reach[1] if reach else ""
        if EMAIL.fullmatch(account):
            account = account.lower()
        if account and account not in (".", "..") and "\\" not in account:
            found.setdefault(account, row.whose)
    return dict(sorted(found.items()))


def owner_accounts(vault: Vault) -> list[str]:
    return [account for account, whose in accounts(vault).items() if whose == "owner"]


def synced_accounts(vault: Vault, owner_only: bool = True) -> list[tuple[str, Path]]:
    home = runtime.configured()
    if not home:
        return []
    chosen = owner_accounts(vault) if owner_only else list(accounts(vault))
    return [(account, Path(home) / "channels" / account) for account in chosen]


SYNC_STALE = timedelta(hours=2)


def coverage(vault: Vault) -> dict | None:
    """What of the owner's Google can be seen, from the registry and the sync job's last run, so a briefing says so
    instead of going quiet. None outside a agent workspace (a vault without Demiurge has nothing to report)."""
    home = runtime.configured()
    if not home:
        return None
    named = accounts(vault)
    found = {"accounts": [f"{account} ({whose})" for account, whose in named.items()], "last_sync": None, "problem": ""}
    if not named:
        found["problem"] = "no mail or calendar account is connected"
        return found
    job = None
    job = next((j for j in runtime.jobs(Path(home)) if j.get("name") == "demiurge-sync"
                or "demiurge-sync" in str(j.get("script") or "")), None)
    if job is None:
        found["problem"] = "the mail and calendar sync is not set up"
        return found
    last = job.get("last_run_at")
    found["last_sync"] = last
    try:
        when = datetime.fromisoformat(str(last))
        when = when.replace(tzinfo=vault.config.zone) if when.tzinfo is None else when
    except ValueError:
        found["problem"] = "the mail and calendar sync has not run yet"
        return found
    now = vault.config.now()
    now = now.replace(tzinfo=vault.config.zone) if now.tzinfo is None else now
    if job.get("last_status") not in (None, "ok"):
        found["problem"] = f"the last mail and calendar sync failed ({' '.join(str(job.get('last_error') or job.get('last_status')).split())[:120]})"
    elif now - when > SYNC_STALE:
        found["problem"] = f"the mail and calendar sync last ran {when.astimezone(vault.config.zone):%a %b %-d %H:%M}, so this may be out of date"
    return found


def local_datetime(value: str, config: Config) -> datetime:
    """ISO dates/times use the owner's zone unless an explicit offset supplies it."""
    moment = datetime.fromisoformat(value)
    return config.local(moment)


def short_location(value: str) -> str:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return ""
    # A place may precede an address, or the address itself may be the first part.
    street = re.compile(r"^\d+\b")
    place = "" if street.match(parts[0]) else parts[0]
    city = ""
    if len(parts) > 1:
        city_index = 2 if street.match(parts[1]) else 1
        if city_index < len(parts):
            city = parts[city_index]
        if re.search(r"\d", city):
            city = ""
    return (f"{place}, {city}" if place and city else place or city)[:60]


@dataclass
class CalendarEvent:
    title: str
    start: datetime
    end: datetime
    all_day: bool
    id: str
    location: str
    response: str

    @property
    def last_day(self) -> date:
        # Google all-day ends, and timed ends exactly at midnight, are exclusive.
        return (self.end - timedelta(microseconds=1)).date() if self.end > self.start else self.start.date()

    def line(self, today: date) -> str:
        start, last = self.start.date(), self.last_day
        when = f"{start:%a %b %-d}"
        if self.all_day:
            when += f" – {last:%a %b %-d}" if last != start else " · all day"
        else:
            when += f" · {self.start:%H:%M}–{self.end:%H:%M}" if last == start else f" {self.start:%H:%M} – {self.end:%a %b %-d %H:%M}"
        if start < today <= last:
            when += f" · now, through {last:%a %b %-d}"
        title = " ".join(self.title.split())[:120]
        return f"- {when} · {title}" + (f" ({self.location})" if self.location else "") + (f" · {self.response}" if self.response else "")


def parse_calendar(text: str, account: str, config: Config) -> list[CalendarEvent]:
    events = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        fields = dict(re.findall(r"^(time|location|id|cancelled):[ \t]*(.*)$", body, re.M))
        if fields.get("cancelled") == "yes":
            continue
        attendees = re.findall(r"^attendee: (.+):\s*(\w+)\s*$", body, re.M)
        response = next((reply for who, reply in attendees if "(you)" in who), "")
        if not response:
            response = next((reply for who, reply in attendees if account.lower() in {email.lower() for _, email in getaddresses([who])}), "")
        if response.lower() == "declined":
            continue
        times = fields.get("time", "").split(" — ", 1)
        try:
            start = local_datetime(times[0].replace(" (all day)", ""), config)
            end = local_datetime(times[-1].replace(" (all day)", ""), config)
        except ValueError:
            continue
        if end < start:
            continue
        events.append(CalendarEvent(title, start, end, "(all day)" in times[0], fields.get("id", ""),
                                    short_location(fields.get("location", "")), response))
    return events


def calendar(vault: Vault, days: int = 7) -> str:
    today = vault.config.today()
    found = {}
    for account, folder in synced_accounts(vault):
        for offset in range(days + 1):
            path = folder / "calendar" / f"{today + timedelta(days=offset)}.md"
            try:
                events = parse_calendar(path.read_text(encoding="utf-8"), account, vault.config)
            except OSError:
                continue
            for event in events:
                if event.start.date() <= today + timedelta(days=days) and event.last_day >= today:
                    key = event.id or (event.title, event.start, event.end)
                    found.setdefault(key, event)
    def order(event: CalendarEvent):
        start = event.start.date()
        return (0 if start == today else 1 if start < today else 2, event.start, event.title)
    events = sorted(found.values(), key=order)[:CALENDAR_LIMIT]
    seen = coverage(vault)
    if seen is None:
        return "" if not events else f"## Calendar: today and the next {days} days\n\n" + "\n".join(event.line(today) for event in events)
    head = f"## Calendar: today and the next {days} days\n\n"
    if not owner_accounts(vault):
        return head + "- I can't see the calendar: no Google account of the owner's is connected."
    if seen["problem"].endswith(("not set up", "not run yet")):
        return head + f"- I can't see the calendar: {seen['problem']}."
    lines = [event.line(today) for event in events] or [f"- Nothing on the calendar for the next {days} days."]
    if seen["problem"]:
        lines.append(f"- Note: {seen['problem']}.")
    return head + "\n".join(lines)


def canvas_schools() -> list[Path]:
    """The owner's synced Canvas schools: folders under $AGENT_HOME/channels named for a host (no '@'), with courses."""
    home = runtime.configured()
    if not home:
        return []
    try:
        folders = sorted((Path(home) / "channels").iterdir())
    except OSError:
        return []
    return [f for f in folders if "@" not in f.name and "." in f.name and (f / "courses.md").is_file()]


def canvas_time(value: str, config: Config) -> datetime | None:
    """A time as the sync writes it ("2026-11-08 15:00 +00:00"), in the owner's zone; None when blank or unreadable."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M %z").astimezone(config.zone)
    except ValueError:
        return None


def parse_assignments(text: str, config: Config) -> list[dict]:
    """One course's assignments.md: a `# Assignments: <course> (<id>)` heading, then a `## <name>` section of
    `key: value` lines per assignment."""
    head, *sections = re.split(r"^## ", text, flags=re.M)
    course = re.search(r"^# Assignments: (.*) \(\d+\)$", head, re.M)
    found = []
    for section in sections:
        title, _, rest = section.partition("\n")
        fields = dict(line.split(": ", 1) for line in rest.splitlines() if ": " in line)
        found.append({"course": course[1] if course else "", "title": title.strip(),
                      "due": canvas_time(fields.get("due", ""), config), "submission": fields.get("submission", ""),
                      "score": fields.get("score", ""), "points": fields.get("points", ""),
                      "graded": canvas_time(fields.get("graded", ""), config)})
    return found


def canvas(vault: Vault, days: int = 7) -> str:
    """What is due on the owner's Canvas, what is missing, and what changed: new grades and announcements. Empty when
    no Canvas is synced, since most owners have none."""
    schools = canvas_schools()
    if not schools:
        return ""
    config = vault.config
    now = config.local()
    due, missing, graded, news = [], [], [], []
    for school in schools:
        for path in sorted((school / "courses").glob("*/assignments.md")):
            try:
                assignments = parse_assignments(path.read_text(encoding="utf-8"), config)
            except OSError:
                continue
            for item in assignments:
                state = item["submission"]
                done = state.startswith(("submitted", "graded", "pending_review"))
                if item["due"] and now <= item["due"] <= now + timedelta(days=days) and not done:
                    due.append(item)
                if "missing" in state:
                    missing.append(item)
                if item["graded"] and timedelta(0) <= now - item["graded"] <= CANVAS_GRADED:
                    graded.append(item)
        for path in sorted((school / "courses").glob("*/announcements/*/*.md")):
            try:
                data, _ = fm.read(path)
            except (OSError, ValueError):
                continue
            posted = canvas_time(str(data.get("posted") or ""), config)
            if posted and timedelta(0) <= now - posted <= CANVAS_NEWS:
                news.append((posted, str(data.get("title") or ""), str(data.get("course") or "")))
    lines = [f"- {i['due']:%a %b %-d %H:%M} · {i['title']} ({i['course']}) · {i['submission'] or 'unsubmitted'}"
             for i in sorted(due, key=lambda i: i["due"])[:CANVAS_LIMIT]] \
        or [f"- Nothing due on Canvas in the next {days} days that isn't already handed in."]
    lines += [f"- Missing: {i['title']} ({i['course']})" for i in missing[:CANVAS_LIMIT]]
    lines += [f"- Graded {i['graded']:%a %b %-d}: {i['title']} ({i['course']}) · {i['score']}/{i['points']}"
              for i in sorted(graded, key=lambda i: i["graded"], reverse=True)[:CANVAS_LIMIT]]
    lines += [f"- Announcement {posted:%a %b %-d}: {title} ({course})" for posted, title, course in sorted(news, reverse=True)[:CANVAS_LIMIT]]
    seen = coverage(vault)
    if seen and "sync" in seen["problem"]:
        lines.append(f"- Note: {seen['problem']}.")
    return f"## Canvas: due in the next {days} days, and what changed\n\n" + "\n".join(lines)


def worth_a_look(data: dict, own: set[str]) -> str:
    """"person" for mail someone wrote, "notice" for a receipt, alert or bot, "" for what nobody needs: a promotion,
    social or forum mail, spam, or the account's own sent mail."""
    labels = data.get("labels", [])
    labels = labels if isinstance(labels, list) else []
    sender = str(data.get("from", ""))
    if EXCLUDED_LABELS.intersection(labels) or "SENT" in labels or own & {e.lower() for _, e in getaddresses([sender])}:
        return ""
    return "notice" if "CATEGORY_UPDATES" in labels or AUTOMATED.search(sender) else "person"


def mail(vault: Vault, since: datetime) -> dict:
    config = vault.config
    since = config.local(since)
    named = accounts(vault)
    owners = set(named)
    filtered = dict(category=0, sent=0, before_since=0, invalid=0)
    messages, notices = [], {}
    for account, folder in synced_accounts(vault, owner_only=False):
        directory = folder / "mail"
        if not directory.is_dir():
            continue
        for day in sorted(directory.iterdir()):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) or day.name < since.date().isoformat() or not day.is_dir():
                continue
            for path in sorted(day.glob("*.md")):
                try:
                    data, _ = fm.read(path)
                    raw = data.get("date", "")
                    try:
                        received = local_datetime(str(raw), config)
                    except ValueError:
                        received = parsedate_to_datetime(str(raw))
                        received = config.local(received)
                    labels = data.get("labels", [])
                    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
                        raise ValueError("invalid labels")
                except (OSError, ValueError, TypeError, OverflowError):
                    filtered["invalid"] += 1
                    continue
                sender = str(data.get("from", ""))
                senders = {email.lower() for _, email in getaddresses([sender])}
                reason = ""
                if received < since:
                    reason = "before_since"
                elif "SENT" in labels or owners & senders:
                    reason = "sent"
                elif EXCLUDED_LABELS.intersection(labels):
                    reason = "category"
                if reason:
                    filtered[reason] += 1
                    continue
                item = {"date": received.isoformat(), "account": account, "whose": named.get(account, ""), "from": sender,
                        "subject": str(data.get("subject", "")), "labels": labels, "path": str(path)}
                if "CATEGORY_UPDATES" in labels or AUTOMATED.search(sender):
                    key = (account, min(senders) if senders else sender.lower())
                    group = notices.setdefault(key, {**item, "count": 0})
                    group["count"] += 1
                    if (received, str(path)) > (datetime.fromisoformat(group["date"]), group["path"]):
                        group.update(item)
                else:
                    messages.append(item)
    newest = lambda item: (datetime.fromisoformat(item["date"]), item["path"])
    messages.sort(key=newest, reverse=True)
    grouped = sorted(notices.values(), key=newest, reverse=True)
    return {"coverage": coverage(vault), "messages": messages[:MAIL_LIMIT], "notices": grouped[:NOTICE_LIMIT], "filtered": filtered,
            "omitted": max(0, len(messages) - MAIL_LIMIT) + max(0, len(grouped) - NOTICE_LIMIT)}


def render_mail(data: dict) -> str:
    def line(value):
        return " ".join(str(value).split())
    row = lambda message: " · ".join(line(message[key]) if key != "labels" else ",".join(line(label) for label in message[key])
                                     for key in ("date", "account", "from", "subject", "labels", "path")).replace(
        f" · {line(message['account'])} · ", f" · {line(message['account'])} ({message.get('whose') or 'unknown'}) · ", 1)
    notices = data.get("notices", [])
    seen = data.get("coverage")
    if seen is None:
        head = "coverage: not a Demiurge setup; no synced mail"
    elif seen["problem"] and not seen["last_sync"]:
        head = f"coverage: I can't see the mail: {seen['problem']}"
    else:
        head = (f"coverage: {', '.join(seen['accounts'])}; last sync {seen['last_sync']}"
                + (f"; {seen['problem']}" if seen["problem"] else ""))
    lines = [head, "people:", *[row(message) for message in data["messages"]],
             "notices (one line per sender, newest message shown):",
             *[row({**notice, "from": f"{notice['from']} ×{notice['count']}"}) for notice in notices]]
    counts = ", ".join(f"{count} {reason.replace('_', ' ')}" for reason, count in data["filtered"].items())
    lines.append(f"{len(data['messages'])} from people · {sum(n['count'] for n in notices)} notices from {len(notices)} senders"
                 f" · filtered: {counts} · {data['omitted']} over cap")
    return "\n".join(lines)
