"""Cheap, stable cron monitor output. Only the heartbeat carries the check time."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import channel_files, fm, loops, runtime, session_tools, wake
from .config import Config
from .vault import Vault


def _heartbeat(home: Path, routine: str, now: datetime, **counts: int) -> None:
    path = home / "records" / routine / "signal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps({"checked": now.isoformat(), **counts}) + "\n")


def _last_failure(home: Path, skill: str) -> str:
    """When this routine's last run died (a gateway restart, a provider error): a new failure is a change, so the
    next tick retries instead of waiting for the work itself to move."""
    try:
        for job in runtime.jobs(home):
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
    # Only live registry rows: a pending, revoked or unregistered account's leftover files are never read or reported.
    synced = channel_files.synced_accounts(Vault(config.vault_path), owner_only=False)
    since = _checked_at(home)
    woke = [item["line"] for item in wake.screen(config, home, synced, now, since)]
    if (due or woke) and (failed := _last_failure(home, config.skill_prefix + "watch")):
        lines.append(f"last failure: {failed}")
    lines.append("channels:")
    lines.extend(woke)
    lines.append("unanswered:")
    lines.extend(f"{session_id} · {source}" for session_id, source in unanswered)
    _heartbeat(home, "watch", now, due=len(due), unanswered=len(unanswered))
    return _additions_only(home, lines)


def _checked_at(home: Path) -> datetime | None:
    """When the last watch pass began: what woke before it was handled there and is not listed again."""
    try:
        at = datetime.fromisoformat(json.loads((home / "records/watch/state.json").read_text())["checked_at"])
        return at if at.tzinfo else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _additions_only(home: Path, lines: list[str]) -> str:
    """The harness wakes watch whenever this output changes, so it changes only when a line is new: a loop closing,
    a handled item dropping off or an answered conversation is no reason to wake, and the last output is repeated.
    The count makes every wake's output distinct, even when a line that left comes back."""
    path = home / "records" / "watch" / "signal-last.json"
    try:
        last = json.loads(path.read_text())
    except (OSError, ValueError):
        last = {"lines": None, "text": "", "wakes": 0}
    text, wakes = last["text"], last.get("wakes", 0)
    if last["lines"] is None or set(lines) - set(last["lines"]):
        wakes += 1
        text = "\n".join(lines + [f"wake {wakes}"])
    fm.write_text(path, json.dumps({"lines": lines, "text": text, "wakes": wakes}) + "\n")
    return text



EXPLORE_RUNS = 3


def explore(config: Config) -> str:
    """Explore's gate: one pass on the first tick of each day, then another only when something was added since the
    last (a conversation, a goal or its weight, an open loop, a dated item within the week), at most three a day.
    What was added becomes the run's context, so the pass starts from what changed."""
    from . import compile_context, goals
    now, home = _check(config), runtime.home()
    lines = [f"conversation: {sid} · {day}" for sid, day in session_tools.spoken(home, now)]
    lines += [f"goal: {goal.gid} · {goal.state.get('weight', 'normal')} · {goal.title}" for goal in goals.active(config)]
    lines += [f"loop: {item.id} · {item.text}" for item in loops.open_loops(config)]
    # An empty calendar or a sync note is not something added.
    lines += [line for line in compile_context.upcoming(Vault(config.vault_path)).splitlines()
              if line.startswith("- ") and not line[2:].casefold().startswith(("nothing", "no ", "i can't see", "note:"))]
    if failed := _last_failure(home, config.skill_prefix + "explore"):
        lines.append(f"last failure: {failed}")
    _heartbeat(home, "explore", now, lines=len(lines))
    path, today = home / "records" / "explore" / "signal-last.json", config.today().isoformat()
    try:
        last = json.loads(path.read_text())
    except (OSError, ValueError):
        last = {}
    if last.get("day") != today:
        state = {"day": today, "lines": lines, "runs": 1, "text": f"day: {today}\nfirst pass of the day"}
    else:
        seen = set(last["lines"])
        added = [line for line in lines if line not in seen]
        state = {**last, "lines": sorted(seen | set(lines))}
        if added and last["runs"] < EXPLORE_RUNS:
            state |= {"runs": last["runs"] + 1,
                      "text": "\n".join([f"day: {today}", f"pass {last['runs'] + 1} of at most {EXPLORE_RUNS} today",
                                         "new since the last pass:", *added])}
    fm.write_text(path, json.dumps(state, ensure_ascii=False) + "\n")
    return state["text"]


def evening(config: Config) -> str:
    """Everything the evening decides, as one JSON, or nothing when it has nothing to decide (the run is skipped)."""
    from . import continuation, needs, tasks, validate
    now, today = _check(config), config.today()
    registered, archived = tasks.load_tasks(config)
    ask = lambda item: {"id": item.id, "since": item.since, "text": item.text, "source": item.source, "task": item.task}
    vault = Vault(config.vault_path)
    answered = config.vault_path / config.agent("Answered") / f"{today:%Y-%m}.md"
    signal = {
        "staleTasks": [{"id": task.tid, "state": task.task_state or "open", "tier": task.state.get("tier", "untiered"),
                        "lastTouched": touched or "never", "title": task.title, "status": task.first_status() or ""}
                       for task, touched in tasks.quiet(registered + archived, today, continuation.last_worked(config))],
        "staleAsks": [ask(item) for item in needs.stale(config, today)],
        "overflowAsks": [ask(item) for item in needs.overflow(config, today)],
        "openAsks": [ask(item) for item in needs.items(vault, today) if item.kind == "ask"],
        "needsYouProblems": validate.owner_questions(vault) + validate.needs_hygiene(vault),
        "staleLoops": [loops.as_dict(item) for item in loops.stale(config, now)],
        "answeredToday": [line.removeprefix("- ") for line in answered.read_text(encoding="utf-8").splitlines()
                          if line.startswith(f"- {today.isoformat()} ")] if answered.is_file() else [],
    }
    return json.dumps(signal, ensure_ascii=False, indent=2) if any(signal.values()) else ""
