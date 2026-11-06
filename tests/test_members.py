"""An organisation's intellect talks only to its members: the door admits them, the engine trusts it. Each session
says who the member is, that what they say is the team's, and that their work is theirs; the operator may also change
the intellect's setup. A personal intellect is unchanged."""
from brain import session_context
from brain.config import Config
from brain.vault import Vault


def config_for(vault, edition, extra=""):
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\nhuman_name = "Harbor"\nedition = "{edition}"\n{extra}')
    return Config.load(vault)


def session(config, **speaker):
    return session_context.build(Vault(config.vault_path), config, **speaker)


def test_a_member_is_named_told_the_policy_and_owns_their_work(vault):
    config = config_for(vault, "enterprise")
    text = session(config, platform="slack", user_id="U0KIM", user_name="Kim Lee")
    assert "This session is with Kim Lee, a member of Harbor." in text
    assert "visible to the whole team" in text and "theirs (slack:U0KIM)" in text
    assert "operator" not in text and "stranger" not in text
    assert session_context.is_member(config, "slack", ("U0KIM",))


def test_the_operator_may_change_the_setup_and_a_maintainer_or_routine_is_no_member(vault):
    config = config_for(vault, "enterprise", 'owner_user_ids = ["slack:U0ROSHAN"]\nmaintainers = ["slack:U0OPS"]\n')
    text = session(config, platform="slack", user_id="U0ROSHAN", user_name="Roshan")
    assert "a member of Harbor and its operator." in text and "brain-setup" in text
    assert "member of Harbor" not in session(config, platform="slack", user_id="U0OPS", user_name="Ops")
    assert not session_context.is_member(config, "cron", ()) and not session_context.is_member(config, "slack", ("U0OPS",))


def test_a_personal_intellect_still_treats_an_unknown_speaker_as_a_stranger(vault):
    config = config_for(vault, "personal")
    text = session(config, platform="slack", user_id="U0KIM", user_name="Kim Lee")
    assert "unknown person" in text and "member of" not in text
    assert not session_context.is_member(config, "slack", ("U0KIM",))
