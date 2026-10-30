"""Disclosure parsing, independent access decisions, and on-demand CLI checks."""
import json

import pytest

from brain import access, fm, nightly, validate
from brain.vault import Vault

ALICE = "people/Alice_Example/Profile.md"
BOB = "people/Bob/Profile.md"
CARA = "people/Cara/Profile.md"
FRIENDS = "people/Groups/Close friends.md"
WORK = "people/Groups/Work friends.md"
HEADER = "| id | audience | subject | tell | do | date | source |\n| --- | --- | --- | --- | --- | --- | --- |\n"


@pytest.fixture
def access_vault(vault):
    notes = {
        ALICE: "# Alicia\n- **Demiurge:** `k57alice`\nSlack: U_ALICE\n",
        BOB: "# Bob\nSlack: U_BOB\n",
        CARA: "# Cara\n",
        FRIENDS: (f"# Close friends\nMentioned, not members: [[{BOB}]]\n\n"
                  f"## Members, October 5, 2026\n- [[memory/{ALICE}|Alice]]\n"
                  f"### Confirmed\n- [[{CARA}]]\n\n## History\n[[{BOB}]]\n"),
        WORK: f"# Work friends\n[[{ALICE}]] and [[{CARA}]]\n[[{ALICE}]]\n[[START]]\n",
        "people/Groups/README.md": "# Groups\nNo members here.\n",
        "people/Disclosure.md": "# Disclosure\n\n" + HEADER,
    }
    for rel, body in notes.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tier": "warm"}
        if rel in (FRIENDS, WORK):
            data["kind"] = "group"
        fm.write(path, data, body)
    return Vault(vault)


def disclosure(vault, body):
    note = vault.notes["people/Disclosure.md"]
    note.body = body
    note.save()


def set_rules(vault, *rows, tail=""):
    """Rows of five cells; every rule gets a date and a source, as validation requires."""
    disclosure(vault, HEADER + "\n".join(row + " 2026-10-29 | test |" for row in rows) + "\n" + tail)


def test_vocabulary_and_vault_subjects(access_vault):
    disclosure(access_vault, """# Disclosure
## Subjects
- `pets`: the owner's animals
- music: musical projects and tastes
- `schedule`: cannot replace the engine's meaning
## Other
- secret: not a subject declaration
""")
    vocabulary = access.subjects(access_vault, access_vault.config)
    assert list(access.SUBJECTS) == ["schedule", "whereabouts", "plans", "work", "health", "finances",
                                     "contacts", "people", "opinions", "setup", "anything"]
    assert access.TELL_LEVELS == ("none", "gist", "details")
    assert access.DO_LEVELS == ("no", "ask", "yes")
    assert vocabulary == access.SUBJECTS | {"pets": "the owner's animals", "music": "musical projects and tastes"}
    assert "pets" not in access.SUBJECTS
    assert "secret" not in vocabulary
    result = access.resolve(access_vault, access_vault.config, None)
    assert result.subjects["pets"] == access.SubjectAccess("none", None, "no", None)
    assert validate.access_problems(access_vault) == []


def test_new_tables_parse_columns_links_fields_and_blank_opinions(access_vault):
    line = (f"| a quote | | `schedule` | D-001 | details | [[memory/{ALICE}\\|Alice]] "
            "| Owner | 2026-10-29 | prose |")
    disclosure(access_vault, "| SOURCE | DO | SUBJECT | ID | TELL | AUDIENCE | Granted By | Date | rule |\n"
               "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" + line + "\n\n"
               + HEADER + f"| D-002 | [[{FRIENDS}]] | plans | | ask | 2026-10-29 | test |\n"
               "\nAn unrelated table:\n| topic | description |\n| --- | --- |\n| rule | prose |\n")
    parsed = access.rules(access_vault, access_vault.config)
    assert len(parsed) == 2
    assert parsed[0] == access.Rule("D-001", access.Audience("person", ALICE), "schedule", "details", None,
                                    {"source": "a quote", "granted by": "Owner", "date": "2026-10-29",
                                     "rule": "prose"}, False, line)
    assert parsed[1].audience == access.Audience("group", FRIENDS)
    assert parsed[1].tell is None and parsed[1].do == "ask"
    assert validate.access_problems(access_vault) == []


