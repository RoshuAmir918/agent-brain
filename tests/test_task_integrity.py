"""The task lifecycle holds together: the night never loses a concurrent write, closed tasks leave the register,
active handoffs stay visible, asks leave with the waiting they stood for, and every step has a command."""
from datetime import date

import pytest

from brain import cli as brain_cli, fm, needs, nightly, state, tasks
from brain.config import Config
from brain.vault import Vault
from conftest import nightly_argv

TODAY = date(2026, 11, 5)


def cli(vault, capsys, *args):
    brain_cli.main(["task", *args, "--vault", str(vault)])
    return capsys.readouterr().out.strip()


def find(config, tid):
    registered, archived = tasks.load_tasks(config)
    return next(t for t in registered + archived if t.tid == tid)


def register(vault):
    return fm.read(vault / "agent/Tasks.md")[1]


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())


def test_the_night_keeps_a_worker_write_made_after_it_loaded_the_tasks(vault, monkeypatch, capsys):
    config = Config.load(vault)
    retier = nightly.retier

    def worker_writes_meanwhile(*args, **kwargs):
        tasks.set_fields(config, "T-001", TODAY, values={"next": "WORKER_NEXT"})
        tasks.new_task(config, "Made meanwhile", "owner words")
        return retier(*args, **kwargs)

    monkeypatch.setattr(nightly, "retier", worker_writes_meanwhile)
    try:
        brain_cli.main(nightly_argv(vault, TODAY, "--write"))
    except SystemExit:
        pass
    capsys.readouterr()
    assert "WORKER_NEXT" in find(config, "T-001").body
    assert find(config, "T-002").title == "Made meanwhile"


def test_a_closed_task_leaves_the_register_for_its_archive_and_the_months_list(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "done", "--why", "shipped, see [[Receipt]]")
    report = []
    nightly.rollover(config, TODAY, report)
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), report)
    assert "T-001" not in register(vault)
    archived = tasks.archive_path(config, "T-001").read_text()
    assert "state=completed" in archived and "ORIGINAL_FULL_BODY" in archived
    month = (vault / "agent/Tasks Archive/2026-11.md").read_text()
    assert "- **T-001 — Sample** · completed 2026-11-05 — shipped, see [[Receipt]]" in month
    assert tasks.completed(config, TODAY)[0]["id"] == "T-001"   # the follow-on check still finds it
    exported = next(t for t in state._tasks(Vault(config.vault_path)) if t["id"] == "T-001")
    assert (exported["open"], exported["closed"], exported["outcome"]) == (False, "2026-11-05", "shipped, see [[Receipt]]")


def test_a_handed_off_task_stays_in_the_register_however_cold_its_uses(vault, capsys):
    config = Config.load(vault)   # T-001's only use is 2026-09-20: cold by activation
    tasks.hand_off(config, "T-001", TODAY, "Home")
    report = []
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), report)
    assert "ORIGINAL_FULL_BODY" in register(vault)
    assert find(config, "T-001").state["tier"] == "hot"


def test_a_subheading_inside_an_entry_belongs_to_it(vault):
    config = Config.load(vault)
    path = vault / "agent/Tasks.md"
    data, body = fm.read(path)
    fm.write(path, data, body.rstrip("\n") + "\n### 2026-11-01 call notes\nThey want it by Friday.\n")
    assert "They want it by Friday." in find(config, "T-001").body


def test_set_work_attaches_a_work_without_restarting_the_handoff(vault, capsys):
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", date(2026, 11, 1), "demiurge:origin")
    tasks.set_pace(config, "T-001", "daily", date(2026, 11, 1))
    assert cli(vault, capsys, "T-001", "set", "--work", "trip-plan") == "T-001 updated: work"
    task = find(config, "T-001")
    assert (task.handoff, tasks.pace(task), task.work) == (("2026-11-01", "demiurge:origin"), "daily", "trip-plan")


def test_answering_the_owner_takes_the_ask_out_of_needs_you(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "set", "--state", "waiting_for_owner", "--ask", "Want me to book the 7pm table?",
        "--why", "It is the last one free.")
    current = vault / "CURRENT.md"
    assert needs.task_ask(config, "T-001").text == "Want me to book the 7pm table?"
    cli(vault, capsys, "T-001", "set", "--state", "in_progress")
    assert "7pm table" not in fm.read(current)[1]
    assert not [item for item in needs.items(config, TODAY) if "7pm" in item.text]
    assert "because T-001 is in progress" in (vault / "agent/Answered/2026-11.md").read_text()


def test_closing_a_task_also_settles_an_ask_sourced_to_it(vault, capsys):
    config = Config.load(vault)
    current = vault / "CURRENT.md"
    data, body = fm.read(current)
    fm.write(current, data, body + "\n## Needs you\n- 2026-11-05 · ask · Which venue? · [[agent/Tasks#T-001]]\n")
    cli(vault, capsys, "T-001", "drop", "--by-owner", "--why", "the owner went elsewhere")
    assert "Which venue?" not in fm.read(current)[1]   # at once, not only at night
    report = []
    nightly.rollover(config, TODAY, report)
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), report)
    assert "Which venue?" not in fm.read(current)[1]


