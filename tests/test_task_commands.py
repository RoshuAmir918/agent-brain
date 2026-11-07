"""Serialized task writes through the CLI, including full entries behind warm lines."""
import json
from contextlib import contextmanager
from datetime import date

import pytest

from brain import cli as brain_cli, fm, goals, ideas, tasks
from brain.config import Config
from conftest import snapshot

TODAY = date(2026, 11, 5)


def cli(vault, capsys, *args):
    brain_cli.main(["task", *args, "--vault", str(vault)])
    return capsys.readouterr().out.strip()


def entry(config):
    return tasks.load_tasks(config)[0] + tasks.load_tasks(config)[1]


@pytest.fixture(params=["register", "warm", "archive"])
def location(vault, request):
    config = Config.load(vault)
    original = entry(config)[0]
    original.body += "- **Verified:** old\n  continued proof\n- **Blocker:** old\n  old blocker\n- **Ask:** old\n- **Why:** old\n- **Delivers:** old\n- **Hub:** [[Old]]\n"
    if request.param == "register":
        tasks.save_task(config, original, TODAY)
    else:
        tasks.write_archive(config, original, "warm" if request.param == "warm" else "cold", TODAY, whole=True)
        path = vault / config.agent("Tasks.md")
        data, _ = fm.read(path)
        fm.write(path, data, "# Tasks\n\n" + (original.render_line(config) if request.param == "warm" else ""))
    (vault / "Topics").mkdir()
    fm.write(vault / "Topics/Project.md", {}, "# Project\n")
    return config, request.param


@pytest.mark.parametrize("name", tasks.FIELDS)
def test_set_each_field_and_clear_full_block(location, vault, capsys, name):
    config, where = location
    value = ("Project" if name == "hub" else "2026-11-06 08:05" if name == "resume" else "new-value" if name == "work"
             else "T-000" if name == "after"
             else "G-1" if name == "goal"
             else "new value")
    if name == "goal":
        goals.new(config, "Reading", "Read widely", from_app=True)
    if name == "after":
        tasks.write_archive(config, tasks.Task("T-000", "Earlier", "", {"state": "completed"}), "cold", TODAY)
    out = cli(vault, capsys, "T-001", "set", "--" + name, value)
    assert out == f"T-001 updated: {name}"
    task, = entry(config)
    expected = "[[Topics/Project]]" if name == "hub" else value
    label = "morning" if name == "morning" else tasks.LABELS.get(name, name.title())   # morning notes keep their lowercase convention
    assert f"- **{label}:** {expected}\n" in task.body
    assert task.body.count(f"**{label}:**") == 1
    if name == "next":
        assert "Next action" not in task.body and "ORIGINAL_FULL_BODY" not in task.body
    if name == "verified":
        assert "continued proof" not in task.body
    if name == "blocker":
        assert "old blocker" not in task.body
    if where != "register":
        assert expected in tasks.archive_path(config, "T-001").read_text()
    if where == "warm" and name == "status":
        assert "Status: new value" in fm.read(vault / config.agent("Tasks.md"))[1]
    assert cli(vault, capsys, "T-001", "set", "--clear", name) == f"T-001 updated: cleared {name}"
    assert f"**{name.title()}:**" not in entry(config)[0].body


def test_multiline_duplicates_and_multiple_clear_preserve_other_fields(vault, capsys):
    config = Config.load(vault)
    task = entry(config)[0]
    task.body = ("- **Status:** old\n  continuation\n\n  - nested value\n"
                 "- **Next:** keep\n- **Status:** duplicate\n  more old prose\n- **Evidence:** keep evidence\n")
    tasks.save_task(config, task, TODAY)
    cli(vault, capsys, "T-001", "set", "--status", "replaced")
    assert entry(config)[0].body == "- **Status:** replaced\n- **Next:** keep\n- **Evidence:** keep evidence\n"
    cli(vault, capsys, "T-001", "set", "--clear", "status", "next")
    assert entry(config)[0].body == "- **Evidence:** keep evidence\n"
    cli(vault, capsys, "T-001", "set", "--status", "appended")
    assert entry(config)[0].body.endswith("- **Status:** appended\n")


@pytest.mark.parametrize("state,normal", [("Ready", "ready"), ("In progress", "in_progress"),
                                           ("blocked", "blocked"), ("waiting-for-owner", "waiting_for_owner"),
                                           ("owner", "yours"), ("parked", "set_aside")])
def test_set_open_state(location, vault, capsys, state, normal):
    config, where = location
    cli(vault, capsys, "T-001", "set", "--state", state)
    assert entry(config)[0].task_state == normal
    assert "status" not in entry(config)[0].state
    if where == "warm":
        assert f"state={normal}" in fm.read(vault / config.agent("Tasks.md"))[1]


