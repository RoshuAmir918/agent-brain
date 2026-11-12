import json
from datetime import datetime
from dataclasses import replace

import pytest

from brain import cli, fm, identity, loops, session_context, tasks
from brain.vault import Vault


MIGRATED = (
    "# Disclosure\n\n| id | audience | subject | tell | do | date | source |\n| --- | --- | --- | --- | --- | --- | --- |\n"
    "| D1 | [[memory/people/Groups/Friends\\|Friends]] | schedule | details | ask | 2026-10-29 | Mira, \"friends can see my calendar\" |\n"
    "| D2 | known | plans | gist |  | 2026-10-29 | default |\n"
    "| D3 | [[memory/people/Alice/Profile\\|Alice]] | health | none |  | 2026-10-29 | Mira |\n\n"
    "## Grant scope\n\nD1 covers reading the calendar, not changing it without Mira.\n"
)


@pytest.fixture
def people_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_HUMAN_NAME", raising=False)
    (tmp_path / "brain.toml").write_text(
        '[brain]\nhuman_name = "Mira"\nowner_user_ids = ["U_OWNER", "12345", "demiurge:k57owner"]\n', encoding="utf-8")
    root = tmp_path / "memory"
    notes = {
        "people/Alice/Profile.md": "# Alice\nSlack: U_ALICE\nAlice likes gardens.\n",
        "people/Bob/Profile.md": "# Bob\nSlack: U_BOB\n",
        "people/Groups/Friends.md": "# Friends\n[[memory/people/Alice/Profile|Alice]]\n",
        "people/Disclosure.md": MIGRATED,
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
def test_owner_context_is_identification_and_the_owner_message_rules(people_vault, identity):
    block = session_context.build(people_vault, people_vault.config, **identity)
    assert block == "This session is with Mira, the owner.\n\n" + session_context.owner_messages(people_vault.config)
    assert block.endswith("answerable in a word, with a short Why.") and "{human_name}" not in block and "Messages to Mira" in block


def test_unknown_context_is_fail_closed(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="NOPE", user_name="Visitor")
    assert block.startswith("This session is with an unknown person (slack NOPE, name shown as Visitor). No profile.")
    assert "nothing about Mira or anyone else may be disclosed" in block
    assert "What you may tell anyone about Mira, and nothing else:\nNothing." in block
    assert "Alice" not in block and "may learn" not in block


def test_ids_resolving_to_more_than_one_profile_are_ambiguous_and_treated_as_unknown(people_vault):
    def build(**ids):
        return session_context.build(people_vault, people_vault.config, platform="slack", **ids)
    assert build(user_id_alt="U_ALICE").startswith("This session is with Alice (slack U_ALICE).")
    assert "Identity is ambiguous" in build(user_id="U_ALICE", user_id_alt="U_BOB")
    people_vault.notes["people/Bob/Profile.md"].body += "Slack: U_ALICE\n"
    block = build(user_id="U_ALICE")
    assert block.startswith("Identity is ambiguous; matching profiles: memory/people/Alice/Profile.md, "
                            "memory/people/Bob/Profile.md. Treat as unknown.\n\n")
    assert "This session is with an unknown person (slack U_ALICE, name shown as not provided)." in block
    assert "nothing about Mira or anyone else may be disclosed" in block
    assert "Alice likes gardens" not in block


@pytest.mark.parametrize("body, user_id, matches", [
    (None, "U_FRONTMATTER_ONLY", False),
    (None, "ALICE", False),
    (None, "U_ALICE-more", False),
    ("prefixU_ALICE", "U_ALICE", False),
    ("U_ALICE-suffix", "U_ALICE", False),
    ("U_ALICE_suffix", "U_ALICE", False),
    ("<@U_ALICE>", "U_ALICE", True),
])
def test_profile_resolution_uses_whole_body_tokens(people_vault, body, user_id, matches):
    person = people_vault.notes["people/Alice/Profile.md"]
    if body is not None:
        person.body = body
    assert identity.matching_profiles(people_vault, people_vault.config, (user_id,)) == ([person] if matches else [])


def test_a_retired_log_stub_is_never_an_identity(people_vault):
    root = people_vault.root
    for folder in ("people/Alicia", "agents/Old"):
        (root / folder).mkdir(parents=True)
    fm.write(root / "people/Alicia/Profile.md", {"kind": "log"}, "# Moved to Alice\nSlack: U_ALICE\n")
    fm.write(root / "agents/Old/Profile.md", {"kind": "log"}, "# Retired\nSlack: U_OLDBOT\n")
    vault = Vault(root)
    assert [note.rel for note in identity.matching_profiles(vault, vault.config, ("U_ALICE",), "slack")] == [
        "people/Alice/Profile.md"]
    assert identity.matching_agents(vault, vault.config, ("U_OLDBOT",), "slack") == []


def test_profile_scan_uses_configured_people_directory_and_direct_children(people_vault):
    people_vault.config = config = replace(people_vault.config, people_dir="contacts/people")
    person = people_vault.notes.pop("people/Alice/Profile.md")
    person.rel = "contacts/people/Alice/Profile.md"
    people_vault.notes[person.rel] = person
    assert identity.matching_profiles(people_vault, config, ("U_ALICE",)) == [person]
    people_vault.config = config = replace(config, people_dir="contacts")
    assert identity.matching_profiles(people_vault, config, ("U_ALICE",)) == []


def test_profile_truncation_keeps_250_words_and_a_full_path_pointer(people_vault):
    person = people_vault.notes["people/Alice/Profile.md"]
    person.body = "U_ALICE " + " ".join(f"word{number}" for number in range(300))
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    profile = block.split("\n\n")[1]
    assert len(profile.split()) == 250
    assert profile.endswith("word248…") and "word249" not in block
    assert "Full profile: memory/people/Alice/Profile.md; dated history beside it in History.md" in block


def test_missing_disclosure_file_is_explicit_and_defaults_to_no_disclosure(people_vault):
    del people_vault.notes["people/Disclosure.md"]
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert "Disclosure.md is missing: memory/people/Disclosure.md." in block
    assert block.split("\n\nWhat you may tell", 1)[0].endswith(session_context.default_rule(people_vault.config))


@pytest.mark.parametrize("metadata", [{}, {"user_name": "Visitor"}, {"chat_id": "C_OWNER"}, {"platform": "cron"}])
def test_absent_platform_and_ids_produce_no_context(people_vault, metadata):
    assert session_context.build(people_vault, people_vault.config, **metadata) == ""


def test_cli_json_carries_the_block_and_whether_the_speaker_is_the_owner(people_vault, capsys):
    root = str(people_vault.root)
    cli.main(["session-context", "--vault", root, "--platform", "slack", "--user-id", "U_OWNER", "--json"])
    owner = json.loads(capsys.readouterr().out)
    assert owner == {"block": session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_OWNER"),
                     "owner": True, "member": False, "answering": False}
    cli.main(["session-context", "--vault", root, "--platform", "slack", "--user-id", "U_ALICE", "--json"])
    alice = json.loads(capsys.readouterr().out)
    assert alice["block"] and not alice["owner"] and not alice["member"] and alice["answering"]
    cli.main(["session-context", "--vault", root, "--json"])
    assert json.loads(capsys.readouterr().out) == {"block": "", "owner": False, "member": False, "answering": False}


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
    assert identity.matching_profiles(
        demiurge_vault, demiurge_vault.config, ("renamed", "k57orpheus"), "demiurge") == [
        demiurge_vault.notes["people/Orpheus/Profile.md"]]


def test_an_id_counts_only_on_its_own_platform_and_whole(demiurge_vault):
    assert identity.matching_profiles(demiurge_vault, demiurge_vault.config, ("U_ALICE",), "slack")
    assert identity.matching_profiles(demiurge_vault, demiurge_vault.config, ("U_ALICE",), "demiurge") == []
    body = "- Demiurge: `k57orpheus2`\n- Slack: k57orpheus\n"
    assert identity.has_qualified_id(body, "demiurge", ("k57orpheus",)) is False
    assert identity.has_qualified_id(body, "slack", ("k57orpheus",)) is True


def test_email_local_part_is_not_a_handle(people_vault):
    people_vault.notes["people/Alice/Profile.md"].body = "# Alice\nEmail: orpheus@gmail.com\n"
    assert identity.matching_profiles(people_vault, people_vault.config, ("orpheus",), "demiurge") == []
    people_vault.notes["people/Alice/Profile.md"].body += "Handle @orpheus\n"
    assert identity.matching_profiles(people_vault, people_vault.config, ("orpheus",), "demiurge") == [
        people_vault.notes["people/Alice/Profile.md"]]


def pending(vault):
    path = vault.root / vault.config.runtime_dir / "pending-migration.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Engine migration\n", encoding="utf-8")


