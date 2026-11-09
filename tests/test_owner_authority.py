"""The owner's word decides: an agent's finish line is a proposal, engagement restarts the pace, a refused save is not
quiet, handoffs without Delivers stay due, a task blocked on another moves when it closes, and an ask knows its conversation."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brain import cli as brain_cli, continuation, journal, needs, state, tasks
from brain.config import Config

GUESS = "I'll call Sample done when a draft shortlist. Say if that's not it."


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


def test_a_workers_proposal_is_a_confirm_and_a_conversation_write_is_the_owners(worker, capsys, monkeypatch):
    config, _ = worker
    assert due(config, capsys)[0]["id"] == "T-001"
    assert asks(config) == []
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    assert task(config).state["delivers_by"] == "agent"
    assert needs.items(config, config.today())[0].kind == "confirm"
    assert [row["id"] for row in due(config, capsys)] == ["T-001"]
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings under budget, in the Library")
    assert "delivers_by" not in task(config).state
    due(config, capsys)
    assert asks(config) == []


def test_a_proposal_is_confirmed_once_replaced_when_changed_and_accepted_after_five_days(worker, capsys):
    config, clock = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    due(config, capsys)
    due(config, capsys)
    assert asks(config) == [GUESS]
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings")
    assert asks(config) == ["I'll call Sample done when three listings. Say if that's not it."]
    clock[0] += timedelta(days=5)
    assert needs.expire_confirms(config, config.today()) == ["I'll call Sample done when three listings. Say if that's not it."]
    due(config, capsys)
    assert asks(config) == []


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


def test_a_worker_closes_on_its_proposal_and_leaves_the_confirm_open(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    assert task(config).task_state == "completed"
    assert "Closed on the intellect's own definition of done" in tasks.closing(task(config))[1]
    assert asks(config) == [GUESS]
    due(config, capsys)
    assert asks(config) == [GUESS]


def test_resolving_a_confirm_accepts_it_and_a_correction_is_a_conversation_write(worker, capsys, monkeypatch):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    item = needs.items(config, config.today())[0]
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "needs-you", "--resolve", item.id, "--because", "Kevin said that's fine")
    assert task(config).delivers == "a draft shortlist"
    assert "delivers_by" not in task(config).state
    assert task(config).task_state == "completed"
    assert continuation.engaged_at(config)["T-001"]
    cli(config, capsys, "task", "T-001", "reopen", "--why", "they want three listings, not a draft")
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three listings under budget in the Library")
    assert task(config).task_state == "ready"
    assert "delivers_by" not in task(config).state
    assert asks(config) == []


def test_the_owner_and_their_conversation_still_close_a_proposed_finish_line(worker, capsys, monkeypatch):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    tasks.new_task(config, "Second", '"another"', values={"delivers": "a list"}, agent_delivers=True)
    tasks.hand_off(config, "T-002", config.today(), "demiurge:owner")
    cli(config, capsys, "task", "T-001", "done", "--by-owner")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "c1")
    cli(config, capsys, "task", "T-002", "done", "--why", "they said the list is enough")
    assert task(config).task_state == task(config, "T-002").task_state == "completed"


def test_grounded_said_from_source_can_create_and_update_a_task(worker, capsys):
    config, _ = worker
    words = "Publish three listings under budget in the Library"
    created = json.loads(cli(config, capsys, "task", "new", "Apartments", "--source", words,
                             "--delivers", "three listings in the Library", "--said", words, "--json").out)
    saved = task(config, created["id"])
    assert "delivers_by" not in saved.state
    assert saved.state["delivers_said"] == config.today().isoformat()
    assert f'- **Said:** "{words}"' in saved.body
    cli(config, capsys, "task", saved.tid, "set", "--delivers", "three listings under budget", "--said", "three listings under budget")
    assert "delivers_by" not in task(config, saved.tid).state
    assert asks(config) == []


@pytest.mark.parametrize("exported", [False, True])
def test_grounded_said_uses_recent_owner_session_turns(worker, capsys, exported):
    from conftest import harness_session, message, write_shards
    config, clock = worker
    record = harness_session("request", [message("user", "wrapped text", clock[0].isoformat(),
                             said="Publish three listings — under budget, in the Library.")])
    home = continuation.runtime.home()
    if exported:
        write_shards(home / "records/sessions", record)
    else:
        folder = home / "harness/sessions" / config.today().isoformat()
        folder.mkdir(parents=True)
        (folder / "request.json").write_text(json.dumps(record))
    words = "THREE listings -  under budget, in the Library."
    cli(config, capsys, "task", "T-001", "set", "--delivers", "three affordable listings", "--said", words)
    assert "delivers_by" not in task(config).state
    assert task(config).state["delivers_said"] == config.today().isoformat()
    assert asks(config) == []


@pytest.mark.parametrize("kind", ["never", "assistant", "stranger", "old", "routine", "evaluation"])
def test_said_cannot_use_invented_agent_stranger_old_or_routine_words(worker, capsys, kind):
    from conftest import harness_session, message, write_shards
    config, clock = worker
    words = "three listings under budget in the Library"
    at = clock[0] - timedelta(days=31 if kind == "old" else 0)
    record = harness_session("evaluation fixture" if kind == "evaluation" else "request",
                             [message("assistant" if kind == "assistant" else "user", words, at.isoformat())],
                             platform="cron" if kind == "routine" else "demiurge", owner=kind != "stranger")
    if kind != "never":
        write_shards(continuation.runtime.home() / "records/sessions", record)
    before = (config.vault_path / "agent/Tasks.md").read_bytes()
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "set", "--delivers", words, "--said", words)
    assert "words were not found in what the owner said; drop --said" in capsys.readouterr().err
    assert (config.vault_path / "agent/Tasks.md").read_bytes() == before
    assert asks(config) == []


@pytest.mark.parametrize("grounded,has_delivers", [(True, True), (False, True), (False, False)])
def test_open_finish_asks_migrate_once_with_a_report(worker, capsys, grounded, has_delivers):
    config, _ = worker
    current = task(config)
    current.body += '- **Source:** "Publish three listings under budget in the Library"\n'
    if has_delivers:
        current.body += '- **Delivers:** ' + ("three listings under budget" if grounded else "a draft shortlist") + "\n"
        current.state["delivers_by"] = "agent"
    current.state["finish_asked"] = "pending"
    tasks.save_task(config, current, config.today())
    journal.add_ask(config, config.today(), "What would done look like for Sample? Until then Agent won't keep working.", "agent/Tasks#T-001")
    captured = cli(config, capsys, "continue", "--due", "--json")
    assert "finish line: T-001:" in captured.err
    assert "finish_asked" not in task(config).state
    if grounded:
        assert "answered by their earlier words" in captured.err
        assert "delivers_by" not in task(config).state
        assert task(config).state["delivers_said"] == config.today().isoformat()
        assert asks(config) == []
    elif has_delivers:
        assert asks(config) == [GUESS]
        assert task(config).state["delivers_by"] == "agent"
    else:
        assert asks(config) == []
        assert json.loads(captured.out)[0]["id"] == "T-001"
    before = (config.vault_path / "CURRENT.md").read_bytes()
    assert cli(config, capsys, "continue", "--due", "--json").err == ""
    assert (config.vault_path / "CURRENT.md").read_bytes() == before


def test_quoted_source_grounds_without_a_runtime_but_the_rest_of_the_body_does_not(worker, capsys, monkeypatch):
    config, _ = worker
    monkeypatch.delenv("AGENT_HOME")
    cli(config, capsys, "task", "new", "Blue", "--source", "Owner: “Ship the ‘blue’ version — by Friday.”")
    cli(config, capsys, "task", "T-002", "set", "--delivers", "the blue version shipped",
        "--said", "Ship the 'blue' version - by Friday.")
    assert "delivers_by" not in task(config, "T-002").state
    current = task(config)
    current.body += '- **Evidence:** I noted: "a shortlist of three is enough"\n'
    tasks.save_task(config, current, config.today())
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "set", "--delivers", "three", "--said", "a shortlist of three is enough")
    assert "words were not found" in capsys.readouterr().err


@pytest.mark.parametrize("words", ["finish this now", "it's done now", "one-two-three-four"])
def test_said_needs_four_words(worker, capsys, words):
    config, _ = worker
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist", "--said", words)
    assert "at least four words" in capsys.readouterr().err
    assert task(config).delivers is None
    assert asks(config) == []


def test_delivers_cannot_ground_itself_and_refused_creation_writes_nothing(worker, capsys):
    config, _ = worker
    before = (config.vault_path / "agent/Tasks.md").read_bytes()
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "new", "Shortlist", "--source", "Look into some apartments",
            "--delivers", "Three finalists in the Library", "--said", "Three finalists in the Library")
    assert "words were not found" in capsys.readouterr().err
    assert (config.vault_path / "agent/Tasks.md").read_bytes() == before
    assert asks(config) == []


def test_a_new_worker_proposal_raises_a_confirm_without_a_handoff(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "new", "Shortlist", "--source", "Look into some apartments", "--delivers", "three finalists")
    item, = needs.items(config, config.today())
    assert (item.kind, item.task) == ("confirm", "T-002")


def test_proposal_confirm_survives_waiting_on_another_question_and_is_not_raised_after_dismissal(worker, capsys):
    config, _ = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "set", "--state", "waiting_for_owner",
        "--ask", "Which account should I use?", "--why", "I can read the listings there.")
    assert {item.kind for item in needs.items(config, config.today())} == {"ask", "confirm"}
    item = next(item for item in needs.items(config, config.today()) if item.kind == "confirm")
    needs.dismiss(config, item.id, config.today())
    due(config, capsys)
    assert task(config).task_state == "waiting_for_owner"
    assert [item.kind for item in needs.items(config, config.today())] == ["ask"]


def test_a_generic_conversation_resolution_does_not_invent_a_correction(worker, capsys, monkeypatch):
    config, clock = worker
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    cli(config, capsys, "task", "T-001", "done", "--why", "the shortlist is in the Library")
    item = needs.items(config, config.today())[0]
    monkeypatch.setattr(journal, "role", lambda *args: "owner")
    journal.append_entry(config, {"outcome": "done", "headline": "Accepted the shortlist", "resolved": [item.id]},
                         "demiurge", clock[0])
    assert task(config).delivers == "a draft shortlist"
    assert task(config).task_state == "completed"
    assert asks(config) == []


def test_closed_archived_confirm_keeps_its_owner_and_its_acceptance_without_runtime(worker, capsys, monkeypatch):
    from brain import fm
    config, _ = worker
    current = task(config)
    current.body = tasks.HANDOFF_LINE.sub(tasks.handoff_line(config.today().isoformat(), "demiurge:kevin", "Home") + "\n", current.body)
    tasks.save_task(config, current, config.today())
    cli(config, capsys, "task", "T-001", "set", "--delivers", "a draft shortlist")
    closed = tasks.close(config, "T-001", config.today(), True, "Shortlist is in the Library.")
    tasks.write_archive(config, closed, "cold", config.today(), whole=True)
    fm.write(config.vault_path / "agent/Tasks.md", {}, "# Tasks\n\n## Active work\n")
    item, = needs.items(config, config.today())
    assert item.owner == "demiurge:kevin"
    monkeypatch.delenv("AGENT_HOME")
    needs.resolve(config, item.id, "three listings under budget", config.today(), owner_engaged=True, by="demiurge:kevin")
    assert task(config).delivers == "a draft shortlist"
    assert task(config).task_state == "completed"
    assert "delivers_by" not in task(config).state


def test_migrate_check_runs_the_no_prompt_step_once_per_vault(worker, capsys, monkeypatch):
    from brain import install
    config, _ = worker
    current = task(config)
    current.body += '- **Delivers:** a draft shortlist\n'
    current.state.update(delivers_by="agent", finish_asked="pending")
    tasks.save_task(config, current, config.today())
    journal.add_ask(config, config.today(), "What would done look like for Sample?", "agent/Tasks#T-001")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    assert install.migrate_check(config) is None
    assert "finish line: T-001:" in capsys.readouterr().err
    assert asks(config) == [GUESS]
    def unexpected(*args):
        pytest.fail("the automatic upgrade step must run only once per vault")
    monkeypatch.setattr(continuation, "finish_lines", unexpected)
    assert install.migrate_check(config) is None
    assert capsys.readouterr().err == ""


def test_migration_grounds_a_short_delivers_from_a_longer_owner_quote(worker, capsys):
    config, _ = worker
    current = task(config)
    current.body += '- **Source:** "Publish a draft shortlist"\n- **Delivers:** a draft shortlist\n'
    current.state["delivers_by"] = "agent"
    tasks.save_task(config, current, config.today())
    journal.add_ask(config, config.today(), "What would done look like for Sample?", "agent/Tasks#T-001")
    due(config, capsys)
    assert "delivers_by" not in task(config).state
    assert '- **Said:** "Publish a draft shortlist"' in task(config).body
    assert asks(config) == []
