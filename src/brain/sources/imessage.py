"""The owner's iMessage history as evidence: who they talk with and how closely, their group chats, and the links
shared, read only from macOS's `chat.db` on the owner's own Mac. No message text leaves this module; every row is
derived (docs/image.md section 3)."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..evidence import Evidence
from . import Context, Source, hints

SOURCE = Source(id="imessage", editions=("personal",), runs="owner-mac", where="own", rank=8, facets=("people", "places", "music", "play", "reading & ideas"),
                sensitive=True, about="who the owner texts and how closely, their group chats, and the links shared",
                input=Path.home() / "Library/Messages/chat.db")

EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)   # Apple's reference date; `message.date` counts nanoseconds from it
GAP = timedelta(hours=6)    # a message after this long a silence starts a conversation
RECENT = 90
SMALL_GROUP = 6   # a group this size or smaller is closeness, not a crowd
SOCIAL = {"x.com", "twitter.com", "www.tiktok.com", "tiktok.com", "vm.tiktok.com", "www.instagram.com", "instagram.com",
          "www.reddit.com", "reddit.com", "old.reddit.com", "www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com",
          "www.facebook.com", "facebook.com", "www.threads.net", "threads.net", "giphy.com", "media.giphy.com", "tenor.com"}
PER_DOMAIN = 20   # links listed one by one from any one site; the rest are counted
URL = re.compile(rb"https?://[^\s<>\"'\x00-\x1f\x7f-\xff]+")
KEEP_QUERY = {"youtube.com": "v", "www.youtube.com": "v", "m.youtube.com": "v"}
SECRETS = re.compile(r"token|code|otp|reset|password|signin|login|auth|verify|unsubscribe|magic", re.I)


@dataclass
class Person:
    key: str
    name: str
    handles: set[str] = field(default_factory=set)
    sent: int = 0          # by the owner, to them
    received: int = 0
    direct_chats: set[int] = field(default_factory=set)
    group_chats: set[int] = field(default_factory=set)
    first: datetime | None = None
    last: datetime | None = None
    started_by_owner: int = 0
    started_by_them: int = 0
    days: set[str] = field(default_factory=set)   # days in the recent window with a message either way
    group_days: set[str] = field(default_factory=set)   # the same, in small groups they share with the owner

    def seen(self, at: datetime) -> None:
        self.first = at if self.first is None or at < self.first else self.first
        self.last = at if self.last is None or at > self.last else self.last


def when(raw: int) -> datetime:
    """`message.date` is nanoseconds since 2001 on current macOS and seconds on old databases."""
    return EPOCH + timedelta(seconds=raw / 1e9 if raw > 1e11 else raw)


def body_text(blob: bytes | None) -> str:
    """The text inside an `attributedBody` typedstream, where current macOS keeps most messages' words."""
    if not blob:
        return ""
    at = blob.find(b"NSString")
    if at < 0:
        return ""
    rest = blob[at + len(b"NSString"):]
    plus = rest.find(b"+")
    if plus < 0:
        return ""
    rest = rest[plus + 1:]
    if not rest:
        return ""
    size, rest = rest[0], rest[1:]
    if size == 0x81:
        size, rest = int.from_bytes(rest[:2], "little"), rest[2:]
    elif size == 0x82:
        size, rest = int.from_bytes(rest[:4], "little"), rest[4:]
    return rest[:size].decode("utf-8", "replace")


def digits(handle: str) -> str:
    """A phone number by its last ten digits, so +1 (555) 010-0199 and 5550100199 are one person; an address as written."""
    if "@" in handle:
        return handle.strip().lower()
    only = re.sub(r"\D", "", handle)
    return only[-10:] if len(only) >= 10 else only