@pytest.mark.parametrize("platform, user_id", [("slack", "U_OWNER"), ("cli", ""), ("cron", "")])
def test_a_pending_migration_is_named_in_every_session_that_can_do_it(people_vault, platform, user_id):
    assert "migration is pending" not in session_context.build(people_vault, people_vault.config,
                                                                platform=platform, user_id=user_id)
    pending(people_vault)
    text = session_context.build(people_vault, people_vault.config, platform=platform, user_id=user_id)
    assert "An engine migration is pending: memory/Runtime/pending-migration." in text
    assert "never a reason to stop" in text and "`brain migrate-done`" in text


@pytest.mark.parametrize("platform, user_id", [("slack", "U_ALICE"), ("slack", "U_STRANGER")])
def test_never_in_a_session_with_someone_else(people_vault, platform, user_id):
    pending(people_vault)
    text = session_context.build(people_vault, people_vault.config, platform=platform, user_id=user_id)
    assert "migration" not in text


def test_a_migrated_vault_gives_a_known_person_their_resolved_access(people_vault):
    block = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_ALICE")
    assert ("What Alice may learn, resolved from memory/people/Disclosure.md (groups: Friends). Gist is the shape "
            "without specifics (whether and roughly when); details are the specifics:") in block
    assert "- schedule: tell details (D1); ask Mira before acting for them (D1)" in block
    assert "- plans: tell gist (D2)" in block
    assert "- health: nothing (D3), Mira's own decision for them, settled." in block and "health: tell" not in block
    assert "Scope: D1 covers reading the calendar" in block
    assert "| D1 |" not in block and block.split("\n\nWhat you may tell", 1)[0].endswith(session_context.default_rule(people_vault.config))
    bob = session_context.build(people_vault, people_vault.config, platform="slack", user_id="U_BOB")
    assert "- plans: tell gist (D2)" in bob and "schedule" not in bob.split("may learn")[1]


