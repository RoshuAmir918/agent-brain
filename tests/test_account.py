"""The role's record: corrections counted once per lesson per piece of work, repeats of what it had learned, misses by
kind, predictions and asks, and the weekly account."""
import pytest

from brain import account, cli, corrections, fm, roles
from brain.config import Config
from conftest import git


@pytest.fixture
def org(repo, tmp_path, monkeypatch):
    root, _ = repo
    toml = root.parent / "brain.toml"
    toml.write_text(toml.read_text() + 'edition = "enterprise"\nagent_email = "agent@example.invalid"\n')
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-10")
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    config = Config.load(root)
    roles.add(config, "Engineering", "engineering")
    profile = config.vault_path / config.people_dir / "Kim/Profile.md"
    profile.parent.mkdir(parents=True)
    fm.write(profile, {}, "# Kim\n- **Slack:** U01KIM\n- **GitHub:** kim\n")
    (config.vault_path / "agent/Owners.md").write_text(
        "| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n| api | Kim (slack:U01KIM) | | | | |\n")
    commit(config, "2026-11-01", "role added")
    corrections.add_work(config, "Engineering", "reviews:acme-app#40", "Notes column", opened="2026-11-03")
    corrections.add_work(config, "Engineering", "reviews:acme-app#41", "Export fix", opened="2026-11-07")
    return config


def commit(config, day, message):
    git(config.repo, "add", "-A")
    git(config.repo, "commit", "-q", "--allow-empty", "-m", message, f"--date={day}T12:00:00")


def comment(config, number, text, at, event="review.comment", source=""):
    return corrections.add_event(config, "Engineering", f"reviews:acme-app#{number}", event, "kim", at, text,
                                 source_id=source or text, where="api/export.ts")


def item(config, number):
    return next(i for i in account.items(config, "Engineering") if i.work == f"reviews:acme-app#{number}")


def test_the_same_lesson_said_three_times_on_one_piece_of_work_counts_once(org):
    first = comment(org, 40, "inline this helper", "2026-11-04", source="a")
    said = corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction", claim="Inline a helper with one caller")
    ident = said.split("^")[1].split()[0]
    for n, text in enumerate(("and this one", "same again"), 1):
        corrections.file(org, "Engineering", comment(org, 40, text, "2026-11-04", source=f"b{n}").id, to=ident)
    counted = item(org, 40)
    assert (counted.corrections, counted.weighted, counted.burden, counted.misses) == (1, 2.0, 3, {"knowledge": 1})   # three comments, one lesson


def test_a_correction_on_a_lesson_already_learned_is_a_repeat_and_an_execution_miss(org):
    first = comment(org, 40, "inline this helper", "2026-11-04")
    ident = corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction",
                             claim="Inline a helper with one caller").split("^")[1].split()[0]
    commit(org, "2026-11-05", "study: learned")
    again = comment(org, 41, "you did it again: inline it", "2026-11-08")
    corrections.file(org, "Engineering", again.id, to=ident)
    assert (item(org, 40).repeated, item(org, 40).misses) == (0, {"knowledge": 1})
    assert (item(org, 41).repeated, item(org, 41).misses) == (1, {"execution": 1})
    other = comment(org, 41, "you missed the note on retries", "2026-11-08", source="r")
    corrections.file(org, "Engineering", other.id, to=ident, miss="retrieval")
    assert item(org, 41).misses == {"retrieval": 1}   # the same lesson on the same work: one, of the kind the role named


def test_decisions_dismissals_and_unfiled_corrections(org):
    from brain import owners
    ruled = comment(org, 40, "always check schema changes with Kim", "2026-11-04", source="d")
    corrections.file(org, "Engineering", ruled.id, decides="schema changes", person="slack:U01KIM", always=True)
    typo = comment(org, 40, "typo", "2026-11-04", source="t")
    corrections.dismiss(org, "Engineering", typo.id, "a typo")
    comment(org, 40, "revert this, it broke exports", "2026-11-05", event="revert", source="v")
    counted = item(org, 40)
    assert counted.misses == {"decision": 1, "unfiled": 1} and counted.corrections == 2 and counted.weighted == 6.0


