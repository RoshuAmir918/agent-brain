"""A line of work survives task boundaries, archives and worker chunks."""
import json
from contextlib import contextmanager
from datetime import date, datetime

import pytest

from brain import cli as brain_cli, continuation, fm, tasks, works
from brain.config import Config
from brain.lock import vault_lock
from conftest import snapshot

TODAY = date(2026, 11, 6)


def cli(config, capsys, *args):
    brain_cli.main([*args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


@pytest.fixture
def stages_config(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Config, "now", lambda self, moment=None: datetime(2026, 11, 6, 13))
    return Config.load(vault)


def archive(config, tid, *, after=None, work=None, state="completed", body=""):
    body += (f"- **After:** {after}\n" if after else "") + (f"- **Work:** {work}\n" if work else "")
    task = tasks.Task(tid, f"Stage {tid}", body, {"state": state})
    tasks.write_archive(config, task, "cold", TODAY, whole=True)
    return task


def test_after_new_set_clear_and_handoff_preserve(stages_config, capsys):
    config = stages_config
    archive(config, "T-002")
    out = json.loads(cli(config, capsys, "task", "new", "Next stage", '--source', '"now do X"',
                         "--after", "T-002", "--delivers", "the next result", "--json"))
    assert out == {"id": "T-003", "title": "Next stage"}
    task = next(t for t in tasks.all_tasks(config) if t.tid == "T-003")
    assert task.after == "T-002" and "- **After:** T-002\n" in task.body
    cli(config, capsys, "handoff", "T-003", "--work", "result", "--report", "Home")
    assert next(t for t in tasks.all_tasks(config) if t.tid == "T-003").after == "T-002"
    cli(config, capsys, "task", "T-003", "set", "--after", "T-001")
    assert next(t for t in tasks.all_tasks(config) if t.tid == "T-003").after == "T-001"
    cli(config, capsys, "task", "T-003", "set", "--clear", "after")
    task = next(t for t in tasks.all_tasks(config) if t.tid == "T-003")
    assert task.after is None and "**After:**" not in task.body
    assert task.work == "result" and task.handoff[1] == "Home"


@pytest.mark.parametrize("command,target,message", [
    ("set", "T-001", "itself"), ("set", "T-999", "does not exist"), ("set", "missing", "T-nnn"),
    ("set", "T-002", "cycle"), ("set", "T-003", "cycle"),
    ("new", "T-004", "itself"), ("new", "T-999", "does not exist"), ("new", "bad", "T-nnn"),
])
def test_after_invalid_set_or_new_is_atomic(stages_config, capsys, command, target, message):
    config = stages_config
    archive(config, "T-002", after="T-001")
    archive(config, "T-003", after="T-002")
    args = (("task", "T-001", "set", "--after", target, "--status", "must not be written") if command == "set"
            else ("task", "new", "Next", "--source", '"now do X"', "--after", target))
    before = snapshot(config.vault_path)
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, *args)
    assert error.value.code == 1 and message in capsys.readouterr().err
    assert snapshot(config.vault_path) == before


def test_stages_union_order_cap_self_exclusion_and_register_precedence(stages_config):
    config = stages_config
    # Closed chains, open work siblings and dropped siblings all contribute once.
    archive(config, "T-002", after="T-001")
    warm = archive(config, "T-003", work="result", state="ready")
    archive(config, "T-004", after="T-002", work="result", body="- **Completed:** 2026-11-06 — fourth result\n")
    archive(config, "T-005", work="result", body="- **Completed:** 2026-11-05 — fifth result\n")
    archive(config, "T-006", work="result", state="in_progress", body="- **Why:** sixth reason\n")
    archive(config, "T-007", work="result", state="cancelled", body="- **Dropped:** 2026-11-04 — seventh reason\n")
    archive(config, "T-008", after="T-004", body="- **Delivers:** eight\n- **Verified:** proof\n- **Next action:** follow up\n")
    with vault_lock(config):
        current = tasks.new_task(config, 'Current', '"next"', work="result", values={"after": "T-008"})
        path = config.vault_path / config.agent("Tasks.md")
        data, body = fm.read(path)
        fm.write(path, data, body + "\n" + warm.render_line(config))
    archive(config, current.tid, after="T-999", work="stale-copy")
    entries = tasks.all_tasks(config)
    assert next(t for t in entries if t.tid == "T-003").work == "result"
    assert next(t for t in entries if t.tid == current.tid).work == "result"
    rows = continuation.stages(config, current)
    assert [row["id"] for row in rows] == [f"T-{i:03}" for i in range(4, 9)]
    assert rows[0]["outcome"] == "fourth result" and rows[0]["closed"] == "2026-11-06"
    assert rows[1]["closed"] == "2026-11-05"  # creation order, not closure order
    assert rows[2]["state"] == "in_progress" and rows[2]["outcome"] == "sixth reason"
    assert rows[3]["state"] == "cancelled" and rows[3]["outcome"] == "seventh reason"
    assert rows[4] == {"id": "T-008", "title": "Stage T-008", "state": "completed", "closed": None,
                       "outcome": None, "delivers": "eight", "verified": "proof", "next": "follow up"}


def test_after_chain_without_work_includes_closed_and_hand_archived(stages_config):
    config = stages_config
    folder = tasks.archive_path(config, "T-002").parent
    folder.mkdir(parents=True)
    (folder / "T-002.md").write_text("# T-002 — Old hand archive\n<!-- brain: state=completed -->\n- **After:** T-001\n")
    archive(config, "T-003", after="T-002")
    with vault_lock(config):
        current = tasks.new_task(config, 'Follow on', '"now do X"', values={"after": "T-003"})
    assert [row["id"] for row in continuation.stages(config, current)] == ["T-001", "T-002", "T-003"]
    # Legacy broken chains and cycles terminate even if someone wrote them by hand.
    archive(config, "T-003", after="T-004")
    assert [row["id"] for row in continuation.stages(config, current)] == ["T-003"]
    archive(config, "T-003", after="T-999")
    assert [row["id"] for row in continuation.stages(config, current)] == ["T-003"]


def test_same_work_alone_supplies_stages_without_a_checkpoint(stages_config, capsys):
    config = stages_config
    archive(config, "T-002", work="result", body="- **Completed:** 2026-11-05 — earlier outcome\n")
    archive(config, "T-003", work="other")
    with vault_lock(config):
        current = tasks.new_task(config, 'Current', '"next"', work="result", values={"delivers": "visible result"})
        tasks.hand_off(config, current.tid, TODAY)
    text = cli(config, capsys, "continue", current.tid)
    assert text.startswith("Earlier stages of this work:\n- T-002")
    assert "earlier outcome" in text and "Stage T-003" not in text
    row = json.loads(cli(config, capsys, "continue", current.tid, "--json"))
    assert row["after"] is None and [stage["id"] for stage in row["stages"]] == ["T-002"]


def test_state_due_and_positional_json_and_instruction_order(stages_config, capsys):
    config = stages_config
    archive(config, "T-002", body="- **Completed:** 2026-11-06 — first outcome\n- **Delivers:** applications\n"
            "- **Verified:** confirmations\n- **Next:** contact agency\n")
    with vault_lock(config):
        task = tasks.new_task(config, 'Next stage', '"next"', values={"after": "T-002", "delivers": "a follow up"})
        tasks.hand_off(config, task.tid, TODAY, "Home")
    continuation.checkpoint(config, task.tid, "Saved stopping point")
    continuation.review(config, task.tid, "met", "follow up confirmed")
    due = json.loads(cli(config, capsys, "continue", "--due", "--json"))[0]
    positional = json.loads(cli(config, capsys, "continue", task.tid, "--json"))
    state_rows = json.loads(cli(config, capsys, "state", "--json"))["tasks"]
    state_row = next(row for row in state_rows if row["id"] == task.tid)
    assert state_row["after"] == "T-002"
    for row in (due, positional):
        assert row["after"] == "T-002"
        assert row["stages"] == [{"id": "T-002", "title": "Stage T-002", "state": "completed", "closed": "2026-11-06",
                                  "delivers": "applications", "outcome": "first outcome", "verified": "confirmations", "next": "contact agency"}]
    assert state_rows[0]["after"] is None
    text = cli(config, capsys, "continue", task.tid)
    expected = ("Earlier stages of this work:\n"
                "- T-002 Stage T-002 (completed 2026-11-06): first outcome; Delivers: applications; "
                "last Verified: confirmations; Next it left: contact agency")
    assert expected in text
    assert text.index("The reviewer judged") < text.index(expected) < text.index("Saved stopping point")
    assert positional["checkpoint"] == due["checkpoint"]
    with vault_lock(config):
        tasks.set_fields(config, task.tid, TODAY, clear=["after"])
    assert "Earlier stages" not in cli(config, capsys, "continue", task.tid)
    assert json.loads(cli(config, capsys, "continue", task.tid, "--json"))["stages"] == []


def test_stage_text_is_bounded_and_omits_missing_fields():
    long = "long " * 200
    rows = [{"id": f"T-{i:03}", "title": long, "state": "completed", "closed": "2026-11-06",
             "outcome": long, "delivers": long, "verified": long, "next": long} for i in range(5)]
    text = continuation.stages_text(rows)
    assert len(text.split()) < 250 and len(text.splitlines()) == 6 and "…" in text
    for row in rows:
        for name in ("state", "closed", "outcome", "delivers", "verified", "next"):
            row[name] = None
    text = continuation.stages_text(rows)
    assert "Delivers" not in text and "Verified" not in text and "Next it left" not in text and "(None" not in text
    assert continuation.stages_text([]) is None


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("location", ["register", "warm", "archive"])
def test_work_history_on_close_is_atomic_and_under_vault_lock(stages_config, capsys, monkeypatch, existing, location):
    config = stages_config
    with vault_lock(config):
        tasks.set_fields(config, "T-001", TODAY, values={"work": "result", "delivers": "eligible applications", "next": "contact agency"})
        if location != "register":
            task = tasks.load_tasks(config)[0][0]
            tasks.write_archive(config, task, "warm", TODAY, whole=True)
            path = config.vault_path / config.agent("Tasks.md")
            fm.write(path, {}, "# Tasks\n" + (task.render_line(config) if location == "warm" else ""))
    folder = works.runtime.home() / "works/result"
    folder.mkdir(parents=True)
    history = folder / "HISTORY.md"
    old = "# History\n\n## Previous stage\n- Outcome: retained\n" if existing else "# History\n"
    if existing:
        history.write_text(old)
    held = False
    original_write = fm.write

    @contextmanager
    def locked(cfg):
        nonlocal held
        with vault_lock(cfg):
            held = True
            try:
                yield
            finally:
                held = False

    def checked_write(path, data, body):
        if path == history:
            assert held
        return original_write(path, data, body)

    monkeypatch.setattr(brain_cli, "vault_lock", locked)
    monkeypatch.setattr(fm, "write", checked_write)
    cli(config, capsys, "task", "T-001", "done", "--why", "submitted with confirmations")
    expected = (old.rstrip() + "\n\n## 2026-11-06 · T-001 · Sample\n- Outcome: submitted with confirmations\n"
                "- Delivers: eligible applications\n- Next it left: contact agency\n")
    assert history.read_text() == expected
    assert not list(folder.glob(".*.tmp"))
    with pytest.raises(SystemExit):
        cli(config, capsys, "task", "T-001", "done", "--why", "again")
    capsys.readouterr()
    assert history.read_text() == expected


@pytest.mark.parametrize("mode", ["no-home", "no-work", "not-local"])
def test_history_skips_when_work_unavailable(stages_config, capsys, monkeypatch, mode):
    config = stages_config
    home = works.runtime.home()
    if mode != "no-work":
        cli(config, capsys, "task", "T-001", "set", "--work", "not-local")
    if mode == "no-home":
        monkeypatch.delenv("AGENT_HOME")
    cli(config, capsys, "task", "T-001", "done", "--why", "finished")
    assert not home.exists()
    assert tasks.load_tasks(config)[0][0].task_state == "completed"


def test_history_append_preserves_previous_entries_and_omits_absent_next(stages_config, capsys):
    config = stages_config
    folder = works.runtime.home() / "works/result"
    folder.mkdir(parents=True)
    for title in ("First", "Second"):
        row = json.loads(cli(config, capsys, "task", "new", title, '--source', f'"{title}"', "--work", "result",
                             "--delivers", "visible result", "--json"))
        cli(config, capsys, "task", row["id"], "done", "--why", f"delivered {title}")
    text = (folder / "HISTORY.md").read_text()
    assert text.count("# History") == 1 and text.count("## 2026-11-06") == 2
    assert text.index("T-002 · First") < text.index("T-003 · Second")
    assert "Next it left" not in text


def test_history_records_reopened_closures_and_latest_outcome(stages_config, capsys):
    config = stages_config
    folder = works.runtime.home() / "works/result"
    folder.mkdir(parents=True)
    cli(config, capsys, "task", "T-001", "set", "--work", "result", "--delivers", "visible result")
    cli(config, capsys, "task", "T-001", "done", "--why", "first evidence")
    cli(config, capsys, "task", "T-001", "reopen", "--why", "changed requirements")
    cli(config, capsys, "task", "T-001", "drop", "--why", "no longer needed")
    history = (folder / "HISTORY.md").read_text()
    assert history.count("## 2026-11-06 · T-001 · Sample") == 2
    assert history.index("first evidence") < history.index("no longer needed")
    current = tasks.Task("T-002", "Follow on", "- **After:** T-001\n")
    row, = continuation.stages(config, current)
    assert row["state"] == "cancelled" and row["outcome"] == "no longer needed"


def test_history_atomic_failure_preserves_existing_file(stages_config, monkeypatch):
    folder = works.runtime.home() / "works/result"
    folder.mkdir(parents=True)
    path = folder / "HISTORY.md"
    path.write_text("# History\n\nPrevious entry\n")

    def fail(source, target):
        raise OSError("rename failed")

    monkeypatch.setattr(fm.os, "replace", fail)
    with vault_lock(stages_config), pytest.raises(OSError, match="rename failed"):
        works.append_history("result", "T-001", "First", TODAY, "evidence", "delivered", None)
    assert path.read_text() == "# History\n\nPrevious entry\n"
    assert not list(folder.glob(".*.tmp"))
