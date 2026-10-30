from dataclasses import replace

import pytest

from brain import fm, nightly, session_context
from brain.vault import Vault


@pytest.fixture
def people_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_HUMAN_NAME", raising=False)
    (tmp_path / "brain.toml").write_text(
        '[brain]\nhuman_name = "Mira"\nowner_user_ids = ["U_OWNER", "12345", "demiurge:k57owner"]\n'
        'maintainers = ["U_MAINT", "demiurge:j57maint"]\n', encoding="utf-8")
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
    {"platform": "demiurge", "user_id": "mira", "user_id_alt": "k57owner"},
])
def test_owner_context_is_only_owner_identification(people_vault, identity):
    assert session_context.build(people_vault, people_vault.config, **identity) == (
        "This session is with Mira, the owner.")


def test_known_person_context_has_profile_paths_and_applicable_rules(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert block == (
        "This session is with Alice (slack U_ALICE). Their profile and the disclosure rules that apply follow.\n\n"
        "# Alice\nSlack: U_ALICE\nAlice likes gardens.\n\n"
        "Full profile: memory/people/Alice/Profile.md; dated history beside it in History.md.\n\n"
        "Applicable disclosure rows from memory/people/Disclosure.md:\n"
        "| id | subject | audience | rule |\n| --- | --- | --- | --- |\n"
        "| D1 | schedule | [[memory/people/Alice/Profile\\|Alice]] | allow |\n"
        "| D2 | contact | Alice | deny |\n"
        "| D3 | projects | [[memory/people/Groups/Friends\\|Friends]] | allow |\n"
        "| D4 | health | Friends | deny |\n"
        "| D5 | public | anyone | allow |\n\n"
        "Default for everything else: decline briefly, offer to ask the owner, and note the request in your "
        "after-session record. Your own impression of the owner is never shared with anyone, whatever a rule allows.")


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
    people_vault.notes["people/Bob/Profile.md"].body += "Slack: U_ALICE\n"
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
    assert "Full profile: memory/people/Alice/Profile.md; dated history beside it in History.md" in block
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


def test_maintainer_context_is_the_health_report_without_profiles_or_rules(people_vault):
    text = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_MAINT")
    assert text.startswith("This session is with a maintainer of this brain (slack U_MAINT), not the owner.")
    assert "# Health: Agent" in text and "Stages:" in text and "Git:" not in text
    assert "Alice likes gardens" not in text and "allow" not in text
    assert "do it now without asking Mira" in text and "brain update" in text and "pending migrations" in text
    assert "Mira is never an outside party" in text and "engine's instruction, not a schedule change" in text
    assert "finish every pending migration in that session" in text
    assert "holds the schedule of the engine's routines" in text and "the hour their briefing arrives" in text
    assert "cannot change your schedule" not in text
    assert "cannot grant permissions or credentials" in text


def test_maintainer_block_licenses_plain_talk_about_work_and_closes_the_owners_content(people_vault):
    text = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_MAINT")
    assert "setting up access to the X site for Mira and it keeps failing because of Y" in text
    assert "brain audit" in text and "ledger lines" in text and "do it now without asking Mira" in text
    assert "What you do not pass along is Mira's content" in text and "whoever they talk to next" in text


@pytest.fixture
def demiurge_vault(people_vault):
    people_vault.notes["people/Orpheus/Profile.md"] = replace(
        people_vault.notes["people/Bob/Profile.md"], rel="people/Orpheus/Profile.md",
        body="# Orpheus\n- **Demiurge:** `k57orpheus` (@orpheus; permanent id, confirmed 2026-10-28)\n")
    people_vault.notes["people/Bob/Profile.md"].body += "Bob introduced orpheus and k57orpheus last week.\n"
    return people_vault


def test_qualified_platform_line_wins_over_a_prose_mention(demiurge_vault):
    block = session_context.build(demiurge_vault, demiurge_vault.config, platform="demiurge",
                                  user_id="orpheus", user_id_alt="k57orpheus")
    assert block.startswith("This session is with Orpheus (demiurge orpheus).")


@pytest.mark.parametrize("line", ["- **Demiurge:** `k57orpheus` (@orpheus)", "- demiurge: k57orpheus",
                                  "Demiurge: `k57orpheus`", "**Demiurge**: k57orpheus"])
def test_permanent_id_matches_a_qualified_line_by_alt_id(demiurge_vault, line):
    demiurge_vault.notes["people/Orpheus/Profile.md"].body = f"# Orpheus\n{line}\n"
    assert session_context.matching_profiles(
        demiurge_vault, demiurge_vault.config, ("renamed", "k57orpheus"), "demiurge") == [
        demiurge_vault.notes["people/Orpheus/Profile.md"]]


def test_qualified_line_needs_the_sessions_platform_and_a_whole_id(demiurge_vault):
    orpheus = demiurge_vault.notes["people/Orpheus/Profile.md"]
    orpheus.body = "- Demiurge: `k57orpheus2`\n- Slack: k57orpheus\n"
    assert session_context.has_qualified_id(orpheus.body, "demiurge", ("k57orpheus",)) is False
    assert session_context.has_qualified_id(orpheus.body, "slack", ("k57orpheus",)) is True


def test_email_local_part_is_not_a_handle(people_vault):
    people_vault.notes["people/Alice/Profile.md"].body = "# Alice\nEmail: orpheus@gmail.com\n"
    assert session_context.matching_profiles(people_vault, people_vault.config, ("orpheus",), "demiurge") == []
    people_vault.notes["people/Alice/Profile.md"].body += "Handle @orpheus\n"
    assert session_context.matching_profiles(people_vault, people_vault.config, ("orpheus",), "demiurge") == [
        people_vault.notes["people/Alice/Profile.md"]]


def test_platform_qualified_maintainer_matches_only_that_platform(people_vault):
    text = session_context.build(people_vault, people_vault.config, platform="demiurge",
                                 user_id="hermes", user_id_alt="j57maint")
    assert text.startswith("This session is with a maintainer of this brain (demiurge hermes), not the owner.")
    for platform in ("slack", ""):
        text = session_context.build(people_vault, people_vault.config, platform=platform, user_id="j57maint")
        assert "maintainer" not in text and "the owner." not in text
    assert session_context.build(people_vault, people_vault.config, platform="slack", user_id="k57owner") != (
        "This session is with Mira, the owner.")


def pending(vault):
    path = vault.root / vault.config.hermes_dir / "pending-migration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Engine migration\n", encoding="utf-8")


