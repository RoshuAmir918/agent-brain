"""Corrections on a role's own work: recorded once with their source, filed as sourced candidates, promoted when a second
piece of work backs them or someone with authority states them as a rule."""
from datetime import date

import pytest

from brain import cli, corrections, fm, roles
from brain.config import Config

TODAY = date(2026, 11, 10)


@pytest.fixture
def org(vault, tmp_path, monkeypatch):
    toml = vault.parent / "brain.toml"
    toml.write_text(toml.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    config = Config.load(vault)
    roles.add(config, "Engineering", "engineering")
    profile = config.vault_path / config.people_dir / "Kim/Profile.md"
    profile.parent.mkdir(parents=True)
    fm.write(profile, {}, "# Kim\n- **Slack:** U01KIM\n- **GitHub:** kim\n")
    (config.vault_path / "agent/Owners.md").write_text(
        "| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n"
        "| api | Kim (slack:U01KIM) | | the API | | |\n")
    for number in (40, 41):
        corrections.add_work(config, "Engineering", f"reviews:acme-app#{number}", f"PR {number}")
    return config


def comment(config, number, by="kim", text="inline this helper, it has one caller", at="2026-11-04", event="review.comment"):
    return corrections.add_event(config, "Engineering", f"reviews:acme-app#{number}", event, by, at, text,
                                 where="api/export.ts")


def taste(config):
    return (config.vault_path / "Roles/Engineering/Taste.md").read_text()


def line_id(config, prefix):
    roles.assign_ids(config, "Engineering")
    return next(lesson.id for lesson in roles.rebuild(config, "Engineering").lessons if lesson.text.startswith(prefix))


def test_a_review_comment_becomes_a_sourced_candidate_and_a_second_instance_promotes_it(org):
    first = comment(org, 40)
    assert (first.speaker, first.weight) == ("owner of api", 2.0)
    said = corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction",
                            claim="Inline a helper that has one caller")
    ident = said.split("^")[1].split()[0]
    assert said == f"new candidate ^{ident} in Taste · Abstraction"
    assert f"- Inline a helper that has one caller (review on acme-app#40, kim, 2026-11-04) *(candidate)* ^{ident}" in taste(org)
    second = comment(org, 41, at="2026-11-06", text="same here: inline it")   # the same night, before any commit
    assert corrections.file(org, "Engineering", second.id, to=ident) == f"added to ^{ident}, now active"
    line = next(l for l in taste(org).splitlines() if l.startswith("- Inline a helper"))
    assert line == (f"- Inline a helper that has one caller (review on acme-app#40, kim, 2026-11-04; review on "
                    f"acme-app#41, kim, 2026-11-06) ^{ident}")
    lesson = next(l for l in roles.rebuild(org, "Engineering").lessons if l.id == ident)
    assert lesson.status == "active"


def test_a_second_comment_on_the_same_work_does_not_promote(org):
    first = comment(org, 40)
    corrections.file(org, "Engineering", first.id, note="Taste", section="Abstraction", claim="Inline a helper with one caller")
    ident = line_id(org, "Inline a helper")
    again = comment(org, 40, at="2026-11-05", text="and this one too")
    assert corrections.file(org, "Engineering", again.id, to=ident) == f"added to ^{ident}"
    assert "*(candidate)*" in next(l for l in taste(org).splitlines() if l.startswith("- Inline a helper"))


def test_an_owner_may_state_a_rule_and_a_teammate_may_not(org):
    ruled = comment(org, 40, text="we never put SQL in migrations", event="review.changes_requested")
    assert ruled.weight == 3.0
    assert corrections.file(org, "Engineering", ruled.id, note="Decisions", section="Data",
                            claim="Data changes go through audited backfills, never SQL in a migration", rule=True) \
        .startswith("new rule ^")
    decisions = (org.vault_path / "Roles/Engineering/Decisions.md").read_text()
    assert "(changes requested on acme-app#40, kim, 2026-11-04, stated as a rule) ^" in decisions
    other = comment(org, 41, by="lee", text="always use tabs")
    assert other.speaker == "a teammate"
    with pytest.raises(ValueError, match="can't make it a rule"):
        corrections.file(org, "Engineering", other.id, note="Taste", section="Style", claim="Use tabs", rule=True)


