"""Roles: the template a person can edit, notes as the source of truth, and a person's edit outranking a learned line."""
import json

import pytest

from brain import cli, fm, roles, validate
from brain.config import Config
from brain.vault import Vault
from conftest import git

AGENT = "agent@example.invalid"


@pytest.fixture
def org(repo, tmp_path, monkeypatch):
    """The agent's clone with a role, and a person's clone of the same remote."""
    root, remote = repo
    toml = root.parent / "brain.toml"
    toml.write_text(toml.read_text() + f'edition = "enterprise"\nagent_email = "{AGENT}"\n')
    git(root.parent, "config", "user.email", AGENT)
    run(root, "roles", "add", "Engineering", "--template", "engineering")
    taste = root / "Roles/Engineering/Taste.md"
    data, body = fm.read(taste)
    fm.write(taste, data, body + "\n## Naming\n\n- Name things after the domain object (review on #12, jo, 2026-10-01)\n"
             "- Prefer a parameter over a sibling method (review on #15, jo, 2026-10-02) *(candidate)*\n")
    git(root.parent, "add", "-A"); git(root.parent, "commit", "-qm", "learn"); git(root.parent, "push", "-q", "origin", "main")
    run(root, "roles", "rebuild")
    git(root.parent, "push", "-q", "origin", "main")
    person = tmp_path / "person"
    git(tmp_path, "clone", "-q", str(remote), str(person))
    git(person, "config", "user.email", "jo@example.invalid")
    git(person, "config", "user.name", "Jo")
    return root, person


def run(root, *args):
    cli.main([*args, "--vault", str(root)])


def person_edits(person, change, message="edit"):
    path = person / "memory/Roles/Engineering/Taste.md"
    path.write_text(change(path.read_text()))
    git(person, "commit", "-qam", message)
    git(person, "push", "-q", "origin", "main")


def index(root):
    return roles.read_index(Config.load(root), "Engineering")


def test_add_copies_the_template_and_never_overwrites(org):
    root, _ = org
    config = Config.load(root)
    assert (root / "Roles/Engineering/Role.md").read_text() == roles.shipped_text("engineering")
    assert roles.base_path(config, "Engineering").read_text() == roles.shipped_text("engineering")
    assert {p.name for p in (root / "Roles/Engineering").glob("*.md")} == {
        "Role.md", "Architecture.md", "Taste.md", "Decisions.md", "Kinds.md"}
    with pytest.raises(ValueError, match="already exists"):
        roles.add(config, "Engineering", "engineering")
    assert roles.problems(config) == []


def test_template_problems_are_said_not_thrown():
    text = roles.shipped_text("engineering").replace("  - chat\n", "  - nowhere\n").replace(
        "weight: 4", "weight: -1").replace("- lens: taste", "- lens: solves_the_ask")
    role, problems = roles.parse(text, "X", roles._known_sources())
    assert role is None
    assert any("no source 'nowhere'" in p for p in problems)
    assert any("weight must be a positive number" in p for p in problems)
    assert any("solves_the_ask is every role's own" in p for p in problems)


def test_every_role_reviews_whether_the_ask_is_solved(org):
    root, _ = org
    lenses = [entry["lens"] for entry in roles.load(Config.load(root), "Engineering").lenses]
    assert lenses[0] == "solves_the_ask" and "rollout" in lenses


def test_rebuild_gives_ids_without_taking_the_lines_from_their_author(org):
    root, person = org
    lines = {lesson.text.split(" (")[0]: lesson for lesson in index(root).lessons}
    assert all(lesson.id for lesson in lines.values())
    assert lines["Prefer a parameter over a sibling method"].status == "candidate"
    assert not any(lesson.human for lesson in lines.values())   # the agent wrote them
    person_edits(person, lambda text: text.replace("## Naming\n", "## Naming\n\n- Keep handlers thin (Jo, 2026-10-05)\n"))
    run(root, "pull"); run(root, "roles", "rebuild")
    added = next(lesson for lesson in index(root).lessons if lesson.text.startswith("Keep handlers thin"))
    assert added.human and added.by == "Jo" and added.id


