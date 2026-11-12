"""Owner work keeps its origin and leaves the worker waiting until the owner settles it."""
from datetime import timedelta

import pytest

from brain import cli, continuation, fm, journal, needs, reception, state, tasks
from brain.config import Config
from conftest import snapshot


def run(config, capsys, *args):
    cli.main([*args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


@pytest.fixture
def config(vault):
    return Config.load(vault)


@pytest.mark.parametrize("archived", [False, True])
def test_worker_followup_records_from(config, capsys, monkeypatch, archived):
    tasks.hand_off(config, "T-001", config.today(), "Home")
    if archived:
        parent = tasks.find(config, "T-001")
        tasks.write_archive(config, parent, "cold", config.today(), whole=True)
        fm.write(config.vault_path / "agent/Tasks.md", {}, "# Tasks\n")
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    run(config, capsys, "task", "new", "Follow up", "--source", "the worker needs this")
    child = tasks.find(config, "T-002")
    assert child.from_task == "T-001"
    assert "- **From:** T-001\n" in child.body
    assert tasks.find(config, "T-001").task_state == "in_progress"


def test_explicit_from_overrides_worker_and_accepts_closed_parent(config, capsys, monkeypatch):
    prior = tasks.new_task(config, "Earlier", "asked")
    tasks.close(config, prior.tid, config.today(), True, "finished")
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    run(config, capsys, "task", "new", "Owner follow up", "--source", "asked", "--from", prior.tid, "--state", "yours")
    assert tasks.find(config, "T-003").from_task == prior.tid
    assert tasks.find(config, prior.tid).task_state == "completed"
    assert tasks.find(config, "T-001").task_state is None


@pytest.mark.parametrize("parent", ["T-999", "bad", "", "T-001\nmore"])
def test_invalid_from_writes_nothing(config, capsys, parent):
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "new", "Follow up", "--source", "asked", "--from", parent)
    assert snapshot(config.vault_path) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_yours_origin_and_owner_fields_reach_state(config, capsys, monkeypatch, explicit):
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "origin")
    extra = ["--report", "demiurge:explicit"] if explicit else []
    run(config, capsys, "task", "new", "Send the introduction", "--source", "Nameless asked Roshan",
        "--state", "yours", "--from", "T-001", "--why", "Nameless is waiting for an introduction",
        "--delivers", "The introduction is sent", "--next", "Open the draft and send it", *extra)
    child = tasks.find(config, "T-002")
    report = "demiurge:explicit" if explicit else "demiurge:origin"
    assert child.report == report and child.handoff is None
    assert f"- **Report:** {report}\n" in child.body
    row = next(row for row in state.build(config, config.today())["tasks"] if row["id"] == child.tid)
    assert {key: row[key] for key in ("from", "report", "why", "delivers", "next", "handoff")} == {
        "from": "T-001", "report": report, "why": None,
        "delivers": "The introduction is sent", "next": "Open the draft and send it", "handoff": None,
    }
    run(config, capsys, "task", child.tid, "set", "--report", "demiurge:changed")
    assert tasks.find(config, child.tid).report == "demiurge:changed"
    assert tasks.find(config, child.tid).handoff is None


def test_owner_completion_resumes_parent_and_keeps_their_words(config, capsys, monkeypatch):
    tasks.hand_off(config, "T-001", config.today(), "demiurge:origin")
    tasks.set_fields(config, "T-001", config.today(), values={"resume": "2000-01-01"})
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    child = tasks.new_task(config, "Send introduction", "Nameless asked", state="yours",
                           values={"delivers": "Arslan has the introduction"}, agent_delivers=True)
    parent = tasks.find(config, "T-001")
    assert parent.task_state == "blocked"
    assert fm.field(parent.body, "Blocker") == f"{child.tid} Send introduction"
    assert parent.resume is None
    assert tasks.due([parent], now="2026-11-09 12:00") == []
    monkeypatch.delenv("DEMIURGE_TASK")
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "owner")
    assert cli.in_conversation()
    words = "I sent it to Arslan; he has the introduction now."
    run(config, capsys, "task", child.tid, "done", "--by-owner", "--why", words)
    child = tasks.find(config, child.tid)
    assert f"- **Completed:** {config.today()} — {words}\n" in child.body
    assert tasks.find(config, "T-001").task_state == "in_progress"
    assert child.tid in fm.field(tasks.find(config, "T-001").body, "Blocker")


