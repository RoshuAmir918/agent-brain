"""The owner's word decides: an agent's finish line is a proposal, engagement restarts the pace, a refused save is not
quiet, skipped handoffs are named, a task blocked on another moves when it closes, and an ask knows its conversation."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brain import cli as brain_cli, continuation, journal, needs, state, tasks
from brain.config import Config

GUESS = "What would done look like for Sample? Until you say, Agent works toward its own guess: a draft shortlist"


@pytest.fixture
def worker(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DEMIURGE_CONVERSATION", raising=False)
    monkeypatch.delenv("DEMIURGE_TASK", raising=False)
    clock = [datetime(2026, 11, 6, 12, tzinfo=ZoneInfo("America/Los_Angeles"))]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", config.today(), "demiurge:owner", work="shortlist")
    return config, clock


def cli(config, capsys, *args):
    brain_cli.main([*args, "--vault", str(config.vault_path)])
    return capsys.readouterr()


def due(config, capsys):
    return json.loads(cli(config, capsys, "continue", "--due", "--json").out)


def task(config, tid="T-001"):
    return tasks.find(config, tid, archived_closed=True)


def asks(config):
    return [item.text for item in needs.items(config, config.today())]


def test_a_delivers_the_worker_wrote_leaves_the_owners_question_open(worker, capsys, monkeypatch):
    config, _ = worker
    assert due(config, capsys) == []
    assert asks(config) == ["What would done look like for Sample? Until then Agent won't keep working on it in the background."]
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    assert task(config).state["delivers_by"] == "agent"
    assert [row["id"] for row in due(config, capsys)] == ["T-001"]   # worked toward the proposal meanwhile
    assert len(asks(config)) == 1 and task(config).state["finish_asked"] == "pending"
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings under budget, in the Library")
    assert "delivers_by" not in task(config).state
    due(config, capsys)
    assert asks(config) == [] and task(config).state["finish_asked"] == "answered"


def test_a_proposed_delivers_is_asked_about_once_and_settles_when_the_owner_answers(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    due(config, capsys)
    due(config, capsys)
    assert asks(config) == [GUESS]
    item = needs.items(config, config.today())[0]
    needs.resolve(config, item.id, "they said three listings", config.today())
    due(config, capsys)
    assert asks(config) == [] and task(config).state["finish_asked"] == "answered"


def test_a_refused_save_is_not_a_quiet_chunk(worker, capsys):
    config, clock = worker
    cli(config, capsys, "continue", "--chunk", "T-001")
    folder = continuation.runtime.home() / "records/works"
    folder.mkdir(parents=True)
    clock[0] += timedelta(minutes=30)
    refusal = {"file": "notes.md", "kind": "an AWS access key", "when": clock[0].isoformat()}
    (folder / "state.json").write_text(json.dumps({"shortlist": {"refusal": refusal}}))
    clock[0] += timedelta(minutes=30)
    out = cli(config, capsys, "continue", "--chunk", "T-001").out.strip()
    assert out == "T-001 work not saved: refused (notes.md contains an AWS access key); remove it and save again"
    assert continuation._progress(config)["T-001"]["quiet"] == 0
    clock[0] += timedelta(minutes=30)   # the same refusal, already reported, is quiet again
    assert cli(config, capsys, "continue", "--chunk", "T-001").out.strip() == "T-001 no progress: 1 quiet chunks; pace regular"


def test_owner_engagement_restores_the_regular_pace_and_forgets_quiet_chunks(worker, capsys):
    config, clock = worker
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "a shortlist"})
    for _ in range(4):
        clock[0] += timedelta(minutes=30)
        cli(config, capsys, "continue", "--chunk", "T-001")
    assert tasks.pace(task(config)) == "daily" and continuation._progress(config)["T-001"]["quiet"] == 3
    assert cli(config, capsys, "continue", "--engaged", "T-001").out.strip() == "T-001 engaged"
    assert tasks.pace(task(config)) == "regular" and continuation._progress(config)["T-001"]["quiet"] == 0


def test_skipped_handoffs_are_named_and_a_blocker_on_a_task_ends_when_it_closes(worker, capsys):
    config, _ = worker
    captured = cli(config, capsys, "continue", "--due", "--json")
    assert json.loads(captured.out) == []
    assert "T-001 skipped: handed off with no Delivers line" in captured.err
    other = tasks.new_task(config, "Get the landlord's answer", '"ask the landlord"')
    tasks.set_fields(config, "T-001", config.today(), state="blocked",
                     values={"delivers": "a shortlist", "blocker": f"waits on {other.tid}"})
    assert due(config, capsys) == []
    tasks.close(config, other.tid, config.today(), True, "the landlord said yes")
    assert task(config).task_state == "in_progress"
    assert [row["id"] for row in due(config, capsys)] == ["T-001"]


def test_an_ask_raised_in_a_conversation_names_it(worker, capsys, monkeypatch):
    config, _ = worker
    at = datetime(2026, 11, 6, 12, 30)
    journal.append_entry(config, {"outcome": "done", "headline": "Planned dinner.",
                                  "needsYou": "Want me to book the 7pm table for Friday?"},
                         "demiurge", at, session="conv123")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "conv456")
    cli(config, capsys, "needs-you", "--ask", "Should I renew the gym membership this month?")
    assert "  - session: demiurge:conv123" in (config.vault_path / "CURRENT.md").read_text()
    items = {item["text"]: item for item in json.loads(cli(config, capsys, "needs-you", "--json").out)}
    assert items["Want me to book the 7pm table for Friday?"]["session"] == "demiurge:conv123"
    assert items["Should I renew the gym membership this month?"]["session"] == "demiurge:conv456"
    exported = {item["text"]: item for item in state.build(config, config.today())["needsYou"]}
    assert exported["Want me to book the 7pm table for Friday?"]["session"] == "demiurge:conv123"
    needs.resolve(config, items["Want me to book the 7pm table for Friday?"]["id"], "booked", config.today())
    current = (config.vault_path / "CURRENT.md").read_text()
    assert "conv123" not in current and "  - session: demiurge:conv456" in current


def test_a_worker_cannot_close_a_task_on_its_own_finish_line(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    assert "is not done until Human says so" in capsys.readouterr().err
    assert task(config).task_state == "in_progress"
    assert asks(config) == [GUESS] and task(config).state["finish_asked"] == "pending"   # asked at once, not next pass
    cli(config, capsys, "task", "T-001", "set", "--state", "waiting_for_owner",
        "--ask", "Twenty names are in your Library; is that done?", "--why", "Say yes and I close it.")
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    assert "What would done look like for Sample?" in (config.vault_path / "CURRENT.md").read_text()


def test_the_owners_answer_lifts_the_hold(worker, capsys, monkeypatch):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    due(config, capsys)
    cli(config, capsys, "task", "T-001", "set", "--state", "waiting_for_owner",
        "--ask", "Twenty names are in your Library; is that done?", "--why", "Say yes and I close it.")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-001", "set", "--state", "in_progress")   # they answered it in conversation
    monkeypatch.delenv("DEMIURGE_CONVERSATION")
    assert asks(config) == []
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    assert task(config).task_state == "completed"


def test_the_owner_and_their_conversation_still_close_a_proposed_finish_line(worker, capsys, monkeypatch):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    tasks.new_task(config, "Second", '"another"', values={"delivers": "a list"}, agent_delivers=True)
    tasks.hand_off(config, "T-002", config.today(), "demiurge:owner")
    cli(config, capsys, "task", "T-001", "done", "--by-owner")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-002", "done", "--why", "they said the list is enough")
    assert task(config).task_state == task(config, "T-002").task_state == "completed"
