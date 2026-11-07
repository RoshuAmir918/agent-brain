"""Goal endings preserve evidence and work, and missed dates ask before ending."""
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from brain import cli, evidence, fm, goals, state, tasks, validate, works
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def session(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("BRAIN_RUN", raising=False)
    fm.write(vault / "Training.md", {"kind": "hub", "tier": "warm"}, "# Training\n")


def run(vault, capsys, *args):
    cli.main(["goals", *args, "--vault", str(vault)])
    return capsys.readouterr().out


def pair(cfg):
    goals.new(cfg, "Race", "Run a marathon", reach=True, by="2026-11-01")
    goals.new(cfg, "Fitness", "Stay fit")


def entries(cfg):
    return {goal.gid: goal for goal in goals.load(cfg)}


@pytest.mark.parametrize("operation", ["reached", "superseded-by"])
@pytest.mark.parametrize("value", [None, "Finished · [[Training]]", "2026-11-08 · Tomorrow · [[Training]]",
                                    "2026-11-07 · Finished · [[Missing]]",
                                    "2026-11-07 · Prepared · [[agent/Prepared/Plan]]",
                                    "2026-11-07 · Finished · evidence:unknown"])
def test_intellect_endings_need_grounded_dated_evidence_without_writes(vault, capsys, tmp_path, monkeypatch, operation, value):
    cfg = Config.load(vault)
    pair(cfg)
    (vault / "agent/Prepared").mkdir()
    fm.write(vault / "agent/Prepared/Plan.md", {"kind": "draft"}, "# Plan\n")
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    before = snapshot(tmp_path)
    args = ["G-1", operation, *(["G-2"] if operation == "superseded-by" else [])]
    if value is not None:
        args.extend(("--evidence", value))
    with pytest.raises(SystemExit, match="evidence"):
        run(vault, capsys, *args)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("operation", ["reached", "superseded-by"])
@pytest.mark.parametrize("source", ["note", "rows"])
def test_intellect_endings_add_evidence_and_export(vault, capsys, monkeypatch, operation, source):
    cfg = Config.load(vault)
    pair(cfg)
    evidence.append("anki", [evidence.Evidence(TODAY, "reviewed", "cards", "own", ("study",), "deck:x")])
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    value = f"{TODAY} · Finished the race · " + ("[[Training]]" if source == "note" else "evidence:anki")
    run(vault, capsys, "G-1", operation, *(["G-2"] if operation == "superseded-by" else []), "--evidence", value)
    exported = {goal["id"]: goal for goal in state.build(cfg, TODAY)["goals"]}
    assert exported["G-1"]["state"] == ("reached" if operation == "reached" else "superseded")
    assert exported["G-1"]["supersededBy"] == (None if operation == "reached" else "G-2")
    assert exported["G-1"]["evidence"][0]["text"].startswith("Finished the race")
    assert not goals.problems(cfg)


@pytest.mark.parametrize("operation", ["reached", "superseded-by"])
@pytest.mark.parametrize("speaker,from_app", [("demiurge:owner", False), ("cli:local", False), ("cron:night", True)])
def test_owner_and_app_endings_need_no_evidence(vault, monkeypatch, operation, speaker, from_app):
    cfg = replace(Config.load(vault), owner_user_ids=["demiurge:owner"])
    pair(cfg)
    monkeypatch.setenv("BRAIN_SPEAKER", speaker)
    goal = goals.change(cfg, "G-1", operation, text="G-2" if operation == "superseded-by" else None, from_app=from_app)
    assert goal.state["state"] != "active"
    if from_app:
        assert "changed this in the app" in goals.notices(cfg)
        assert "G-1 — Race" not in (cfg.repo / ".context.md").read_text()


def test_superseding_merges_evidence_relinks_open_work_and_reopen_keeps_links(vault, capsys, tmp_path):
    cfg = Config.load(vault)
    pair(cfg)
    for n in range(7):
        goals.change(cfg, "G-1", "evidence", text=f"{TODAY - timedelta(days=n)} · Race {n} · [[Training]]")
        goals.change(cfg, "G-2", "evidence", text=f"{TODAY - timedelta(days=n+6)} · Fitness {n} · [[Training]]")
    shared = f"{TODAY} · Shared · [[Training]]"
    for gid in ("G-1", "G-2"):
        goals.change(cfg, gid, "evidence", text=shared)
    source_evidence = entries(cfg)["G-1"].evidence
    hot = tasks.new_task(cfg, "Training plan", '"Make a plan"', goal="G-1", work="plan")
    cold = tasks.new_task(cfg, "Shoes", '"Choose shoes"', goal="G-1")
    tasks.write_archive(cfg, cold, "cold", TODAY, whole=True)
    data, body = fm.read(vault / cfg.agent("Tasks.md"))
    _, start, end = next(span for span in tasks.parse_register(body)[0] if span[0] == cold.tid)
    fm.write(vault / cfg.agent("Tasks.md"), data, body[:start] + body[end:])
    closed = tasks.new_task(cfg, "Pick race", '"Pick race"', goal="G-1")
    tasks.close(cfg, closed.tid, TODAY, False, "Picked")
    for rel, data, body in (
        ("Drafts/Pacing.md", {"kind": "draft", "goal": "G-1"}, "# Pacing\n"),
        ("agent/Prepared/Plan.md", {"kind": "draft"}, "# Plan\n- **Goal:** G-1\n"),
        ("Training.md", {"kind": "hub", "tier": "warm", "work": "training"},
         "# Training\n- **Goal:** G-1\n\n## Improvements\n### Proposal\n- **Goal:** G-1\n"),
    ):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        fm.write(vault / rel, data, body)
    home = tmp_path / "home"
    for slug in ("plan", "training", "routine", "retired"):
        path = home / "works" / slug / "GOAL.md"
        path.parent.mkdir(parents=True)
        fm.write(path, {"goal": "G-1", "task": hot.tid}, "# Work\n")
    (home / "works/LIBRARY.md").write_text(
        "# Library\n- Routine | routine | document | active | editable | unknown | created 2026-11-01 | opened never | goal G-1\n"
        "- Retired | retired | document | retired | editable | unknown | created 2026-11-01 | opened never | goal G-1\n")
    run(vault, capsys, "G-1", "superseded-by", "G-2", "--why", "Fitness is the wider aim", "--from-app")
    old, target = entries(cfg)["G-1"], entries(cfg)["G-2"]
    assert old.state["superseded_by"] == "G-2" and old.evidence == source_evidence
    assert "- **Became:** [[" in old.body and "]] G-2 — Fitness" in old.body
    assert "Fitness is the wider aim" in old.body
    assert "- **Earlier as:** G-1 — Race" in target.body
    assert len(target.evidence) == 10 and target.evidence == sorted(target.evidence, key=lambda ev: ev["at"], reverse=True)
    assert sum(ev["text"] == "Shared" for ev in target.evidence) == 1
    assert "Fitness 6" in (vault / cfg.agent("Goals Archive/G-2.md")).read_text()
    assert "### G-1" in goals.read(cfg)[1].split("## Superseded\n")[1]
    linked = {task.tid: task.goal for task in tasks.all_tasks(cfg)}
    assert linked[hot.tid] == linked[cold.tid] == "G-2" and linked[closed.tid] == "G-1"
    assert all(works.goal_for(slug) == "G-2" for slug in ("plan", "training", "routine"))
    assert works.goal_for("retired") == "G-1"
    assert fm.read(vault / "Drafts/Pacing.md")[0]["goal"] == "G-2"
    assert fm.field(fm.read(vault / "agent/Prepared/Plan.md")[1], "Goal") == "G-2"
    hub = fm.read(vault / "Training.md")[1]
    assert fm.hub_goal(hub) == "G-2" and "- **Goal:** G-1" in hub.split("## Improvements")[1]
    run(vault, capsys, "G-1", "reopen", "--why", "Racing again", "--from-app")
    reopened = entries(cfg)["G-1"]
    assert reopened.state["state"] == "active" and "superseded_by" not in reopened.state
    assert reopened.state["updated"] == TODAY.isoformat()
    assert entries(cfg)["G-1"].public()["supersededBy"] is None
    assert {task.tid: task.goal for task in tasks.all_tasks(cfg)} == linked
    assert not validate.goal_problems(Vault(vault))


@pytest.mark.parametrize("target", ["G-1", "G-999", "reached", "set-aside"])
def test_superseding_requires_different_active_target_without_writes(vault, capsys, tmp_path, target):
    cfg = Config.load(vault)
    pair(cfg)
    if target in ("reached", "set-aside"):
        goals.change(cfg, "G-2", target, why="Finished")
        target = "G-2"
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="different active|unknown or not active"):
        run(vault, capsys, "G-1", "superseded-by", target, "--from-app")
    assert snapshot(tmp_path) == before