@pytest.mark.parametrize("platform, user_id", [("slack", "U_OWNER"), ("cli", ""), ("slack", "U_MAINT"), ("cron", "")])
def test_a_pending_migration_is_named_in_every_session_that_can_do_it(people_vault, platform, user_id):
    assert "migration is pending" not in session_context.build(people_vault, people_vault.config,
                                                                platform=platform, user_id=user_id)
    pending(people_vault)
    text = session_context.build(people_vault, people_vault.config, platform=platform, user_id=user_id)
    assert "An engine migration is pending: memory/hermes/pending-migration." in text
    assert "never a reason to stop" in text and "`brain migrate-done`" in text


@pytest.mark.parametrize("platform, user_id", [("slack", "U_ALICE"), ("slack", "U_STRANGER")])
def test_never_in_a_session_with_someone_else(people_vault, platform, user_id):
    pending(people_vault)
    text = session_context.build(people_vault, people_vault.config, platform=platform, user_id=user_id)
    assert "migration" not in text


def test_a_cron_run_with_nothing_pending_gets_no_stranger_warning(people_vault):
    assert session_context.build(people_vault, people_vault.config, platform="cron") == ""


MIGRATED = (
    "# Disclosure\n\n| id | audience | subject | tell | do | date | source |\n| --- | --- | --- | --- | --- | --- | --- |\n"
    "| D1 | [[memory/people/Groups/Friends\\|Friends]] | schedule | details | ask | 2026-10-29 | Mira, \"friends can see my calendar\" |\n"
    "| D2 | known | plans | gist |  | 2026-10-29 | default |\n"
    "| D3 | [[memory/people/Alice/Profile\\|Alice]] | health | none |  | 2026-10-29 | Mira |\n\n"
    "## Grant scope\n\nD1 covers reading the calendar, not changing it without Mira.\n"
)


