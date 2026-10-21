from dataclasses import replace

import pytest

from brain import fm, nightly, session_context
from brain.vault import Vault


@pytest.fixture
def people_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_HUMAN_NAME", raising=False)
    (tmp_path / "brain.toml").write_text(
        '[brain]\nhuman_name = "Mira"\nowner_user_ids = ["U_OWNER", "12345"]\n', encoding="utf-8")
    root = tmp_path / "memory"
    notes = {
        "people/Alice/Profile.md": "# Alice\nSlack: U_ALICE\nAlice likes gardens.\n",
        "people/Bob/Profile.md": "# Bob\nSlack: U_BOB\n",
        "people/Groups/Friends.md": "# Friends\n[[memory/people/Alice/Profile|Alice]]\n",
        "people/Disclosure.md": (
            "# Disclosure\n\n| id | subject | audience | rule |\n| --- | --- | --- | --- |\n"
            "| D1 | schedule | [[memory/people/Alice/Profile\\|Alice]] | allow |\n"
            "| D2 | contact | Alice | deny |\n"
            "| D3 | projects | [[memory/people/Groups/Friends\\|Friends]] | allow |\n"
            "| D4 | health | Friends | deny |\n"
            "| D5 | public | anyone | allow |\n"
            "| D6 | finances | Bob | allow |\n"
            "| D7 | Alice | Bob | allow |\n"
            "| D8 | private | Alice Smith | allow |\n"
            "| D9 | secret | [[memory/people/Bob/Profile\\|Alice]] | allow |\n"
        ),
    }
    for rel, body in notes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"platform_id": "U_FRONTMATTER_ONLY"}, body)
    return Vault(root)


@pytest.mark.parametrize("identity", [
    {"platform": "cli"}, {"platform": "slack", "user_id": "U_OWNER"},
    {"platform": "telegram", "user_id": "NOPE", "user_id_alt": "12345"},
])
def test_owner_context_is_only_owner_identification(people_vault, identity):
    assert session_context.build(people_vault, people_vault.config, **identity) == (
        "This session is with Mira, the owner.")