def test_legacy_rules_are_read_only_and_not_validation_problems(access_vault):
    disclosure(access_vault, "| ID | subject | audience | rule |\n| --- | --- | --- | --- |\n"
               f"| D-001 | schedule | [[{ALICE}]] | Allow |\n"
               "| D-002 | health | Anyone | deny |\n"
               "| D-004 | fantasy banter | Verified current opponent, private conversation only | allow |\n\n" + HEADER
               + "| D-003 | known | plans | gist | ask | 2026-10-29 | test |\n")
    before = access_vault.notes["people/Disclosure.md"].path.read_bytes()
    parsed = access.rules(access_vault, access_vault.config)
    assert [(rule.tell, rule.do, rule.legacy) for rule in parsed] == [
        ("details", "no", True), ("none", "no", True), ("details", "no", True), ("gist", "ask", False)]
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.subjects["schedule"] == access.SubjectAccess("details", "D-001", "no", "D-001")
    assert validate.access_problems(access_vault) == []
    assert access_vault.notes["people/Disclosure.md"].path.read_bytes() == before


@pytest.mark.parametrize("heading", ["## Members", "## Members, October 5, 2026"])
def test_groups_use_members_heading_or_all_body_links(access_vault, heading):
    access_vault.notes[FRIENDS].body = (
        f"[[{BOB}]]\n{heading}\n[[memory/{ALICE}|Alice]]\n### More\n[[{CARA}]]\n"
        f"## Not members\n[[{BOB}]]\n")
    assert access.groups(access_vault, access_vault.config) == {FRIENDS: [ALICE, CARA], WORK: [ALICE, CARA]}
    access_vault.notes[FRIENDS].body = f"[[{ALICE}]]\n{heading}\n## History\n[[{BOB}]]"
    assert access.groups(access_vault, access_vault.config)[FRIENDS] == []


def test_group_dimensions_take_the_most_generous_rule_independently(access_vault):
    set_rules(access_vault,
              f"| D-001 | [[{FRIENDS}]] | schedule | details | ask |",
              f"| D-002 | [[{WORK}]] | schedule | gist | yes |")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.groups == [FRIENDS, WORK]
    assert result.subjects["schedule"] == access.SubjectAccess("details", "D-001", "yes", "D-002")


def test_person_beats_groups_even_with_none_and_no(access_vault):
    set_rules(access_vault,
              f"| D-001 | [[{FRIENDS}]] | schedule | details | yes |",
              f"| D-002 | [[{ALICE}]] | schedule | none | no |",
              "| D-003 | known | schedule | details | yes |",
              "| D-004 | anyone | schedule | details | yes |")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.subjects["schedule"] == access.SubjectAccess("none", "D-002", "no", "D-002")


@pytest.mark.parametrize("audience", [f"[[{ALICE}]]", f"[[{FRIENDS}]]", "known", "anyone"])
def test_subject_beats_anything_at_the_same_audience_level(access_vault, audience):
    set_rules(access_vault,
              f"| D-001 | {audience} | anything | details | yes |",
              f"| D-002 | {audience} | schedule | none | ask |",
              tail="\n## Subjects\n- pets: the owner's animals\n")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.subjects["schedule"] == access.SubjectAccess("none", "D-002", "ask", "D-002")
    for subject in ("work", "pets", "anything"):
        assert result.subjects[subject] == access.SubjectAccess("details", "D-001", "yes", "D-001")


def test_known_only_applies_to_real_profiles_and_anyone_applies_to_strangers(access_vault):
    set_rules(access_vault, "| D-001 | known | anything | details | ask |",
              "| D-002 | Anyone | opinions | gist | |")
    known = access.resolve(access_vault, access_vault.config, BOB)
    assert known.person == BOB and known.groups == []
    assert known.subjects["opinions"] == access.SubjectAccess("details", "D-001", "ask", "D-001")
    for person in (None, "people/Absent/Profile.md", FRIENDS):
        stranger = access.resolve(access_vault, access_vault.config, person)
        assert stranger.person is None and stranger.groups == []
        assert stranger.subjects["opinions"] == access.SubjectAccess("gist", "D-002", "no", None)
        assert stranger.subjects["work"] == access.SubjectAccess("none", None, "no", None)