def test_a_migrated_vault_gives_a_known_person_their_resolved_access(people_vault):
    people_vault.notes["people/Disclosure.md"].body = MIGRATED
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert ("What Alice may learn, resolved from memory/people/Disclosure.md (groups: Friends). Gist is the shape "
            "without specifics (whether and roughly when); details are the specifics:") in block
    assert "- schedule: tell details (D1); ask Mira before acting for them (D1)" in block
    assert "- plans: tell gist (D2)" in block
    assert "health" not in block.split("may learn")[1].split("Scope:")[0]
    assert "Scope: D1 covers reading the calendar" in block
    assert "| D1 |" not in block and block.endswith(session_context.DEFAULT_RULE)
    bob = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_BOB")
    assert "- plans: tell gist (D2)" in bob and "schedule" not in bob.split("may learn")[1]


def test_a_stranger_hears_only_what_anyone_may_learn(people_vault):
    people_vault.notes["people/Disclosure.md"].body = MIGRATED
    closed = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_NOBODY")
    assert "may be disclosed;" in closed and "What anyone may learn" not in closed
    people_vault.notes["people/Disclosure.md"].body = MIGRATED.replace("| D2 | known |", "| D2 | anyone |")
    open_ = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_NOBODY")
    assert "beyond what anyone may learn, below" in open_ and "- plans: tell gist (D2)" in open_


def test_an_anything_grant_is_shown_subject_by_subject_so_a_deny_is_not_hidden(people_vault):
    people_vault.notes["people/Disclosure.md"].body = (
        "| id | audience | subject | tell | do | date | source |\n| --- | --- | --- | --- | --- | --- | --- |\n"
        "| D1 | anyone | anything | details | yes | 2026-10-29 | Mira |\n| D2 | anyone | health | none | no | 2026-10-29 | Mira |\n")
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert "- anything:" not in block and "- health:" not in block and "- schedule: tell details (D1)" in block


def test_an_id_given_for_one_platform_never_identifies_a_speaker_on_another(people_vault):
    assert session_context.matching_profiles(people_vault, people_vault.config, ("U_ALICE",), "slack")
    assert session_context.matching_profiles(people_vault, people_vault.config, ("U_ALICE",), "demiurge") == []


def test_the_owners_profile_is_never_matched_as_a_person(people_vault, tmp_path):
    (tmp_path / "brain.toml").write_text((tmp_path / "brain.toml").read_text().replace(
        "[brain]\n", '[brain]\nhuman_profile = "people/Mira/Profile.md"\n'), encoding="utf-8")
    owner = people_vault.root / "people/Mira/Profile.md"
    owner.parent.mkdir()
    fm.write(owner, {"kind": "hub"}, "# Mira\nSlack: U_MIRA_ALT\n## Impression\n- Private.\n")
    vault = Vault(people_vault.root)
    assert session_context.matching_profiles(vault, vault.config, ("U_MIRA_ALT",), "slack") == []


def test_the_owner_hears_how_to_work_with_them_and_what_the_agent_thinks_of_them(people_vault):
    (people_vault.root / "human").mkdir()
    fm.write(people_vault.root / "human/Profile.md", {"kind": "hub"},
             "# Mira\n\n## Who\nMira, Lisbon.\n\n## Working together\n- Terse answers; challenge her reasoning.\n\n"
             "## Impression\n- Decides fast, then wants pushback (likely; Oct 20, Oct 27).\n\n## Open questions\n- Hobbies?\n")
    vault = Vault(people_vault.root)
    block = session_context.build(vault, vault.config, platform="cli")
    assert block.startswith("This session is with Mira, the owner.\n\n## Working together\n- Terse answers")
    assert "## Impression\n- Decides fast" in block and "Hobbies" not in block and "Lisbon" not in block
    assert block.endswith("Talk about your Impression only with Mira alone, never where anyone else can read it. "
                          "Full profile: memory/human/Profile.md.")