@pytest.mark.parametrize("action", ["done", "drop"])
@pytest.mark.parametrize("waiting", ["ask", "waiting_for_owner", "yours"])
def test_close_refused_until_owner_settles_dependency(config, capsys, action, waiting):
    if waiting == "ask":
        journal.add_ask(config, config.today(), "Which person should receive it?", "agent/Tasks#T-001")
    elif waiting == "waiting_for_owner":
        tasks.set_fields(config, "T-001", config.today(), state=waiting,
                         values={"ask": "Which person should receive it?", "why": "The introduction needs a recipient"})
    else:
        tasks.append_task(config, tasks.Task("T-002", "Send introduction", "- **From:** T-001\n", {"state": "yours"}))
    before = snapshot(config.vault_path)
    if action == "done" and waiting != "yours":
        out = run(config, capsys, "task", "T-001", action, "--why", "finished")
        assert "settled 1 open ask(s)" in out
        assert tasks.find(config, "T-001").task_state == "completed"
        assert not needs.items(config, config.today())
        answered = (config.vault_path / config.agent(f"Answered/{config.today():%Y-%m}.md")).read_text()
        assert "finished" in answered
        return
    with pytest.raises(SystemExit) as error:
        run(config, capsys, "task", "T-001", action, "--why", "finished")
    assert error.value.code == 1
    message = capsys.readouterr().err
    assert "T-001" in message
    if waiting == "yours":
        assert f"waits on {config.human_name} for T-002 'Send introduction'; it is blocked until that closes" in message
        assert tasks.find(config, "T-001").task_state == "blocked"
    else:
        assert "answer" in message
        assert snapshot(config.vault_path) == before
        if waiting == "ask":
            assert "needs-you --resolve" in message and "--because" in message
    run(config, capsys, "task", "T-001", action, "--by-owner", "--why", "I am closing this myself")
    assert tasks.find(config, "T-001").task_state == ("completed" if action == "done" else "cancelled")
    assert not [item for item in needs.items(config, config.today()) if item.kind == "ask" and item.task == "T-001"]


def test_owner_work_for_one_task_is_one_list(config):
    first = tasks.new_task(config, "First owner step", "asked", state="yours", from_task="T-001")
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError, match=f"T-001 already waits on .* for {first.tid}.*brain task {first.tid} set --next"):
        tasks.new_task(config, "Second owner step", "asked", state="yours", from_task="T-001")
    assert snapshot(config.vault_path) == before
    tasks.close(config, first.tid, config.today(), True, "did both steps", by_owner=True)
    assert tasks.find(config, "T-001").task_state == "in_progress"
    tasks.new_task(config, "Next owner step", "asked", state="yours", from_task="T-001")


@pytest.mark.parametrize("computed", [False, True])
@pytest.mark.parametrize("answer", [None, "I will send Arslan the introduction tomorrow"])
def test_resolve_yours_preserves_ask_origin_and_runs_answer_hooks(config, capsys, monkeypatch, computed, answer):
    tasks.hand_off(config, "T-001", config.today(), "demiurge:asked-here")
    question = "Can you send Arslan an introduction?"
    if computed:
        tasks.set_fields(config, "T-001", config.today(), state="waiting_for_owner",
                         values={"ask": question, "why": "Nameless is waiting"})
    else:
        journal.add_ask(config, config.today(), question, "agent/Tasks#T-001",
                        why="Nameless is waiting", session="demiurge:asked-here")
    ask, = needs.items(config, config.today())
    calls = []
    monkeypatch.setattr(reception, "answered", lambda *args: calls.append(("reception", args)))
    monkeypatch.setattr(continuation, "answered", lambda *args, **kwargs: calls.append(("continuation", args, kwargs)))
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "answered-elsewhere")
    extra = ["--because", answer] if answer else []
    out = run(config, capsys, "needs-you", "--resolve", ask.id, "--yours", "Send the introduction",
              "--delivers", "Arslan has the introduction", "--why", "Nameless needs Roshan's introduction", *extra)
    assert out == "T-002 created: Send the introduction"
    child = tasks.find(config, "T-002")
    assert child.task_state == "yours" and child.from_task == "T-001"
    assert child.report == "demiurge:asked-here" and child.handoff is None
    assert f'Ask: "{question}"' in child.body
    if answer:
        assert f'Answer: "{answer}"' in child.body
    assert child.delivers == "Arslan has the introduction"
    assert not needs.items(config, config.today())
    assert tasks.find(config, "T-001").task_state == "blocked"
    assert [call[0] for call in calls] == ["reception", "continuation"]
    assert calls[1][1][3] == (answer or "became owner work: Send the introduction")
    assert calls[1][1][4] is True
    log = (config.vault_path / config.agent(f"Answered/{config.today():%Y-%m}.md")).read_text()
    assert log.count(question) == 1


