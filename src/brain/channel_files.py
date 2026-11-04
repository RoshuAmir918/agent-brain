"""Read the owner's optional Demiurge sync, without fetching channels or changing files."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from . import channels, fm
from .config import Config
from .vault import Vault

EMAIL = re.compile(r"[\w.!#$%&'*+=?^{}~-]+@[\w.-]+\.[a-zA-Z]{2,}")
FOLDER = re.compile(r"(?:^|/)channels/([^/\s`\"'<>()]+)")
EXCLUDED_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS", "SPAM", "TRASH"}
# Mail no person wrote: bots, no-reply and notification senders. Gmail files many of them in the primary inbox.
AUTOMATED = re.compile(r'\[bot\]|(^|[<\s"._+-])(no-?reply|do-?not-?reply|notifications?|notify|mailer-daemon|postmaster|alerts?)@',
                       re.I)
CALENDAR_LIMIT = 12
MAIL_LIMIT = 40


def owner_accounts(vault: Vault) -> list[str]:
    accounts = set()
    for row in channels.live(channels.load(vault), "owner"):
        identity, reach = EMAIL.search(row.identity), FOLDER.search(row.reach)
        account = identity[0].lower() if identity else reach[1] if reach else ""
        if EMAIL.fullmatch(account):
            account = account.lower()
        if account and account not in (".", "..") and "\\" not in account:
            accounts.add(account)
    return sorted(accounts)


def synced_accounts(vault: Vault) -> list[tuple[str, Path]]:
    home = os.environ.get("HERMES_HOME")
    if not home:
        return []
    return [(account, Path(home) / "channels" / account) for account in owner_accounts(vault)]


def local_datetime(value: str, config: Config) -> datetime:
    """ISO dates/times use the owner's zone unless an explicit offset supplies it."""
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=config.zone) if moment.tzinfo is None else moment.astimezone(config.zone)


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
    if not events:
        return ""
    return f"## Calendar: today and the next {days} days\n\n" + "\n".join(event.line(today) for event in events)


def mail(vault: Vault, since: datetime) -> dict:
    config = vault.config
    since = since.replace(tzinfo=config.zone) if since.tzinfo is None else since.astimezone(config.zone)
    owners = {account.lower() for account in owner_accounts(vault)}
    filtered = dict(category=0, automated=0, sent=0, before_since=0, invalid=0)
    messages = []
    for account, folder in synced_accounts(vault):
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
                        received = received.replace(tzinfo=config.zone) if received.tzinfo is None else received.astimezone(config.zone)
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
                elif AUTOMATED.search(sender):
                    reason = "automated"
                if reason:
                    filtered[reason] += 1
                    continue
                messages.append({"date": received.isoformat(), "account": account, "from": sender,
                                 "subject": str(data.get("subject", "")), "labels": labels, "path": str(path)})
    messages.sort(key=lambda item: (datetime.fromisoformat(item["date"]), item["path"]), reverse=True)
    return {"messages": messages[:MAIL_LIMIT], "filtered": filtered, "omitted": max(0, len(messages) - MAIL_LIMIT)}


def render_mail(data: dict) -> str:
    def line(value):
        return " ".join(str(value).split())
    lines = [" · ".join(line(message[key]) if key != "labels" else ",".join(line(label) for label in message[key])
                        for key in ("date", "account", "from", "subject", "labels", "path")) for message in data["messages"]]
    counts = ", ".join(f"{count} {reason.replace('_', ' ')}" for reason, count in data["filtered"].items())
    lines.append(f"{len(data['messages'])} shown · filtered: {counts} · {data['omitted']} over cap")
    return "\n".join(lines)
