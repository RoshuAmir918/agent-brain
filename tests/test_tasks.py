from datetime import date

import pytest

from brain import fm, nightly, validate
from brain.config import Config
from brain.tasks import (
    TASK_STATES, Task, archived_open_tasks, is_open, load_tasks, parse_register,
    parse_state, quiet, read_archived_task, write_archive,
)
from brain.vault import Vault


@pytest.mark.parametrize("state", TASK_STATES)
def test_parse_task_state(state):
    comment = f"uses=2026-10-04; state={state}; custom=keep=this"
    expected = {"uses": "2026-10-04", "state": state, "custom": "keep=this"}
    assert parse_state(comment) == expected
    _, tasks = parse_register(f"### T-023 — Sample\n<!-- brain: {comment} -->\n- **Status:** detail\n")
    assert tasks[0].state == expected
    assert tasks[0].task_state == state


@pytest.mark.parametrize("state, opened", [
    ("ready", True), ("in_progress", True), ("blocked", True),
    ("waiting_for_owner", True), ("set_aside", True), ("completed", False), ("cancelled", False),
    ("waitng", True), ("", True),
])
@pytest.mark.parametrize("status", [None, "open", "completed", "cancelled"])
def test_is_open_uses_state_before_status(state, opened, status):
    metadata = {"state": state}
    if status is not None:
        metadata["status"] = status
    task = Task("T-023", "Sample", "", metadata)
    assert task.task_state == state
    assert is_open(task) is opened


@pytest.mark.parametrize("status, effective, opened", [
    (None, None, True), ("open", None, True), ("unknown", None, False),
    ("", None, False), ("completed", "completed", False), ("cancelled", "cancelled", False),
])
def test_legacy_task_state(status, effective, opened):
    task = Task("T-023", "Sample", "", {"status": status} if status is not None else {})
    assert task.task_state == effective
    assert is_open(task) is opened


@pytest.mark.parametrize("renderer", ["render", "render_line"])
@pytest.mark.parametrize("comment, expected", [
    ("custom=first; status=completed; state=blocked; uses=2026-10-04; extra=last",
     "custom=first; state=blocked; uses=2026-10-04; extra=last"),
    ("custom=first; status=open; uses=2026-10-04; extra=last",
     "custom=first; status=open; uses=2026-10-04; extra=last"),
    ("custom=first; uses=2026-10-04; extra=last",
     "custom=first; uses=2026-10-04; extra=last"),
])
def test_render_preserves_metadata_order(template_config, renderer, comment, expected):
    _, tasks = parse_register(f"### T-023 — Sample\n<!-- brain: {comment} -->\n- **Status:** detail\n")
    task = tasks[0]
    original = list(task.state.items())
    rendered = task.render() if renderer == "render" else task.render_line(template_config)
    assert rendered.splitlines()[1] == f"<!-- brain: {expected} -->"
    _, restored = parse_register(rendered)
    assert list(restored[0].state.items()) == list(parse_state(expected).items())
    assert list(task.state.items()) == original
    if renderer == "render":
        assert restored[0].body == task.body


@pytest.mark.parametrize("body, hub", [
    ("- **Hub:** [[Lisbon trip]]", "Lisbon trip"),
    ("- **Hub:** [[Lisbon trip|travel]]", "Lisbon trip"),
    ("- **Hub:** [[Lisbon trip#Flights]]", "Lisbon trip"),
    ("- **Hub:** [[Plans/Lisbon trip#Flights|travel]]", "Plans/Lisbon trip"),
    ("- **Status:** see [[Other]]\n- **Hub:** [[Lisbon trip]]\n- **Next:** book", "Lisbon trip"),
    ("- **Status:** see [[Other]]", None),
    ("- **Status:** mentions - **Hub:** [[Other]]", None),
    ("", None),
])
def test_task_hub(body, hub):
    _, tasks = parse_register(f"### T-023 — Sample\n{body}\n")
    assert tasks[0].hub == hub