def test_due_is_read_only_and_missed_asks_repeat_after_fourteen_days(vault, capsys, tmp_path, monkeypatch):
    cfg = Config.load(vault)
    pair(cfg)
    for title, by in (("Today", str(TODAY)), ("Future", str(TODAY + timedelta(days=1))), ("No date", None)):
        goals.new(cfg, title, title, reach=True, by=by)
    goals.new(cfg, "Finished", "Finished", reach=True, by="2026-11-01")
    goals.change(cfg, "G-6", "reached")
    before = snapshot(tmp_path)
    def refuse_lock(*args):
        pytest.fail("due must not lock or write")
    with monkeypatch.context() as preview:
        preview.setattr(cli, "vault_lock", refuse_lock)
        assert [goal["id"] for goal in json.loads(run(vault, capsys, "due", "--json"))] == ["G-1"]
        assert "G-1" in run(vault, capsys, "due")
    assert snapshot(tmp_path) == before
    run(vault, capsys, "G-1", "asked-missed")
    assert entries(cfg)["G-1"].state["missed_asked"] == TODAY.isoformat()
    assert entries(cfg)["G-1"].state["state"] == "active"
    assert json.loads(run(vault, capsys, "due", "--json")) == []
    for age, listed in ((13, False), (14, True)):
        monkeypatch.setenv("BRAIN_TODAY", str(TODAY + timedelta(days=age)))
        assert ("G-1" in [goal.gid for goal in goals.due(cfg)]) == listed
    assert not goals.problems(cfg)


