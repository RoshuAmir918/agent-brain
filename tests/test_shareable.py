"""Audience-only snapshots keep private content and personal grants out."""
import json
from datetime import date

import pytest

from brain import access, fm, session_context, state
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot


TODAY = date(2026, 10, 29)
KEVIN = "people/Kevin/Profile.md"
FRIENDS = "people/Groups/Close friends.md"
WORK = "people/Groups/Work.md"
EMPTY = "people/Groups/Empty.md"
HEADER = "| id | audience | subject | tell | do |\n| --- | --- | --- | --- | --- |\n"


def note(root, rel, body="NOTE_BODY_SECRET", **data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, body + "\n")


def rules(config, *rows):
    note(config.vault_path, f"{config.people_dir}/Disclosure.md", HEADER + "\n".join(rows)
         + "\n\n## Subjects\n- pets: the owner's animals\n")


@pytest.fixture
def sharing(vault):
    config = Config.load(vault)
    note(vault, KEVIN, "# PROFILE_TITLE_SECRET\n- **Demiurge:** `k57kevin` (@PROFILE_HANDLE_SECRET)\n"
         "Slack: U_KEVIN\nTelegram: -12345\n**Discord**: `98765`\nSlack: U_KEVIN\n"
         "Email: PROFILE_EMAIL_SECRET@example.org\nSlack: PROFILE_EMAIL_SECRET@example.org\n"
         "Demiurge: PROFILE_EMAIL_SECRET.name@example.org\n- **Name:** PROFILE_NAME_SECRET\n"
         "Mentioned Slack: U_NOT_AN_ID\nPROFILE_BODY_SECRET", name="PROFILE_FRONTMATTER_SECRET")
    note(vault, "people/Other/Profile.md", "Slack: U_NONMEMBER_SECRET")
    note(vault, FRIENDS, f"# GROUP_TITLE_SECRET\n## Members\n[[{KEVIN}]]\n"
         "## History\n[[people/Other/Profile]]\nGROUP_BODY_SECRET", kind="group")
    note(vault, WORK, f"## Members\n[[{KEVIN}]]", kind="group")
    note(vault, EMPTY, "## Members\n", kind="group")
    note(vault, "Plans/Trip.md")
    note(vault, "Work/Calendar.md", subject="schedule", skill="brain-calendar", records="appointments")
    note(vault, "Work/Employer.md", subject="work", skill="brain-employer", records="job")
    note(vault, "Health/Clinic.md", subject="health", skill="brain-clinic", records="care")
    note(vault, "Pets.md", subject="pets")
    note(vault, "Drafts/OUTPUT_TITLE_SECRET.md", kind="draft")
    note(vault, "agent/Receipts/2026-10.md",
         "2026-10-29 12:00 | CALENDAR | done | Calendar receipt. | NEXT_SECRET\n"
         "2026-10-29 11:00 | appointments | done | Records alias receipt. | NEXT_SECRET\n"
         "2026-10-29 10:00 | unmatched | done | Plan receipt. | NEXT_SECRET\n"
         "2026-10-29 09:00 | job | done | WORK_ACCOUNT_SECRET | NEXT_SECRET\n"
         "2026-10-29 08:00 | care | done | HEALTH_ACCOUNT_SECRET | NEXT_SECRET\n"
         "2026-09-29 | calendar | done | OLD_ACCOUNT_SECRET | NEXT_SECRET\n"
         "2026-10-30 | calendar | done | FUTURE_ACCOUNT_SECRET | NEXT_SECRET", kind="log")
    rules(config,
          f"| D-PERSON | [[{KEVIN}]] | anything | details | yes |",
          f"| D-FRIENDS | [[{FRIENDS}]] | plans | gist | |",
          f"| D-CALENDAR | [[{FRIENDS}]] | schedule | details | |",
          f"| D-WORK | [[{FRIENDS}]] | work | none | |",
          f"| D-FINANCES | [[{WORK}]] | finances | details | |",
          "| D-KNOWN | known | plans | details | |",
          "| D-SCHEDULE | known | schedule | gist | |",
          "| D-DO | known | setup | | yes |",
          "| D-ANYONE | anyone | work | gist | |",
          "| D-OPINIONS | anyone | opinions | gist | |")
    return config


