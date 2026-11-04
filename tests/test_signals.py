"""Cron gates change only with actionable state; every clean check leaves a heartbeat."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from brain import install, loops, nightly, session_tools, signals, tasks, validate
from brain.config import Config
from brain.vault import Vault

AT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def monitor(vault, tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    clock = [AT]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    return Config.load(vault), home, clock


def database(home):
    conn = sqlite3.connect(home / "state.db")
    # The columns consumed from Hermes's hermes_state_common.py schema.
    conn.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);"
                       "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL, "
                       "timestamp REAL NOT NULL);")
    return conn


def session(conn, sid="s1", source="demiurge", age=timedelta(minutes=11), role="user", at=AT):
    conn.execute("INSERT INTO sessions VALUES (?, ?)", (sid, source))
    conn.execute("INSERT INTO messages (session_id, role, timestamp) VALUES (?, ?, ?)",
                 (sid, role, (at - age).timestamp()))
    conn.commit()


def run(capsys, config, command):
    nightly.main([command, "--vault", str(config.vault_path)])
    return capsys.readouterr().out


def test_watch_idle_is_stable_without_database_and_heartbeat_advances(monitor, capsys):
    config, home, clock = monitor
    first = run(capsys, config, "watch-signal")
    assert first == "due:\nchannels:\nunanswered:\n"
    heartbeat = home / "records/watch/signal.json"
    assert json.loads(heartbeat.read_text()) == {"checked": AT.isoformat(), "due": 0, "unanswered": 0}
    clock[0] += timedelta(minutes=2, seconds=13)
    assert run(capsys, config, "watch-signal") == first
    assert json.loads(heartbeat.read_text())["checked"] == clock[0].isoformat()
    assert not (home / "state.db").exists()  # mode=ro never creates the optional source


def test_watch_changes_when_loop_becomes_due_and_closes(monitor):
    config, home, clock = monitor
    loops.add(config, AT, "promised", "Check fares", "Trip")
    first = signals.watch(config)
    clock[0] += timedelta(hours=4)
    second = signals.watch(config)
    item = loops.open_loops(config)[0]
    assert second != first and f"{item.id} · promised · Check fares" in second
    assert signals.watch(config) == second
    assert json.loads((home / "records/watch/signal.json").read_text())["due"] == 1
    loops.close(config, item.id, clock[0], "done", "Checked")
    assert signals.watch(config) == first


def test_watch_changes_for_new_channel_files_and_sorts_rows(monitor):
    config, home, clock = monitor
    initial = signals.watch(config)
    mail = home / "channels/z-account/mail/2026-10-28"
    calendar = home / "channels/a-account/calendar"
    mail.mkdir(parents=True)
    calendar.mkdir(parents=True)
    (calendar / "2026-10-28.md").write_text("Events")
    os.utime(calendar / "2026-10-28.md", (500, 500))
    (mail / "first.md").write_text("Mail")
    os.utime(mail / "first.md", (1000, 1000))
    result = signals.watch(config)
    assert result != initial
    assert result == ("due:\nchannels:\na-account/calendar: 2026-10-28.md @500\n"
                      "z-account/mail: 1 recent, newest 2026-10-28/first.md\nunanswered:")
    (mail / "second.md").write_text("New mail")
    os.utime(mail / "second.md", (2000, 2000))
    changed = signals.watch(config)
    assert changed != result and "second.md" in changed
    assert signals.watch(config) == changed
    # Rewriting old mail (a label or read-state change) is not new mail; empty directories still have a row.
    os.utime(mail / "first.md", (3000, 3000))
    (mail / "first.md").write_text("Mail, now read")
    assert signals.watch(config) == changed
    (home / "channels/a-account/mail").mkdir()
    assert "a-account/mail: \n" in signals.watch(config)


def test_watch_changes_for_unanswered_session_and_reply(monitor):
    config, home, clock = monitor
    initial = signals.watch(config)
    with database(home) as conn:
        session(conn, "z-session", "telegram")
        session(conn, "a-session")
        changed = signals.watch(config)
        assert changed != initial and changed.endswith("unanswered:\na-session · demiurge\nz-session · telegram")
        assert signals.watch(config) == changed
        assert json.loads((home / "records/watch/signal.json").read_text())["unanswered"] == 2
        conn.execute("INSERT INTO messages (session_id, role, timestamp) VALUES ('a-session', 'assistant', ?)",
                     (AT.timestamp(),))
        conn.commit()
        assert "a-session ·" not in signals.watch(config)


@pytest.mark.parametrize("age,expected", [
    (timedelta(minutes=9, seconds=59), False), (timedelta(minutes=10), False),
    (timedelta(minutes=10, seconds=1), True), (timedelta(hours=23, minutes=59, seconds=59), True),
    (timedelta(hours=24), False), (timedelta(hours=24, seconds=1), False),
    (timedelta(seconds=-1), False),
])
def test_unanswered_age_cutoffs(monitor, age, expected):
    config, home, clock = monitor
    with database(home) as conn:
        session(conn, age=age)
    assert bool(session_tools.unanswered(home, AT)) is expected
    # Seconds within the check minute cannot cause membership to flap.
    assert session_tools.unanswered(home, AT + timedelta(seconds=59)) == session_tools.unanswered(home, AT)


def test_unanswered_enters_and_leaves_only_at_age_boundaries(monitor):
    config, home, clock = monitor
    with database(home) as conn:
        session(conn, age=timedelta(minutes=9, seconds=30))
    first = signals.watch(config)
    clock[0] += timedelta(minutes=1)
    assert signals.watch(config) != first
    clock[0] += timedelta(hours=24)
    assert signals.watch(config) == first


@pytest.mark.parametrize("source,role", [("cron", "user"), ("subagent", "user"), ("cli", "user"),
                                         ("demiurge", "assistant"), ("telegram", "tool")])
def test_unanswered_excludes_nonconversations_and_nonuser_tails(monitor, source, role):
    config, home, clock = monitor
    with database(home) as conn:
        session(conn, source=source, role=role)
    assert session_tools.unanswered(home, AT) == []


@pytest.mark.parametrize("broken", ["corrupt", "missing-columns", "locked"])
def test_watch_optional_database_errors_are_empty_and_still_heartbeat(monitor, broken):
    config, home, clock = monitor
    if broken == "corrupt":
        (home / "state.db").write_text("not sqlite")
        assert signals.watch(config) == "due:\nchannels:\nunanswered:"
    elif broken == "missing-columns":
        with sqlite3.connect(home / "state.db") as conn:
            conn.execute("CREATE TABLE sessions (id TEXT)")
        assert signals.watch(config) == "due:\nchannels:\nunanswered:"
    else:
        with database(home) as conn:
            session(conn)
            conn.execute("BEGIN EXCLUSIVE")
            assert signals.watch(config) == "due:\nchannels:\nunanswered:"
    assert (home / "records/watch/signal.json").is_file()


def test_continue_idle_is_stable_across_days_and_writes_heartbeat(monitor, capsys):
    config, home, clock = monitor
    assert run(capsys, config, "continue-signal") == "date: none\n"
    clock[0] += timedelta(days=1)
    assert run(capsys, config, "continue-signal") == "date: none\n"
    assert json.loads((home / "records/continue/signal.json").read_text()) == {
        "checked": clock[0].isoformat(), "due": 0}


def test_continue_reuses_due_tasks_and_changes_with_next_state_or_date(monitor, capsys):
    config, home, clock = monitor
    tasks.hand_off(config, "T-001", AT.date(), "Home")
    first = run(capsys, config, "continue-signal")
    assert first == "date: 2026-10-28\nT-001 · in_progress · ORIGINAL_FULL_BODY\n"
    clock[0] += timedelta(minutes=30)
    assert run(capsys, config, "continue-signal") == first
    task = tasks.load_tasks(config)[0][0]
    task.body = task.body.replace("ORIGINAL_FULL_BODY", "Verify the draft")
    tasks.save_task(config, task, AT.date())
    second = run(capsys, config, "continue-signal")
    assert second != first and "Verify the draft" in second
    task.state["state"] = "ready"
    tasks.save_task(config, task, AT.date())
    third = run(capsys, config, "continue-signal")
    assert third != second and "T-001 · ready ·" in third
    clock[0] += timedelta(days=1)
    assert run(capsys, config, "continue-signal") == third.replace("2026-10-28", "2026-10-29")
    task.state["state"] = "waiting_for_owner"
    tasks.save_task(config, task, AT.date())
    assert run(capsys, config, "continue-signal") == "date: none\n"


def test_continue_lists_archived_tasks_sorted_by_id_and_full_next_line(monitor):
    config, home, clock = monitor
    for tid, state, date in [("T-009", "in_progress", "2026-10-20"), ("T-002", "ready", "2026-10-21"),
                              ("T-003", "completed", "2026-10-20")]:
        tasks.write_archive(config, tasks.Task(tid, "Work", "- **Handed off:** " + date + " · report in Home\n"
                                              "- **Next:** " + "full next line " * 20 + "\n", {"state": state}),
                            "warm", AT.date(), whole=True)
    result = signals.continue_(config).splitlines()
    assert len(result) == 3 and result[1].startswith("T-002 · ready ·") and result[2].startswith("T-009 · in_progress ·")
    assert result[1].endswith(("full next line " * 20).strip())


@pytest.mark.parametrize("command", ["watch-signal", "continue-signal"])
def test_engine_error_is_nonzero_and_does_not_heartbeat(monitor, command):
    config, home, clock = monitor
    result = subprocess.run([sys.executable, "-m", "brain", command, "--vault", str(home / "missing-vault")],
                             capture_output=True, text=True)
    assert result.returncode != 0 and result.stdout == "" and "vault not found" in result.stderr
    assert not (home / "records").exists()


@pytest.mark.parametrize("routine", ["watch", "continue"])
@pytest.mark.parametrize("wrapped", [True, False])
@pytest.mark.parametrize("age", [None, 25, 23, 24])
def test_monitor_hygiene_missing_stale_and_clean(monitor, routine, wrapped, age):
    config, home, clock = monitor
    (home / "cron").mkdir()
    jobs = [{"skills": [config.skill_prefix + routine], "monitor_script": routine + "-signal.sh"}]
    (home / "cron/jobs.json").write_text(json.dumps({"jobs": jobs} if wrapped else jobs))
    if age is not None:
        path = home / "records" / routine / "signal.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"checked": (AT - timedelta(hours=age)).isoformat()}))
    problems = validate.watch_signal_hygiene(Vault(config.vault_path))
    assert len(problems) == (1 if age is None or age > 24 else 0)
    if problems:
        assert problems == [f"{routine}'s monitor has not run cleanly in a day: check `hermes cron list` and run "
                            f"`bash $HERMES_HOME/scripts/{routine}-signal.sh`"]
        assert problems[0] in validate.hygiene(Vault(config.vault_path))


def test_monitor_hygiene_skips_absent_or_unrelated_jobs_and_no_home(monitor, monkeypatch):
    config, home, clock = monitor
    vault = Vault(config.vault_path)
    assert validate.watch_signal_hygiene(vault) == []
    (home / "cron").mkdir()
    path = home / "cron/jobs.json"
    for raw in [{"jobs": [{"skills": ["brain-watch"]}, {"skills": ["other-continue"], "monitor_script": "s.sh"}]},
                {"jobs": None}, [None, {"skills": None}], "bad shape"]:
        path.write_text(json.dumps(raw))
        assert validate.watch_signal_hygiene(vault) == []
    path.write_text("not json")
    assert validate.watch_signal_hygiene(vault) == []
    monkeypatch.delenv("HERMES_HOME")
    assert validate.watch_signal_hygiene(vault) == []


def test_hygiene_handles_invalid_heartbeat_and_profile_continue_prefix(monitor):
    config, home, clock = monitor
    (home / "cron").mkdir()
    config_path = config.repo / "brain.toml"
    config_path.write_text(config_path.read_text() + 'skill_prefix = "custom-"\n')
    (home / "cron/jobs.json").write_text(json.dumps({"jobs": [
        {"skills": ["custom-watch"], "monitor_script": "watch-signal.sh"},
        {"skills": ["custom-continue"], "monitor_script": "continue-signal.sh"}]}))
    for routine in ("watch", "continue"):
        path = home / "records" / routine / "signal.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"checked": "invalid"}')
    assert len(validate.watch_signal_hygiene(Vault(config.vault_path))) == 2


@pytest.mark.parametrize("routine", ["watch", "continue"])
@pytest.mark.parametrize("override", [False, True])
def test_installed_signal_wrappers_use_job_cwd_or_vault_override(monitor, monkeypatch, routine, override):
    config, home, clock = monitor
    installed = install.scripts()
    script = home / "scripts" / f"{routine}-signal.sh"
    assert script in installed and script.stat().st_mode & 0o111 == 0o111
    monkeypatch.delenv("VAULT_REPO", raising=False)
    if override:
        monkeypatch.setenv("VAULT_REPO", str(config.repo))
    result = subprocess.run(["bash", str(script)], cwd=home if override else config.repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("due:\nchannels:\nunanswered:\n" if routine == "watch" else "date: none\n")
    assert (home / "records" / routine / "signal.json").is_file()


def test_ready_loop_already_due_changes_the_watch_signal(monitor):
    config, home, clock = monitor
    loops.add(config, AT, "owed", "Tell Kevin about the share", "Trip", with_="Kevin")
    clock[0] += timedelta(hours=5)
    due = signals.watch(config)
    item = loops.open_loops(config)[0]
    loops.ready(config, item.id, clock[0])
    ready = signals.watch(config)
    assert ready != due and f"{item.id} · owed · ready · Tell Kevin" in ready


@pytest.mark.parametrize("routine", ["watch", "continue"])
def test_a_new_run_failure_changes_the_signal_only_while_something_is_due(monitor, routine):
    config, home, clock = monitor
    (home / "cron").mkdir()
    jobs = home / "cron/jobs.json"
    skill = config.skill_prefix + routine
    signal = signals.watch if routine == "watch" else signals.continue_

    def fail(at):
        jobs.write_text(json.dumps({"jobs": [{"skills": [skill], "last_failure": {"at": at, "detail": "shutdown"}}]}))

    fail("2026-10-28T08:00:00+00:00")
    idle = signal(config)
    assert "last failure" not in idle
    if routine == "watch":
        loops.add(config, AT, "promised", "Check fares", "Trip")
        clock[0] += timedelta(hours=5)
    else:
        tasks.hand_off(config, "T-001", AT.date(), "Home")
    first = signal(config)
    assert "last failure: 2026-10-28T08:00:00+00:00" in first
    assert signal(config) == first
    fail("2026-10-28T17:30:00+00:00")
    assert signal(config) != first


def test_a_rewritten_calendar_day_changes_the_signal(monitor):
    config, home, clock = monitor
    calendar = home / "channels/a/calendar"
    calendar.mkdir(parents=True)
    (calendar / "2026-12-04.md").write_text("One event")
    os.utime(calendar / "2026-12-04.md", (1000, 1000))
    first = signals.watch(config)
    os.utime(calendar / "2026-12-04.md", (2000, 2000))
    assert signals.watch(config) != first


def test_a_routine_delivery_is_not_an_unanswered_turn(monitor):
    config, home, clock = monitor
    conn = database(home)
    conn.execute("ALTER TABLE messages ADD COLUMN content TEXT")
    conn.execute("INSERT INTO sessions VALUES ('room', 'demiurge')")
    conn.execute("INSERT INTO messages (session_id, role, timestamp, content) VALUES (?, 'user', ?, ?)",
                 ("room", (AT - timedelta(minutes=30)).timestamp(), "[Cron delivery: night-chores] round begun"))
    conn.commit()
    assert session_tools.unanswered(home, AT) == []
    conn.execute("INSERT INTO messages (session_id, role, timestamp, content) VALUES (?, 'user', ?, ?)",
                 ("room", (AT - timedelta(minutes=20)).timestamp(), "are you there?"))
    conn.commit()
    assert session_tools.unanswered(home, AT) == [("room", "demiurge")]
