"""The engine reads the runtime's jobs and conversations the same way under Hermes and the Demiurge harness."""
import json
from datetime import datetime, timedelta, timezone

from brain import runtime, session_tools, signals

NOW = datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)


def harness_jobs(home, jobs):
    (home / "harness").mkdir(parents=True, exist_ok=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")


def test_harness_jobs_win_and_read_in_hermes_field_names(tmp_path):
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron/jobs.json").write_text(json.dumps({"jobs": [{"name": "demiurge-sync", "last_run_at": "2026-11-04T17:02:00-05:00"}]}))
    harness_jobs(tmp_path, [
        {"id": "a", "name": "demiurge-sync", "skills": [], "script": "demiurge-sync.sh", "lastRunAt": "2026-11-04T22:20:29Z", "lastStatus": "ok"},
        {"id": "b", "name": "watch", "skills": ["orphan-watch"], "monitorScript": "watch-signal.sh", "lastStatus": "skipped"},
        {"id": "c", "name": "continue", "skills": ["orphan-continue"], "lastRunAt": "2026-11-04T21:30:00Z", "lastStatus": "failed", "lastError": "timeout"}])
    found = {job["name"]: job for job in runtime.jobs(tmp_path)}
    assert found["demiurge-sync"]["last_run_at"] == "2026-11-04T22:20:29Z"
    assert found["watch"]["monitor_script"] == "watch-signal.sh" and found["watch"]["last_status"] == "ok"
    assert found["continue"]["last_failure"] == {"at": "2026-11-04T21:30:00Z"} and found["continue"]["last_error"] == "timeout"


def test_hermes_jobs_are_read_when_there_is_no_harness(tmp_path):
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron/jobs.json").write_text(json.dumps({"jobs": [{"name": "watch", "last_failure": {"at": "x"}}]}))
    assert runtime.jobs(tmp_path) == [{"name": "watch", "last_failure": {"at": "x"}}]
    assert runtime.jobs(tmp_path / "missing") == []


def test_last_failure_reads_the_harness(tmp_path):
    harness_jobs(tmp_path, [{"id": "c", "name": "continue", "skills": ["brain-continue"], "lastRunAt": "2026-11-04T21:30:00Z", "lastStatus": "failed"}])
    assert signals._last_failure(tmp_path, "brain-continue") == "2026-11-04T21:30:00Z"


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


def test_home_and_configured_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert runtime.configured() is None
    assert runtime.home() == tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    assert runtime.home() == runtime.configured() == tmp_path / "hermes"
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    assert runtime.home() == runtime.configured() == tmp_path / "agent"
    monkeypatch.delenv("HERMES_HOME")
    assert runtime.home() == runtime.configured() == tmp_path / "agent"


def test_signal_and_install_paths_use_agent_home_only(vault, tmp_path, monkeypatch):
    from brain import install
    from brain.config import Config

    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert signals._home() == home
    signals.watch(Config.load(vault))
    assert (home / "records/watch/signal.json").is_file()
    assert install.scripts_path() == home / "scripts"
    assert install.provider_path() == home / "plugins/brain"
    assert install.records_path() == home / "records"
