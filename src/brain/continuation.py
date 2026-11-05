"""Engine-judged progress between continue chunks, shared by both speeds: a new output for the task, or the task
closing or coming to rest on the owner. Plans, checkpoints and self-verification are not progress."""
from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import date, datetime

from . import runtime, signals, state, tasks
from .config import Config
from .lock import vault_lock


def _created_since(created: str | None, since: str, config: Config) -> bool:
    if not created:
        return False
    try:
        # A date-only output has day precision; a new id on that same day counts.
        if len(created) == 10:
            return date.fromisoformat(created) >= datetime.fromisoformat(since).date()
        at, previous = datetime.fromisoformat(created), datetime.fromisoformat(since)
        at = at.replace(tzinfo=config.zone) if at.tzinfo is None else at
        return at > previous
    except ValueError:
        return False


def chunk(config: Config, tid: str) -> str:
    """Record a baseline or one chunk; only new outputs and resting or closed transitions count."""
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
            verified = state._field(task.body, "Verified") or ""
            outputs = [output for output in state.build(config, now.date())["outputs"] if output.get("task") == tid]
            current = {"verified": hashlib.sha256(verified.encode("utf-8")).hexdigest(),
                       "outputs": sorted(output["id"] for output in outputs), "state": task.task_state,
                       "quiet": 0, "at": now.isoformat()}
            if previous is None:
                message = f"{tid} baseline stored: 0 quiet chunks"
            else:
                # Only what the owner can see counts. A Verified line rewritten each chunk with the agent's own
                # self-checks (tests passed, readback matched) kept runaway handoffs looking busy on 2026-11-04.
                reasons = []
                if any(output["id"] not in previous["outputs"] and
                       _created_since(output["created"], previous["at"], config) for output in outputs):
                    reasons.append("new output")
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
            records.setdefault(key, {})[tid] = current
            staged = path.with_suffix(".json.tmp")
            staged.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            staged.replace(path)
            return message
