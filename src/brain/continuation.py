"""Engine-judged progress between continue chunks, shared by both speeds: saved work for the task,
or the task closing or coming to rest on the owner. Vault drafts, plans and self-verification are not progress."""
from __future__ import annotations

import fcntl
import json
import re
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from . import fm, journal, needs, runtime, signals, state, tasks
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


@contextmanager
def _progress_records(config: Config):
    """Caller holds the vault lock; the second lock protects records shared by vaults."""
    folder = signals._home() / "records" / "continue"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "progress.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "progress.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = {}
        rows = records.setdefault(str(config.vault_path.resolve()), {})
        yield rows
        staged = path.with_suffix(".json.tmp")
        try:
            staged.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def last_worked(config: Config) -> dict[str, str]:
    """When each of this vault's handed-off tasks last had a chunk counted, from the progress record."""
    return {tid: row["at"] for tid, row in _progress(config).items() if isinstance(row.get("at"), str)}


def _count(row: dict, day: str) -> int:
    chunks = row.get("chunks", {})
    if isinstance(chunks, dict) and chunks.get("date") == day:
        count = chunks.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
    return 0


def chunks_today(config: Config) -> dict[str, int]:
    day = config.local().date().isoformat()
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


def mark_engaged(config: Config, tid: str, instruction: str | None = None, by: str | None = None) -> bool:
    """The owner engaged: restore the budget and restart the direction check."""
    with vault_lock(config):
        return _mark_engaged(config, tid, instruction, by)


def _mark_engaged(config: Config, tid: str, instruction: str | None = None, by: str | None = None) -> bool:
    """Lock-free task-facing variant; caller holds the vault lock. A task's budget follows its own owner's engagement:
    in an organisation, someone else (`by`) asking about it is interest, not the owner steering it, and nothing is
    written."""
    task = next((t for t in sum(tasks.load_tasks(config), []) if t.tid == tid), None) if by else None
    if task is not None and task.owner and by != task.owner:
        return False
    if runtime.configured() is None:
        return True
    path = _engaged_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _progress_records(config) as rows:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = {}
        records.setdefault(str(config.vault_path.resolve()), {})[tid] = config.local().isoformat()
        staged = path.with_suffix(".json.tmp")
        staged.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        staged.replace(path)
        row = rows.setdefault(tid, {})
        row["chunks_since_review"] = 0
        row.pop("circling", None)
        row.pop("budget_cap", None)
        if instruction:
            row["instruction"] = {"at": config.local().isoformat(), "text": instruction}
    return True


def budget_for(config: Config, task, engaged: dict[str, str] | None = None) -> int:
    """Today's chunk budget: whole on a day the owner engaged with the task (or handed it off), then halved for each
    day without engagement, down to one. A task nobody is following slows down by itself instead of circling."""
    engaged = engaged_at(config) if engaged is None else engaged
    anchors = [task.handoff[0]] if task.handoff else []
    if engaged.get(task.tid):
        anchors.append(engaged[task.tid][:10])
    try:
        days = (config.local().date() - date.fromisoformat(max(anchors))).days if anchors else 0
    except ValueError:
        days = 0
    limit = config.daily_chunks if days <= 0 else max(1, config.daily_chunks >> days)
    return min(limit, budget_caps(config).get(task.tid, limit))


def budgets(config: Config, task_list) -> dict[str, int]:
    engaged = engaged_at(config)
    return {task.tid: budget_for(config, task, engaged) for task in task_list}


def budget_caps(config: Config) -> dict[str, int]:
    day = config.local().date().isoformat()
    return {tid: row["budget_cap"]["limit"] for tid, row in _progress(config).items()
            if row.get("budget_cap", {}).get("date") == day}


def review_status(config: Config, tid: str) -> dict:
    row = _progress(config).get(tid, {})
    count = row.get("chunks_since_review", 0)
    return {"reviewDue": count >= config.review_every, "chunksSinceReview": count,
            "lastReview": row.get("last_review")}


