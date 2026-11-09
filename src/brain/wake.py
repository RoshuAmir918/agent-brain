"""The wake screen: every new channel item is asked one question, whether it is actionable before the next review,
and only what passes reaches watch. Everything else waits for the next scheduled pass, which still sees it."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path

from . import channel_files, fm, goals, loops, needs
from .config import Config
from .vault import Vault

THRESHOLD = 0.25   # from the 11-09 replay: every item a strong model called actionable scored at least 0.30
BATCH = 100
DEADLINE = 240     # seconds of screening per tick, well inside the harness's monitor timeout
PENDING = timedelta(days=3)   # how long a wake no watch pass has handled is kept after its item is gone
CALENDAR_DAYS = 14
EXCERPT = 1200


def instructions(config: Config) -> str:
    base = files("brain").joinpath("templates", "wake.md").read_text(encoding="utf-8").strip()
    own = config.vault_path / config.agent("Wake.md")
    try:
        _, body = fm.read(own)
    except (OSError, ValueError):
        body = ""
    if body.strip():
        base += ("\n\nThe owner's own wishes, kept by the assistant; they take precedence over the defaults above:\n"
                 + body.strip())
    return base + "\nShould the assistant wake for this item?"


def context(config: Config, accounts: list[str]) -> dict:
    today = config.today()
    people = config.vault_path / config.people_dir
    return {
        "owner": config.human_name,
        "owner_accounts": accounts,
        "owner_goals": [f"{g.title}: {g.words}"[:200] for g in goals.active(config)][:20],
        "open_loops": [f"{i.kind} with {i.with_ or '?'}: {i.text}"[:200] for i in loops.open_loops(config)][:30],
        "open_asks": [i.text[:200] for i in needs.items(Vault(config.vault_path), today)][:20],
        "people_the_owner_knows": sorted(p.name for p in people.iterdir() if p.is_dir())[:80] if people.is_dir() else [],
    }


def candidates(config: Config, synced: list[tuple[str, Path]], seen_calendar: dict[str, str]) -> list[dict]:
    """New mail worth a look in the last two date folders, and calendar days from today on whose text changed."""
    own = {name for name, _ in synced}
    today, horizon = config.today().isoformat(), (config.today() + timedelta(days=CALENDAR_DAYS)).isoformat()
    out = []
    for name, account in synced:
        mail = account / "mail"
        if mail.is_dir():
            for folder in sorted(path for path in mail.iterdir() if path.is_dir())[-2:]:
                for path in sorted(path for path in folder.rglob("*") if path.is_file()):
                    try:
                        data, body = fm.read(path)
                    except (OSError, ValueError):
                        continue
                    if not channel_files.worth_a_look(data, own):
                        continue
                    out.append({"id": f"{name}/mail/{path.relative_to(mail).as_posix()}", "item": {
                        "channel": "mail", "account": name, "from": data.get("from", ""), "to": data.get("to", ""),
                        "date": str(data.get("date", "")), "subject": data.get("subject", ""),
                        "labels": data.get("labels", []), "excerpt": " ".join(body.split())[:EXCERPT]}})
        calendar = account / "calendar"
        if calendar.is_dir():
            for path in sorted(calendar.glob("*.md")):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem) or not today <= path.stem <= horizon:
                    continue
                key = f"{name}/calendar/{path.name}"
                text = path.read_text(encoding="utf-8", errors="replace")
                before = seen_calendar.get(key, "")
                changes = [line for line in difflib.unified_diff(before.splitlines(), text.splitlines(), lineterm="", n=0)
                           if line[:1] in "+-" and line[:3] not in ("+++", "---")]
                out.append({"id": f"{key}@{hashlib.sha1(text.encode()).hexdigest()[:8]}", "calendar": (key, text), "item": {
                    "channel": "calendar", "account": name, "date": path.stem,
                    "subject": f"calendar for {path.stem} changed",
                    "changes": "\n".join(changes)[:EXCERPT]}})
    return out


def score(config: Config, accounts: list[str], items: list[dict]) -> list[float | None]:
    """Jev's yes-probability per item through Demiurge, or 1.0 for every item when the gate is off; None where it
    could not answer, which the caller treats as a wake so a screening outage never hides anything."""
    if os.environ.get("BRAIN_WAKE_GATE", "").strip().lower() == "off":
        return [1.0] * len(items)
    request = {"instructions": instructions(config), "context": context(config, accounts)}
    scores: list[float | None] = []
    end = time.monotonic() + DEADLINE
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        try:
            left = end - time.monotonic()
            if left < 5:
                raise TimeoutError
            out = subprocess.run(["demiurge", "screen", "--json"], input=json.dumps({**request, "items": [i["item"] for i in batch]}),
                                 capture_output=True, text=True, timeout=left, check=True).stdout
            got = json.loads(out)["scores"]
            scores += [p if isinstance(p, (int, float)) else None for p in got][:len(batch)] + [None] * (len(batch) - len(got))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            scores += [None] * len(batch)
    return scores


def screen(config: Config, home: Path, synced: list[tuple[str, Path]], now: datetime, since: datetime | None) -> list[dict]:
    """Screens what is new since the last tick and returns what woke after the last watch pass began, oldest first."""
    path = home / "records" / "watch" / "screened.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = None
    first = state is None
    state = state or {}
    seen, calendar = state.get("items", {}), state.get("calendar", {})
    found = candidates(config, synced, calendar)
    new = [c for c in found if c["id"] not in seen]
    # The first screen only learns what is already there: a deploy must not wake for days of old mail.
    scores = [0.0] * len(new) if first else score(config, [name for name, _ in synced], new) if new else []
    log = []
    for c, p in zip(new, scores):
        item = c["item"]
        woke = not first and (p is None or p >= THRESHOLD)
        line = f"{c['id']} · {item.get('from') or 'changed'} · {item.get('subject', '')}"[:240]
        seen[c["id"]] = {"p": p, "woke": woke, "at": now.isoformat(), "line": line}
        if not first:
            log.append({"at": now.isoformat(), "id": c["id"], "p": p, "woke": woke,
                        "from": item.get("from", ""), "subject": item.get("subject", "")})
    current = {c["id"] for c in found}
    pending = lambda v: v["woke"] and (since is None or datetime.fromisoformat(v["at"]) > since)
    # A wake nobody has handled outlives its item (a calendar day edited again, mail leaving the window), so a failed
    # watch pass still finds it next time.
    state = {"items": {k: v for k, v in seen.items()
                       if k in current or pending(v) and now - datetime.fromisoformat(v["at"]) < PENDING},
             "calendar": {key: text for c in found if (cal := c.get("calendar")) for key, text in [cal]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps(state) + "\n")
    if log:
        with (home / "records" / "watch" / "screen.jsonl").open("a") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in log)
    return sorted((v | {"id": k} for k, v in state["items"].items() if pending(v)), key=lambda v: (v["at"], v["id"]))
