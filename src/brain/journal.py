"""The agent's journal: one structured row per conversation, written after it goes quiet, and the owner's catch-ups."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import access_log, fm, ledger
from .lock import vault_lock
from .config import Config
from .vault import Vault

HEADER = ("---\nkind: log\ntier: cold\n---\n\n"
          "Format: `when | platform | outcome | headline | next step`, then optional indented `needs you`, "
          "`touched` and `thought` lines. Written after each conversation goes quiet.\n\n")
DETAIL = re.compile(r"^  - (needs you|touched|thought): (.*)$")
WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")


@dataclass
class Entry:
    when: str
    platform: str
    outcome: str
    headline: str
    next: str
    needs_you: str | None = None
    touched: list[str] = field(default_factory=list)
    thought: str | None = None
    line: str = ""


def path_for(config: Config, month: str) -> Path:
    return config.vault_path / config.agent("Journal") / f"{month}.md"


def _clean(text: str | None) -> str:
    return " ".join((text or "").replace("|", "/").split())


def parse(text: str) -> list[Entry]:
    entries = []
    for line in text.splitlines():
        if (detail := DETAIL.match(line)) and entries:
            key, value = detail[1], detail[2].strip()
            if key == "needs you":
                entries[-1].needs_you = value
            elif key == "thought":
                entries[-1].thought = value
            else:
                entries[-1].touched = WIKILINK.findall(value)
            continue
        row = ledger.parse_row(line)
        if row is not None and row.outcome is not None:
            entries.append(Entry(row.when, row.routine, row.outcome, row.account, row.next, line=line))
    return entries


def append_entry(config: Config, record: dict, platform: str, at: datetime, *,
                 user_id: str = "", user_id_alt: str = "") -> Path:
    """Writes one row, keeping only touched notes that exist; a `needsYou` becomes an ask in CURRENT; requests from
    anyone but the owner go to the access log."""
    outcome = record.get("outcome")
    headline = _clean(record.get("headline"))
    if outcome not in ledger.OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}; choose from " + ", ".join(ledger.OUTCOMES))
    if not headline:
        raise ValueError("missing headline")
    local = at.replace(tzinfo=config.zone) if at.tzinfo is None else at.astimezone(config.zone)
    from .state import _public   # the export's boundary: a profile or the owner's notes are never named here
    vault = Vault(config.vault_path)
    touched = [title for title in dict.fromkeys(_clean(title) for title in record.get("touched") or [])
               if title and (target := vault.resolve(title)) and _public(vault, target)]
    lines = [ledger.render_row(ledger.Row(local.strftime("%Y-%m-%d %H:%M"), _clean(platform) or "hermes", outcome,
                                          headline, _clean(record.get("next"))))]
    if needs_you := _clean(record.get("needsYou")):
        lines.append(f"  - needs you: {needs_you}")
    if touched:
        lines.append("  - touched: " + ", ".join(f"[[{title}]]" for title in touched))
    if thought := _clean(record.get("thought")):
        lines.append(f"  - thought: {thought}")
    path = path_for(config, local.strftime("%Y-%m"))
    if outcome != "nothing":   # a quiet conversation keeps no row, only the requests it answered
        _append(config, path, "\n".join(lines) + "\n")
    if needs_you:
        add_ask(config, local.date(), needs_you, touched[0] if touched else None)
    access_log.append(config, record.get("access"), platform, at, user_id=user_id, user_id_alt=user_id_alt)
    return path


def append_synthesis(config: Config, text: str, at: datetime) -> Path:
    local = at.replace(tzinfo=config.zone) if at.tzinfo is None else at.astimezone(config.zone)
    path = path_for(config, local.strftime("%Y-%m"))
    _append(config, path, f"\n## Synthesis {local:%Y-%m-%d %H:%M}\n\n{text.strip()}\n\n")
    return path


def _append(config: Config, path: Path, text: str) -> None:
    """Appends, never rewrites: two sessions ending together each keep their row."""
    with vault_lock(config):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(HEADER, encoding="utf-8")
        with path.open("rb") as stream:
            stream.seek(0, 2)
            ends_clean = True
            if stream.tell():
                stream.seek(-1, 2)
                ends_clean = stream.read(1) == b"\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(("" if ends_clean else "\n") + text)


def add_ask(config: Config, day: date, text: str, source: str | None) -> None:
    """Appends an ask to CURRENT's `## Needs you` section, creating the section at the end if it is missing."""
    with vault_lock(config):
        _add_ask(config, day, text, source)


def _add_ask(config: Config, day: date, text: str, source: str | None) -> None:
    path = config.vault_path / "CURRENT.md"
    data, body = fm.read(path) if path.exists() else ({}, "")
    text = " ".join(text.replace("·", "-").split())   # the separator, inside the text, would break the bullet
    from .needs import parse_section
    if any(item.kind == "ask" and item.text.casefold() == text.casefold() for item in parse_section(body)):
        return   # already asked; the first one stands until it is resolved
    bullet = f"- {day.isoformat()} · ask · {text}" + (f" · [[{source}]]" if source else "")
    heading = re.search(r"^## Needs you[ \t]*$", body, re.M)
    if heading is None:
        body = body.rstrip("\n") + ("\n\n" if body.strip() else "") + "## Needs you\n\n" + bullet + "\n"
    else:
        following = re.compile(r"^## ", re.M).search(body, heading.end())
        end = following.start() if following else len(body)
        before, after = body[:end].rstrip("\n"), body[end:]
        gap = "\n\n" if before.endswith("## Needs you") else "\n"
        body = before + gap + bullet + "\n" + ("\n" + after if after else "")
    fm.write(path, data, body)


def recent(config: Config, today: date, count: int = 30) -> list[Entry]:
    """The last `count` entries, reaching into last month's file when this month's is short."""
    previous = (today.replace(day=1).toordinal() - 1)
    months = dict.fromkeys((date.fromordinal(previous).strftime("%Y-%m"), today.strftime("%Y-%m")))
    entries = []
    for month in months:
        path = path_for(config, month)
        if path.exists():
            entries += parse(path.read_text(encoding="utf-8"))
    return entries[-count:]