def test_a_stranger_hears_only_what_anyone_may_learn(people_vault):
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
    assert "- anything:" not in block and "- schedule: tell details (D1)" in block
    assert "- health: nothing (D2), Mira's own decision for them, settled." in block


def test_the_owners_profile_is_never_matched_as_a_person(people_vault, tmp_path):
    (tmp_path / "brain.toml").write_text((tmp_path / "brain.toml").read_text().replace(
        "[brain]\n", '[brain]\nhuman_profile = "people/Mira/Profile.md"\n'), encoding="utf-8")
    owner = people_vault.root / "people/Mira/Profile.md"
    owner.parent.mkdir()
    fm.write(owner, {"kind": "hub"}, "# Mira\nSlack: U_MIRA_ALT\n## Impression\n- Private.\n")
    vault = Vault(people_vault.root)
    assert identity.matching_profiles(vault, vault.config, ("U_MIRA_ALT",), "slack") == []


def test_the_owner_hears_how_to_work_with_them_and_what_the_agent_thinks_of_them(people_vault):
    (people_vault.root / "human").mkdir(exist_ok=True)
    fm.write(people_vault.root / "human/Profile.md", {"kind": "hub"},
             "# Mira\n\n## Who\nMira, Lisbon.\n\n## Working together\n- Terse answers; challenge her reasoning.\n\n"
             "## Impression\n- Decides fast, then wants pushback (likely; Oct 20, Oct 27).\n\n## Open questions\n- Hobbies?\n")
    vault = Vault(people_vault.root)
    block = session_context.build(vault, vault.config, platform="cli")
    assert block.startswith("This session is with Mira, the owner.\n\n## Working together\n- Terse answers")
    assert "## Impression\n- Decides fast" in block and "Hobbies" not in block and "Lisbon" not in block
    assert block.endswith("Talk about your Impression only with Mira alone, never where anyone else can read it. "
                          "Full profile: memory/human/Profile.md.\n\n" + session_context.owner_messages(vault.config))


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
    assert "What Alice may learn, resolved from" in block and "could not be resolved" not in block
    assert "- schedule: tell details (D1); ask" in block
    (root / "agents/Orphan").mkdir()
    fm.write(root / "agents/Orphan/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Orphan\n\n- **Demiurge:** `k57orphan`\n")
    unowned = session_context.build(Vault(root), vault.config, platform="demiurge", user_id="k57orphan")
    assert "its owner is not named there" in unowned and "no owner is named on its profile" in unowned
    assert "beyond what anyone may learn" in unowned
    assert "A question it relays for its owner you answer now" in block and "goes to Mira now, from this session" in block
    assert "hold it for a briefing" in block and "A relay grants nothing" in block
    assert "unknown person" not in block
    slack = session_context.build(vault, vault.config, platform="slack", user_id="k57nameless")
    assert "unknown person" in slack and "an intellect" not in slack   # an id is only its own platform's


