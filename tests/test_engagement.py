"""A task's budget follows its owner's engagement, and an ask must stand alone."""
from datetime import date, datetime, timedelta, timezone

import pytest

from brain import cli, continuation, needs, tasks
from brain.config import Config

AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


@pytest.fixture
def setup(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    clock = [AT]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "notes in Home"})
    tasks.hand_off(config, "T-001", AT.date(), "Home")
    return config, clock, monkeypatch


def task(config):
    return next(t for t in tasks.load_tasks(config)[0] if t.tid == "T-001")


def test_budget_halves_each_day_without_engagement_and_restores(setup):
    config, clock, _ = setup
    assert continuation.budget_for(config, task(config)) == config.daily_chunks
    clock[0] = AT + timedelta(days=1)
    assert continuation.budget_for(config, task(config)) == config.daily_chunks // 2
    clock[0] = AT + timedelta(days=3)
    assert continuation.budget_for(config, task(config)) == config.daily_chunks // 8
    clock[0] = AT + timedelta(days=30)
    assert continuation.budget_for(config, task(config)) == 1
    continuation.mark_engaged(config, "T-001")
    assert continuation.budget_for(config, task(config)) == config.daily_chunks


def test_a_task_change_in_a_conversation_is_engagement_but_a_workers_is_not(setup, capsys):
    config, clock, monkeypatch = setup
    clock[0] = AT + timedelta(days=2)
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "jn7thread")
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    cli.main(["task", "T-001", "set", "--next", "worker step", "--vault", str(config.vault_path)])
    assert continuation.budget_for(config, task(config)) == config.daily_chunks // 4
    monkeypatch.delenv("DEMIURGE_TASK")
    cli.main(["task", "T-001", "set", "--next", "skip the midterm outline", "--vault", str(config.vault_path)])
    assert continuation.budget_for(config, task(config)) == config.daily_chunks


def test_continue_engaged_and_due_json_carry_the_budget(setup, capsys):
    import json
    config, clock, _ = setup
    clock[0] = AT + timedelta(days=1)
    cli.main(["continue", "--due", "--json", "--vault", str(config.vault_path)])
    row = json.loads(capsys.readouterr().out)[0]
    assert row["budget"] == config.daily_chunks // 2 and row["budgetLeft"] == config.daily_chunks // 2
    cli.main(["continue", "--engaged", "T-001", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out.strip() == "T-001 engaged"
    cli.main(["continue", "--due", "--json", "--vault", str(config.vault_path)])
    assert json.loads(capsys.readouterr().out)[0]["budget"] == config.daily_chunks


@pytest.mark.parametrize("text", ["Do you approve the proposed action?", "Approve the plan?", "Should I proceed?",
                                  "Want me to go ahead with it?", "OK?"])
def test_generic_asks_are_refused(text):
    assert needs.vague_ask(text)


@pytest.mark.parametrize("text", ["Want me to publish the 86 verified offers to In The Block now?",
                                  "For the apartment search, want more listings, or tighter commute times?",
                                  "Which of your two Gmails should I read first?"])
def test_asks_that_name_their_subject_pass(text):
    assert needs.vague_ask(text) is None


def test_the_cli_refuses_a_vague_ask(setup, capsys):
    config, _, _ = setup
    with pytest.raises(SystemExit):
        cli.main(["needs-you", "--ask", "Do you approve the proposed action?", "--vault", str(config.vault_path)])
    assert "ask refused" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["task", "T-001", "set", "--ask", "Approve the plan?", "--vault", str(config.vault_path)])
