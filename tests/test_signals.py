"""Cron gates change only with actionable state; every clean check leaves a heartbeat."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from brain import channels, cli, fm, install, loops, session_tools, signals, tasks, validate, wake
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


@pytest.fixture
def scored(monkeypatch):
    """Jev stand-in: a score per subject (calendar items by date), 0.0 for anything unlisted; records what was asked."""
    table, asked = {}, []
    def score(config, accounts, items):
        asked.extend(i["item"] for i in items)
        return [table.get(i["item"].get("subject") if i["item"]["channel"] == "mail" else i["item"]["date"], 0.0) for i in items]
    monkeypatch.setattr(wake, "score", score)
    return table, asked


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
    assert first == "due:\nchannels:\nunanswered:\nwake 1\n"
    heartbeat = home / "records/watch/signal.json"
    assert json.loads(heartbeat.read_text()) == {"checked": AT.isoformat(), "due": 0, "unanswered": 0}
    clock[0] += timedelta(minutes=2, seconds=13)
    assert run(capsys, config, "watch-signal") == first
    assert json.loads(heartbeat.read_text())["checked"] == clock[0].isoformat()
    assert not (home / "harness").exists()  # reading never creates the optional source


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
    assert signals.watch(config) == "due:\nchannels:\nunanswered:\nwake 1"
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
    assert result.stdout == "due:\nchannels:\nunanswered:\nwake 1\n"
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


def mail_file(home, account, name, subject, sender="Alex <alex@example.com>", labels=("INBOX",), day="2026-10-28"):
    folder = home / f"channels/{account}/mail/{day}"
    folder.mkdir(parents=True, exist_ok=True)
    fm.write(folder / name, {"from": sender, "subject": subject, "labels": list(labels)}, "body")


def test_watch_wakes_only_when_something_is_added(monitor, scored):
    config, home, clock = monitor
    loops.add(config, AT, "promised", "Check fares", "Trip")
    first = signals.watch(config)
    clock[0] += timedelta(hours=4)
    second = signals.watch(config)
    item = loops.open_loops(config)[0]
    assert second != first and f"{item.id} · promised · Check fares" in second
    loops.close(config, item.id, clock[0], "done", "Checked")
    assert signals.watch(config) == second   # a loop closing is no reason to wake
    session(home, "z-session", "telegram", at=clock[0])
    session(home, "a-session", at=clock[0])
    third = signals.watch(config)
    assert third.endswith("unanswered:\na-session · demiurge\nz-session · telegram\nwake 3") and f"{item.id}" not in third
    session(home, "a-session", role="assistant", age=timedelta(0), at=clock[0])
    assert signals.watch(config) == third   # nor is a conversation being answered
    clock[0] += timedelta(minutes=30)
    session(home, "a-session", at=clock[0])
    assert signals.watch(config) == third.replace("wake 3", "wake 4")   # but the same conversation unanswered again is


def test_only_actionable_items_wake_and_every_decision_is_logged(monitor, scored):
    config, home, clock = monitor
    table, asked = scored
    register(config, "owner@example.com")
    mail_file(home, "owner@example.com", "old.md", "Already here")
    baseline = signals.watch(config)
    assert asked == [] and "Already here" not in baseline   # the first screen only learns what is there
    table.update({"Rent is due today": 0.9, "Weekly digest": 0.1})
    mail_file(home, "owner@example.com", "digest.md", "Weekly digest", sender="News <news@example.com>")
    assert signals.watch(config) == baseline   # sleeping on an item changes nothing
    mail_file(home, "owner@example.com", "rent.md", "Rent is due today", sender="Landlord <l@example.com>")
    mail_file(home, "owner@example.com", "promo.md", "Sale", sender="Shop <deals@shop.com>", labels=("CATEGORY_PROMOTIONS",))
    woke = signals.watch(config)
    assert woke == ("due:\nchannels:\nowner@example.com/mail/2026-10-28/rent.md · Landlord <l@example.com> · "
                    "Rent is due today\nunanswered:\nwake 2")
    assert [i["subject"] for i in asked] == ["Weekly digest", "Rent is due today"]   # promotions never reach Jev
    assert signals.watch(config) == woke and len(asked) == 2   # each item is screened once
    log = [json.loads(line) for line in (home / "records/watch/screen.jsonl").read_text().splitlines()]
    assert [(row["subject"], row["p"], row["woke"]) for row in log] == [("Weekly digest", 0.1, False), ("Rent is due today", 0.9, True)]


def test_what_watch_already_handled_is_not_listed_again(monitor, scored):
    config, home, clock = monitor
    table, _ = scored
    register(config, "o")
    signals.watch(config)
    table.update({"First": 0.9, "Second": 0.9})
    mail_file(home, "o", "1.md", "First")
    first = signals.watch(config)
    clock[0] += timedelta(minutes=15)
    fm.write_text(home / "records/watch/state.json", json.dumps({"checked_at": clock[0].isoformat()}))
    assert signals.watch(config) == first   # handled, so dropped, but dropping is no change
    clock[0] += timedelta(minutes=15)
    mail_file(home, "o", "2.md", "Second")
    second = signals.watch(config)
    assert "Second" in second and "First" not in second


def test_a_changed_calendar_day_is_screened_with_what_changed(monitor, scored):
    config, home, clock = monitor
    table, asked = scored
    register(config, "o")
    calendar = home / "channels/o/calendar"
    calendar.mkdir(parents=True)
    (calendar / "2026-10-29.md").write_text("- 09:00 Dentist\n")
    (calendar / "2026-10-01.md").write_text("- past\n")
    first = signals.watch(config)
    os.utime(calendar / "2026-10-29.md", (2000, 2000))
    assert signals.watch(config) == first and asked == []   # a rewrite without a change is nothing new
    table["2026-10-29"] = 0.8
    (calendar / "2026-10-29.md").write_text("- 11:00 Dentist (moved)\n")
    (calendar / "2026-10-01.md").write_text("- past, edited\n")
    changed = signals.watch(config)
    assert "o/calendar/2026-10-29.md@" in changed and "· changed · calendar for 2026-10-29 changed" in changed
    assert [i["changes"] for i in asked] == ["-- 09:00 Dentist\n+- 11:00 Dentist (moved)"]   # past days are not screened


def test_an_unanswered_screen_wakes_and_the_gate_can_be_turned_off(monitor, monkeypatch):
    config, home, clock = monitor
    register(config, "o")
    signals.watch(config)
    monkeypatch.setenv("PATH", str(home / "nowhere"))   # no demiurge CLI: Jev cannot answer
    mail_file(home, "o", "1.md", "Anything")
    assert "Anything" in signals.watch(config)
    monkeypatch.setenv("BRAIN_WAKE_GATE", "off")
    assert wake.score(config, [], [{"item": {}}] * 2) == [1.0, 1.0]


def test_watch_reads_only_live_registry_rows(monitor, scored):
    config, home, clock = monitor
    table, asked = scored
    signals.watch(config)
    for account in ("live", "revoked", "stray"):
        mail_file(home, account, "a.md", "Hello")
    register(config, "live")
    with (config.vault_path / config.agent("Channels.md")).open("a") as stream:
        stream.write(channels.Channel("revoked", "owner", "revoked", "`$AGENT_HOME/channels/revoked/`",
                                      "", "", "", "revoked").render() + "\n")
    signals.watch(config)
    assert [i["account"] for i in asked] == ["live"]


def test_the_owner_s_wake_file_refines_the_question(monitor):
    config, home, clock = monitor
    assert "only informative" in wake.instructions(config) and "own wishes" not in wake.instructions(config)
    fm.write(config.vault_path / config.agent("Wake.md"), {"kind": "orientation"}, "- Wake me for any Sentry error.\n")
    assert wake.instructions(config).endswith("- Wake me for any Sentry error.\nShould the assistant wake for this item?")


def test_an_unhandled_wake_survives_the_day_changing_again_and_a_later_revision_wakes_again(monitor, scored):
    config, home, clock = monitor
    table, _ = scored
    register(config, "o")
    calendar = home / "channels/o/calendar"
    calendar.mkdir(parents=True)
    (calendar / "2026-10-29.md").write_text("- 09:00 Dentist\n")
    signals.watch(config)
    table["2026-10-29"] = 0.9
    (calendar / "2026-10-29.md").write_text("- 11:00 Dentist\n")
    moved = signals.watch(config)
    table["2026-10-29"] = 0.0
    (calendar / "2026-10-29.md").write_text("- 11:00 Dentist, room 4\n")
    assert signals.watch(config) == moved   # the watch pass for the move failed: it is still listed
    clock[0] += timedelta(minutes=15)
    fm.write_text(home / "records/watch/state.json", json.dumps({"checked_at": clock[0].isoformat()}))
    signals.watch(config)
    clock[0] += timedelta(minutes=15)
    table["2026-10-29"] = 0.9
    (calendar / "2026-10-29.md").write_text("cancelled\n")
    cancelled = signals.watch(config)
    assert cancelled != moved and "calendar/2026-10-29.md@" in cancelled


def test_screening_past_its_deadline_wakes_instead_of_hanging(monitor, monkeypatch):
    config, home, clock = monitor
    monkeypatch.setattr(wake, "DEADLINE", 0)
    assert wake.score(config, [], [{"item": {}}] * 3) == [None, None, None]


def test_explore_wakes_each_morning_then_only_on_additions_at_most_three_times(monitor, capsys):
    config, home, clock = monitor
    first = run(capsys, config, "explore-signal")
    assert first == f"day: {config.today()}\nfirst pass of the day\n"
    clock[0] += timedelta(hours=2)
    assert run(capsys, config, "explore-signal") == first
    session(home, "s1", at=clock[0])
    second = run(capsys, config, "explore-signal")
    assert second.startswith(f"day: {config.today()}\npass 2 of at most 3 today\nnew since the last pass:\n")
    assert f"conversation: s1 · {config.today()}" in second and "goal:" not in second
    assert run(capsys, config, "explore-signal") == second
    loops.add(config, clock[0], "owed", "Tell Kevin about the share", "Trip", with_="Kevin")
    third = run(capsys, config, "explore-signal")
    assert "pass 3 of at most 3" in third and "Tell Kevin" in third and "conversation:" not in third
    session(home, "s2", at=clock[0])
    assert run(capsys, config, "explore-signal") == third
    assert json.loads((home / "records/explore/signal.json").read_text())["checked"] == clock[0].isoformat()
    clock[0] += timedelta(days=1)
    assert run(capsys, config, "explore-signal") == f"day: {config.today()}\nfirst pass of the day\n"


def test_explore_ignores_routine_and_day_old_conversations(monitor):
    config, home, clock = monitor
    signals.explore(config)
    session(home, "cron1", source="cron")
    assert "pass 2" not in signals.explore(config)
    session(home, "old", age=timedelta(days=1, minutes=1))
    assert "pass 2" not in signals.explore(config)


def test_explore_finds_a_resumed_thread_retries_a_failure_and_ignores_empty_calendar_lines(monitor, monkeypatch):
    config, home, clock = monitor
    signals.explore(config)
    from conftest import harness_session, message
    folder = home / "harness/sessions/2026-10-01"
    folder.mkdir(parents=True)
    record = harness_session("old-thread", [message("user", "x", (clock[0] - timedelta(minutes=30)).isoformat())])
    (folder / "old-thread.json").write_text(json.dumps(record))
    assert "conversation: old-thread" in signals.explore(config)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [
        {"skills": [config.skill_prefix + "explore"], "lastStatus": "failed", "lastRunAt": AT.isoformat()}]}))
    third = signals.explore(config)
    assert "pass 3" in third and "last failure:" in third
    monkeypatch.setattr(signals, "EXPLORE_RUNS", 9)
    from brain import compile_context
    monkeypatch.setattr(compile_context, "upcoming", lambda vault: "## Calendar\n- Nothing on the calendar for the next 7 days.\n- Note: x.")
    assert signals.explore(config) == third