def finish_lines(config: Config) -> list[tasks.Task]:
    """Raise one task-sourced standing ask, even without a runtime, before scheduling either pace."""
    with vault_lock(config):
        registered, archived = tasks.load_tasks(config)
        for task in registered + archived:
            if not task.handoff or not tasks.is_open(task) or task.task_state in ("yours", "set_aside"):
                continue
            waiting = task.task_state == "waiting_for_owner"
            if (task.delivers or waiting) and task.state.get("finish_asked") == "pending":
                # A task waiting on its owner already asks them one thing; the finish line waits until it moves again.
                because = "the task now has a Delivers line" if task.delivers else "the task already waits on its owner"
                current = config.vault_path / "CURRENT.md"
                for item in needs.open_asks(current.read_text(encoding="utf-8")) if current.exists() else []:
                    if task.tid in item.source and item.text.startswith("What would done look like for "):
                        needs.resolve(config, item.id, because, config.today(), record_answer=False)
                if task.delivers:
                    task.state["finish_asked"] = "answered"
                else:
                    task.state.pop("finish_asked")
                tasks.save_task(config, task, config.today())
            elif waiting:
                continue
            elif not task.delivers and not task.state.get("finish_asked"):
                journal._add_ask(config, config.today(),
                                 f"What would done look like for {task.title}? Until then {config.agent_name} "
                                 "won't keep working on it in the background.",
                                 config.agent(f"Tasks#{task.tid}"))
                task.state["finish_asked"] = "pending"
                tasks.save_task(config, task, config.today())
        return registered + archived


def _task(config: Config, tid: str, *, open_only: bool = False) -> tasks.Task:
    registered, archived = tasks.load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None and re.fullmatch(r"T-\d{3}", tid) and tasks.archive_path(config, tid).exists():
        task = tasks.read_archived_task(config, tasks.archive_path(config, tid))
    if task is None or not task.handoff or (open_only and not tasks.is_open(task)):
        raise ValueError(f"{tid} is not an open handed-off task" if open_only else f"{tid} is not a handed-off task")
    return task


def stages(config: Config, task: tasks.Task, entries: list[tasks.Task] | None = None) -> list[dict]:
    """The After chain and other tasks on this work, oldest first, keeping the newest five."""
    by_id = {entry.tid: entry for entry in (tasks.all_tasks(config) if entries is None else entries)}
    earlier, seen = {}, {task.tid}
    after = task.after
    while after and after not in seen:
        seen.add(after)
        stage = by_id.get(after)
        if stage is None:
            break
        earlier[stage.tid] = stage
        after = stage.after
    if task.work:
        earlier.update((entry.tid, entry) for entry in by_id.values() if entry.work == task.work and entry.tid != task.tid)
    # IDs are allocated in creation order; use them rather than last-use or closure dates.
    out = []
    for stage in sorted(earlier.values(), key=lambda entry: entry.tid)[-5:]:
        closed, outcome = state._closing(stage)
        out.append({"id": stage.tid, "title": stage.title, "state": stage.task_state, "closed": closed,
                    "delivers": stage.delivers, "outcome": outcome or fm.field(stage.body, "Why"),
                    "verified": fm.field(stage.body, "Verified"), "next": fm.field(stage.body, "Next", "Next action")})
    return out


def stages_text(earlier: list[dict]) -> str | None:
    if not earlier:
        return None

    def short(value: str, words: int) -> str:
        text = " ".join(value.split())
        trimmed = " ".join(text.split()[:words])[:160].rstrip()
        return trimmed + ("…" if trimmed != text else "")

    lines = ["Earlier stages of this work:"]
    for stage in earlier:
        status = " ".join(value for value in (stage["state"], stage["closed"]) if value)
        line = f"- {stage['id']} {short(stage['title'], 8)}" + (f" ({status})" if status else "")
        if stage["outcome"]:
            line += f": {short(stage['outcome'], 9)}"
        for name, label in (("delivers", "Delivers"), ("verified", "last Verified"), ("next", "Next it left")):
            if stage[name]:
                line += f"; {label}: {short(stage[name], 6)}"
        lines.append(line)
    return "\n".join(lines)


