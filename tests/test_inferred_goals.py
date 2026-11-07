"""Inferred goals stay evidenced, correctable and safe to turn into asked work."""
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from brain import cli, fm, goals, nightly, state, tasks, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 7)


@pytest.fixture(autouse=True)
def session(monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    for name in ("AGENT_HOME", "BRAIN_RUN", "DEMIURGE_TASK", "BRAIN_SPEAKER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")


@pytest.fixture(autouse=True)
def evidence_note(vault):
    fm.write(vault / "Reading.md", {"kind": "hub", "tier": "warm"}, "# Reading\n")


def run(vault, capsys, *args):
    cli.main(["goals", *args, "--vault", str(vault)])
    return capsys.readouterr().out


def infer(cfg, title="Reading", words="Read beyond my field", at=TODAY):
    return goals.new(cfg, title, words, inferred=True, said=at.isoformat(),
                     evidence=[f"{at} · Returned to the reading list · [[Reading]]"])


def test_origin_round_trip_legacy_default_and_export(vault, capsys):
    cfg = Config.load(vault)
    run(vault, capsys, "new", "Reading", "--words", "Read beyond my field", "--said", "2026-11-01",
        "--inferred", "--evidence", "2026-11-01 · Made a reading list · [[Reading]]",
        "--evidence", "2026-11-06 · Finished another book · [[Reading]]")
    entry = goals.load(cfg)[0]
    assert entry.origin == "inferred" and "origin=inferred" in entry.render()
    assert entry.render() == goals.parse_register(entry.render())[1][0].render()
    assert entry.said_at == "2026-11-01" and len(entry.evidence) == 2
    assert state.build(cfg, TODAY)["goals"][0]["origin"] == "inferred"
    legacy = goals.new(cfg, "Race", "Run sub-3")
    legacy.state.pop("origin")
    goals._write(cfg, *goals.read(cfg), legacy)
    assert goals.load(cfg)[1].origin == "said"
    assert state.build(cfg, TODAY)["goals"][1]["origin"] == "said"
    assert not goals.problems(cfg)


@pytest.mark.parametrize("evidence", [[], [""], ["One passing mention"], ["2026-02-30 · Books"],
                                      ["2026-11-08 · Tomorrow"], ["2026-11-06 · Books\n### G-2 — injected"]])
def test_inferred_requires_valid_inline_evidence_without_writes(vault, capsys, evidence):
    before = snapshot(vault)
    args = ["new", "Reading", "--words", "Read beyond my field", "--inferred"]
    for value in evidence:
        args.extend(("--evidence", value))
    with pytest.raises(SystemExit):
        run(vault, capsys, *args)
    assert snapshot(vault) == before


def test_keep_adopts_reading_and_refreshes_app_context(vault, capsys):
    cfg = Config.load(vault)
    infer(cfg)
    run(vault, capsys, "G-1", "keep", "--from-app")
    entry = goals.load(cfg)[0]
    assert entry.origin == "said" and entry.state["state"] == "active"
    assert entry.words == "Read beyond my field" and "you kept it" in entry.body
    assert state.build(cfg, TODAY)["goals"][0]["origin"] == "said"
    assert "G-1 — Reading (said)" in (cfg.repo / ".context.md").read_text()
    assert goals.capture(cfg)["app"] and not goals.problems(cfg)


@pytest.mark.parametrize("why", [None, "This is one reading list"])
def test_not_a_goal_archives_and_refuses_title_or_words_without_reusing_id(vault, capsys, why):
    cfg = Config.load(vault)
    infer(cfg)
    run(vault, capsys, "G-1", "not-a-goal", *(('--why', why) if why else ()), "--from-app")
    assert not goals.load(cfg) and state.build(cfg, TODAY)["goals"] == []
    archive = (vault / cfg.agent("Goals Archive/G-1.md")).read_text()
    assert (why or "Not a goal") in archive and "Read beyond my field" in archive
    assert goals.archived(cfg)[0].gid == "G-1"
    refused = json.loads(run(vault, capsys, "refused", "--json"))
    assert refused[0]["title"] == "Reading" and refused[0]["words"] == "Read beyond my field"
    assert "Refused goal; never re-propose: Reading" in goals.context(cfg)
    before = snapshot(vault)
    for title, words in (("READING!", "Different words"), ("New title", "Read beyond my field.")):
        with pytest.raises(ValueError, match="refused"):
            infer(cfg, title, words)
        assert snapshot(vault) == before
    assert goals.new(cfg, "Race", "Run sub-3").gid == "G-2"


@pytest.mark.parametrize("why", ["-no", "x" * 201, "no\x00thanks", "a\tb", "\tno", "a\nb", "<!--", "-->"])
def test_refusal_reason_validation_is_atomic(vault, why):
    cfg = Config.load(vault)
    infer(cfg)
    before = snapshot(vault)
    with pytest.raises(ValueError):
        goals.change(cfg, "G-1", "not-a-goal", why=why)
    assert snapshot(vault) == before


@pytest.mark.parametrize("reach", [False, True])
@pytest.mark.parametrize("worker", [False, True])
def test_to_task_quoted_source_guard_archives_detaches_and_links(vault, capsys, tmp_path, monkeypatch, reach, worker):
    cfg = Config.load(vault)
    goal = goals.new(cfg, "Marathon", "Run a sub-3 marathon", reach=reach,
                     by="2027-04-20" if reach else None)
    parent = tasks.new_task(cfg, "Training plan", '"Make a training plan"', goal=goal.gid, work="plan")
    cold = tasks.new_task(cfg, "Shoe choice", '"Find shoes"', goal=goal.gid)
    tasks.write_archive(cfg, cold, "cold", TODAY, whole=True)
    data, body = fm.read(vault / cfg.agent("Tasks.md"))
    span = next(span for span in tasks.parse_register(body)[0] if span[0] == cold.tid)
    fm.write(vault / cfg.agent("Tasks.md"), data, body[:span[1]] + body[span[2]:])
    closed = tasks.new_task(cfg, "Pick a race", '"Pick a race"', goal=goal.gid, work="race")
    tasks.close(cfg, closed.tid, TODAY, False, "Already picked")
    draft = vault / "Drafts/Pacing.md"
    draft.parent.mkdir()
    fm.write(draft, {"kind": "draft", "tier": "warm", "goal": goal.gid}, "# Pacing\n")
    home = tmp_path / "home"
    work = home / "works/plan/GOAL.md"
    work.parent.mkdir(parents=True)
    fm.write(work, {"goal": goal.gid}, "")
    finished_work = home / "works/race/GOAL.md"
    finished_work.parent.mkdir(parents=True)
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("BRAIN_RUN", "routine")
    if worker:
        monkeypatch.setenv("DEMIURGE_TASK", parent.tid)
    result = run(vault, capsys, goal.gid, "to-task", "--from-app")
    assert result.splitlines()[-1] == "G-1 · became T-005"
    entries = {task.tid: task for task in tasks.all_tasks(cfg)}
    made = entries["T-005"]
    assert made.title == goal.title and made.goal is None
    assert '- **Source:** "Run a sub-3 marathon"' in made.body
    assert made.delivers == ("Run a sub-3 marathon by 2027-04-20" if reach else None)
    assert entries[parent.tid].goal is None and entries[cold.tid].goal is None
    assert entries[closed.tid].goal == goal.gid
    assert made.work == "plan" and fm.read(work)[0]["task"] == made.tid
    assert fm.read(finished_work)[0]["task"] == made.tid
    assert fm.read(draft)[0]["task"] == made.tid
    assert not goals.load(cfg) and f"became {made.tid}" in goals.archived(cfg)[0].body
    assert not validate.goal_problems(Vault(vault))
    assert f"became {made.tid}" in goals.notices(cfg)
    before = snapshot(vault)
    with pytest.raises(SystemExit, match="not a goal"):
        run(vault, capsys, goal.gid, "to-task", "--from-app")
    assert snapshot(vault) == before


def test_no_more_than_five_active_inferred_including_reopens(vault, monkeypatch):
    cfg = Config.load(vault)
    for n in range(5):
        at = TODAY - timedelta(days=3 - n // 2)
        monkeypatch.setenv("BRAIN_TODAY", at.isoformat())
        infer(cfg, f"Aim {n}", f"My own words {n}", at)
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    before = snapshot(vault)
    with pytest.raises(ValueError, match="at most 5"):
        infer(cfg, "Sixth", "Sixth direction")
    assert snapshot(vault) == before
    goals.change(cfg, "G-1", "set-aside", why="Later")
    infer(cfg, "Sixth", "Sixth direction")
    with pytest.raises(ValueError, match="at most 5"):
        goals.change(cfg, "G-1", "reopen", why="Again")
    goals.change(cfg, "G-2", "keep", from_app=True)
    goals.change(cfg, "G-1", "reopen", why="Again")
    assert sum(goal.origin == "inferred" for goal in goals.active(cfg)) == 5


def test_same_aim_adds_evidence_instead_of_new_goal(vault):
    cfg = Config.load(vault)
    infer(cfg)
    with pytest.raises(ValueError, match="add evidence"):
        infer(cfg, "Reading!", "New words")
    with pytest.raises(ValueError, match="add evidence"):
        infer(cfg, "New title", "Read beyond my field")
    goals.change(cfg, "G-1", "evidence", text="2026-11-07 · Finished a book · [[Reading]]")
    assert len(goals.load(cfg)) == 1 and len(goals.load(cfg)[0].evidence) == 2


def test_inferred_decay_at_thirty_days_uses_evidence_not_edits_or_said_date(vault, monkeypatch):
    cfg = Config.load(vault)
    for age in (29, 30, 31):
        monkeypatch.setenv("BRAIN_TODAY", (TODAY - timedelta(days=age)).isoformat())
        infer(cfg, f"Reading {age}", f"Own words {age}", TODAY - timedelta(days=age))
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night")
    goals.change(cfg, "G-2", "edit", title="An edit today")
    goals.change(cfg, "G-3", "evidence", text="2026-11-06 · Another book · [[Reading]]")
    goals.new(cfg, "Said goal", "Still a goal", said="2026-01-01", from_app=True)
    assert goals.decay(cfg, TODAY) == ["G-2"]
    assert goals.decay(cfg, TODAY) == []
    assert [goal.gid for goal in goals.active(cfg)] == ["G-1", "G-3", "G-4"]
    assert "No new evidence for 30 days" in goals.load(cfg)[-1].body
    assert not goals.problems(cfg)


def test_nightly_runs_decay_and_preview_stays_read_only(vault, tmp_path):
    cfg = Config.load(vault)
    fm.write(vault / "Reading.md", {"kind": "hub", "tier": "warm"}, "# Reading\n")
    infer(cfg, at=TODAY - timedelta(days=30))
    before = snapshot(vault)
    assert any("G-1 set aside" in line for line in nightly.preview(vault, TODAY))
    assert snapshot(vault) == before
    report = nightly.run(vault, TODAY, library_records=tmp_path / "library.json")
    assert "goal: G-1 set aside after 30 days without evidence" in report
    assert goals.load(cfg)[0].state["state"] == "set_aside"


def test_asked_never_takes_lock_or_changes_legacy_capture(vault, capsys, monkeypatch):
    cfg = Config.load(vault)
    goals.save_capture(cfg, {"asked": "2026-10-01", "app": []})
    before = snapshot(vault)
    def refuse_lock(*args):
        pytest.fail("asked must not lock or write")
    monkeypatch.setattr(cli, "vault_lock", refuse_lock)
    assert run(vault, capsys, "asked") == "Goals are inferred now; don't ask.\n"
    assert snapshot(vault) == before


@pytest.mark.parametrize("operation", ["new", "keep", "not-a-goal", "to-task"])
@pytest.mark.parametrize("speaker", ["", "cron:night", "subagent:run", "demiurge:visitor"])
def test_owner_operations_refuse_intellect_and_visitors_without_writes(vault, capsys, monkeypatch, operation, speaker):
    cfg = Config.load(vault)
    infer(cfg)
    monkeypatch.setenv("BRAIN_RUN", "routine")
    monkeypatch.setenv("BRAIN_SPEAKER", speaker)
    before = snapshot(vault)
    args = ("new", "Said", "--words", "My goal") if operation == "new" else ("G-1", operation)
    with pytest.raises(SystemExit, match="intellect infers.*person keeps, refuses or demotes"):
        run(vault, capsys, *args)
    with pytest.raises(ValueError, match="BRAIN_SPEAKER naming the owner"):
        if operation == "new":
            goals.new(cfg, "Said", "My goal")
        else:
            goals.change(cfg, "G-1", operation)
    assert snapshot(vault) == before


@pytest.mark.parametrize("operation", ["new", "keep", "not-a-goal", "to-task"])
@pytest.mark.parametrize("speaker,from_app", [("demiurge:owner", False), ("cli:local", False), ("cron:night", True)])
def test_owner_operations_accept_owner_identity_or_app(vault, monkeypatch, operation, speaker, from_app):
    cfg = replace(Config.load(vault), owner_user_ids=["demiurge:owner"])
    infer(cfg)
    monkeypatch.setenv("BRAIN_SPEAKER", speaker)
    if operation == "new":
        assert goals.new(cfg, "Said", "My goal", from_app=from_app).origin == "said"
    else:
        goals.change(cfg, "G-1", operation, from_app=from_app)
        assert not goals.problems(cfg)


def test_two_per_24_hours_counts_kept_and_archived_goals_across_midnight(vault, monkeypatch):
    cfg = Config.load(vault)
    created = datetime(2026, 11, 6, 23, 30, tzinfo=cfg.zone)
    monkeypatch.setattr(Config, "now", lambda self: created)
    for n in range(2):
        goal = infer(cfg, f"Aim {n}", f"Words {n}", created.date())
        assert datetime.fromisoformat(goal.state["inferred"]) == created
    goals.change(cfg, "G-1", "keep")
    goals.change(cfg, "G-2", "to-task")
    created += timedelta(hours=1)
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:explore")
    before = snapshot(vault)
    with pytest.raises(ValueError, match="2 new inferred goals per 24 hours"):
        goals.new(cfg, "Third aim", "Third words", inferred=True, evidence=[f"{created.date()} · Different aim · [[START]]"])
    assert snapshot(vault) == before
    created += timedelta(hours=23)
    assert goals.new(cfg, "Third aim", "Third words", inferred=True,
                     evidence=[f"{created.date()} · Different aim · [[START]]"]).gid == "G-3"


@pytest.mark.parametrize("operation", ["not-a-goal", "to-task"])
@pytest.mark.parametrize("title,candidate,same_note", [
    ("Reading", "Reading more", False),
    ("Reading outside engineering", "Reading", False),
    ("Reading", "Broaden my horizons", True),
])
def test_refused_or_demoted_rephrases_match_title_tokens_or_evidence(vault, operation, title, candidate, same_note):
    cfg = Config.load(vault)
    infer(cfg, title)
    result = goals.change(cfg, "G-1", operation)
    refused = goals.capture(cfg)["refused"][0]
    assert refused["evidence"][0]["link"] == "Reading"
    assert refused["why"] == (f"became {result.tid}" if operation == "to-task" else "Not a goal")
    fm.write(vault / "Other.md", {"kind": "hub", "tier": "warm"}, "# Other\n")
    before = snapshot(vault)
    with pytest.raises(ValueError, match="refused"):
        goals.new(cfg, candidate, "Different own words", inferred=True,
                  evidence=[f"{TODAY} · A different observation · [[{'Reading' if same_note else 'Other'}]]"])
    assert snapshot(vault) == before


def test_duplicate_check_reads_archive_without_refusal(vault):
    cfg = Config.load(vault)
    goal = infer(cfg)
    goals._archive(cfg, *goals.read(cfg), goal, "Old aim")
    assert not goals.capture(cfg)["refused"]
    with pytest.raises(ValueError, match="already exists; add evidence"):
        infer(cfg, "Reading!", "Other words")


@pytest.mark.parametrize("operation", ["new", "evidence"])
@pytest.mark.parametrize("link", ["", "[[Missing]]", "[[agent/Own note]]", "[[agent/Prepared/My draft]]", "[[Drafts/Plan]]"])
def test_inferred_evidence_requires_existing_note_outside_agent_and_drafts(vault, operation, link):
    cfg = Config.load(vault)
    for rel in ("agent/Own note.md", "agent/Prepared/My draft.md", "Drafts/Plan.md"):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"kind": "draft" if "draft" in rel or rel.startswith("Drafts/") else "hub"}, "# Note\n")
    if operation == "evidence":
        infer(cfg)
    before = snapshot(vault)
    with pytest.raises(ValueError, match="existing note outside the agent directory"):
        text = f"{TODAY} · I prepared something · {link}"
        if operation == "new":
            goals.new(cfg, "Reading", "Read", inferred=True, evidence=[text])
        else:
            goals.change(cfg, "G-1", "evidence", text=text)
    assert snapshot(vault) == before


