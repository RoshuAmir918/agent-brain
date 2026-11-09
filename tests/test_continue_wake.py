"""Computer rests wait for a runtime wake, without counting as owner engagement."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brain import cli as brain_cli, continuation, fm, state, tasks, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot


def waiting(data):
    """The harness's view of tasks resting on the owner's computer, derived from the task rows."""
    return [row["id"] for row in data["tasks"] if row["open"] and row["resume"] == "computer"]


@pytest.fixture
def resting(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    clock = [datetime(2026, 11, 6, 12, tzinfo=ZoneInfo("America/Los_Angeles"))]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", config.today(), "demiurge:owner")
    tasks.set_fields(config, "T-001", config.today(), state="blocked",
                     values={"delivers": "deck on the laptop", "blocker": "Deck ready in the Library; laptop offline",
                             "resume": "computer"})
    return config, clock


def cli(config, capsys, *args):
    brain_cli.main(["continue", *args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


def current(config):
    registered, archived = tasks.load_tasks(config)
    return next(t for t in registered + archived if t.tid == "T-001")


@pytest.mark.parametrize("task_state", ["blocked", "in_progress", "ready"])
def test_computer_rest_is_never_due_until_woken(resting, capsys, task_state):
    config, clock = resting
    tasks.set_fields(config, "T-001", config.today(), state=task_state)
    before = current(config).body
    clock[0] += timedelta(days=100)
    assert tasks.due([current(config)]) == []
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--due")
    assert error.value.code == 1
    capsys.readouterr()
    assert current(config).task_state == task_state
    assert cli(config, capsys, "--wake", "computer") == "T-001 woken: Sample"
    assert current(config).resume is None
    assert current(config).task_state == ("in_progress" if task_state == "blocked" else task_state)
    assert current(config).body == tasks._replace_field(before, "resume", None)
    assert [row["id"] for row in json.loads(cli(config, capsys, "--due", "--json"))] == ["T-001"]
    assert "T-001" in cli(config, capsys, "--due")


@pytest.mark.parametrize("location", ["register", "warm", "archive"])
def test_wake_updates_full_entry_and_warm_state(resting, capsys, location):
    config, _ = resting
    task = current(config)
    if location != "register":
        tasks.write_archive(config, task, "warm" if location == "warm" else "cold", config.today(), whole=True)
        path = config.vault_path / config.agent("Tasks.md")
        data, _ = fm.read(path)
        fm.write(path, data, "# Tasks\n\n" + (task.render_line(config) if location == "warm" else ""))
    assert validate.task_states(Vault(config.vault_path)) == []
    assert waiting(state.build(config, config.today())) == ["T-001"]
    assert json.loads(cli(config, capsys, "--wake", "computer", "--json")) == ["T-001"]
    assert current(config).task_state == "in_progress" and current(config).resume is None
    if location == "warm":
        assert "state=in_progress" in path.read_text()
    if location != "register":
        body = tasks.archive_path(config, "T-001").read_text()
        assert "Resume:" not in body and "state=in_progress" in body
    assert waiting(state.build(config, config.today())) == []


@pytest.mark.parametrize("as_json", [False, True])
def test_wake_output_empty_exit_and_ignores_dates_and_closed_tasks(resting, capsys, as_json):
    config, _ = resting
    for tid, resume, task_state in [("T-002", "computer", "ready"), ("T-003", "2026-11-06", "blocked"),
                                     ("T-004", "2099-11-06 08:00", "blocked"),
                                     ("T-005", "computer", "completed"), ("T-006", "computer", "cancelled")]:
        tasks.append_task(config, tasks.Task(tid, "Other", f"- **Resume:** {resume}\n", {"state": task_state}))
    expected = {task.tid: task.render() for task in tasks.load_tasks(config)[0] if task.tid >= "T-003"}
    options = ["--json"] if as_json else []
    out = cli(config, capsys, "--wake", "computer", *options)
    assert (json.loads(out) if as_json else out.splitlines()) == (
        ["T-001", "T-002"] if as_json else ["T-001 woken: Sample", "T-002 woken: Other"])
    assert {task.tid: task.render() for task in tasks.load_tasks(config)[0] if task.tid >= "T-003"} == expected
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--wake", "computer", *options)
    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out.strip() == ("[]" if as_json else "") and output.err == ""
    assert snapshot(config.vault_path) == before


@pytest.mark.parametrize("task_state", ["waiting_for_owner", "yours", "set_aside"])
def test_wake_keeps_other_resting_states(resting, capsys, task_state):
    config, _ = resting
    tasks.set_fields(config, "T-001", config.today(), state=task_state,
                     values={"ask": "Should the deck go on your laptop?"})
    cli(config, capsys, "--wake", "computer")
    assert current(config).resume is None and current(config).task_state == task_state
    assert json.loads(cli(config, capsys, "--due", "--json")) == []


def test_wake_holds_lock_and_does_not_record_engagement_or_reset_progress(resting, capsys, monkeypatch):
    config, _ = resting
    cli(config, capsys, "--engaged", "T-001", "--instruction", "Put the deck on my laptop")
    cli(config, capsys, "--chunk", "T-001")
    before = snapshot(continuation.runtime.home())
    held = False
    original = tasks.save_task

    @contextmanager
    def locked(cfg):
        nonlocal held
        assert cfg == config and not held
        held = True
        try:
            yield
        finally:
            held = False

    def guarded(*args, **kwargs):
        assert held
        return original(*args, **kwargs)

    monkeypatch.setattr(continuation, "vault_lock", locked)
    monkeypatch.setattr(tasks, "save_task", guarded)
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "owner")
    cli(config, capsys, "--wake", "computer")
    assert snapshot(continuation.runtime.home()) == before


@pytest.mark.parametrize("options", [["--wake", "network"], ["--wake", "computer", "--chunk", "T-001"]])
def test_invalid_wake_arguments_do_not_write(resting, capsys, options):
    config, _ = resting
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, *options)
    assert error.value.code == 2 and capsys.readouterr().err
    assert snapshot(config.vault_path) == before


def test_state_json_preserves_computer_and_lists_only_open_waits(resting, capsys):
    config, _ = resting
    for tid, resume, task_state in [("T-002", "computer", "ready"), ("T-003", "2026-11-07", "blocked"),
                                     ("T-004", "computer", "completed")]:
        tasks.append_task(config, tasks.Task(tid, "Other", f"- **Resume:** {resume}\n", {"state": task_state}))
    brain_cli.main(["state", "--json", "--vault", str(config.vault_path)])
    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == 3
    assert waiting(data) == ["T-001", "T-002"]
    assert [row["resume"] for row in data["tasks"]] == ["computer", "computer", "2026-11-07", "computer"]