def test_a_struck_claim_is_refused(org, monkeypatch):
    event = comment(org, 40)
    index = roles.Index("Engineering", "", struck=[roles.Struck("abcd", "Inline a helper that has one caller (x)",
                                                                "Jo", "2026-11-08", "deleted")])
    monkeypatch.setattr(roles, "rebuild", lambda config, name: index)
    with pytest.raises(ValueError, match="Jo struck this"):
        corrections.file(org, "Engineering", event.id, note="Taste", section="Abstraction",
                         claim="Inline a helper that has one caller")
    assert corrections.pending(org, "Engineering") == [event]


def test_a_persons_line_is_confirmed_never_edited(org, monkeypatch):
    path = org.vault_path / "Roles/Engineering/Taste.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n## Abstraction\n\n- Inline small helpers (Jo, 2026-10-01) ^beef\n")
    index = roles.Index("Engineering", "", lessons=[roles.Lesson("beef", "Roles/Engineering/Taste.md", "Abstraction",
                                                                 "Inline small helpers (Jo, 2026-10-01)", "Jo, 2026-10-01",
                                                                 "active", human=True)])
    monkeypatch.setattr(roles, "rebuild", lambda config, name: index)
    before = path.read_text()
    event = comment(org, 40)
    assert corrections.file(org, "Engineering", event.id, to="beef").startswith("confirms ^beef")
    assert path.read_text() == before
    assert corrections.filed(org, "Engineering")[event.id]["how"] == "confirms ^beef"


def test_events_are_checked_and_kept_once(org):
    with pytest.raises(ValueError, match="not a correction this role's template counts"):
        comment(org, 40, event="review.praise")
    with pytest.raises(ValueError, match="not the role's own work"):
        corrections.add_event(org, "Engineering", "reviews:acme-app#99", "review.comment", "kim", "2026-11-04", "x")
    first = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "kim", "2026-11-04",
                                  "inline it", source_id="gh-123")
    assert first is not None
    assert corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "kim", "2026-11-04",
                                 "inline it (edited)", source_id="gh-123") is None
    assert len(corrections.events(org, "Engineering")) == 1


def test_waiting_corrections_come_heaviest_first_and_a_dismissed_one_leaves(org):
    light = comment(org, 40, text="typo in the comment")
    heavy = corrections.add_event(org, "Engineering", "reviews:acme-app#41", "revert", "kim", "2026-11-07",
                                  "reverted: broke the export")
    assert [event.id for event in corrections.pending(org, "Engineering")] == [heavy.id, light.id]
    corrections.dismiss(org, "Engineering", light.id, "a typo, nothing beyond this PR")
    assert [event.id for event in corrections.pending(org, "Engineering")] == [heavy.id]
    assert corrections.file(org, "Engineering", light.id, note="Taste", section="X", claim="Y") == "already filed"


def test_the_command_line(org, capsys):
    run = lambda *args: cli.main(["roles", *args, "--vault", str(org.vault_path)])
    run("work", "Engineering", "add", "reviews:acme-app#42", "--title", "Export fix")
    run("correction", "Engineering", "add", "--work", "reviews:acme-app#42", "--event", "review.comment", "--by", "kim",
        "--at", "2026-11-09", "--text", "split the migration from the code", "--source-id", "gh-9")
    out = capsys.readouterr().out
    assert "(owner of api, weight 2)" in out
    ident = out.split("recorded ")[1].split()[0]
    run("correction", "Engineering", "next")
    assert f"{ident} · review.comment (2) · reviews:acme-app#42" in capsys.readouterr().out
    run("correction", "Engineering", "file", ident, "--note", "Kinds", "--section", "Schema changes",
        "--claim", "Ship a migration in its own PR, before the code that uses it")
    assert "in Kinds · Schema changes" in capsys.readouterr().out
    run("correction", "Engineering", "next")
    assert "no corrections waiting" in capsys.readouterr().out