def mentioned(vault, text, form="line"):
    return session_context._people_block(form, list(session_context._named(vault, vault.config, text, form).values()))


def test_people_named_line_and_card_forms(people_vault):
    root = people_vault.root
    (root / "people/Hope_Dobbelaere").mkdir()
    fm.write(root / "people/Hope_Dobbelaere/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Hope\n\n- **Relationship:** Mira's partner, in [[memory/people/Groups/Family|Family]] since 2024.\n- **Slack:** `U_HOPE`\n")
    (root / "people/Hope_Dobbelaere/History.md").write_text(
        "# Hope history\n\nNewest first.\n- a preamble bullet that is not an entry\n\n"
        "- 2026-10-28 · Slack · asked whether Mira is free Thursday.\n- 2026-10-01 · older entry.\n")
    vault = Vault(root)
    line = mentioned(vault, "hope texted me, and alice too")
    assert line == ("People named in this message, from their profiles:\n"
                    "- Alice: Slack: U_ALICE (memory/people/Alice)\n"
                    "- Hope Dobbelaere: Mira's partner, in Family since 2024. (memory/people/Hope_Dobbelaere)")
    assert mentioned(vault, "nothing about anyone") == ""
    assert "Hope" not in mentioned(vault, "hopeful about Bob")
    card = mentioned(vault, "Hope", form="card")
    assert card.startswith("People named in this message, from their profiles:\n### Hope Dobbelaere (memory/people/Hope_Dobbelaere)\n\n# Hope")
    assert "Newest history:\n2026-10-28 · Slack · asked whether Mira is free Thursday." in card and "older entry" not in card