def contacts(books: list[Path]) -> dict[str, str]:
    """Names from the Contacts databases, by phone digits and lower-cased email."""
    names: dict[str, str] = {}
    for book in books:
        try:
            db = sqlite3.connect(f"file:{book}?mode=ro", uri=True)
            people = {pk: " ".join(part for part in (first, last) if part) or (org or "")
                      for pk, first, last, org in db.execute("SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD")}
            for owner, number in db.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if people.get(owner) and number:
                    names.setdefault(digits(number), people[owner])
            for owner, address in db.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if people.get(owner) and address:
                    names.setdefault(address.strip().lower(), people[owner])
            db.close()
        except sqlite3.Error:
            continue
    return names


def default_books() -> list[Path]:
    base = Path.home() / "Library/Application Support/AddressBook"
    return sorted(base.glob("AddressBook-v22.abcddb")) + sorted(base.glob("Sources/*/AddressBook-v22.abcddb"))


def clean(url: str) -> str | None:
    """A shared link without its tracking and session parameters; none at all when it looks like it carries a secret."""
    url = url.rstrip(".,;:!?)]}'\"")
    parts = urlsplit(url)
    if not parts.netloc or SECRETS.search(parts.path + "?" + parts.query):
        return None
    keep = KEEP_QUERY.get(parts.netloc.lower())
    query = "&".join(p for p in parts.query.split("&") if keep and p.startswith(keep + "=")) if keep else ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def export(db: Path, books: list[Path], now: datetime, days: int | None = None) -> dict:
    """Read the history (the last `days` days, or all of it) and return people, groups and links, nothing verbatim."""
    names = contacts(books)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    chats = {rowid: (style, display or "") for rowid, style, display in conn.execute("SELECT ROWID, style, display_name FROM chat")}
    members: dict[int, set[str]] = defaultdict(set)
    for chat_id, handle in conn.execute("SELECT j.chat_id, h.id FROM chat_handle_join j JOIN handle h ON h.ROWID = j.handle_id"):
        members[chat_id].add(handle)
    floor = None if days is None else int((now - timedelta(days=days) - EPOCH).total_seconds() * 1e9)
    rows = conn.execute(
        "SELECT m.date, m.is_from_me, h.id, j.chat_id, m.text, m.attributedBody FROM message m "
        "JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN handle h ON h.ROWID = m.handle_id "
        "WHERE coalesce(m.associated_message_type, 0) = 0 AND coalesce(m.item_type, 0) = 0 AND coalesce(m.is_system_message, 0) = 0"
        + (" AND m.date > ?" if floor is not None else "") + " ORDER BY j.chat_id, m.date",
        (floor,) if floor is not None else ())
    people: dict[str, Person] = {}
    groups: dict[int, dict] = {}
    links: dict[str, dict] = {}
    recent = (now - timedelta(days=RECENT)).date().isoformat()

    def person(handle: str) -> Person:
        key = digits(handle)
        if key not in people:
            people[key] = Person(key, names.get(key, ""))
        people[key].handles.add(handle)
        return people[key]

    last_at: dict[int, datetime] = {}
    for raw, mine, handle, chat_id, text, blob in rows:
        at = when(raw)
        style, display = chats.get(chat_id, (45, ""))
        group = style == 43 or len(members.get(chat_id, ())) > 1
        started = chat_id not in last_at or at - last_at[chat_id] > GAP
        last_at[chat_id] = at
        if group:
            g = groups.setdefault(chat_id, {"name": display, "members": len(members.get(chat_id, ())), "messages": 0, "mine": 0, "last": at})
            g["messages"] += 1
            g["mine"] += bool(mine)
            g["last"] = max(g["last"], at)
            counterparts = [person(h) for h in members.get(chat_id, ())]
        else:
            counterparts = [person(h) for h in (members.get(chat_id) or ({handle} if handle else set()))]
        small = group and len(members.get(chat_id, ())) <= SMALL_GROUP
        for p in counterparts:
            p.seen(at)
            (p.group_chats if group else p.direct_chats).add(chat_id)
            if small and at.date().isoformat() >= recent:
                p.group_days.add(at.date().isoformat())
            if not group:
                if mine:
                    p.sent += 1
                else:
                    p.received += 1
                if started:
                    if mine:
                        p.started_by_owner += 1
                    else:
                        p.started_by_them += 1
                if at.date().isoformat() >= recent:
                    p.days.add(at.date().isoformat())
        words = (text or "").encode("utf-8") + b" " + (blob or b"")
        for match in set(URL.findall(words)):
            url = clean(match.decode("utf-8", "replace"))
            if not url:
                continue
            where = (display or "a group chat") if group else (counterparts[0].name or "someone") if counterparts else "someone"
            link = links.setdefault(url, {"url": url, "domain": urlsplit(url).netloc, "count": 0, "mine": 0, "first": at, "last": at, "where": Counter()})
            link["count"] += 1
            link["mine"] += bool(mine)
            link["first"], link["last"] = min(link["first"], at), max(link["last"], at)
            link["where"][where] += 1
    conn.close()

    def closeness(p: Person) -> float:
        """Days talking one to one in the last 90, weighted by how even the exchange is, plus half a day for each day in a
        small group with them: where people mostly talk in small groups, that is where closeness shows."""
        sent, received = p.sent, p.received
        both = min(sent, received) / max(sent, received) if max(sent, received) else 0.0
        return round(len(p.days) * (0.5 + both) + 0.5 * len(p.group_days - p.days), 2)

    social = [link for link in links.values() if link["domain"] in SOCIAL]
    per_domain: Counter = Counter()
    listed = []
    for link in sorted((link for link in links.values() if link["domain"] not in SOCIAL), key=lambda link: (link["last"], link["count"]), reverse=True):
        per_domain[link["domain"]] += 1
        if per_domain[link["domain"]] <= PER_DOMAIN:
            listed.append({"url": link["url"], "domain": link["domain"], "count": link["count"], "mine": link["mine"],
                           "first": link["first"].date().isoformat(), "last": link["last"].date().isoformat(),
                           "where": [w for w, _ in link["where"].most_common(3)]})
    listed.sort(key=lambda link: (link["domain"], link["last"]), reverse=False)
    ranked = sorted((p for p in people.values() if p.sent + p.received or p.group_chats), key=lambda p: (-closeness(p), -(p.sent + p.received)))
    return {
        "people": [{"name": p.name or f"unknown ({next(iter(p.handles))[-4:]})", "handles": sorted(p.handles), "sent": p.sent,
                    "received": p.received, "direct": len(p.direct_chats), "groups": len(p.group_chats),
                    "first": p.first.date().isoformat() if p.first else "", "last": p.last.date().isoformat() if p.last else "",
                    "startedByOwner": p.started_by_owner, "startedByThem": p.started_by_them, "daysRecent": len(p.days),
                    "groupDaysRecent": len(p.group_days),
                    "closeness": closeness(p)} for p in ranked],
        "groups": [{"name": g["name"] or f"{g['members']} people", "members": g["members"], "messages": g["messages"],
                    "mine": g["mine"], "last": g["last"].date().isoformat()} for g in sorted(groups.values(), key=lambda g: -g["messages"])],
        "links": listed,
        "media": media(social),
    }


