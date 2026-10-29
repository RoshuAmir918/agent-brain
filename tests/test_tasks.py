from datetime import date

import pytest

from brain import fm, nightly, validate
from brain.config import Config
from brain.tasks import (
    TASK_STATES, Task, archived_open_tasks, is_open, load_tasks, parse_register,
    parse_state, read_archived_task, write_archive,
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
    ("waiting_for_owner", True), ("completed", False), ("cancelled", False),
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
        (vault / "Warm Index.md").write_text("[[agent/Tasks Archive/T-023|T-023]]\n")
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
])
def test_written_states_are_normalised(written, state, opened):
    task = Task("T-023", "Sample", "", {"state": written})
    assert task.task_state == state and is_open(task) is opened
