"""Read the Demiurge harness's jobs and conversations through the runtime contract."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROUTINE_PLATFORMS = ("cron", "subagent", "cli")


def configured() -> Path | None:
    """The explicitly configured workspace, if this process belongs to an agent."""
    value = os.environ.get("AGENT_HOME")
    return Path(value).expanduser() if value else None


def home() -> Path:
    """The explicitly configured agent workspace."""
    workspace = configured()
    if workspace is None:
        raise RuntimeError("AGENT_HOME is not set")
    return workspace


def jobs(home: Path) -> list[dict]:
    """The runtime's jobs normalized to the engine's field names: name, skills, script, prompt, monitor_script, last_run_at,
    last_status, last_error and last_failure ({"at": ...} for the last failed run)."""
    harness = home / "harness" / "jobs.json"
    if harness.is_file():
        try:
            raw = json.loads(harness.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        found = []
        listed = raw.get("jobs", []) if isinstance(raw, dict) else raw
        for job in listed if isinstance(listed, list) else []:
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
    return []
