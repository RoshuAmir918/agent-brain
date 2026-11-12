"""After-conversation capture."""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

from . import access_log, fm, ledger, loops, needs, tasks
from .lock import vault_lock
from .config import Config
from .record import RECORD_SCHEMA, UNCAPTURED
from .identity import role
from .vault import Vault


def _clean(text: str | None) -> str:
    return " ".join((text or "").replace("|", "/").split())


# The record's list fields: whether an item may be a plain string, and the string keys an object item needs.
LISTS = {"touched": (True, ()), "ready": (True, ()), "resolved": (True, ()), "workingTogether": (True, ()),
         "noted": (True, ()), "loops": (False, ("kind", "text")), "closed": (True, ("loop", "outcome", "why"))}


def _coerce(record: dict) -> dict:
    """The harness does not enforce the record's schema: keep only well-formed fields and items, before anything is
    written, so one malformed item never stops the capture halfway."""
    out = {key: value if isinstance(value, str) or value is None else None
           for key, value in record.items() if key not in LISTS and key != "access"}
    out["access"] = record.get("access")
    for key, (plain, required) in LISTS.items():
        value = record.get(key)
        out[key] = [item for item in value if plain and isinstance(item, str) or required and isinstance(item, dict)
                    and all(isinstance(item.get(name), str) for name in required)
                    and all(v is None or isinstance(v, str) for v in item.values())] if isinstance(value, list) else []
    return out


def append_entry(config: Config, record: dict, platform: str, at: datetime, *,
                 user_id: str = "", user_id_alt: str = "", session: str = "") -> Path | None:
    """Records open loops, keeping only touched notes that exist; a `needsYou` becomes an ask in CURRENT; requests from
    anyone but the owner go to the access log. A record of nothing that captures nothing writes nothing (None)."""
    record = _coerce(record)
    outcome = record.get("outcome")
    headline = _clean(record.get("headline"))
    if outcome not in ledger.OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}; choose from " + ", ".join(ledger.OUTCOMES))
    if not headline:
        raise ValueError("missing headline")
    if outcome == "nothing" and not any(record.get(key) for key in RECORD_SCHEMA["properties"] if key not in UNCAPTURED):
        return None
    local = config.local(at)
    from .state import _public   # the export's boundary: a profile or the owner's notes are never named here
    vault = Vault(config.vault_path)
    ids = tuple(value for value in (user_id, user_id_alt) if value)
    # In an organisation's intellect a member is the owner of their own conversation and tasks.
    owner = role(vault, config, platform, ids) in ("owner", "member")
    speaker = f"{platform}:{ids[-1]}" if ids and platform not in ("", "cli") else None
    touched = [title for title in dict.fromkeys(_clean(title) for title in record.get("touched") or [])
               if title and (target := vault.resolve(title)) and _public(vault, target)]
    for loop in record.get("loops") or []:
        try:
            loops.add(config, local, loop["kind"], loop["text"], touched[0] if touched else "Conversations",
                      loop.get("with") or "", loop.get("due") or "")
        except ValueError as error:
            print(f"skipped loop: {error}", file=sys.stderr)
    for closure in record.get("closed") or []:
        which, outcome, why = ((closure, "done", "closed in conversation") if isinstance(closure, str) else
                               (closure["loop"], closure["outcome"], closure["why"]))
        # A retried record or another session may already have closed this loop.
        if any(item.id == which or fm.plain(item.text) == fm.plain(which)
               for item in loops.open_loops(config)):
            try:
                loops.close(config, which, local, outcome, why)
            except ValueError:
                pass   # ambiguous text or a concurrent close leaves the rest of the capture intact
    for which in record.get("ready") or []:
        if any(item.id == which or fm.plain(item.text) == fm.plain(which)
               for item in loops.open_loops(config)):
            try:
                loops.ready(config, which, local)
            except ValueError:
                pass   # already closed by another session, or ambiguous
    for which in record.get("resolved") or []:
        with vault_lock(config):
            matched = [item for item in needs.items(config, local.date()) if item.kind in ("ask", "confirm")
                       and (item.id == which or fm.plain(item.text) == fm.plain(which))]
            if len(matched) == 1:
                try:
                    needs.resolve(config, matched[0].id, because="answered in conversation", today=local.date(),
                                  owner_engaged=owner, by=speaker)
                except ValueError:
                    pass   # already gone, or an item that resolves where it lives
    if needs_you := _clean(record.get("needsYou")):
        from .needs import vague_ask
        if (reason := vague_ask(needs_you)):
            print(f"skipped ask {needs_you!r}: {reason}", file=sys.stderr)
        else:
            with vault_lock(config):
                add_ask(config, local.date(), needs_you, touched[0] if touched else None,
                        session=f"{platform}:{session}" if session and ":" not in session else session)
    if owner:
        working_together(config, local.date(), record.get("workingTogether"))
        promote_touched(config, vault, record.get("touched") or [], local.date(), speaker)
    noted(config, local, record.get("noted"), None if owner else (_clean(record.get("speaker")) or user_id or "someone"))
    access_log.append(config, record.get("access"), platform, at, user_id=user_id, user_id_alt=user_id_alt)
    return loops.path_for(config)


