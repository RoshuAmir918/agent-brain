import sys
from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from brain.config import Config
from brain import cli, install
from conftest import snapshot


def test_discovery_from_cwd_and_vault_merges_defaults(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nagent_name = "Helper"\n')
    nested = tmp_path / "memory/people/nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    config = Config.load()
    assert config.repo == tmp_path
    assert config.agent_name == "Helper" and config.human_name == "Human"
    assert config.agent_dir == "agent" and config.skill_prefix == "brain-" and config.edition == "personal"
    assert Config.load(tmp_path / "memory").repo == tmp_path
    assert Config.load(tmp_path / "other").vault_path == tmp_path / "other"
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "./notes/wiki/"\nagent_dir = "internal//agent/"\n')
    config = Config.load(cwd=tmp_path)
    assert (config.vault, config.agent_dir) == ("notes/wiki", "internal/agent")
    assert config.link(config.agent("Tasks")) == "notes/wiki/internal/agent/Tasks"


def test_environment_names_override_toml_at_each_load(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nhuman_name = "Owner"\nagent_name = "Helper"\n')
    monkeypatch.setenv("BRAIN_HUMAN_NAME", "Person")
    monkeypatch.setenv("BRAIN_AGENT_NAME", "Assistant")
    config = Config.load(cwd=tmp_path)
    assert (config.human_name, config.agent_name) == ("Person", "Assistant")
    monkeypatch.delenv("BRAIN_AGENT_NAME")
    assert Config.load(cwd=tmp_path).agent_name == "Helper"


POSITIVE = "must be a positive integer"


@pytest.mark.parametrize("toml, env, match", [
    *[("[brain]\n" + setting, None, None) for setting in (
        'vault = "../outside"', 'agent_dir = "/absolute"', 'people_dir = ""',
        'human_profile = "../source.md"', 'human_profile = "Profile.md"', 'skill_prefix = "../"',
        'agent_name = 1', 'unknown = "value"')],
    ('[brain]\nowner_user_ids = "U_OWNER"', None, "brain.owner_user_ids must be a list of strings"),
    ('[brain]\nowner_user_ids = ["U_OWNER", 12345]', None, "brain.owner_user_ids must be a list of strings"),
    ('[brain]\nlog_dirs = [""]', None, "brain.log_dirs must be a list of strings"),
    ('[brain]\nlog_dirs = ["../x"]', None, "brain.log_dirs must be relative"),
    ('[brain]\nedition = "team"', None, "brain.edition must be one of personal, enterprise"),
    ('[brain]\ntimezone = "Mars/Olympus"', None, "timezone"),
    *[(f"[continue]\ndaily_chunks = {value}", None, "continue.daily_chunks " + POSITIVE) for value in ("0", "true", '"4"')],
    ("[continue]\nreview_every = 0", None, "continue.review_every " + POSITIVE),
    ("[continue]\nunknown = 1", None, "only daily_chunks"),
    *[("", value, "BRAIN_DAILY_CHUNKS " + POSITIVE) for value in ("0", "", "1.5")],
])
def test_invalid_config_fails_before_writing(tmp_path, monkeypatch, toml, env, match):
    if env is None:
        monkeypatch.delenv("BRAIN_DAILY_CHUNKS", raising=False)
    else:
        monkeypatch.setenv("BRAIN_DAILY_CHUNKS", env)
    (tmp_path / "brain.toml").write_text(toml + "\n")
    with pytest.raises(ValueError, match=match):
        Config.load(cwd=tmp_path)


@pytest.mark.parametrize("name", ["soul", "skills", "scripts", "records", "vault", "repo", "python", "schedule"])
def test_paths_from_cwd_and_explicit_vault_need_agent_home_only_for_runtime(vault, monkeypatch, capsys, name, tmp_path):
    monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.chdir(vault / "agent")
    config = Config.load()
    before = snapshot(vault.parent)
    if name in ("scripts", "records"):
        with pytest.raises(SystemExit, match="AGENT_HOME is not set"):
            cli.main(["paths", name])
        monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    expected = {"soul": lambda: config.soul_path, "skills": lambda: config.skills_path,
                "scripts": lambda: tmp_path / "agent-home/scripts", "records": install.records_path,
                "vault": lambda: vault, "repo": lambda: vault.parent, "python": lambda: Path(sys.executable),
                "schedule": lambda: vault / "Runtime/schedule.toml"}[name]().resolve()
    cli.main(["paths", name])
    assert capsys.readouterr().out == str(expected) + "\n"
    monkeypatch.chdir(tmp_path)
    cli.main(["paths", name, "--vault", str(vault)])
    assert capsys.readouterr().out == str(expected) + "\n"
    assert snapshot(vault.parent) == before


def test_validate_without_agent_home_writes_nothing_and_reports_a_bad_schedule(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_HOME", raising=False)
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "memory"\n')
    (tmp_path / "memory/Runtime").mkdir(parents=True)
    (tmp_path / "memory/Runtime/schedule.toml").write_text('[routines]\n')
    monkeypatch.chdir(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(SystemExit) as validated:
        cli.main(["validate"])
    assert validated.value.code == 0 and capsys.readouterr().out == "0 problems\n"
    assert snapshot(tmp_path) == before
    (tmp_path / "memory/Runtime/schedule.toml").write_text('[routines.bad]\nevery = "invalid"\n')
    with pytest.raises(SystemExit) as validated:
        cli.main(["validate"])
    out = capsys.readouterr().out
    assert validated.value.code == 1 and "every" in out and out.endswith("1 problems\n")


def test_repository_template_lists_all_public_config_keys():
    import tomllib

    path = Path(__file__).parents[1] / "docs" / "brain.toml.example"
    values = tomllib.loads(path.read_text())["brain"]
    expected = {item.name: item.default if item.default is not MISSING else item.default_factory() for item in fields(Config)
                if item.name not in ("repo", "_vault_path")}
    assert values == expected


def test_owner_user_ids_are_strings_and_do_not_change_template_values(tmp_path):
    defaults = Config(tmp_path)
    (tmp_path / "brain.toml").write_text('[brain]\nowner_user_ids = ["U_OWNER", "12345"]\n')
    config = Config.load(cwd=tmp_path)
    assert config.owner_user_ids == ["U_OWNER", "12345"]
    assert config.template_values() == defaults.template_values()
    assert Config(tmp_path).owner_user_ids is not defaults.owner_user_ids


def test_log_dirs_route_agent_folders_to_log(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nlog_dirs = ["Receipts", "Results/Daily"]\n')
    config = Config.load(tmp_path)
    assert config.log_dirs == ["Receipts", "Results/Daily"]
    assert config.agent("Receipts/") in config.log_paths
    assert config.agent("Results/Daily/") in config.log_paths
    assert config.agent("Sleep Log/") in config.log_paths


def test_a_retired_maintainers_key_still_loads(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nmaintainers = ["U_MAINT"]\n')
    assert not hasattr(Config.load(tmp_path), "maintainers")


def test_timezone_defaults_to_pacific(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "brain.toml").write_text('[brain]\ntimezone = "America/New_York"\n', encoding="utf-8")
    assert Config.load(tmp_path / "memory").timezone == "America/New_York"
    assert Config(tmp_path).timezone == "America/Los_Angeles"


def test_vault_at_the_repo_root_means_the_configured_vault(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "memory"\n')
    (tmp_path / "memory").mkdir()
    assert Config.load(tmp_path).vault_path == (tmp_path / "memory").resolve()
    assert Config.load(tmp_path / "memory").vault_path == (tmp_path / "memory").resolve()


def test_continue_settings_default_toml_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_DAILY_CHUNKS", raising=False)
    config = Config.load(cwd=tmp_path)
    assert (config.daily_chunks, config.review_every) == (24, 4)
    (tmp_path / "brain.toml").write_text("[continue]\ndaily_chunks = 7\nreview_every = 6\n")
    config = Config.load(cwd=tmp_path)
    assert (config.daily_chunks, config.review_every) == (7, 6)
    monkeypatch.setenv("BRAIN_DAILY_CHUNKS", "4")
    assert Config.load(cwd=tmp_path).daily_chunks == 4