def test_prefetch_applies_the_context_settings_and_names_each_person_once(people_vault, capsys, monkeypatch):
    root = people_vault.root
    fm.write(root / "Garden plans.md", {"tier": "warm", "index_line": "Beds for spring."}, "# Garden plans\n")
    monkeypatch.setenv("BRAIN_PEOPLE", "line")
    monkeypatch.setenv("BRAIN_PREFETCH_LIMIT", "1")

    def prefetch(*flags):
        cli.main(["prefetch", "--vault", str(root), "--json", *flags, "--", "what did Alice say about the garden plans?"])
        return json.loads(capsys.readouterr().out)

    found = prefetch("--owner")
    assert found["people"] == ["Alice"]
    people, recall = found["text"].split("\n\n")
    assert people == "People named in this message, from their profiles:\n- Alice: Slack: U_ALICE (memory/people/Alice)"
    assert recall.splitlines() == [session_context.RECALL_HEADER, "- memory/Garden plans.md (warm): title/path: garden, plans"]
    assert prefetch("--owner", "--seen", "Alice") == {"text": recall, "people": ["Alice"]}
    assert prefetch() == {"text": recall, "people": []}
    monkeypatch.setenv("BRAIN_PREFETCH_FORM", "full")
    monkeypatch.setenv("BRAIN_PREFETCH_CHARS", "120")
    full = prefetch()["text"]
    assert full.startswith(session_context.FULL_HEADER + "\n\n## memory/Garden plans.md (warm)\n\n# Garden plans") and len(full) <= 120
    monkeypatch.setenv("BRAIN_PREFETCH", "0")
    assert prefetch("--owner", "--seen", "Alice")["text"] == ""


def _own_work_vault(people_vault):
    root = people_vault.root
    disclosure = root / "people/Disclosure.md"
    disclosure.write_text(MIGRATED.replace("| D3 |", "| D4 | [[memory/people/Alice/Profile\\|Alice]] | setup | details |  | 2026-10-29 | Mira |\n| D3 |"),
                          encoding="utf-8")
    (root / "agent/Experiments").mkdir(parents=True)
    fm.write(root / "Weather screen.md", {"kind": "hub", "skill": "agent-weather", "work": "weather"},
             "# Weather screen\n\nSee [[agent/Experiments/Weather runs]], [[Weather diary]] and [[agent/Drafts/Gust memo]].\n\n"
             "## Objective\nMira wants gust checks for Dallas.\n\n## Strategy\nUse stadium-point forecasts over the game window.\n\n"
             "## Weaknesses\nBlank gust fields are unknown, not calm.\n## Permission\nMira's Dallas gust budget is private.\n\n"
             "## Owner background\n\n### Strategy\nMira's private Dallas gust plan.\n")
    runs = "\n\n".join(f"Week {week}: quiet screen, nothing flagged." for week in range(1, 200))
    fm.write(root / "agent/Experiments/Weather runs.md", {"tier": "warm"},
             f"# Weather runs\n\n## Run records\n\n{runs}\n\n### October 8\n\nThe Dallas gust fields were blank, so the screen stayed incomplete.\n")
    fm.write(root / "Weather diary.md", {"tier": "hot"}, "# Weather diary\n\nMira hates Dallas gust days.\n")
    (root / "agent/Drafts").mkdir(parents=True)
    fm.write(root / "agent/Drafts/Gust memo.md", {}, "# Gust memo\n\nFor Mira: the Dallas gust worry is overblown.\n")
    return Vault(root)


def test_an_answering_session_recalls_its_own_work_only_for_whom_the_owner_lets_learn_it(people_vault):
    vault, config = _own_work_vault(people_vault), people_vault.config
    found = session_context.prefetch(vault, config, "how do you screen weather and gust forecasts?", owner=False, seen=set(),
                                     answering=True, platform="slack", user_ids=("U_ALICE",))
    text = found["text"]
    assert text.startswith(session_context.WORK_HEADER) and found["people"] == []
    assert "Blank gust fields are unknown, not calm." in text and "agent/Experiments/Weather runs.md" in text
    assert "Mira wants gust checks" not in text and "Mira hates" not in text and "overblown" not in text
    for stranger in (("slack", ("U_BOB",)), ("demiurge", ("k57nobody",)), ("", ())):
        assert session_context.prefetch(vault, config, "weather gust forecasts", owner=False, seen=set(), answering=True,
                                         platform=stranger[0], user_ids=stranger[1])["text"] == ""
    assert session_context.prefetch(vault, config, "weather gust", owner=False, seen=set())["text"]


