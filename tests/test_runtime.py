"""The engine reads the Demiurge harness through the runtime contract."""
import json

import pytest
from datetime import datetime, timedelta, timezone

from brain import install, runtime, session_tools, signals
from brain.config import Config

NOW = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)


def harness_jobs(home, jobs):
    (home / "harness").mkdir(parents=True, exist_ok=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")


def test_harness_jobs_are_normalized(tmp_path):
    assert runtime.jobs(tmp_path) == []
    harness_jobs(tmp_path, [
        {"id": "a", "name": "demiurge-sync", "skills": [], "script": "demiurge-sync.sh", "lastRunAt": "2026-11-04T22:20:29Z", "lastStatus": "ok"},
        {"id": "b", "name": "watch", "skills": ["orphan-watch"], "monitorScript": "watch-signal.sh", "lastStatus": "skipped"},
        {"id": "c", "name": "explore", "skills": ["orphan-explore"], "lastRunAt": "2026-11-04T21:30:00Z", "lastStatus": "failed", "lastError": "timeout"}])
    found = {job["name"]: job for job in runtime.jobs(tmp_path)}
    assert found["demiurge-sync"]["last_run_at"] == "2026-11-04T22:20:29Z"
    assert found["watch"]["monitor_script"] == "watch-signal.sh" and found["watch"]["last_status"] == "ok"
    assert found["explore"]["last_failure"] == {"at": "2026-11-04T21:30:00Z"} and found["explore"]["last_error"] == "timeout"
    assert signals._last_failure(tmp_path, "orphan-explore") == "2026-11-04T21:30:00Z"


def session(home, sid, platform, entries, day="2026-11-04"):
    folder = home / "harness/sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.json").write_text(json.dumps({"id": sid, "speaker": {"platform": platform}, "entries": [
        {"at": at.isoformat().replace("+00:00", "Z"), "item": {"type": "message", "role": role, "text": "x"}} for at, role in entries]}))


def test_unanswered_reads_harness_sessions(tmp_path):
    session(tmp_path, "waiting", "demiurge", [(NOW - timedelta(minutes=40), "assistant"), (NOW - timedelta(minutes=30), "user")])
    session(tmp_path, "answered", "demiurge", [(NOW - timedelta(minutes=30), "user"), (NOW - timedelta(minutes=29), "assistant")])
    session(tmp_path, "too-new", "demiurge", [(NOW - timedelta(minutes=5), "user")])
    session(tmp_path, "routine", "cron", [(NOW - timedelta(minutes=30), "user")])
    session(tmp_path, "old", "demiurge", [(NOW - timedelta(days=2), "user")], day="2026-11-02")
    assert session_tools.unanswered(tmp_path, NOW) == [("waiting", "demiurge")]


def test_home_install_paths_and_signals_use_agent_home_only(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    assert runtime.configured() is None
    with pytest.raises(RuntimeError, match="^AGENT_HOME is not set$"):
        runtime.home()
    with pytest.raises(RuntimeError, match="^AGENT_HOME is not set$"):
        install.scripts_path()
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    assert runtime.home() == runtime.configured() == home
    signals.watch(Config.load(vault))
    assert (home / "records/watch/signal.json").is_file()
    assert install.scripts_path() == home / "scripts"
    assert install.records_path() == home / "records"