@pytest.mark.parametrize("args,message", [
    (["--state", "completed"], "done or drop"), (["--state", "cancelled"], "done or drop"),
    (["--state", "Done"], "done or drop"), (["--state", "canceled"], "done or drop"),
    (["--state", "flying"], "unknown task state"),
    (["--ask", "word " * 17], "at most 16 words"), (["--why", "word " * 41], "at most 40 words"),
    (["--hub", "Missing"], "note does not exist"), (["--hub", "[[START]]"], "without wikilink brackets"),
    (["--clear", "status", "--status", "value"], "set and cleared"),
    ([], "needs a field"),
] + [(["--" + name, "first\nsecond"], "single-line") for name in (*tasks.FIELDS, "state")]
  + [(["--status", "first\rsecond"], "single-line")])
def test_invalid_set_does_not_write(vault, capsys, args, message):
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        cli(vault, capsys, "T-001", "set", *args)
    assert error.value.code == 1
    assert message in capsys.readouterr().err
    assert snapshot(vault) == before


@pytest.mark.parametrize("flag", ["--title", "--report", "--reviewed"])
def test_task_new_refuses_set_only_flags(vault, capsys, flag):
    before = snapshot(vault)
    with pytest.raises(SystemExit):
        cli(vault, capsys, "new", "A new task", "--source", "START", flag, "x")
    assert "go with task T-nnn set" in capsys.readouterr().err
    assert snapshot(vault) == before


def test_word_limit_boundaries(vault, capsys):
    cli(vault, capsys, "T-001", "set", "--ask", "word " * 16, "--why", "word " * 40)
    task = entry(Config.load(vault))[0]
    assert "- **Ask:** " in task.body and "- **Why:** " in task.body


@pytest.mark.parametrize("closed", [None, "completed", "cancelled"])
def test_unknown_and_closed_task_refused(location, vault, capsys, closed):
    config, _ = location
    if closed:
        tasks.close(config, "T-001", TODAY, closed == "completed", "evidence")
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        cli(vault, capsys, "T-001" if closed else "T-999", "set", "--status", "new")
    assert error.value.code == 1
    assert "not an open task" in capsys.readouterr().err
    assert snapshot(vault) == before


def test_new_parses_appends_sources_json_and_shared_id_allocation(vault, capsys):
    config = Config.load(vault)
    tasks.write_archive(config, tasks.Task("T-041", "Old", "", {"state": "completed"}), "cold", TODAY)
    fm.write(vault / "References.md", {}, "Previously mentioned T-057.\n")   # a mention uses no id up
    assert tasks.next_id(config) == "T-042"
    assert cli(vault, capsys, "new", "Prepare notes", "--source", "Please prepare the notes",
               "--status", "starting", "--next", "read", "--delivers", "a summary", "--hub", "START",
               "--work", "notes", "--state", "In progress") == "T-042 created: Prepare notes"
    task = next(task for task in entry(config) if task.tid == "T-042")
    assert (task.title, task.task_state, task.work, task.hub, task.delivers) == ("Prepare notes", "in_progress", "notes", "START", "a summary")
    assert '- **Source:** "Please prepare the notes"' in task.body and task.handoff is None
    assert "- **Next:** read\n" in task.body
    assert tasks.parse_register(task.render())[1][0].render() == task.render()
    output = cli(vault, capsys, "new", "Another", "--source", "memory/START.md", "--json")
    assert json.loads(output) == {"id": "T-043", "title": "Another"}
    task = next(task for task in entry(config) if task.tid == "T-043")
    assert task.task_state == "ready" and "- **Source:** [[START]]" in task.body
    # Accepted ideas use precisely the same allocator and append path.
    ideas.add(config, TODAY, "Organize notes?", "START", "Save time")
    ideas.respond(config, "Organize notes?", TODAY, "yes")
    assert next(task for task in entry(config) if task.tid == "T-044").handoff == (TODAY.isoformat(), "Home")


@pytest.mark.parametrize("args,message", [
    ([], "needs --source"), (["--source", "words", "--state", "blocked"], "ready or in_progress"),
    (["--source", "words", "--state", ""], "ready or in_progress"),
    (["--source", "words", "--work", "Bad slug"], "work slug"),
    (["--source", "words", "--hub", "Missing"], "note does not exist"),
    (["--source", "words\nmore"], "single-line"),
    (["--source", "words", "--next", "line\nbreak"], "single-line"),
    (["--source", "words", "--verified", "no"], "unsupported new task field"),
])
def test_new_invalid_does_not_write(vault, capsys, args, message):
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        cli(vault, capsys, "new", "Title", *args)
    assert error.value.code == 1 and message in capsys.readouterr().err
    assert snapshot(vault) == before


