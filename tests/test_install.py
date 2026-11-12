from importlib.resources import files
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from brain import cli, fm, install, ledger, responsibilities, schedule, validate
from brain.config import Config
from brain.vault import Vault
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
    assert templates and len(list(config.skills_path.glob("*/SKILL.md"))) == len(templates)
    for routine in templates:
        text = (config.skills_path / (prefix + routine.name) / "SKILL.md").read_text()
        assert not re.search(r"\{(?:" + "|".join(config.template_values()) + r")\}", text)
        assert fm.split(text)[0]["name"] == prefix + routine.name
        assert ".brain" not in text and "python3 -m brain" not in text
    before = snapshot(vault)
    cli.main(args)
    assert snapshot(vault) == before


@pytest.mark.parametrize("entry_point", ["migrate-check", "sync"])
def test_responsibility_pause_conversion_precedes_sync_once(vault, monkeypatch, entry_point):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-12")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    config = Config.load(vault)
    documents = {
        "food-deals": {"version": 1, "custom": {"keep": [1, 2]}, "triggers": [
            {"kind": "schedule", "when": "0 8 * * *"}]},
        "l1-study": {"version": 1, "triggers": [
            {"kind": "schedule", "when": "every 1h"}, {"kind": "mail"},
            {"kind": "schedule", "when": "once 2026-11-01T08:00:00-08:00"},
            {"kind": "schedule", "when": "0 8 * * *"}]},
        "retired": {"version": 1, "status": "retired", "ended": "2026-11-10", "triggers": [
            {"kind": "schedule", "when": "every 1h"}]},
    }
    paths = {}
    for ident, data in documents.items():
        paths[ident] = vault / "Responsibilities" / ident / "RESPONSIBILITY.md"
        paths[ident].parent.mkdir(parents=True)
        fm.write(paths[ident], data, "Keep body exactly.\n\n")
    legacy = schedule.Routine(every="2h", responsibility="legacy", enabled=False)
    fm.write(vault / "Legacy.md", {"skill": "brain-legacy", "records": "legacy"}, "Legacy work.\n")
    schedule.save(config, {
        "food-deals": schedule.Routine(cron="0 8 * * *", responsibility="food-deals", enabled=False),
        "l1-study": schedule.Routine(every="1h", responsibility="l1-study"),
        "l1-study-3": schedule.Routine(cron="0 8 * * *", responsibility="l1-study", enabled=False),
        "retired": schedule.Routine(every="1h", responsibility="retired", enabled=False),
        "legacy": legacy,
    })
    if entry_point == "migrate-check":
        install.migrate_check(config)
    else:
        responsibilities.sync(Vault(vault))
    assert fm.read(paths["food-deals"]) == ({**documents["food-deals"], "status": "paused"}, "Keep body exactly.\n\n")
    partial, body = fm.read(paths["l1-study"])
    assert "status" not in partial and body == "Keep body exactly.\n\n"
    assert partial["triggers"] == [*documents["l1-study"]["triggers"][:3],
                                   {**documents["l1-study"]["triggers"][3], "paused": True}]
    assert fm.read(paths["retired"])[0] == documents["retired"]
    marker = vault / config.runtime_dir / "responsibility-status-0.185.0"
    assert marker.exists()
    before = {ident: path.read_bytes() for ident, path in paths.items()}
    assert responsibilities.migrate_pauses(Vault(vault)) == []
    responsibilities.sync(Vault(vault))
    assert {ident: path.read_bytes() for ident, path in paths.items()} == before
    actual = schedule.load(config)
    assert not actual["food-deals"].enabled and not actual["l1-study-3"].enabled
    assert actual["l1-study"].enabled and "retired" not in actual
    assert actual["legacy"] == legacy
    data, body = fm.read(paths["food-deals"])
    data["status"] = "active"
    fm.write(paths["food-deals"], data, body)
    responsibilities.sync(Vault(vault))
    assert schedule.load(config)["food-deals"].enabled


def test_every_routine_that_messages_the_owner_carries_one_owner_message_policy(vault):
    config = Config.load(vault)
    for routine in ("continue", "watch", "explore", "improve", "morning"):
        text = install.render_skill(config, routine)
        assert text.count("## Messages to " + config.human_name + "\n") == 1, routine
        assert "{owner_messages}" not in text and "{human_name}" not in text, routine
    assert "## Messages to" not in install.render_skill(config, "recall")   # owner sessions carry the short version


