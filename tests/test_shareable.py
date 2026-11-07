"""Audience grants: member ids, hub subjects and unconfirmed profiles."""

import pytest

from brain import access, fm, identity
from brain.config import Config
from brain.vault import Vault


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
    assert identity.profile_ids(line) == expected


def test_an_unknown_hub_subject_is_a_validation_problem(sharing):
    from brain import validate
    note(sharing.vault_path, "Private/Life.md", subject="private")
    assert "Private/Life.md: unknown subject 'private'; add it under ## Subjects in Disclosure.md or use one there" \
        in validate.access_problems(Vault(sharing.vault_path))


def test_an_unconfirmed_profile_gets_no_person_or_group_grants_and_is_no_exported_member(sharing):
    note(sharing.vault_path, KEVIN, "# Kevin (identity unconfirmed)\n- **Demiurge:** `k57kevin`\n")
    vault = Vault(sharing.vault_path)
    result = access.resolve(vault, sharing, KEVIN)
    assert result.groups == [] and result.subjects["anything"].tell == "none"
    assert result.subjects["work"].tell_rule == "D-ANYONE" and result.subjects["finances"].tell == "none"