def test_new_refuses_exhausted_ids(vault, capsys):
    # Only a real task uses an id up: a T-999 task heading in the register exhausts them, a mention never does.
    register = vault / Config.load(vault).agent("Tasks.md")
    data, body = fm.read(register)
    fm.write(register, data, body.rstrip("\n") + "\n\n### T-999 — The last one\n<!-- brain: state=ready -->\n- **Status:** x\n")
    with pytest.raises(SystemExit) as error:
        cli(vault, capsys, "new", "Title", "--source", "words")
    assert error.value.code == 1 and "no unused T-nnn" in capsys.readouterr().err


@pytest.mark.parametrize("command", [("handoff", "T-001"), ("task", "T-001", "done", "--by-owner"),
                                     ("task", "T-001", "drop", "--by-owner"), ("task", "T-001", "yours"),
                                     ("task", "T-001", "set", "--status", "updated"),
                                     ("task", "new", "Title", "--source", "words")])
def test_cli_holds_nonreentrant_vault_lock_across_read_write(vault, monkeypatch, capsys, command):
    config = Config.load(vault)
    held, entered = False, 0

    @contextmanager
    def lock(cfg):
        nonlocal held, entered
        assert cfg == config and not held
        held = True
        entered += 1
        try:
            yield
        finally:
            held = False

    def guarded(original):
        def call(*args, **kwargs):
            assert held
            return original(*args, **kwargs)
        return call

    monkeypatch.setattr(brain_cli, "vault_lock", lock)
    for name in ("load_tasks", "save_task", "next_id", "append_task"):
        monkeypatch.setattr(tasks, name, guarded(getattr(tasks, name)))
    brain_cli.main([*command, "--vault", str(vault)])
    capsys.readouterr()
    assert entered == 1 and not held


def test_replacing_a_field_keeps_notes_and_sections_after_it():
    from brain.tasks import _replace_field
    body = ("- **Status:** old\n  - detail of old\n- **Next:** go\n\nA paragraph of notes.\n\n"
            "#### November 5 checkpoint\n- **Status:** historical, inside a section? no: top-level\n- **Verified:** x\n")
    out = _replace_field(body, "next", "new step")
    assert "A paragraph of notes." in out and "#### November 5 checkpoint" in out and "- **Next:** new step\n" in out
    out = _replace_field(body, "verified", None)
    assert "- **Verified:**" not in out and "A paragraph of notes." in out
    body2 = "- **Status:** old\n  - detail\n\nNotes after.\n"
    assert _replace_field(body2, "status", "new") == "- **Status:** new\n\nNotes after.\n"


def test_set_reviewed_appends_a_dated_line_each_time(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "set", "--reviewed", "asked: the deadline is live")
    cli(vault, capsys, "T-001", "set", "--state", "set_aside", "--reviewed", "set aside: nothing at stake")
    body = entry(config)[0].body
    assert body.count("- **Reviewed:** ") == 2 and "set aside: nothing at stake" in body
    assert entry(config)[0].task_state == "set_aside"


def test_resume_rests_a_task_until_its_time_and_report_changes_only_the_report(vault, capsys, monkeypatch):
    from datetime import datetime
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", TODAY, "Home")
    tasks.set_fields(config, "T-001", TODAY, values={"delivers": "notes in Home"})
    cli(vault, capsys, "T-001", "set", "--resume", "2026-11-06 08:05")
    task, = entry(config)
    assert task.resume == "2026-11-06 08:05"
    assert tasks.due([task], now="2026-11-05 22:00") == []
    assert tasks.due([task], now="2026-11-06 08:05") == [task]
    assert tasks.due([task]) == [task]
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "set", "--resume", "tomorrow morning")
    cli(vault, capsys, "T-001", "set", "--report", "demiurge:jn7thread")
    task, = entry(config)
    assert task.handoff == (TODAY.isoformat(), "demiurge:jn7thread") and task.task_state == "in_progress"


def test_resume_computer_round_trips_through_task_cli(location, vault, capsys):
    config, _ = location
    assert cli(vault, capsys, "T-001", "set", "--resume", "computer") == "T-001 updated: resume"
    assert entry(config)[0].resume == "computer"
    assert "- **Resume:** computer" in entry(config)[0].body
    cli(vault, capsys, "T-001", "set", "--clear", "resume")
    assert entry(config)[0].resume is None


@pytest.mark.parametrize("resume", ["tomorrow", "Computer", "network", "computer online", "", "2026-99-99 29:90",
                                    "2026-02-30", "2026-11-06 24:00"])
def test_invalid_resume_words_refused_without_writes(vault, capsys, resume):
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        cli(vault, capsys, "T-001", "set", "--resume", resume)
    assert error.value.code == 1
    assert "resume must be" in capsys.readouterr().err
    assert snapshot(vault) == before


