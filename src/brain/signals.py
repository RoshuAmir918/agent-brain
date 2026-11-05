"""Cheap, stable cron monitor output. Only the heartbeat carries the check time."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import channel_files, fm, loops, runtime, session_tools, state, tasks
from .config import Config


def _home() -> Path:
    return runtime.home()


def _heartbeat(home: Path, routine: str, now: datetime, **counts: int) -> None:
    path = home / "records" / routine / "signal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".json.tmp")
    staged.write_text(json.dumps({"checked": now.isoformat(), **counts}) + "\n", encoding="utf-8")
    staged.replace(path)


def _daily_job(job: dict) -> bool:
    return job.get("name") == "continue-daily" or str(job.get("prompt") or "").strip() == "Run continue daily."


def _last_failure(home: Path, skill: str) -> str:
    """When this routine's last run died (a gateway restart, a provider error): a new failure is a change, so the
    next tick retries instead of waiting for the work itself to move."""
    try:
        for job in runtime.jobs(home):
            if _daily_job(job):
                continue  # continue shares its skill with the daily job, which has a separate gate
            skills = job.get("skills") or []
            if skill in ([skills] if isinstance(skills, str) else skills):
                failure = job.get("last_failure") or {}
                return str(failure.get("at") or "") if isinstance(failure, dict) else ""
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return ""


def config_vault(config: Config):
    from .vault import Vault
    return Vault(config.vault_path)


def _check(config: Config) -> datetime:
    if not config.vault_path.is_dir():
        raise FileNotFoundError(f"vault not found: {config.vault_path}")
    now = config.now()
    return now.replace(tzinfo=config.zone) if now.tzinfo is None else now


def watch(config: Config) -> str:
    now, home = _check(config), _home()
    due = loops.due(config, now)
    unanswered = session_tools.unanswered(home, now)
    lines = ["due:"]
    lines.extend(f"{item.id} · {item.kind}{' · ready' if item.ready else ''} · {item.text}"
                 for item in sorted(due, key=lambda item: item.id))
    if due and (failed := _last_failure(home, config.skill_prefix + "watch")):
        lines.append(f"last failure: {failed}")
    lines.append("channels:")
    channels = home / "channels"
    own = set(channel_files.accounts(config_vault(config))) | {path.name.lower() for path in channels.iterdir()} \
        if channels.is_dir() else set()
    rows = []
    for account in sorted(channels.iterdir()) if channels.is_dir() else []:
        for kind in ("mail", "calendar"):
            directory = account / kind
            if not directory.is_dir():
                continue
            files = [path for path in directory.rglob("*") if path.is_file()]
            if kind == "mail":
                # New mail is a new file in a date folder; the sync rewrites old mail (labels, read state), so
                # mtimes would wake watch for nothing. Every message counts, from a person or a bot (a reminder, an
                # alert, a receipt): a quiet wake is cheaper than a missed one. Only mail nobody needs (promotions,
                # social, forums, spam, the account's own sent mail) leaves the signal unchanged.
                names = sorted(path.relative_to(directory).as_posix() for path in files)
                folders = sorted({name.split("/", 1)[0] for name in names if "/" in name})[-2:]
                worth = []
                for rel in (name for name in names if name.split("/", 1)[0] in folders):
                    try:
                        data, _ = fm.read(directory / rel)
                    except (OSError, ValueError):
                        continue
                    if channel_files.worth_a_look(data, own):
                        worth.append(rel)
                name = f"{len(worth)} worth a look" + (f", newest {worth[-1]}" if worth else "")
            else:
                newest = max(files, key=lambda path: (path.stat().st_mtime_ns, path.relative_to(directory).as_posix()),
                             default=None)
                # The calendar sync rewrites only changed days, so the newest mtime moves only when something did.
                name = f"{newest.relative_to(directory).as_posix()} @{newest.stat().st_mtime_ns // 10**9}" if newest else ""
            rows.append(f"{account.name}/{kind}: {name}")
    lines.extend(sorted(rows))
    lines.append("unanswered:")
    lines.extend(f"{session_id} · {source}" for session_id, source in unanswered)
    _heartbeat(home, "watch", now, due=len(due), unanswered=len(unanswered))
    return "\n".join(lines)


def continue_(config: Config, daily: bool = False) -> str:
    from . import continuation
    now, home = _check(config), _home()
    registered, archived = tasks.load_tasks(config)
    due = tasks.due(registered + archived, pace="daily" if daily else "regular",
                    chunks=continuation.chunks_today(config), daily_chunks=config.daily_chunks)
    lines = [f"date: {now.date().isoformat()}" if due else "date: none"]
    for task in sorted(due, key=lambda task: task.tid):
        if daily:
            lines.append(task.tid)
            continue
        nxt = state._field(task.body, "Next", "Next action") or ""
        lines.append(f"{task.tid} · {task.task_state or 'open'} · {nxt}")
    if due and not daily and (failed := _last_failure(home, config.skill_prefix + "continue")):
        lines.append(f"last failure: {failed}")
    _heartbeat(home, "continue-daily" if daily else "continue", now, due=len(due))
    return "\n".join(lines)