def test_install_scripts_copies_executable_package_resources(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    (home / "scripts").mkdir(parents=True)
    for retired in ("continue-signal.sh", "continue-daily-signal.sh"):
        (home / "scripts" / retired).write_text("exec brain continue-signal\n")
    cli.main(["install-scripts"])
    installed = sorted((home / "scripts").glob("*.sh"))
    assert [path.name for path in installed] == ["attribution.sh", "evening-signal.sh", "explore-signal.sh", "export-sessions.sh", "night-after.sh", "night-before.sh", "refresh.sh", "upgrade.sh", "watch-signal.sh"]
    refresh = (home / "scripts/refresh.sh").read_text()
    assert refresh.index("brain people sync --run >/dev/null 2>&1 || true") < refresh.index("brain compile-context --write")
    assert refresh.index("brain responsibility sync >/dev/null 2>&1 || true") < refresh.index("brain commit --push")
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
    (commands / "git").write_text("#!/bin/sh\nexit 0\n")
    (commands / "git").chmod(0o755)
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
    assert not list((vault / "Runtime/skills").glob("*/SKILL.md"))
    subprocess.run(["bash", str(home / "scripts/upgrade.sh")], cwd=tmp_path,
                   env=dict(os.environ), check=True, capture_output=True, text=True)
    assert list((vault / "Runtime/skills").glob("*/SKILL.md"))
    monkeypatch.setenv("BRAIN_HOT_WORDS", "0")
    fm.write(vault / "Acme Context.md", {"tier": "hot", "uses": [], "uses_inferred": []}, "# Fixture\n")
    shutil.copytree(Path(__file__).parent / "fixtures/sessions", home / "records/sessions/jsonl", dirs_exist_ok=True)
    subprocess.run(["bash", str(home / "scripts/attribution.sh")], cwd=tmp_path,
                   env=dict(os.environ), check=True, capture_output=True, text=True)
    log = (vault / "agent/Use Log/2026-10.md").read_text()
    assert "| Acme Context.md | opened on demand in session cli-session | observed" in log
    assert "judged:" not in log


def test_refresh_keeps_going_when_responsibility_sync_fails(tmp_path, monkeypatch):
    root = tmp_path / "vault repo"
    runtime = root / "memory/Runtime"
    (runtime / "skills").mkdir(parents=True)
    (runtime / "health.md").write_text("Healthy.\n")
    (runtime / "schedule.toml").write_text("# derived schedule\n")
    soul = root / "memory/SOUL.md"
    soul.write_text("Identity.\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "commands.jsonl"
    paths = {"soul": str(soul), "skills": str(runtime / "skills"), "schedule": str(runtime / "schedule.toml")}
    command = commands / "brain"
    command.write_text(f"#!{sys.executable}\nimport json, sys\n"
        f"with open({str(log)!r}, 'a') as stream:\n    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"paths = {paths!r}\n"
        "if sys.argv[1:3] == ['responsibility', 'sync']:\n    print('sync failed', file=sys.stderr)\n    sys.exit(1)\n"
        "if sys.argv[1] == 'paths':\n    print(paths[sys.argv[2]])\n")
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("VAULT_REPO", str(root))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-11")
    result = subprocess.run(["bash", str(Path(__file__).resolve().parents[1] / "src/brain/scripts/refresh.sh")],
                            capture_output=True, text=True, check=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls.index(["responsibility", "sync"]) < next(i for i, call in enumerate(calls) if call[0] == "commit")
    commit, = [call for call in calls if call[0] == "commit"]
    assert "sync failed" not in result.stdout + result.stderr


def test_default_skills_are_written_once_and_core_skills_always(tmp_path):
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    config = Config.load(tmp_path / "memory")
    install.skills(config)
    morning = config.skills_path / "brain-morning" / "SKILL.md"
    sleep1 = config.skills_path / "brain-sleep1" / "SKILL.md"
    morning.write_text("mine", encoding="utf-8")
    sleep1.write_text("mine", encoding="utf-8")
    install.skills(config)
    assert morning.read_text(encoding="utf-8") != "mine" and sleep1.read_text(encoding="utf-8") != "mine"
    morning.write_text(morning.read_text(encoding="utf-8") + "\n## Local\n\n- MY_OWN_STEP\n", encoding="utf-8")
    install.skills(config)
    assert "MY_OWN_STEP" in morning.read_text(encoding="utf-8")
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


def test_update_uses_own_interpreter_and_reinstalls_scripts_only_with_a_runtime(vault, tmp_path, monkeypatch, capsys):
    import sys
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, check: calls.append((cmd, check)))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    for path in install.scripts():
        path.write_text("outdated", encoding="utf-8")
    installed = install.update(Config.load(vault))
    assert calls == [([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
                       "git+https://github.com/RoshuAmir918/agent-brain"], True)]
    assert installed
    for path in installed:
        assert path.read_bytes() == files("brain").joinpath("scripts", path.name).read_bytes()
    calls.clear()
    monkeypatch.delenv("AGENT_HOME")
    def unexpected():
        pytest.fail("scripts must not be installed without a runtime")
    monkeypatch.setattr(install, "scripts", unexpected)
    before = snapshot(vault.parent)
    cli.main(["update", "--vault", str(vault)])
    assert len(calls) == 1
    assert capsys.readouterr().out == "scripts not reinstalled: AGENT_HOME is not set\n"
    assert snapshot(vault.parent) == before
    (vault.parent / "brain.toml").write_text('[brain]\nupdate_source = ""\n', encoding="utf-8")
    assert install.update(Config.load(vault)) == []


def test_a_new_vault_or_nothing_pending_records_the_installed_version(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    monkeypatch.setattr(install, "installed_version", lambda name: "1.2.0")
    assert install.pending_migrations(config) == []
    assert install.migrate_check(config) is None
    assert install.recorded_version(config) == "1.2.0"
    monkeypatch.setattr(install, "installed_version", lambda name: "9.9.9")
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    assert install.migrate_check(config) is None
    assert install.recorded_version(config) == "9.9.9"


def test_migrate_check_writes_pending_prompt_and_done_clears_it(tmp_path, monkeypatch):
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
    from brain import schedule
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


def test_every_shipped_migration_is_ordered_by_version_and_fully_rendered(vault, monkeypatch):
    config = Config.load(vault)
    install.record_version(config, "0.0.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "999.0.0")
    shipped = [path.name[:-3] for path in files("brain").joinpath("migrations").iterdir()
               if path.name.endswith(".md") and path.name != "README.md"]
    pending = install.pending_migrations(config)
    assert [tag for tag, _ in pending] == sorted(shipped, key=install.version_tuple)
    unrendered = re.compile(r"\{(?:" + "|".join(config.template_values()) + r")\}")
    assert [tag for tag, prompt in pending if unrendered.search(prompt)] == []


def test_the_memory_spec_follows_the_engine_and_keeps_the_vaults_local_section(tmp_path):
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


@pytest.mark.parametrize("edition, prefix", [("personal", ""), ("enterprise", "own-")])
def test_install_removes_retired_skills_and_keeps_own_ones(vault, edition, prefix):
    config = replace(Config.load(vault), edition=edition, skill_prefix=prefix)
    for name in ("sleep2", "rem", "attribute", "my-routine"):
        folder = config.skills_path / (prefix + name)
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("Own content\n## Local\nKeep this\n")
    install.skills(config)
    for name in ("sleep2", "rem", "attribute"):
        assert not (config.skills_path / (prefix + name)).exists()
    assert (config.skills_path / (prefix + "image") / "SKILL.md").is_file()
    assert "Keep this" in (config.skills_path / (prefix + "my-routine") / "SKILL.md").read_text()
    assert "sleep2" not in ledger.NIGHT_STAGES
    assert not any("sleep2" in issue for issue in validate.checks(Vault(vault)))


@pytest.mark.parametrize("stage", ["before", "after"])
def test_night_scripts_pass_yesterday_and_stage_and_do_not_upgrade(vault, tmp_path, monkeypatch, stage):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("VAULT_REPO", str(vault.parent))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-01")
    scripts = {path.name: path for path in install.scripts()}
    command = tmp_path / "bin/brain"
    command.parent.mkdir()
    command.write_text('#!/bin/sh\nif [ "$1" = timezone ]; then echo America/New_York; else printf "%s\\n" "$*" >> "$AGENT_HOME/calls"; fi\n')
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(command.parent) + os.pathsep + os.environ["PATH"])
    subprocess.run(["bash", str(scripts[f"night-{stage}.sh"])], check=True)
    assert (home / "calls").read_text().splitlines() == [
        "pull", f"nightly --date 2026-10-31 --write --commit --push --stage {stage}"]
    for name in ("export-sessions.sh", "refresh.sh"):
        script = scripts[name].read_text()
        assert all(f"brain {cmd}" not in script for cmd in ("update", "migrate-check", "install-skills"))
    upgrade = scripts["upgrade.sh"].read_text()
    assert upgrade.index("brain update") < upgrade.index("brain migrate-check") < upgrade.index("brain install-skills")


@pytest.mark.parametrize("where", ["register", "warm", "archive"])
def test_task_ask_conversion_preserves_computed_id_and_removes_fields_once(vault, monkeypatch, capsys, where):
    from datetime import date
    from brain import items, needs, tasks
    config = Config.load(vault)
    today = date(2026, 11, 11)
    monkeypatch.setenv("BRAIN_TODAY", today.isoformat())
    task = tasks.find(config, "T-001")
    task.set_state("waiting_for_owner")
    task.body = ("- **Status:** Waiting for the venue decision\n"
                 "- **Ask:** Use the smaller venue?\n"
                 "- **Why:** The hall fits everyone.\n"
                 "- **Handed off:** 2026-11-10 · report in demiurge:task-thread\n"
                 "- **Latest evidence:** 2026-11-10 — shortlist ready\n")
    if where == "register":
        tasks.save_task(config, task, today)
    else:
        tasks.write_archive(config, task, "warm" if where == "warm" else "cold", today, whole=True)
        data, _ = fm.read(vault / "agent/Tasks.md")
        fm.write(vault / "agent/Tasks.md", data, "# Tasks\n" + (task.render_line(config) if where == "warm" else ""))
    old_source = "agent/Tasks.md#T-001" if where != "archive" else "agent/Tasks Archive/T-001.md"
    old_id = needs.Item("ask", "Use the smaller venue?", "task", "2026-11-10", old_source, label=task.title).id
    assert old_id == items.derived("task", old_source, task.title)
    marker = vault / config.runtime_dir / "task-asks-0.171.0"
    def installed_version(name):
        assert marker.exists() and needs.task_ask(config, "T-001").id == old_id
        return "0.171.0"
    monkeypatch.setattr(install, "installed_version", installed_version)
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    install.migrate_check(config)
    question, = needs.items(config, today)
    assert (question.id, question.text, question.detail, question.task, question.source, question.session) == (
        old_id, "Use the smaller venue?", "The hall fits everyone.", "T-001", "agent/Tasks#T-001", "demiurge:task-thread")
    body = tasks.find(config, "T-001").body
    assert "**Ask:**" not in body and "**Why:**" not in body
    assert "Waiting for the venue decision" in body and "shortlist ready" in body
    assert f"  - id: {old_id}" in fm.read(vault / "CURRENT.md")[1]
    assert capsys.readouterr().err.splitlines() == ["task ask: T-001: moved the question to Needs you"]
    before = snapshot(vault)
    install.migrate_check(config)
    assert snapshot(vault) == before and not capsys.readouterr().err
    task = tasks.find(config, "T-001")
    task.body += "- **Ask:** A later legacy question?\n"
    tasks.save_task(config, task, today)
    install.migrate_check(config)
    assert "**Ask:** A later legacy question?" in tasks.find(config, "T-001").body
    assert needs.task_ask(config, "T-001").id == old_id


@pytest.mark.parametrize("link", ["source", "same-text", "task-detail"])
def test_task_ask_conversion_reuses_an_existing_current_bullet(vault, monkeypatch, capsys, link):
    from datetime import date
    from brain import needs, tasks
    config = Config.load(vault)
    today = date(2026, 11, 11)
    task = tasks.find(config, "T-001")
    task.set_state("waiting_for_owner")
    task.body += "- **Ask:** Use the smaller venue?\n- **Why:** The task's reason.\n"
    tasks.save_task(config, task, today)
    source = "agent/Tasks#T-001" if link == "source" else "Trip"
    text = "Pick the hall or the garden?" if link == "source" else "Use the smaller venue?"
    original = (f"## Needs you\n- 2026-11-10 · ask · {text} · [[{source}]]\n"
                "  - why: The original reason.\n  - id: 123456abcdef\n")
    if link == "task-detail":
        original += "  - task: T-001\n"
    fm.write(vault / "CURRENT.md", {}, original)
    monkeypatch.setattr(install, "pending_migrations", lambda config: [])
    install.migrate_check(config)
    question, = needs.items(config, today)
    assert (question.id, question.text, question.detail, question.task, question.source) == (
        "123456abcdef", text, "The original reason.", "T-001", source)
    assert fm.read(vault / "CURRENT.md")[1] == original + ("  - task: T-001\n" if link != "task-detail" else "")
    assert "**Ask:**" not in tasks.find(config, "T-001").body
    assert "**Why:**" not in tasks.find(config, "T-001").body
    assert capsys.readouterr().err.splitlines() == ["task ask: T-001: moved the question to Needs you"]