def test_inferred_evidence_resolves_aliases_and_said_evidence_stays_free(vault):
    cfg = Config.load(vault)
    goals.new(cfg, "Reading", "Read", inferred=True, evidence=[f"{TODAY} · [[Reading#Books|My books]] again"])
    goals.change(cfg, "G-1", "evidence", text=f"{TODAY} · Finished it · [[memory/Reading]]")
    goals.change(cfg, "G-1", "keep")
    goals.change(cfg, "G-1", "evidence", text=f"{TODAY} · No link needed")
    assert len(goals.load(cfg)[0].evidence) == 3


def test_inferred_evidence_update_requires_a_date(vault):
    cfg = Config.load(vault)
    infer(cfg)
    before = snapshot(vault)
    with pytest.raises(ValueError, match="inferred evidence needs a date"):
        goals.change(cfg, "G-1", "evidence", text="Drafted a reading plan · [[Reading]]")
    assert snapshot(vault) == before


def test_decay_ignores_legacy_self_evidence_and_deleted_notes(vault):
    cfg = Config.load(vault)
    old = TODAY - timedelta(days=40)
    first = infer(cfg, at=old)
    (vault / cfg.agent("Prepared")).mkdir()
    fm.write(vault / cfg.agent("Prepared/Plan.md"), {"kind": "draft"}, "# Plan\n")
    goals._add_evidence(cfg, first, f"- **Evidence:** {TODAY} · Prepared a plan · [[agent/Prepared/Plan]]")
    goals._write(cfg, *goals.read(cfg), first)
    fm.write(vault / "Other.md", {"kind": "hub"}, "# Other\n")
    second = goals.new(cfg, "Running", "Run daily", said=str(old), inferred=True,
                       evidence=[f"{TODAY} · Training · [[Other]]"])
    (vault / "Other.md").unlink()
    assert goals.decay(cfg, TODAY) == [first.gid, second.gid]