def test_unlinked_ask_does_not_inherit_resolving_workers_task(config, capsys, monkeypatch):
    journal.add_ask(config, config.today(), "Can you renew your passport?", "CURRENT.md", session="demiurge:origin")
    ask, = needs.items(config, config.today())
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    run(config, capsys, "needs-you", "--resolve", ask.id, "--yours", "Renew passport")
    child = tasks.find(config, "T-002")
    assert child.from_task is None and child.report == "demiurge:origin"
    assert tasks.find(config, "T-001").task_state is None


@pytest.mark.parametrize("kind,title,extra", [("confirm", "Send it", []), ("ask", "", [])])
def test_invalid_conversion_keeps_ask_open_and_creates_nothing(config, capsys, kind, title, extra):
    journal.add_ask(config, config.today(), "Send the introduction?", "agent/Tasks#T-001", kind=kind)
    ask, = needs.items(config, config.today())
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit):
        run(config, capsys, "needs-you", "--resolve", ask.id, "--yours", title, *extra)
    assert snapshot(config.vault_path) == before


def test_old_task_files_have_null_from_and_report(config):
    task = tasks.find(config, "T-001")
    parsed, = tasks.parse_register(task.render())[1]
    assert parsed.from_task is None and parsed.report is None
    row, = state.build(config, config.today())["tasks"]
    assert row["from"] is None and row["report"] is None
    tasks.hand_off(config, "T-001", config.today(), "demiurge:old")
    assert tasks.find(config, "T-001").report == "demiurge:old"


def test_report_on_new_requires_yours(config, capsys):
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "new", "Follow up", "--source", "asked", "--report", "demiurge:origin")
    assert "--report with new needs --state yours" in capsys.readouterr().err
    assert snapshot(config.vault_path) == before


def test_later_owner_work_stays_open_and_quiet(config, capsys):
    out = run(config, capsys, "task", "new", "Explore job opportunities with Hope", "--source", "we'll do it together eventually",
              "--state", "yours", "--later")
    tid = out.split()[0]
    task = tasks.find(config, tid)
    assert task.later and tasks.is_open(task) and "later=true" in task.render()
    row = next(r for r in state.build(config, config.today())["tasks"] if r["id"] == tid)
    assert row["later"] is True and row["state"] == "yours"
    assert tid not in [t.tid for t, _ in tasks.quiet([task], config.today() + timedelta(days=60))]
    assert run(config, capsys, "task", tid, "set", "--now") == f"{tid} updated: now"
    assert not tasks.find(config, tid).later
    run(config, capsys, "task", tid, "set", "--later")
    assert tasks.find(config, tid).later


def test_later_only_for_owner_work(config, capsys):
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "new", "Mine", "--source", "asked", "--later")
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "T-001", "set", "--later")
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "T-001", "set", "--later", "--now")
    assert snapshot(config.vault_path) == before


def test_later_dropped_when_it_stops_being_owner_work(config, capsys):
    tid = run(config, capsys, "task", "new", "Some day", "--source", "eventually", "--state", "yours", "--later").split()[0]
    run(config, capsys, "task", tid, "set", "--state", "ready")
    assert "later" not in tasks.find(config, tid).state


def test_owner_work_a_task_waits_on_is_never_later(config, capsys):
    run(config, capsys, "task", "new", "Send the key", "--source", "worker needs it", "--state", "yours", "--from", "T-001")
    child = tasks.find(config, "T-002")
    assert tasks.find(config, "T-001").task_state == "blocked"
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit):
        run(config, capsys, "task", child.tid, "set", "--later")
    with pytest.raises(SystemExit):
        run(config, capsys, "task", "new", "Another", "--source", "worker needs it", "--state", "yours", "--from", "T-001", "--later")
    assert snapshot(config.vault_path) == before


def test_conversion_with_long_why_keeps_owner_text(config, capsys):
    journal.add_ask(config, config.today(), "Send the introduction?", "agent/Tasks#T-001")
    ask, = needs.items(config, config.today())
    why = " ".join(f"reason{i}" for i in range(41))
    run(config, capsys, "needs-you", "--resolve", ask.id, "--yours", "Send it", "--why", why)
    assert fm.field(tasks.find(config, "T-002").body, "Why") == why
