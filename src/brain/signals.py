"""Cheap, stable cron monitor output. Only the heartbeat carries the check time."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import channel_files, fm, loops, runtime, session_tools, tasks
from .config import Config
from .vault import Vault


def _heartbeat(home: Path, routine: str, now: datetime, **counts: int) -> None:
    path = home / "records" / routine / "signal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps({"checked": now.isoformat(), **counts}) + "\n")


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


def _check(config: Config) -> datetime:
    if not config.vault_path.is_dir():
        raise FileNotFoundError(f"vault not found: {config.vault_path}")
    return config.now()


def watch(config: Config) -> str:
    now, home = _check(config), runtime.home()
    due = loops.due(config, now)
    unanswered = session_tools.unanswered(home, now)
    lines = ["due:"]
    lines.extend(f"{item.id} · {item.kind}{' · ready' if item.ready else ''} · {item.text}"
                 for item in sorted(due, key=lambda item: item.id))
    if due and (failed := _last_failure(home, config.skill_prefix + "watch")):
        lines.append(f"last failure: {failed}")
    lines.append("channels:")
    # Only live registry rows: a pending, revoked or unregistered account's leftover files are never read or reported.
    synced = channel_files.synced_accounts(Vault(config.vault_path), owner_only=False)
    own = {name for name, _ in synced}
    rows = []
    for _, account in synced:
        for kind in ("mail", "calendar"):
            directory = account / kind
            if not directory.is_dir():
                continue
            if kind == "mail":
                # New mail is a new file in a date folder; the sync rewrites old mail (labels, read state), so
                # mtimes would wake watch for nothing. Every message counts, from a person or a bot (a reminder, an
                # alert, a receipt): a quiet wake is cheaper than a missed one. Only mail nobody needs (promotions,
                # social, forums, spam, the account's own sent mail) leaves the signal unchanged.
                folders = sorted(path for path in directory.iterdir() if path.is_dir())[-2:]
                worth = []
                for path in sorted(path for folder in folders for path in folder.rglob("*") if path.is_file()):
                    try:
                        data, _ = fm.read(path)
                    except (OSError, ValueError):
                        continue
                    if channel_files.worth_a_look(data, own):
                        worth.append(path.relative_to(directory).as_posix())
                # The digest catches a message that left while another, sorting lower, arrived in the same tick.
                digest = hashlib.sha1("\n".join(worth).encode()).hexdigest()[:12]
                name = f"{len(worth)} worth a look" + (f", newest {worth[-1]}, {digest}" if worth else "")
            else:
                files = [path for path in directory.rglob("*") if path.is_file()]
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
    now, home = _check(config), runtime.home()
    task_list = continuation.finish_lines(config)
    due = tasks.due(task_list, pace="daily" if daily else None,
                    chunks=continuation.chunks_today(config), daily_chunks=config.daily_chunks,
                    now=tasks.local_now(config), budgets=continuation.budgets(config, task_list),
                    caps=continuation.budget_caps(config))
    lines = [f"date: {now.date().isoformat()}" if due else "date: none"]
    for task in sorted(due, key=lambda task: task.tid):
        if daily:
            lines.append(task.tid)
            continue
        nxt = fm.field(task.body, "Next", "Next action") or ""
        lines.append(f"{task.tid} · {task.task_state or 'open'} · {nxt}")
    if due and not daily and (failed := _last_failure(home, config.skill_prefix + "continue")):
        lines.append(f"last failure: {failed}")
    _heartbeat(home, "continue-daily" if daily else "continue", now, due=len(due))
    return "\n".join(lines)