def test_known_person_context_has_profile_paths_and_applicable_rules(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert block == (
        "This session is with Alice (slack U_ALICE). Their profile and the disclosure rules that apply follow.\n\n"
        "# Alice\nSlack: U_ALICE\nAlice likes gardens.\n\n"
        "Full profile: memory/people/Alice/Profile.md\n\n"
        "Applicable disclosure rows from memory/people/Disclosure.md:\n"
        "| id | subject | audience | rule |\n| --- | --- | --- | --- |\n"
        "| D1 | schedule | [[memory/people/Alice/Profile\\|Alice]] | allow |\n"
        "| D2 | contact | Alice | deny |\n"
        "| D3 | projects | [[memory/people/Groups/Friends\\|Friends]] | allow |\n"
        "| D4 | health | Friends | deny |\n"
        "| D5 | public | anyone | allow |\n\n"
        "Default for everything else: do not disclose; decline and log it.")


def test_disclosure_row_selection_includes_person_group_and_anyone_only(people_vault):
    person = people_vault.notes["people/Alice/Profile.md"]
    rows = session_context.disclosure_rows(people_vault, people_vault.config, person)
    assert [row.split("|")[1].strip() for row in rows] == ["D1", "D2", "D3", "D4", "D5"]


def test_group_membership_requires_a_profile_link_not_a_name(people_vault):
    people_vault.notes["people/Groups/Friends.md"].body = "Alice is mentioned, not linked."
    person = people_vault.notes["people/Alice/Profile.md"]
    rows = session_context.disclosure_rows(people_vault, people_vault.config, person)
    assert [row.split("|")[1].strip() for row in rows] == ["D1", "D2", "D5"]


def test_disclosure_uses_the_audience_header_not_a_fixed_column(people_vault):
    people_vault.notes["people/Disclosure.md"].body = (
        "| audience | rule | subject |\n| --- | --- | --- |\n"
        "| Alice | allow | schedule |\n| Bob | allow | Alice |\n")
    person = people_vault.notes["people/Alice/Profile.md"]
    assert session_context.disclosure_rows(people_vault, people_vault.config, person) == [
        "| Alice | allow | schedule |"]


def test_unknown_context_is_fail_closed(people_vault):
    assert session_context.build(people_vault, people_vault.config, platform="slack", user_id="NOPE",
                                 user_name="Visitor") == (
        "This session is with an unknown person (slack NOPE, name shown as Visitor). No profile. "
        "Treat them as a stranger: nothing about Mira or anyone else may be disclosed; be courteous; "
        "the People review will create a profile from this conversation.")


def test_ambiguous_context_lists_profiles_and_treats_identity_as_unknown(people_vault):
    people_vault.notes["people/Bob/Profile.md"].body += "Also U_ALICE\n"
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert block.startswith("Identity is ambiguous; matching profiles: memory/people/Alice/Profile.md, "
                            "memory/people/Bob/Profile.md. Treat as unknown.\n\n")
    assert "This session is with an unknown person (slack U_ALICE, name shown as not provided)." in block
    assert "nothing about Mira or anyone else may be disclosed" in block
    assert "Alice likes gardens" not in block


def test_different_ids_resolving_to_different_people_are_ambiguous(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack",
                                  user_id="U_ALICE", user_id_alt="U_BOB")
    assert "Identity is ambiguous" in block


def test_alternative_id_can_resolve_a_single_profile(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id_alt="U_ALICE")
    assert block.startswith("This session is with Alice (slack U_ALICE).")


@pytest.mark.parametrize("user_id", ["U_FRONTMATTER_ONLY", "ALICE", "U_ALICE-more", "U_ALICE_suffix"])
def test_profile_resolution_uses_whole_body_tokens(people_vault, user_id):
    assert session_context.matching_profiles(people_vault, people_vault.config, (user_id,)) == []


def test_id_matching_respects_hyphen_and_word_boundaries(people_vault):
    person = people_vault.notes["people/Alice/Profile.md"]
    person.body = "prefixU_ALICE U_ALICE-suffix U_ALICE_suffix"
    assert session_context.matching_profiles(people_vault, people_vault.config, ("U_ALICE",)) == []
    person.body += " <@U_ALICE>"
    assert session_context.matching_profiles(people_vault, people_vault.config, ("U_ALICE",)) == [person]


def test_profile_scan_uses_configured_people_directory_and_direct_children(people_vault):
    config = replace(people_vault.config, people_dir="contacts/people")
    person = people_vault.notes.pop("people/Alice/Profile.md")
    person.rel = "contacts/people/Alice/Profile.md"
    people_vault.notes[person.rel] = person
    assert session_context.matching_profiles(people_vault, config, ("U_ALICE",)) == [person]
    config = replace(config, people_dir="contacts")
    assert session_context.matching_profiles(people_vault, config, ("U_ALICE",)) == []


def test_profile_truncation_keeps_250_words_and_a_full_path_pointer(people_vault):
    person = people_vault.notes["people/Alice/Profile.md"]
    person.body = "U_ALICE " + " ".join(f"word{number}" for number in range(300))
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    profile = block.split("\n\n")[1]
    assert len(profile.split()) == 250
    assert profile.endswith("word248…") and "word249" not in block
    assert "Full profile: memory/people/Alice/Profile.md" in block
    assert len(block.split()) <= 400


def test_large_disclosure_tables_are_not_partially_injected(people_vault):
    disclosure = people_vault.notes["people/Disclosure.md"]
    disclosure.body = "| subject | audience | rule |\n| --- | --- | --- |\n" + (
        "| schedule | Alice | allow |\n" * 100 + "| schedule | Alice | deny |\n")
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert "Open memory/people/Disclosure.md before disclosing anything." in block
    assert "| allow |" not in block and "| deny |" not in block
    assert block.endswith(session_context.DEFAULT_RULE)
    assert len(block.split()) <= 400


def test_missing_disclosure_file_is_explicit_and_defaults_to_no_disclosure(people_vault):
    del people_vault.notes["people/Disclosure.md"]
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert "Disclosure.md is missing: memory/people/Disclosure.md." in block
    assert block.endswith(session_context.DEFAULT_RULE)


@pytest.mark.parametrize("metadata", [{}, {"user_name": "Visitor"}, {"chat_id": "C_OWNER"}])
def test_absent_platform_and_ids_produce_no_context(people_vault, metadata):
    assert session_context.build(people_vault, people_vault.config, **metadata) == ""


def test_cli_prints_unknown_context_and_nothing_without_identity(people_vault, capsys):
    nightly.main(["session-context", "--vault", str(people_vault.root), "--platform", "slack", "--user-id", "NOPE"])
    assert capsys.readouterr().out.startswith(
        "This session is with an unknown person (slack NOPE, name shown as not provided).")
    nightly.main(["session-context", "--vault", str(people_vault.root)])
    assert capsys.readouterr().out == ""