def test_a_pushed_hand_edit_is_honoured_by_the_next_pass(org):
    root, person = org
    person_edits(person, lambda text: text.replace("after the domain object", "after the domain object, never the table"))
    run(root, "pull"); run(root, "roles", "rebuild")
    edited = next(lesson for lesson in index(root).lessons if "never the table" in lesson.text)
    assert edited.human and edited.by == "Jo"


def test_a_struck_line_stays_struck(org):
    root, person = org
    ident = next(lesson.id for lesson in index(root).lessons if lesson.text.startswith("Prefer a parameter"))
    person_edits(person, lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("- Prefer")) + "\n")
    run(root, "pull"); run(root, "roles", "rebuild")
    struck = {entry.id: entry for entry in index(root).struck}
    assert struck[ident].how == "deleted" and struck[ident].by == "Jo"
    claim = "Prefer a parameter over a sibling method (review on #40, ann)"
    assert "Jo struck this" in roles.refusal(index(root), claim, struck[ident].at)
    assert roles.refusal(index(root), claim, "2999-01-01") is None   # new evidence may bring it back
    with pytest.raises(SystemExit, match="refused"):
        run(root, "roles", "may-learn", "Engineering", "--claim", claim, "--evidence", struck[ident].at)


def test_moving_a_line_under_struck_strikes_it_and_the_agent_retiring_one_does_not(org):
    root, person = org
    taste = person / "memory/Roles/Engineering/Taste.md"
    lines = taste.read_text().splitlines()
    name = next(line for line in lines if line.startswith("- Name things"))
    taste.write_text("\n".join(line for line in lines if line != name) + f"\n\n## Struck\n\n{name}\n")
    git(person, "commit", "-qam", "strike"); git(person, "push", "-q", "origin", "main")
    run(root, "pull")
    agent_taste = root / "Roles/Engineering/Taste.md"
    agent_taste.write_text("\n".join(line for line in agent_taste.read_text().splitlines() if not line.startswith("- Prefer")) + "\n")
    git(root.parent, "commit", "-qam", "consolidate: retire")
    run(root, "roles", "rebuild")
    struck = index(root).struck
    assert [(entry.how, entry.by) for entry in struck] == [("moved", "Jo")]


def test_deleting_the_index_changes_nothing_after_a_rebuild(org):
    root, person = org
    person_edits(person, lambda text: text.replace("after the domain object", "after the domain object, never the table"))
    run(root, "pull"); run(root, "roles", "rebuild")
    path = roles.index_path(Config.load(root), "Engineering")
    before = path.read_text()
    path.unlink()
    run(root, "roles", "rebuild")
    assert path.read_text() == before
    assert git(root.parent, "status", "--porcelain") == ""   # the index is never committed


def test_a_pull_keeps_the_persons_side_of_a_clash(org):
    root, person = org
    person_edits(person, lambda text: text.replace("after the domain object", "after what the user calls it"))
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text().replace("after the domain object", "after the table"))
    git(root.parent, "commit", "-qam", "agent rewords")
    mine = git(root.parent, "rev-parse", "--short=12", "HEAD")
    run(root, "pull")
    assert "after what the user calls it" in taste.read_text() and "after the table" not in taste.read_text()
    kept = validate.kept_edits(Vault(root))
    assert len(kept) == 1 and "Roles/Engineering/Taste.md" in kept[0] and mine in kept[0]


def test_the_agents_commits_carry_its_own_author(org):
    root, _ = org
    (root / "Roles/Engineering/Kinds.md").write_text("---\ntier: warm\n---\n# Kinds\n\nchanged\n")
    git(root.parent, "config", "user.email", "someone-else@example.invalid")
    cli.commit(root, "agent work")
    assert git(root.parent, "log", "-1", "--format=%ae") == AGENT


