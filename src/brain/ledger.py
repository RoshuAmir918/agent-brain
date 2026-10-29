"""Routine receipts: one monthly ledger, one structured row per run."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import fm
from .config import Config
from .lock import vault_lock
from .vault import Vault

OUTCOMES = ("done", "progress", "waiting", "blocked", "failed", "nothing")
MONTHLY = re.compile(r"\d{4}-\d{2}\.md\Z")
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
TIMED = re.compile(r"\d{4}-\d{2}-\d{2} (\d{2}:\d{2})(?:–(\d{2}:\d{2}))?\Z")
RUNS = re.compile(r"(\d+) runs, nothing new\.\Z")
ENGINE_LOGS = {"Use Log", "Sleep Log", "Tasks Archive", "Research", "Explorations", "Improvements", "Retrieval Checks",
               "Journal"}
NIGHT_STAGES = {"attribute", "sleep1", "rem", "sleep2"}   # each is one commit: git is their ledger
HEADER = ("---\nkind: log\ntier: cold\n---\n\n"
          "Format: `when | routine | outcome | account | next step`; each entry is under 25 words.\n\n")


@dataclass
class Row:
    when: str
    routine: str
    outcome: str | None
    account: str
    next: str


def parse_row(line: str) -> Row | None:
    parts = [part.strip() for part in line.strip().removeprefix("- ").split("|")]
    if len(parts) < 4 or not DATED.match(parts[0]):
        return None
    if len(parts) >= 5 and parts[2] in OUTCOMES:
        return Row(parts[0], parts[1], parts[2], " | ".join(parts[3:-1]), parts[-1])
    return Row(parts[0], parts[1], None, " | ".join(parts[2:-1]), parts[-1])


def render_row(row: Row) -> str:
    if row.outcome not in OUTCOMES:
        raise ValueError("a five-column row needs an outcome in " + ", ".join(OUTCOMES))
    return " | ".join((row.when, row.routine, row.outcome, row.account, row.next))


def discover(config: Config) -> list[Path]:
    """Monthly ledgers in the log folders, and any monthly file under the agent folder that declares `kind: log`
    (a routine's first ledger, written before its folder joins `log_dirs`)."""
    root = config.vault_path / config.agent_dir
    paths = set()
    for prefix in config.log_paths:
        for path in (config.vault_path / prefix).rglob("*.md"):
            if MONTHLY.fullmatch(path.name) and path.relative_to(root).parts[0] not in ENGINE_LOGS:
                paths.add(path)
    for path in root.rglob("*.md"):
        if (MONTHLY.fullmatch(path.name) and path not in paths and path.relative_to(root).parts[0] not in ENGINE_LOGS
                and fm.read(path)[0].get("kind") == "log"):
            paths.add(path)
    return sorted(paths)


def routine_for_skill(config: Config, skill: str) -> str:
    """The routine a cron job's skill runs: the responsibility hub's `records` name, else the skill without the
    vault's prefix, else the skill itself."""
    vault = Vault(config.vault_path)
    for note in vault.notes.values():
        if note.data.get("skill") == skill and note.data.get("records"):
            return str(note.data["records"])
    return skill.removeprefix(config.skill_prefix)


def ledger_for(config: Config, routine: str, month: str) -> Path:
    if not routine.strip() or routine != routine.strip() or routine in (".", "..") or any(
            char in routine for char in "/\\|\r\n"):
        raise ValueError("routine must be a nonempty name, not a path or a row")
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
        raise ValueError("month must be YYYY-MM")
    folders: dict[Path, list[Path]] = {}
    for path in discover(config):
        folders.setdefault(path.parent, []).append(path)
    for folder, paths in folders.items():
        current = folder / f"{month}.md"
        source = current if current in paths else max(paths)
        if any(row.routine.casefold() == routine.casefold() for line in source.read_text(encoding="utf-8").splitlines()
               if (row := parse_row(line)) is not None):
            return current
    return config.vault_path / config.agent(routine[0].upper() + routine[1:]) / f"{month}.md"


def append(config: Config, routine: str, outcome: str, account: str, next: str, at: datetime) -> Path:
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}; choose from " + ", ".join(OUTCOMES))
    local = at.replace(tzinfo=config.zone) if at.tzinfo is None else at.astimezone(config.zone)
    path = ledger_for(config, routine, local.strftime("%Y-%m"))
    account = " ".join(account.replace("|", "/").split())
    next = " ".join(next.replace("|", "/").split())
    row = Row(local.strftime("%Y-%m-%d %H:%M"), routine, outcome, account, next)
    with vault_lock(config):
        return _append_row(path, row)


def _append_row(path: Path, row: Row) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(HEADER, encoding="utf-8")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        if stream.tell():
            stream.seek(-1, 2)
            separator = "" if stream.read(1) == b"\n" else "\n"
        else:
            separator = ""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(separator + render_row(row) + "\n")
    return path


def collapse(config: Config, today: date) -> list[Path]:
    changed = []
    for path in discover(config):
        with path.open(encoding="utf-8", newline="") as stream:
            lines = stream.readlines()
        groups: dict[tuple[str, str], list[tuple[int, Row]]] = {}
        for position, line in enumerate(lines):
            row = parse_row(line)
            if row is not None and row.outcome == "nothing" and row.when[:10] < today.isoformat():
                groups.setdefault((row.when[:10], row.routine.casefold()), []).append((position, row))
        replacements, removed = {}, set()
        for (day, _), entries in groups.items():
            if len(entries) < 2:
                continue
            times, count = [], 0
            for _, row in entries:
                if timed := TIMED.fullmatch(row.when):
                    times.extend(time for time in timed.groups() if time is not None)
                runs = RUNS.fullmatch(row.account)
                count += int(runs[1]) if runs else 1
            when = f"{day} {min(times)}–{max(times)}" if times else day
            position, first = entries[0]
            ending = "\r\n" if lines[position].endswith("\r\n") else "\n"
            replacements[position] = render_row(Row(when, first.routine, "nothing", f"{count} runs, nothing new.",
                                                    entries[-1][1].next)) + ending
            removed.update(position for position, _ in entries[1:])
        if replacements:
            with path.open("w", encoding="utf-8", newline="") as stream:
                stream.writelines(replacements.get(position, line) for position, line in enumerate(lines)
                                  if position not in removed)
            changed.append(path)
    return changed
