"""The screen and the study queue: requests carry the whole exchange and who each speaker is, answers are kept once, and
the study reads only what was kept, resuming after an interruption."""
import json
from datetime import date

import pytest

from brain import cli, fm, roles, schedule, screen
from brain.config import Config

TODAY = date(2026, 11, 10)


@pytest.fixture
def org(vault, tmp_path, monkeypatch):
    toml = vault.parent / "brain.toml"
    toml.write_text(toml.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    config = Config.load(vault)
    roles.add(config, "Engineering", "engineering")
    profile = config.vault_path / config.people_dir / "Kim/Profile.md"
    profile.parent.mkdir(parents=True)
    fm.write(profile, {}, "# Kim\n- **Slack:** U01KIM\n- **GitHub:** kim\n")
    (config.vault_path / "agent/Owners.md").write_text(
        "| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n"
        "| api | Kim (slack:U01KIM) | | the API | | |\n")
    reviews = home / "channels/acme-app/reviews"
    reviews.mkdir(parents=True)
    fm.write(reviews / "12.md", {"pr": 12, "title": "Export columns", "author": "ann", "merged": "2026-11-05",
                                 "url": "https://example.invalid/12"},
             "- 2026-11-03 · ann · api/export.ts · Added a helper for the column list\n"
             "- 2026-11-04 · kim · api/export.ts · inline it\n"
             "- 2026-11-04 · ann · api/export.ts · done, inlined\n"
             "- 2026-11-04 · github-actions[bot] ·  · build passed\n"
             "- 2026-11-04 · review-bot[bot] · api/export.ts · this query has no index\n")
    answers.config = config
    return config


def keys(config, number=12):
    role = roles.load(config, "Engineering")
    exchange = next(e for _, e in screen.exchanges(config, role, None) if e.id == f"reviews:acme-app#{number}")
    return [said.key for said in exchange.items]


def ident(config, n, number=12):
    return f"reviews:acme-app#{number}/{keys(config, number)[n]}"


def answers(request, labels, config=None):
    """A classifier's answers: `labels` maps an item's position to (kind, worth, fact)."""
    order = keys(config or answers.config, int(request["exchange"].split("#")[1]))
    out = []
    for n, (kind, worth, fact) in labels.items():
        key = order[n] if n < len(order) else None
        if key in request["items"]:
            out += [{"name": f"kind_{key}", "choice": kind}, {"name": f"worth_{key}", "score": worth},
                    {"name": f"fact_{key}", "choice": "yes" if fact else "no"}]
    return {"exchange": request["exchange"], "classifier": "stub", "answers": out}


ALL = {0: ("local", 0, False), 1: ("correction", 3, False), 2: ("local", 1, False), 3: ("noise", 0, False),
       4: ("correction", 2, False)}


def test_a_request_shows_the_whole_exchange_and_who_each_speaker_is(org):
    [request] = screen.requests(org, "Engineering")
    assert request["items"] == keys(org)
    text = request["input"]
    assert "Export columns (by ann, merged)" in text
    assert "[1] 2026-11-04 kim (owner of api) on api/export.ts:\ninline it" in text
    assert "[0] 2026-11-03 ann (the author of this work)" in text and "[2] 2026-11-04 ann (the author" in text
    assert "github-actions[bot] (a bot)" in text
    names = [q["name"] for q in request["questions"]]
    first = keys(org)[0]
    assert names[:3] == [f"kind_{first}", f"worth_{first}", f"fact_{first}"] and len(names) == 15
    assert request["questions"][0]["instructions"].startswith("Item [0]:")
    kinds = [c["value"] for c in request["questions"][0]["choices"]]
    assert kinds == ["correction", "taste", "decision", "local", "noise"]
    assert [level["label"] for level in request["questions"][1]["levels"]] == ["0", "1", "2", "3"]


def test_answers_are_kept_once_and_a_gap_is_asked_again(org):
    [request] = screen.requests(org, "Engineering")
    partial = {n: value for n, value in ALL.items() if n != 2}
    assert screen.record(org, "Engineering", [answers(request, partial)]) == 4
    assert screen.record(org, "Engineering", [answers(request, partial)]) == 0
    [again] = screen.requests(org, "Engineering")
    assert again["items"] == [keys(org)[2]]
    assert "[1] 2026-11-04 kim" in again["input"]   # still read with the whole exchange
    assert screen.record(org, "Engineering", [answers(again, ALL)]) == 1
    assert screen.requests(org, "Engineering") == []
    label = screen.labels(org, "Engineering")[ident(org, 1)]
    assert (label.kind, label.worth, label.speaker, label.classifier) == ("correction", 3, "owner of api", "stub")


def test_garbled_answers_leave_the_item_pending(org):
    [request] = screen.requests(org, "Engineering")
    bad = answers(request, ALL)
    bad["answers"] = [a | {"choice": "praise"} if a["name"] == f"kind_{keys(org)[1]}" else a for a in bad["answers"]]
    screen.record(org, "Engineering", [bad])
    assert screen.requests(org, "Engineering")[0]["items"] == [keys(org)[1]]
    assert screen.next_digest(org, "Engineering") == ""   # the ruling waits for its whole exchange


def test_the_study_reads_what_was_kept_and_resumes_if_interrupted(org):
    [request] = screen.requests(org, "Engineering")
    screen.record(org, "Engineering", [answers(request, ALL)])
    first = screen.next_digest(org, "Engineering")
    assert "**2026-11-04 kim (owner of api)**" in first
    assert "2026-11-03 ann" in first and "done, inlined" in first   # the exchange around it
    assert "this query has no index" not in first   # a bot's finding nobody answered
    assert screen.next_digest(org, "Engineering") == first   # stopped before done: shown again
    assert screen.done(org, "Engineering") == 1
    assert screen.next_digest(org, "Engineering") == ""
    assert screen.done(org, "Engineering") == 0


def test_a_company_fact_is_kept_whatever_its_kind(org):
    [request] = screen.requests(org, "Engineering")
    screen.record(org, "Engineering", [answers(request, ALL | {2: ("local", 0, True)})])
    assert "**2026-11-04 ann (the author of this work)** [local, 0, fact]" in screen.next_digest(org, "Engineering")


def test_calibration_compares_kind_and_keep_against_a_labelled_sample(org):
    [request] = screen.requests(org, "Engineering")
    screen.record(org, "Engineering", [answers(request, ALL)])
    sample = [{"id": ident(org, 1), "kind": "correction", "kept": True},
              {"id": ident(org, 0), "kind": "taste", "kept": False},
              {"id": "reviews:acme-app#12/9", "kind": "noise", "kept": False}]
    assert screen.calibrate(org, "Engineering", sample) == {"compared": 2, "kind": 0.5, "kept": 1.0}


def test_adding_a_role_schedules_its_study_then_its_screen(org):
    routines = schedule.load(org)
    study, screened = routines["role-engineering-study"], routines["role-engineering-screen"]
    assert study.skills == ["brain-study"] and study.cron
    assert screened.after == "role-engineering-study" and not screened.agent
    assert screened.classify == ["roles", "screen", "Engineering"]
    assert schedule.problems(org) == []


def test_the_runtime_contract_through_the_command_line(org, capsys):
    cli.main(["roles", "screen", "Engineering", "next", "--vault", str(org.vault_path)])
    [request] = json.loads(capsys.readouterr().out)
    import io, sys
    sys.stdin = io.StringIO(json.dumps([answers(request, ALL)]))
    try:
        cli.main(["roles", "screen", "Engineering", "record", "--vault", str(org.vault_path)])
    finally:
        sys.stdin = sys.__stdin__
    assert "5 item(s) labelled" in capsys.readouterr().out
    cli.main(["roles", "study", "Engineering", "next", "--vault", str(org.vault_path)])
    assert "kim (owner of api)" in capsys.readouterr().out
    cli.main(["roles", "study", "Engineering", "done", "--vault", str(org.vault_path)])
    assert "1 item(s) marked studied" in capsys.readouterr().out


def test_a_role_without_a_screen_is_refused_plainly(org):
    role = org.vault_path / "Roles/Engineering/Role.md"
    text = role.read_text()
    role.write_text(text[:text.index("screen:")] + text[text.index("---\n# Engineering"):])
    with pytest.raises(ValueError, match="no screen"):
        screen.requests(org, "Engineering")


def review_file(config):
    return config.vault_path.parent / "home/channels/acme-app/reviews/12.md"


def test_a_comment_added_between_asking_and_recording_changes_no_label(org):
    [request] = screen.requests(org, "Engineering")
    answered = answers(request, ALL)
    path = review_file(org)
    path.write_text(path.read_text().replace("- 2026-11-04 · kim", "- 2026-11-03 · lee · api/export.ts · why a helper?\n- 2026-11-04 · kim", 1))
    assert screen.record(org, "Engineering", [answered]) == 5
    assert screen.labels(org, "Engineering")[ident(org, 2)].text == "inline it"   # kim's ruling, now third
    [again] = screen.requests(org, "Engineering")
    assert again["items"] == [keys(org)[1]]   # only lee's new comment


def test_an_exchange_asked_about_stays_asked_about_past_the_window(org, monkeypatch):
    [request] = screen.requests(org, "Engineering")
    monkeypatch.setenv("BRAIN_TODAY", "2027-02-01")
    later = Config.load(org.vault_path)
    assert [r["exchange"] for r in screen.requests(later, "Engineering")] == [request["exchange"]]


def test_a_bot_finding_counts_once_a_person_speaks_on_its_file(org):
    path = review_file(org)
    path.write_text(path.read_text() + "- 2026-11-05 · ann · api/export.ts · added the index\n")
    [request] = screen.requests(org, "Engineering")
    screen.record(org, "Engineering", [answers(request, ALL | {5: ("local", 0, False)})])
    assert "this query has no index" in screen.next_digest(org, "Engineering")


def test_a_torn_last_line_is_ended_before_the_next_row(org):
    [request] = screen.requests(org, "Engineering")
    folder = screen.folder(org, "Engineering")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "labels.jsonl").write_bytes(b'{"id": "torn\xc3')
    assert screen.record(org, "Engineering", [answers(request, ALL)]) == 5
    assert len(screen.labels(org, "Engineering")) == 5


