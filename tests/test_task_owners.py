"""In an organisation's intellect a task is the person's who handed it off: the handoff line names them, its asks are
for them, and only their engagement keeps its budget whole. A personal intellect's tasks are all its owner's."""
import json

import pytest

from brain import cli, continuation, needs, state, tasks
from brain.config import Config


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))


def enterprise(vault):
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\nedition = "enterprise"\n')
    return Config.load(vault)


def run(vault, capsys, *args):
    cli.main([*args, "--vault", str(vault)])
    return capsys.readouterr().out.strip()


def find(config, tid):
    registered, archived = tasks.load_tasks(config)
    return next(t for t in registered + archived if t.tid == tid)


def test_an_organisations_handoff_is_for_the_speaker_and_a_personal_one_names_nobody(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_SPEAKER", "slack:U0KIM")
    run(vault, capsys, "handoff", "T-001", "--report", "Home")
    assert find(Config.load(vault), "T-001").owner is None
    config = enterprise(vault)
    run(vault, capsys, "handoff", "T-001", "--report", "Home")
    task = find(config, "T-001")
    assert task.owner == "slack:U0KIM" and "· for slack:U0KIM · report in Home" in task.body and task.handoff[1] == "Home"


def test_an_owner_is_kept_through_a_rehandoff_without_a_speaker_and_a_report_change(vault, capsys, monkeypatch):
    config = enterprise(vault)
    run(vault, capsys, "handoff", "T-001", "--for", "slack:U0KIM")
    run(vault, capsys, "handoff", "T-001")
    run(vault, capsys, "task", "T-001", "set", "--report", "demiurge:abc")
    task = find(config, "T-001")
    assert task.owner == "slack:U0KIM" and task.handoff[1] == "demiurge:abc"
    with pytest.raises(SystemExit):
        run(vault, capsys, "handoff", "T-001", "--for", "Kim")
    assert "<platform>:<id>" in capsys.readouterr().err


def test_a_tasks_asks_are_for_its_owner(vault, capsys):
    config = enterprise(vault)
    run(vault, capsys, "handoff", "T-001", "--for", "slack:U0KIM")
    run(vault, capsys, "task", "T-001", "set", "--state", "waiting_for_owner", "--ask", "Use the staging database?", "--why", "Prod is read-only.")
    mine = json.loads(run(vault, capsys, "needs-you", "--json", "--for", "slack:U0KIM"))
    theirs = json.loads(run(vault, capsys, "needs-you", "--json", "--for", "slack:U0LEE"))
    assert [item["text"] for item in mine] == ["Use the staging database?"] and mine[0]["owner"] == "slack:U0KIM"
    assert theirs == []
    exported = state.build(config, config.today())
    assert any(item["owner"] == "slack:U0KIM" for item in exported["needsYou"])
    assert next(t for t in exported["tasks"] if t["id"] == "T-001")["owner"] == "slack:U0KIM"


def test_only_the_owners_engagement_keeps_the_budget_whole(vault, capsys, monkeypatch):
    config = enterprise(vault)
    run(vault, capsys, "handoff", "T-001", "--for", "slack:U0KIM")
    before = dict(continuation.engaged_at(config))
    monkeypatch.setenv("BRAIN_SPEAKER", "slack:U0LEE")
    assert "not engaged: slack:U0LEE is not its owner" in run(vault, capsys, "continue", "--engaged", "T-001")
    assert continuation.engaged_at(config) == before
    assert run(vault, capsys, "continue", "--engaged", "T-001", "--by", "slack:U0KIM") == "T-001 engaged"
