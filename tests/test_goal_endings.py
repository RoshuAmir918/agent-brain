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


@pytest.mark.parametrize("ending,target,message", [
    (None, "G-1", "different active"), (None, "G-999", "unknown or not active"),
    (None, "reached", "unknown or not active"), (None, "set-aside", "unknown or not active"),
    ("reached", "G-2", "only an active goal can be superseded; reopen it first"),
    ("set-aside", "G-2", "only an active goal can be superseded; reopen it first"),
    ("superseded-by", "G-2", "only an active goal can be superseded; reopen it first"),
])
def test_superseding_needs_an_active_source_and_a_different_active_target_without_writes(
        vault, capsys, tmp_path, ending, target, message):
    cfg = Config.load(vault)
    pair(cfg)
    goals.new(cfg, "Swim", "Learn to swim")
    if ending:
        goals.change(cfg, "G-1", ending, text="G-3", why="Finished")
    if target in ("reached", "set-aside"):
        goals.change(cfg, "G-2", target, why="Finished")
        target = "G-2"
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit, match=message):
        run(vault, capsys, "G-1", "superseded-by", target, "--from-app")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("basis,operation", [("duplicate", "reached"), ("latest", "superseded-by"), ("said", "reached"),
                                             ("inferred", "superseded-by"), ("same-day", "reached")])
def test_intellect_ending_evidence_must_be_new(vault, tmp_path, monkeypatch, basis, operation):
    cfg = Config.load(vault)
    existing = "2026-11-01 · Signed up · [[Training]]"
    if basis == "inferred":
        goals.new(cfg, "Race", "Run a marathon", inferred=True, evidence=[existing])
        goals.new(cfg, "Fitness", "Stay fit")
    else:
        pair(cfg)
    if basis in ("duplicate", "latest"):
        goals.change(cfg, "G-1", "evidence", text=existing)
    if basis in ("duplicate", "latest", "same-day"):
        goals.change(cfg, "G-1", "evidence", text="2026-11-06 · Still training · [[Training]]")
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    if basis == "same-day":
        goal = goals.change(cfg, "G-1", operation, text="G-2", evidence=["2026-11-06 · Finished · [[Training]]"])
        assert goal.state["state"] != "active" and goal.evidence[0]["text"] == "Finished"
        return
    value = existing if basis in ("duplicate", "inferred") else "2026-11-05 · Finished · [[Training]]"
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="new evidence|latest evidence"):
        goals.change(cfg, "G-1", operation, text="G-2", evidence=[f"{TODAY} · New finish · [[Training]]", value])
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("change,from_app,age,operation", [
    ("edit", False, 13, "reached"), ("edit", False, 14, "superseded-by"),
    ("reopen", True, 13, "set-aside"), ("reopen", True, 14, "reached"),
    (None, False, 0, "set-aside"),
])
def test_intellect_endings_respect_person_changes_after_intellect_edit(
        vault, tmp_path, monkeypatch, operation, change, from_app, age):
    """Only the person's own edit or reopen protects a goal for fourteen days; the intellect's own edit does not."""
    cfg = Config.load(vault)
    changed_at = str(TODAY - timedelta(days=age))
    monkeypatch.setenv("BRAIN_TODAY", changed_at)
    pair(cfg)
    if change == "reopen":
        goals.change(cfg, "G-1", "reached")
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night" if from_app else "cli:owner")
    if change:
        goals.change(cfg, "G-1", change, title="My race", why="Still pursuing it", from_app=from_app)
    monkeypatch.setenv("BRAIN_TODAY", str(TODAY))
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    goals.change(cfg, "G-1", "edit", title="Race plan")
    goal = entries(cfg)["G-1"]
    assert goal.state.get("owner_updated") == (changed_at if change else None)
    assert goal.state["updated"] == str(TODAY)
    before = snapshot(tmp_path)
    kwargs = dict(text="G-2", why="Finished", evidence=[f"{TODAY} · Finished · [[Training]]"])
    if change and age < 14:
        with pytest.raises(ValueError, match="person.*14 days"):
            goals.change(cfg, "G-1", operation, **kwargs)
        assert snapshot(tmp_path) == before
    else:
        assert goals.change(cfg, "G-1", operation, **kwargs).state["state"] != "active"


@pytest.mark.parametrize("operation,from_app", [("superseded-by", False), ("set-aside", True)])
def test_intellect_endings_stop_after_three_goals_today_but_person_can_end(
        vault, tmp_path, monkeypatch, operation, from_app):
    cfg = Config.load(vault)
    for n in range(5):
        goals.new(cfg, f"Aim {n}", f"Goal {n}")
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    for gid, ending in (("G-1", "reached"), ("G-2", "superseded-by"), ("G-3", "set-aside")):
        goals.change(cfg, gid, ending, text="G-5", why="Stale" if ending == "set-aside" else None,
                     evidence=[f"{TODAY} · Finished · [[Training]]"])
    assert f"- **Superseded:** {TODAY} — Replaced by G-5" in entries(cfg)["G-2"].body
    goals.change(cfg, "G-1", "reopen", why="Again", from_app=True)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="three goals.*today"):
        goals.change(cfg, "G-4", operation, text="G-5", why="Finished",
                     evidence=[f"{TODAY} · Finished · [[Training]]"])
    assert snapshot(tmp_path) == before
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night" if from_app else "cli:owner")
    assert goals.change(cfg, "G-4", operation, text="G-5", why="Finished", from_app=from_app).state["state"] != "active"


