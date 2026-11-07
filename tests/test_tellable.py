import json
from dataclasses import replace

import pytest

from brain import access, cli, compile_context, fm, install, session_context, tellable, validate
from brain.config import Config
from brain.vault import Vault

ALICE = "people/Alice_Example/Profile.md"
BOB = "people/Bob/Profile.md"
FRIENDS = "people/Groups/Close friends.md"
HEADER = "| id | audience | subject | tell | do | date | source |\n| --- | --- | --- | --- | --- | --- | --- |\n"
BODY = """# Tellable

The owner's own sourced facts.

## experiences
- Likes Dr. Lee on Mission St · said · 2026-10-30 · [[START]]
  - gist: has a dentist they like in the Mission
- Stayed at the Memmo Alfama; would go back · transactional · 2026-11-04 · mail: booking confirmation
- Returned the uncomfortable headphones · transactional · 2026-11-05 · mail: return confirmation
  - gist: returned some headphones

## schedule
- Climbing at Mission Cliffs most Tuesday evenings · calendar · 2026-11-04 · calendar: Climbing
  - gist: usually busy Tuesday evenings

## opinions
- Short essays over long books · sent · 2026-11-01 · mail: reading
  - gist: likes short reading
- Loves jazz · said · 2026-10-29 · [[START]]

## health
- Has migraines · said · 2026-11-01 · [[START]]
  - gist: has a recurring health problem
"""


def write_tellable(root, body=BODY, rel="human/Tellable.md"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"kind": "orientation", "pinned": True, "tier": "hot"}, body)
    return path


def set_rules(vault, *rows, tail=""):
    note = vault.notes["people/Disclosure.md"]
    note.body = HEADER + "\n".join(
        f"| D{i} | {audience} | {subject} | {level} | no | 2026-11-01 | owner |"
        for i, (audience, subject, level) in enumerate(rows)) + "\n" + tail
    note.save()


@pytest.fixture
def tellable_vault(vault, monkeypatch):
    monkeypatch.delenv("BRAIN_HUMAN_NAME", raising=False)
    (vault.parent / "brain.toml").write_text(
        '[brain]\nvault = "memory"\nhuman_name = "Mira"\n'
        'owner_user_ids = ["slack:U_OWNER"]\n')
    notes = {
        ALICE: "# Alicia\n- **Demiurge:** `k57alice`\n- **Slack:** `U_ALICE`\n",
        BOB: "# Bob\nSlack: U_BOB\n",
        FRIENDS: f"# Close friends\n\n## Members\n- [[{ALICE}]]\n",
        "agents/Nameless/Profile.md": (
            f"# Nameless\n- **Owner:** [[memory/{ALICE.removesuffix('.md')}|Alicia]]\n"
            "- **Demiurge:** `k57nameless`\n"),
        "people/Disclosure.md": HEADER,
    }
    for rel, body in notes.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"kind": "group" if rel == FRIENDS else "profile", "tier": "warm"}, body)
    write_tellable(vault)
    loaded = Vault(vault)
    set_rules(loaded, (f"[[{FRIENDS}]]", "experiences", "details"),
              ("known", "experiences", "gist"), ("anyone", "experiences", "gist"),
              ("known", "schedule", "details"), ("known", "opinions", "gist"))
    return loaded


def test_parse_retains_text_kind_date_source_and_optional_gist():
    lines, problems = tellable.parse(BODY)
    assert problems == [] and len(lines) == 7
    assert lines[0] == tellable.Line("experiences", "Likes Dr. Lee on Mission St", "said", "2026-10-30",
                                     "[[START]]", "has a dentist they like in the Mission")
    assert lines[1].kind == "transactional" and lines[1].gist == ""
    assert lines[3].kind == "calendar" and lines[4].kind == "sent"
    assert not hasattr(lines[0], "id")


@pytest.mark.parametrize("section, problem", [("Hobbies", "unknown"), ("Experiences", "unknown"),
                                               ("people", "forbidden"), ("anything", "forbidden"),
                                               ("setup", "forbidden")])
def test_unknown_and_forbidden_sections_are_reported_and_omitted(section, problem):
    lines, problems = tellable.parse(f"## {section}\n- Fact · said · 2026-11-01 · [[START]]\n")
    assert lines == [] and f"{problem} section {section!r}" in problems


@pytest.mark.parametrize("line", ["- no fields", "- Fact · said · 2026-11-01 · ",
                                  "-  · said · 2026-11-01 · source", "- Fact · said · 2026-11-01 · source ^abcd",
                                  "- Fact · said · 2026-11-01 · source · extra", "  - summary: vague"])
def test_malformed_lines_are_reported_and_omitted(line):
    lines, problems = tellable.parse("## experiences\n" + line + "\n")
    assert lines == [] and any("malformed line" in problem for problem in problems)