def test_an_answering_session_searches_its_own_work_down_to_the_passage(people_vault):
    vault, config = _own_work_vault(people_vault), people_vault.config
    found = session_context.search_own_work(vault, config, "Dallas gust", platform="slack", user_ids=("U_ALICE",))
    assert found.startswith(session_context.OWN_WORK_RULE)
    assert "## agent/Experiments/Weather runs.md > Run records > October 8\n\nThe Dallas gust fields were blank" in found
    assert "Blank gust fields are unknown, not calm." in found
    assert "Mira" not in found.removeprefix(session_context.OWN_WORK_RULE) and "quiet screen" not in found and "overblown" not in found
    assert "private" not in found.removeprefix(session_context.OWN_WORK_RULE)
    assert session_context.search_own_work(vault, config, "sourdough", platform="slack",
                                           user_ids=("U_ALICE",)).startswith("Nothing in your own work matches")
    assert "has not allowed" in session_context.search_own_work(vault, config, "Dallas gust", platform="slack", user_ids=("U_BOB",))


def test_an_intellect_sees_the_open_work_that_names_it_with_its_hub(people_vault):
    root = people_vault.root
    for folder in ("agents/Nameless", "agent"):
        (root / folder).mkdir(parents=True)
    fm.write(root / "agents/Nameless/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Nameless\n\n- **Owner:** [[memory/people/Alice/Profile|Alice]]\n- **Demiurge:** `k57nameless`\n")
    fm.write(root / "agent/Tasks.md", {}, "# Tasks\n\n"
             "### T-001 — Move the site\n- **Next:** Nameless reads the zone.\n- **Hub:** [[Site move]]\n\n"
             "### T-002 — Plan the garden\n- **Next:** Pick seeds.\n")
    fm.write(root / "Site move.md", {"kind": "hub"}, "# Site move\n\nAlice accepted the account invitation.\n")
    vault = Vault(root)
    block = session_context.build(vault, vault.config, platform="demiurge", user_id="k57nameless")
    assert "Open work with Nameless" in block and "Alice accepted the account invitation." in block
    assert "Plan the garden" not in block
    vault.notes["people/Disclosure.md"].body = MIGRATED.replace("| D2 | known | plans | gist |", "| D2 | known | plans | none |")
    assert "Open work with Nameless" not in session_context.build(vault, vault.config, platform="demiurge", user_id="k57nameless")


def test_a_routine_run_gets_the_owner_card(people_vault):
    (people_vault.root / "human").mkdir(exist_ok=True)
    fm.write(people_vault.root / "human/Profile.md", {"kind": "profile"},
             "# Mira\n\n## Working together\n\nJust tell me whether I need to do anything.\n")
    block = session_context.build(Vault(people_vault.root), people_vault.config, platform="cron")
    assert "Just tell me whether I need to do anything." in block


