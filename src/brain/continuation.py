"""Engine-judged progress between continue chunks, shared by both speeds: saved work for the task,
or the task closing or coming to rest on the owner. Vault drafts, plans and self-verification are not progress."""
from __future__ import annotations

import fcntl
import json
import subprocess
from datetime import datetime

from . import runtime, signals, tasks
from .config import Config
from .lock import vault_lock


def _work_commit(work: str | None) -> tuple[str, str] | None:
    """The work's last pushed commit and when it was pushed, from the runtime's sync state."""
    if not work:
        return None
    try:
        saved = json.loads((runtime.home() / "records" / "works" / "state.json").read_text(encoding="utf-8"))[work]
        commit, at = (saved.get("lastCommit") or {}).get("hash", ""), saved["lastPush"]
        return (commit, at) if isinstance(commit, str) and isinstance(at, str) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _work_saved(work: str | None, pushed: tuple[str, str] | None, previous: dict, config: Config) -> bool:
    """Only a push makes work visible to the owner, and only one that changed more than whitespace moved it."""
    if not work or pushed is None:
        return False
    try:
        at, since = datetime.fromisoformat(pushed[1]), datetime.fromisoformat(previous["at"])
    except ValueError:
        return False
    at = at.replace(tzinfo=config.zone) if at.tzinfo is None else at
    since = since.replace(tzinfo=config.zone) if since.tzinfo is None else since
    if at <= since:
        return False
    before = previous.get("work_commit")
    if not before or not pushed[0]:
        return True
    if before == pushed[0]:
        return False
    # A history the engine cannot read (a rebase rewrote it) gets the benefit of the doubt.
    try:
        same = subprocess.run(["git", "-C", str(runtime.home() / "works" / work), "diff", "-w", "--quiet", before, pushed[0]],
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return same.returncode != 0


def last_worked(config: Config) -> dict[str, str]:
    """When each of this vault's handed-off tasks last had a chunk counted, from the progress record."""
    if runtime.configured() is None:
        return {}
    try:
        records = json.loads((signals._home() / "records" / "continue" / "progress.json").read_text(encoding="utf-8"))
        return {tid: row["at"] for tid, row in records.get(str(config.vault_path.resolve()), {}).items()
                if isinstance(row, dict) and isinstance(row.get("at"), str)}
    except (OSError, ValueError, AttributeError):
        return {}


def chunk(config: Config, tid: str) -> str:
    """Record a baseline or one chunk; saved work and resting or closed transitions count."""
    with vault_lock(config):
        registered, archived = tasks.load_tasks(config)
        task = next((task for task in registered + archived if task.tid == tid), None)
        if task is None:
            path = tasks.archive_path(config, tid)
            task = tasks.read_archived_task(config, path) if path.exists() else None
        if task is None or not task.handoff:
            raise ValueError(f"{tid} is not a handed-off task")
        if runtime.configured() is None:
            return f"{tid} progress not recorded: AGENT_HOME is not set"
        folder = signals._home() / "records" / "continue"
        folder.mkdir(parents=True, exist_ok=True)
        # Different vaults share this file, so the vault's lock alone is not enough.
        with (folder / "progress.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = folder / "progress.json"
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                records = {}
            key = str(config.vault_path.resolve())
            previous = records.get(key, {}).get(tid)
            now = config.now()
            now = now.replace(tzinfo=config.zone) if now.tzinfo is None else now
            pushed = _work_commit(task.work)
            current = {"state": task.task_state, "quiet": 0, "at": now.isoformat()}
            if previous is None:
                message = f"{tid} baseline stored: 0 quiet chunks"
            else:
                # Only what the owner can see counts. A Verified line rewritten each chunk with the agent's own
                # self-checks (tests passed, readback matched) kept runaway handoffs looking busy on 2026-11-04;
                # task drafts and reflections held in the vault are also invisible to the owner.
                reasons = []
                if _work_saved(task.work, pushed, previous, config):
                    reasons.append("work saved")
                if current["state"] != previous["state"] and task.task_state in (*tasks.RESTING, "completed", "cancelled"):
                    reasons.append(f"state became {task.task_state}")
                if reasons:
                    why = "; ".join(reasons)
                    if tasks.pace(task) == "daily":
                        task = tasks.set_pace(config, tid, "regular", now.date())
                        message = f"{tid} back to regular: {why}"
                    else:
                        message = f"{tid} progress: {why}; quiet reset to 0"
                else:
                    # An owner promotion starts a fresh run of regular chunks.
                    promoted = previous.get("pace") == "daily" and tasks.pace(task) == "regular"
                    current["quiet"] = (0 if promoted else previous["quiet"]) + 1
                    if current["quiet"] >= 3 and tasks.pace(task) == "regular":
                        task = tasks.set_pace(config, tid, "daily", now.date())
                        message = f"{tid} moved to daily: 3 chunks without progress"
                    else:
                        message = f"{tid} no progress: {current['quiet']} quiet chunks; pace {tasks.pace(task)}"
            current["pace"] = tasks.pace(task)
            if pushed and pushed[0]:
                current["work_commit"] = pushed[0]
            records.setdefault(key, {})[tid] = current
            staged = path.with_suffix(".json.tmp")
            staged.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            staged.replace(path)
            return message