@pytest.mark.parametrize("kind", ["seen", "guess", "received", "forwarded", "cc", "group"])
def test_other_kinds_are_reported_and_never_returned(kind):
    lines, problems = tellable.parse(f"## experiences\n- Fact · {kind} · 2026-11-01 · source\n")
    assert lines == [] and any(f"kind {kind!r}" in problem for problem in problems)


@pytest.mark.parametrize("evidence", ["2026-02-30", "11/01", "20261101", "2026-W01-1"])
def test_bad_dates_are_reported_and_never_returned(evidence):
    lines, problems = tellable.parse(f"## experiences\n- Fact · said · {evidence} · source\n")
    assert lines == [] and any(f"evidence date {evidence!r} is not YYYY-MM-DD" in problem for problem in problems)


def test_gist_requires_a_valid_line_in_the_current_section():
    body = ("  - gist: no section\n## experiences\n  - gist: no line\n"
            "- Valid · said · 2026-11-01 · source\n## experiences\n  - gist: new section\n"
            "- malformed\n  - gist: no valid line\n")
    lines, problems = tellable.parse(body)
    assert len(lines) == 1 and lines[0].gist == ""
    assert sum("gist: line with no line above it" in problem for problem in problems) == 4
    assert any("malformed line" in problem for problem in problems)


def test_custom_subjects_work_in_resolve_and_validation(tellable_vault):
    vault = tellable_vault
    set_rules(vault, ("anyone", "pets", "details"), tail="\n## Subjects\n- pets: the owner's animals\n")
    write_tellable(vault.root, "## pets\n- Has a cat · said · 2026-11-01 · [[START]]\n")
    vault.load()
    assert validate.tellable_hygiene(vault) == []
    assert tellable.resolve(vault, vault.config)["subjects"] == {"pets": {"level": "details", "lines": ["Has a cat"]}}


def test_validate_reports_tellable_problems(tellable_vault, capsys):
    write_tellable(tellable_vault.root, "## experiences\n- broken\n## people\n")
    vault = Vault(tellable_vault.root)
    assert any("human/Tellable.md: experiences: malformed line" in problem for problem in validate.hygiene(vault))
    assert any("human/Tellable.md: forbidden section 'people'" in problem for problem in validate.tellable_hygiene(vault))
    with pytest.raises(SystemExit) as error:
        cli.main(["validate", "--vault", str(vault.root)])
    assert error.value.code == 1 and "human/Tellable.md: experiences: malformed line" in capsys.readouterr().out


def test_resolver_details_gist_none_and_newest_first(tellable_vault):
    result = tellable.resolve(tellable_vault, tellable_vault.config, "alicia")
    assert result == {"person": ALICE, "groups": [FRIENDS], "subjects": {
        "experiences": {"level": "details", "lines": ["Returned the uncomfortable headphones",
                         "Stayed at the Memmo Alfama; would go back", "Likes Dr. Lee on Mission St"]},
        "schedule": {"level": "details", "lines": ["Climbing at Mission Cliffs most Tuesday evenings"]},
        "opinions": {"level": "gist", "lines": ["likes short reading"]},
    }}
    for who in ("Bob", "known"):
        subjects = tellable.resolve(tellable_vault, tellable_vault.config, who)["subjects"]
        assert subjects["experiences"] == {"level": "gist", "lines": ["returned some headphones",
                                                                   "has a dentist they like in the Mission"]}
        assert subjects["opinions"]["lines"] == ["likes short reading"]
        assert "health" not in subjects
    for who in ("anyone", "nobody"):
        assert tellable.resolve(tellable_vault, tellable_vault.config, who) == {
            "person": None, "groups": [], "subjects": {
                "experiences": {"level": "gist", "lines": ["returned some headphones", "has a dentist they like in the Mission"]}}}


def test_forbidden_sections_and_invalid_facts_never_leave_even_with_anything_access(tellable_vault):
    vault = tellable_vault
    set_rules(vault, ("anyone", "anything", "details"))
    body = ("## people\n- Third party · said · 2026-11-01 · source\n## setup\n- Machinery · said · 2026-11-01 · source\n"
            "## anything\n- Wildcard · sent · 2026-11-01 · source\n## experiences\n"
            "- Received mail · received · 2026-11-01 · source\n- Bad date · said · yesterday · source\n")
    write_tellable(vault.root, body)
    vault.load()
    assert tellable.resolve(vault, vault.config)["subjects"] == {}


