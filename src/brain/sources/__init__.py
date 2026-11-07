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


def registry() -> dict[str, ModuleType]:
    found = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        source = getattr(module, "SOURCE", None)
        if isinstance(source, Source):
            found[source.id] = module
    return dict(sorted(found.items()))