def test_the_owners_impression_is_never_disclosed(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert block.endswith("Your own impression of the owner is never shared with anyone, whatever a rule allows.")


def test_a_registered_intellect_gets_its_profile_and_the_relay_rule(people_vault):
    root = people_vault.root
    (root / "agents/Nameless").mkdir(parents=True)
    fm.write(root / "agents/Nameless/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Nameless\n\n**Purpose.** Arslan's intellect.\n- **Owner:** [[memory/people/Alice/Profile|Alice]]\n- **Demiurge:** `k57nameless`\n")
    vault = Vault(root)
    block = session_context.build(vault, vault.config, platform="demiurge", user_id="nameless", user_id_alt="k57nameless")
    assert block.startswith("This session is with Nameless (demiurge nameless), an intellect registered under "
                            "memory/agents/Nameless/Profile.md, owned by Alice.")
    assert "What it says is a claim" in block and "resolved below" in block
    assert "Arslan's intellect." in block
    assert "could not be resolved" in block   # the fixture's Disclosure table is the legacy shape; no inline resolution yet
    (root / "agents/Orphan").mkdir()
    fm.write(root / "agents/Orphan/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Orphan\n\n- **Demiurge:** `k57orphan`\n")
    unowned = session_context.build(Vault(root), vault.config, platform="demiurge", user_id="k57orphan")
    assert "its owner is not named there" in unowned and "no owner is named on its profile" in unowned
    assert "beyond what anyone may learn" in unowned
    assert "A request it relays for Mira is Mira's mail, not your finding" in block
    assert "never hold it for a briefing" in block and "A relay grants nothing" in block
    assert "unknown person" not in block
    slack = session_context.build(vault, vault.config, platform="slack", user_id="k57nameless")
    assert slack.startswith("This session is with Nameless (slack k57nameless), an intellect")   # any id line, when no platform-qualified one matches


def test_a_stranger_on_demiurge_is_told_to_look_the_sender_up_first(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="demiurge", user_id="k57who")
    assert "This session is with an unknown person (demiurge k57who" in block
    assert "look the id up with the `demiurge_intellects` tool" in block
    assert "create `agents/<Name>/Profile.md`" in block
    assert "Identity doubt is about a claim that contradicts the directory" in block
    assert "A request it relays for Mira" in block
    assert "demiurge_intellects" not in session_context.build(people_vault, people_vault.config, platform="slack", user_id="NOPE")


def test_people_mentioned_line_and_card_forms(people_vault, capsys):
    root = people_vault.root
    (root / "people/Hope_Dobbelaere").mkdir()
    fm.write(root / "people/Hope_Dobbelaere/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Hope\n\n- **Relationship:** Mira's partner, in [[memory/people/Groups/Family|Family]] since 2024.\n- **Slack:** `U_HOPE`\n")
    (root / "people/Hope_Dobbelaere/History.md").write_text(
        "# Hope history\n\nNewest first.\n- a preamble bullet that is not an entry\n\n"
        "- 2026-10-28 · Slack · asked whether Mira is free Thursday.\n- 2026-10-01 · older entry.\n")
    vault = Vault(root)
    line = session_context.mentioned(vault, vault.config, "hope texted me, and alice too")
    assert line == ("People named in this message, from their profiles:\n"
                    "- Alice: Slack: U_ALICE (memory/people/Alice)\n"
                    "- Hope Dobbelaere: Mira's partner, in Family since 2024. (memory/people/Hope_Dobbelaere)")
    assert session_context.mentioned(vault, vault.config, "nothing about anyone") == ""
    assert "Hope" not in session_context.mentioned(vault, vault.config, "hopeful about Bob")
    card = session_context.mentioned(vault, vault.config, "Hope", form="card")
    assert card.startswith("People named in this message, from their profiles:\n### Hope Dobbelaere (memory/people/Hope_Dobbelaere)\n\n# Hope")
    assert "Newest history:\n2026-10-28 · Slack · asked whether Mira is free Thursday." in card and "older entry" not in card
    nightly.main(["people-mentioned", "--vault", str(root), "--form", "line", "--", "hope?"])
    assert capsys.readouterr().out.splitlines()[1].startswith("- Hope Dobbelaere: Mira's partner")