def test_a_closed_task_can_be_reopened_from_its_archive(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "done", "--why", "looked finished")
    nightly.rollover(config, TODAY, [])
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), [])
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "reopen")
    capsys.readouterr()
    assert cli(vault, capsys, "T-001", "reopen", "--why", "the client wants one more page") == "T-001 ready: Sample"
    task = find(config, "T-001")
    assert tasks.is_open(task) and "- **Reopened:** 2026-11-05 — the client wants one more page" in task.body
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), [])
    assert "### T-001 — Sample" in register(vault)


def test_rename_and_take_back_a_handoff(vault, capsys):
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", TODAY, "Home")
    tasks.set_pace(config, "T-001", "daily", TODAY)
    cli(vault, capsys, "T-001", "set", "--title", "Sample, renamed", "--clear", "handoff")
    task = find(config, "T-001")
    assert task.title == "Sample, renamed" and task.handoff is None and "Pace" not in task.body and tasks.is_open(task)


def test_a_turn_must_say_why_it_closes_a_task_and_close_flags_are_never_dropped(vault, capsys, monkeypatch):
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "abc")
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "done")
    assert "needs --why" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "drop", "--why", "moot", "--reviewed", "dropped")
    assert "go with task T-nnn set" in capsys.readouterr().err
    monkeypatch.delenv("DEMIURGE_CONVERSATION")
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "done")   # a routine or another platform's turn: nothing says the owner closed it
    assert "needs --why" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "yours", "--by-owner")
    assert "--by-owner goes with" in capsys.readouterr().err
    cli(vault, capsys, "T-001", "done", "--by-owner")   # the owner's own Done in the app
    assert "the owner closed it in their app" in find(Config.load(vault), "T-001").body


def test_older_reviewed_lines_move_to_the_archive_instead_of_stopping_the_entry(vault):
    config = Config.load(vault)
    task = find(config, "T-001")
    task.body += "- **Latest evidence:** 2026-11-04 [[Receipt]]\n" + "".join(
        f"- **Reviewed:** 2026-10-{day:02d} — kept: still waiting on the venue to confirm the date and the deposit\n"
        for day in range(1, 13))
    bounded = tasks.bounded_hot_body(task, config)
    assert len(bounded.split()) <= tasks.MAX_WORDS
    assert "2026-10-12" in bounded and "**Earlier evidence:**" in bounded


def test_a_handoff_worked_today_is_not_quiet(vault):
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", date(2026, 10, 1), "Home")
    task = find(config, "T-001")
    assert tasks.quiet([task], TODAY)
    assert not tasks.quiet([task], TODAY, {"T-001": "2026-11-05T09:30:00-08:00"})


def test_a_handoff_too_long_to_bound_is_still_listed_as_a_warm_line(vault):
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", TODAY, "Home")
    task = find(config, "T-001")
    task.body += "- **Evidence:** " + " ".join(f"STEP_{n}" for n in range(400)) + "\n"
    task.state["tier"] = "cold"
    tasks.write_archive(config, task, "cold", TODAY, whole=True)
    path = vault / "agent/Tasks.md"
    data, _ = fm.read(path)
    fm.write(path, data, "# Tasks\n## Active work\n")
    report = []
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), report)
    assert "### T-001 — Sample" in register(vault) and "- **Warm:**" in register(vault)
    assert "STEP_399" in tasks.archive_path(config, "T-001").read_text()
    assert any("listed warm" in line for line in report)


def test_a_new_task_is_in_the_register_after_its_first_night(vault, capsys):
    config = Config.load(vault)
    tid = cli(vault, capsys, "new", "Move Ledger into the Library", "--source", "owner words").split()[0]
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), [])
    assert f"### {tid} — Move Ledger into the Library" in register(vault)


def add_ask(vault, line):
    current = vault / "CURRENT.md"
    data, body = fm.read(current)
    fm.write(current, data, body.rstrip("\n") + ("\n" if "## Needs you" in body else "\n## Needs you\n") + line + "\n")
    return current


def test_a_handoff_or_set_aside_settles_the_asks_a_waiting_task_stood_for(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "set", "--state", "waiting_for_owner", "--ask", "Want me to book the 7pm table?",
        "--why", "It is the last one free.")
    current = add_ask(vault, "- 2026-11-05 · ask · Which venue? · [[agent/Tasks#T-001]]")
    tasks.hand_off(config, "T-001", TODAY, "Home")
    assert "Which venue?" not in fm.read(current)[1]
    tasks.set_fields(config, "T-001", TODAY, state="waiting_for_owner", values={"ask": "Want me to book the 7pm table?"})
    add_ask(vault, "- 2026-11-05 · ask · Which date? · [[agent/Tasks#T-001]]")
    tasks.set_aside(config, "T-001", TODAY, "dismissed by the owner in their app")
    assert "Which date?" not in fm.read(current)[1]
    assert "because T-001 is set aside" in (vault / "agent/Answered/2026-11.md").read_text()