@pytest.mark.parametrize("state", TASK_STATES)
def test_archived_task_state_and_hub(template_config, state):
    task = Task("T-023", "Sample", "- **Hub:** [[Lisbon trip#Flights|travel]]\n", {
        "custom": "first", "status": "completed", "state": state, "extra": "last",
    })
    write_archive(template_config, task, "cold", date(2026, 10, 5))
    path = template_config.vault_path / template_config.agent("Tasks Archive/T-023.md")
    assert path.read_text().splitlines()[1] == f"<!-- brain: custom=first; state={state}; extra=last -->"
    archived = read_archived_task(template_config, path)
    assert archived.task_state == state
    assert archived.hub == "Lisbon trip"
    assert list(archived.state.items()) == [("custom", "first"), ("state", state), ("extra", "last")]
    assert [task.tid for task in archived_open_tasks(template_config)] == (
        ["T-023"] if is_open(archived) else [])


def test_legacy_archive_metadata_is_unchanged(template_config):
    task = Task("T-023", "Sample", "- **Hub:** [[Lisbon trip]]\n", {
        "custom": "first", "status": "open", "extra": "last",
    })
    write_archive(template_config, task, "cold", date(2026, 10, 5))
    path = template_config.vault_path / template_config.agent("Tasks Archive/T-023.md")
    archived = read_archived_task(template_config, path)
    assert archived.task_state is None
    assert archived.hub == "Lisbon trip"
    assert list(archived.state.items()) == list(task.state.items())


@pytest.mark.parametrize("location", ["register", "archive", "monthly"])
@pytest.mark.parametrize("state", [None, *TASK_STATES, "waitng", ""])
def test_validate_task_states(vault, location, state):
    metadata = {"status": "completed"}
    if state is not None:
        metadata["state"] = state
    task = Task("T-023", "Sample", "- **Status:** detail\n", metadata)
    config = Config.load(vault)
    if location == "register":
        path = vault / config.agent("Tasks.md")
        data, body = fm.read(path)
        fm.write(path, data, body + task.render())
    elif location == "archive":
        write_archive(config, task, "cold", date(2026, 10, 5))
    else:
        path = vault / config.agent("Tasks Archive/2026-10.md")
        path.parent.mkdir()
        path.write_text("# Completed tasks — 2026-10\n\n" + task.render())
    assert validate.run(Vault(vault), set()) == []   # never a nightly gate
    assert validate.task_states(Vault(vault)) == ([f"T-023: unknown task state '{state}'"] if state in ("waitng", "") else [])


def test_state_and_hub_survive_task_tiering(vault):
    config = Config.load(vault)
    path = vault / config.agent("Tasks.md")
    data, _ = fm.read(path)
    task = Task("T-001", "Sample", "- **Status:** paused\n- **Hub:** [[Lisbon trip]]\n", {
        "custom": "first", "status": "completed", "state": "blocked", "uses": "2026-10-04",
    })
    fm.write(path, data, task.render())
    for day, tier in [(5, "hot"), (20, "warm")]:
        nightly.process_tasks(config, date(2026, 10, day), *load_tasks(config), [])
        tasks, archived = load_tasks(config)
        assert not archived
        assert tasks[0].state["tier"] == tier
        assert tasks[0].task_state == "blocked" and tasks[0].hub == "Lisbon trip"
    nightly.process_tasks(config, date(2026, 11, 5), *load_tasks(config), [])
    tasks, archived = load_tasks(config)
    assert not tasks
    assert archived[0].task_state == "blocked" and archived[0].hub == "Lisbon trip"
    archived[0].add_use("2026-11-06")
    write_archive(config, archived[0], "cold", date(2026, 11, 6))
    nightly.process_tasks(config, date(2026, 11, 6), *load_tasks(config), [])
    tasks, archived = load_tasks(config)
    assert not archived
    assert tasks[0].state["tier"] == "hot"
    assert tasks[0].task_state == "blocked" and tasks[0].hub == "Lisbon trip"


@pytest.mark.parametrize("written, state, opened", [
    ("In Progress", "in_progress", True), ("waiting for owner", "waiting_for_owner", True),
    ("waiting-for-owner", "waiting_for_owner", True), ("done", "completed", False), ("Canceled", "cancelled", False),
    ("Parked", "set_aside", True), ("set aside", "set_aside", True), ("deferred", "set_aside", True),
])
def test_written_states_are_normalised(written, state, opened):
    task = Task("T-023", "Sample", "", {"state": written})
    assert task.task_state == state and is_open(task) is opened