@pytest.mark.parametrize("rel,subject,expected", [
    ("Plans/Default.md", None, "plans"), ("people/Hub.md", None, "people"),
    ("Plans/Schedule.md", "schedule", "schedule"), ("people/Pet.md", "pets", "pets"),
    ("Plans/Unknown.md", "unknown", "plans"), ("people/Unknown.md", "unknown", "people"),
    ("Plans/Bad.md", ["schedule"], "plans"), ("people-other/Hub.md", None, "plans"),
])
def test_hub_subject_frontmatter_and_directory_defaults(sharing, rel, subject, expected):
    note(sharing.vault_path, rel, subject=subject)
    loaded = Vault(sharing.vault_path)
    assert access.hub_subject(loaded, sharing, rel) == expected


def test_audience_levels_do_not_use_person_rules_or_other_memberships(sharing):
    shared = state.build(sharing, TODAY)["shareable"]
    assert list(shared) == ["group:Close friends", "group:Empty", "group:Work", "known", "anyone"]
    assert shared["group:Close friends"]["tell"] == {"schedule": "details", "plans": "gist", "opinions": "gist"}
    assert shared["known"]["tell"] == {"schedule": "gist", "plans": "details", "work": "gist", "opinions": "gist"}
    assert shared["anyone"]["tell"] == {"work": "gist", "opinions": "gist"}
    assert shared["group:Empty"]["tell"] == shared["known"]["tell"]
    assert shared["group:Work"]["tell"] == shared["known"]["tell"] | {"finances": "details"}
    loaded = Vault(sharing.vault_path)
    person = access.resolve(loaded, sharing, KEVIN)
    assert person.groups == [FRIENDS, WORK]
    assert all(value.tell == "details" for value in person.subjects.values())
    audience = access.resolve(loaded, sharing, KEVIN, audience=access.Audience("group", FRIENDS))
    assert audience.person is None and audience.groups == [FRIENDS]
    assert audience.subjects["health"].tell == audience.subjects["finances"].tell == "none"


def test_hubs_only_expose_titles_and_subjects_at_gist_or_details(sharing):
    result = state.build(sharing, TODAY)
    shared = result["shareable"]
    loaded = Vault(sharing.vault_path)
    titles = [{"title": hub["title"], "subject": access.hub_subject(loaded, sharing, hub["source"])}
              for hub in result["hubs"]]
    assert {hub["title"] for hub in shared["group:Close friends"]["hubs"]} == {"Activity Log", "Trip", "Calendar"}
    assert shared["anyone"]["hubs"] == [{"title": "Employer", "subject": "work"}]
    for entry in shared.values():
        assert entry["hubs"] == [hub for hub in titles if entry["tell"].get(hub["subject"]) in ("gist", "details")]
        assert all(set(hub) == {"title", "subject"} for hub in entry["hubs"])


def test_receipts_require_details_and_match_both_skill_and_records(sharing):
    result = state.build(sharing, TODAY)
    shared = result["shareable"]
    assert shared["group:Close friends"]["receipts"] == [
        {"at": "2026-10-29T12:00", "routine": "CALENDAR", "account": "Calendar receipt."},
        {"at": "2026-10-29T11:00", "routine": "appointments", "account": "Records alias receipt."}]
    assert shared["known"]["receipts"] == [
        {"at": "2026-10-29T10:00", "routine": "unmatched", "account": "Plan receipt."}]
    assert shared["anyone"]["receipts"] == []
    for entry in shared.values():
        assert all(set(receipt) == {"at", "routine", "account"} for receipt in entry["receipts"])
        assert all(receipt.items() <= row.items() for receipt in entry["receipts"] for row in result["ledger"]
                   if receipt["at"] == row["at"])


def test_group_members_expose_only_profile_paths_and_explicit_platform_ids(sharing):
    shared = state.build(sharing, TODAY)["shareable"]
    member = {"profile": KEVIN, "ids": ["demiurge:k57kevin", "slack:U_KEVIN", "telegram:-12345", "discord:98765"]}
    assert shared["group:Close friends"]["members"] == shared["group:Work"]["members"] == [member]
    assert shared["group:Empty"]["members"] == []
    assert "members" not in shared["known"] and "members" not in shared["anyone"]
    assert "PROFILE_" not in json.dumps(shared) and "NONMEMBER_SECRET" not in json.dumps(shared)


