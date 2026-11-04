"""What the agent's runtime records about its jobs and conversations, read the same way whether the agent runs under
Hermes (`cron/jobs.json`, `state.db`) or the Demiurge harness (`harness/jobs.json`, `harness/sessions/`). The harness's
files win when both exist: after a switch, Hermes's stop changing."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROUTINE_PLATFORMS = ("cron", "subagent", "cli")


def configured() -> Path | None:
    """The explicitly configured workspace, if this process belongs to an agent."""
    value = os.environ.get("AGENT_HOME") or os.environ.get("HERMES_HOME")
    return Path(value).expanduser() if value else None


def home() -> Path:
    """The agent workspace: AGENT_HOME, then HERMES_HOME, then ~/.hermes."""
    return configured() or Path.home() / ".hermes"


def jobs(home: Path) -> list[dict]:
    """The runtime's jobs in Hermes's field names: name, skills, script, prompt, monitor_script, last_run_at,
    last_status, last_error and last_failure ({"at": ...} for the last failed run)."""
    harness = home / "harness" / "jobs.json"
    if harness.is_file():
        try:
            raw = json.loads(harness.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        found = []
        for job in raw.get("jobs", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []:
            if not isinstance(job, dict):
                continue
            status = job.get("lastStatus")
            found.append({"id": job.get("id"), "name": job.get("name"), "skills": job.get("skills") or [],
                          "script": job.get("script"), "prompt": job.get("prompt"),
                          "schedule": job.get("schedule"), "deliver": job.get("deliver", "local"),
                          "no_agent": job.get("noAgent", False), "workdir": job.get("workdir"),
                          "enabled": job.get("enabled", True),
                          "monitor_script": job.get("monitorScript"), "last_run_at": job.get("lastRunAt"),
                          # a monitor that saw no change skipped the run cleanly; that is not a failure
                          "last_status": "ok" if status == "skipped" else status, "last_error": job.get("lastError"),
                          "last_failure": {"at": job.get("lastRunAt")} if status == "failed" else None})
        return found
    try:
        raw = json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    listed = raw.get("jobs", []) if isinstance(raw, dict) else raw
    return [job for job in listed if isinstance(job, dict)] if isinstance(listed, list) else []