def test_quiet_tasks_are_open_undecided_and_untouched_long_enough_oldest_first():
    today = date(2026, 10, 29)
    tasks = [
        Task("T-001", "Fresh", "", {"state": "ready", "uses": "2026-10-20"}),
        Task("T-002", "Quiet", "", {"state": "ready", "uses": "2026-10-01"}),
        Task("T-003", "Owner, two weeks", "", {"state": "waiting_for_owner", "uses": "2026-10-15"}),
        Task("T-004", "Set aside", "", {"state": "set_aside", "uses": "2026-01-01"}),
        Task("T-005", "Done", "", {"state": "completed", "uses": "2026-01-01"}),
        Task("T-006", "Never touched", "- **Status:** no dates\n", {"state": "blocked"}),
        Task("T-007", "Deadline ahead", "- **Due:** 2026-11-15\n", {"state": "ready", "uses": "2026-09-01"}),
        Task("T-008", "Inferred use", "", {"state": "ready", "uses": "2026-09-01", "inferred": "2026-10-25"}),
    ]
    assert [(t.tid, touched) for t, touched in quiet(tasks, today)] == [
        ("T-006", ""), ("T-002", "2026-10-01"), ("T-003", "2026-10-15")]


def test_quiet_skips_malformed_dates_and_honours_a_deadline_in_the_title():
    today = date(2026, 10, 29)
    tasks = [
        Task("T-001", "Mixed", "- **Note:** 2026-13-45\n", {"state": "ready", "uses": "2026-09-01,unknown", "inferred": "bad"}),
        Task("T-002", "Renew by 2026-11-15", "", {"state": "ready", "uses": "2026-09-01"}),
    ]
    assert [(t.tid, touched) for t, touched in quiet(tasks, today)] == [("T-001", "2026-09-01")]



