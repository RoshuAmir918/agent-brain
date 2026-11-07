"""Goals belong to the person, and give each piece of unasked work a visible reason."""
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from brain import cli, compile_context, fm, goals, ideas, state, tasks, validate, works
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def session(monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    for name in ("AGENT_HOME", "BRAIN_RUN", "DEMIURGE_TASK", "DEMIURGE_CONVERSATION", "BRAIN_SPEAKER"):
        monkeypatch.delenv(name, raising=False)


def run(vault, capsys, *args):
    cli.main([*args, "--vault", str(vault)])
    return capsys.readouterr().out


def note(vault, rel, body, **data):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"tier": "warm", **data}, body)
    return path


def test_format_round_trip_free_sections_and_archive(vault):
    cfg = Config.load(vault)
    path = note(vault, cfg.human_goals, goals.TEMPLATE.replace("## Aspirations", "A preamble.\n\n## Aspirations\nMaybe piano.")
                .replace("## Adopted practices", "## Adopted practices\nRead at breakfast.")
                .replace("## Set aside", "## Set aside\nAn old free-text wish."), kind="orientation")
    goal = goals.new(cfg, "Marathon", "Sub-3, 2:55 as a stretch", reach=True, by="2027-04-20")
    assert goals.parse_register(goal.render())[1][0].render() == goal.render()
    assert goal.public() == {"id": "G-1", "title": "Marathon", "kind": "reach", "state": "active",
                             "by": "2027-04-20", "words": "Sub-3, 2:55 as a stretch", "saidAt": "2026-11-07",
                             "evidence": [], "links": {key: [] for key in ("tasks", "works", "drafts", "ideas", "hubs")}}
    for count in range(12):
        at = (TODAY - timedelta(days=11-count)).isoformat()
        goals.change(cfg, goal.gid, "evidence", text=f"{at} · Run {count} · [[Training log]]")
    goal = goals.load(cfg)[0]
    assert len(goal.evidence) == 10
    assert goal.evidence[0] == {"at": "2026-11-07", "text": "Run 11", "link": "Training log"}
    assert goal.evidence[-1]["text"] == "Run 2"
    archive = (vault / cfg.agent("Goals Archive/G-1.md")).read_text()
    assert "Run 0" in archive and "Run 1" in archive and "Run 2" not in archive
    assert Vault(vault).kind_for(cfg.agent("Goals Archive/G-1.md")) == "log"
    assert all(hub["title"] != "G-1" for hub in state.build(cfg, TODAY)["hubs"])
    assert "Earlier evidence" in goal.body
    assert all(text in path.read_text() for text in ("Maybe piano.", "Read at breakfast.", "An old free-text wish.", "A preamble."))
    assert not goals.problems(cfg)


def test_every_owner_command_uses_the_contract_argv(vault, capsys):
    assert "G-1" in run(vault, capsys, "goals", "new", "Race", "--words", "Run sub-3", "--reach", "--by", "2027-04-20", "--from-app")
    run(vault, capsys, "goals", "G-1", "edit", "--title", "Spring race", "--words", "Run well", "--clear-by", "--from-app")
    run(vault, capsys, "goals", "G-1", "edit", "--by", "2027-05-01", "--from-app")
    assert "G-1 — Spring race" in run(vault, capsys, "goals", "show", "G-1")
    run(vault, capsys, "goals", "G-1", "evidence", "2026-11-06 · Long run")
    assert "Long run" in run(vault, capsys, "goals", "list", "--json")
    cfg = Config.load(vault)
    for command, expected, heading in (("reached", "reached", "Reached"), ("reopen", "active", "Active goals"),
                                       ("set-aside", "set_aside", "Set aside"), ("reopen", "active", "Active goals")):
        run(vault, capsys, "goals", "G-1", command, "--why", "The person said so", "--from-app")
        goal = goals.load(cfg)[0]
        assert goal.state["state"] == expected and "The person said so" in goal.body
        body = goals.read(cfg)[1]
        assert "### G-1" in body.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    assert json.loads(run(vault, capsys, "goals", "list", "--json"))[0]["id"] == "G-1"
    assert "G-1" in run(vault, capsys, "goals", "list")
    assert "G-2" in run(vault, capsys, "goals", "new", "Read widely", "--words", "Read widely")
    run(vault, capsys, "goals", "G-2", "edit", "--by", "2027-06-01", "--from-app")
    assert next(goal for goal in goals.load(cfg) if goal.gid == "G-2").state["kind"] == "reach"
    assert not goals.problems(cfg)


