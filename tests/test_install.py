from importlib.resources import files
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from brain import cli, fm
from brain.config import Config
from conftest import snapshot


@pytest.mark.parametrize("prefix", ["brain-", "helper-", ""])
def test_install_skills_renders_every_template_and_is_idempotent(vault, prefix, capsys):
    config_path = vault.parent / "brain.toml"
    config_path.write_text('[brain]\nskill_prefix = "' + prefix + '"\n')
    config = Config.load(vault)
    args = ["install-skills", "--vault", str(vault)]
    cli.main(args)
    assert capsys.readouterr().out == str(config.skills_path) + "\n"
    templates = [path for path in files("brain").joinpath("skills").iterdir()
                 if path.joinpath("SKILL.md").is_file() and "editions: [enterprise]" not in path.joinpath("SKILL.md").read_text()]
    assert len(templates) == 14
    assert len(list(config.skills_path.glob("*/SKILL.md"))) == len(templates)
    for routine in templates:
        text = (config.skills_path / (prefix + routine.name) / "SKILL.md").read_text()
        assert not re.search(r"\{(?:" + "|".join(config.template_values()) + r")\}", text)
        assert fm.split(text)[0]["name"] == prefix + routine.name
        assert ".brain" not in text and "python3 -m brain" not in text
    before = snapshot(vault)
    cli.main(args)
    assert snapshot(vault) == before