def test_blank_cells_fall_through_independently(access_vault):
    set_rules(access_vault,
              f"| D-001 | [[{ALICE}]] | schedule | gist | |",
              f"| D-002 | [[{FRIENDS}]] | schedule | details | ask |",
              f"| D-003 | [[{ALICE}]] | plans | details | |",
              "| D-004 | known | plans | | yes |",
              "| D-005 | anyone | work | gist | |")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.subjects["schedule"] == access.SubjectAccess("gist", "D-001", "ask", "D-002")
    assert result.subjects["plans"] == access.SubjectAccess("details", "D-003", "yes", "D-004")
    assert result.subjects["work"] == access.SubjectAccess("gist", "D-005", "no", None)


def test_blank_subject_cell_leaves_wildcard_opinion_at_same_level(access_vault):
    set_rules(access_vault,
              f"| D-001 | [[{ALICE}]] | anything | gist | |",
              f"| D-002 | [[{ALICE}]] | schedule | | ask |",
              f"| D-003 | [[{FRIENDS}]] | anything | details | yes |")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.subjects["schedule"] == access.SubjectAccess("gist", "D-001", "ask", "D-002")
    assert result.subjects["work"] == access.SubjectAccess("gist", "D-001", "yes", "D-003")


def test_scope_collects_paragraphs_but_only_carries_deciding_rules(access_vault):
    set_rules(access_vault,
              f"| D-001 | [[{FRIENDS}]] | schedule | details | ask |",
              f"| D-002 | [[{WORK}]] | schedule | gist | yes |",
              "| D-003 | anyone | schedule | gist | no |",
              f"| D-004 | [[{ALICE}]] | health | none | no |",
              tail="""
## Grant scope
D-001 permits planning a visit,
not sharing in a room.

D-001 excludes appointments of other kinds.

D-002 permits calendar changes only.

D-003 is superseded for this person.

D-004 refuses health requests.

D-0010 is not D-001.

## Other
D-002 is outside Grant scope.
""")
    scopes = access.grant_scope(access_vault, access_vault.config)
    assert scopes == {
        "D-001": "D-001 permits planning a visit,\nnot sharing in a room.\n\nD-001 excludes appointments of other kinds.",
        "D-002": "D-002 permits calendar changes only.",
        "D-003": "D-003 is superseded for this person.",
        "D-004": "D-004 refuses health requests.",
    }
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.scope == {rule_id: scopes[rule_id] for rule_id in ("D-001", "D-002", "D-004")}


def test_missing_disclosure_defaults_without_errors(access_vault):
    del access_vault.notes["people/Disclosure.md"]
    assert access.rules(access_vault, access_vault.config) == []
    assert validate.access_problems(access_vault) == []
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert result.person == ALICE and result.groups == [FRIENDS, WORK]
    assert result.scope == {}
    assert all(value == access.SubjectAccess("none", None, "no", None) for value in result.subjects.values())


@pytest.mark.parametrize("who", ["alice example", "ALICE_EXAMPLE", "alicia", ALICE,
                                 ALICE.removesuffix(".md"), "demiurge:k57alice", "k57alice", "slack:U_ALICE"])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_name_path_and_platform_id(access_vault, capsys, who, json_output):
    set_rules(access_vault, f"| D-001 | [[{FRIENDS}]] | schedule | details | ask |",
              tail="\n## Grant scope\nD-001 permits planning a visit only.\n")
    nightly.main(["access", who, "--vault", str(access_vault.root)] + (["--json"] if json_output else []))
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_output:
        data = json.loads(captured.out)
        assert set(data) == {"person", "groups", "subjects", "scope"}
        assert data["person"] == ALICE and data["groups"] == [FRIENDS, WORK]
        assert data["subjects"]["schedule"] == {"tell": "details", "tellRule": "D-001", "do": "ask", "doRule": "D-001"}
        assert data["subjects"]["health"] == {"tell": "none", "tellRule": None, "do": "no", "doRule": None}
        assert data["scope"] == {"D-001": "D-001 permits planning a visit only."}
    else:
        assert captured.out.splitlines() == [
            f"Person: memory/{ALICE} · Groups: memory/{FRIENDS}, memory/{WORK}",
            "schedule: details (D-001) / ask (D-001)",
            "Everything else: none / no",
            "Scope: D-001 permits planning a visit only.",
        ]


