"""The engine gates background work and applies a separate reviewer's direction check."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brain import cli as brain_cli, continuation, journal, needs, signals, tasks
from brain.config import Config


@pytest.fixture
def worker(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    clock = [datetime(2026, 11, 6, 12, tzinfo=ZoneInfo("America/Los_Angeles"))]
    monkeypatch.setattr(Config, "now", lambda self, moment=None: clock[0])
    config = Config.load(vault)
    task = tasks.load_tasks(config)[0][0]
    task.body += '- **Source:** "Publish my apartment shortlist in the Library"\n'
    tasks.save_task(config, task, config.today())
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "a shortlist published in the Library"})
    tasks.hand_off(config, "T-001", config.today(), "demiurge:owner", work="shortlist")
    return config, clock


def cli(config, capsys, *args):
    brain_cli.main(["continue", *args, "--vault", str(config.vault_path)])
    return capsys.readouterr().out.strip()


def due(config, capsys, *args):
    return json.loads(cli(config, capsys, "--due", "--json", *args))


def context(config, capsys):
    return json.loads(cli(config, capsys, "--review-context", "T-001", "--json"))


def verdict(config, capsys, name, reason="the shortlist is moving", *args):
    return cli(config, capsys, "--review", "T-001", "--verdict", name, "--reason", reason, *args)


def task(config):
    return tasks.load_tasks(config)[0][0]


def count(config, capsys, clock, n=1):
    for _ in range(n):
        clock[0] += timedelta(minutes=30)
        folder = continuation.runtime.home() / "records/works"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps({"shortlist": {"lastPush": clock[0].isoformat()}}))
        cli(config, capsys, "--chunk", "T-001")


@pytest.mark.parametrize("daily", [False, True])
@pytest.mark.parametrize("work", [False, True])
def test_missing_delivers_raises_one_standing_ask_and_returns_when_set(worker, capsys, daily, work):
    config, clock = worker
    tasks.set_fields(config, "T-001", config.today(), clear=["delivers", *([] if work else ["work"])])
    if daily:
        tasks.set_pace(config, "T-001", "daily", config.today())
    options = ["--daily"] if daily else []
    before = (config.vault_path / "agent/Tasks.md").read_text()
    for _ in range(3):
        assert due(config, capsys, *options) == []
        assert signals.continue_(config, daily=daily) == "date: none"
    questions = needs.items(config, config.today())
    assert len(questions) == 1
    assert questions[0].text == "What would done look like for Sample? Until then Agent won't keep working on it in the background."
    assert "T-001" in questions[0].source
    first = (config.vault_path / "agent/Tasks.md").read_text()
    due(config, capsys, *options)
    assert (config.vault_path / "agent/Tasks.md").read_text() == first != before
    assert task(config).task_state == "in_progress"
    tasks.set_fields(config, "T-001", config.today(), values={"delivers": "a published shortlist"})
    assert due(config, capsys, *options)[0]["id"] == "T-001"
    assert needs.items(config, config.today()) == []


def test_a_task_waiting_on_its_owner_is_not_asked_its_finish_line_until_it_moves(worker, capsys):
    config, _ = worker
    tasks.set_fields(config, "T-001", config.today(), clear=["delivers", "work"])
    due(config, capsys)
    assert [item.text for item in needs.items(config, config.today())] == [
        "What would done look like for Sample? Until then Agent won't keep working on it in the background."]
    tasks.set_fields(config, "T-001", config.today(), state="waiting_for_owner",
                     values={"ask": "Does the shortlist page open for you?", "why": "Then I can finish it."})
    due(config, capsys)
    assert [item.text for item in needs.items(config, config.today())] == ["Does the shortlist page open for you?"]
    assert "finish_asked" not in task(config).state
    tasks.set_fields(config, "T-001", config.today(), state="in_progress")
    due(config, capsys)
    assert [item.text for item in needs.items(config, config.today())] == [
        "What would done look like for Sample? Until then Agent won't keep working on it in the background."]


def test_gate_works_without_runtime_and_does_not_touch_unhanded_tasks(worker, capsys, monkeypatch):
    config, _ = worker
    monkeypatch.delenv("AGENT_HOME")
    tasks.set_fields(config, "T-001", config.today(), clear=["delivers", "handoff"])
    before = (config.vault_path / "agent/Tasks.md").read_bytes()
    assert due(config, capsys) == []
    assert (config.vault_path / "agent/Tasks.md").read_bytes() == before
    assert needs.items(config, config.today()) == []
    tasks.hand_off(config, "T-001", config.today())
    assert due(config, capsys) == []
    assert due(config, capsys) == []
    assert len(needs.items(config, config.today())) == 1


def test_review_count_default_cadence_reset_on_review_and_engagement(worker, capsys):
    config, clock = worker
    assert {k: due(config, capsys)[0][k] for k in ("reviewDue", "chunksSinceReview", "lastReview")} == {
        "reviewDue": False, "chunksSinceReview": 0, "lastReview": None}
    count(config, capsys, clock, 3)
    assert due(config, capsys)[0]["reviewDue"] is False
    count(config, capsys, clock)
    assert due(config, capsys)[0]["reviewDue"] is True
    assert due(config, capsys)[0]["chunksSinceReview"] == 4
    verdict(config, capsys, "converging", "each result removes a delivery step")
    row = due(config, capsys)[0]
    assert row["chunksSinceReview"] == 0 and row["reviewDue"] is False
    assert row["lastReview"] == {"verdict": "converging", "at": clock[0].isoformat(), "reason": "each result removes a delivery step"}
    count(config, capsys, clock)
    # Same timestamp still resets the count: engagement is an event, not a date comparison.
    cli(config, capsys, "--engaged", "T-001", "--instruction", "Only the three finalists, please")
    assert due(config, capsys)[0]["chunksSinceReview"] == 0
    assert context(config, capsys)["ownerInstruction"] == "Only the three finalists, please"
    assert due(config, capsys)[0]["lastReview"] == row["lastReview"]


def test_review_cadence_is_configurable(worker, capsys):
    config, clock = worker
    with (config.repo / "brain.toml").open("a") as stream:
        stream.write("\n[continue]\nreview_every = 2\n")
    count(config, capsys, clock, 2)
    assert due(config, capsys)[0]["reviewDue"] is True


def test_review_context_six_results_source_budget_and_last_review(worker, capsys):
    config, clock = worker
    for i in range(8):
        clock[0] += timedelta(seconds=1)
        cli(config, capsys, "--checkpoint", "T-001", "--text", f"Verified delivery step {i}")
        count(config, capsys, clock)
    data = context(config, capsys)
    assert data == {
        "id": "T-001", "title": "Sample", "delivers": "a shortlist published in the Library", "work": "shortlist",
        "status": "open", "next": "ORIGINAL_FULL_BODY",
        "source": '"Publish my apartment shortlist in the Library"',
        "ownerInstruction": '"Publish my apartment shortlist in the Library"',
        "results": data["results"], "lastReview": None, "chunksToday": 8, "budgetLeft": 8}
    assert [r["text"] for r in data["results"]] == [f"Verified delivery step {i}" for i in range(2, 8)]
    assert all(datetime.fromisoformat(r["at"]).tzinfo is not None for r in data["results"])
    verdict(config, capsys, "converging")
    assert context(config, capsys)["lastReview"]["verdict"] == "converging"
    assert len(context(config, capsys)["results"]) == 6


def test_legacy_context_uses_last_checkpoint(worker, capsys):
    config, _ = worker
    cli(config, capsys, "--checkpoint", "T-001", "--text", "Published the first shortlist")
    assert context(config, capsys)["results"][0]["text"] == "Published the first shortlist"


def test_owner_task_edits_and_answered_asks_are_available_to_reviewer(worker, capsys, monkeypatch):
    config, clock = worker
    count(config, capsys, clock, 2)
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "owner")
    brain_cli.main(["task", "T-001", "set", "--next", "Publish just the finalists", "--vault", str(config.vault_path)])
    capsys.readouterr()
    assert context(config, capsys)["ownerInstruction"] == "Publish just the finalists"
    assert due(config, capsys)[0]["chunksSinceReview"] == 0
    journal.add_ask(config, config.today(), "Publish the three finalists?", "agent/Tasks#T-001")
    brain_cli.main(["needs-you", "--resolve", "Publish the three finalists?", "--because", "Yes, just those three",
                  "--vault", str(config.vault_path)])
    capsys.readouterr()
    assert context(config, capsys)["ownerInstruction"] == "Publish the three finalists? Answer: Yes, just those three"
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    count(config, capsys, clock)
    brain_cli.main(["task", "T-001", "set", "--next", "worker's plan", "--vault", str(config.vault_path)])
    capsys.readouterr()
    assert due(config, capsys)[0]["chunksSinceReview"] == 1
    assert "Yes, just those three" in context(config, capsys)["ownerInstruction"]


@pytest.mark.parametrize("close", [False, True])
def test_met_begins_next_chunk_instructions_and_clears_after_one_chunk_or_close(worker, capsys, close):
    config, clock = worker
    cli(config, capsys, "--checkpoint", "T-001", "--text", "The shortlist is published")
    verdict(config, capsys, "met", "the three finalists are visible")
    expected = ("The reviewer judged the Delivers met (the three finalists are visible). This chunk closes the task: "
                "verify the deliverable exactly as the Delivers says, then `brain task T-001 done` with the evidence "
                "and tell Human once where the task reports, or, if it is not actually met, say precisely what is missing "
                "with `brain task T-001 set --next …`.")
    assert due(config, capsys)[0]["checkpoint"].startswith(expected)
    assert cli(config, capsys, "T-001").startswith(expected)
    assert json.loads(cli(config, capsys, "T-001", "--json"))["checkpoint"].startswith(expected)
    assert task(config).task_state == "in_progress"
    if close:
        brain_cli.main(["task", "T-001", "done", "--why", "verified the Library", "--vault", str(config.vault_path)])
        capsys.readouterr()
        assert "close_out" not in continuation._progress(config)["T-001"]
        assert task(config).task_state == "completed"
    else:
        count(config, capsys, clock)
        assert due(config, capsys)[0]["checkpoint"] == "The shortlist is published"
    assert context(config, capsys)["lastReview"]["verdict"] == "met"


def test_circling_nonblocking_ask_halves_remaining_budget_and_second_review_waits(worker, capsys):
    config, clock = worker
    count(config, capsys, clock, 4)
    verdict(config, capsys, "circling", "adding listings instead of choosing", "--ask", "Should the shortlist stop at three apartments?")
    row = due(config, capsys)[0]
    assert (row["chunksToday"], row["budget"], row["budgetLeft"]) == (4, 10, 6)
    assert task(config).task_state == "in_progress"
    assert len(needs.items(config, config.today())) == 1
    assert needs.items(config, config.today())[0].text == "Should the shortlist stop at three apartments?"
    verdict(config, capsys, "circling", "still adding listings", "--ask", "Should the shortlist stop at two apartments?")
    assert task(config).task_state == "waiting_for_owner"
    assert due(config, capsys) == []
    questions = needs.items(config, config.today())
    assert len(questions) == 1 and questions[0].text == "Should the shortlist stop at two apartments?"
    assert not needs.open_asks((config.vault_path / "CURRENT.md").read_text())


def test_owner_engagement_clears_pending_circling_and_restores_budget(worker, capsys):
    config, clock = worker
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at three apartments?")
    count(config, capsys, clock)
    cli(config, capsys, "--engaged", "T-001")
    assert due(config, capsys)[0]["budget"] == 16
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at two apartments?")
    assert task(config).task_state == "in_progress"
    assert due(config, capsys)[0]["budgetLeft"] == 7   # floor(15 / 2)
    clock[0] += timedelta(days=1)
    assert due(config, capsys)[0]["budget"] == 8   # penalty only applied on the review's local day


@pytest.mark.parametrize("ask", [None, "Do you approve the proposed action?"])
def test_circling_refuses_missing_or_generic_ask_without_changes(worker, capsys, ask):
    config, _ = worker
    before = (config.vault_path / "agent/Tasks.md").read_bytes()
    with pytest.raises(SystemExit) as error:
        verdict(config, capsys, "circling", "drifting", *(["--ask", ask] if ask else []))
    assert error.value.code == 2
    assert capsys.readouterr().err
    assert (config.vault_path / "agent/Tasks.md").read_bytes() == before
    assert context(config, capsys)["lastReview"] is None


@pytest.mark.parametrize("resume", [None, "2026-11-08 10:30"])
def test_stuck_blocks_until_resume_with_reason(worker, capsys, resume):
    config, clock = worker
    verdict(config, capsys, "stuck", "The publishing laptop is offline", *(["--resume", resume] if resume else []))
    current = task(config)
    assert current.task_state == "blocked"
    assert "- **Blocker:** The publishing laptop is offline" in current.body
    assert current.resume == (resume or "2026-11-07 08:00")
    assert due(config, capsys) == []
    clock[0] = datetime.fromisoformat(current.resume).replace(tzinfo=config.zone)
    assert due(config, capsys)[0]["id"] == "T-001"


def test_stuck_accepts_computer_resume_and_waits_for_wake(worker, capsys):
    config, clock = worker
    verdict(config, capsys, "stuck", "The publishing laptop is offline", "--resume", "computer")
    assert task(config).resume == "computer" and task(config).task_state == "blocked"
    clock[0] += timedelta(days=1)
    assert due(config, capsys) == []
    assert cli(config, capsys, "--wake", "computer") == "T-001 woken: Sample"
    assert due(config, capsys)[0]["id"] == "T-001"


def test_daily_circling_cap_applies_and_can_reach_zero(worker, capsys):
    config, clock = worker
    tasks.set_pace(config, "T-001", "daily", config.today())
    assert due(config, capsys, "--daily")[0]["budgetLeft"] is None
    count(config, capsys, clock, 15)
    verdict(config, capsys, "circling", "scope keeps growing", "--ask", "Should the shortlist stop at three apartments?")
    assert due(config, capsys, "--daily") == []   # floor(1 / 2) = 0 more chunks today


def test_review_task_writes_hold_vault_lock(worker, capsys, monkeypatch):
    config, _ = worker
    held = False
    original = tasks.save_task

    @contextmanager
    def locked(cfg):
        nonlocal held
        assert not held
        held = True
        try:
            yield
        finally:
            held = False

    def guarded(*args, **kwargs):
        assert held
        return original(*args, **kwargs)

    monkeypatch.setattr(continuation, "vault_lock", locked)
    monkeypatch.setattr(tasks, "save_task", guarded)
    verdict(config, capsys, "stuck", "The publishing laptop is offline")


def test_stuck_before_morning_defaults_to_today(worker, capsys):
    config, clock = worker
    clock[0] = clock[0].replace(hour=2)
    verdict(config, capsys, "stuck", "The publishing laptop is offline")
    assert task(config).resume == "2026-11-06 08:00"


def test_unchanged_checkpoint_is_not_a_new_chunk_result(worker, capsys):
    config, clock = worker
    cli(config, capsys, "--checkpoint", "T-001", "--text", "Published one shortlist")
    count(config, capsys, clock, 2)
    assert [r["text"] for r in context(config, capsys)["results"]] == ["Published one shortlist", "open"]


def test_single_daily_task_json_keeps_daily_budget_shape(worker, capsys):
    config, _ = worker
    tasks.set_pace(config, "T-001", "daily", config.today())
    single = json.loads(cli(config, capsys, "T-001", "--json"))
    assert single["budget"] is None and single["budgetLeft"] is None


def test_answering_a_steering_ask_is_engagement_but_worker_resolution_is_not(worker, capsys, monkeypatch):
    config, clock = worker
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at three apartments?")
    count(config, capsys, clock)
    monkeypatch.setenv("DEMIURGE_CONVERSATION", "owner")
    brain_cli.main(["needs-you", "--resolve", "Should the shortlist stop at three apartments?", "--because", "Yes, just three",
                  "--vault", str(config.vault_path)])
    capsys.readouterr()
    assert due(config, capsys)[0]["chunksSinceReview"] == 0
    assert due(config, capsys)[0]["budget"] == 16
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at two apartments?")
    assert task(config).task_state == "in_progress"
    count(config, capsys, clock)
    monkeypatch.setenv("DEMIURGE_TASK", "T-001")
    brain_cli.main(["needs-you", "--resolve", "Should the shortlist stop at two apartments?", "--because", "evidence resolved it",
                  "--vault", str(config.vault_path)])
    capsys.readouterr()
    assert due(config, capsys)[0]["chunksSinceReview"] == 1
    assert due(config, capsys)[0]["budget"] == 8


def test_owner_record_task_touch_resets_review_count(worker, capsys):
    config, clock = worker
    count(config, capsys, clock, 4)
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at three apartments?")
    count(config, capsys, clock)
    journal.append_entry(config, {"outcome": "progress", "headline": "Discussed the shortlist", "touched": ["agent/Tasks#T-001"]},
                         "cli", clock[0])
    assert due(config, capsys)[0]["chunksSinceReview"] == 0
    assert due(config, capsys)[0]["budget"] == 16
    verdict(config, capsys, "circling", "too many listings", "--ask", "Should the shortlist stop at two apartments?")
    assert task(config).task_state == "in_progress"


@pytest.mark.parametrize("options", [
    ["--review", "T-001"],
    ["--review", "T-001", "--verdict", "met", "--reason", ""],
    ["--review", "T-001", "--verdict", "met", "--reason", "one\ntwo"],
    ["--review", "T-999", "--verdict", "met", "--reason", "done"],
    ["--due", "--verdict", "met"],
    ["--review-context", "T-001", "--instruction", "orphan"],
    ["--review", "T-001", "--verdict", "stuck", "--reason", "offline", "--resume", "tomorrow"],
])
def test_review_invalid_arguments_do_not_record_verdict(worker, capsys, options):
    config, _ = worker
    with pytest.raises(SystemExit) as error:
        cli(config, capsys, *options)
    assert error.value.code == 2 and capsys.readouterr().err
    assert context(config, capsys)["lastReview"] is None


def test_legacy_task_sourced_answer_is_available_without_rewriting_records(worker, capsys):
    config, _ = worker
    folder = config.vault_path / "agent/Answered"
    folder.mkdir()
    path = folder / "2026-11.md"
    text = ("# Answered\n- 2026-11-06 · ask · Should the shortlist stop at three apartments?"
            " · because Yes, just three · [[agent/Tasks#T-001]]\n"
            "- 2026-11-06 · ask · Publish all apartments? · because Yes · [[agent/Tasks#T-002]]\n")
    path.write_text(text)
    assert context(config, capsys)["ownerInstruction"] == "Should the shortlist stop at three apartments? Answer: Yes, just three"
    assert path.read_text() == text