def test_the_week_in_one_account(org, capsys):
    first = comment(org, 40, "inline this helper", "2026-11-04")
    corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction", claim="Inline a helper with one caller")
    account.predict(org, "Engineering", "reviews:acme-app#40", ["the helper may be flagged", "no test for empty notes"])
    assert account.predict(org, "Engineering", "reviews:acme-app#40", ["again"]) == 0   # kept once
    account.asked(org, "Engineering", "reviews:acme-app#40", "findable", "which column name")
    account.asked(org, "Engineering", "reviews:acme-app#41", "needed", "export contract")
    from datetime import date
    report = account.account(org, "Engineering", days=10, today=date(2026, 11, 11))
    t = report["totals"]
    assert (t["work"], t["corrections"], t["predicted"], t["learned"]) == (2, 1, 2, 1)
    assert t["asks"] == {"needed": 1, "findable": 1, "unneeded": 0}
    text = account.render(report)
    assert "2 piece(s) of work, 1 correction(s)" in text and "1 needed, 1 it could have answered itself" in text
    assert "| Notes column | 1 | 0 | 2 | 1 knowledge |" in account.render(report)


def test_switches_are_kept_with_each_work_so_the_account_can_compare(org):
    role = org.vault_path / "Roles/Engineering/Role.md"
    role.write_text(role.read_text().replace("  review: true", "  review: false"))
    corrections.add_work(org, "Engineering", "reviews:acme-app#42", "No review", opened="2026-11-08")
    comment(org, 42, "this needed a review", "2026-11-09", source="x")
    from datetime import date
    compare = account.account(org, "Engineering", days=10, today=date(2026, 11, 11))["compare"]
    assert compare == {"gate on, review on": {"work": 2, "corrections_per_work": 0.0},
                       "gate on, review off": {"work": 1, "corrections_per_work": 1.0}}
    role.write_text(role.read_text().replace("  review: false", "  review: maybe"))
    assert any("process maps gate and review" in p for p in roles.problems(org))


def test_the_weekly_account_is_scheduled(org):
    from brain import schedule
    routine = schedule.load(org)["role-engineering-account"]
    assert routine.cron == "0 8 * * 1" and routine.deliver == "demiurge" and "brain roles account" in routine.prompt


def test_a_lesson_learned_early_the_same_day_is_known_to_work_opened_later(org):
    first = comment(org, 40, "inline this helper", "2026-11-04")
    ident = corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction",
                             claim="Inline a helper with one caller").split("^")[1].split()[0]
    git(org.repo, "add", "-A"); git(org.repo, "commit", "-q", "--allow-empty", "-m", "study at night", "--date=2026-11-08T01:30:00")
    corrections.add_work(org, "Engineering", "reviews:acme-app#43", "Morning work", opened="2026-11-08T09:00:00")
    corrections.file(org, "Engineering", comment(org, 43, "inline it again", "2026-11-08", source="m").id, to=ident)
    assert item(org, 43).repeated == 1


def test_work_recorded_before_switches_is_not_given_todays(org):
    from brain import screen
    path = screen.folder(org, "Engineering") / "work.jsonl"
    path.write_text(path.read_text() + '{"id": "reviews:acme-app#9", "title": "Old", "link": "", "opened": "2026-11-05"}\n')
    from datetime import date
    assert "process unknown" in account.account(org, "Engineering", days=10, today=date(2026, 11, 11))["compare"]


def test_upgrading_gives_existing_roles_their_new_routines(org):
    from brain import schedule
    routines = schedule.load(org)
    del routines["role-engineering-account"]
    schedule.save(org, routines)
    assert roles.ensure_routines(org) == ["role-engineering-account"]
    assert roles.ensure_routines(org) == []
