"""Merged PR history routes code questions through area evidence and linked identities."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
import json

import pytest

from brain import cli, consults, fm, owners, routing, seed, session_context
from brain.config import Config
from conftest import snapshot

TODAY = date(2026, 11, 6)
SLACK = "slack:U01KIM"
GITHUB = "github:kim"
AREA = "apps edc-frontend"


@pytest.fixture
def enterprise(vault, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    return Config.load(vault)


def review(folder, number=101, author="kim", merged="2026-11-06", comments=(), **extra):
    data = dict(pr=number, title="Form builder", author=author, merged=merged)
    data.update(extra)
    fm.write(folder / f"{number}.md", data, "".join(f"- {comment}\n" for comment in comments))


@pytest.fixture
def reviews(tmp_path):
    folder = tmp_path / "reviews"
    folder.mkdir()
    review(folder, comments=[
        "2026-10-30 · nathan · apps/edc-frontend/x.ts · Reuse the field.",
        "2026-11-01 · nathan · apps/edc-frontend/y.ts · Same here.",
        "2026-10-31 · roshan ·  · Split the migration.",
        "2026-11-04 · kim · apps/author-only/x.ts · Done.",
        "2026-11-02 · review-bot · apps/edc-frontend/x.ts · Generated review.",
        "2026-11-02 · checks[bot] · apps/edc-frontend/x.ts · Generated review.",
        "2026-11-02 · bot-checks · apps/edc-frontend/x.ts · Generated review.",
        "2026-11-02 · bad/name · apps/edc-frontend/x.ts · Invalid identity.",
    ], title="EDC · frontend\nform builder with reusable fields and validation plus many other changes across apps today",
           url="https://example.com/pull/101", merged_by="roshan",
           approved=["nathan", "kim", "quiet-reviewer", "quiet-reviewer", "review-bot", "bad/name"],
           files=["apps/edc-frontend/x.ts", "apps/edc-frontend/y.ts", "src/Foo_bar!/x.py", "README.md"])
    review(folder, 102, "sam", "2026-11-05", [
        "2026-11-03 · kim · apps/EDC-frontend/form.ts · Reuse the form.",
        "2026-11-04 · nathan ·  · Looks good.",
        "2026-11-04 · sam · apps/author-only/x.ts · Fixed.",
    ])
    review(folder, 103, comments=["2026-11-05 · nathan ·  · No paths to infer."])
    review(folder, 90, merged="2026-08-07", files=["old/path/x.ts"])
    review(folder, 104, merged="2026-11-07", files=["future/path/x.ts"])
    return folder


def test_history_records_changes_approvals_and_latest_reviews_without_writing(reviews):
    before = snapshot(reviews)
    rows, problems = seed.routing_evidence(reviews, TODAY)
    assert problems == [] and snapshot(reviews) == before
    assert len(rows) == 19
    rich = [row for row in rows if row.link]
    assert len(rich) == 16
    assert {row.area for row in rows} == {AREA, "src foo bar", "top level"}
    assert Counter(row.kind for row in rich) == dict(changed=3, approved=9, reviewed=4)
    assert {row.person for row in rich if row.kind == "changed"} == {GITHUB}
    assert {row.person for row in rich if row.kind == "approved"} == {
        "github:nathan", "github:quiet-reviewer", "github:roshan"}
    assert {row.day for row in rich if row.kind != "reviewed"} == {"2026-11-06"}
    assert [(row.area, row.day) for row in rich if row.kind == "reviewed" and row.person == "github:nathan"] == [
        (AREA, "2026-11-01")]
    assert {row.area for row in rich if row.kind == "reviewed" and row.person == "github:roshan"} == {
        AREA, "src foo bar", "top level"}
    assert {row.text for row in rich} == {"#101 EDC - frontend form builder with reusable fields and validation plus many"}
    assert all(row.link == "https://example.com/pull/101" for row in rich)
    legacy = [row for row in rows if not row.link]
    assert {(row.area, row.person, row.kind) for row in legacy} == {
        (AREA, "github:sam", "changed"), (AREA, GITHUB, "reviewed"), (AREA, "github:nathan", "reviewed")}
    assert rows == sorted(rows, key=lambda row: (row.area, row.day, row.person, row.kind, row.text, row.link))


@pytest.mark.parametrize("path,expected", [
    ("apps/edc-frontend/x.ts", AREA), ("README.md", "top level"),
    ("Src/Foo_bar!/x.py", "src foo bar"), (".github/workflows/ci.yml", "github workflows"),
    ("---/x.py", "top level"), ("-app/x.py", "app"),
])
def test_code_areas_are_valid_consult_names(path, expected):
    assert seed.code_area(path) == expected
    assert owners.AREA.fullmatch(expected) and routing.area_name(expected) == expected


@pytest.mark.parametrize("merger,expected", [
    ("kim", {"github:nathan"}), ("nathan", {"github:nathan"}),
    ("roshan", {"github:nathan", "github:roshan"}),
    ("review-bot", {"github:nathan"}), ("bad/name", {"github:nathan"}),
])
def test_mergers_add_only_distinct_non_author_human_approvals(tmp_path, merger, expected):
    review(tmp_path, files=["apps/edc-frontend/x.ts"], approved=["kim", "nathan"], merged_by=merger)
    rows, problems = seed.routing_evidence(tmp_path, TODAY)
    assert problems == []
    assert {row.person for row in rows if row.kind == "approved"} == expected
    assert [row.person for row in rows if row.kind == "changed"] == [GITHUB]


def test_window_uses_merge_date_and_includes_its_boundaries(reviews):
    review(reviews, 89, merged="2026-08-08", files=["boundary/path/x.ts"])
    rows, _ = seed.routing_evidence(reviews, TODAY)
    assert any(row.text.startswith("#89 ") for row in rows)
    assert not any(row.text.startswith(("#90 ", "#104 ")) for row in rows)
    rows, _ = seed.routing_evidence(reviews, TODAY, days=0)
    assert rows and {row.text.split()[0] for row in rows} == {"#101"}
    loaded, problems = seed.load(reviews, TODAY)
    assert len(loaded) == 4 and problems == []
    assert len(next(item for item in loaded if item.number == 101).comments) == 7


def test_explicit_files_control_pr_areas_and_legacy_files_use_comment_paths(tmp_path):
    review(tmp_path, author="review-bot", files=["apps/new/x.ts"], approved=["nathan"], comments=[
        "2026-11-05 · roshan ·  · Review body.",
        "2026-11-04 · kim · apps/old/x.ts · Line comment.",
    ])
    rows, problems = seed.routing_evidence(tmp_path, TODAY)
    assert problems == []
    assert {(row.area, row.person, row.kind) for row in rows} == {
        ("apps new", "github:nathan", "approved"), ("apps new", "github:roshan", "reviewed"),
        ("apps old", GITHUB, "reviewed")}


@pytest.mark.parametrize("extra,key", [
    ({"url": ["bad"]}, "url"), ({"url": "https://bad/\nlink"}, "url"),
    ({"url": "[[source]]"}, "url"), ({"merged_by": ["kim"]}, "merged_by"),
    ({"approved": "kim"}, "approved"), ({"approved": ["nathan", None]}, "approved"),
    ({"files": "apps/api/x.py"}, "files"), ({"files": ["apps/api/x.py", None]}, "files"),
])
def test_bad_new_keys_are_problems_without_breaking_the_taste_reader(tmp_path, extra, key):
    review(tmp_path, **extra)
    assert len(seed.load(tmp_path, TODAY)[0]) == 1
    _, problems = seed.routing_evidence(tmp_path, TODAY)
    assert len(problems) == 1 and problems[0].startswith(f"101.md: {key} must")


def test_reader_keeps_load_problems_and_skips_bad_comment_dates(tmp_path):
    (tmp_path / "bad.md").write_text("---\ntitle: no number\n---\n")
    review(tmp_path, files=["apps/api/x.py"], comments=[
        "2026-99-01 · nathan · apps/api/x.py · Invalid date.", "malformed comment"])
    rows, problems = seed.routing_evidence(tmp_path, TODAY)
    assert [row.kind for row in rows] == ["changed"]
    assert any("needs frontmatter" in problem for problem in problems)
    assert any("malformed comment" in problem for problem in problems)
    assert any("invalid comment date" in problem for problem in problems)


@pytest.mark.parametrize("extra", [{}, {"files": ["apps/api/x.py"]}])
def test_pr_files_allow_blank_lines_and_headings_before_comments(tmp_path, extra):
    review(tmp_path, comments=["2026-11-05 · nathan · apps/api/x.py · Reuse the service."], **extra)
    path = tmp_path / "101.md"
    data, body = fm.read(path)
    fm.write(path, data, "\n# Comments\n\n" + body + "\n")
    loaded, problems = seed.load(tmp_path, TODAY)
    assert len(loaded[0].comments) == 1 and problems == []
    rows, problems = seed.routing_evidence(tmp_path, TODAY)
    assert {row.kind for row in rows} == {"changed", "reviewed"} and problems == []


def test_batch_writes_once_per_area_with_one_lock_and_deduplicates(enterprise, reviews, monkeypatch):
    rows, _ = seed.routing_evidence(reviews, TODAY)
    original_write, original_lock = fm.write, consults.vault_lock
    writes, locks, held = [], [], []

    @contextmanager
    def checked_lock(config):
        locks.append(config)
        with original_lock(config):
            held.append(True)
            yield
            held.pop()

    def write(path, data, body):
        assert held
        writes.append(path)
        original_write(path, data, body)

    monkeypatch.setattr(consults, "vault_lock", checked_lock)
    monkeypatch.setattr(fm, "write", write)
    assert consults.record_evidence(enterprise, rows + rows) == len(rows)
    assert len(locks) == 1 and len(writes) == len(set(writes)) == 3
    before = snapshot(enterprise.vault_path)
    assert consults.record_evidence(enterprise, rows) == 0
    assert not consults.add_evidence(enterprise, **vars(rows[0]))
    assert snapshot(enterprise.vault_path) == before and len(writes) == 3
    new = replace(rows[0], text="Another PR")
    assert consults.add_evidence(enterprise, **vars(new))
    assert len(writes) == 4
    assert consults.record_evidence(enterprise, []) == 0
    for path in set(writes):
        assert "## Evidence" in path.read_text() and "## Decisions" not in path.read_text()


@pytest.mark.parametrize("changes,match", [
    ({"area": "../outside"}, "area must"), ({"day": "2026-99-01"}, "month"),
    ({"person": "Kim"}, "<platform>:<id>"), ({"kind": "guess"}, "evidence kind"),
    ({"text": ""}, "own words"), ({"text": "one\ntwo"}, "single-line"),
    ({"link": "[[link]]"}, "brackets"), ({"link": "one\ntwo"}, "single-line"),
])
def test_batch_validates_every_row_before_writing(enterprise, changes, match):
    row = seed.Row(AREA, str(TODAY), GITHUB, "changed", "#101 Form builder")
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match=match):
        consults.record_evidence(enterprise, [row, replace(row, **changes)])
    assert snapshot(enterprise.vault_path) == before


def test_cli_dry_run_is_read_only_and_reports_counts_and_problems(enterprise, reviews, capsys):
    (reviews / "bad.md").write_text("---\ntitle: no number\n---\n")
    args = ["consult", "seed", "--vault", str(enterprise.vault_path), "--reviews", str(reviews), "--dry-run"]
    before = snapshot(enterprise.repo)
    cli.main(args + ["--json"])
    data = json.loads(capsys.readouterr().out)
    assert (data["prs"], data["found"], data["added"]) == (3, 19, 0)
    assert data["areas"][AREA] == dict(changed=2, reviewed=4, approved=3)
    assert data["problems"] == ["bad.md: needs frontmatter pr, author and merged (YYYY-MM-DD)"]
    cli.main(args)
    out = capsys.readouterr().out
    assert "3 PRs read, 19 lines found, 0 lines added" in out
    assert f"{AREA} · changed 2 · reviewed 4 · approved 3" in out
    assert "problem: bad.md:" in out
    cli.main(args + ["--days", "0", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert (data["prs"], data["found"]) == (2, 16)
    assert snapshot(enterprise.repo) == before


def test_cli_records_json_and_reruns_idempotently(enterprise, reviews, capsys):
    args = ["consult", "seed", "--vault", str(enterprise.vault_path), "--reviews", str(reviews), "--json"]
    cli.main(args)
    first = json.loads(capsys.readouterr().out)
    assert (first["prs"], first["found"], first["added"]) == (3, 19, 19)
    before = snapshot(enterprise.repo)
    cli.main(args)
    assert json.loads(capsys.readouterr().out) == {**first, "added": 0}
    assert snapshot(enterprise.repo) == before


def test_cli_refuses_missing_folder_and_negative_window(enterprise, tmp_path):
    args = ["consult", "seed", "--vault", str(enterprise.vault_path), "--reviews"]
    with pytest.raises(SystemExit, match="no folder"):
        cli.main(args + [str(tmp_path / "absent")])
    with pytest.raises(SystemExit, match="non-negative"):
        cli.main(args + [str(tmp_path), "--days", "-1"])


def test_personal_seed_and_batch_refuse_without_writing(vault, reviews):
    before = snapshot(vault)
    with pytest.raises(SystemExit, match="enterprise edition"):
        cli.main(["consult", "seed", "--vault", str(vault), "--reviews", str(reviews), "--dry-run"])
    with pytest.raises(ValueError, match="enterprise edition"):
        consults.record_evidence(Config.load(vault), [])
    assert snapshot(vault) == before


def profile(config, folder="Kim", body="- **GitHub:** kim\n- **Demiurge:** kim-id\n- **Slack:** U01KIM\n", **data):
    path = config.vault_path / config.people_dir / folder / "Profile.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, "# " + folder + "\n" + body)


def test_identities_read_only_explicit_person_ids_with_canonical_priority(enterprise):
    profile(enterprise, body="- **Demiurge:** kim-id\n- **GitHub:** `kim`\n- **Slack:** U01KIM\n"
            "Kim mentions GitHub: stranger and slack:U02STRANGER.\nGitHub: bad_login\nGitHub: bad.name\n")
    profile(enterprise, "Sam", "- Telegram: sam-telegram\n- Demiurge: sam-id\n- GitHub: sam\n")
    profile(enterprise, "Lee", "- Discord: lee-discord\n- GitHub: lee\n")
    profile(enterprise, "Only", "- GitHub: only\n")
    profile(enterprise, "Retired", "- GitHub: retired\n", kind="log")
    path = enterprise.vault_path / enterprise.agents_dir / "Bot" / "Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {}, "- GitHub: intellect\n")
    before = snapshot(enterprise.repo)
    assert routing.identities(enterprise) == {
        GITHUB: SLACK, SLACK: SLACK, "demiurge:kim-id": SLACK,
        "telegram:sam-telegram": "demiurge:sam-id", "demiurge:sam-id": "demiurge:sam-id",
        "github:sam": "demiurge:sam-id", "discord:lee-discord": "discord:lee-discord",
        "github:lee": "discord:lee-discord", "github:only": "github:only"}
    assert snapshot(enterprise.repo) == before
    assert session_context.profile_ids("- **GitHub:** kim\n") == []
    assert "github" not in session_context.PLATFORMS


def test_github_history_and_slack_consults_merge_with_via_and_folder_word_matching(enterprise):
    profile(enterprise)
    path = routing.area_path(enterprise, AREA)
    path.parent.mkdir(parents=True)
    fm.write(path, {}, f"## Evidence\n- {TODAY} · {GITHUB} · approved · #101 Form builder · [[https://example.com/pr/101]]\n"
             f"- {TODAY} · github:unlinked · reviewed · #101 Form builder\n"
             f"## Decisions\n- {TODAY} · Kim · Reuse the form. · id:012345abcdef\n  - asked: Reuse this form?\n")
    logs = enterprise.vault_path / enterprise.agent("Answered/2026-11.md")
    logs.parent.mkdir(parents=True)
    logs.write_text(f"- {TODAY} · consult confirmed · Reuse this form? · by {SLACK} · {AREA} · 2.0h · id:012345abcdef\n")
    before = snapshot(enterprise.repo)
    ranked = consults.candidates(enterprise, "EDC frontend form builder")
    assert routing.relevant(enterprise, "EDC frontend form builder") == [AREA]
    assert [entry.person for entry in ranked] == [SLACK, "github:unlinked"]
    assert ranked[0].score == routing.WEIGHTS["approved"] + routing.WEIGHTS["answered"]
    assert ranked[0].median_hours == 2
    assert next(item for item in ranked[0].evidence if item["kind"] == "approved")["via"] == GITHUB
    assert all("via" not in item for item in ranked[0].evidence if item["kind"] == "answered")
    assert "via" not in ranked[1].evidence[0]
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("owner,muted", [(GITHUB, SLACK), (SLACK, GITHUB), ("demiurge:kim-id", GITHUB)])
def test_restricted_gate_and_mutes_cover_canonical_and_alias_ids(enterprise, owner, muted):
    profile(enterprise)
    (enterprise.vault_path / "agent/Owners.md").write_text(
        "| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n"
        f"| {AREA} | Kim ({owner}) | | forms | | |\n")
    assert routing.allowed(enterprise, AREA) == {GITHUB, SLACK, "demiurge:kim-id"}
    for person in (GITHUB, SLACK, "demiurge:kim-id"):
        consults._gate(enterprise, AREA, person)
    with pytest.raises(ValueError, match="restricted consult"):
        consults._gate(enterprise, AREA, "github:stranger")
    path = routing.area_path(enterprise, AREA)
    path.parent.mkdir(parents=True)
    fm.write(path, {}, f"## Not asked\n- {TODAY} · {muted} · stop-asking\n")
    for person in (GITHUB, SLACK, "demiurge:kim-id"):
        assert routing.stopped(enterprise, AREA, person)
    assert not routing.stopped(enterprise, "other area", SLACK)
    candidate, = consults.candidates(enterprise, "forms", AREA)
    assert candidate.person == SLACK and candidate.muted


def test_agent_posted_comments_are_not_a_persons_review(enterprise, tmp_path):
    folder = tmp_path / "agent-reviews"
    folder.mkdir()
    review(folder, author="kim", files=["apps/edc-frontend/x.ts"], comments=[
        "2026-11-02 · roshan · apps/edc-frontend/x.ts · Posted by Codex: move this into the service.",
        "2026-11-03 · nathan · apps/edc-frontend/x.ts · Move this into the service.",
    ])
    rows, _ = seed.routing_evidence(folder, TODAY)
    reviewed = {row.person for row in rows if row.kind == "reviewed"}
    assert reviewed == {"github:nathan"}
