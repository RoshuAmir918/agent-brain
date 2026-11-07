"""The owner's own mail as evidence, from the synced account files: whom they write to and how often, and the
services that write to them. Only the owner's side: someone else's mail stays the seed's to read, never a row. Headers
only, never a body or a subject, which can carry codes (docs/image.md section 3)."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from email.utils import getaddresses

from ..evidence import Evidence
from . import Context, Source, hints

SOURCE = Source(id="mail", editions=("personal",), runs="runtime", facets=("people", "places", "food & drink", "play"),
                about="whom the owner writes to and the services that write to them, from synced mail headers")
BOOKED = ("booking", "reservation", "resy.", "opentable.", "airbnb.", "expedia.", "hotel", "airline", "delta.", "united.",
          "tock.", "eventbrite.", "ticketmaster.")


def pointer(kind: str, key: str) -> str:
    return f"mail:{kind}/{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def who(header: str) -> list[tuple[str, str]]:
    return [(name.strip() or address.split("@")[0], address.casefold()) for name, address in getaddresses([header]) if address]


def rows(data: dict) -> list[Evidence]:
    """Each row is dated by the newest mail it counts, so a rerun with nothing new writes the same rows."""
    out = []
    written: dict[str, dict] = defaultdict(lambda: {"name": "", "count": 0, "newest": ""})
    for item in data["own"]["sent"]:
        for name, address in who(item.get("to", "")):
            entry = written[address]
            entry["name"] = entry["name"] or name
            entry["count"] += 1
            entry["newest"] = max(entry["newest"], item["date"][:10])
    for address, entry in written.items():
        out.append(Evidence(datetime.fromisoformat(entry["newest"]).date(), "talks",
                            f"Wrote to {entry['name']} {entry['count']} times, last {entry['newest']}", "own", ("people",),
                            pointer("to", address)))
    for item in data["own"]["transactional"]:
        address = item["from"].casefold()   # the seed groups notices by sender address
        domain = address.split("@")[-1]
        verb = "booked" if any(mark in domain for mark in BOOKED) else "received"
        notices = "1 notice" if item["count"] == 1 else f"{item['count']} notices"
        out.append(Evidence(datetime.fromisoformat(item["date"]).date(), verb, f"{notices} from {domain}, last {item['date'][:10]}",
                            "own", hints(domain, "home & routine"), pointer("from", address)))
    return out


def extract(ctx: Context) -> list[Evidence]:
    if ctx.config is None:
        raise ValueError("mail reads the runtime's synced accounts; run it where the vault is")
    from .. import seed
    from ..vault import Vault
    try:
        data = seed.personal(ctx.vault or Vault(ctx.config.vault_path), ctx.today)
    except SystemExit as error:
        if "has synced" in str(error):
            return []   # nothing connected yet is not a failure   # the seed's reading list says why it has nothing
        raise ValueError(str(error)) from None
    return rows(data)