def test_morning_lines_append_and_clear_together(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "set", "--morning", "check the 8am text")
    cli(vault, capsys, "T-001", "set", "--morning", "mention the new deck")
    assert entry(config)[0].body.count("- **morning:** ") == 2
    cli(vault, capsys, "T-001", "set", "--clear", "morning")
    assert "morning:" not in entry(config)[0].body


def test_new_task_goes_before_standing_state(vault, capsys):
    config = Config.load(vault)
    register = vault / config.agent("Tasks.md")
    data, body = fm.read(register)
    fm.write(register, data, body.rstrip("\n") + "\n\n## Standing coordination state\n\n- Permission to read mail.\n")
    cli(vault, capsys, "new", "Fresh work", "--source", "asked today")
    text = fm.read(register)[1]
    assert text.index("Fresh work") < text.index("## Standing coordination state")


def test_writes_are_atomic_and_validate_reports_nul_bytes(vault):
    from brain import validate
    from brain.vault import Vault
    path = vault / "Atomic.md"
    fm.write(path, {"kind": "hub", "tier": "warm"}, "# Atomic\n")
    assert not list(vault.glob(".Atomic.md.*.tmp"))
    path.write_bytes(path.read_bytes() + b"\x00\x00")
    assert any("Atomic.md: contains 2 NUL bytes" in p for p in validate.checks(Vault(vault)))


def test_brain_commit_stages_scoped_paths_under_the_repo_lock(vault, capsys, monkeypatch):
    import subprocess
    repo = Config.load(vault).repo
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    if not (repo / ".git").exists():
        git("init", "-q"); git("config", "user.email", "t@e.x"); git("config", "user.name", "t")
        git("add", "-A"); git("commit", "-qm", "base")
    held = []
    real = brain_cli.repo_lock
    def watching(config):
        held.append(config.repo)
        return real(config)
    monkeypatch.setattr(brain_cli, "repo_lock", watching)
    (vault / "One.md").write_text("one\n"); (vault / "Two.md").write_text("two\n"); (vault / "Staged.md").write_text("s\n")
    git("add", str(vault / "Staged.md"))
    brain_cli.main(["commit", "-m", "test: one only", str(vault / "One.md"), "--vault", str(vault)])
    assert capsys.readouterr().out.startswith("committed ")
    assert held == [repo]
    shown = git("show", "--name-only", "--format=%s", "HEAD")
    assert "test: one only" in shown and "One.md" in shown and "Two.md" not in shown and "Staged.md" not in shown
    assert "Staged.md" in git("diff", "--cached", "--name-only")
    brain_cli.main(["commit", "-m", "nothing", str(vault / "One.md"), "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "nothing to commit"


def test_a_note_mentioning_t999_does_not_use_up_task_ids(vault, capsys):
    (vault / "Eval notes.md").write_text("# Eval notes\n\nThe case seeds T-999 and T-998 as fixtures.\n")
    out = cli(vault, capsys, "new", "Help Hope explore job opportunities", "--source", "asked today")
    assert out.startswith("T-") and "T-1000" not in out and "T-999" not in out


def test_completed_lists_the_weeks_finished_tasks_newest_first(vault, capsys):
    import json
    from datetime import timedelta
    config = Config.load(vault)
    cli(vault, capsys, "new", "Assemble Hope's résumé", "--source", "help Hope with her résumé")
    tid = entry(config)[-1].tid
    tasks.close(config, tid, TODAY, True, "the résumé is assembled")
    old = cli(vault, capsys, "new", "Old work", "--source", "long ago").split()[0]
    tasks.close(config, old, TODAY - timedelta(days=30), True, "done long ago")
    def completed(*args):
        brain_cli.main(["completed", *args, "--vault", str(vault)])
        return capsys.readouterr().out
    rows = json.loads(completed("--json"))
    assert [row["id"] for row in rows] == [tid]
    assert rows[0]["evidence"] == "the résumé is assembled" and rows[0]["source"] == '"help Hope with her résumé"'
    assert completed("--days", "60").count("· completed") == 2


def test_brain_commit_works_before_the_vault_has_a_compiled_context(vault, capsys):
    import subprocess
    repo = Config.load(vault).repo
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("init", "-q"); git("config", "user.email", "t@e.x"); git("config", "user.name", "t")
    assert not (repo / ".context.md").exists()
    (vault / "One.md").write_text("one\n")
    brain_cli.main(["commit", "-m", "seed(first)", "--vault", str(vault)])
    assert capsys.readouterr().out.startswith("committed ")
    assert "One.md" in git("show", "--name-only", "--format=%s", "HEAD")
