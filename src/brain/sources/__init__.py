"""The source registry (docs/image.md section 3): one module per kind of raw input, each turning it into evidence.
A module declares `SOURCE` and `extract(ctx) -> list[Evidence]`; adding a source is adding a module, nothing else."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType

RUNS = ("owner-mac", "runtime", "agent")
# What a link, a sender or an event title says about which part of a life it belongs to; first matches win in order.
HINTS = (("places", ("maps.", "goo.gl/maps", "yelp.", "tripadvisor.", "airbnb.", "booking.com", "zillow.", "redfin.",
                     "streeteasy.", "expedia.", "flight", "hotel", "trip ")),
         ("food & drink", ("opentable.", "resy.", "doordash.", "ubereats.", "seamless.", "tock.", "dinner", "lunch", "brunch",
                           "restaurant", "drinks")),
         ("music", ("spotify.", "music.apple.", "soundcloud.", "bandcamp.", "tidal.", "songlink.", "ra.co", "concert", "gig")),
         ("play", ("strava.", "chess.com", "lichess.", "steampowered.", "twitch.tv", "ticketmaster.", "eventbrite.", "partiful.",
                   "gym", "climb", "boulder", "run ", "running", "yoga", "tennis", "game", "match", "hike", "poker")),
         ("health", ("doctor", "dentist", "therapy", "clinic", "physio")),   # said-only: the judging pass never reads it
         ("reading & ideas", ("book club", "lecture", "talk:", "reading")))


def hints(text: str, default: str = "reading & ideas") -> tuple[str, ...]:
    text = text.casefold()
    return tuple(facet for facet, marks in HINTS if any(mark in text for mark in marks)) or (default,)


@dataclass(frozen=True)
class Source:
    id: str
    editions: tuple[str, ...]
    runs: str   # owner-mac: only the owner's machine holds it; runtime: synced channel files; agent: research
    facets: tuple[str, ...]   # the hints its rows carry
    about: str
    sensitive: bool = False   # the owner's private life: runs only when the owner starts it
    input: Path | None = None   # where the raw input is by default


@dataclass
class Context:
    today: date
    since: date | None = None
    input: Path | None = None
    config: object | None = None   # the vault's Config where there is one; a source on the owner's Mac has none
    vault: object | None = None   # the loaded Vault, when the caller has one


def registry() -> dict[str, ModuleType]:
    found = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        source = getattr(module, "SOURCE", None)
        if isinstance(source, Source):
            found[source.id] = module
    return dict(sorted(found.items()))


def nightly(vault, today: date, home: Path | None = None) -> list[str]:
    """Extract every source that reads what the runtime already holds, for this edition. A failing source is reported
    and skipped; the night goes on."""
    from .. import evidence
    config, report = vault.config, []
    for name, module in registry().items():
        if module.SOURCE.runs != "runtime" or config.edition not in module.SOURCE.editions:
            continue
        try:
            rows = module.extract(Context(today, None, None, config, vault))
            added = evidence.append(name, rows, home)
        except (ValueError, OSError) as error:
            report.append(f"evidence: {name}: not extracted ({error})")
            continue
        if added:
            report.append(f"evidence: {name}: {added} new row(s)")
    return report