def test_an_upgraded_template_is_proposed_and_merged_around_edits(org, monkeypatch):
    root, _ = org
    config = Config.load(root)
    role = root / "Roles/Engineering/Role.md"
    role.write_text(role.read_text().replace("burden: review comments per PR", "burden: comments per merged PR"))
    shipped = roles.shipped_text("engineering")
    newer = shipped.replace("- ci.run\n", "- ci.run\n- ci.rerun_failed\n")
    monkeypatch.setattr(roles, "shipped_text", lambda template: newer)
    run(root, "roles", "template", "check")
    assert "ci.rerun_failed" in roles.proposal_path(config, "Engineering").read_text()
    assert "ci.rerun_failed" not in role.read_text()   # never overwritten
    assert any("template changed" in problem for problem in roles.problems(config))
    run(root, "roles", "template", "accept", "Engineering")
    text = role.read_text()
    assert "ci.rerun_failed" in text and "comments per merged PR" in text
    assert not roles.proposal_path(config, "Engineering").exists()
    run(root, "roles", "template", "check")
    assert not roles.proposal_path(config, "Engineering").exists()


def test_a_declined_template_change_is_not_proposed_again(org, monkeypatch):
    root, _ = org
    config = Config.load(root)
    newer = roles.shipped_text("engineering").replace("- ci.run\n", "- ci.run\n- ci.rerun_failed\n")
    monkeypatch.setattr(roles, "shipped_text", lambda template: newer)
    run(root, "roles", "template", "check")
    run(root, "roles", "template", "decline", "Engineering")
    run(root, "roles", "template", "check")
    assert not roles.proposal_path(config, "Engineering").exists()
    assert "ci.rerun_failed" not in (root / "Roles/Engineering/Role.md").read_text()


def strike_of(root, prefix):
    return next(entry for entry in index(root).struck if entry.text.startswith(prefix))


def test_a_strike_is_dated_and_signed_by_the_commit_that_made_it(org):
    root, person = org
    person_edits(person, lambda text: text.replace("## Naming", "## Struck"), "strike the whole section")
    run(root, "pull")
    struck = strike_of(root, "Name things")
    assert (struck.how, struck.by) == ("moved", "Jo")
    assert struck.commit == git(root.parent, "log", "-1", "--format=%h", "--abbrev=12", "HEAD")


def test_a_strike_outlives_the_agent_removing_the_struck_section(org):
    root, person = org
    person_edits(person, lambda text: text.replace("## Naming", "## Struck"))
    run(root, "pull")
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text().split("## Struck")[0])
    git(root.parent, "commit", "-qam", "consolidate: tidy")
    assert {entry.text.split(" (")[0] for entry in index(root).struck} == {
        "Name things after the domain object", "Prefer a parameter over a sibling method"}


def test_a_line_deleted_before_it_had_an_id_is_struck(org):
    root, person = org
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text() + "- Keep migrations reversible (review on #20, jo, 2026-10-03)\n")
    git(root.parent, "commit", "-qam", "learn more"); git(root.parent, "push", "-q", "origin", "main")
    git(person, "pull", "-q", "origin", "main")
    person_edits(person, lambda text: text.replace("- Keep migrations reversible (review on #20, jo, 2026-10-03)\n", ""))
    run(root, "pull")
    assert strike_of(root, "Keep migrations reversible").how == "deleted"


def test_a_struck_id_is_never_given_to_a_new_line(org):
    root, person = org
    ident = next(lesson.id for lesson in index(root).lessons if lesson.text.startswith("Prefer"))
    person_edits(person, lambda text: "\n".join(l for l in text.splitlines() if not l.startswith("- Prefer")) + "\n")
    run(root, "pull")
    assert ident in roles._ids_in_history(Config.load(root), "Engineering")


def test_an_uncommitted_strike_counts_without_a_rebuild_and_is_never_tagged(org):
    root, _ = org
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text().replace("## Naming", "## Struck") + "\n## Errors\n\n- Fail loudly (Jo, 2026-10-09)\n")
    assert {entry.how for entry in index(root).struck} == {"moved"}
    run(root, "roles", "rebuild")
    assert "^" not in taste.read_text().splitlines()[-1]   # a pending edit is left alone until committed