def promote_touched(config: Config, vault: Vault, touched: list[str], today: date, by: str | None = None) -> None:
    """Only the owner's touch of a task's own entry promotes it; a whole-register link is ambiguous. In an organisation,
    a member touching someone else's task (`by` is not its owner) leaves it as it is."""
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
            if not task.handoff or task.owner and by and by != task.owner:
                continue
            own_archive = config.agent(f"Tasks Archive/{task.tid}.md")
            if any((source == own_archive and (not anchor or anchor == task.tid or
                                              anchor.startswith(task.tid + " — "))) or
                   (source == config.agent("Tasks.md") and (anchor == task.tid or
                                                            anchor.startswith(task.tid + " — ")))
                   for source, anchor in targets):
                if tasks.pace(task) == "daily":
                    tasks.set_pace(config, task.tid, "regular", today)
                from . import continuation
                continuation.mark_engaged(config, task.tid)


HEADING = re.compile(r"^##[ \t]+Working together\b[^\n]*$", re.M)


def _plain_line(text: str) -> str:
    """A line as said, for spotting an exact repeat: no bullet or date, case, punctuation or spacing."""
    text = re.sub(r"^\s*[-*+]\s*(?:\d{4}-\d{2}-\d{2}:\s*)?", "", text)
    return " ".join(re.findall(r"\w+", text.casefold()))


def working_together(config: Config, day: date, lines) -> list[str]:
    """The owner's corrections and directions about how to work with them, written to the profile's Working together
    section in the session they were given, so every later session obeys them. Only an exact repeat is skipped: a line
    that reverses an earlier one is the correction itself, and the night merges what remains."""
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
        held = {_plain_line(line) for line in section.splitlines() if line.strip()}
        added = []
        for text in new:
            plain = _plain_line(text)
            if not plain or plain in held:
                continue
            added.append(f"- {day.isoformat()}: {text}")
            held.add(plain)
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


def add_ask(config: Config, day: date, text: str, source: str | None, why: str = "", *,
            distinct_source: bool = False, kind: str = "ask", session: str = "") -> bool:
    """Appends an ask to CURRENT's `## Needs you` section, creating the section at the end if it is missing; the caller
    holds the vault lock. Always adds: a question is never lost to a full list. Past the ceiling, the evening decides
    the lowest-ranked ones down to it."""
    path = config.vault_path / "CURRENT.md"
    data, body = fm.read(path) if path.exists() else ({}, "")
    text = " ".join(text.replace("·", "-").split())   # the separator, inside the text, would break the bullet
    asks = needs.parse_section(body)
    if any(item.kind == kind and item.text.casefold() == text.casefold() and (not distinct_source or item.source == source) for item in asks):
        return True   # already asked; the first one stands until it is resolved
    if kind == "ask" and (tid := needs.task_of(source or "")):
        body = needs.replace_task_asks(config, body, tid, day)
    bullet = f"- {day.isoformat()} · {kind} · {text}" + (f" · [[{source}]]" if source else "")
    why = " ".join(why.split())
    if why:
        bullet += f"\n  - why: {why}"
    if session.strip():
        bullet += f"\n  - session: {session.strip()}"
    bullet += f"\n  - id: {needs.Item(kind, text, 'current', day.isoformat(), source or 'CURRENT.md').id}"
    span = fm.section(body, "Needs you")
    if span is None:
        body = body.rstrip("\n") + ("\n\n" if body.strip() else "") + "## Needs you\n\n" + bullet + "\n"
    else:
        end = span[1]
        before, after = body[:end].rstrip("\n"), body[end:]
        gap = "\n\n" if before.endswith("## Needs you") else "\n"
        body = before + gap + bullet + "\n" + ("\n" + after if after else "")
    fm.write(path, data, body)
    from . import reception
    reception.record(config, "ask", text, source or "CURRENT")
    return True