def test_lookup_frontmatter_display_name_and_qualified_id_precedence(access_vault):
    access_vault.notes[ALICE].data["display_name"] = "Alice E."
    access_vault.notes[BOB].body += "Bob mentioned k57alice yesterday.\n"
    assert access.matching_people(access_vault, access_vault.config, "ALICE E.") == [access_vault.notes[ALICE]]
    assert access.matching_people(access_vault, access_vault.config, "demiurge:k57alice") == [access_vault.notes[ALICE]]
    assert access.matching_people(access_vault, access_vault.config, "k57alice") == [access_vault.notes[ALICE]]


def test_a_bare_name_or_id_in_prose_never_resolves_to_that_profile(access_vault):
    access_vault.notes[BOB].body += "Bob keeps asking about TARS and about k57carol.\n"
    assert access.matching_people(access_vault, access_vault.config, "TARS") == []
    assert access.matching_people(access_vault, access_vault.config, "k57carol") == []
    access_vault.notes[BOB].body += "- **Demiurge:** `k57carol`\n"
    assert access.matching_people(access_vault, access_vault.config, "k57carol") == [access_vault.notes[BOB]]


@pytest.mark.parametrize("who", ["alicia", "demiurge:k57alice", "k57alice"])
def test_cli_ambiguity_exits_with_matching_profiles(access_vault, who):
    note = access_vault.notes[BOB]
    note.body = "# Alicia\nDemiurge: k57alice\n"
    note.save()
    with pytest.raises(SystemExit) as error:
        nightly.main(["access", who, "--vault", str(access_vault.root)])
    assert "ambiguous" in str(error.value)
    assert ALICE in str(error.value) and BOB in str(error.value)
    assert error.value.code != 0


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_no_match_resolves_as_stranger_and_says_so(access_vault, capsys, json_output):
    set_rules(access_vault, "| D-001 | anyone | work | gist | |", "| D-002 | known | health | details | yes |")
    nightly.main(["access", "Visitor", "--vault", str(access_vault.root)] + (["--json"] if json_output else []))
    captured = capsys.readouterr()
    if json_output:
        data = json.loads(captured.out)
        assert data["person"] is None and data["groups"] == []
        assert data["subjects"]["work"]["tell"] == "gist"
        assert data["subjects"]["health"]["tell"] == "none"
        assert "no profile matches 'Visitor'; resolving as stranger" in captured.err
    else:
        assert "stranger (no profile matches 'Visitor')" in captured.out
        assert "work: gist (D-001) / no (default)" in captured.out
        assert "health:" not in captured.out


def test_validation_reports_bad_values_missing_metadata_duplicates_and_empty_groups(access_vault):
    disclosure(access_vault, "| id | audience | subject | tell | do | DATE | SOURCE |\n"
               "| --- | --- | --- | --- | --- | --- | --- |\n"
               "| D-001 | [[Missing]] | secrets | full | maybe | | |\n"
               "| D-001 | [[START]] | schedule | gist | ask | 2026-10-29 | a quote |\n")
    path = access_vault.root / "people/Groups/Empty.md"
    fm.write(path, {"tier": "warm", "kind": "group"}, "# Empty\nAlice is not a profile link.\n")
    access_vault.load()
    problems = validate.access_problems(access_vault)
    assert len(problems) == 9
    for text in ("unknown subject 'secrets'", "unknown tell level 'full'", "unknown do level 'maybe'",
                 "missing date", "missing source", "duplicate id", "[[Missing]]", "[[START]]",
                 "people/Groups/Empty.md: group has no members"):
        assert any(text in problem for problem in problems)


def test_every_rule_needs_a_date_and_a_source_even_without_the_columns(access_vault):
    set_rules(access_vault, "| D-001 | anyone | schedule | gist | |")
    assert validate.access_problems(access_vault) == []
    disclosure(access_vault, "| id | audience | subject | tell | do |\n| --- | --- | --- | --- | --- |\n"
               "| D-001 | anyone | finances | details | yes |\n")
    assert validate.access_problems(access_vault) == [
        "people/Disclosure.md: D-001: missing date", "people/Disclosure.md: D-001: missing source"]


def test_rows_for_one_audience_that_disagree_keep_the_strictest(access_vault):
    set_rules(access_vault, f"| D-001 | [[{FRIENDS}]] | schedule | details | yes |",
              f"| D-002 | [[{FRIENDS}]] | schedule | none | no |",
              f"| D-003 | [[{WORK}]] | plans | gist | |",
              f"| D-004 | [[{FRIENDS}]] | plans | details | |")
    result = access.resolve(access_vault, access_vault.config, ALICE)
    assert (result.subjects["schedule"].tell, result.subjects["schedule"].tell_rule) == ("none", "D-002")
    assert result.subjects["plans"].tell == "details"   # across groups, groups still add access