def test_a_line_deleted_while_resolving_a_merge_is_struck(org, tmp_path):
    root, person = org
    git(person, "checkout", "-qb", "side")
    path = person / "memory/Roles/Engineering/Kinds.md"
    path.write_text(path.read_text() + "\nside note\n")
    git(person, "commit", "-qam", "side")
    git(person, "checkout", "-q", "main")
    taste = person / "memory/Roles/Engineering/Taste.md"
    git(person, "merge", "-q", "--no-ff", "--no-commit", "side")
    taste.write_text("\n".join(l for l in taste.read_text().splitlines() if not l.startswith("- Name things")) + "\n")
    git(person, "add", "-A"); git(person, "commit", "-qm", "merge side")
    git(person, "push", "-q", "origin", "main")
    run(root, "pull")
    assert strike_of(root, "Name things").by == "Jo"


def test_a_pull_records_every_file_both_sides_changed(org):
    root, person = org
    for rel in ("Taste.md", "Kinds.md"):
        other = person / f"memory/Roles/Engineering/{rel}"
        other.write_text(other.read_text() + "\nperson\n")
    git(person, "commit", "-qam", "person"); git(person, "push", "-q", "origin", "main")
    shas = {}
    for rel in ("Taste.md", "Kinds.md"):
        mine = root / f"Roles/Engineering/{rel}"
        mine.write_text(mine.read_text() + "\nagent\n")
        git(root.parent, "commit", "-qam", f"agent {rel}")
        shas[rel] = git(root.parent, "log", "-1", "--format=%h", "--abbrev=12")
    run(root, "pull")
    kept = validate.kept_edits(Vault(root))
    assert len(kept) == 2
    assert all(any(rel in line and sha in line for line in kept) for rel, sha in shas.items())


def test_a_second_strike_is_dated_by_its_own_commit(org):
    root, person = org
    line = next(l for l in (root / "Roles/Engineering/Taste.md").read_text().splitlines() if l.startswith("- Prefer"))
    person_edits(person, lambda text: text.replace(line + "\n", ""), "strike once")
    run(root, "pull")
    first = strike_of(root, "Prefer").commit
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text() + line + "\n")
    git(root.parent, "commit", "-qam", "agent relearns"); git(root.parent, "push", "-q", "origin", "main")
    git(person, "pull", "-q", "origin", "main")
    person_edits(person, lambda text: text.replace(line + "\n", ""), "strike again")
    run(root, "pull")
    assert strike_of(root, "Prefer").commit != first


def test_a_person_restoring_a_struck_line_unstrikes_it_even_with_an_id(org):
    root, person = org
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text() + "- Keep migrations reversible (review on #20, jo, 2026-10-03)\n")
    git(root.parent, "commit", "-qam", "learn"); git(root.parent, "push", "-q", "origin", "main")
    git(person, "pull", "-q", "origin", "main")
    person_edits(person, lambda text: text.replace("- Keep migrations reversible (review on #20, jo, 2026-10-03)\n", ""))
    person_edits(person, lambda text: text + "- Keep migrations reversible (review on #20, jo, 2026-10-03) ^beef\n")
    run(root, "pull")
    assert not any(e.text.startswith("Keep migrations") for e in index(root).struck)


def test_the_same_words_under_two_ids_are_two_lines(org):
    root, person = org
    person_edits(person, lambda text: text + "- Same words (Jo, 2026-10-05) ^aaa1\n- Same words (Jo, 2026-10-05) ^bbb2\n")
    person_edits(person, lambda text: text.replace("- Same words (Jo, 2026-10-05) ^aaa1\n", ""))
    run(root, "pull")
    assert [e.id for e in index(root).struck if e.text.startswith("Same words")] == ["aaa1"]


def test_a_strike_gets_the_id_its_line_is_given_later(org):
    root, _ = org
    taste = root / "Roles/Engineering/Taste.md"
    taste.write_text(taste.read_text() + "\n## Struck\n\n- Untagged and struck (Jo, 2026-10-05)\n")
    git(root.parent, "commit", "-qam", "strike")
    run(root, "roles", "rebuild")
    assert strike_of(root, "Untagged").id


def test_an_uncommitted_template_change_refreshes_the_index(org):
    root, _ = org
    assert any(l.note.endswith("Taste.md") for l in index(root).lessons)
    role = root / "Roles/Engineering/Role.md"
    role.write_text(role.read_text().replace("- shape: Taste\n", "- shape: Tastes\n"))
    assert not any(l.note.endswith("Taste.md") for l in index(root).lessons)