def test_install_scripts_copies_executable_package_resources(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    cli.main(["install-scripts"])
    installed = sorted((home / "scripts").glob("*.sh"))
    assert [path.name for path in installed] == ["attribution.sh", "continue-daily-signal.sh", "continue-signal.sh", "export-sessions.sh", "refresh.sh", "watch-signal.sh"]
    refresh = (home / "scripts/refresh.sh").read_text()
    assert refresh.index("brain people sync --run >/dev/null 2>&1 || true") < refresh.index("brain compile-context --write")
    output = capsys.readouterr().out
    for path in installed:
        assert path.read_bytes() == files("brain").joinpath("scripts", path.name).read_bytes()
        assert path.stat().st_mode & 0o111 == 0o111
        assert f"installed {path}" in output
        subprocess.run(["bash", "-n", str(path)], check=True)
    before = snapshot(home)
    cli.main(["install-scripts"])
    assert snapshot(home) == before


def test_packaged_scripts_run_with_config_and_isolated_external_commands(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("VAULT_REPO", str(vault.parent))
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, text in {
        "git": "#!/bin/sh\nexit 0\n",
    }.items():
        path = commands / name
        path.write_text(text)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    (vault / "agent/SOUL.md").write_text("Fixture identity\n")
    from conftest import harness_session, message
    for sid, platform in (("s1", "demiurge"), ("cron_j_1", "cron")):
        session = harness_session(sid, [message("user", "hello")], platform=platform)
        folder = home / "harness/sessions" / session["day"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{sid}.json").write_text(json.dumps(session))
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-21")
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nupdate_source = ""\n')
    cli.main(["install-scripts"])
    subprocess.run(["bash", str(home / "scripts/export-sessions.sh")], cwd=tmp_path,
                   env=dict(os.environ), check=True, capture_output=True, text=True)
    assert (home / "SOUL.md").read_text() == "Fixture identity\n"
    assert (vault.parent / ".context.md").exists()
    assert (home / "records/sessions/jsonl/s1.json").exists()
    assert not (vault / "Conversations/Sessions/jsonl").exists()
    assert (vault / "Conversations/Sessions/s1.md").exists()
    assert (home / "records/sessions/cron_j_1.md").exists() and not (vault / "Conversations/Sessions/cron_j_1.md").exists()
    assert len(list((vault / "Conversations/Digests").glob("*.md"))) == 1
    assert len(list((vault / "Runtime/skills").glob("*/SKILL.md"))) == 14


def test_attribution_script_writes_observed_rows_without_a_model(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("VAULT_REPO", str(vault.parent))
    monkeypatch.setenv("BRAIN_HOT_WORDS", "0")
    commands = tmp_path / "bin"
    commands.mkdir()
    (commands / "git").write_text("#!/bin/sh\nexit 0\n")
    (commands / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    fm.write(vault / "Acme Context.md", {"tier": "hot", "uses": [], "uses_inferred": []}, "# Fixture\n")
    shutil.copytree(Path(__file__).parent / "fixtures/sessions", home / "records/sessions/jsonl")
    cli.main(["install-scripts"])
    subprocess.run(["bash", str(home / "scripts/attribution.sh")], cwd=tmp_path,
                   env=dict(os.environ), check=True, capture_output=True, text=True)
    log = (vault / "agent/Use Log/2026-10.md").read_text()
    assert "| Acme Context.md | opened on demand in session cli-session | observed" in log
    assert "judged:" not in log


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
    # Morning is the engine's since 0.18: the vault's own copy is kept once, beside the new text, to move under Local.
    assert morning.read_text(encoding="utf-8") != "mine"
    assert morning.with_name("previous.md").read_text(encoding="utf-8") == "mine"
    assert sleep1.read_text(encoding="utf-8") != "mine" and not sleep1.with_name("previous.md").exists()
    morning.write_text(morning.read_text(encoding="utf-8") + "\n## Local\n\n- MY_OWN_STEP\n", encoding="utf-8")
    install.skills(config)
    assert "MY_OWN_STEP" in morning.read_text(encoding="utf-8")
    assert morning.with_name("previous.md").read_text(encoding="utf-8") == "mine"
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
    from brain import cli, install
    from brain.config import Config
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    (tmp_path / "brain.toml").write_text('[brain]\nhuman_name = "Mira"\nagent_name = "Pip"\n', encoding="utf-8")
    (tmp_path / "memory").mkdir()
    monkeypatch.chdir(tmp_path)
    cli.main(["responsibility", "Fantasy GM", "--skill", "brain-housekeeping"])
    hub = tmp_path / "memory/Fantasy GM.md"
    skill = tmp_path / "memory/Runtime/skills/brain-housekeeping/SKILL.md"
    assert (tmp_path / "home/records/housekeeping").is_dir()
    text = hub.read_text(encoding="utf-8")
    assert text.startswith("---\nkind: hub\nskill: brain-housekeeping\nrecords: housekeeping\n")
    assert "[[memory/Runtime/skills/brain-housekeeping/SKILL|the skill]]" in text and "Mira" in text
    assert "Which of Mira's goals this work advances" in text   # no Goals note, so no link to one
    (tmp_path / "memory/human").mkdir()
    fm.write(tmp_path / "memory/human/Goals.md", {}, "# Goals\n")
    install.responsibility(Config.load(tmp_path / "memory"), "Chess", "brain-chess", "chess")
    assert "goals in [[memory/human/Goals|Goals]] this work" in (tmp_path / "memory/Chess.md").read_text()
    assert "[[memory/Fantasy GM|Fantasy GM]]" in skill.read_text(encoding="utf-8")
    skill.write_text("mine", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    assert install.responsibility(config, "Fantasy GM", "brain-housekeeping", "housekeeping") == []
    assert skill.read_text(encoding="utf-8") == "mine"


def test_update_uses_own_interpreter_and_reinstalls_scripts(tmp_path, monkeypatch):
    import sys
    from brain import install
    from brain.config import Config
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, check: calls.append(cmd))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    for path in install.scripts():
        path.write_text("outdated", encoding="utf-8")
    installed = install.update(Config.load(tmp_path / "memory"))
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert calls[0][-1] == "git+https://github.com/RoshuAmir918/agent-brain"
    assert {path.name for path in installed} == {"attribution.sh", "refresh.sh", "export-sessions.sh", "watch-signal.sh", "continue-signal.sh", "continue-daily-signal.sh"}
    for path in installed:
        resource_dir = "scripts"
        assert path.read_bytes() == files("brain").joinpath(resource_dir, path.name).read_bytes()


def test_update_is_disabled_by_empty_source(tmp_path):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text('[brain]\nupdate_source = ""\n', encoding="utf-8")
    assert install.update(Config.load(tmp_path / "memory")) == []


def test_update_without_runtime_installs_package_and_skips_scripts(vault, monkeypatch, capsys):
    import sys
    from brain import install

    monkeypatch.delenv("AGENT_HOME", raising=False)
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, check: calls.append((cmd, check)))
    def unexpected():
        pytest.fail("scripts must not be installed without a runtime")
    monkeypatch.setattr(install, "scripts", unexpected)
    before = snapshot(vault.parent)
    cli.main(["update", "--vault", str(vault)])
    assert calls == [([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
                      Config.load(vault).update_source], True)]
    assert capsys.readouterr().out == "scripts not reinstalled: AGENT_HOME is not set\n"
    assert snapshot(vault.parent) == before


@pytest.mark.parametrize("runtime_dir", ["Runtime", "Runs"])
def test_runtime_directory_renders_in_local_sections(vault, runtime_dir):
    from brain import install

    (vault.parent / "brain.toml").write_text(f'[brain]\nruntime_dir = "{runtime_dir}"\n')
    config = Config.load(vault)
    target = config.skills_path / "brain-watch/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text('old\n\n## Local\nRead {runtime_dir}/schedule.toml.\n')
    spec = vault / config.agent("Memory Spec.md")
    spec.write_text('# Spec\n\n## Local\nRead {runtime_dir}/README.md.\n')
    install.skills(config)
    assert f'Read {runtime_dir}/schedule.toml.' in target.read_text()
    assert f'Read {runtime_dir}/README.md.' in spec.read_text()


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


def test_a_new_vault_records_the_installed_version_and_replays_nothing(tmp_path, monkeypatch):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.2.0")
    assert install.pending_migrations(config) == []
    assert install.migrate_check(config) is None
    assert install.recorded_version(config) == "1.2.0"


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


def test_a_new_migration_starts_once_and_a_repeat_check_does_not_start_it_again(tmp_path, monkeypatch, migration_runs):
    from brain import install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    install.record_version(config, "1.0.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.1.0")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [("1.1.0", "Do the thing.")])
    install.migrate_check(config)
    install.migrate_check(config)
    assert len(migration_runs) == 1
    monkeypatch.setattr(install, "installed_version", lambda name: "1.2.0")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [("1.1.0", "Do the thing."), ("1.2.0", "And more.")])
    install.migrate_check(config)
    assert len(migration_runs) == 2


def test_an_unscheduled_migration_is_retried_and_a_stale_pending_file_goes(tmp_path, monkeypatch, migration_runs):
    from brain import install, schedule
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    install.record_version(config, "1.0.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.1.0")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [("1.1.0", "Do the thing.")])
    install.migrate_check(config)
    schedule.save(config, {"watch": schedule.Routine(every="1h")})
    install.migrate_check(config)
    assert len(migration_runs) == 2
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    install.migrate_check(config)
    assert not (config.vault_path / config.runtime_dir / "pending-migration.md").exists()


def test_a_migration_prompt_has_its_tokens_rendered(vault, monkeypatch):
    from brain import install
    from brain.config import Config
    config = Config.load(vault)
    install.record_version(config, "0.51.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "0.51.1")
    (tag, prompt), = install.pending_migrations(config)
    assert tag == "0.51.1" and "{agent_dir}" not in prompt and f"{config.agent_dir}/Primed.md" in prompt


def test_the_memory_spec_follows_the_engine_and_keeps_the_vaults_local_section(tmp_path):
    from brain import fm, install
    from brain.config import Config
    (tmp_path / "brain.toml").write_text('[brain]\nagent_dir = "Orphan"\n', encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    target = config.vault_path / config.agent("Memory Spec.md")
    target.parent.mkdir(parents=True)
    fm.write(target, {"kind": "orientation", "pinned": True, "tier": "hot", "uses": ["2026-10-01"]},
             "# Memory spec\nMidday owns correspondence.\n\n## Local\nKeep letters on Sundays.\n")
    install.memory_spec(config)
    data, body = fm.read(target)
    assert "Midday owns correspondence" not in body and "{" not in body.split("## Local")[0].replace("{prefix}", "")
    assert "`Orphan/Prepared/" in body and body.rstrip().endswith("Keep letters on Sundays.")
    assert data["uses"] == ["2026-10-01"]
    before = target.read_text(encoding="utf-8")
    install.memory_spec(config)
    assert target.read_text(encoding="utf-8") == before
