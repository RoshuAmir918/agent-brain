"""Review regressions, including the six reproduction scripts and seeded template."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import re

import pytest

from brain import cli, fm, goals, state, tasks, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 7)
FIXTURES = Path(__file__).parent / "fixtures/goals"


@pytest.fixture(autouse=True)
def session(monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    for name in ("AGENT_HOME", "BRAIN_RUN", "DEMIURGE_TASK", "DEMIURGE_CONVERSATION", "BRAIN_SPEAKER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")


def seed(vault, body):
    cfg = Config.load(vault)
    path = vault / cfg.human_goals
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return cfg, path


def run(vault, capsys, *args):
    cli.main([*args, "--vault", str(vault)])
    return capsys.readouterr().out


def test_t1_real_template_first_and_second_new(vault):
    cfg, path = seed(vault, (FIXTURES / "seeded-template.md").read_text())
    before = fm.read(path)[1]
    goals.new(cfg, "Marathon", "Run sub-3")
    assert not goals.problems(cfg)
    goals.new(cfg, "Read", "Read more")
    assert not goals.problems(cfg)
    body = goals.read(cfg)[1]
    for marker in ("What the owner has chosen", "### [Goal]", "- **What and why:** [their words, dated]"):
        assert marker in body and body.index(marker) < body.index("### G-1")
    for _, start, end in reversed(goals.parse_register(body)[0]):
        body = body[:start] + body[end:]
    assert re.sub(r"\n{3,}", "\n\n", body).rstrip() == before.rstrip()


def test_t1_set_aside_keeps_legacy_lines_outside_blocks(vault):
    legacy = "Goals with no evidence for ninety days.\n\n- Learn Rust (2026-08-01)\n"
    cfg, path = seed(vault, goals.TEMPLATE.replace("## Set aside\n", "## Set aside\n" + legacy))
    goals.new(cfg, "Marathon", "Run sub-3")
    goals.change(cfg, "G-1", "set-aside", why="injured")
    assert legacy in path.read_text()
    assert path.read_text().index("Learn Rust") < path.read_text().index("### G-1")
    assert not goals.problems(cfg)
    assert legacy.strip() not in goals.load(cfg)[0].body


def test_free_text_between_and_after_blocks_never_changes_digests_or_moves(vault):
    cfg, path = seed(vault, goals.TEMPLATE)
    goals.new(cfg, "One", "One")
    goals.new(cfg, "Two", "Two")
    body = path.read_text().replace("### G-2", "None.\n\n### G-2").replace("## Adopted practices", "Section epilogue.\n\n## Adopted practices")
    path.write_text(body)
    assert not goals.problems(cfg)
    goals.new(cfg, "Three", "Three")
    assert path.read_text().index("### G-2") < path.read_text().index("### G-3") < path.read_text().index("Section epilogue.")
    goals.change(cfg, "G-1", "set-aside", why="Later")
    assert "None." in path.read_text().split("## Active goals", 1)[1].split("## Adopted", 1)[0]
    assert all("None." not in goal.body and "Section epilogue." not in goal.body for goal in goals.load(cfg))
    assert not goals.problems(cfg)


@pytest.mark.parametrize("field", ["title", "words"])
@pytest.mark.parametrize("marker", ["-->", "<!--"])
@pytest.mark.parametrize("operation", ["new", "edit"])
def test_t1_comment_delimiters_refuse_with_teaching_message(vault, field, marker, operation):
    cfg, _ = seed(vault, goals.TEMPLATE)
    goals.new(cfg, "Reading", "Read more")
    before = snapshot(vault)
    values = {"title": "Reading", "words": "Read more", field: "Learn A " + marker + "B"}
    with pytest.raises(ValueError, match="HTML comment.*plain text"):
        if operation == "new":
            goals.new(cfg, **values)
        else:
            goals.change(cfg, "G-1", "edit", **values)
    assert snapshot(vault) == before


def test_t6_contract_argv_special_titles_words_and_edit(vault, capsys):
    titles = ["G-1", "list", 'Run 5k: sub-20 "fast" (2026-01-01)', "Lire plus — été ☀️ 日本語", "x" * 80, "edit", 'He said "go" (unknown)']
    for title in titles:
        run(vault, capsys, "goals", "new", title, "--words", 'words "q" (2026-01-01)', "--from-app")
    run(vault, capsys, "goals", "G-2", "edit", "--title", "G-9", "--words", 'new "w"', "--from-app")
    run(vault, capsys, "goals", "G-3", "reached", "--why", "G-1: done; ok", "--from-app")
    cfg = Config.load(vault)
    entries = {goal.gid: goal for goal in goals.load(cfg)}
    assert entries["G-2"].title == "G-9" and entries["G-2"].words == 'new "w"'
    assert entries["G-3"].state["state"] == "reached"
    assert entries["G-1"].words == 'words "q" (2026-01-01)'
    assert not goals.problems(cfg)


@pytest.mark.parametrize("platform,user_id,visible", [("cli", "", True), ("cron", "", True), ("demiurge", "visitor", False)])
def test_session_context_preview_has_no_writes_or_lock_and_ack_is_explicit(vault, capsys, monkeypatch, platform, user_id, visible):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read more", from_app=True)
    before = snapshot(vault)
    args = ("session-context", "--platform", platform, "--user-id", user_id)
    def refuse_lock(*args):
        pytest.fail("a read-only session-context must not open or create a write lock")
    with monkeypatch.context() as preview:
        preview.setattr(cli, "vault_lock", refuse_lock)
        for _ in range(2):
            assert ("added this in the app" in run(vault, capsys, *args)) == visible
    assert snapshot(vault) == before and goals.capture(cfg)["app"]
    assert ("added this in the app" in run(vault, capsys, *args, "--ack-notices")) == visible
    assert bool(goals.capture(cfg)["app"]) == (not visible)


@pytest.mark.parametrize("mode", ["routine", "worker"])
def test_run_marker_guards_without_agent_home(vault, monkeypatch, mode):
    monkeypatch.setenv("BRAIN_RUN", mode)
    with pytest.raises(ValueError, match="No goal, no unasked work"):
        tasks.new_task(Config.load(vault), "Unasked", "Some note")


@pytest.mark.parametrize("goal_state", ["active", "reached", "set-aside", "missing"])
def test_worker_follow_up_inherits_only_active_goal(vault, monkeypatch, goal_state):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read more")
    parent = tasks.new_task(cfg, "Reading list", '"Make a list"', goal="G-1")
    if goal_state == "missing":
        (vault / cfg.human_goals).unlink()
    elif goal_state != "active":
        goals.change(cfg, "G-1", goal_state, why="Done for now")
    monkeypatch.setenv("DEMIURGE_TASK", parent.tid)
    follow_up = tasks.new_task(cfg, "Order the books", "Reading list")
    assert follow_up.goal == ("G-1" if goal_state == "active" else None)


@pytest.mark.parametrize("hub_goal", [None, "G-1", "G-999"])
def test_hub_export_and_validation_use_only_hub_level_goal(vault, hub_goal):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read more")
    goals.new(cfg, "Depth", "Build depth")
    body = "# Library\n" + (f"- **Goal:** {hub_goal}\n" if hub_goal else "")
    body += "\n## Improvements\n### Proposal\n- **Goal:** G-2\n\n## Later\n- **Goal:** G-888\n"
    fm.write(vault / "Library.md", {"kind": "hub", "tier": "warm", "goal": "G-2"}, body)
    exported = state.build(cfg, TODAY)["goals"]
    assert [entry["links"]["hubs"] for entry in exported] == [["Library"] if hub_goal == "G-1" else [], []]
    problems = validate.goal_problems(Vault(vault))
    assert bool(problems) == (hub_goal == "G-999")
    assert all("G-888" not in item for item in problems)
