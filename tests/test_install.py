from importlib.resources import files
import os
import re
import subprocess

import pytest

from brain import fm, nightly
from brain.config import Config
from conftest import snapshot


@pytest.mark.parametrize("prefix", ["brain-", "helper-", ""])
def test_install_skills_renders_every_template_and_is_idempotent(vault, prefix, capsys):
    config_path = vault.parent / "brain.toml"
    config_path.write_text('[brain]\nskill_prefix = "' + prefix + '"\n')
    config = Config.load(vault)
    args = ["install-skills", "--vault", str(vault)]
    nightly.main(args)
    assert capsys.readouterr().out == str(config.skills_path) + "\n"
    templates = [path for path in files("brain").joinpath("skills").iterdir()
                 if path.joinpath("SKILL.md").is_file()]
    assert len(templates) == 12
    assert len(list(config.skills_path.glob("*/SKILL.md"))) == len(templates)
    for routine in templates:
        text = (config.skills_path / (prefix + routine.name) / "SKILL.md").read_text()
        assert not re.search(r"\{(?:" + "|".join(config.template_values()) + r")\}", text)
        assert fm.split(text)[0]["name"] == prefix + routine.name
        assert ".brain" not in text and "python3 -m brain" not in text
    before = snapshot(vault)
    nightly.main(args)
    assert snapshot(vault) == before


