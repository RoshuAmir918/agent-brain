"""After-conversation capture and the historical journal's rows and catch-ups."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import access_log, fm, ledger, loops, needs, tasks
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
    """Records open loops, keeping only touched notes that exist; a `needsYou` becomes an ask in CURRENT; requests from
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
    for loop in record.get("loops") or []:
        loops.add(config, local, loop["kind"], loop["text"], touched[0] if touched else "Conversations",
                  loop.get("with") or "", loop.get("due") or "")
    for which in record.get("closed") or []:
        # A retried record or another session may already have closed this loop.
        if any(item.id == which or loops._plain(item.text) == loops._plain(which)
               for item in loops.open_loops(config)):
            try:
                loops.close(config, which, local, "done", "closed in conversation")
            except ValueError:
                pass   # ambiguous text or a concurrent close leaves the rest of the capture intact
    for which in record.get("ready") or []:
        if any(item.id == which or needs._plain(item.text) == needs._plain(which)
               for item in loops.open_loops(config)):
            try:
                loops.ready(config, which, local)
            except ValueError:
                pass   # already closed by another session, or ambiguous
    for which in record.get("resolved") or []:
        with vault_lock(config):
            matched = [item for item in needs.items(config, local.date()) if item.kind in ("ask", "confirm")
                       and (item.id == which or needs._plain(item.text) == needs._plain(which))]
            if len(matched) == 1:
                try:
                    needs.resolve(config, matched[0].id, because="answered in conversation", today=local.date())
                except ValueError:
                    pass   # already gone, or an item that resolves where it lives
    if needs_you := _clean(record.get("needsYou")):
        add_ask(config, local.date(), needs_you, touched[0] if touched else None)
    from .session_context import is_owner
    owner = is_owner(config, platform, tuple(value for value in (user_id, user_id_alt) if value))
    if owner:
        working_together(config, local.date(), record.get("workingTogether"))
        promote_touched(config, vault, record.get("touched") or [], local.date())
    noted(config, local, record.get("noted"), None if owner else (_clean(record.get("speaker")) or user_id or "someone"))
    access_log.append(config, record.get("access"), platform, at, user_id=user_id, user_id_alt=user_id_alt)
    return loops.path_for(config)


def promote_touched(config: Config, vault: Vault, touched: list[str], today: date) -> None:
    """Only the owner's touch of a task's own entry promotes it; a whole-register link is ambiguous."""
    targets = set()
    for title in touched:
        target, _, anchor = _clean(title).partition("#")
        source = vault.resolve(target)
        if source:
            targets.add((source, anchor))
    if not targets:
        return
    with vault_lock(config):
        registered, archived = tasks.load_tasks(config)
        for task in registered + archived:
            if not task.handoff or tasks.pace(task) != "daily":
                continue
            own_archive = config.agent(f"Tasks Archive/{task.tid}.md")
            if any((source == own_archive and (not anchor or anchor == task.tid or
                                              anchor.startswith(task.tid + " — "))) or
                   (source == config.agent("Tasks.md") and (anchor == task.tid or
                                                            anchor.startswith(task.tid + " — ")))
                   for source, anchor in targets):
                tasks.set_pace(config, task.tid, "regular", today)


WORKING_TOGETHER_WORDS = 250   # the section loads in every session with the owner; past this it is merged at night
HEADING = re.compile(r"^##[ \t]+Working together\b[^\n]*$", re.M)