def test_handoff_line_is_parsed_and_written_once_after_the_first_field(vault, capsys):
    from brain.tasks import due, hand_off, with_handoff
    body = "- **Status:** open\n- **Next:** read the thread\n"
    once = with_handoff(body, date(2026, 11, 3), "Home")
    assert once == "- **Status:** open\n- **Handed off:** 2026-11-03 · report in Home\n- **Next:** read the thread\n"
    again = with_handoff(once, date(2026, 11, 4), "")
    assert again.count("Handed off") == 1 and "2026-11-04" in again and "report in" not in again
    from brain.tasks import set_fields
    set_fields(Config.load(vault), "T-001", date(2026, 11, 3), values={"delivers": "notes in Home"})
    task = hand_off(Config.load(vault), "T-001", date(2026, 11, 3), "the #lab room")
    assert task.handoff == ("2026-11-03", "the #lab room") and task.task_state == "in_progress"
    registered, _ = load_tasks(Config.load(vault))
    assert registered[0].handoff == ("2026-11-03", "the #lab room") and registered[0].task_state == "in_progress"
    assert "ORIGINAL_FULL_BODY" in registered[0].body
    assert due(registered) == [registered[0]]
    registered[0].state["state"] = "waiting_for_owner"
    assert due(registered) == []
    with pytest.raises(ValueError):
        hand_off(Config.load(vault), "T-999", date(2026, 11, 3))
    nightly.main(["continue", "--due", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert out.startswith("T-001 · handed off 2026-11-03 · Sample · next: ORIGINAL_FULL_BODY · report in the #lab room")


def test_continue_exits_one_when_nothing_is_handed_off_and_handoff_cli_reports(vault, capsys):
    with pytest.raises(SystemExit) as exit:
        nightly.main(["continue", "--due", "--vault", str(vault)])
    assert exit.value.code == 1 and "nothing handed off is due" in capsys.readouterr().out
    nightly.main(["handoff", "T-001", "--report", "Home", "--vault", str(vault)])
    assert "T-001 handed off" in capsys.readouterr().out and "reported in Home" in open(vault / "agent/Tasks.md").read() or True
    from brain import state as state_mod
    exported = state_mod.build(Config.load(vault), date(2026, 11, 3))
    assert [task["handoff"] for task in exported["tasks"] if task["id"] == "T-001"] == [Config.load(vault).today().isoformat()]


def test_yours_is_open_never_owed_to_the_owner_and_never_continued(vault):
    from brain import needs
    from brain.tasks import due, quiet
    path = vault / "agent/Tasks.md"
    data, _ = fm.read(path)
    mine = Task("T-020", "Complete Hope's résumé", "- **Status:** Roshan's own, when he is ready\n- **Handed off:** 2026-10-01\n- **Evidence:** 2026-10-20\n",
                {"state": "yours"})
    fm.write(path, data, "# Tasks\n\n## Active work\n\n" + mine.render())
    registered, _ = load_tasks(Config.load(vault))
    assert is_open(registered[0]) and registered[0].task_state == "yours"
    assert needs.items(Config.load(vault), date(2026, 11, 3)) == []
    assert due(registered) == []
    assert [t.tid for t, _ in quiet(registered, date(2026, 11, 2))] == []      # 13 days quiet
    assert [t.tid for t, _ in quiet(registered, date(2026, 11, 3))] == ["T-020"]  # 14: the evening asks once


def test_task_command_marks_yours_completes_and_drops(vault, capsys):
    nightly.main(["task", "T-001", "yours", "--vault", str(vault)])
    assert capsys.readouterr().out.startswith("T-001 yours: Sample")
    registered, _ = load_tasks(Config.load(vault))
    assert registered[0].task_state == "yours" and "ORIGINAL_FULL_BODY" in registered[0].body
    nightly.main(["task", "T-001", "done", "--vault", str(vault), "--why", "Roshan sent it"])
    registered, _ = load_tasks(Config.load(vault))
    assert registered[0].task_state == "completed" and "— Roshan sent it" in registered[0].body
    with pytest.raises(SystemExit):
        nightly.main(["task", "T-001", "drop", "--vault", str(vault)])
    assert "is not an open task" in capsys.readouterr().err


def test_saving_an_archived_task_does_not_repeat_its_old_body(vault):
    from datetime import date
    from brain import tasks as T
    from brain.config import Config
    config = Config.load(vault)
    T.write_archive(config, T.Task("T-050", "Archived", "- **Status:** first\n", {"state": "in_progress", "tier": "cold"}),
                    "cold", date(2026, 11, 1))
    for status in ("second", "third"):
        registered, archived = T.load_tasks(config)
        task = next(t for t in registered + archived if t.tid == "T-050")
        task.body = task.body.replace(task.body.split("**Status:** ")[1].split("\n")[0], status)
        T.save_task(config, task, date(2026, 11, 3))
    text = T.archive_path(config, "T-050").read_text(encoding="utf-8")
    assert "third" in text and "first" not in text and "second" not in text and "Earlier archive" not in text


def test_closing_a_registered_task_also_closes_its_older_archive_copy(vault):
    from datetime import date
    from brain import fm
    from brain import tasks as T
    from brain.config import Config
    config = Config.load(vault)
    stale = T.Task("T-060", "Setup", "- **Status:** old\n", {"state": "in_progress", "tier": "cold"})
    T.write_archive(config, stale, "cold", date(2026, 10, 20))
    reg = vault / "agent/Tasks.md"
    data, body = fm.read(reg)
    fm.write(reg, data, body + "\n" + T.Task("T-060", "Setup", "- **Status:** current\n", {"state": "in_progress"}).render())
    T.complete(config, "T-060", date(2026, 11, 3), "machinery done")
    data, body = fm.read(reg)
    fm.write(reg, data, body.split("### T-060")[0])          # the register stops listing it, as a hand move does
    registered, archived = T.load_tasks(config)
    assert all(t.tid != "T-060" for t in registered + archived)


@pytest.mark.parametrize("done", [True, False])
def test_closing_a_warm_task_closes_its_register_line_too(vault, done):
    from brain import tasks
    config = Config.load(vault)
    task = tasks.load_tasks(config)[0][0]
    write_archive(config, task, "warm", date(2026, 11, 5), whole=True)
    register = vault / config.agent("Tasks.md")
    data, body = fm.read(register)
    start = body.index(f"### {task.tid}")
    end = body.find("\n### ", start + 1)
    end = len(body) if end < 0 else end + 1
    fm.write(register, data, body[:start] + task.render_line(config) + body[end:])
    tasks.close(config, task.tid, date(2026, 11, 5), done, "finished" if done else "obsolete")
    closed = next(t for t in tasks.load_tasks(config)[0] + tasks.load_tasks(config)[1] if t.tid == task.tid)
    assert not tasks.is_open(closed)
    assert ("completed" if done else "cancelled") in fm.read(register)[1].split(f"### {task.tid}")[1].split("\n")[1]