def media(links: list[dict]) -> dict:
    """Social and video links, counted rather than listed: how much of each site is shared, by the owner and to them, and
    the communities and accounts that recur, which say more about taste than any single post."""
    sites: dict[str, dict] = {}
    names: dict[str, Counter] = {"subreddits": Counter(), "accounts": Counter()}
    for link in links:
        site = link["domain"].removeprefix("www.").removeprefix("m.").removeprefix("old.").removeprefix("vm.")
        site = {"twitter.com": "x.com", "youtu.be": "youtube.com"}.get(site, site)
        row = sites.setdefault(site, {"site": site, "shared": 0, "mine": 0, "where": Counter()})
        row["shared"] += link["count"]
        row["mine"] += link["mine"]
        row["where"].update(link["where"])
        path = urlsplit(link["url"]).path
        if sub := re.match(r"/r/([A-Za-z0-9_]+)", path):
            names["subreddits"][sub[1]] += link["count"]
        elif site == "x.com" and (who := re.match(r"/([A-Za-z0-9_]{1,15})/status/", path)):
            names["accounts"][f"x:{who[1]}"] += link["count"]
        elif site == "tiktok.com" and (who := re.match(r"/@([A-Za-z0-9_.]+)", path)):
            names["accounts"][f"tiktok:{who[1]}"] += link["count"]
    return {"sites": [{**{k: v for k, v in row.items() if k != "where"}, "where": [w for w, _ in row["where"].most_common(3)]}
                      for row in sorted(sites.values(), key=lambda row: -row["shared"])],
            "subreddits": names["subreddits"].most_common(15), "accounts": names["accounts"].most_common(15)}


