"""How much the portrait knows and from where: each section's lines, each facet, each source declared for this
edition and when it was last read (the `source` and `read` keys of the Seeds notes), connected channels no seed has
read, unread backfill, and which top After lines got attention this week."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from . import channel_files

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
    from .sources import declared
    kinds = declared(config.edition)
    ids = [source.id for source in kinds]
    read: dict[str, str] = {}
    covered: set[str] = set()
    problems = []
    prefix = config.agent("Seeds/")
    for rel, note in sorted(vault.notes.items()):
        if not rel.startswith(prefix):
            continue
        kind, when = str(note.data.get("source") or ""), str(note.data.get("read") or "")
        if kind not in ids or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            problems.append(f"{rel}: needs frontmatter source (one of {', '.join(ids)}) and read (YYYY-MM-DD)")
            continue
        read[kind] = max(read.get(kind, ""), when)
        if note.data.get("channel"):
            covered.add(str(note.data["channel"]).casefold())
    floor = (today - timedelta(days=STALE_DAYS)).isoformat()
    sources = [{"source": source.id, "where": source.where, "needs": source.needs, "read": read.get(source.id, ""),
                "status": "never" if source.id not in read else "stale" if read[source.id] < floor else "fresh"}
               for source in kinds]
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
    for where in ("public", "internal", "own"):
        if not any(s["where"] == where for s in data["sources"]):
            continue
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
