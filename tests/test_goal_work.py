"""Readable goal work and owner steering, using only synthetic vaults and runtimes."""
import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from brain import cli, continuation, fm, goal_activity, goals, ideas, ledger, needs, reception, state, tasks
from brain.config import Config
from conftest import snapshot

TODAY = date(2026, 11, 7)


@pytest.fixture
def cfg(vault, monkeypatch):
    monkeypatch.delenv("BRAIN_RUN", raising=False)
    monkeypatch.delenv("BRAIN_SKILL", raising=False)
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")
    config = Config.load(vault)
    goals.new(config, "Learn medicine", "I want to learn medicine")
    fm.write(vault / "Medicine.md", {"tier": "warm", "kind": "hub"}, "# Medicine\n- **Goal:** G-1\n")
    return config


def run(cfg, capsys, *args):
    cli.main(["goals", *args, "--vault", str(cfg.vault_path)])
    return capsys.readouterr().out


def draft(cfg, name="Renal deck", *, created=None, **data):
    path = cfg.vault_path / cfg.agent(f"Prepared/{name}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"tier": "warm", "kind": "draft", "goal": "G-1", "created": created or TODAY.isoformat(),
                    "index_line": "Prepared a renal deck for your next review", **data},
             "# Renal deck\n" + "\n".join(f"- Card {n}" for n in range(20)))
    return path.relative_to(cfg.vault_path).as_posix()


def test_account_overwrites_rolls_iso_year_and_archives_after_eight_weeks(cfg, capsys, monkeypatch):
    for n in range(10):
        day = date(2026, 10, 26) + timedelta(weeks=n)
        monkeypatch.setenv("BRAIN_TODAY", str(day))
        run(cfg, capsys, "G-1", "account", f"I prepared week {n}'s review. It is held for you.", "--week", goals.week(day))
    goal = goals.load(cfg)[0]
    assert len(goals.ACCOUNT.findall(goal.body)) == 8
    assert goals.account(goal, day) == {
        "thisWeek": {"week": "2026-W53", "text": "I prepared week 9's review. It is held for you."},
        "lastWeek": {"week": "2026-W52", "text": "I prepared week 8's review. It is held for you."}}
    run(cfg, capsys, "G-1", "account", "I revised your review. Nothing was sent.", "--week", goals.week(day))
    assert len(goals.ACCOUNT.findall(goals.load(cfg)[0].body)) == 8
    assert "week 9's" not in goals.load(cfg)[0].body
    archive = cfg.vault_path / cfg.agent("Goals Archive/G-1.md")
    assert "week 0's" in archive.read_text() and "week 1's" in archive.read_text()
    assert fm.read(archive)[0] == {"kind": "log", "tier": "cold"}
    monkeypatch.setenv("BRAIN_TODAY", "2027-01-04")
    assert goals.account(goals.load(cfg)[0], date(2027, 1, 4))["thisWeek"] == {"week": "2027-W01", "text": None}
    assert not goals.problems(cfg)