def test_a_classify_routine_must_name_a_group_and_run_no_model(org):
    bad = {"routines": {"a": {"cron": "0 1 * * *", "agent": False, "classify": [""]},
                        "b": {"cron": "0 1 * * *", "classify": ["roles", "screen", "X"]}}}
    problems = schedule._problems(bad)
    assert any("a.classify: names a brain command group" in p for p in problems)
    assert any("b: classify runs no model" in p for p in problems)


def test_two_names_for_one_schedule_are_refused(org):
    roles.add(org, "Data Science", "engineering")
    with pytest.raises(ValueError, match="belongs to another role"):
        roles.add(org, "Data-Science", "engineering")
    assert not (org.vault_path / "Roles/Data-Science").exists()   # refused before writing anything


def test_an_off_scale_score_leaves_the_item_pending(org):
    [request] = screen.requests(org, "Engineering")
    screen.record(org, "Engineering", [answers(request, ALL | {1: ("correction", 999, False)})])
    assert screen.requests(org, "Engineering")[0]["items"] == [keys(org)[1]]


def test_a_newly_merged_pr_with_old_comments_is_screened(org, monkeypatch):
    path = review_file(org)
    path.write_text(path.read_text().replace("- 2026-11-0", "- 2026-08-0"))   # comments months old, merged on 2026-11-05
    assert screen.requests(org, "Engineering")


def test_a_role_whose_study_routine_name_is_taken_is_refused(org):
    from brain.lock import vault_lock
    routines = schedule.load(org)
    routines["role-ops-study"] = schedule.Routine(cron="0 3 * * *", prompt="", script="other.sh", agent=False)
    schedule.save(org, routines)
    with pytest.raises(ValueError, match="belongs to another role"):
        roles.add(org, "Ops", "engineering")


def test_malformed_screen_entries_are_problems_not_crashes():
    text = roles.shipped_text("engineering")
    nulls = text.replace("  - kind: noise\n    covers: status, thanks, commands, CI or deploy chatter, or a bot's boilerplate\n",
                         "  - kind: noise\n    covers:\n")
    role, problems = roles.parse(nulls, "X", roles._known_sources())
    assert role is None and any("'noise' needs covers" in p for p in problems)
    nested = text.replace("    kinds:\n    - correction\n", "    kinds:\n    - {kind: correction}\n")
    role, problems = roles.parse(nested, "X", roles._known_sources())
    assert role is None and any("screen.keep.kinds" in p for p in problems)