def test_word_cap_drops_oldest_lines_across_subjects(tellable_vault):
    vault = tellable_vault
    set_rules(vault, ("anyone", "anything", "details"))
    body = ""
    for subject, days in (("experiences", (1, 8, 4)), ("schedule", (2, 7, 3)), ("opinions", (6, 5))):
        body += f"## {subject}\n"
        for day in days:
            body += f"- day{day} " + "word " * 89 + f"· said · 2026-11-{day:02} · source\n"
    write_tellable(vault.root, body)
    vault.load()
    result = tellable.resolve(vault, vault.config)
    assert len(tellable.render(result).split()) == 558
    assert {subject: [text.split()[0] for text in value["lines"]] for subject, value in result["subjects"].items()} == {
        "experiences": ["day8", "day4"], "schedule": ["day7", "day3"], "opinions": ["day6", "day5"]}


@pytest.mark.parametrize("words, kept", [(597, True), (598, False)])
def test_cap_counts_rendered_subject_labels_and_drops_whole_lines(tellable_vault, words, kept):
    vault = tellable_vault
    set_rules(vault, ("anyone", "experiences", "details"))
    write_tellable(vault.root, "## experiences\n- " + "word " * words + "· said · 2026-11-01 · source\n")
    vault.load()
    result = tellable.resolve(vault, vault.config)
    assert bool(result["subjects"]) is kept
    assert len(tellable.render(result).split()) <= tellable.WORDS
    if not kept:
        assert tellable.render(result) == "Nothing yet."


def test_cap_uses_gist_size_and_evidence_dates(tellable_vault):
    vault = tellable_vault
    body = "## experiences\n" + "".join(
        f"- Detail {day} · said · 2026-11-{day:02} · source\n  - gist: " + f"day{day} " + "word " * 89 + "\n"
        for day in range(1, 9))
    write_tellable(vault.root, body)
    vault.load()
    result = tellable.resolve(vault, vault.config)
    assert [text.split()[0] for text in result["subjects"]["experiences"]["lines"]] == [f"day{day}" for day in range(8, 2, -1)]
    assert len(tellable.render(result).split()) == 558


@pytest.mark.parametrize("who", ["alice example", "ALICE_EXAMPLE", "alicia", ALICE,
                                 ALICE.removesuffix(".md"), "demiurge:k57alice", "k57alice", "slack:U_ALICE"])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_person_forms_text_and_json(tellable_vault, capsys, who, json_output):
    cli.main(["tellable", who, "--vault", str(tellable_vault.root)] + (["--json"] if json_output else []))
    captured = capsys.readouterr()
    assert captured.err == ""
    result = tellable.resolve(tellable_vault, tellable_vault.config, ALICE)
    if json_output:
        assert json.loads(captured.out) == result
    else:
        assert captured.out == f"Person: memory/{ALICE} · Groups: memory/{FRIENDS}\n" + tellable.render(result) + "\n"


@pytest.mark.parametrize("who", ["known", "anyone"])
def test_cli_audiences_are_not_reported_as_unknown(tellable_vault, capsys, who):
    cli.main(["tellable", who, "--vault", str(tellable_vault.root), "--json"])
    captured = capsys.readouterr()
    assert captured.err == "" and json.loads(captured.out) == tellable.resolve(tellable_vault, tellable_vault.config, who)


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_unknown_resolves_as_stranger_and_says_so(tellable_vault, capsys, json_output):
    cli.main(["tellable", "unknown", "--vault", str(tellable_vault.root)] + (["--json"] if json_output else []))
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out) == tellable.resolve(tellable_vault, tellable_vault.config)
        assert "no profile matches 'unknown'; resolving as stranger." in captured.err
    else:
        assert "Person: stranger (no profile matches 'unknown') · Groups: none" in captured.out
        assert "- experiences (gist): returned some headphones" in captured.out and captured.err == ""


def test_cli_ambiguous_person_exits_with_matching_profiles(tellable_vault):
    note = tellable_vault.notes[BOB]
    note.body = "# Alicia\nDemiurge: k57alice\n"
    note.save()
    with pytest.raises(SystemExit) as error:
        cli.main(["tellable", "alicia", "--vault", str(tellable_vault.root)])
    assert str(error.value) == f"brain tellable: ambiguous person; matching profiles: memory/{ALICE}, memory/{BOB}"


def test_missing_note_has_no_lines_and_cli_says_nothing_yet(tellable_vault, capsys):
    vault = tellable_vault
    (vault.root / vault.config.human_tellable).unlink()
    vault.load()
    assert validate.tellable_hygiene(vault) == []
    assert tellable.resolve(vault, vault.config, ALICE) == {"person": ALICE, "groups": [FRIENDS], "subjects": {}}
    cli.main(["tellable", "alicia", "--vault", str(vault.root)])
    assert capsys.readouterr().out.endswith("\nNothing yet.\n")


