"""Worker-facing continue state and compatibility with the legacy daily list."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from brain import continuation, fm, nightly, signals, tasks
from brain.config import Config


@pytest.fixture
def worker(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    clock = [datetime(2026, 11, 5, 0, 30, tzinfo=ZoneInfo("America/Los_Angeles"))]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "finished notes in Home"})
    tasks.hand_off(config, "T-001", config.today(), "demiurge:conversation", work="result")
    return config, home, clock


def cli(config, capsys, *args):
    nightly.main(["continue", *args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


def row(config, home):
    return json.loads((home / "records/continue/progress.json").read_text())[str(config.vault_path.resolve())]["T-001"]


def save_work(home, at):
    path = home / "records/works/state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"result": {"lastPush": at.isoformat()}}))


def test_checkpoint_atomic_per_task_and_visible_in_due(worker, capsys):
    config, home, clock = worker
    old = home / "records/continue/state.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"legacy": "leave me alone"}\n')
    assert cli(config, capsys, "--checkpoint", "T-001", "--text", "Read chapter 2; resume at chapter 3") == "T-001 checkpoint saved"
    path = home / "records/continue/tasks/T-001.json"
    assert json.loads(path.read_text()) == {"task": "T-001", "at": clock[0].isoformat(), "text": "Read chapter 2; resume at chapter 3"}
    assert old.read_text() == '{"legacy": "leave me alone"}\n'
    second = tasks.new_task(config, "Second", "words")
    cli(config, capsys, "--checkpoint", second.tid, "--text", "second checkpoint")
    clock[0] += timedelta(minutes=25)
    cli(config, capsys, "--checkpoint", "T-001", "--text", "New stopping point")
    assert continuation.checkpoint_text(second.tid) == "second checkpoint"
    assert "checkpoint: New stopping point" in cli(config, capsys, "--due")
    assert json.loads(cli(config, capsys, "--due", "--json"))[0]["checkpoint"] == "New stopping point"
    assert not list(path.parent.glob("*.tmp"))
    assert not (home / "records/continue/progress.json").exists()


def test_checkpoint_failed_replace_keeps_previous_and_cleans_temp(worker, monkeypatch):
    config, home, _ = worker
    continuation.checkpoint(config, "T-001", "previous")
    original = Path.replace

    def fail(self, target):
        if Path(target).name == "T-001.json":
            raise OSError("replace failed")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        continuation.checkpoint(config, "T-001", "new")
    assert continuation.checkpoint_text("T-001") == "previous"
    assert not list((home / "records/continue/tasks").glob("*.tmp"))


@pytest.mark.parametrize("args", [("--checkpoint", "T-001"), ("--text", "orphan"),
                                  ("--checkpoint", "T-001", "--text", "x", "--json"),
                                  ("--checkpoint", "T-999", "--text", "x"),
                                  ("--checkpoint", "../../oops", "--text", "x")])
def test_checkpoint_rejects_invalid_command(worker, capsys, args):
    config, _, _ = worker
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, *args)
    assert error.value.code == 2
    assert capsys.readouterr().err


def test_checkpoint_requires_runtime(worker, monkeypatch, capsys):
    config, _, _ = worker
    monkeypatch.delenv("AGENT_HOME")
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--checkpoint", "T-001", "--text", "x")
    assert error.value.code == 2 and "AGENT_HOME" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [None, "invalid", "[]", "null", '{"task":"T-999","text":"wrong task"}', '{"task":"T-001","text":3}'])
def test_missing_or_bad_checkpoint_is_null(worker, capsys, raw):
    config, home, _ = worker
    if raw is not None:
        path = home / "records/continue/tasks/T-001.json"
        path.parent.mkdir(parents=True)
        path.write_text(raw)
    assert json.loads(cli(config, capsys, "--due", "--json"))[0]["checkpoint"] is None


def test_due_json_shape_order_and_daily(worker, capsys):
    config, home, clock = worker
    tasks.set_fields(config, "T-001", config.today(), values={"next": "deliver the notes", "delivers": "notes in Home"})
    first = clock[0].isoformat()
    cli(config, capsys, "--chunk", "T-001")
    two = tasks.new_task(config, "Second", "owner words", values={"delivers": "a result"})
    tasks.hand_off(config, two.tid, config.today(), "Home")
    clock[0] += timedelta(minutes=30)
    cli(config, capsys, "--chunk", two.tid)
    three = tasks.new_task(config, "Third", "START", values={"delivers": "a result"})
    tasks.hand_off(config, three.tid, config.today())
    listed = cli(config, capsys, "--due").splitlines()
    output = json.loads(cli(config, capsys, "--due", "--json"))
    assert [item["id"] for item in output] == [line.split(" · ")[0] for line in listed] == [three.tid, "T-001", two.tid]
    assert output[1] == {"id": "T-001", "title": "Sample", "state": "in_progress", "pace": "regular",
                         "handedOff": "2026-11-05", "report": "demiurge:conversation", "work": "result",
                         "delivers": "notes in Home", "next": "deliver the notes", "lastChunk": first,
                         "checkpoint": None, "chunksToday": 1, "budget": 16, "budgetLeft": 15,
                         "reviewDue": False, "chunksSinceReview": 1, "lastReview": None}
    assert output[0]["lastChunk"] is None and output[0]["next"] is None and output[0]["work"] is None
    assert output[0]["chunksToday"] == 0 and output[0]["budgetLeft"] == 16
    tasks.set_pace(config, "T-001", "daily", config.today())
    daily = json.loads(cli(config, capsys, "--due", "--daily", "--json"))
    assert [item["id"] for item in daily] == ["T-001"]
    assert daily[0]["pace"] == "daily" and daily[0]["budgetLeft"] is None


def test_due_empty_json_exits_zero_text_still_one(vault, capsys):
    config = Config.load(vault)
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    assert json.loads(cli(config, capsys, "--due", "--daily", "--json")) == []
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--due")
    assert error.value.code == 1 and "nothing handed off is due" in capsys.readouterr().out


def test_combined_due_daily_once_per_owner_day(worker, capsys):
    config, home, clock = worker
    tasks.set_pace(config, "T-001", "daily", config.today())
    # Noon in UTC is still early morning for this owner.
    clock[0] = datetime(2026, 11, 5, 19, 59, tzinfo=timezone.utc)
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    assert signals.continue_(config) == "date: none"
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--due")
    assert error.value.code == 1
    capsys.readouterr()
    assert "T-001" in cli(config, capsys, "--due", "--daily")
    assert json.loads(cli(config, capsys, "--due", "--daily", "--json"))[0]["id"] == "T-001"

    clock[0] += timedelta(minutes=1)
    for _ in range(2):
        daily = json.loads(cli(config, capsys, "--due", "--json"))
        assert len(daily) == 1
        assert (daily[0]["id"], daily[0]["pace"], daily[0]["chunksToday"]) == ("T-001", "daily", 0)
        assert daily[0]["budget"] is None and daily[0]["budgetLeft"] is None
        assert "T-001" in cli(config, capsys, "--due")
        assert "T-001" in signals.continue_(config)
    cli(config, capsys, "--chunk", "T-001")  # The baseline counts too.
    for at in (clock[0], datetime(2026, 11, 6, 7, 59, tzinfo=timezone.utc)):
        clock[0] = at
        assert json.loads(cli(config, capsys, "--due", "--json")) == []
        assert signals.continue_(config) == "date: none"
    # The legacy daily selection still includes a task already counted today.
    assert json.loads(cli(config, capsys, "--due", "--daily", "--json"))[0]["chunksToday"] == 1
    clock[0] = datetime(2026, 11, 6, 19, 59, tzinfo=timezone.utc)
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    clock[0] += timedelta(minutes=1)
    daily = json.loads(cli(config, capsys, "--due", "--json"))
    assert [item["id"] for item in daily] == ["T-001"]
    assert daily[0]["chunksToday"] == 0


def test_combined_due_orders_both_paces_by_last_chunk(worker, capsys):
    config, home, clock = worker
    clock[0] -= timedelta(days=1)
    cli(config, capsys, "--chunk", "T-001")
    tasks.set_pace(config, "T-001", "daily", config.today())
    clock[0] += timedelta(days=1, hours=12)
    two = tasks.new_task(config, "Second", "owner words", values={"delivers": "a result"})
    tasks.hand_off(config, two.tid, config.today())
    cli(config, capsys, "--chunk", two.tid)
    three = tasks.new_task(config, "Third", "owner words", values={"delivers": "a result"})
    tasks.hand_off(config, three.tid, config.today())
    listed = cli(config, capsys, "--due").splitlines()
    output = json.loads(cli(config, capsys, "--due", "--json"))
    assert [item["id"] for item in output] == [line.split(" · ")[0] for line in listed] == [three.tid, "T-001", two.tid]
    assert [item["pace"] for item in output] == ["regular", "daily", "regular"]
    assert {line.split(" · ")[0] for line in signals.continue_(config).splitlines()[1:]} == {three.tid, "T-001", two.tid}


@pytest.mark.parametrize("gate", ["completed", "cancelled", "waiting_for_owner", "yours", "set_aside",
                                 "blocked", "computer", "resume", "delivers", "handoff", "cap"])
def test_combined_daily_keeps_existing_gates(worker, capsys, monkeypatch, gate):
    config, home, clock = worker
    clock[0] = clock[0].replace(hour=12)
    tasks.set_pace(config, "T-001", "daily", config.today())
    if gate in ("completed", "cancelled"):
        tasks.close(config, "T-001", config.today(), gate == "completed", "The task is settled")
    elif gate in ("waiting_for_owner", "yours", "set_aside", "blocked"):
        tasks.set_fields(config, "T-001", config.today(), state=gate, values={"ask": "Which notes should I finish?"})
    elif gate in ("computer", "resume"):
        resume = "computer" if gate == "computer" else config.today().isoformat() + " 14:00"
        tasks.set_fields(config, "T-001", config.today(), values={"resume": resume})
    elif gate in ("delivers", "handoff"):
        tasks.set_fields(config, "T-001", config.today(), clear=[gate])
    else:
        monkeypatch.setenv("BRAIN_DAILY_CHUNKS", "1")
        cli(config, capsys, "--review", "T-001", "--verdict", "circling", "--reason", "No visible result",
            "--ask", "Which notes should I finish?")
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    assert signals.continue_(config) == "date: none"
    if gate == "resume":
        clock[0] = clock[0].replace(hour=14)
        assert json.loads(cli(config, capsys, "--due", "--json"))[0]["id"] == "T-001"


def test_sixteenth_chunk_budget_waits_until_next_local_day(worker, capsys):
    config, home, clock = worker
    for n in range(1, 17):
        save_work(home, clock[0] - timedelta(minutes=1))
        message = cli(config, capsys, "--chunk", "T-001")
        assert row(config, home)["chunks"] == {"date": "2026-11-05", "count": n}
        if n < 16:
            assert "budget" not in message
            assert json.loads(cli(config, capsys, "--due", "--json"))[0]["budgetLeft"] == 16 - n
        clock[0] += timedelta(minutes=30)
    assert message == "T-001 reached today's budget of 16 chunks; it waits until tomorrow"
    assert continuation.DAILY_CHUNKS == 16
    assert tasks.pace(tasks.load_tasks(config)[0][0]) == "regular"
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, "--due")
    assert error.value.code == 1
    capsys.readouterr()
    assert signals.continue_(config) == "date: none"
    # A bare due() keeps its historical behavior; callers supply the budget counts.
    registered, archived = tasks.load_tasks(config)
    assert len(tasks.due(registered + archived, worked=continuation.last_worked(config))) == 1
    assert tasks.due(registered + archived, worked=continuation.last_worked(config),
                     chunks=continuation.chunks_today(config), daily_chunks=config.daily_chunks) == []
    # UTC has already rolled over, but the owner's local day has not.
    clock[0] = datetime(2026, 11, 6, 7, 59, tzinfo=timezone.utc)
    assert json.loads(cli(config, capsys, "--due", "--json")) == []
    clock[0] += timedelta(minutes=2)
    result = json.loads(cli(config, capsys, "--due", "--json"))[0]
    # A new local day with no owner engagement since the handoff: the budget halves (whole again on engagement).
    assert result["chunksToday"] == 0 and result["budget"] == 8 and result["budgetLeft"] == 8
    assert "T-001" in signals.continue_(config)
    cli(config, capsys, "--chunk", "T-001")
    assert row(config, home)["chunks"] == {"date": "2026-11-06", "count": 1}


def test_daily_task_unaffected_and_budget_message_only_on_crossing(worker, capsys, monkeypatch):
    config, home, clock = worker
    monkeypatch.setenv("BRAIN_DAILY_CHUNKS", "1")
    config = Config.load(config.vault_path)
    assert cli(config, capsys, "--chunk", "T-001") == "T-001 reached today's budget of 1 chunks; it waits until tomorrow"
    assert "budget" not in cli(config, capsys, "--chunk", "T-001")
    tasks.set_pace(config, "T-001", "daily", config.today())
    assert "budget" not in cli(config, capsys, "--chunk", "T-001")
    daily = json.loads(cli(config, capsys, "--due", "--daily", "--json"))
    assert daily[0]["id"] == "T-001" and daily[0]["chunksToday"] == 3
    assert daily[0]["budgetLeft"] is None


def test_toml_budget_drives_chunk_and_due(worker, capsys):
    config, home, _ = worker
    with (config.repo / "brain.toml").open("a") as stream:
        stream.write("\n[continue]\ndaily_chunks = 2\n")
    cli(config, capsys, "--chunk", "T-001")
    assert json.loads(cli(config, capsys, "--due", "--json"))[0]["budgetLeft"] == 1
    assert cli(config, capsys, "--chunk", "T-001") == "T-001 reached today's budget of 2 chunks; it waits until tomorrow"
    assert json.loads(cli(config, capsys, "--due", "--json")) == []


def test_legacy_progress_without_counts_keeps_order_and_starts_at_one(worker, capsys):
    config, home, clock = worker
    path = home / "records/continue/progress.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({str(config.vault_path.resolve()): {"T-001": {
        "state": "in_progress", "at": clock[0].isoformat(), "quiet": 0, "pace": "regular"}},
        "/another/vault": {"T-777": {"at": "preserved"}}}))
    assert continuation.chunks_today(config) == {"T-001": 0}
    assert continuation.last_worked(config) == {"T-001": clock[0].isoformat()}
    cli(config, capsys, "--chunk", "T-001")
    assert row(config, home)["chunks"]["count"] == 1
    assert json.loads(path.read_text())["/another/vault"] == {"T-777": {"at": "preserved"}}
