"""The owner's word decides: an agent's finish line is a proposal shown on the task, engagement restarts the pace, a refused save is not
quiet, handoffs without Delivers stay due, a task blocked on another moves when it closes, and an ask knows its conversation."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brain import cli as brain_cli, continuation, journal, needs, state, tasks
from brain.config import Config

GUESS = "I'll call Sample done when a draft shortlist. Say if that's not it."


def exported(config, tid="T-001"):
    return next(row for row in state.build(config, config.today())["tasks"] if row["id"] == tid)


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


def test_a_workers_proposal_is_shown_on_the_task_and_a_conversation_write_is_the_owners(worker, capsys, monkeypatch):
    config, _ = worker
    assert due(config, capsys)[0]["id"] == "T-001"
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    assert task(config).state["delivers_by"] == "agent"
    assert exported(config)["deliversProposed"] is True
    assert asks(config) == []
    assert [row["id"] for row in due(config, capsys)] == ["T-001"]
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings under budget, in the Library")
    assert "delivers_by" not in task(config).state
    assert exported(config)["deliversProposed"] is False


def test_old_proposal_confirm_migrates_only_on_upgrade(worker, capsys, monkeypatch):
    from brain import install
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    t = task(config)
    t.state["delivers_confirm"] = "abc"
    tasks.save_task(config, t, config.today())
    journal.add_ask(config, config.today(), GUESS, config.agent("Tasks#T-001"), kind="confirm")
    assert asks(config) == [GUESS]
    due(config, capsys)
    assert asks(config) == [GUESS]
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    install.migrate_check(config)
    assert asks(config) == []
    assert "delivers_confirm" not in task(config).state
    assert task(config).state["delivers_by"] == "agent"
    monkeypatch.setattr(continuation, "finish_lines", lambda *args: pytest.fail("migration ran twice"))
    install.migrate_check(config)


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


def test_no_delivers_is_due_and_a_blocker_on_a_task_ends_when_it_closes(worker, capsys):
    config, _ = worker
    captured = cli(config, capsys, "continue", "--due", "--json")
    assert json.loads(captured.out)[0]["id"] == "T-001"
    assert captured.err == ""
    assert asks(config) == []
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
                                  "needsYou": "Want me to book the 7pm table for Friday?",
                                  "needsYouWhy": "It is the last table available; yes books it, no leaves Friday open."},
                         "demiurge", at, session="conv123")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "conv456")
    cli(config, capsys, "needs-you", "--ask", "Should I renew the gym membership this month?", "--why", "Your choice decides how this work continues.")
    assert "  - session: demiurge:conv123" in (config.vault_path / "CURRENT.md").read_text()
    items = {item["text"]: item for item in json.loads(cli(config, capsys, "needs-you", "--json").out)}
    assert items["Want me to book the 7pm table for Friday?"]["session"] == "demiurge:conv123"
    assert items["Should I renew the gym membership this month?"]["session"] == "demiurge:conv456"
    exported = {item["text"]: item for item in state.build(config, config.today())["needsYou"]}
    assert exported["Want me to book the 7pm table for Friday?"]["session"] == "demiurge:conv123"
    needs.resolve(config, items["Want me to book the 7pm table for Friday?"]["id"], "booked", config.today())
    current = (config.vault_path / "CURRENT.md").read_text()
    assert "conv123" not in current and "  - session: demiurge:conv456" in current


def test_a_worker_closes_on_its_proposal_and_says_so(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    assert task(config).task_state == "completed"
    assert "Closed on the intellect's own definition of done" in tasks.closing(task(config))[1]
    assert asks(config) == []


def test_a_correction_is_a_conversation_write(worker, capsys, monkeypatch):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-001", "reopen", "--why", "they want three listings, not a draft")
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings under budget in the Library")
    assert task(config).task_state == "ready"
    assert "delivers_by" not in task(config).state
    assert asks(config) == []


@pytest.mark.parametrize("said", ["too short", "these words never came from the owner"])
def test_ungrounded_said_becomes_agent_proposal_even_in_conversation(worker, capsys, monkeypatch, said):
    config, _ = worker
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    out = cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist", "--said", said)
    saved = task(config)
    assert "--said was not found in the owner's words; Delivers is recorded as your proposal" in out.out
    assert tasks.proposed_delivers(saved)
    assert "delivers_said" not in saved.state and "**Said:**" not in saved.body


@pytest.mark.parametrize("grounded", [False, True])
def test_new_task_said_retains_only_grounded_owner_attribution(worker, capsys, monkeypatch, grounded):
    config, _ = worker
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    words = "Please prepare three listings under budget"
    said = "prepare three listings under budget" if grounded else "publish all of the private listings"
    out = cli(config, capsys, "task", "new", "Find listings", "--source", words,
              "--delivers", "three listings", "--said", said, "--json")
    result = json.loads(out.out)
    saved = task(config, result["id"])
    assert tasks.proposed_delivers(saved) is not grounded
    assert "messages" not in result
    if grounded:
        assert saved.state["delivers_said"] == config.today().isoformat()
        assert f'**Said:** "{said}"' in saved.body
    else:
        assert "**Said:**" not in saved.body
        assert out.err == "--said was not found in the owner's words; Delivers is recorded as your proposal\n"