def test_stale_flags_do_not_spend_the_peoples_ending_allowance(vault, monkeypatch):
    cfg = Config.load(vault)
    for n in range(4):
        old = str(TODAY - timedelta(days=40 + n))
        monkeypatch.setenv("BRAIN_TODAY", old)
        goals.new(cfg, f"Aim {n}", f"Goal {n}", inferred=True,
                  evidence=[f"{old} · Training · [[Training]]"])
    monkeypatch.setenv("BRAIN_TODAY", str(TODAY))
    goals.new(cfg, "Swim", "Learn to swim")
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    assert goals.decay(cfg, TODAY) == ["G-1", "G-2", "G-3", "G-4"]
    assert all(goal.state["state"] == "active" for goal in goals.load(cfg))
    for gid in ("G-1", "G-2", "G-5"):
        assert goals.change(cfg, gid, "set-aside", why="Judged stale").state["state"] == "set_aside"
    with pytest.raises(ValueError, match="three goals.*today"):
        goals.change(cfg, "G-3", "set-aside", why="Stale")


@pytest.mark.parametrize("linked_by", ["task", "hub"])
@pytest.mark.parametrize("file_goal,library_goal,expected", [
    ("G-3", "G-1", "G-3"), (None, "G-3", "G-3"),
    ("G-1", "G-3", "G-2"), (None, "G-1", "G-2"), (None, None, None),
])
def test_superseding_relinks_only_works_currently_owned_by_source(
        vault, tmp_path, linked_by, file_goal, library_goal, expected):
    cfg = Config.load(vault)
    pair(cfg)
    goals.new(cfg, "Swim", "Learn to swim")
    if linked_by == "task":
        tasks.new_task(cfg, "Plan", '"Make a plan"', goal="G-1", work="plan")
    else:
        fm.write(vault / "Training.md", {"kind": "hub", "work": "plan"}, "# Training\n- **Goal:** G-1\n")
    work = tmp_path / "home/works/plan/GOAL.md"
    work.parent.mkdir(parents=True)
    if file_goal:
        fm.write(work, {"goal": file_goal}, "# Work\n")
    listing = tmp_path / "home/works/LIBRARY.md"
    listing.write_text("# Library\n- Plan | plan | document | active | editable | unknown | "
                       "created 2026-11-01 | opened never" + (f" | goal {library_goal}" if library_goal else "") + "\n")
    goals.change(cfg, "G-1", "superseded-by", text="G-2")
    assert (works.goal_for("plan") or works.parse(works.library())[0].goal) == expected
    if linked_by == "task":
        assert next(task for task in tasks.all_tasks(cfg) if task.work == "plan").goal == "G-2"
    else:
        assert fm.hub_goal(fm.read(vault / "Training.md")[1]) == "G-2"


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


def test_stale_due_reason_and_grounded_evidence_clear_without_ending(vault, monkeypatch, capsys):
    today = date(2026, 11, 8)
    monkeypatch.setenv("BRAIN_TODAY", str(today))
    config = Config.load(vault)
    fm.write(vault / "Training.md", {"kind": "hub", "tier": "warm"}, "# Training\n")
    old = today - timedelta(days=30)
    goal = goals.new(config, "Marathon", "Run a marathon", inferred=True, said=str(old),
                     evidence=[f"{old} · Training began · [[Training]]"])
    assert goals.decay(config, today) == [goal.gid]
    flagged = goals.load(config)[0]
    assert flagged.state["state"] == "active" and flagged.state["stale"] == str(today)
    assert "Set-Aside" not in flagged.body
    before = snapshot(vault)
    cli.main(["goals", "due", "--json", "--vault", str(vault)])
    row, = json.loads(capsys.readouterr().out)
    assert row["id"] == goal.gid and row["stale"] == str(today)
    assert "No grounded evidence for 30 days" in row["reasons"][0]
    cli.main(["goals", "due", "--vault", str(vault)])
    assert "flagged stale" in capsys.readouterr().out
    assert snapshot(vault) == before
    assert goals.decay(config, today + timedelta(days=1)) == []
    assert goals.load(config)[0].state["stale"] == str(today)
    goals.change(config, goal.gid, "evidence", text=f"{old} · Old evidence repeated · [[Training]]")
    assert goals.load(config)[0].state["stale"] == str(today)
    goals.change(config, goal.gid, "evidence", text=f"{today} · Still training · [[Training]]")
    assert "stale" not in goals.load(config)[0].state
    assert goals.due(config) == [] and goals.decay(config, today) == []
    assert not goals.problems(config)


def test_decay_clears_stale_mark_when_existing_evidence_becomes_grounded(vault, monkeypatch):
    today = date(2026, 11, 8)
    monkeypatch.setenv("BRAIN_TODAY", str(today))
    config = Config.load(vault)
    fm.write(vault / "Training.md", {"kind": "hub"}, "# Training\n")
    goal = goals.new(config, "Running", "Run", inferred=True, said="2026-09-01",
                     evidence=["2026-09-01 · Began · [[Training]]"])
    assert goals.decay(config, today) == [goal.gid]
    goal = goals.load(config)[0]
    goal.body += f"- **Evidence:** {today} · Ran again · [[Restored]]\n"
    goals._write(config, *goals.read(config), goal)
    assert goals.decay(config, today) == []
    fm.write(vault / "Restored.md", {"kind": "hub"}, "# Restored\n")
    assert goals.decay(config, today) == [goal.gid]
    assert "stale" not in goals.load(config)[0].state
    assert goals.load(config)[0].state["state"] == "active"