@pytest.mark.parametrize("args", [
    ("new", "Race", "--words", "Race", "--by", "2027-01-01"),
    ("new", "Race", "--words", "Race", "--reach", "--by", "2027-02-30"),
    ("new", "A\nB", "--words", "Race"), ("new", "x"*81, "--words", "Race"),
    ("new", "Race", "--words", "x"*501), ("new", "Race"),
    ("show", "G-99"), ("G-99", "edit", "--title", "Nothing"), ("migrate",),
])
def test_bad_commands_refuse_without_writing(vault, capsys, args):
    before = snapshot(vault)
    with pytest.raises(SystemExit):
        run(vault, capsys, "goals", *args)
    assert snapshot(vault) == before


@pytest.mark.parametrize("edit,expected", [
    (lambda text: text.replace("state=active", "state=unknown"), "unknown goal state"),
    (lambda text: text.replace("kind=become", "kind=instruction"), "kind must be"),
    (lambda text: text.replace("### G-1 —", "### G-1:"), "malformed goal heading"),
    (lambda text: text.replace('"Read widely"', '"Hand edit"'), "hand-edited goal"),
    (lambda text: text.replace("2026-11-07", "2026-02-30"), "invalid date"),
    (lambda text: text + text[text.index("### G-1"):].split("## Adopted", 1)[0], "duplicate goal id"),
])
def test_validate_rejects_bad_blocks_and_hand_edits(vault, capsys, edit, expected):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read widely")
    path = vault / cfg.human_goals
    path.write_text(edit(path.read_text()))
    assert any(expected in problem for problem in validate.goal_problems(Vault(vault)))
    with pytest.raises(SystemExit) as error:
        run(vault, capsys, "validate")
    assert error.value.code == 1
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("gid", ["G-999", "G-2", "G-3"])
def test_links_refuse_unknown_reached_and_set_aside(vault, capsys, tmp_path, monkeypatch, gid):
    cfg = Config.load(vault)
    for title in ("Reading", "Race", "Piano"):
        goals.new(cfg, title, title)
    goals.change(cfg, "G-2", "reached", why="Finished")
    goals.change(cfg, "G-3", "set-aside", why="Later")
    note(vault, "Reading.md", "# Reading", kind="hub")
    note(vault, "agent/Prepared/Plan.md", "# Plan", kind="draft")
    home = tmp_path / "home"
    (home / "works/reading").mkdir(parents=True)
    monkeypatch.setenv("AGENT_HOME", str(home))
    for args in (("task", "new", "Find books", "--source", "Reading", "--goal", gid),
                 ("task", "T-001", "set", "--goal", gid),
                 ("ideas", "--add", "Read together", "--source", "Reading", "--goal", gid),
                 ("works", "goal", "reading", "--goal", gid),
                 ("goals", gid, "link", "Reading"), ("goals", gid, "link", "agent/Prepared/Plan")):
        with pytest.raises(SystemExit) as error:
            run(vault, capsys, *args)
        assert "unknown or not active" in str(error.value) or "unknown or not active" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["routine", "worker", "task", "cron", "subagent"])
@pytest.mark.parametrize("kind", ["task", "idea"])
def test_routines_need_a_goal_or_the_owner_quoted(vault, capsys, tmp_path, monkeypatch, mode, kind):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read widely")
    note(vault, "Reading.md", "# Reading", kind="hub")
    if mode in ("routine", "worker"):
        monkeypatch.setenv("BRAIN_RUN", mode)
        monkeypatch.setenv("AGENT_HOME", str(tmp_path))
        monkeypatch.setenv("DEMIURGE_CONVERSATION", "thread")  # explicit markers take precedence
    elif mode == "task":
        monkeypatch.setenv("DEMIURGE_TASK", "T-999")  # not a task: a worker's follow-ups pass only through the task it runs
        monkeypatch.setenv("DEMIURGE_CONVERSATION", "thread")
    else:
        monkeypatch.setenv("BRAIN_SPEAKER", mode + ":run")
    args = ("task", "new", "Find books") if kind == "task" else ("ideas", "--add", "Find books")
    with pytest.raises(SystemExit) as error:
        run(vault, capsys, *args, "--source", "Reading")
    message = str(error.value) + capsys.readouterr().err
    assert "No goal, no unasked work" in message and "G-1 — Reading" in message
    run(vault, capsys, *args, "--source", "Reading", "--goal", "G-1")
    # Owner-asked work in a worker/routine is allowed without a goal.
    if kind == "task":
        run(vault, capsys, "task", "new", "Owner request", "--source", '"Please find books"')
    else:
        monkeypatch.setattr(ideas, "ADD_SPACING_NEW", 0)
        run(vault, capsys, "ideas", "--add", "Owner request", "--source", '"Please suggest books"')