@pytest.mark.parametrize("gid", ["G-2", "G-3", "G-4"])
def test_asked_missed_rejects_goals_without_missed_finish_line(vault, capsys, tmp_path, gid):
    cfg = Config.load(vault)
    pair(cfg)
    goals.new(cfg, "Today", "Today", reach=True, by=str(TODAY))
    goals.new(cfg, "No date", "No date", reach=True)
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match="past its finish date"):
        run(vault, capsys, gid, "asked-missed")
    assert snapshot(tmp_path) == before


def test_judged_stale_reason_is_preserved(vault, capsys):
    cfg = Config.load(vault)
    pair(cfg)
    run(vault, capsys, "G-1", "set-aside", "--why", "Training stopped after the injury")
    assert "Training stopped after the injury" in entries(cfg)["G-1"].body
    assert entries(cfg)["G-1"].state["state"] == "set_aside"


def test_nightly_skill_limits_endings_and_respects_recent_owner_changes():
    from importlib.resources import files
    people = files("brain").joinpath("skills/people/SKILL.md").read_text()
    recall = files("brain").joinpath("skills/recall/SKILL.md").read_text()
    assert "At most three endings a night" in people
    assert "reopened or edited in the last fourteen days" in people
    assert "brain goals due --json" in people and "asked-missed" in people
    assert "brain needs-you --confirm" in people and "brain needs-you --ask" in people
    assert '"done", "that\'s replaced by X" or "forget it"' in recall