def instructions(config: Config, task: tasks.Task, earlier: list[dict] | None = None) -> str | None:
    checkpoint = checkpoint_text(task.tid)
    close = _progress(config).get(task.tid, {}).get("close_out")
    parts = []
    if close and tasks.is_open(task):
        prefix = (f"The reviewer judged the Delivers met ({close['reason']}). This chunk closes the task: "
                  f"verify the deliverable exactly as the Delivers says, then `brain task {task.tid} done` "
                  f"with the evidence and tell {config.human_name} once where the task reports, or, if it is not "
                  f"actually met, say precisely what is missing with `brain task {task.tid} set --next …`.")
        parts.append(prefix)
    if block := stages_text(stages(config, task) if earlier is None else earlier):
        parts.append(block)
    if checkpoint:
        parts.append(checkpoint)
    return "\n\n".join(parts) or None


def clear_close_out(config: Config, tid: str) -> None:
    """Called by the task write path under the vault lock when a task closes."""
    if runtime.configured() is not None and _progress(config).get(tid, {}).get("close_out"):
        with _progress_records(config) as rows:
            rows[tid].pop("close_out", None)


def answered(config: Config, source: str, question: str, because: str, owner_engaged: bool = False,
             by: str | None = None) -> None:
    """Remember a task-sourced answer without treating a worker's discovery as owner engagement. Caller holds the lock."""
    match = re.search(r"\bT-\d{3}\b", source)
    if match and runtime.configured() is not None:
        registered, archived = tasks.load_tasks(config)
        if any(t.tid == match[0] and t.handoff for t in registered + archived):
            text = f"{question} Answer: {because}"
            if not (owner_engaged and _mark_engaged(config, match[0], text, by)):
                with _progress_records(config) as rows:
                    rows.setdefault(match[0], {})["instruction"] = {"at": config.local().isoformat(), "text": text}


def _owner_instruction(config: Config, task: tasks.Task, row: dict) -> str | None:
    if instruction := (row.get("instruction") or {}).get("text"):
        return instruction
    # Before review records existed, task-sourced resolutions were kept only in the Answered ledger.
    for path in sorted((config.vault_path / config.agent("Answered")).glob("*.md"), reverse=True):
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            parts = line.removeprefix("- ").split(" · ")
            if (len(parts) == 5 and parts[1] == "ask" and parts[0] >= task.handoff[0]
                    and re.search(r"\b" + re.escape(task.tid) + r"\b", parts[4])
                    and parts[3].startswith("because ") and parts[3] != "because the task now has a Delivers line"):
                return f"{parts[2]} Answer: {parts[3].removeprefix('because ')}"
    return fm.field(task.body, "Source")


def review_context(config: Config, tid: str) -> dict:
    with vault_lock(config):
        task = _task(config, tid)
        row = _progress(config).get(tid, {})
        results = row.get("results", [])[-6:]
        # Old engines only kept the most recent checkpoint; include it when no counted results exist.
        if not results and (saved := _checkpoint(tid)):
            results = [{"at": saved.get("at"), "text": saved["text"]}]
        count = chunks_today(config).get(tid, 0)
        limit = budget_caps(config).get(tid) if tasks.pace(task) == "daily" else budget_for(config, task)
        return {"id": task.tid, "title": task.title, "delivers": task.delivers, "work": task.work,
                "status": fm.field(task.body, "Status"), "next": fm.field(task.body, "Next", "Next action"),
                "source": fm.field(task.body, "Source"),
                "ownerInstruction": _owner_instruction(config, task, row),
                "results": results, "lastReview": row.get("last_review"), "chunksToday": count,
                "budgetLeft": None if limit is None else max(0, limit - count)}