def test_a_waiting_task_keeps_its_ask(vault, capsys):
    cli(vault, capsys, "T-001", "set", "--state", "waiting_for_owner", "--ask", "Want me to book the 7pm table?")
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "set", "--clear", "ask")
    assert "needs the question" in capsys.readouterr().err
    assert find(Config.load(vault), "T-001").task_state == "waiting_for_owner"


@pytest.mark.parametrize("call", [
    lambda config: tasks.close(config, "T-001", TODAY, True, "done\n### T-002 — Phantom"),
    lambda config: tasks.close(config, "T-001", TODAY, False, "moot\nmore"),
    lambda config: tasks.set_aside(config, "T-001", TODAY, " "),
    lambda config: tasks.hand_off(config, "T-001", TODAY, "Home\n### T-002 — Phantom"),
])
def test_reasons_and_report_targets_are_single_line_and_reasons_are_required(vault, call):
    config = Config.load(vault)
    before = register(vault)
    with pytest.raises(ValueError):
        call(config)
    assert register(vault) == before


def test_closing_a_hot_excerpt_keeps_the_full_body_in_the_archive(vault):
    config = Config.load(vault)
    task = find(config, "T-001")
    task.body += "- **Latest evidence:** 2026-11-04 [[Receipt]]\n" + "".join(
        f"- **Reviewed:** 2026-10-{day:02d} — kept: FULL_ONLY_{day} still waiting on the venue to confirm the date\n"
        for day in range(1, 13))
    tasks.save_task(config, task, TODAY)
    tasks.write_archive(config, task, "hot", TODAY)
    task.body = tasks.bounded_hot_body(task, config)
    shed = next(f"FULL_ONLY_{day} " for day in range(1, 13) if f"FULL_ONLY_{day} " not in task.body)
    assert tasks.archive_pointer(config, "T-001") in task.body
    tasks.save_task(config, task, TODAY)
    tasks.close(config, "T-001", TODAY, True, "booked")
    assert shed in tasks.archive_path(config, "T-001").read_text()


def test_completed_reads_the_latest_closing_and_the_register_wins(vault):
    config = Config.load(vault)
    tasks.close(config, "T-001", date(2026, 10, 1), True, "first try")
    tasks.write_archive(config, find_any(config, "T-001"), "cold", date(2026, 10, 1))
    tasks.reopen(config, "T-001", TODAY, "one more page")
    assert tasks.completed(config, TODAY, 60) == []   # the archive's completed copy is stale
    tasks.close(config, "T-001", TODAY, True, "second try")
    row, = tasks.completed(config, TODAY)
    assert (row["completed"], row["evidence"]) == ("2026-11-05", "second try")


def find_any(config, tid):
    return next(t for t in tasks.all_tasks(config) if t.tid == tid)


def test_a_hand_archived_legacy_task_can_be_reopened(vault, capsys):
    config = Config.load(vault)
    path = vault / "agent/Tasks.md"
    data, _ = fm.read(path)
    fm.write(path, data, "# Tasks\n## Active work\n")
    legacy = tasks.archive_path(config, "T-001")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    fm.write(legacy, {"tier": "cold"}, "# T-001 — Sample\n<!-- brain: status=completed -->\n- **Status:** LEGACY_BODY\n")
    assert cli(vault, capsys, "T-001", "reopen", "--why", "back on") == "T-001 ready: Sample"
    task = find(config, "T-001")
    assert tasks.is_open(task) and "LEGACY_BODY" in task.body


def test_a_legacy_waiting_task_without_an_ask_stays_editable(vault, capsys):
    config = Config.load(vault)
    task = find(config, "T-001")
    task.state = {**task.state, "state": "waiting_for_owner"}
    task.state.pop("status", None)
    tasks.save_task(config, task, TODAY)
    cli(vault, capsys, "T-001", "set", "--next", "LEGACY_NEXT")
    assert "LEGACY_NEXT" in find(config, "T-001").body
    cli(vault, capsys, "T-001", "set", "--state", "ready")
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "set", "--state", "waiting_for_owner")
    assert "needs the question" in capsys.readouterr().err


def test_a_hand_archived_task_without_state_is_history_not_open_work(vault, capsys):
    config = Config.load(vault)
    path = vault / "agent/Tasks.md"
    data, _ = fm.read(path)
    fm.write(path, data, "# Tasks\n## Active work\n")
    legacy = tasks.archive_path(config, "T-001")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    fm.write(legacy, {"tier": "cold"}, "# T-001 — Sample\n- **Status:** LEGACY_BODY\n")
    assert tasks.load_tasks(config) == ([], [])
    assert [t.tid for t in tasks.all_tasks(config)] == ["T-001"]
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "set", "--next", "x")
    nightly.process_tasks(config, TODAY, *tasks.load_tasks(config), [])
    assert "T-001" not in register(vault)