@pytest.mark.parametrize("line,expected", [
    ("- **Demiurge:** `k57kevin` (@kevin; confirmed today)", ["demiurge:k57kevin"]),
    ("**SLACK**: U_KEVIN", ["slack:U_KEVIN"]),
    ("  * **Telegram:** `-12345`", ["telegram:-12345"]),
    ("+ Discord: 98765", ["discord:98765"]),
    ("Email: kevin@example.org", []), ("Slack: kevin@example.org", []),
    ("Slack: kevin+tag@example.org", []), ("Telegram: kevin/tag@example.org", []),
    ("Demiurge: `kevin.name@example.org`", []), ("Discord: @kevin", []),
    ("Kevin mentioned Slack: U_KEVIN", []), ("Name: Kevin", []),
])
def test_member_id_lines_do_not_export_email_addresses_or_prose(line, expected):
    assert session_context.profile_ids(line) == expected


@pytest.mark.parametrize("missing_disclosure", [False, True])
def test_empty_tell_maps_keep_empty_content_and_group_members(sharing, missing_disclosure):
    rules(sharing, f"| D-PERSON | [[{KEVIN}]] | anything | details | yes |",
          f"| D-DO | [[{FRIENDS}]] | anything | | yes |")
    if missing_disclosure:
        (sharing.vault_path / "people/Disclosure.md").unlink()
    for key, entry in state.build(sharing, TODAY)["shareable"].items():
        assert {field: entry[field] for field in ("tell", "hubs", "receipts")} == {"tell": {}, "hubs": [], "receipts": []}
        assert ("members" in entry) == key.startswith("group:")


def test_audience_wildcards_subject_overrides_and_blank_dimensions(sharing):
    rules(sharing, f"| D-PERSON | [[{KEVIN}]] | anything | details | yes |",
          f"| D-GROUP | [[{FRIENDS}]] | anything | gist | |",
          f"| D-NONE | [[{FRIENDS}]] | work | none | |",
          f"| D-BLANK | [[{FRIENDS}]] | schedule | | yes |",
          "| D-KNOWN | known | anything | details | |",
          "| D-ANYONE | anyone | pets | gist | |")
    shared = state.build(sharing, TODAY)["shareable"]
    expected = {subject: "gist" for subject in access.SUBJECTS | {"pets": "animals"} if subject != "work"}
    assert shared["group:Close friends"]["tell"] == expected
    assert all(level == "details" for level in shared["known"]["tell"].values())
    assert shared["anyone"]["tell"] == {"pets": "gist"}


@pytest.mark.parametrize("folder,data", [
    ("sources", {}), ("human", {}), ("people", {}), ("agents", {}), ("hermes", {}),
    ("Public", {"tier": "private"}), ("Public", {"kind": "conversation"}),
])
def test_shareable_respects_public_boundary_even_with_all_subjects_granted(sharing, folder, data):
    rules(sharing, "| D-ALL | anyone | anything | details | |")
    note(sharing.vault_path, f"{folder}/PRIVATE_HUB_SECRET.md", "PRIVATE_BODY_SECRET",
         **{"skill": "brain-private", "records": "private", **data})
    note(sharing.vault_path, f"{folder}/PRIVATE_LOG_SECRET.md",
         "2026-10-29 | private | done | PRIVATE_ACCOUNT_SECRET | PRIVATE_NEXT_SECRET",
         **{"kind": "log", **data})
    serialized = json.dumps(state.build(sharing, TODAY)["shareable"])
    for secret in ("PROFILE_", "NOTE_BODY_SECRET", "GROUP_", "OUTPUT_", "PRIVATE_", "NEXT_SECRET", "D-ALL"):
        assert secret not in serialized


def test_hub_and_receipt_caps_apply_after_filtering_and_preserve_snapshot_order(sharing):
    rules(sharing, "| D-ALL | anyone | plans | details | |")
    for index in range(40):
        note(sharing.vault_path, f"Many/{index:02d} Allowed.md")
        note(sharing.vault_path, f"Many/{index:02d} Hidden.md", subject="health")
    lines = [f"2026-10-29 15:{index:02d} | unmatched | done | Receipt {index}. | Wait." for index in range(25)]
    lines.extend(f"2026-10-29 16:{index:02d} | care | done | HIDDEN_ACCOUNT_SECRET | Wait." for index in range(25))
    note(sharing.vault_path, "agent/Receipts/2026-10.md", "\n".join(lines), kind="log")
    result = state.build(sharing, TODAY)
    entry = result["shareable"]["anyone"]
    assert len(entry["hubs"]) == 30 and len(entry["receipts"]) == 20
    assert entry["hubs"] == [{"title": hub["title"], "subject": "plans"} for hub in result["hubs"]
                             if hub["title"].endswith("Allowed")][:30]
    assert entry["receipts"] == [{key: row[key] for key in ("at", "routine", "account")} for row in result["ledger"]
                                 if row["routine"] == "unmatched"][:20]