@pytest.mark.parametrize("operation", ["reopen", "edit"])
@pytest.mark.parametrize("from_app", [False, True])
def test_owner_edit_or_reopen_adopts_inferred_goal_and_prevents_redecay(vault, monkeypatch, operation, from_app):
    cfg = replace(Config.load(vault), owner_user_ids=["demiurge:owner"])
    infer(cfg, at=TODAY - timedelta(days=40))
    if operation == "reopen":
        assert goals.decay(cfg, TODAY - timedelta(days=1)) == ["G-1"]
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:night" if from_app else "demiurge:owner")
    kwargs = {"why": "Pursuing it"} if operation == "reopen" else {"title": "My reading"}
    goals.change(cfg, "G-1", operation, from_app=from_app, **kwargs)
    assert goals.load(cfg)[0].origin == "said"
    assert goals.decay(cfg, TODAY) == []


def test_nightly_skips_decay_with_problem_and_finishes(vault, tmp_path):
    cfg = Config.load(vault)
    infer(cfg, at=TODAY - timedelta(days=40))
    data, body = goals.read(cfg)
    fm.write(vault / cfg.human_goals, data, body + "\n### G-9 — Broken\n- nothing\n")
    report = nightly.run(vault, TODAY, library_records=tmp_path / "library.json")
    assert any("goal: decay skipped:" in line and "G-9" in line for line in report)
    assert goals.load(cfg)[0].state["state"] == "active"
    assert (vault / cfg.agent("Sleep Log/2026-11.md")).exists()