def test_an_intellect_session_lists_own_tasks_blocked_on_it_whatever_its_owner_may_learn(people_vault):
    root = people_vault.root
    for folder in ("agents/Orphan", "agent"):
        (root / folder).mkdir(parents=True)
    fm.write(root / "agents/Orphan/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Orphan\n\n- **Owner:** [[memory/people/Alice/Profile|Alice]]\n- **Demiurge:** `k57orphan`\n")
    fm.write(root / "agent/Tasks.md", {}, "# Tasks\n\n"
             "### T-001 — Go live\n<!-- brain: state=blocked -->\n- **Blocker:** Orphan to say how the key is supplied.\n\n"
             "### T-002 — Plan the garden\n<!-- brain: state=blocked -->\n- **Blocker:** The nursery is closed.\n\n"
             "### T-003 — Ask about seeds\n<!-- brain: state=in_progress -->\n- **Next:** Wait on the answer.\n")
    vault = Vault(root)
    loops.add(vault.config, datetime(2026, 11, 11, 12, 0), "waiting", "Which seeds grow here?", "agent/Tasks",
              "Orphan", task="T-003")
    vault.notes["people/Disclosure.md"].body = MIGRATED.replace("| D2 | known | plans | gist |", "| D2 | known | plans | none |")
    block = session_context.build(vault, vault.config, platform="demiurge", user_id="k57orphan")
    assert "Your tasks waiting on Orphan:" in block
    assert "T-001 — Go live (blocker: Orphan to say how the key is supplied.)" in block
    assert "T-003 — Ask about seeds" in block and "Plan the garden" not in block
    assert "`task_answer`" in block

    args = ["task-answer", "--vault", str(root), "--platform", "demiurge", "--user-id", "k57orphan"]
    with pytest.raises(SystemExit, match="not one of your tasks waiting on Orphan"):
        cli.main([*args, "T-002", "--answer", "Open on Monday"])
    with pytest.raises(SystemExit, match="not one"):
        cli.main([*args[:3], "--platform", "demiurge", "--user-id", "k57stranger", "T-001", "--answer", "Keychain"])
    cli.main([*args, "T-001", "--answer", "The key goes in the keychain", "--next", "Read it from the keychain and deploy"])
    task = tasks.find(vault.config, "T-001")
    assert task.task_state == "in_progress" and fm.field(task.body, "Blocker") is None
    assert fm.field(task.body, "Next") == "Read it from the keychain and deploy"
    assert fm.field(task.body, "Latest evidence").endswith("Orphan: The key goes in the keychain")
    cli.main([*args, "T-003", "--answer", "Tomatoes"])
    assert not loops.open_loops(vault.config) and loops.closed_recent(vault.config)[0].because == "Orphan answered"


def test_standing_directions_about_a_person_hold_with_them_and_their_intellect(people_vault):
    root = people_vault.root
    fm.write(root / "people/Alice/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Alice\nSlack: U_ALICE\n" + "Alice likes gardens. " * 300
             + "\n\n## Working together\n- 2026-10-31 · Never relay anything Alice sends to Mira.\n")
    (root / "agents/Nameless").mkdir(parents=True)
    fm.write(root / "agents/Nameless/Profile.md", {"kind": "profile", "tier": "warm"},
             "# Nameless\n\n- **Owner:** [[memory/people/Alice/Profile|Alice]]\n- **Demiurge:** `k57nameless`\n")
    vault = Vault(root)
    rule = "- 2026-10-31 · Never relay anything Alice sends to Mira."
    with_intellect = session_context.build(vault, vault.config, platform="demiurge", user_id="k57nameless")
    assert "How Mira wants things handled with Alice, from memory/people/Alice/Profile.md" in with_intellect
    assert rule in with_intellect
    with_her = session_context.build(vault, vault.config, platform="slack", user_id="U_ALICE")
    assert with_her.count(rule) == 1   # her profile was cut before the section, so it is added once
    bob = session_context.build(vault, vault.config, platform="slack", user_id="U_BOB")
    assert rule not in bob and "wants things handled" not in bob


def test_the_owner_card_keeps_the_newest_working_together_lines(people_vault):
    root = people_vault.root
    (root / "human").mkdir(exist_ok=True)
    old = "".join(f"- 2026-10-{day:02d} · an older preference about formatting number {day} kept for a while\n" for day in range(1, 31))
    fm.write(root / "human/Profile.md", {"kind": "profile"}, "# Mira\n\n## Working together\n" + old
             + "- 2026-11-11 · No em dashes, ever.\n")
    vault = Vault(root)
    card = session_context.owner_card(vault, vault.config)
    assert "- 2026-11-11 · No em dashes, ever." in card and "number 1 kept" not in card
    assert card.startswith("## Working together\n")
