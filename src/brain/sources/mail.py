"""The owner's mail, read from the synced account files: each message's headers classified by whose it is, and the
owner's own side as evidence (whom they write to, the services that write to them). Headers only, never a body or a
subject in a row, which can carry codes (docs/image.md section 3). The seed's reading list is built on `read`."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from .. import channel_files, fm
from ..evidence import Evidence
from . import Context, Nothing, Source, hints

SOURCE = Source(id="mail", editions=("personal",), runs="runtime", facets=("people", "places", "food & drink", "play"),
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
    return sorted((folder / "mail").glob("*/*.md")) + sorted((folder / "backfill/mail").glob("*/*.md"))


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


def rows(mails: list[Mail]) -> list[Evidence]:
    """Each row is dated by the newest mail it counts, so a rerun with nothing new writes the same rows."""
    out = []
    written: dict[str, dict] = defaultdict(lambda: {"name": "", "named": "", "count": 0, "newest": ""})
    notices: dict[str, dict] = defaultdict(lambda: {"count": 0, "newest": ""})
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
        elif mail.kind == "transactional":
            entry = notices[min(mail.senders)]
            entry["count"] += 1
            entry["newest"] = max(entry["newest"], day)
    for address, entry in written.items():
        out.append(Evidence(datetime.fromisoformat(entry["newest"]).date(), "talks",
                            f"Wrote to {entry['name'] or address.split('@')[0]} {entry['count']} times, last {entry['newest']}", "own", ("people",),
                            pointer("to", address)))
    for address, entry in notices.items():
        domain = address.split("@")[-1]
        notice = "1 notice" if entry["count"] == 1 else f"{entry['count']} notices"
        out.append(Evidence(datetime.fromisoformat(entry["newest"]).date(),
                            "booked" if any(mark in domain for mark in BOOKED) else "received",
                            f"{notice} from {domain}, last {entry['newest']}", "own", hints(domain, "home & routine"),
                            pointer("from", address)))
    return out


def extract(ctx: Context) -> list[Evidence]:
    if ctx.config is None:
        raise ValueError("mail reads the runtime's synced accounts; run it where the vault is")
    from ..vault import Vault
    vault = ctx.vault or Vault(ctx.config.vault_path)
    accounts, owners = owned(vault)
    mails, _ = read(vault, accounts, owners)
    if not mails:
        raise Nothing("no synced mail")
    return rows([mail for mail in mails if not ctx.since or mail.received.date() >= ctx.since])
