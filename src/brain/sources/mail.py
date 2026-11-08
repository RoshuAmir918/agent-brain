"""The owner's mail, read from the synced account files: each message's headers classified by whose it is, and the
owner's own side as evidence (whom they write to, the services that write to them). Headers only, never a body or a
subject in a row, which can carry codes (docs/image.md section 3). The seed's reading list is built on `read`."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from .. import channel_files, fm, routing, tasks
from ..routing import DAYS, Row
from ..evidence import Evidence
from . import Context, Nothing, Source, domain, globbed, hints, work_account

SOURCE = Source(id="mail", editions=("personal", "enterprise"), runs="runtime", where="own", rank=6, needs="google",
                files=("mail/*/*.md", "backfill/mail/*/*.md"), facets=("people", "places", "food & drink", "play"),
                about="whom the owner writes to and the services that write to them, from synced mail headers")
FORWARDED = re.compile(r"^\s*(?:>\s*)*(?:-+\s*Forwarded message\s*-+|Begin forwarded message:)", re.M | re.I)
OWN = ("sent", "transactional")   # the kinds that are the owner's own; the rest are someone else's
BOOKED = ("booking", "reservation", "resy.", "opentable.", "airbnb.", "expedia.", "hotel", "airline", "delta.", "united.",
          "tock.", "eventbrite.", "ticketmaster.")


@dataclass
class Mail:
    kind: str   # sent, forwarded, cc, transactional, group or person
    received: datetime
    sender: str
    senders: frozenset[str]
    to: str
    cc: str
    subject: str
    excluded: bool   # promotions, social, spam and the like: read for the window, never for what it says
    path: Path
    account: str

    @property
    def whose(self) -> str:
        return "own" if self.kind in OWN else "other"


def files(folder: Path) -> list[Path]:
    """An account's synced mail, the rolling copy first, then the one-time twelve-month backfill."""
    return globbed(SOURCE, folder)


def addresses(value: str) -> set[str]:
    return {email.casefold() for _, email in getaddresses([value]) if email}


def read(vault, accounts: list[tuple[str, Path]], owners: set[str]) -> tuple[list[Mail], list[str]]:
    """Every message the accounts hold, once each (the rolling copy wins), classified, and what could not be read."""
    out, problems = [], []
    for name, folder in accounts:
        seen = set()
        for path in files(folder):
            if path.stem in seen:
                continue
            seen.add(path.stem)   # the filename is the mail id
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
            out.append(Mail(kind, received, sender, frozenset(senders), data.get("to", ""), data.get("cc", ""), subject,
                            bool(channel_files.EXCLUDED_LABELS.intersection(labels)), path, name))
    return out, problems


def owned(vault) -> tuple[list[tuple[str, Path]], set[str]]:
    """The owner's synced accounts and the addresses that are the owner's."""
    return channel_files.synced_accounts(vault), set(channel_files.owner_accounts(vault))


def pointer(kind: str, key: str) -> str:
    return f"mail:{kind}/{hashlib.sha256(key.encode()).hexdigest()[:8]}"


NOTICES = 2   # a sender heard from once is a sign-up or a one-off, not a part of the owner's life


def rows(mails: list[Mail]) -> list[Evidence]:
    """Each row is dated by the newest mail it counts, so a rerun with nothing new writes the same rows. A
    correspondent or a sender reached through a work account is Work as well."""
    out = []
    written: dict[str, dict] = defaultdict(lambda: {"name": "", "named": "", "count": 0, "newest": "", "work": False})
    notices: dict[str, dict] = defaultdict(lambda: {"count": 0, "newest": "", "work": False})
    for mail in mails:
        if mail.excluded:
            continue
        day = mail.received.date().isoformat()
        if mail.kind == "sent":
            for name, address in getaddresses([mail.to]):
                if address:
                    entry = written[address.casefold()]
                    if name.strip() and day >= entry["named"]:   # the newest display name the owner used for them
                        entry["name"], entry["named"] = name.strip(), day
                    entry["count"] += 1
                    entry["newest"] = max(entry["newest"], day)
                    entry["work"] = entry["work"] or work_account(mail.account)
        elif mail.kind == "transactional":
            entry = notices[domain(min(mail.senders).split("@")[-1])]
            entry["count"] += 1
            entry["newest"] = max(entry["newest"], day)
            entry["work"] = entry["work"] or work_account(mail.account)
    for address, entry in written.items():
        out.append(Evidence(datetime.fromisoformat(entry["newest"]).date(), "talks",
                            f"Wrote to {entry['name'] or address.split('@')[0]} {entry['count']} times, last {entry['newest']}",
                            "own", ("people", "work") if entry["work"] else ("people",), pointer("to", address)))
    for site, entry in notices.items():
        if entry["count"] < NOTICES:
            continue
        found = hints(site, "shopping")
        if entry["work"] and "work" not in found:
            found = (*found, "work")
        out.append(Evidence(datetime.fromisoformat(entry["newest"]).date(),
                            "booked" if any(mark in site for mark in BOOKED) else "received",
                            f"{entry['count']} notices from {site}, last {entry['newest']}", "own", found,
                            pointer("from", site)))
    return out


def extract(ctx: Context) -> list[Evidence]:
    if ctx.config is None:
        raise ValueError("mail reads the runtime's synced accounts; run it where the vault is")
    if ctx.config.edition != "personal":
        raise Nothing("an organisation's mail is read for consults (brain consult seed), not as the owner's own")
    from ..vault import Vault
    vault = ctx.vault or Vault(ctx.config.vault_path)
    accounts, owners = owned(vault)
    mails, _ = read(vault, accounts, owners)
    if not mails:
        raise Nothing("no synced mail")
    return rows([mail for mail in mails if not ctx.since or mail.received.date() >= ctx.since])


# Company mail, for an organisation: who defers to whom, read for a consult seeder. Bodies are scanned for cues that
# never leave the scanner.
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


def deference_reading(vault, today: date, account: str = "all", days: int = DAYS) -> dict:
    """List company mail with deference cues, exposing only addresses, dates and pointers."""
    if vault.config.edition != "enterprise":
        raise SystemExit("brain consult seed: mail needs the enterprise edition")
    if days < 0:
        raise SystemExit("brain consult seed: --days must be non-negative")
    account = account.casefold()
    if account != "all" and account not in channel_files.owner_accounts(vault):
        raise SystemExit(f"brain consult seed: {account} is not an owner account")
    found = [(name, files(folder)) for name, folder in channel_files.synced_accounts(vault)
             if account == "all" or account == name]
    found = [(name, paths) for name, paths in found if paths]
    if not found:
        raise SystemExit("brain consult seed: no owner account has synced mail files"
                         + (f" for {account}" if account != "all" else ""))
    floor = today - timedelta(days=days)
    messages, problems = [], []
    for name, paths in found:
        for path in paths:
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
            "accounts": [name for name, _ in found], "messages": messages, "problems": problems}


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