def _plain_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def working_together(config: Config, day: date, lines) -> list[str]:
    """The owner's corrections and directions about how to work with them, written to the profile's Working together
    section in the session they were given, so every later session obeys them. A line whose words another line already
    holds is a duplicate and is skipped; the night merges what remains."""
    new = [text for text in dict.fromkeys(_clean(line) for line in (lines or []) if isinstance(line, str)) if text]
    path = config.vault_path / config.human_profile
    if not new or not path.exists():
        return []
    with vault_lock(config):
        data, body = fm.read(path)
        heading = HEADING.search(body)
        if heading is None:
            body = body.rstrip("\n") + "\n\n## Working together\n"
            heading = HEADING.search(body)
        following = re.search(r"^##?[ \t]+", body[heading.end():], re.M)
        end = heading.end() + following.start() if following else len(body)
        section = body[heading.end():end]
        held = [_plain_words(line) for line in section.splitlines() if line.strip()]
        added = []
        for text in new:
            words = _plain_words(text)
            if words and any(words <= other for other in held):
                continue
            added.append(f"- {day.isoformat()}: {text}")
            held.append(words)
        if added:
            section = section.rstrip("\n") + "\n" + "\n".join(added) + "\n"
            body = body[:heading.end()] + section + ("\n" + body[end:].lstrip("\n") if body[end:].strip() else "")
            fm.write(path, data, body)
    return added


def noted(config: Config, at: datetime, lines, speaker: str | None) -> Path | None:
    """New facts and plans stated in a conversation, kept that day in a dated conversation note the night files into
    hubs and profiles. Someone other than the owner is quoted as a claim, never as the owner's word."""
    new = [text for text in dict.fromkeys(_clean(line) for line in (lines or []) if isinstance(line, str)) if text]
    if not new:
        return None
    path = config.vault_path / "Conversations" / f"{at.date().isoformat()} Noted.md"
    with vault_lock(config):
        if path.exists():
            data, body = fm.read(path)
            data.setdefault("tier", "warm")   # files written before 0.28.2 lacked it, which validate names
        else:
            data = {"kind": "conversation", "tier": "warm", "created": at.date().isoformat()}
            body = (f"# Noted {at.date().isoformat()}\n\nWhat was said in conversations today that is worth remembering, "
                    "written as it was said. The night files each into its note.\n")
        prefix = "" if speaker is None else f"{speaker} said: "
        body = body.rstrip("\n") + "\n" + "".join(f"\n- {at.strftime('%H:%M')} · {prefix}{text}" for text in new) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, data, body)
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


def add_ask(config: Config, day: date, text: str, source: str | None, force: bool = False, why: str = "") -> bool:
    """Appends an ask to CURRENT's `## Needs you` section, creating the section at the end if it is missing. Always
    adds: a question is never lost to a full list. Past the ceiling, the evening decides the lowest-ranked ones down
    to it. `force` is kept for callers written before the ceiling stopped refusing."""
    with vault_lock(config):
        return _add_ask(config, day, text, source, force, why)


def _add_ask(config: Config, day: date, text: str, source: str | None, force: bool = False, why: str = "") -> bool:
    path = config.vault_path / "CURRENT.md"
    data, body = fm.read(path) if path.exists() else ({}, "")
    text = " ".join(text.replace("·", "-").split())   # the separator, inside the text, would break the bullet
    from .needs import open_asks
    asks = open_asks(body)
    if any(item.text.casefold() == text.casefold() for item in asks):
        return True   # already asked; the first one stands until it is resolved
    bullet = f"- {day.isoformat()} · ask · {text}" + (f" · [[{source}]]" if source else "")
    why = " ".join(why.split())
    if why:
        bullet += f"\n  - why: {why}"
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
    return True


def unread_lines(config: Config, today: date) -> int:
    """Dated lines in this and last month's journal that are not rows the engine reads (an older bullet format), so a
    reader can tell "no conversations" from "a journal it cannot read"."""
    previous = (today.replace(day=1).toordinal() - 1)
    count = 0
    for month in dict.fromkeys((date.fromordinal(previous).strftime("%Y-%m"), today.strftime("%Y-%m"))):
        path = path_for(config, month)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if re.match(r"^\s*-?\s*20\d\d-\d\d-\d\d", line) and ledger.parse_row(line) is None:
                    count += 1
    return count


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
