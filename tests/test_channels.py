from datetime import date

import pytest

from brain import channels, cli, fm, session_context, state, validate
from brain.config import Config
from brain.vault import Vault

TABLE = ("# Channels\n\nRegistry.\n\n"
         "| channel | whose | identity | reach | read | write | granted | status |\n"
         "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
         "| Home | owner | mira | demiurge | every session | replies | setup, 2026-10-01 | live |\n"
         "| Own Gmail | own | echo@example.com | op://Echo/Gmail app password | mail | sends to Mira only | Mira, 2026-10-20, in conversation | live |\n"
         "| Mira's Gmail | owner | mira@example.com | op://Echo/Mira Gmail app password | unread mail, headers | none | Mira, 2026-10-28, connect grant g1 | pending |\n"
         "| Old Slack | own | U1 | slack | — | — | setup | revoked |\n\n"
         "## Destinations\n\n- **Home:** demiurge.\n")


@pytest.fixture
def registry_vault(vault):
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nhuman_name = "Mira"\nagent_name = "Echo"\n')
    fm.write(vault / "agent/Channels.md", {"kind": "orientation", "pinned": True, "tier": "hot"}, TABLE)
    return vault


def test_rows_parse_and_only_live_ones_count_as_reach(registry_vault):
    rows = channels.load(Vault(registry_vault))
    assert [row.channel for row in rows] == ["Home", "Own Gmail", "Mira's Gmail", "Old Slack"]
    assert rows[1].op_refs == ["op://Echo/Gmail app password"]
    assert channels.item_of("op://Echo/Gmail app password/password") == "op://Echo/Gmail app password"
    assert channels.item_of("op://Echo/Gmail app password") == "op://Echo/Gmail app password"
    assert [row.channel for row in channels.live(rows)] == ["Home", "Own Gmail"]
    assert [row.channel for row in channels.live(rows, "own")] == ["Own Gmail"]


def test_sync_registers_completed_connect_grants_once_and_keeps_the_rest_of_the_note(registry_vault, capsys):
    listing = ("2026-11-03 09:12  g2  echo       secret   done op://Echo/Calendar token\n"
               "2026-11-03 09:10  g3  echo       secret   pending\n"
               "2026-11-02 22:00  g4  other      secret   done op://Other/Thing\n"
               "2026-11-01 08:00  g5  echo       secret   expired\n"
               "2026-10-20 10:00  g0  echo       secret   done op://Echo/Gmail app password\n")
    added = channels.sync(Config.load(registry_vault), Vault(registry_vault), listing.splitlines(), date(2026, 11, 3), "echo")
    assert [(row.channel, row.whose, row.reach, row.status) for row in added] == [("Calendar token", "owner", "op://Echo/Calendar token", "live")]
    assert added[0].granted == "Mira, 2026-11-03, connect grant g2"
    body = (registry_vault / "agent/Channels.md").read_text()
    assert body.count("| Calendar token |") == 1 and "## Destinations" in body and "Registry." in body
    assert channels.sync(Config.load(registry_vault), Vault(registry_vault), listing.splitlines(), date(2026, 11, 3), "echo") == []
    cli.main(["channels", "--vault", str(registry_vault)])
    out = capsys.readouterr().out
    assert "Calendar token · owner · live · reach op://Echo/Calendar token" in out and "Old Slack · own · revoked" in out


def test_validate_reports_vocabulary_budget_and_unregistered_credential_references(registry_vault):
    fm.write(registry_vault / "Acme.md", {"kind": "hub", "tier": "warm"}, "# Acme\nRead `op://Echo/Acme token/password` and `op://Echo/Gmail app password/password`; an unquoted op://Echo/Nope is not a reference.\n")
    (registry_vault / "agent/Channels").mkdir()
    fm.write(registry_vault / "agent/Channels/History.md", {"kind": "log", "tier": "cold"}, "- 2026-10-20 · op://Echo/Nothing here is fine, it is history.\n")
    problems = validate.channels(Vault(registry_vault))
    assert problems == ["Acme.md: credential reference op://Echo/Acme token is not a registered channel's reach"]
    data, body = fm.read(registry_vault / "agent/Channels.md")
    fm.write(registry_vault / "agent/Channels.md", data, body.replace("| pending |", "| maybe |").replace("| own | echo@", "| mine | echo@") + "word " * 400)
    problems = validate.channels(Vault(registry_vault))
    assert any("status must be one of" in p for p in problems) and any("whose must be one of" in p for p in problems)
    assert any("words; the registry is rows" in p for p in problems)
    assert Vault(registry_vault).kind_for("agent/Channels/History.md") == "log"


def test_the_owner_hears_what_the_agent_can_reach_and_the_state_exports_the_rows(registry_vault):
    (registry_vault / "human").mkdir()
    fm.write(registry_vault / "human/Profile.md", {"kind": "hub", "tier": "hot"}, "# Mira\n\n## Working together\n\n- Brief, no preamble.\n")
    (registry_vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nhuman_name = "Mira"\nagent_name = "Echo"\nowner_user_ids = ["U_OWNER"]\n')
    vault = Vault(registry_vault)
    block = session_context.build(vault, vault.config, platform="slack", user_id="U_OWNER")
    assert "What you can reach, from memory/agent/Channels.md: Home (owner, mira): read every session; write replies; Own Gmail (own, echo@example.com): read mail; write sends to Mira only." in block
    assert "Mira's Gmail" not in block and "Anything not listed, you cannot reach from here" in block
    exported = state.build(Config.load(registry_vault), date(2026, 11, 3))["channels"]
    assert [row["channel"] for row in exported] == ["Home", "Own Gmail", "Mira's Gmail", "Old Slack"]
    assert exported[2]["status"] == "pending" and exported[1]["reach"] == "op://Echo/Gmail app password"