def test_no_active_goals_and_conversation_unaffected(vault, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("BRAIN_RUN", "routine")
    for args in (("task", "new", "Surprise"), ("ideas", "--add", "Surprise")):
        with pytest.raises(SystemExit) as error:
            run(vault, capsys, *args, "--source", "Some note")
        message = str(error.value) + capsys.readouterr().err
        assert "has no active goals" in message and "Nothing unasked should be made" in message
    monkeypatch.delenv("BRAIN_RUN")  # AGENT_HOME alone is a conversation.
    assert "created" in run(vault, capsys, "task", "new", "Requested work", "--source", "Please do this")
    assert "added idea" in run(vault, capsys, "ideas", "--add", "Requested idea", "--source", "Conversation")


def test_no_source_refusal_teaches_and_organisation_duties_need_no_personal_goal(vault, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("BRAIN_RUN", "routine")
    for args in (("task", "new", "Unasked"), ("ideas", "--add", "Unasked")):
        with pytest.raises(SystemExit) as error:
            run(vault, capsys, *args)
        assert "has no active goals" in str(error.value) + capsys.readouterr().err
    cfg = replace(Config.load(vault), edition="enterprise")
    assert tasks.new_task(cfg, "Assigned work", "Organisation duty").goal is None


def test_export_contract_links_privacy_and_accepted_idea(vault, capsys, tmp_path, monkeypatch):
    cfg = Config.load(vault)
    goals.new(cfg, "Marathon", "Run sub-3", reach=True, by="2027-04-20")
    for count in range(6):
        goals.change(cfg, "G-1", "evidence", text=f"2026-11-0{count+1} · Run {count} · [[Training]]")
    note(vault, "Training.md", "# Training", kind="hub")
    note(vault, "agent/Prepared/Taper week.md", "# Taper week", kind="draft")
    note(vault, "Drafts/Race day.md", "# Race day", kind="draft")
    note(vault, "Hidden.md", "# Hidden\n- **Goal:** G-1", kind="hub", tier="private")
    run(vault, capsys, "goals", "G-1", "link", "Training")
    run(vault, capsys, "goals", "G-1", "link", "agent/Prepared/Taper week")
    run(vault, capsys, "goals", "G-1", "link", "Drafts/Race day")
    run(vault, capsys, "task", "T-001", "set", "--goal", "G-1", "--work", "marathon-plan")
    run(vault, capsys, "task", "new", "Intervals", "--source", "Training", "--goal", "G-1")
    run(vault, capsys, "ideas", "--add", "Training partner", "--source", "Training", "--goal", "G-1")
    home = tmp_path / "home"
    (home / "works/race-notes").mkdir(parents=True)
    (home / "works/LIBRARY.md").write_text("# Library\n- Notes | race-notes | document | active | editable | unknown\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    run(vault, capsys, "works", "goal", "race-notes", "--goal", "G-1")
    exported = json.loads(run(vault, capsys, "state", "--json"))
    goal = exported["goals"][0]
    assert set(goal) == {"id", "title", "kind", "state", "by", "words", "saidAt", "evidence", "links"}
    assert len(goal["evidence"]) == 5 and goal["evidence"][0] == {"at": "2026-11-06", "text": "Run 5", "link": "Training"}
    assert goal["links"] == {"tasks": ["T-001", "T-002"], "works": ["marathon-plan", "race-notes"],
                              "drafts": ["Drafts/Race day", "agent/Prepared/Taper week"], "ideas": [exported["idea"]["id"]], "hubs": ["Training"]}
    assert exported["idea"]["goal"] == "G-1" and all(task["goal"] == "G-1" for task in exported["tasks"])
    run(vault, capsys, "ideas", "--respond", exported["idea"]["id"], "--answer", "yes")
    assert tasks.all_tasks(cfg)[-1].goal == "G-1"
    goals.change(cfg, "G-1", "reached", why="Ran it")
    assert state.build(cfg, TODAY)["goals"][0]["state"] == "reached"
    assert not validate.goal_problems(Vault(vault))  # Historical links survive reaching a goal.
    enterprise = replace(cfg, edition="enterprise")
    assert state.build(enterprise, TODAY)["goals"] == []
    assert all(task["goal"] is None for task in state.build(enterprise, TODAY)["tasks"])


def test_app_changes_reach_context_once_not_consumed_by_nightly_or_export(vault, capsys):
    cfg = Config.load(vault)
    run(vault, capsys, "goals", "new", "Reading", "--words", "Read widely", "--from-app")
    goals.change(cfg, "G-1", "evidence", text="2026-11-06 · Read a novel")
    expected = f"{cfg.human_name} added this in the app: G-1 — Reading"
    assert expected in compile_context.build(Vault(vault))
    compile_context.write(Vault(vault))  # Nightly refresh is not a session consuming the notice.
    state.build(cfg, TODAY)
    run(vault, capsys, "compile-context", "--sections", "current")
    assert goals.capture(cfg)["app"]
    run(vault, capsys, "compile-context", "--write")
    context = (cfg.repo / ".context.md").read_text()
    assert expected not in context and "Latest evidence: 2026-11-06 · Read a novel" in context
    assert goals.capture(cfg)["app"]  # The cached file is reused; notices belong to a single session.
    run(vault, capsys, "session-context", "--platform", "demiurge", "--user-id", "unknown")
    assert goals.capture(cfg)["app"]
    before = snapshot(vault)
    assert expected in run(vault, capsys, "session-context", "--platform", "cli")
    assert expected in run(vault, capsys, "session-context", "--platform", "cli")
    assert snapshot(vault) == before
    assert expected in run(vault, capsys, "session-context", "--platform", "cli", "--ack-notices")
    assert expected not in run(vault, capsys, "session-context", "--platform", "cli", "--ack-notices")
    run(vault, capsys, "goals", "G-1", "edit", "--words", "Read broadly", "--from-app")
    assert "G-1 — Reading" in (cfg.repo / ".context.md").read_text()
    assert "changed this in the app" in run(vault, capsys, "compile-context", "--sections", "goals")
    assert "changed this in the app" not in run(vault, capsys, "compile-context", "--sections", "goals")


def test_capture_asked_once_even_without_an_answer_and_nudge_operations(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")
    assert goals.QUESTION in run(vault, capsys, "goals", "asked")
    assert goals.QUESTION not in run(vault, capsys, "goals", "asked")
    cfg = Config.load(vault)
    assert "claim with `brain goals asked`" not in goals.context(cfg)
    monkeypatch.setenv("BRAIN_TODAY", "2026-08-01")
    goals.new(cfg, "Reading", "Read widely")
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    run(vault, capsys, "goals", "G-1", "nudge")
    assert goals.load(cfg)[0].state["nudged"] == "2026-11-07"
    with pytest.raises(SystemExit):
        run(vault, capsys, "goals", "G-1", "nudge")
    goals.change(cfg, "G-1", "evidence", text="Still a goal")
    assert "nudged" not in goals.load(cfg)[0].state


@pytest.mark.parametrize("fixture", ["seeded-template.md", "none.md"])
def test_migrate_empty_active_goals_writes_nothing(vault, capsys, monkeypatch, fixture):
    cfg = Config.load(vault)
    path = vault / cfg.human_goals
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((Path(__file__).parent / "fixtures/goals" / fixture).read_bytes())
    before = snapshot(vault)
    def refuse_lock(*args):
        pytest.fail("a dry run must not acquire a write lock")
    with monkeypatch.context() as preview:
        preview.setattr(cli, "vault_lock", refuse_lock)
        assert run(vault, capsys, "goals", "migrate", "--dry-run") == "no legacy content in Active goals; nothing to migrate\n"
    assert run(vault, capsys, "goals", "migrate", "--apply") == "no legacy content in Active goals; nothing to migrate\n"
    assert not goals.load(cfg) and snapshot(vault) == before


@pytest.mark.parametrize("fixture", ["roshan.md", "rohan.md"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_migrate_archives_original_clears_only_active_goals_and_is_idempotent(vault, capsys, fixture, newline):
    cfg = Config.load(vault)
    path = vault / cfg.human_goals
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (Path(__file__).parent / "fixtures/goals" / fixture).read_text()
    original = "---\nkind: orientation\ntier: hot\npinned: true\n---\n" + original
    original = original.replace("\n", newline).encode()
    path.write_bytes(original)
    before = snapshot(vault)
    archive_rel = cfg.agent("Goals Archive/Migration.md")
    report = f"legacy content in Active goals; would archive to {archive_rel}\n"
    assert run(vault, capsys, "goals", "migrate", "--dry-run") == report
    assert run(vault, capsys, "goals", "migrate", "--dry-run") == report
    assert snapshot(vault) == before
    assert run(vault, capsys, "goals", "migrate", "--apply") == f"archived original to {archive_rel}; Active goals cleared\n"
    archive = vault / archive_rel
    prefix = fm.join({"kind": "log", "tier": "cold"}, "").encode()
    assert archive.read_bytes() == prefix + original
    assert Vault(vault).kind_for(archive_rel) == "log"
    head, rest = original.split(b"## Active goals" + newline.encode(), 1)
    _, tail = rest.split(b"## Adopted practices", 1)
    assert path.read_bytes() == head + b"## Active goals" + newline.encode() + goals.ACTIVE_DESCRIPTION.encode() + newline.encode() * 2 + b"## Adopted practices" + tail
    assert not goals.load(cfg) and not goals.problems(cfg)
    assert snapshot(vault).keys() == before.keys() | {archive_rel}
    assert all(snapshot(vault)[rel] == digest for rel, digest in before.items() if rel != cfg.human_goals)
    after = snapshot(vault)
    assert run(vault, capsys, "goals", "migrate", "--apply") == "no legacy content in Active goals; nothing to migrate\n"
    assert snapshot(vault) == after


def test_hub_link_keeps_each_proposals_own_goal(vault):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read widely")
    goals.new(cfg, "Depth", "Engineering depth")
    path = note(vault, "Library.md", "# Library\n\n## Improvements\n### A systems shelf\n- **Goal:** G-2\n- **Status:** proposed\n", kind="hub")
    goals.link_note(cfg, "G-1", "Library")
    body = fm.read(path)[1]
    assert "- **Goal:** G-1" in body.split("## Improvements", 1)[0]
    assert "- **Goal:** G-2" in body.split("## Improvements", 1)[1]


def test_a_workers_follow_up_inherits_the_task_it_works_on(vault, capsys, monkeypatch):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read widely")
    created = lambda title: next(t for t in tasks.load_tasks(cfg)[0] if t.title == title)
    run(vault, capsys, "task", "new", "Reading list", "--source", '"Make me a reading list"', "--goal", "G-1")
    run(vault, capsys, "task", "new", "Fix the bike", "--source", '"Fix my bike"')
    monkeypatch.setenv("DEMIURGE_TASK", created("Reading list").tid)
    run(vault, capsys, "task", "new", "Order the books", "--source", "Reading list")
    assert created("Order the books").goal == "G-1"
    monkeypatch.setenv("DEMIURGE_TASK", created("Fix the bike").tid)
    run(vault, capsys, "task", "new", "Buy a chain", "--source", "Fix the bike")
    assert created("Buy a chain").goal is None
    monkeypatch.setenv("DEMIURGE_TASK", "T-999")
    with pytest.raises(SystemExit):
        run(vault, capsys, "task", "new", "Unrelated", "--source", "Some note")


def test_words_keep_the_day_they_were_said(vault, capsys):
    out = run(vault, capsys, "goals", "new", "Reading", "--words", "Read widely", "--said", "2026-10-26")
    assert "G-1" in out
    cfg = Config.load(vault)
    assert goals.load(cfg)[0].said_at == "2026-10-26"
    run(vault, capsys, "goals", "G-1", "edit", "--said", "2026-10-20")
    goal = goals.load(cfg)[0]
    assert goal.said_at == "2026-10-20" and goal.words == "Read widely"
    run(vault, capsys, "goals", "G-1", "edit", "--words", "Read more widely")
    assert goals.load(cfg)[0].said_at == TODAY.isoformat()
    with pytest.raises(SystemExit) as error:
        run(vault, capsys, "goals", "new", "Later", "--words", "x", "--said", "2027-01-01")
    assert "can't be in the future" in str(error.value)
    with pytest.raises(SystemExit):
        run(vault, capsys, "goals", "G-1", "evidence", "2026-11-01 · read", "--said", "2026-10-01")