PEOPLE = 60   # the closest people, by closeness; the rest of an address book is not the owner's life
GROUPS = 30
def pointer(kind: str, key: str) -> str:
    """A stable pointer that names no phone number or address: the same person or link gets the same one each run."""
    return f"imessage:{kind}/{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def rows(data: dict, today) -> list[Evidence]:
    """The export as evidence. Counts are a snapshot dated the day they were taken; each link is dated its last share."""
    out = []
    for person in data["people"][:PEOPLE]:
        fact = (f"Texts with {person['name']}: {person['daysRecent']} of the last {RECENT} days one to one, "
                f"{person['groupDaysRecent']} in small groups; {person['sent']} sent, {person['received']} received; "
                f"conversations started {person['startedByOwner']} by the owner, {person['startedByThem']} by them; "
                f"since {person['first'] or 'unknown'}")
        out.append(Evidence(today, "talks", fact, "own", ("people",), pointer("person", ",".join(person["handles"])),
                            {"closeness": str(person["closeness"])}))
    for group in data["groups"][:GROUPS]:
        out.append(Evidence(today, "talks", f"In the group chat {group['name']} ({group['members']} people): "
                                            f"{group['messages']} messages, {group['mine']} by the owner, last {group['last']}",
                            "own", ("people",), pointer("group", group["name"])))
    for link in data["links"]:
        whose = "own" if link["mine"] else f"other:{link['where'][0] if link['where'] else 'someone'}"
        where = ", ".join(link["where"]) or "a chat"
        out.append(Evidence(datetime.fromisoformat(link["last"]).date(), "shared",
                            f"{link['url']} shared {link['count']} times ({link['mine']} by the owner) in {where}",
                            whose, hints(link["domain"]), pointer("link", link["url"])))
    for site in data["media"]["sites"]:
        out.append(Evidence(today, "shared", f"{site['shared']} links from {site['site']} shared, {site['mine']} by the owner",
                            "own", hints(site["site"]), pointer("site", site["site"])))
    for kind in ("subreddits", "accounts"):
        if data["media"][kind]:
            names = ", ".join(f"{name} ({count})" for name, count in data["media"][kind])
            out.append(Evidence(today, "shared", f"Most shared {kind}: {names}", "own", ("reading & ideas",),
                                pointer("media", kind)))
    return out


def extract(ctx: Context) -> list[Evidence]:
    db = ctx.input or SOURCE.input
    if not db.is_file():
        raise ValueError(f"no Messages database at {db}")
    now = datetime.combine(ctx.today, datetime.min.time(), timezone.utc) + timedelta(days=1)
    days = (ctx.today - ctx.since).days + 1 if ctx.since else None
    try:
        data = export(db, default_books(), now, days)
    except sqlite3.Error as error:
        raise ValueError(f"could not read {db} ({error}); the terminal needs Full Disk Access in System Settings, "
                         "Privacy & Security") from error
    return rows(data, ctx.today)