def test_install_scripts_copies_executable_package_resources(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    nightly.main(["install-scripts"])
    installed = sorted((home / "scripts").glob("*.sh"))
    assert [path.name for path in installed] == ["export-sessions.sh", "refresh.sh"]
    output = capsys.readouterr().out
    for path in installed:
        assert path.read_bytes() == files("brain").joinpath("scripts", path.name).read_bytes()
        assert path.stat().st_mode & 0o111 == 0o111
        assert f"installed {path}" in output
        subprocess.run(["bash", "-n", str(path)], check=True)
    before = snapshot(home)
    nightly.main(["install-scripts"])
    assert snapshot(home) == before


def test_scripts_path_defaults_to_hermes_home(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    nightly.main(["paths", "scripts"])
    assert capsys.readouterr().out == str(tmp_path / ".hermes/scripts") + "\n"


def test_install_provider_copies_only_three_files_and_replaces_them(tmp_path, monkeypatch, capsys):
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    nightly.main(["install-provider"])
    directory = home / "plugins/brain"
    names = {"__init__.py", "plugin.yaml", "README.md"}
    assert {path.name for path in directory.iterdir()} == names
    for name in names:
        target = directory / name
        assert target.read_bytes() == files("brain").joinpath("hermes_plugin", name).read_bytes()
        target.write_text("outdated")
    assert capsys.readouterr().out == (f"{directory}\nhermes config set memory.provider brain\n"
                                     "restart the gateway\n")
    nightly.main(["install-provider"])
    for name in names:
        assert (directory / name).read_bytes() == files("brain").joinpath("hermes_plugin", name).read_bytes()
    assert {path.relative_to(home).as_posix() for path in home.rglob("*") if path.is_file()} == {
        "plugins/brain/" + name for name in names}


def test_provider_path_defaults_to_hermes_home(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    nightly.main(["paths", "provider"])
    assert capsys.readouterr().out == str(tmp_path / ".hermes/plugins/brain") + "\n"


def test_packaged_scripts_run_with_config_and_isolated_external_commands(vault, tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("VAULT_REPO", str(vault.parent))
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, text in {
        "git": "#!/bin/sh\nexit 0\n",
        "hermes": '#!/bin/sh\nif [ "$5" = "jsonl" ]; then printf "%s\\n%s\\n" "{\\"id\\": \\"s1\\"}" "{\\"id\\": \\"cron_j_1\\", \\"source\\": \\"cron\\"}" > "$3"; else mkdir -p "$3"; : > "$3/$7.md"; fi\n',
    }.items():
        path = commands / name
        path.write_text(text)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    fm.write(vault / "Hot Set.md", {}, "# Hot set\n## Automatic load\n")
    (vault / "agent/SOUL.md").write_text("Fixture identity\n")
    nightly.main(["install-scripts"])
    subprocess.run(["bash", str(home / "scripts/export-sessions.sh")], cwd=tmp_path,
                   env=dict(os.environ), check=True, capture_output=True, text=True)
    assert (home / "SOUL.md").read_text() == "Fixture identity\n"
    assert (vault.parent / ".hermes.md").exists()
    assert (home / "records/sessions/jsonl/s1.jsonl").exists()
    assert not (vault / "Conversations/Sessions/jsonl").exists()
    assert (vault / "Conversations/Sessions/s1.md").exists()
    assert (home / "records/sessions/cron_j_1.md").exists() and not (vault / "Conversations/Sessions/cron_j_1.md").exists()
    assert len(list((vault / "Conversations/Digests").glob("*.md"))) == 1
    assert len(list((vault / "hermes/skills").glob("*/SKILL.md"))) == 12


def test_default_skills_are_written_once_and_core_skills_always(tmp_path):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    config = Config.load(tmp_path / "memory")
    install.skills(config)
    morning = config.skills_path / "brain-morning" / "SKILL.md"
    sleep1 = config.skills_path / "brain-sleep1" / "SKILL.md"
    morning.write_text("mine", encoding="utf-8")
    sleep1.write_text("mine", encoding="utf-8")
    install.skills(config)
    assert morning.read_text(encoding="utf-8") == "mine"
    assert sleep1.read_text(encoding="utf-8") != "mine"
    improve = config.skills_path / "brain-improve" / "SKILL.md"
    improve.write_text("mine", encoding="utf-8")
    install.skills(config)
    assert "Propose, never adopt" in improve.read_text(encoding="utf-8")
    watch = config.skills_path / "brain-watch" / "SKILL.md"
    watch.write_text(watch.read_text(encoding="utf-8") + "\n## Local\nRead the owner's second mailbox too.\n", encoding="utf-8")
    install.skills(config)
    text = watch.read_text(encoding="utf-8")
    assert text.startswith("---\nname: brain-watch") and text.endswith("\n\n## Local\nRead the owner's second mailbox too.\n")
    assert text.count("## Local") == 1 and "{human_name}" not in text
    install.skills(config)
    assert watch.read_text(encoding="utf-8") == text


def test_responsibility_builds_three_homes_and_keeps_existing_files(tmp_path, monkeypatch):
    from brain import install, nightly
    from brain.config import Config
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "brain.toml").write_text('[brain]\nhuman_name = "Mira"\nagent_name = "Pip"\n', encoding="utf-8")
    (tmp_path / "memory").mkdir()
    monkeypatch.chdir(tmp_path)
    nightly.main(["responsibility", "Fantasy GM", "--skill", "brain-housekeeping"])
    hub = tmp_path / "memory/Fantasy GM.md"
    skill = tmp_path / "memory/hermes/skills/brain-housekeeping/SKILL.md"
    assert (tmp_path / "home/records/housekeeping").is_dir()
    text = hub.read_text(encoding="utf-8")
    assert text.startswith("---\nkind: hub\nskill: brain-housekeeping\nrecords: housekeeping\n")
    assert "[[memory/hermes/skills/brain-housekeeping/SKILL|the skill]]" in text and "Mira" in text
    assert "[[memory/Fantasy GM|Fantasy GM]]" in skill.read_text(encoding="utf-8")
    skill.write_text("mine", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    assert install.responsibility(config, "Fantasy GM", "brain-housekeeping", "housekeeping") == []
    assert skill.read_text(encoding="utf-8") == "mine"


def test_update_uses_own_interpreter_and_reinstalls_scripts_and_provider(tmp_path, monkeypatch):
    import sys
    from brain import install
    from brain.config import Config
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, check: calls.append(cmd))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    install.provider()
    for path in install.provider_path().iterdir():
        path.write_text("outdated", encoding="utf-8")
    installed = install.update(Config.load(tmp_path / "memory"))
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert calls[0][-1] == "git+https://github.com/RoshuAmir918/agent-brain"
    assert {path.name for path in installed} == {"refresh.sh", "export-sessions.sh", "__init__.py", "plugin.yaml", "README.md"}
    for path in installed:
        resource_dir = "scripts" if path.suffix == ".sh" else "hermes_plugin"
        assert path.read_bytes() == files("brain").joinpath(resource_dir, path.name).read_bytes()


def test_update_is_disabled_by_empty_source(tmp_path):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text('[brain]\nupdate_source = ""\n', encoding="utf-8")
    assert install.update(Config.load(tmp_path / "memory")) == []


def test_migrate_check_records_version_when_nothing_applies(tmp_path, monkeypatch):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    monkeypatch.setattr(install, "installed_version", lambda name: "9.9.9")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    assert install.migrate_check(config) is None
    assert install.recorded_version(config) == "9.9.9"


def test_migrate_check_writes_pending_prompt_and_done_clears_it(tmp_path, monkeypatch):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    install.record_version(config, "1.0.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.2.0")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [("1.1.0", "Rename the thing."), ("1.2.0", "unattended: true\nRun the other thing.")])
    pending = install.migrate_check(config)
    text = pending.read_text(encoding="utf-8")
    assert text.index("## 1.1.0") < text.index("## 1.2.0")
    assert install.recorded_version(config) == "1.0.0"
    assert install.migrate_done(config) == "1.2.0"
    assert not pending.exists() and install.recorded_version(config) == "1.2.0"


def test_pending_migrations_selects_versions_in_range(tmp_path, monkeypatch):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    install.record_version(config, "0.1.2")
    monkeypatch.setattr(install, "installed_version", lambda name: "0.1.4")
    class Entry:
        def __init__(self, name, text): self.name, self.text = name, text
        def read_text(self, encoding): return self.text
    class Dir:
        def iterdir(self): return [Entry("README.md", "x"), Entry("0.1.2.md", "old"), Entry("0.1.3.md", "mid"), Entry("0.1.4.md", "new"), Entry("0.2.0.md", "future")]
    class Root:
        def joinpath(self, name): return Dir()
    monkeypatch.setattr(install, "files", lambda package: Root())
    assert install.pending_migrations(config) == [("0.1.3", "mid"), ("0.1.4", "new")]


def test_migrate_done_carries_a_deferred_step_as_an_ask(tmp_path, monkeypatch):
    from brain import install, needs
    from brain.config import Config
    from datetime import date
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    config.vault_path.mkdir(parents=True, exist_ok=True)
    (config.vault_path / "CURRENT.md").write_text("# Current\n", encoding="utf-8")
    install.record_version(config, "1.0.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.2.0")
    assert install.migrate_done(config, ["Reword SOUL's routing to Home and Hearth."]) == "1.2.0"
    assert install.recorded_version(config) == "1.2.0"
    [ask] = needs.items(config, date(2026, 10, 28))
    assert ask.kind == "ask" and ask.text == "Migration to 1.2.0 left for you: Reword SOUL's routing to Home and Hearth."
