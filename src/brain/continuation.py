"""Engine-judged progress between continue chunks, shared by both speeds: saved work for the task,
or the task closing or coming to rest on the owner. Vault drafts, plans and self-verification are not progress."""
from __future__ import annotations

import fcntl
import json
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

from . import runtime, signals, tasks
from .config import DAILY_CHUNKS, Config
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


def _progress(config: Config) -> dict[str, dict]:
    if runtime.configured() is None:
        return {}
    try:
        records = json.loads((signals._home() / "records" / "continue" / "progress.json").read_text(encoding="utf-8"))
        return {tid: row for tid, row in records.get(str(config.vault_path.resolve()), {}).items() if isinstance(row, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def last_worked(config: Config) -> dict[str, str]:
    """When each of this vault's handed-off tasks last had a chunk counted, from the progress record."""
    return {tid: row["at"] for tid, row in _progress(config).items() if isinstance(row.get("at"), str)}


def _local_now(config: Config) -> datetime:
    now = config.now()
    return now.replace(tzinfo=config.zone) if now.tzinfo is None else now.astimezone(config.zone)


def _count(row: dict, day: str) -> int:
    chunks = row.get("chunks", {})
    if isinstance(chunks, dict) and chunks.get("date") == day:
        count = chunks.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
    return 0


def chunks_today(config: Config) -> dict[str, int]:
    day = _local_now(config).date().isoformat()
    return {tid: _count(row, day) for tid, row in _progress(config).items()}


def _engaged_path() -> Path:
    return signals._home() / "records" / "continue" / "engaged.json"


def engaged_at(config: Config) -> dict[str, str]:
    """When the owner last engaged with each task (a handoff, an instruction in a conversation, an answer), by task."""
    if runtime.configured() is None:
        return {}
    try:
        records = json.loads(_engaged_path().read_text(encoding="utf-8"))
        rows = records.get(str(config.vault_path.resolve()), {})
        return {tid: at for tid, at in rows.items() if isinstance(at, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def mark_engaged(config: Config, tid: str) -> None:
    """The owner engaged with the task now: its daily budget is whole again."""
    if runtime.configured() is None:
        return
    path = _engaged_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "progress.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = {}
        records.setdefault(str(config.vault_path.resolve()), {})[tid] = _local_now(config).isoformat()
        staged = path.with_suffix(".json.tmp")
        staged.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        staged.replace(path)


def budget_for(config: Config, task, engaged: dict[str, str] | None = None) -> int:
    """Today's chunk budget: whole on a day the owner engaged with the task (or handed it off), then halved for each
    day without engagement, down to one. A task nobody is following slows down by itself instead of circling."""
    engaged = engaged_at(config) if engaged is None else engaged
    anchors = [task.handoff[0]] if task.handoff else []
    if engaged.get(task.tid):
        anchors.append(engaged[task.tid][:10])
    if not anchors:
        return config.daily_chunks
    try:
        days = (_local_now(config).date() - date.fromisoformat(max(anchors))).days
    except ValueError:
        return config.daily_chunks
    return config.daily_chunks if days <= 0 else max(1, config.daily_chunks >> days)


def budgets(config: Config, task_list) -> dict[str, int]:
    engaged = engaged_at(config)
    return {task.tid: budget_for(config, task, engaged) for task in task_list}


def checkpoint(config: Config, tid: str, text: str) -> None:
    """Atomically replace one task's checkpoint, holding the vault lock internally."""
    if not re.fullmatch(r"T-\d{3}", tid):
        raise ValueError("checkpoint needs a T-nnn task id")
    if runtime.configured() is None:
        raise ValueError("checkpoint needs AGENT_HOME")
    with vault_lock(config):
        registered, archived = tasks.load_tasks(config)
        if not any(task.tid == tid for task in registered + archived) and not tasks.archive_path(config, tid).exists():
            raise ValueError(f"unknown task: {tid}")
        folder = runtime.home() / "records" / "continue" / "tasks"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{tid}.json"
        staged = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, suffix=".tmp", delete=False) as stream:
                staged = stream.name
                json.dump({"task": tid, "at": _local_now(config).isoformat(), "text": text}, stream, ensure_ascii=False)
                stream.write("\n")
            Path(staged).replace(path)
        finally:
            if staged is not None:
                Path(staged).unlink(missing_ok=True)


def checkpoint_text(tid: str) -> str | None:
    if runtime.configured() is None or not re.fullmatch(r"T-\d{3}", tid):
        return None
    try:
        row = json.loads((runtime.home() / "records" / "continue" / "tasks" / f"{tid}.json").read_text(encoding="utf-8"))
        return row["text"] if row.get("task") == tid and isinstance(row.get("text"), str) else None
    except (OSError, ValueError, AttributeError):
        return None


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
            now = _local_now(config)
            day = now.date().isoformat()
            count = _count(previous or {}, day) + 1
            pushed = _work_commit(task.work)
            current = {"state": task.task_state, "quiet": 0, "at": now.isoformat(), "chunks": {"date": day, "count": count}}
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
            limit = budget_for(config, task)
            if tasks.pace(task) == "regular" and count == limit:
                message = (f"{tid} reached today's budget of {limit} chunks; it waits until tomorrow"
                           + ("" if limit == config.daily_chunks else " (the budget is smaller while its owner has not engaged)"))
            return message
