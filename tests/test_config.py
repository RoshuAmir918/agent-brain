from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from brain.config import Config
from brain import nightly


def test_defaults_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAIN_HUMAN_NAME", raising=False)
    monkeypatch.delenv("BRAIN_AGENT_NAME", raising=False)
    config = Config.load(cwd=tmp_path)
    assert config == Config(tmp_path)
    assert config.vault_path == tmp_path / "memory"
    assert config.soul_path == tmp_path / "memory/agent/SOUL.md"
    assert config.skills_path == tmp_path / "memory/hermes/skills"


def test_discovery_from_cwd_and_vault_merges_defaults(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nagent_name = "Helper"\n')
    nested = tmp_path / "memory/people/nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    config = Config.load()
    assert config.repo == tmp_path
    assert config.agent_name == "Helper" and config.human_name == "Human"
    assert config.agent_dir == "agent" and config.skill_prefix == "brain-"
    assert Config.load(tmp_path / "memory").repo == tmp_path
    assert Config.load(tmp_path / "other").vault_path == tmp_path / "other"


def test_environment_names_override_toml_at_each_load(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nhuman_name = "Owner"\nagent_name = "Helper"\n')
    monkeypatch.setenv("BRAIN_HUMAN_NAME", "Person")
    monkeypatch.setenv("BRAIN_AGENT_NAME", "Assistant")
    config = Config.load(cwd=tmp_path)
    assert (config.human_name, config.agent_name) == ("Person", "Assistant")
    monkeypatch.delenv("BRAIN_AGENT_NAME")
    assert Config.load(cwd=tmp_path).agent_name == "Helper"


@pytest.mark.parametrize("setting", [
    'vault = "../outside"', 'agent_dir = "/absolute"', 'people_dir = ""',
    'human_profile = "../source.md"', 'skill_prefix = "../"',
    'agent_name = 1', 'unknown = "value"',
])
def test_invalid_config_fails_before_writing(tmp_path, setting):
    (tmp_path / "brain.toml").write_text("[brain]\n" + setting + "\n")
    with pytest.raises(ValueError):
        Config.load(cwd=tmp_path)


@pytest.mark.parametrize("name", ["soul", "skills", "scripts", "provider", "vault", "repo"])
def test_paths_from_cwd_and_explicit_vault(vault, monkeypatch, capsys, name, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.chdir(vault / "agent")
    config = Config.load()
    expected = {"soul": config.soul_path, "skills": config.skills_path,
                "scripts": tmp_path / "hermes-home/scripts", "provider": tmp_path / "hermes-home/plugins/brain",
                "vault": vault, "repo": vault.parent}[name]
    nightly.main(["paths", name])
    assert capsys.readouterr().out == str(expected) + "\n"
    monkeypatch.chdir(tmp_path)
    nightly.main(["paths", name, "--vault", str(vault)])
    assert capsys.readouterr().out == str(expected) + "\n"


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


@pytest.mark.parametrize("value", ['"U_OWNER"', '["U_OWNER", 12345]', '[true]', '{}', 'false'])
def test_owner_user_ids_reject_non_string_lists(tmp_path, value):
    (tmp_path / "brain.toml").write_text("[brain]\nowner_user_ids = " + value + "\n")
    with pytest.raises(ValueError, match="brain.owner_user_ids must be a list of strings"):
        Config.load(cwd=tmp_path)


def test_config_normalizes_relative_paths(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "./notes/wiki/"\nagent_dir = "internal//agent/"\n')
    config = Config.load(cwd=tmp_path)
    assert config.vault == "notes/wiki"
    assert config.agent_dir == "internal/agent"
    assert config.link(config.agent("Tasks")) == "notes/wiki/internal/agent/Tasks"


def test_nested_configured_paths_preview_and_write(vault, monkeypatch):
    from conftest import snapshot

    repo = vault.parent
    root = repo / "notes/wiki"
    root.parent.mkdir()
    vault.rename(root)
    (root / "internal").mkdir()
    (root / "agent").rename(root / "internal/agent")
    (repo / "brain.toml").write_text('[brain]\nvault = "notes/wiki"\nagent_dir = "internal/agent"\n')
    sources = repo / "sources"
    sources.mkdir()
    (sources / "Evidence.md").write_text("Read-only evidence")
    (root / "Source Hub.md").write_text("# Hub\n[[sources/Evidence]]\n")
    monkeypatch.chdir(root / "internal/agent")
    before = snapshot(repo)
    nightly.main(["nightly", "--date", "2026-10-05"])
    assert snapshot(repo) == before
    nightly.main(["nightly", "--date", "2026-10-05", "--write"])
    assert (repo / ".hermes.md").exists()
    assert "[[notes/wiki/internal/agent/Tasks Archive/T-001|" in (root / "internal/agent/Tasks.md").read_text()
    assert "## notes/wiki/internal/agent/Triggers.md" in (repo / ".hermes.md").read_text()


def test_log_dirs_route_agent_folders_to_log(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nlog_dirs = ["Receipts", "Results/Daily"]\n')
    config = Config.load(tmp_path)
    assert config.log_dirs == ["Receipts", "Results/Daily"]
    assert config.agent("Receipts/") in config.log_paths
    assert config.agent("Results/Daily/") in config.log_paths
    assert config.agent("Sleep Log/") in config.log_paths


@pytest.mark.parametrize("value", ['"Receipts"', '["../x"]', '["/abs"]', '[""]'])
def test_log_dirs_reject_bad_values(tmp_path, value):
    (tmp_path / "brain.toml").write_text("[brain]\nlog_dirs = " + value + "\n")
    with pytest.raises(ValueError, match="brain.log_dirs"):
        Config.load(tmp_path)


def test_maintainers_are_a_list_of_strings(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nmaintainers = ["U_MAINT"]\n')
    assert Config.load(tmp_path).maintainers == ["U_MAINT"]
    (tmp_path / "brain.toml").write_text('[brain]\nmaintainers = "U_MAINT"\n')
    with pytest.raises(ValueError, match="brain.maintainers must be a list of strings"):
        Config.load(tmp_path)