def new_line(config, number=40, claim="Inline a helper that has one caller"):
    event = comment(config, number)
    return corrections.file(config, "Engineering", event.id, note="Taste", section="Abstraction", claim=claim).split("^")[1].split()[0]


def test_a_struck_line_takes_no_evidence(org):
    ident = new_line(org)
    path = org.vault_path / "Roles/Engineering/Taste.md"
    path.write_text(path.read_text().replace("## Abstraction", "## Struck"))
    second = comment(org, 41, at="2026-11-06", text="inline it")
    with pytest.raises(ValueError, match="no live line"):
        corrections.file(org, "Engineering", second.id, to=ident)
    assert second.id not in corrections.filed(org, "Engineering")


def test_a_claim_cannot_carry_its_own_status(org):
    event = comment(org, 41, by="lee", text="always use tabs")
    with pytest.raises(ValueError, match="carries no status"):
        corrections.file(org, "Engineering", event.id, note="Taste", section="Style", claim="Use tabs *(rule: everyone)*")


def test_an_agent_posting_under_the_owners_account_has_no_authority(org):
    event = comment(org, 40, text="Use the repository helper. Posted by codex")
    assert event.speaker == "an agent, posting under a person's account"
    with pytest.raises(ValueError, match="can't make it a rule"):
        corrections.file(org, "Engineering", event.id, note="Taste", section="X", claim="Use the repository helper", rule=True)


def test_a_rule_keeps_its_marker_when_evidence_is_added(org):
    path = org.vault_path / "Roles/Engineering/Decisions.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n## Data\n\n- No SQL in migrations (kim, 2026-10-01) *(rule: API only)* ^beef\n")
    event = comment(org, 40, text="no SQL in this migration please")
    corrections.file(org, "Engineering", event.id, to="beef")
    assert "- No SQL in migrations (kim, 2026-10-01; review on acme-app#40, kim, 2026-11-04) *(rule: API only)* ^beef" \
        in path.read_text()


def test_a_run_that_stopped_before_recording_does_not_write_twice(org, monkeypatch):
    event = comment(org, 40)
    real = corrections._record
    monkeypatch.setattr(corrections, "_record", lambda *args: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        corrections.file(org, "Engineering", event.id, note="Taste", section="Abstraction", claim="Inline a helper with one caller")
    monkeypatch.setattr(corrections, "_record", real)
    assert corrections.file(org, "Engineering", event.id, note="Taste", section="Abstraction",
                            claim="Inline a helper with one caller") == "already in the notes; recorded"
    assert taste(org).count("Inline a helper with one caller") == 1
    assert corrections.pending(org, "Engineering") == []


def test_two_repositories_numbered_alike_are_two_pieces_of_work(org):
    corrections.add_work(org, "Engineering", "reviews:acme-api#40", "PR 40 in the API")
    ident = new_line(org)
    other = corrections.add_event(org, "Engineering", "reviews:acme-api#40", "review.comment", "kim", "2026-11-06", "inline it")
    assert corrections.file(org, "Engineering", other.id, to=ident).endswith("now active")
    assert corrections.works_in("review on #40, kim; Jo, 2026-11-01") == {"#40"}


def test_the_same_comment_id_on_two_works_is_two_events(org):
    a = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "kim", "2026-11-04", "x", source_id="7")
    b = corrections.add_event(org, "Engineering", "reviews:acme-app#41", "review.comment", "kim", "2026-11-04", "x", source_id="7")
    assert a and b and a.id != b.id