def test_nightly_catches_decay_failure_per_goal_and_continues(vault, tmp_path, monkeypatch):
    cfg = Config.load(vault)
    for n in range(2):
        infer(cfg, f"Aim {n}", f"Words {n}", TODAY - timedelta(days=40))
    change = goals.change
    def fail_one(config, gid, operation, **kwargs):
        if gid == "G-1":
            raise OSError("write failed")
        return change(config, gid, operation, **kwargs)
    monkeypatch.setattr(goals, "change", fail_one)
    report = nightly.run(vault, TODAY, library_records=tmp_path / "library.json")
    assert "goal: G-1 decay skipped: write failed" in report
    assert "goal: G-2 set aside after 30 days without evidence" in report
    assert goals.load(cfg)[0].state["state"] == "active"


def test_not_a_goal_detaches_open_tasks_in_register_and_archive(vault):
    cfg = Config.load(vault)
    infer(cfg)
    hot = tasks.new_task(cfg, "Reading list", '"Make a list"', goal="G-1")
    cold = tasks.new_task(cfg, "Choose books", '"Choose books"', goal="G-1")
    tasks.write_archive(cfg, cold, "cold", TODAY, whole=True)
    data, body = fm.read(vault / cfg.agent("Tasks.md"))
    _, start, end = next(span for span in tasks.parse_register(body)[0] if span[0] == cold.tid)
    fm.write(vault / cfg.agent("Tasks.md"), data, body[:start] + body[end:])
    closed = tasks.new_task(cfg, "Bookshop", '"Pick a shop"', goal="G-1")
    tasks.close(cfg, closed.tid, TODAY, False, "Picked")
    for n in range(12):
        goals.change(cfg, "G-1", "evidence", text=f"{TODAY - timedelta(days=n)} · Read book {n} · [[Reading]]")
    goals.change(cfg, "G-1", "not-a-goal")
    entries = {task.tid: task for task in tasks.all_tasks(cfg)}
    assert entries[hot.tid].goal is None and entries[cold.tid].goal is None
    assert entries[closed.tid].goal == "G-1"
    assert not validate.goal_problems(Vault(vault))
    assert goals.new(cfg, "Other", "Other words").gid == "G-2"