def test_receipt_subject_uses_hub_source_not_an_ambiguous_title(sharing):
    note(sharing.vault_path, "A/Same.md", subject="work", skill="brain-first", records="first")
    note(sharing.vault_path, "B/Same.md", subject="health", skill="brain-second", records="second")
    note(sharing.vault_path, "agent/Receipts/2026-10.md",
         "2026-10-29 | first | done | Work receipt. | Wait.\n"
         "2026-10-29 | second | done | HEALTH_ACCOUNT_SECRET | Wait.", kind="log")
    rules(sharing, "| D-WORK | anyone | work | details | |")
    assert state.build(sharing, TODAY)["shareable"]["anyone"]["receipts"] == [
        {"at": "2026-10-29", "routine": "first", "account": "Work receipt."}]


def test_configured_people_directory_and_skill_prefix(sharing):
    root = sharing.vault_path
    (root / "Contacts").mkdir()
    (root / "people").rename(root / "Contacts/People")
    for path in (root / "Contacts/People").rglob("*.md"):
        path.write_text(path.read_text().replace("people/", "Contacts/People/"))
    (root.parent / "brain.toml").write_text('[brain]\nvault = "memory"\npeople_dir = "Contacts/People"\nskill_prefix = "own-"\n')
    note(root, "Work/Calendar.md", subject="schedule", skill="own-calendar", records="appointments")
    config = Config.load(root)
    shared = state.build(config, TODAY)["shareable"]
    assert shared["group:Close friends"]["members"][0]["profile"] == "Contacts/People/Kevin/Profile.md"
    assert shared["group:Close friends"]["receipts"][0]["routine"] == "CALENDAR"
    assert access.hub_subject(Vault(root), config, "Contacts/People/Default.md") == "people"


def test_shareable_is_deterministic_and_does_not_write(sharing):
    before = snapshot(sharing.vault_path.parent)
    metadata = {path: path.stat().st_mtime_ns for path in sharing.vault_path.rglob("*")}
    first = json.dumps(state.build(sharing, TODAY), ensure_ascii=False)
    second = json.dumps(state.build(sharing, TODAY), ensure_ascii=False)
    assert first == second
    assert snapshot(sharing.vault_path.parent) == before
    assert {path: path.stat().st_mtime_ns for path in sharing.vault_path.rglob("*")} == metadata


def test_a_private_responsibilitys_receipts_never_pass_as_plans(sharing):
    note(sharing.vault_path, "Health/Clinic.md", subject="health", skill="brain-clinic", records="care", tier="private")
    exported = json.dumps(state.build(sharing, TODAY)["shareable"])
    assert "HEALTH_ACCOUNT_SECRET" not in exported


def test_a_grant_narrowed_by_scope_is_not_exported(sharing):
    path = sharing.vault_path / f"{sharing.people_dir}/Disclosure.md"
    path.write_text(path.read_text() + "\n## Grant scope\n\nD-CALENDAR only when Mira is there.\n")
    friends = state.build(sharing, TODAY)["shareable"]["group:Close friends"]
    assert "schedule" not in friends["tell"] and "Calendar receipt." not in json.dumps(friends)


def test_an_unmigrated_vault_shows_every_audience_nothing(sharing):
    note(sharing.vault_path, f"{sharing.people_dir}/Disclosure.md",
         "| id | subject | audience | rule |\n| --- | --- | --- | --- |\n| D1 | plans | anyone | allow |\n")
    shareable = state.build(sharing, TODAY)["shareable"]
    assert all(entry["tell"] == {} and entry["hubs"] == [] and entry["receipts"] == [] for entry in shareable.values())


def test_an_unknown_hub_subject_is_a_validation_problem(sharing):
    from brain import validate
    note(sharing.vault_path, "Private/Life.md", subject="private")
    assert "Private/Life.md: unknown subject 'private'; add it under ## Subjects in Disclosure.md or use one there" \
        in validate.access_problems(Vault(sharing.vault_path))
