"""The source registry (docs/image.md section 3): one module per kind of raw input, each turning it into evidence.
A module declares `SOURCE` and `extract(ctx) -> list[Evidence]`; adding a source is adding a module, nothing else."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable

RUNS = ("owner-mac", "runtime", "agent")


class Nothing(ValueError):
    """The source has nothing to read: no account connected, no file synced. Not a failure."""


from .hinting import HINTS, hints  # noqa: F401  (sources import hints from the registry)


@dataclass(frozen=True)
class Source:
    id: str
    editions: tuple[str, ...]
    runs: str   # owner-mac: only the owner's machine holds it; runtime: synced channel files; agent: research
    facets: tuple[str, ...]   # the hints its rows carry
    about: str
    sensitive: bool = False   # the owner's private life: runs only when the owner starts it
    input: Path | None = None   # where the raw input is by default
    where: str = "own"   # for coverage: own (the owner's accounts), internal (the organisation's own) or public
    needs: str = ""   # the kind of connection it reads through (google, github, tracker, crm, chat, docs); "" for none
    files: tuple[str, ...] = ()   # where a runtime source's synced files are, as globs within a channel folder
    rank: int = 50   # coverage order within `where`: an organisation's own statements of direction first


def globbed(source: Source, folder: Path) -> list[Path]:
    """A source's synced files in one channel folder, in the order its globs give (the rolling copy before a backfill)."""
    return [path for pattern in source.files for path in sorted(folder.glob(pattern))]


@dataclass
class Context:
    today: date
    since: date | None = None
    input: Path | None = None
    config: object | None = None   # the vault's Config where there is one; a source on the owner's Mac has none
    vault: object | None = None   # the loaded Vault, when the caller has one


@dataclass(frozen=True)
class Registered:
    source: Source
    extract: Callable[[Context], list] | None   # None for a source the agent reads with its own tools and skill


def registry() -> dict[str, Registered]:
    """Every source, by id: a module declares one as `SOURCE` with its `extract`, or several the agent reads itself as
    `SOURCES`."""
    found = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        declared = getattr(module, "SOURCES", ())
        single = getattr(module, "SOURCE", None)
        for source in (*declared, *([single] if isinstance(single, Source) else [])):
            if source.id in found:
                raise ValueError(f"source {source.id!r} is declared twice")
            found[source.id] = Registered(source, getattr(module, "extract", None) if source is single else None)
    return dict(sorted(found.items()))


def declared(edition: str) -> list[Source]:
    """The sources an edition reads, in coverage order. An organisation's own accounts are its internal sources."""
    order = {"public": 0, "internal": 1, "own": 2}
    found = [replace(entry.source, where="internal") if edition == "enterprise" and entry.source.where == "own"
             else entry.source for entry in registry().values() if edition in entry.source.editions]
    return sorted(found, key=lambda source: (order.get(source.where, 3), source.rank, source.id))


def nightly(vault, today: date, home: Path | None = None) -> list[str]:
    """Extract every source that reads what the runtime already holds, for this edition. A failing source is reported
    and skipped; the night goes on."""
    from .. import evidence
    config, report = vault.config, []
    for name, entry in registry().items():
        if entry.extract is None or entry.source.runs != "runtime" or config.edition not in entry.source.editions:
            continue
        try:
            rows = entry.extract(Context(today, None, None, config, vault))
            added = evidence.append(name, rows, home)
        except Nothing:
            continue
        except (ValueError, OSError) as error:
            report.append(f"evidence: {name}: not extracted ({error})")
            continue
        if added:
            report.append(f"evidence: {name}: {added} new row(s)")
    return report
