"""Pace belongs to the task; only engine-observed outcomes move it back."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from brain import cli, fm, journal, signals, state, tasks, validate
from brain.config import Config
from brain.vault import Vault
from conftest import make_vault

AT = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


@pytest.fixture
def work(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    clock = [AT]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "finished notes in Home"})
    tasks.hand_off(config, "T-001", AT.date(), "Home")
    return config, home, clock


def task(config):
    return tasks.load_tasks(config)[0][0]


def run(config, capsys, *args):
    cli.main(["continue", *args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


def fingerprint(config, home):
    return json.loads((home / "records/continue/progress.json").read_text())[str(config.vault_path.resolve())]["T-001"]


def demote(config, capsys):
    return [run(config, capsys, "--chunk", "T-001") for _ in range(4)]


@pytest.mark.parametrize("archived", [False, True])
def test_pace_parse_set_remove_and_owner_handoff(work, archived):
    config, home, clock = work
    if archived:
        original = task(config)
        tasks.write_archive(config, original, "warm", AT.date(), whole=True)
        path = config.vault_path / config.agent("Tasks.md")
        data, _ = fm.read(path)
        fm.write(path, data, "# Tasks\n" + original.render_line(config))
    assert tasks.pace(task(config)) == "regular"
    tasks.set_pace(config, "T-001", "daily", AT.date())
    tasks.set_pace(config, "T-001", "daily", AT.date())
    assert tasks.pace(task(config)) == "daily" and task(config).body.count("**Pace:") == 1
    assert "ORIGINAL_FULL_BODY" in task(config).body
    assert state.build(config, AT.date())["tasks"][0]["pace"] == "daily"
    tasks.set_pace(config, "T-001", "regular", AT.date())
    assert "**Pace:" not in task(config).body
    tasks.set_pace(config, "T-001", "daily", AT.date())
    assert tasks.pace(tasks.hand_off(config, "T-001", AT.date(), "Home")) == "regular"
    assert "**Pace:" not in task(config).body


def test_due_filters_pace_and_resting_states_oldest_first():
    regular = tasks.Task("T-002", "Regular", "- **Handed off:** 2026-11-02\n")
    daily = tasks.Task("T-003", "Daily", "- **Handed off:** 2026-11-01\n- **Pace:** daily\n")
    older = tasks.Task("T-001", "Older", "- **Handed off:** 2026-10-30\n- **Pace:** daily\n")
    all_tasks = [regular, daily, older]
    for item in all_tasks:
        item.body += "- **Delivers:** a result in Home\n"
    assert tasks.due(all_tasks) == [regular]
    assert tasks.due(all_tasks, pace="daily") == [older, daily]
    for resting in (*tasks.RESTING, "completed", "cancelled"):
        daily.state["state"] = resting
        assert tasks.due([daily], pace="daily") == []


def test_baseline_and_three_quiet_chunks_demote_then_stay_daily(work, capsys):
    config, home, clock = work
    assert demote(config, capsys) == [
        "T-001 baseline stored: 0 quiet chunks",
        "T-001 no progress: 1 quiet chunks; pace regular",
        "T-001 no progress: 2 quiet chunks; pace regular",
        "T-001 moved to daily: 3 chunks without progress"]
    assert fingerprint(config, home)["quiet"] == 3 and tasks.pace(task(config)) == "daily"
    assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 4 quiet chunks; pace daily"


def work_state(config, home, saved):
    tasks.hand_off(config, "T-001", AT.date(), work="trip-plan")
    path = home / "records/works/state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved))
    return path


@pytest.mark.parametrize("saved,progress", [
    ({"lastPush": "2026-11-04T12:15:00Z"}, True),
    ({"lastPush": "2026-11-04T07:15:00-05:00"}, True),
    ({"lastPush": "2026-11-04T12:15:00Z",
      "lastCommit": {"hash": "new", "when": "2026-11-04T12:15:00Z"}}, True),
    ({"lastPush": "2026-11-04T12:15:00Z",
      "lastCommit": {"hash": "new", "when": "2026-11-04T12:10:00Z"}}, True),
    ({"lastPush": "2026-11-04T12:00:00Z"}, False),
    ({"lastPush": "2026-11-04T11:59:00Z"}, False),
    ({"lastCommit": {"hash": "local", "when": "2026-11-04T12:15:00Z"}}, False),
    ({"lastPush": "2026-11-04T11:59:00Z",
      "lastCommit": {"hash": "local", "when": "2026-11-04T12:15:00Z"}}, False),
    ({"lastPush": "2026-11-04T11:59:00Z", "refusal": "read-only",
      "lastCommit": {"hash": "refused", "when": "2026-11-04T12:15:00Z"}}, False),
    ({"lastPull": "2026-11-04T12:15:00Z"}, False),
])
def test_work_saved_progress_requires_push_since_previous_chunk(work, capsys, saved, progress):
    config, home, clock = work
    path = work_state(config, home, {})
    assert "baseline stored" in run(config, capsys, "--chunk", "T-001")
    path.write_text(json.dumps({"trip-plan": saved}))
    clock[0] += timedelta(minutes=30)
    result = run(config, capsys, "--chunk", "T-001")
    assert result == ("T-001 progress: work saved; quiet reset to 0" if progress else
                      "T-001 no progress: 1 quiet chunks; pace regular")
    assert fingerprint(config, home)["quiet"] == (0 if progress else 1)
    clock[0] += timedelta(minutes=30)
    assert "no progress" in run(config, capsys, "--chunk", "T-001")


def test_work_saved_promotes_daily_to_regular(work, capsys):
    config, home, clock = work
    path = work_state(config, home, {})
    demote(config, capsys)
    clock[0] += timedelta(minutes=30)
    path.write_text(json.dumps({"trip-plan": {"lastPush": clock[0].isoformat()}}))
    assert run(config, capsys, "--chunk", "T-001") == "T-001 back to regular: work saved"
    assert tasks.pace(task(config)) == "regular"
    assert fingerprint(config, home)["quiet"] == 0


@pytest.mark.parametrize("raw", [None, b"invalid json", b"\xff", b"[]", b"null",
                                b'{"trip-plan": null}', b'{"trip-plan": []}',
                                b'{"trip-plan": {"lastPush": "bad-date"}}',
                                b'{"trip-plan": {"lastPush": 12}}'])
def test_missing_or_unreadable_work_state_is_no_progress(work, capsys, raw):
    config, home, clock = work
    path = work_state(config, home, {})
    if raw is None:
        path.unlink()
    else:
        path.write_bytes(raw)
    run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 1 quiet chunks; pace regular"


def test_work_state_io_error_is_no_progress(work, capsys, monkeypatch):
    config, home, clock = work
    path = work_state(config, home, {"trip-plan": {"lastPush": "2026-11-04T12:15:00Z"}})
    read_text = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("not readable")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    assert "no progress" in run(config, capsys, "--chunk", "T-001")


@pytest.mark.parametrize("named_work", [True, False])
def test_saves_to_other_works_or_reserved_library_key_are_not_progress(work, capsys, named_work):
    config, home, clock = work
    work_state(config, home, {slug: {"lastPush": "2026-11-04T12:15:00Z"} for slug in ("other-work", "_library")})
    if not named_work:
        current = task(config)
        current.body = current.body.replace("- **Work:** trip-plan\n", "")
        tasks.save_task(config, current, AT.date())
    run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    assert "no progress" in run(config, capsys, "--chunk", "T-001")


def test_a_new_plan_or_a_rewritten_verified_line_is_not_progress(work, capsys):
    config, home, clock = work
    demote(config, capsys)
    current = task(config)
    current.body = current.body.replace("ORIGINAL_FULL_BODY", "New plan and checkpoint")
    tasks.save_task(config, current, AT.date())
    assert "no progress" in run(config, capsys, "--chunk", "T-001")
    # Production 2026-11-04: Verified rewritten each chunk with the agent's own self-checks kept runaways busy.
    current.body += "- **Verified:** all 67 routing-lab tests and the code-archive readback passed; self-review\n"
    tasks.save_task(config, current, AT.date())
    assert "no progress" in run(config, capsys, "--chunk", "T-001")
    assert tasks.pace(task(config)) == "daily"


@pytest.mark.parametrize("new_state", [*tasks.RESTING, "completed", "cancelled"])
@pytest.mark.parametrize("archived", [False, True])
def test_resting_or_closed_transition_is_progress_even_in_archive(work, capsys, new_state, archived):
    config, home, clock = work
    if archived:
        tasks.write_archive(config, task(config), "warm", AT.date(), whole=True)
        data, _ = fm.read(config.vault_path / config.agent("Tasks.md"))
        fm.write(config.vault_path / config.agent("Tasks.md"), data, "# Tasks\n")
    demote(config, capsys)
    registered, other = tasks.load_tasks(config)
    current = (registered + other)[0]
    current.state["state"] = new_state
    tasks.save_task(config, current, AT.date())
    assert run(config, capsys, "--chunk", "T-001") == f"T-001 back to regular: state became {new_state}"
    assert fingerprint(config, home)["quiet"] == 0
    # Staying blocked or closed does not repeatedly count as progress.
    assert "no progress" in run(config, capsys, "--chunk", "T-001")


@pytest.mark.parametrize("kind", ["draft", "reflection"])
@pytest.mark.parametrize("created", ["2026-11-04T12:01:00+00:00", "2026-11-04", "2026-11-03",
                                     "2026-11-04T11:59:00+00:00", "not-a-date", None])
def test_new_vault_output_does_not_promote_daily_task(work, capsys, kind, created):
    config, home, clock = work
    demote(config, capsys)
    clock[0] += timedelta(minutes=30)
    data = {"kind": kind, "task": "T-001"}
    if created is not None:
        data["created"] = created
    path = config.vault_path / "Prepared/Comparison.md"
    path.parent.mkdir()
    fm.write(path, data, "Draft held in the vault")
    # It remains a state output, but is invisible to the owner and cannot restore the task's pace.
    assert any(output["task"] == "T-001" for output in state.build(config, AT.date())["outputs"])
    assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 4 quiet chunks; pace daily"
    assert fingerprint(config, home)["quiet"] == 4
    assert tasks.pace(task(config)) == "daily"


def test_existing_output_and_deleting_verified_are_not_progress(work, capsys):
    config, home, clock = work
    current = task(config)
    current.body += "- **Verified:** Existing evidence\n"
    tasks.save_task(config, current, AT.date())
    fm.write(config.vault_path / "Existing.md", {"kind": "draft", "task": "T-001", "created": AT.date()}, "Draft")
    run(config, capsys, "--chunk", "T-001")
    current.body = current.body.replace("- **Verified:** Existing evidence\n", "")
    tasks.save_task(config, current, AT.date())
    assert "no progress" in run(config, capsys, "--chunk", "T-001")
    assert fingerprint(config, home)["quiet"] == 1


def test_a_new_draft_does_not_prevent_regular_task_demotion(work, capsys):
    config, home, clock = work
    for _ in range(3):
        run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    fm.write(config.vault_path / "Comparison.md", {"kind": "draft", "task": "T-001", "created": clock[0].isoformat()}, "Draft")
    assert run(config, capsys, "--chunk", "T-001") == "T-001 moved to daily: 3 chunks without progress"
    assert fingerprint(config, home)["quiet"] == 3
    assert tasks.pace(task(config)) == "daily"


def test_existing_progress_fingerprint_keeps_its_quiet_count(work, capsys):
    config, home, clock = work
    for _ in range(3):
        run(config, capsys, "--chunk", "T-001")
    path = home / "records/continue/progress.json"
    records = json.loads(path.read_text())
    previous = records[str(config.vault_path.resolve())]["T-001"]
    previous.update(outputs=["old-draft"], verified="old-self-check")
    path.write_text(json.dumps(records))
    assert run(config, capsys, "--chunk", "T-001") == "T-001 moved to daily: 3 chunks without progress"
    assert fingerprint(config, home)["quiet"] == 3


@pytest.mark.parametrize("tid", ["T-001", "T-999"])
def test_chunk_rejects_unknown_and_non_handed_off(vault, tmp_path, monkeypatch, capsys, tid):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit) as error:
        cli.main(["continue", "--chunk", tid, "--vault", str(vault)])
    assert error.value.code == 2 and f"{tid} is not a handed-off task" in capsys.readouterr().err


def test_pace_cli_and_daily_due(work, capsys):
    config, home, clock = work
    assert run(config, capsys, "--pace", "T-001", "daily") == "T-001 pace: daily"
    assert "T-001 · handed off" in run(config, capsys, "--due", "--daily")
    with pytest.raises(SystemExit) as error:
        run(config, capsys, "--due")
    assert error.value.code == 1
    capsys.readouterr()
    assert run(config, capsys, "--pace", "T-001", "regular") == "T-001 pace: regular"
    assert "T-001 · handed off" in run(config, capsys, "--due")
    with pytest.raises(SystemExit) as error:
        run(config, capsys, "--pace", "T-001", "fast")
    assert error.value.code == 2


def test_progress_separates_vaults(work, tmp_path, monkeypatch, capsys):
    config, home, clock = work
    demote(config, capsys)
    second = Config.load(make_vault(tmp_path / "other" / "memory"))
    tasks.hand_off(second, "T-001", AT.date())
    assert run(second, capsys, "--chunk", "T-001") == "T-001 baseline stored: 0 quiet chunks"
    assert fingerprint(config, home)["quiet"] == 3
    assert fingerprint(second, home)["quiet"] == 0


def test_daily_signal_ids_date_heartbeat_and_ignores_next(work, capsys):
    config, home, clock = work
    tasks.set_pace(config, "T-001", "daily", AT.date())
    assert signals.continue_(config) == "date: none"
    cli.main(["continue-signal", "--daily", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out == "date: 2026-11-04\nT-001\n"
    current = task(config)
    current.body = current.body.replace("ORIGINAL_FULL_BODY", "Different Next")
    tasks.save_task(config, current, AT.date())
    clock[0] += timedelta(minutes=30)
    assert signals.continue_(config, daily=True) == "date: 2026-11-04\nT-001"
    assert json.loads((home / "records/continue-daily/signal.json").read_text()) == {
        "checked": clock[0].isoformat(), "due": 1}
    clock[0] += timedelta(days=1)
    assert signals.continue_(config, daily=True) == "date: 2026-11-05\nT-001"
    tasks.set_pace(config, "T-001", "regular", AT.date())
    assert signals.continue_(config, daily=True) == "date: none"
    assert json.loads((home / "records/continue-daily/signal.json").read_text())["due"] == 0


@pytest.mark.parametrize("identification", [{"name": "continue-daily"}, {"prompt": "Run continue daily."}])
def test_retired_daily_monitor_needs_no_heartbeat(work, identification):
    config, home, clock = work
    (home / "harness").mkdir(parents=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [
        {"skills": [config.skill_prefix + "continue"], "monitorScript": "continue-daily-signal.sh", **identification}]}))
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == []
    signals.continue_(config)
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == []
    signals.continue_(config, daily=True)
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == []
    clock[0] += timedelta(hours=25)
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == []


def test_regular_failure_retry_skips_daily_job_with_the_same_skill(work):
    config, home, clock = work
    (home / "harness").mkdir(parents=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [
        {"name": "continue-daily", "skills": [config.skill_prefix + "continue"], "lastStatus": "failed", "lastRunAt": "daily"},
        {"name": "continue", "skills": [config.skill_prefix + "continue"], "lastStatus": "failed", "lastRunAt": "regular"}]}))
    assert "last failure: regular" in signals.continue_(config)
    tasks.set_pace(config, "T-001", "daily", AT.date())
    assert "last failure" not in signals.continue_(config, daily=True)


@pytest.mark.parametrize("title,owner,promoted", [("agent/Tasks#T-001", True, True),
                                                   ("agent/Tasks.md#T-001 — Sample", True, True),
                                                   ("agent/Tasks", True, False),
                                                   ("agent/Tasks#T-002", True, False),
                                                   ("agent/Tasks#T-001", False, False),
                                                   ("T-001", True, True)])
def test_record_owner_touch_promotes_only_own_task_entry(work, capsys, title, owner, promoted):
    config, home, clock = work
    tasks.write_archive(config, task(config), "warm", AT.date(), whole=True)
    demote(config, capsys)
    journal.append_entry(config, {"outcome": "progress", "headline": "Discussed the task", "touched": [title]},
                         "cli" if owner else "slack", AT)
    assert tasks.pace(task(config)) == ("regular" if promoted else "daily")
    if promoted:
        assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 1 quiet chunks; pace regular"


def test_fresh_owner_handoff_starts_a_new_regular_quiet_sequence(work, capsys):
    config, home, clock = work
    demote(config, capsys)
    tasks.hand_off(config, "T-001", AT.date(), "Home")
    assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 1 quiet chunks; pace regular"


def test_chunk_without_runtime_skips_progress_recording(work, monkeypatch, capsys):
    config, home, clock = work
    monkeypatch.delenv("AGENT_HOME")
    assert run(config, capsys, "--chunk", "T-001") == "T-001 progress not recorded: AGENT_HOME is not set"
    assert not (home / "records/continue/progress.json").exists()


def test_work_saved_needs_more_than_whitespace(work, capsys):
    import subprocess
    config, home, clock = work
    copy = home / "works/trip-plan"
    copy.mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(copy), *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")

    def commit(text):
        (copy / "index.md").write_text(text)
        git("add", "-A")
        git("commit", "-qm", "save")
        return git("rev-parse", "HEAD")

    path = work_state(config, home, {})
    first = commit("# Trip\n\nDay one: Lisbon.\n")
    path.write_text(json.dumps({"trip-plan": {"lastPush": clock[0].isoformat(), "lastCommit": {"hash": first, "when": clock[0].isoformat()}}}))
    assert "baseline stored" in run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    spaced = commit("# Trip\n\nDay one:   Lisbon.  \n")
    path.write_text(json.dumps({"trip-plan": {"lastPush": clock[0].isoformat(), "lastCommit": {"hash": spaced, "when": clock[0].isoformat()}}}))
    clock[0] += timedelta(minutes=1)
    assert run(config, capsys, "--chunk", "T-001") == "T-001 no progress: 1 quiet chunks; pace regular"
    clock[0] += timedelta(minutes=30)
    moved = commit("# Trip\n\nDay one: Lisbon.\nDay two: Sintra.\n")
    path.write_text(json.dumps({"trip-plan": {"lastPush": clock[0].isoformat(), "lastCommit": {"hash": moved, "when": clock[0].isoformat()}}}))
    clock[0] += timedelta(minutes=1)
    assert run(config, capsys, "--chunk", "T-001") == "T-001 progress: work saved; quiet reset to 0"


def test_handoff_reports_in_the_conversation_it_came_from(vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "jn7ledger")
    cli.main(["handoff", "T-001", "--report", "the Ledger thread", "--vault", str(vault)])
    assert "reported in demiurge:jn7ledger" in capsys.readouterr().out
    assert tasks.load_tasks(Config.load(vault))[0][0].handoff[1] == "demiurge:jn7ledger"
    monkeypatch.delenv("DEMIURGE_CONVERSATION")
    cli.main(["handoff", "T-001", "--report", "Home", "--vault", str(vault)])
    assert tasks.load_tasks(Config.load(vault))[0][0].handoff[1] == "Home"



def test_due_puts_the_task_worked_least_recently_first(work, capsys):
    from brain import continuation
    config, home, clock = work
    one = task(config)
    two = tasks.Task(tid="T-099", title="Torts notes", state={"state": "in_progress"},
                     body="- **Handed off:** 2026-11-05 · report in Home\n- **Delivers:** notes in Home\n")
    assert [t.tid for t in tasks.due([two, one])] == ["T-001", "T-099"]   # oldest handoff without history
    run(config, capsys, "--chunk", "T-001")
    worked = continuation.last_worked(config)
    assert set(worked) == {"T-001"}
    assert [t.tid for t in tasks.due([one, two], worked=worked)] == ["T-099", "T-001"]   # never worked goes first
    worked["T-099"] = (clock[0] + timedelta(minutes=30)).isoformat()
    assert [t.tid for t in tasks.due([one, two], worked=worked)] == ["T-001", "T-099"]


def test_handoff_with_workers_tells_the_turn_not_to_start(vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("DEMIURGE_WORKERS", "1")
    cli.main(["handoff", "T-001", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert "a background worker starts on it" in out and "do not start the work in this turn" in out