def review(config: Config, tid: str, verdict: str, reason: str, *, ask: str | None = None,
           resume: str | None = None) -> dict:
    if verdict not in ("converging", "met", "circling", "stuck"):
        raise ValueError("unknown review verdict")
    reason = tasks.single_line("reason", reason)
    if not reason:
        raise ValueError("review needs a nonempty --reason")
    if verdict == "circling":
        if not ask:
            raise ValueError("circling needs --ask")
        ask = tasks._field_value(config, "ask", ask)
    elif ask is not None:
        raise ValueError("--ask goes with a circling review")
    if resume is not None:
        if verdict != "stuck":
            raise ValueError("--resume goes with a stuck review")
        resume = tasks._field_value(config, "resume", resume)
    if runtime.configured() is None:
        raise ValueError("review needs AGENT_HOME")
    with vault_lock(config):
        task = _task(config, tid, open_only=True)
        limit = budget_for(config, task)
        with _progress_records(config) as rows:
            row = rows.setdefault(tid, {})
            now = config.local()
            last = {"verdict": verdict, "at": now.isoformat(), "reason": reason}
            if verdict == "circling":
                tasks.set_fields(config, tid, config.today(),
                                 state="waiting_for_owner" if row.get("circling") else None,
                                 values={"ask": ask})
                # CURRENT carries the first non-blocking ask; the escalated task supplies its own question.
                needs.settle_tasks(config, config.today(), [(tid, task.body)], "replaced by the review's steering question")
                if not row.get("circling"):
                    journal._add_ask(config, config.today(), ask, config.agent(f"Tasks#{tid}"))
                count = _count(row, now.date().isoformat())
                row["budget_cap"] = {"date": now.date().isoformat(), "limit": count + max(0, limit - count) // 2}
            elif verdict == "stuck":
                morning = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if morning <= now:
                    morning += timedelta(days=1)
                tasks.set_fields(config, tid, config.today(), state="blocked",
                                 values={"blocker": reason, "resume": resume or morning.strftime("%Y-%m-%d %H:%M")})
            row["circling"] = verdict == "circling"
            row["chunks_since_review"] = 0
            row["last_review"] = last
            row.setdefault("reviews", []).append(last)
            if verdict == "met":
                row["close_out"] = last
        return last


def wake(config: Config, reason: str) -> list[tasks.Task]:
    """End matching rests under the vault lock, without recording owner engagement."""
    if reason != "computer":
        raise ValueError("unknown wake reason; expected computer")
    with vault_lock(config):
        registered, archived = tasks.load_tasks(config)
        woken = []
        for task in registered + archived:
            if tasks.is_open(task) and task.resume == reason:
                # Like an elapsed date, keep Blocker as the last evidence. Clearing Resume
                # means a blocked task needs its active state restored to remain eligible.
                woken.append(tasks.set_fields(config, task.tid, config.today(), clear=["resume"],
                                               state="in_progress" if task.task_state == "blocked" else None))
        return woken


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
                json.dump({"task": tid, "at": config.local().isoformat(), "text": text}, stream, ensure_ascii=False)
                stream.write("\n")
            Path(staged).replace(path)
        finally:
            if staged is not None:
                Path(staged).unlink(missing_ok=True)


def _checkpoint(tid: str) -> dict | None:
    if runtime.configured() is None or not re.fullmatch(r"T-\d{3}", tid):
        return None
    try:
        row = json.loads((runtime.home() / "records" / "continue" / "tasks" / f"{tid}.json").read_text(encoding="utf-8"))
        return row if row.get("task") == tid and isinstance(row.get("text"), str) else None
    except (OSError, ValueError, AttributeError):
        return None


def checkpoint_text(tid: str) -> str | None:
    row = _checkpoint(tid)
    return row["text"] if row else None


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
        with _progress_records(config) as rows:
            previous = rows.get(tid)
            now = config.local()
            day = now.date().isoformat()
            count = _count(previous or {}, day) + 1
            pushed = _work_commit(task.work)
            current = {**(previous or {}), "state": task.task_state, "quiet": 0, "at": now.isoformat(),
                       "chunks": {"date": day, "count": count},
                       "chunks_since_review": (previous or {}).get("chunks_since_review", 0) + 1}
            current.pop("close_out", None)   # one counted chunk gets the close-out instruction
            if previous is None or "state" not in previous:
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
            saved = _checkpoint(tid)
            result = (saved["text"] if saved and saved != (previous or {}).get("checkpoint") else
                      fm.field(task.body, "Status") or fm.field(task.body, "Next", "Next action") or message)
            if saved:
                current["checkpoint"] = saved
            current["results"] = [*(previous or {}).get("results", []), {"at": now.isoformat(), "text": result}][-6:]
            rows[tid] = current
            limit = budget_for(config, task)
            if tasks.pace(task) == "regular" and count == limit:
                message = (f"{tid} reached today's budget of {limit} chunks; it waits until tomorrow"
                           + ("" if limit == config.daily_chunks else " (the budget is smaller while its owner has not engaged)"))
            return message