def test_activity_combines_records_is_stable_read_only_and_human_readable(cfg, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_RUN", "routine")
    home = tmp_path / "runtime"
    (home / "works/renal").mkdir(parents=True)
    (home / "works/LIBRARY.md").write_text(
        "# Library\n- Renal practice | renal | tool | draft | editable | unknown | created 2026-11-05 | opened never | goal G-1\n")
    (home / "works/renal/HISTORY.md").write_text("# History\n\n## 2026-11-06 · T-002 · Prepare practice\n- Outcome: Made ten cards\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    task = tasks.new_task(cfg, "Prepare practice", "Medicine", goal="G-1", work="renal", values={"status": "Prepared ten cards", "delivers": "A practice deck"})
    continuation.checkpoint(cfg, task.tid, "Checked the renal answers.\nThe deck is held.")
    source = draft(cfg)
    rid = reception.record(cfg, "draft", "The renal deck", source.removesuffix(".md"))
    reception.set_outcome(cfg, rid, "used")
    ideas.add(cfg, TODAY, "Want short practice each day?", "Medicine", goal="G-1")
    ledger.append(cfg, "explore", "done", "G-1: Built a cardio-first order from your misses", "held", cfg.now())
    ledger.append(cfg, "improve", "progress", "G-1: Compared spaced practice approaches", "Continue research", cfg.now())
    before = snapshot(cfg.vault_path)
    items = json.loads(run(cfg, capsys, "activity", "G-1", "--json"))["G-1"]
    assert {item["kind"] for item in items} == {"task", "made", "research", "offer"}
    assert len(items) == 5
    assert items == goals.activity(cfg)["G-1"]
    assert snapshot(cfg.vault_path) == before
    assert all(goals.ITEM_ID.fullmatch(item["id"]) for item in items)
    assert all("G-1" not in item["text"] and "T-002" not in item["text"] for item in items)
    assert [item["at"] for item in items] == sorted((item["at"] for item in items), reverse=True)
    made = next(item for item in items if item["id"].startswith("draft:"))
    assert made["text"] == "Prepared a renal deck for your next review" and made["state"] == "done"
    assert any(rid in line and "used" in line for line in made["detail"])
    task_item = next(item for item in items if item["kind"] == "task")
    assert "Checked the renal answers" in task_item["text"] and task_item["unasked"]
    assert any("Outcome: Made ten cards" in line for line in task_item["detail"])
    assert "Delivers: A practice deck" in task_item["detail"]
    assert all("\n" not in line for item in items for line in item["detail"])


def test_export_windows_bounds_privacy_and_no_fabricated_dates(cfg):
    for n in range(23):
        draft(cfg, f"Deck {n}")
    draft(cfg, "Old deck", created="2026-10-24")
    draft(cfg, "Private deck", tier="private", index_line="PRIVATE DECK")
    draft(cfg, "Undated", created="unknown", index_line="UNDATED")
    draft(cfg, "Future", created="2026-12-01", index_line="FUTURE")
    private_source = cfg.vault_path / cfg.people_dir / "Person/Private.md"
    private_source.parent.mkdir(parents=True, exist_ok=True)
    fm.write(private_source, {"tier": "warm", "kind": "draft", "goal": "G-1", "created": TODAY}, "PRIVATE PERSON")
    before = snapshot(cfg.vault_path)
    exported = state.build(cfg, TODAY)["goals"][0]
    assert set(exported) == {"id", "title", "kind", "origin", "state", "by", "supersededBy", "words", "saidAt", "evidence", "links", "weight", "account", "activity"}
    assert exported["account"] == {"thisWeek": {"week": "2026-W45", "text": None}, "lastWeek": {"week": "2026-W44", "text": None}}
    assert len(exported["activity"]) == 20
    assert all(len(item["detail"]) <= 12 for item in exported["activity"])
    encoded = json.dumps(exported["activity"])
    assert all(needle not in encoded for needle in ("Old deck", "PRIVATE", "UNDATED", "FUTURE"))
    assert len(goals.activity(cfg, weeks=3)["G-1"]) == 24
    assert snapshot(cfg.vault_path) == before
    assert state.build(replace(cfg, edition="enterprise"), TODAY)["goals"] == []


@pytest.mark.parametrize("weight", ["less"])
def test_weight_owner_only_cached_immediately_and_persistent(cfg, capsys, monkeypatch, weight):
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:explore")
    before = snapshot(cfg.vault_path)
    with pytest.raises(SystemExit, match="person.*weights"):
        run(cfg, capsys, "G-1", "weight", weight)
    assert snapshot(cfg.vault_path) == before
    run(cfg, capsys, "G-1", "weight", weight, "--from-app")
    assert goals.load(cfg)[0].state["weight"] == weight
    assert f"weight: {weight}" in goals.context(cfg)
    assert f"weight: {weight}" in (cfg.repo / ".context.md").read_text()
    assert f"weight: {weight}" in goals.notices(cfg)
    assert json.loads(run(cfg, capsys, "list", "--json"))[0]["weight"] == weight
    assert not goals.problems(cfg)


def test_paused_goal_blocks_unasked_creation_but_preserves_requests(cfg, capsys, monkeypatch):
    run(cfg, capsys, "G-1", "weight", "pause", "--from-app")
    monkeypatch.setenv("BRAIN_RUN", "routine")
    with pytest.raises(ValueError, match="paused"):
        tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    with pytest.raises(ValueError, match="paused"):
        ideas.add(cfg, TODAY, "Want cards?", "Medicine", goal="G-1")
    task = tasks.new_task(cfg, "Requested cards", '"Please make cards"', goal="G-1")
    assert not next(item for item in goals.activity(cfg)["G-1"] if item["id"] == task.tid)["unasked"]
    monkeypatch.delenv("BRAIN_RUN")
    assert tasks.new_task(cfg, "Conversation request", "Medicine", goal="G-1")


def test_correction_settles_reception_parks_task_and_survives_notice_ack(cfg, capsys, monkeypatch):
    task = tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    rid = reception.record(cfg, "draft", "Practice cards", cfg.agent("Tasks") + "#" + task.tid)
    monkeypatch.setenv("BRAIN_SPEAKER", "cron:explore")
    before = snapshot(cfg.vault_path)
    with pytest.raises(SystemExit):
        run(cfg, capsys, "G-1", "correct", task.tid)
    assert snapshot(cfg.vault_path) == before
    reason = "Please stop making these · use questions instead"
    run(cfg, capsys, "G-1", "correct", task.tid, "--why", reason, "--from-app")
    parked = tasks.find(cfg, task.tid)
    assert parked.task_state == "set_aside" and reason in parked.body
    row = next(row for _, row in reception.rows(cfg) if row.id == rid)
    assert row.outcome == "declined:" + reason
    item = next(item for item in goals.activity(cfg)["G-1"] if item["id"] == task.tid)
    assert item["corrected"]["why"] == reason and item["state"] == "declined"
    goals.acknowledge(cfg, goals.capture(cfg)["app"])
    assert "Not this (task): Prepare cards" in goals.context(cfg)
    assert reason in (cfg.repo / ".context.md").read_text()
    corrections = json.loads(run(cfg, capsys, "list", "--json"))[0]["corrections"]
    assert corrections[0]["id"] == task.tid and corrections[0]["why"] == reason


def test_correct_done_task_and_old_made_item_without_reception(cfg, capsys):
    task = tasks.new_task(cfg, "Finished cards", "Medicine", goal="G-1")
    tasks.close(cfg, task.tid, TODAY, True, "Ready")
    run(cfg, capsys, "G-1", "correct", task.tid)
    assert tasks.find(cfg, task.tid).task_state == "completed"
    draft(cfg, "Old", created="2026-08-01")
    item = next(item for item in goals.activity(cfg, weeks=None)["G-1"] if item["id"].startswith("draft:"))
    run(cfg, capsys, "G-1", "correct", item["id"])
    assert goals.capture(cfg)["corrections"]["G-1"][item["id"]]["why"] == ""
    goals.new(cfg, "Run", "Run more")
    before = snapshot(cfg.vault_path)
    with pytest.raises(SystemExit, match="not an activity item"):
        run(cfg, capsys, "G-2", "correct", task.tid)
    assert snapshot(cfg.vault_path) == before


@pytest.mark.parametrize("answer", ["yes", "no", "not-now", "stop-asking"])
def test_answered_ideas_keep_their_goal_and_proposition(cfg, answer):
    ideas.add(cfg, TODAY, "Want daily practice?", "Medicine", goal="G-1")
    idea = ideas._open(cfg, TODAY)[0]
    ideas.respond(cfg, idea.id, TODAY, answer)
    item = next(item for item in goals.activity(cfg)["G-1"] if item["id"] == "idea:" + idea.id)
    assert item["text"] == "Want daily practice?"
    assert item["state"] == ("done" if answer == "yes" else "held for you" if answer == "not-now" else "declined")
    assert any(answer in line for line in item["detail"])


@pytest.mark.parametrize("argv", [
    ("G-1", "weight", "high"), ("G-1", "weight", "pause; state=reached"),
    ("G-1", "correct", "bad/id"), ("G-1", "correct", "x" * 81),
    ("G-1", "account", "Bad\naccount"), ("G-1", "account", "<!-- hidden -->"),
    ("G-1", "account", ""), ("G-1", "correct", "T-001", "--why", "Bad\nreason"),
    ("activity", "--weeks", "0"), ("activity", "--weeks", "-1"), ("activity", "G-999"),
    ("activity", "--weeks", "99999999999999999999"),
    ("list", "--weeks", "1"), ("activity", "G-1", "extra"),
    ("G-1", "account", "I worked on G-1."), ("G-1", "account", "I finished T-012."),
    ("G-1", "account", "I read [[Medicine]]."), ("G-1", "account", "I read Medicine.md."),
    ("G-1", "account", "I read agent/Prepared/Review."), ("G-1", "account", r"I read agent\Review."),
    ("G-1", "account", "I made cards.", "--week", "2025-W53"),
    ("G-1", "account", "I made cards.", "--week", "2026-W00"),
    ("G-1", "account", "I made cards.", "--week", "2026-W1"),
    ("G-1", "weight", "more", "--week", "2026-W45"),
])
def test_invalid_goal_work_commands_do_not_write(cfg, capsys, argv):
    before = snapshot(cfg.vault_path)
    with pytest.raises(SystemExit):
        run(cfg, capsys, *argv)
    assert snapshot(cfg.vault_path) == before


def test_stamp_tracks_runtime_activity_and_corrections_in_a_hidden_runtime_folder(cfg, tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    folder = home / "records/continue/tasks"
    folder.mkdir(parents=True)
    monkeypatch.setenv("AGENT_HOME", str(home))
    before = state.stamp(cfg)
    (folder / "T-001.json").write_text('{"task":"T-001","text":"A result"}')
    assert before != state.stamp(cfg)
    config = replace(cfg, runtime_dir=".runtime")
    before = state.stamp(config)
    values = goals.capture(config)
    values["corrections"] = {"G-1": {"draft:abc": {"at": str(TODAY), "why": "Not this", "kind": "made", "text": "A deck"}}}
    goals.save_capture(config, values)
    assert state.stamp(config) != before


def test_archived_task_and_made_corrections_remain_visible(cfg, capsys):
    task = tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    tasks.write_archive(cfg, task, "warm", TODAY, whole=True)
    data, body = fm.read(cfg.vault_path / cfg.agent("Tasks.md"))
    fm.write(cfg.vault_path / cfg.agent("Tasks.md"), data, body.replace(task.render(), task.render_line(cfg)))
    run(cfg, capsys, "G-1", "correct", task.tid, "--why", "Try a different format")
    archived = tasks.read_archived_task(cfg, tasks.archive_path(cfg, task.tid))
    assert archived.task_state == "set_aside" and "Try a different format" in archived.body
    source = draft(cfg)
    rid = reception.record(cfg, "draft", "Renal deck", source)
    item = next(item for item in goals.activity(cfg)["G-1"] if item["id"].startswith("draft:"))
    before = state.stamp(cfg)
    run(cfg, capsys, "G-1", "correct", item["id"], "--why", "Prefer practice questions")
    assert state.stamp(cfg) != before
    assert next(row for _, row in reception.rows(cfg) if row.id == rid).outcome == "declined:Prefer practice questions"
    exported = state.build(cfg, TODAY)["goals"][0]["activity"]
    assert next(row for row in exported if row["id"] == item["id"])["corrected"]["why"] == "Prefer practice questions"


def test_reception_on_goal_hub_and_exact_ledger_goal_matches(cfg):
    rid = reception.record(cfg, "message", "A study finding for you", "Medicine")
    reception.set_outcome(cfg, rid, "liked")
    ledger.append(cfg, "explore", "done", "G-10: Work for another goal", "", cfg.now())
    ledger.append(cfg, "explore", "nothing", "G-1: Nothing useful today", "", cfg.now())
    ledger.append(cfg, "other", "done", "G-1: A different routine", "", cfg.now())
    items = goals.activity(cfg)["G-1"]
    assert len(items) == 1 and items[0]["id"] == "offer:" + rid and items[0]["state"] == "done"


def test_goal_account_only_and_asked_draft_provenance(cfg, capsys):
    task = tasks.new_task(cfg, "Asked cards", '"Please make cards"', goal="G-1")
    draft(cfg, task=task.tid)
    other = tasks.new_task(cfg, "Requested cards", '"Please prepare cards"')
    draft(cfg, "Other deck", task=other.tid)   # provenance follows the owning task even when it has no goal
    assert all(not item["unasked"] for item in goals.activity(cfg)["G-1"])
    run(cfg, capsys, "G-1", "account", "I made the cards you asked for. They are held for you.")
    assert state.build(cfg, TODAY)["goals"][0]["account"]["thisWeek"]["text"] == "I made the cards you asked for. They are held for you."


def test_activity_skips_invalid_reception_dates_and_deduplicates_receipts(cfg):
    rid = reception.record(cfg, "draft", "Renal deck", draft(cfg))
    path, row = next((path, row) for path, row in reception.rows(cfg) if row.id == rid)
    path.write_text(path.read_text().replace(row.at, "2026-99-99 12:00"))
    receipt = ledger.append(cfg, "explore", "done", "G-1: Prepared a review", "held", cfg.now())
    text = receipt.read_text()
    receipt.write_text(text + text.splitlines()[-1] + "\n")
    items = goals.activity(cfg)["G-1"]
    assert len(items) == 2 and len({item["id"] for item in items}) == 2
    assert not any(rid in line for item in items for line in item["detail"])


def test_item_links_use_app_targets(cfg, tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    (home / "works").mkdir(parents=True)
    (home / "works/LIBRARY.md").write_text(
        "- Renal practice | renal | tool | draft | editable | unknown | created 2026-11-07 | opened never | goal G-1\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    task = tasks.new_task(cfg, "Prepare practice", "Medicine", goal="G-1")
    source = draft(cfg)
    ideas.add(cfg, TODAY, "Want daily practice?", "Medicine#Improvements", goal="G-1")
    rid = reception.record(cfg, "message", "A finding", "Medicine#Findings")
    items = goals.activity(cfg)["G-1"]
    assert next(item for item in items if item["id"] == task.tid)["link"] == task.tid
    assert next(item for item in items if item["id"] == "work:renal")["link"] == "renal"
    assert next(item for item in items if item["id"].startswith("draft:"))["link"] == source
    assert next(item for item in items if item["id"].startswith("idea:"))["link"] == "Medicine.md"
    assert next(item for item in items if item["id"] == "offer:" + rid)["link"] == "Medicine.md"
    tasks.write_archive(cfg, task, "warm", TODAY, whole=True)
    data, body = fm.read(cfg.vault_path / cfg.agent("Tasks.md"))
    fm.write(cfg.vault_path / cfg.agent("Tasks.md"), data, body.replace(task.render(), task.render_line(cfg)))
    assert next(item for item in goals.activity(cfg)["G-1"] if item["id"] == task.tid)["link"] == task.tid


def test_offers_from_one_note_keep_separate_outcomes_and_corrections(cfg, capsys):
    first = reception.record(cfg, "message", "A study finding", "Medicine#Findings")
    second = reception.record(cfg, "message", "A different finding", "Medicine#Findings")
    reception.set_outcome(cfg, first, "liked")
    reception.set_outcome(cfg, second, "used")
    before = goals.activity(cfg)["G-1"]
    assert {item["id"] for item in before} == {"offer:" + first, "offer:" + second}
    assert all(len(item["detail"]) == 1 for item in before)
    run(cfg, capsys, "G-1", "correct", "offer:" + first, "--why", "Prefer practice")
    outcomes = {row.id: row.outcome for _, row in reception.rows(cfg)}
    assert outcomes == {first: "declined:Prefer practice", second: "used"}
    after = {item["id"]: item for item in goals.activity(cfg)["G-1"]}
    assert after["offer:" + first]["state"] == "declined"
    assert after["offer:" + second] == next(item for item in before if item["id"] == "offer:" + second)


@pytest.mark.parametrize("kind", ["idea", "task", "research", "proposal"])
def test_links_do_not_attribute_other_offers_to_their_goal(cfg, kind):
    fm.write(cfg.vault_path / "Unowned.md", {"kind": "hub", "tier": "warm"},
             "# Unowned\n\n## Improvements\n- **Goal:** G-1\n")
    if kind == "idea":
        ideas.add(cfg, TODAY, "Want daily practice?", "Unowned", goal="G-1")
    elif kind == "task":
        tasks.new_task(cfg, "Prepare practice", "Unowned", goal="G-1", values={"hub": "Unowned"})
    elif kind == "research":
        path = ledger.append(cfg, "explore", "done", "G-1: Compared practice approaches", "", cfg.now())
    source = path.relative_to(cfg.vault_path).as_posix() if kind == "research" else "Unowned"
    rid = reception.record(cfg, "message", "An unrelated finding", source)
    assert all(item["id"] != "offer:" + rid for item in goals.activity(cfg)["G-1"])


def test_untagged_answered_fallback_does_not_inherit_hub_or_draft_goal(cfg):
    source = draft(cfg)
    path = cfg.vault_path / cfg.agent("Answered/2026-11.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"- {TODAY} · dismissed no · idea · Untagged hub idea · id:0123456789ab · [[Medicine]]\n"
                    f"- {TODAY} · dismissed no · idea · Untagged draft idea · id:0123456789ac · [[{source}]]\n"
                    f"- {TODAY} · dismissed no · idea · Tagged idea · id:0123456789ad · goal:G-1 · [[Medicine]]\n")
    ids = {item["id"] for item in goals.activity(cfg)["G-1"]}
    assert "idea:0123456789ab" not in ids and "idea:0123456789ac" not in ids
    assert "idea:0123456789ad" in ids


def test_answered_loop_is_not_an_idea_even_with_a_matching_response_id(cfg):
    source = "Medicine · goal:G-1 · Notes"
    fm.write(cfg.vault_path / f"{source}.md", {"kind": "hub", "tier": "warm"}, "# Notes\n")
    needs.answered(cfg, TODAY,
                   f"- {TODAY} · idea · Earlier idea · yes · id:0123456789ab · [[Medicine]]")
    needs.answered(cfg, TODAY,
                   f"- {TODAY} 12:00 · loop done · promised · Send notes · because Sent"
                   f" · id:0123456789ab · [[{source}]]")
    assert goals.activity(cfg)["G-1"] == []


def test_owned_work_and_receipts_fold_once_and_keep_recent_task_visible(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_RUN", "routine")
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-01")
    task = tasks.new_task(cfg, "Prepare practice", "Medicine", goal="G-1", work="renal")
    monkeypatch.setenv("BRAIN_TODAY", str(TODAY))
    home = tmp_path / "runtime"
    (home / "works/renal").mkdir(parents=True)
    (home / "works/LIBRARY.md").write_text(
        "- Renal practice | renal | tool | draft | editable | unknown | created 2026-11-07 | opened never | goal G-1\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    source = draft(cfg, task=task.tid)
    rid = reception.record(cfg, "draft", "A deck", source)
    ledger.append(cfg, "explore", "done", f"G-1: Prepared {task.tid}", "held", cfg.now())
    ledger.append(cfg, "improve", "done", f"G-1: Checked [[{source}]]", "held", cfg.now())
    items = goals.activity(cfg)["G-1"]
    assert len(items) == 1 and items[0]["id"] == task.tid
    assert items[0]["unasked"]
    detail = "\n".join(items[0]["detail"])
    assert all(value in detail for value in ("Renal practice", "# Renal deck", rid, "Prepared T-", "Checked [["))


def test_standalone_draft_receipt_folds_but_other_goal_work_stays_separate(cfg):
    goals.new(cfg, "Exercise", "Run more")
    task = tasks.new_task(cfg, "Requested cards", '"Please make cards"', goal="G-2")
    source = draft(cfg, task=task.tid)
    ledger.append(cfg, "explore", "done", f"G-1: Prepared [[{source}]]", "held", cfg.now())
    items = goals.activity(cfg)["G-1"]
    assert len(items) == 1 and items[0]["id"].startswith("draft:")
    assert any("Prepared [[" in line for line in items[0]["detail"])
    assert not items[0]["unasked"]
    assert goals.activity(cfg)["G-2"][0]["id"] == task.tid


@pytest.mark.parametrize(("raw", "titles", "expected"), [
    ("work: Built [[agent/Prepared/Renal.md|a renal deck]], held", {}, "Built a renal deck"),
    ("people: Read [[path/Cardio.md]], filed", {}, "Read Cardio"),
    ("occasion: Shared agent/Prepared/Renal.md, messaged", {}, "Shared Renal"),
    ("research: Read [[path/Cardio]], delivered", {}, "Read Cardio"),
    ("work: G-1: Compared T-012 and G-99, nothing", {"T-012": "a task"}, "Compared a task and the goal"),
    ("gesture: Shared /agent/Prepared/Renal.md, delivered", {}, "Shared Renal"),
    ("Finished T-012 for G-1", {"T-012": "work: Read [[path/Cardio.md]], held", "G-1": "Learn G-2"},
     "Finished Read Cardio for Learn the goal"),
])
def test_readable_removes_storage_syntax_from_text_and_referenced_titles(raw, titles, expected):
    assert goal_activity.readable(raw, titles) == expected


def test_legacy_idea_reception_still_matches_after_humanizing_ids(cfg):
    fm.write(cfg.vault_path / cfg.agent("Ideas.md"), {},
             f"## Open\n- {TODAY} · Want practice for G-1? · [[Medicine]]\n  - goal: G-1\n")
    reception.record(cfg, "idea", "Want practice for G-1?", "Medicine")
    rid = reception.rows(cfg)[0][1].id
    reception.set_outcome(cfg, rid, "used")
    items = goals.activity(cfg)["G-1"]
    assert len(items) == 1 and items[0]["id"].startswith("idea:")
    assert items[0]["state"] == "done" and "G-1" not in items[0]["text"]


@pytest.mark.parametrize("source", ["status", "result", "checkpoint"])
def test_task_text_cleans_title_and_every_result_source(cfg, monkeypatch, source):
    task = tasks.new_task(cfg, "work: Review [[path/Medicine.md]]", "Medicine", goal="G-1",
                          values={"status": "people: Read [[path/Cardio.md|the cards]], held"})
    if source == "result":
        tasks.close(cfg, task.tid, TODAY, True, "work: Delivered [[path/Renal.md]], delivered")
    elif source == "checkpoint":
        monkeypatch.setattr(continuation, "_checkpoint", lambda tid: {
            "at": str(TODAY), "text": "occasion: Checked path/Renal.md for G-1 and T-012, filed"})
    text = goals.activity(cfg)["G-1"][0]["text"]
    assert all(value not in text for value in ("[[", ".md", "path/", "G-1", "T-012", "work:", "people:", "occasion:", ", held", ", filed", ", delivered"))
    assert text.startswith("Review Medicine — ")


@pytest.mark.parametrize(("day", "hour", "expected"), [
    ("2027-01-04", 0, "2026-W53"), ("2027-01-04", 11, "2026-W53"),
    ("2027-01-04", 12, "2027-W01"), ("2027-01-05", 8, "2027-W01"),
])
def test_account_defaults_to_yesterdays_week_before_local_noon(cfg, capsys, monkeypatch, day, hour, expected):
    monkeypatch.setattr(Config, "now", lambda self, moment=None: moment or datetime.fromisoformat(day).replace(hour=hour, tzinfo=self.zone))
    run(cfg, capsys, "G-1", "account", "I prepared cards. They are held for you.")
    assert goals.ACCOUNT.findall(goals.load(cfg)[0].body) == [(expected, "I prepared cards. They are held for you.")]


@pytest.mark.parametrize(("speaker", "mode", "skill", "allowed"), [
    ("cli:owner", "", "", True), ("demiurge:stranger", "", "", False),
    ("cron:people", "routine", "", True), ("cron:brain-people", "routine", "", True),
    ("cron:people", "", "", False), ("cron:people", "worker", "", False),
    ("cron:explore", "routine", "", False), ("subagent:people", "worker", "brain-people", False),
    ("cron:night", "routine", "brain-people", True), ("cron:night", "routine", "people", True),
    ("cron:night", "routine", "brain-explore", False),
])
def test_account_owner_or_people_routine_only(cfg, capsys, monkeypatch, speaker, mode, skill, allowed):
    monkeypatch.setenv("BRAIN_SPEAKER", speaker)
    monkeypatch.setenv("BRAIN_RUN", mode)
    monkeypatch.setenv("BRAIN_SKILL", skill)
    before = snapshot(cfg.vault_path)
    if allowed:
        run(cfg, capsys, "G-1", "account", "I prepared cards. They are held.", "--week", "2026-W45")
        assert goals.account(goals.load(cfg)[0], TODAY)["thisWeek"]["text"]
    else:
        with pytest.raises(SystemExit, match="only the owner or the people routine"):
            run(cfg, capsys, "G-1", "account", "I prepared cards. They are held.", "--week", "2026-W45")
        assert snapshot(cfg.vault_path) == before


@pytest.mark.parametrize("marker", [None, "routine", "worker", "task", "cron", "subagent"])
def test_unasked_requires_creation_inside_guard_routine(cfg, monkeypatch, marker):
    if marker in ("routine", "worker"):
        monkeypatch.setenv("BRAIN_RUN", marker)
    elif marker == "task":
        monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    elif marker:
        monkeypatch.setenv("BRAIN_SPEAKER", marker + ":explore")
    task = tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    assert (task.state["unasked"] == "true") == (marker is not None)
    monkeypatch.delenv("BRAIN_RUN", raising=False)
    monkeypatch.delenv("DEMIURGE_TASK", raising=False)
    monkeypatch.setenv("BRAIN_SPEAKER", "cli:owner")
    assert goal_activity._unasked(task) == (marker is not None)


def test_unasked_excludes_unknown_quoted_yours_and_handoff_provenance(cfg, monkeypatch):
    task = tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    task.state.pop("unasked")
    assert not goal_activity._unasked(task)
    monkeypatch.setenv("BRAIN_RUN", "worker")
    asked = tasks.new_task(cfg, "Requested cards", '“Please make cards”', goal="G-1")
    asked.state["unasked"] = "true"
    assert not goal_activity._unasked(asked)
    yours = tasks.new_task(cfg, "My cards", "Medicine", state="yours", goal="G-1")
    assert not goal_activity._unasked(yours)
    parent = tasks.new_task(cfg, "Requested practice", '"Please make practice"', goal="G-1")
    tasks.hand_off(cfg, parent.tid, TODAY, "here")
    monkeypatch.setenv("DEMIURGE_TASK", parent.tid)
    followup = tasks.new_task(cfg, "Follow up", "Medicine")
    assert followup.goal == "G-1" and followup.state["unasked"] == "false"
    assert not goal_activity._unasked(followup)


def test_repeated_correction_of_set_aside_task_does_not_add_task_lines(cfg, capsys):
    task = tasks.new_task(cfg, "Prepare cards", "Medicine", goal="G-1")
    run(cfg, capsys, "G-1", "correct", task.tid, "--why", "Prefer practice")
    before = tasks.find(cfg, task.tid).render()
    run(cfg, capsys, "G-1", "correct", task.tid, "--why", "Prefer practice")
    assert tasks.find(cfg, task.tid).render() == before
