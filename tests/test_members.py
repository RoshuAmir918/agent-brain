"""An organisation's intellect talks only to its members: the door admits them, the engine trusts it. Each session
says who the member is, that what they say is the team's, and that their work is theirs; the operator may also change
the intellect's setup. A personal intellect is unchanged."""
import json
from datetime import datetime

from brain import access_log, cli, fm, identity, journal, session_context
from brain.config import Config
from brain.vault import Vault


def config_for(vault, edition, extra=""):
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\nhuman_name = "Acme"\nedition = "{edition}"\n{extra}')
    return Config.load(vault)


def session(config, **speaker):
    return session_context.build(Vault(config.vault_path), config, **speaker)


def test_a_member_is_named_told_the_policy_and_owns_their_work(vault):
    config = config_for(vault, "enterprise")
    text = session(config, platform="slack", user_id="U0KIM", user_name="Kim Lee")
    assert "This session is with Kim Lee, a member of Acme." in text
    assert "visible to the whole team" in text and "theirs (slack:U0KIM)" in text
    assert "operator" not in text and "stranger" not in text
    assert identity.role(Vault(config.vault_path), config, "slack", ("U0KIM",)) == "member"


def test_the_operator_may_change_the_setup_and_a_routine_is_no_member(vault):
    config = config_for(vault, "enterprise", 'owner_user_ids = ["slack:U0ROSHAN"]\n')
    text = session(config, platform="slack", user_id="U0ROSHAN", user_name="Roshan")
    assert "a member of Acme and its operator." in text and "brain-setup" in text
    vault = Vault(config.vault_path)
    assert [identity.role(vault, config, platform, ids) for platform, ids in (
        ("cron", ()), ("cron", ("job",)), ("subagent", ("child",)))] == [
        "unknown", "unknown", "unknown"]


def test_session_context_json_says_member_or_operator_not_by_wording(vault, capsys):
    config_for(vault, "enterprise", 'owner_user_ids = ["slack:U0ROSHAN"]\n')
    flags = ["session-context", "--vault", str(vault), "--platform", "slack", "--json"]
    cli.main([*flags, "--user-id", "U0KIM", "--user-name", "Kim Lee"])
    member = json.loads(capsys.readouterr().out)
    assert "a member of Acme." in member["block"] and (member["owner"], member["member"]) == (False, True)
    cli.main([*flags, "--user-id", "U0ROSHAN"])
    operator = json.loads(capsys.readouterr().out)
    assert "and its operator." in operator["block"] and (operator["owner"], operator["member"]) == (True, False)
    cli.main(["session-context", "--vault", str(vault), "--platform", "cron", "--json"])
    assert json.loads(capsys.readouterr().out)["member"] is False


def test_a_personal_intellect_still_treats_an_unknown_speaker_as_a_stranger(vault):
    config = config_for(vault, "personal")
    text = session(config, platform="slack", user_id="U0KIM", user_name="Kim Lee")
    assert "unknown person" in text and "member of" not in text
    assert identity.role(Vault(config.vault_path), config, "slack", ("U0KIM",)) == "unknown"


def test_another_intellect_is_no_member_in_the_journal_or_the_access_log(vault):
    config = config_for(vault, "enterprise")
    agent = vault / config.agents_dir / "Nameless" / "Profile.md"
    agent.parent.mkdir(parents=True)
    fm.write(agent, {"kind": "profile"}, "# Nameless\n- **Slack:** `U0BOT`\n")
    owner = vault / config.human_profile
    owner.parent.mkdir(parents=True, exist_ok=True)
    fm.write(owner, {"kind": "profile"}, "# Acme\n\n## Working together\n- 2026-10-20: Short replies.\n")
    at = datetime(2026, 10, 28, 14, 5)
    record = {"headline": "Talked about the launch.", "outcome": "progress", "speaker": "Nameless",
              "workingTogether": ["Call me boss"], "noted": ["The launch moved to Friday"],
              "access": [{"subject": "schedule", "asked": "the launch date", "outcome": "declined", "rule": None}]}
    journal.append_entry(config, record, "slack", at, user_id="U0BOT")
    assert "boss" not in fm.read(owner)[1]
    assert "Nameless said: The launch moved to Friday" in (vault / "Conversations/2026-10-28 Noted.md").read_text()
    assert "agents/Nameless/Profile.md]] · schedule · declined" in access_log.path_for(config, "2026-10").read_text()
