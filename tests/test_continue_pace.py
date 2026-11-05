"""Pace belongs to the task; only engine-observed outcomes move it back."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from brain import fm, journal, nightly, signals, state, tasks, validate
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
    tasks.hand_off(config, "T-001", AT.date(), "Home")
    return config, home, clock


def task(config):
    return tasks.load_tasks(config)[0][0]


def run(config, capsys, *args):
    nightly.main(["continue", *args, "--vault", str(config.vault_path)])
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


@pytest.mark.parametrize("created,progress", [("2026-11-04T12:01:00+00:00", True),
                                              ("2026-11-04", True), ("2026-11-03", False),
                                              ("2026-11-04T11:59:00+00:00", False),
                                              ("2026-11-04T12:00:00+00:00", False),
                                              ("not-a-date", False), (None, False)])
def test_new_output_counts_only_for_the_task_and_since_previous_chunk(work, capsys, created, progress):
    config, home, clock = work
    demote(config, capsys)
    fm.write(config.vault_path / "Unrelated.md", {"kind": "draft", "created": AT.date(), "task": "T-002"}, "Draft")
    assert "no progress" in run(config, capsys, "--chunk", "T-001")
    data = {"kind": "draft", "task": "T-001"}
    if created is not None:
        data["created"] = created
    fm.write(config.vault_path / "Comparison.md", data, "Delivered draft")
    result = run(config, capsys, "--chunk", "T-001")
    assert result == ("T-001 back to regular: new output" if progress else
                      "T-001 no progress: 5 quiet chunks; pace daily")
    output = next(output for output in state.build(config, AT.date())["outputs"] if output["task"] == "T-001")
    assert output["id"] in fingerprint(config, home)["outputs"]
    assert "no progress" in run(config, capsys, "--chunk", "T-001")


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


def test_an_output_resets_the_regular_quiet_count_before_demotion(work, capsys):
    config, home, clock = work
    for _ in range(3):
        run(config, capsys, "--chunk", "T-001")
    clock[0] += timedelta(minutes=30)
    fm.write(config.vault_path / "Comparison.md", {"kind": "draft", "task": "T-001", "created": clock[0].isoformat()}, "Draft")
    assert run(config, capsys, "--chunk", "T-001") == "T-001 progress: new output; quiet reset to 0"
    assert fingerprint(config, home)["quiet"] == 0
    assert tasks.pace(task(config)) == "regular"


@pytest.mark.parametrize("tid", ["T-001", "T-999"])
def test_chunk_rejects_unknown_and_non_handed_off(vault, tmp_path, monkeypatch, capsys, tid):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit) as error:
        nightly.main(["continue", "--chunk", tid, "--vault", str(vault)])
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
    nightly.main(["continue-signal", "--daily", "--vault", str(config.vault_path)])
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
def test_daily_monitor_hygiene_has_its_own_heartbeat(work, identification):
    config, home, clock = work
    (home / "harness").mkdir(parents=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [
        {"skills": [config.skill_prefix + "continue"], "monitorScript": "continue-daily-signal.sh", **identification}]}))
    expected = ["continue-daily's monitor has not run cleanly in a day: check `brain schedule list` and run "
                "`bash $AGENT_HOME/scripts/continue-daily-signal.sh`"]
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == expected
    signals.continue_(config)  # Regular heartbeat does not satisfy the daily job.
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == expected
    signals.continue_(config, daily=True)
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == []
    clock[0] += timedelta(hours=25)
    assert validate.watch_signal_hygiene(Vault(config.vault_path)) == expected


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
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert run(config, capsys, "--chunk", "T-001") == "T-001 progress not recorded: AGENT_HOME is not set"
    assert not (home / "records/continue/progress.json").exists()
