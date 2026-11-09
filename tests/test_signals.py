"""Cron gates change only with actionable state; every clean check leaves a heartbeat."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from brain import channels, cli, fm, install, loops, session_tools, signals, tasks, validate
from brain.config import Config
from brain.vault import Vault

AT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def monitor(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    clock = [AT]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "notes in Home"})
    return config, home, clock


def register(config, *accounts, status="live"):
    rows = [channels.Channel(account, "owner", account, f"`$AGENT_HOME/channels/{account}/`", "", "", "", status).render()
            for account in accounts]
    fm.write(config.vault_path / config.agent("Channels.md"), {"kind": "orientation"},
             channels.HEADER + "\n" + "\n".join(rows) + "\n")


def session(home, sid="s1", source="demiurge", age=timedelta(minutes=11), role="user", at=AT):
    from conftest import harness_session, message
    folder = home / "harness/sessions/2026-10-28"
    folder.mkdir(parents=True, exist_ok=True)
    record = harness_session(sid, [message(role, "x", (at - age).isoformat())], platform=source)
    (folder / f"{sid}.json").write_text(json.dumps(record))


def run(capsys, config, command):
    cli.main([command, "--vault", str(config.vault_path)])
    return capsys.readouterr().out


def test_watch_idle_is_stable_without_sessions_and_heartbeat_advances(monitor, capsys):
    config, home, clock = monitor
    first = run(capsys, config, "watch-signal")
    assert first == "due:\nchannels:\nunanswered:\n"
    heartbeat = home / "records/watch/signal.json"
    assert json.loads(heartbeat.read_text()) == {"checked": AT.isoformat(), "due": 0, "unanswered": 0}
    clock[0] += timedelta(minutes=2, seconds=13)
    assert run(capsys, config, "watch-signal") == first
    assert json.loads(heartbeat.read_text())["checked"] == clock[0].isoformat()
    assert not (home / "harness").exists()  # reading never creates the optional source


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
    register(config, "a-account", "z-account")
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
                      "z-account/mail: 1 worth a look, newest 2026-10-28/first.md, d21806a4b064\nunanswered:")
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
    assert "a-account/mail: 0 worth a look\n" in signals.watch(config)


def test_watch_changes_for_unanswered_session_and_reply(monitor):
    config, home, clock = monitor
    initial = signals.watch(config)
    session(home, "z-session", "telegram")
    session(home, "a-session")
    changed = signals.watch(config)
    assert changed != initial and changed.endswith("unanswered:\na-session · demiurge\nz-session · telegram")
    assert signals.watch(config) == changed
    assert json.loads((home / "records/watch/signal.json").read_text())["unanswered"] == 2
    session(home, "a-session", role="assistant", age=timedelta(0))
    assert "a-session ·" not in signals.watch(config)


@pytest.mark.parametrize("age,expected", [
    (timedelta(minutes=9, seconds=59), False), (timedelta(minutes=10), False),
    (timedelta(minutes=10, seconds=1), True), (timedelta(hours=23, minutes=59, seconds=59), True),
    (timedelta(hours=24), False), (timedelta(hours=24, seconds=1), False),
    (timedelta(seconds=-1), False),
])
def test_unanswered_age_cutoffs(monitor, age, expected):
    config, home, clock = monitor
    session(home, age=age)
    assert bool(session_tools.unanswered(home, AT)) is expected
    # Seconds within the check minute cannot cause membership to flap.
    assert session_tools.unanswered(home, AT + timedelta(seconds=59)) == session_tools.unanswered(home, AT)


@pytest.mark.parametrize("source,role", [("cron", "user"), ("subagent", "user"), ("cli", "user"),
                                         ("demiurge", "assistant"), ("telegram", "tool")])
def test_unanswered_excludes_nonconversations_and_nonuser_tails(monitor, source, role):
    config, home, clock = monitor
    session(home, source=source, role=role)
    assert session_tools.unanswered(home, AT) == []


@pytest.mark.parametrize("broken", ["invalid JSON", "{}", '{"entries": [null]}'])
def test_watch_optional_session_errors_are_empty_and_still_heartbeat(monitor, broken):
    config, home, clock = monitor
    folder = home / "harness/sessions/2026-10-28"
    folder.mkdir(parents=True)
    (folder / "broken.json").write_text(broken)
    assert signals.watch(config) == "due:\nchannels:\nunanswered:"
    assert (home / "records/watch/signal.json").is_file()


def test_engine_error_is_nonzero_and_does_not_heartbeat(monitor):
    config, home, clock = monitor
    result = subprocess.run([sys.executable, "-m", "brain", "watch-signal", "--vault", str(home / "missing-vault")],
                             capture_output=True, text=True)
    assert result.returncode != 0 and result.stdout == "" and "vault not found" in result.stderr
    assert not (home / "records").exists()


@pytest.mark.parametrize("wrapped,age", [(True, None), (False, 25), (True, 23), (False, 24)])
def test_monitor_hygiene_missing_stale_and_clean(monitor, wrapped, age):
    config, home, clock = monitor
    routine = "watch"
    (home / "harness").mkdir()
    jobs = [{"skills": [config.skill_prefix + routine], "monitorScript": routine + "-signal.sh"}]
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": jobs} if wrapped else jobs))
    if age is not None:
        path = home / "records" / routine / "signal.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"checked": (AT - timedelta(hours=age)).isoformat()}))
    problems = validate.watch_signal_hygiene(Vault(config.vault_path))
    assert len(problems) == (1 if age is None or age > 24 else 0)
    if problems:
        assert problems == [f"{routine}'s monitor has not run cleanly in a day: check `brain schedule list` and run "
                            f"`bash $AGENT_HOME/scripts/{routine}-signal.sh`"]
        assert problems[0] in validate.hygiene(Vault(config.vault_path))


def test_monitor_hygiene_skips_absent_or_unrelated_jobs_and_no_home(monitor, monkeypatch):
    config, home, clock = monitor
    vault = Vault(config.vault_path)
    assert validate.watch_signal_hygiene(vault) == []
    (home / "harness").mkdir()
    path = home / "harness/jobs.json"
    for raw in [{"jobs": [{"skills": ["brain-watch"]}, {"skills": ["other-continue"], "monitorScript": "s.sh"}]},
                {"jobs": None}, [None, {"skills": None}], "bad shape"]:
        path.write_text(json.dumps(raw))
        assert validate.watch_signal_hygiene(vault) == []
    path.write_text("not json")
    assert validate.watch_signal_hygiene(vault) == []
    monkeypatch.delenv("AGENT_HOME")
    assert validate.watch_signal_hygiene(vault) == []


def test_hygiene_handles_invalid_watch_heartbeat(monitor):
    config, home, clock = monitor
    (home / "harness").mkdir()
    config_path = config.repo / "brain.toml"
    config_path.write_text(config_path.read_text() + 'skill_prefix = "custom-"\n')
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [
        {"skills": ["custom-watch"], "monitorScript": "watch-signal.sh"}]}))
    path = home / "records/watch/signal.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"checked": "invalid"}')
    assert len(validate.watch_signal_hygiene(Vault(config.vault_path))) == 1


@pytest.mark.parametrize("override", [False, True])
def test_installed_watch_wrapper_uses_job_cwd_or_vault_override(monitor, monkeypatch, override):
    config, home, clock = monitor
    installed = install.scripts()
    script = home / "scripts/watch-signal.sh"
    assert script in installed and script.stat().st_mode & 0o111 == 0o111
    monkeypatch.delenv("VAULT_REPO", raising=False)
    if override:
        monkeypatch.setenv("VAULT_REPO", str(config.repo))
    result = subprocess.run(["bash", str(script)], cwd=home if override else config.repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "due:\nchannels:\nunanswered:\n"
    assert (home / "records/watch/signal.json").is_file()


def test_ready_loop_already_due_changes_the_watch_signal(monitor):
    config, home, clock = monitor
    loops.add(config, AT, "owed", "Tell Kevin about the share", "Trip", with_="Kevin")
    clock[0] += timedelta(hours=5)
    due = signals.watch(config)
    item = loops.open_loops(config)[0]
    loops.ready(config, item.id, clock[0])
    ready = signals.watch(config)
    assert ready != due and f"{item.id} · owed · ready · Tell Kevin" in ready


def test_a_new_run_failure_changes_the_signal_only_while_something_is_due(monitor):
    config, home, clock = monitor
    (home / "harness").mkdir()
    jobs = home / "harness/jobs.json"
    skill = config.skill_prefix + "watch"
    signal = signals.watch

    def fail(at):
        jobs.write_text(json.dumps({"jobs": [{"skills": [skill], "lastStatus": "failed", "lastRunAt": at}]}))

    fail("2026-10-28T08:00:00+00:00")
    idle = signal(config)
    assert "last failure" not in idle
    loops.add(config, AT, "promised", "Check fares", "Trip")
    clock[0] += timedelta(hours=5)
    first = signal(config)
    assert "last failure: 2026-10-28T08:00:00+00:00" in first
    assert signal(config) == first
    fail("2026-10-28T17:30:00+00:00")
    assert signal(config) != first


def test_a_rewritten_calendar_day_changes_the_signal(monitor):
    config, home, clock = monitor
    register(config, "a")
    calendar = home / "channels/a/calendar"
    calendar.mkdir(parents=True)
    (calendar / "2026-12-04.md").write_text("One event")
    os.utime(calendar / "2026-12-04.md", (1000, 1000))
    first = signals.watch(config)
    os.utime(calendar / "2026-12-04.md", (2000, 2000))
    assert signals.watch(config) != first


def test_watch_wakes_for_people_and_bots_but_not_promotions_or_sent_mail(monitor):
    config, home, clock = monitor
    register(config, "owner@example.com")
    mail = home / "channels/owner@example.com/mail/2026-10-28"
    mail.mkdir(parents=True)
    fm.write(mail / "a.md", {"from": "Alex <alex@example.com>", "labels": ["INBOX"]}, "body")
    first = signals.watch(config)
    assert "1 worth a look, newest 2026-10-28/a.md" in first
    fm.write(mail / "b.md", {"from": "Robinhood <noreply@robinhood.com>", "labels": ["CATEGORY_UPDATES", "INBOX"]}, "body")
    second = signals.watch(config)
    assert second != first and "2 worth a look, newest 2026-10-28/b.md" in second
    fm.write(mail / "c.md", {"from": "Robinhood <noreply@robinhood.com>", "labels": ["CATEGORY_UPDATES", "INBOX"]}, "body")
    assert signals.watch(config) != second   # a second message from the same bot still wakes watch
    third = signals.watch(config)
    fm.write(mail / "promo.md", {"from": "Shop <deals@shop.com>", "labels": ["CATEGORY_PROMOTIONS"]}, "body")
    fm.write(mail / "sent.md", {"from": "Owner <owner@example.com>", "labels": ["SENT"]}, "body")
    assert signals.watch(config) == third
    (mail / "c.md").unlink()
    fm.write(mail / "a2.md", {"from": "Alex <alex@example.com>", "labels": ["INBOX"]}, "body")
    assert signals.watch(config) != third   # same count and newest name, a different message


def test_watch_reads_only_live_registry_rows(monitor):
    config, home, clock = monitor
    for account in ("live", "revoked", "stray"):
        (home / f"channels/{account}/calendar").mkdir(parents=True)
        (home / f"channels/{account}/calendar/2026-10-28.md").write_text("Events")
    register(config, "live")
    with (config.vault_path / config.agent("Channels.md")).open("a") as stream:
        stream.write(channels.Channel("revoked", "owner", "revoked", "`$AGENT_HOME/channels/revoked/`",
                                      "", "", "", "revoked").render() + "\n")
    rows = signals.watch(config).split("channels:\n")[1].split("unanswered:")[0]
    assert rows.startswith("live/calendar: ") and "revoked" not in rows and "stray" not in rows