def test_an_unconfirmed_profile_is_not_known(access_vault):
    set_rules(access_vault, "| D-001 | known | schedule | gist | |")
    path = access_vault.notes[ALICE].path
    assert access.resolve(access_vault, access_vault.config, ALICE).subjects["schedule"].tell == "gist"
    path.write_text(path.read_text() + "\nIdentity unconfirmed by the owner.\n")
    vault = Vault(access_vault.root)
    assert access.resolve(vault, vault.config, ALICE).subjects["schedule"].tell == "none"


def test_access_checks_are_on_demand_not_a_nightly_gate(access_vault, capsys):
    set_rules(access_vault, "| D-001 | anyone | secret | gist | |")
    assert validate.run(access_vault, set(access_vault.notes)) == []
    assert validate.kinds(access_vault) == []
    with pytest.raises(SystemExit) as error:
        nightly.main(["validate", "--vault", str(access_vault.root)])
    assert error.value.code == 1
    assert capsys.readouterr().out == "people/Disclosure.md: D-001: unknown subject 'secret'\n1 problems\n"


def test_configured_people_directory_is_used_for_all_access_operations(access_vault):
    root = access_vault.root
    (root / "Contacts").mkdir()
    (root / "people").rename(root / "Contacts/People")
    (root.parent / "brain.toml").write_text('[brain]\nvault = "memory"\npeople_dir = "Contacts/People"\n')
    for rel in ("Groups/Close friends.md", "Groups/Work friends.md", "Disclosure.md"):
        path = root / "Contacts/People" / rel
        path.write_text(path.read_text().replace("people/", "Contacts/People/"))
    person = "Contacts/People/Alice_Example/Profile.md"
    fm.write(root / "Contacts/People/Disclosure.md", {"tier": "warm"}, HEADER
             + "| D-001 | [[Contacts/People/Groups/Close friends]] | schedule | details | ask | 2026-10-29 | test |\n")
    loaded = Vault(root)
    result = access.resolve(loaded, loaded.config, person)
    assert result.person == person
    assert result.groups == ["Contacts/People/Groups/Close friends.md", "Contacts/People/Groups/Work friends.md"]
    assert result.subjects["schedule"].tell == "details"
    assert access.matching_people(loaded, loaded.config, "alice example")[0].rel == person
    assert validate.access_problems(loaded) == []


def test_an_owner_profile_past_its_budget_is_a_validation_problem(access_vault):
    from brain import fm
    path = access_vault.root / access_vault.config.human_profile
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"kind": "hub"}, "# Owner\n" + "word " * 501)
    assert validate.owner_profile(Vault(access_vault.root)) == [
        f"{access_vault.config.human_profile}: 503 words; keep it under 500, dated material in History.md"]
    fm.write(path, {"kind": "hub"}, "# Owner\nShort.\n")
    assert validate.owner_profile(Vault(access_vault.root)) == []


def test_an_intellect_session_resolves_its_owners_access_inline(access_vault):
    from brain import session_context
    disclosure(access_vault, "# Disclosure\n\n" + HEADER
               + f"| D1 | [[memory/{ALICE}|Alice]] | schedule | details | ask | 2026-10-05 | owner |\n"
               + f"| D2 | [[memory/{ALICE}|Alice]] | whereabouts | gist | no | 2026-10-05 | owner |\n")
    root = access_vault.root
    (root / "agents/Nameless").mkdir(parents=True)
    fm.write(root / "agents/Nameless/Profile.md", {"kind": "profile", "tier": "warm"},
             f"# Nameless\n\n- **Owner:** [[memory/{ALICE.removesuffix('.md')}|Alice]]\n- **Demiurge:** `k57nameless`\n")
    vault = Vault(root)
    block = session_context.build(vault, vault.config, platform="demiurge", user_id="k57nameless")
    assert ", owned by Alice." in block
    assert "What Alice may learn, resolved from memory/people/Disclosure.md" in block
    assert "- schedule: tell details (D1); ask" in block and "- whereabouts: tell gist (D2)" in block
    assert "could not be resolved" not in block
    assert "A request it relays for" in block