@pytest.mark.parametrize("platform, user_id, name, who", [
    ("slack", "U_ALICE", "Alice Example", ALICE),
    ("demiurge", "k57nameless", "Alicia", ALICE),
    ("slack", "U_STRANGER", "anyone", "anyone"),
])
def test_outside_sessions_end_with_the_exact_resolved_block(tellable_vault, platform, user_id, name, who):
    vault = tellable_vault
    block = session_context.build(vault, vault.config, platform=platform, user_id=user_id)
    expected = (f"What you may tell {name} about Mira, and nothing else:\n"
                + tellable.render(tellable.resolve(vault, vault.config, who))
                + "\nAnswer about Mira only from this block; anything else you know about Mira or anyone else is not yours "
                "to tell here. What they ask that it does not answer, ask Mira.")
    assert block.endswith(expected)
    assert sum(line.startswith("What you may tell ") for line in block.splitlines()) == 1
    if "may learn, resolved" in block:
        assert block.index("may learn, resolved") < block.index(expected)
    assert session_context.TELLABLE_HEADING.format(name=name, owner="Mira") == expected.splitlines()[0]


@pytest.mark.parametrize("platform, user_id", [("cli", ""), ("slack", "U_OWNER"), ("cron", "")])
def test_owner_and_cron_sessions_have_no_tellable_block(tellable_vault, platform, user_id):
    block = session_context.build(tellable_vault, tellable_vault.config, platform=platform, user_id=user_id)
    assert "What you may tell " not in block and "Likes Dr. Lee" not in block


def test_enterprise_members_are_exempt_but_intellects_get_the_block(tellable_vault):
    vault = tellable_vault
    config = replace(vault.config, edition="enterprise")
    for platform, user_id in (("cli", ""), ("slack", "U_OWNER"), ("slack", "U_ALICE"), ("slack", "U_STRANGER")):
        block = session_context.build(vault, config, platform=platform, user_id=user_id)
        assert "a member of Mira" in block and "What you may tell " not in block
    block = session_context.build(vault, config, platform="demiurge", user_id="k57nameless")
    assert "What you may tell Alicia about Mira, and nothing else:\n- experiences (details):" in block


def test_ambiguous_identity_gets_anyone_lines(tellable_vault):
    vault = tellable_vault
    vault.notes[BOB].body += "Slack: U_ALICE\n"
    block = session_context.build(vault, vault.config, platform="slack", user_id="U_ALICE")
    assert "Identity is ambiguous" in block
    assert "What you may tell anyone about Mira, and nothing else:\n- experiences (gist):" in block
    assert "Stayed at the Memmo" not in block and "Likes Dr. Lee" not in block


@pytest.mark.parametrize("owner", ["", "Missing person", "Alicia"])
def test_unresolved_intellect_owner_falls_back_to_anyone(tellable_vault, owner):
    vault = tellable_vault
    agent = vault.notes["agents/Nameless/Profile.md"]
    agent.body = "# Nameless\nDemiurge: k57nameless\n" + (f"Owner: {owner}\n" if owner else "")
    if owner == "Alicia":
        vault.notes[BOB].body = "# Alicia\n"
    block = session_context.build(vault, vault.config, platform="demiurge", user_id="k57nameless")
    assert "could not be resolved" in block
    assert "What you may tell anyone about Mira, and nothing else:\n- experiences (gist):" in block
    assert "Stayed at the Memmo" not in block


@pytest.mark.parametrize("user_id", ["U_ALICE", "U_STRANGER", "k57nameless"])
def test_missing_disclosure_still_has_an_empty_block(tellable_vault, user_id):
    vault = tellable_vault
    del vault.notes["people/Disclosure.md"]
    block = session_context.build(vault, vault.config, platform="slack", user_id=user_id)
    assert "and nothing else:\nNothing yet.\nAnswer about Mira only from this block;" in block
    assert "Stayed at the Memmo" not in block


def test_tellable_is_beside_configured_profile_and_never_compiled(tellable_vault):
    config = replace(tellable_vault.config, human_profile="People/Mira/Profile.md")
    assert config.human_tellable == "People/Mira/Tellable.md"
    assert config.template_values()["human_tellable"] == config.human_tellable
    assert config.human_tellable in config.orientation and config.human_tellable not in config.context_sources
    compiled = compile_context.build(tellable_vault)
    assert "Tellable.md" not in compiled and "Likes Dr. Lee" not in compiled
    assert "human/Tellable" not in compile_context.directory(tellable_vault)


def test_skills_render_tellable_path_and_keep_enterprise_boundary(tellable_vault):
    config = tellable_vault.config
    for name in ("sleep1", "recall"):
        rendered = install.render_skill(config, name)
        assert "`human/Tellable.md`" in rendered and "{human_tellable}" not in rendered
    enterprise_sleep = install.render_skill(replace(config, edition="enterprise"), "sleep1")
    assert "Tellable.md" not in enterprise_sleep
    enterprise_recall = install.render_skill(replace(config, edition="enterprise"), "recall")
    assert "Talking to people other than" not in enterprise_recall
    assert 'session\'s "What you may tell" block' in enterprise_recall
