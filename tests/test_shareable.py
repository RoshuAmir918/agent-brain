"""Audience grants: member ids, hub subjects and unconfirmed profiles."""

import pytest

from brain import access, fm, identity
from brain.config import Config
from brain.vault import Vault


KEVIN = "people/Kevin/Profile.md"
FRIENDS = "people/Groups/Close friends.md"
WORK = "people/Groups/Work.md"
HEADER = "| id | audience | subject | tell | do |\n| --- | --- | --- | --- | --- |\n"


def note(root, rel, body="", **data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, body + "\n")


def rules(config, *rows):
    note(config.vault_path, f"{config.people_dir}/Disclosure.md", HEADER + "\n".join(rows)
         + "\n\n## Subjects\n- pets: the owner's animals\n")


@pytest.fixture
def sharing(vault):
    config = Config.load(vault)
    note(vault, FRIENDS, f"## Members\n[[{KEVIN}]]", kind="group")
    note(vault, WORK, f"## Members\n[[{KEVIN}]]", kind="group")
    rules(config,
          f"| D-PERSON | [[{KEVIN}]] | anything | details | yes |",
          f"| D-WORK | [[{FRIENDS}]] | work | none | |",
          f"| D-FINANCES | [[{WORK}]] | finances | details | |",
          "| D-ANYONE | anyone | work | gist | |")
    return config


@pytest.mark.parametrize("line,expected", [
    ("- **Demiurge:** `k57kevin` (@kevin; confirmed today)", ["demiurge:k57kevin"]),
    ("**SLACK**: U_KEVIN", ["slack:U_KEVIN"]),
    ("  * **Telegram:** `-12345`", ["telegram:-12345"]),
    ("+ Discord: 98765", ["discord:98765"]),
    ("- **WhatsApp:** 15551234", ["whatsapp:15551234"]), ("- Signal: sig-1", ["signal:sig-1"]),
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
