"""Declared routines, CLI edits and imports from both runtimes."""
import json
from dataclasses import replace

import pytest

from brain import nightly, runtime, schedule, validate
from brain.config import Config
from brain.schedule import Routine
from brain.vault import Vault


@pytest.fixture
def config(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    return Config.load(vault)


def cli(config, *args):
    return nightly.main(["schedule", *args, "--vault", str(config.vault_path)])


def write(config, text):
    target = schedule.path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def jobs(config, values, harness=True):
    folder = runtime.home() / ("harness" if harness else "cron")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "jobs.json").write_text(json.dumps({"jobs": values}), encoding="utf-8")


def test_path_sits_beside_engine_version_in_the_runtime_directory(config, capsys):
    config = replace(config, hermes_dir="custom/Runtime")
    assert schedule.path(config) == config.vault_path / "custom/Runtime/schedule.toml"
    (config.repo / "brain.toml").write_text('[brain]\nhermes_dir = "custom/Runtime"\n')
    nightly.main(["paths", "schedule", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out == str(schedule.path(config)) + "\n"


def test_parse_render_round_trip_and_defaults_are_stable(config):
    source = '''[routines.z-watch]
cron = "*/15 6-22 * * MON-FRI"
skills = ["brain-watch", "with space"]
prompt = "Line one\\nLine two: \\"quoted\\""
deliver = "demiurge:conversation-1"
monitor = "watch-signal.sh"
script = "before.sh"
agent = false
workdir = "/somewhere else"
enabled = false
owner = true

[routines.a-once]
at = "2026-11-05T06:30:00-05:00"

[routines.middle]
every = "2h"
prompt = "Run middle."
deliver = "local"
agent = true
enabled = true
owner = false
skills = []
'''
    write(config, source)
    routines = schedule.load(config)
    assert routines["z-watch"].prompt == 'Line one\nLine two: "quoted"'
    schedule.save(config, routines)
    text = schedule.path(config).read_text()
    assert text.startswith(schedule.HEADER + "\n[routines.a-once]\n")
    assert text.index("a-once") < text.index("middle") < text.index("z-watch")
    assert '[routines.middle]\nevery = "2h"\n\n' in text
    assert "prompt = \"Run middle.\"" not in text
    assert schedule.problems(config) == []
    parsed = schedule.load(config)
    assert replace(routines["middle"], prompt=None) == parsed["middle"]
    assert routines["z-watch"] == parsed["z-watch"]
    schedule.save(config, parsed)
    assert schedule.path(config).read_text() == text
    keys = [line.split(" = ")[0] for line in text.split('[routines.z-watch]\n')[1].splitlines()]
    assert keys == ["cron", "skills", "prompt", "deliver", "monitor", "script", "agent", "workdir", "enabled", "owner"]


@pytest.mark.parametrize("source, expected", [
    ('[routines.x', "Expected ']'"),
    ('[routines.x]\nevery="1h"\n[routines.x]\nevery="2h"', 'Cannot declare'),
    ('[routines.x]\nevery="1h"\nevery="2h"', 'Cannot overwrite'),
    ('unknown=1', 'only [routines'),
    ('routines=[]', 'routine tables'),
    ('[routines]\nx=1', 'must be a table'),
    ('[routines."Bad_Name"]\nevery="1h"', 'name must match'),
    ('[routines.x]\nextra=1\nevery="1h"', 'unknown field extra'),
    ('[routines.x]', 'exactly one'),
    ('[routines.x]\nevery="1h"\ncron="* * * * *"', 'exactly one'),
    ('[routines.x]\ncron=1', 'must be a string'),
    ('[routines.x]\ncron="* * * *"', 'five-field'),
    ('[routines.x]\ncron="99 * * * *"', 'five-field'),
    ('[routines.x]\ncron="*/0 * * * *"', 'five-field'),
    ('[routines.x]\ncron="nonsense * * * *"', 'five-field'),
    ('[routines.x]\nevery="0m"', '<n>m or <n>h'),
    ('[routines.x]\nevery="1d"', '<n>m or <n>h'),
    ('[routines.x]\nevery="1.5h"', '<n>m or <n>h'),
    ('[routines.x]\nat="2026-11-05"', 'ISO datetime with an offset'),
    ('[routines.x]\nat="2026-11-05T12:00:00"', 'ISO datetime with an offset'),
    ('[routines.x]\nat="tomorrow"', 'ISO datetime with an offset'),
    ('[routines.x]\nevery="1h"\nskills="x"', 'list of strings'),
    ('[routines.x]\nevery="1h"\nskills=[1]', 'list of strings'),
    *[(f'[routines.x]\nevery="1h"\n{key}=1', f'{key}: must be a string')
      for key in ('prompt', 'deliver', 'monitor', 'script', 'workdir')],
    *[(f'[routines.x]\nevery="1h"\n{key}="false"', f'{key}: must be a boolean')
      for key in ('agent', 'enabled', 'owner')],
    ('[routines.x]\nevery="1h"\ndeliver="email"', 'local, demiurge or demiurge:<id>'),
    ('[routines.x]\nevery="1h"\ndeliver="demiurge:"', 'local, demiurge or demiurge:<id>'),
    *[(f'[routines.x]\nevery="1h"\n{key}=""', f'{key}: must be a nonempty string')
      for key in ('monitor', 'script', 'workdir')],
    ('[routines.x]\nevery="1h"\nagent=false', 'agent=false requires script'),
])
def test_validation_problems(config, source, expected):
    write(config, source)
    assert expected in "\n".join(schedule.problems(config))
    with pytest.raises(ValueError):
        schedule.load(config)


def test_all_problems_are_reported(config):
    write(config, '[routines.x]\nevery="0h"\nagent=false\nowner=1\nunknown=true\n')
    assert len(schedule.problems(config)) == 4


def test_absent_schedule_is_empty_and_valid(config):
    assert schedule.load(config) == {}
    assert schedule.problems(config) == []


def test_cli_create_merge_change_timing_pause_resume_remove(config, capsys):
    cli(config, "set", "watch", "--cron", "*/15 6-22 * * *", "--skills", "brain-watch, other", "--owner", "--deliver", "demiurge")
    assert "created routine watch" in capsys.readouterr().out
    cli(config, "set", "watch", "--prompt", "Check things", "--monitor", "watch-signal.sh")
    watch = schedule.load(config)["watch"]
    assert watch.cron == "*/15 6-22 * * *" and watch.skills == ["brain-watch", "other"]
    assert watch.owner and watch.deliver == "demiurge" and watch.prompt == "Check things"
    cli(config, "set", "watch", "--every", "30m", "--disabled")
    watch = schedule.load(config)["watch"]
    assert watch.cron is None and watch.every == "30m" and watch.monitor == "watch-signal.sh" and not watch.enabled
    cli(config, "resume", "watch")
    assert schedule.load(config)["watch"].enabled
    cli(config, "pause", "watch")
    assert not schedule.load(config)["watch"].enabled
    cli(config, "set", "watch", "--enabled", "--skills", "", "--script", "watch.sh", "--no-agent", "--workdir", "/tmp")
    watch = schedule.load(config)["watch"]
    assert watch.enabled and not watch.skills and not watch.agent and watch.script == "watch.sh" and watch.workdir == "/tmp"
    cli(config, "set", "watch", "--agent", "--at", "2026-11-05T12:00:00Z")
    watch = schedule.load(config)["watch"]
    assert watch.agent and watch.every is None and watch.at == "2026-11-05T12:00:00Z"
    cli(config, "list")
    assert "watch: at 2026-11-05T12:00:00Z" in capsys.readouterr().out
    cli(config, "list", "--json")
    assert json.loads(capsys.readouterr().out)["watch"]["owner"]
    cli(config, "remove", "watch")
    assert schedule.load(config) == {}


@pytest.mark.parametrize("args", [("set", "new"), ("set", "bad_name", "--every", "1h"),
                                  ("set", "watch", "--no-agent"), ("set", "watch", "--every", "0m"),
                                  ("set", "watch", "--deliver", "slack"), ("remove", "missing"),
                                  ("pause", "missing"), ("resume", "missing")])
def test_invalid_mutation_never_writes(config, args):
    schedule.save(config, {"watch": Routine(every="15m")})
    before = schedule.path(config).read_bytes()
    with pytest.raises(SystemExit) as error:
        cli(config, *args)
    assert error.value.code
    assert schedule.path(config).read_bytes() == before


def test_invalid_create_never_creates_file(config):
    with pytest.raises(SystemExit):
        cli(config, "set", "new")
    assert not schedule.path(config).exists()
    with pytest.raises(ValueError):
        schedule.save(config, {"new": Routine()})
    assert not schedule.path(config).exists()


def test_mutations_run_problems_after_writing(config, monkeypatch):
    checked = []
    original = schedule.problems
    monkeypatch.setattr(schedule, "problems", lambda cfg: checked.append(schedule.load(cfg)) or original(cfg))
    cli(config, "set", "watch", "--every", "15m")
    cli(config, "pause", "watch")
    cli(config, "resume", "watch")
    cli(config, "remove", "watch")
    jobs(config, [])
    cli(config, "import", "--force")
    assert len(checked) == 5


def test_check_exit_codes_and_invalid_file_not_rewritten(config, capsys):
    with pytest.raises(SystemExit) as error:
        cli(config, "check")
    assert error.value.code == 0 and capsys.readouterr().out == "0 problems\n"
    target = write(config, '[routines.x]\nevery="0h"')
    before = target.read_bytes()
    with pytest.raises(SystemExit) as error:
        cli(config, "check")
    assert error.value.code == 1 and "1 problems" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli(config, "set", "x", "--every", "1h")
    assert target.read_bytes() == before


@pytest.mark.parametrize("harness", [True, False])
def test_import_both_runtime_formats_and_force(config, capsys, harness):
    values = [
        {"name": "watch", "schedule": {"kind": "cron", "expr": "*/15 6-22 * * *"}, "skills": ["brain-watch"],
         "prompt": "Run watch.", "deliver": "demiurge", "monitor_script": "watch-signal.sh", "workdir": str(config.repo)},
        {"name": "continue", "schedule": {"kind": "interval", "minutes": 30}, "prompt": "Run continue."},
        {"name": "continue-daily", "schedule": {"kind": "cron", "expr": "0 12 * * *"}, "prompt": "Run continue daily.",
         "skills": ["brain-continue"], "monitor_script": "continue-daily-signal.sh", "enabled": False},
        {"name": "prime", "schedule": {"kind": "cron", "expr": "30 6 * * *"}, "skills": ["brain-prime"]},
        {"name": "demiurge-sync", "schedule": {"kind": "interval", "minutes": 120}, "script": "demiurge-sync.sh", "no_agent": True},
        {"name": "once", "schedule": {"kind": "once", "at": "2026-11-05T06:30:00-05:00"}, "prompt": "Ask the questions", "workdir": "/elsewhere"},
        {"name": "finished", "schedule": {"kind": "once", "at": "2026-11-04T12:00:00Z"}, "enabled": False, "last_run_at": "2026-11-04T12:00:00Z"},
        {"name": "brain-migration", "schedule": {"kind": "once", "at": "2026-11-05T12:00:00Z"}},
    ]
    if harness:
        for job in values:
            for snake, camel in (("monitor_script", "monitorScript"), ("no_agent", "noAgent"), ("last_run_at", "lastRunAt")):
                if snake in job:
                    job[camel] = job.pop(snake)
    else:
        values[3]["skill"] = values[3].pop("skills")[0]
        for job in values:
            if job["schedule"]["kind"] == "once":
                job["schedule"] = {"kind": "once", "run_at": job["schedule"]["at"], "display": "one time"}
        values[6].pop("enabled")
        values[6]["state"] = "completed"
    jobs(config, values, harness=harness)
    cli(config, "import")
    assert "imported 6 routine(s)" in capsys.readouterr().out
    found = schedule.load(config)
    assert set(found) == {"watch", "continue", "continue-daily", "prime", "demiurge-sync", "once"}
    assert found["watch"] == Routine(cron="*/15 6-22 * * *", skills=["brain-watch"], deliver="demiurge", monitor="watch-signal.sh")
    assert found["continue"].every == "30m" and found["continue"].prompt is None
    assert found["continue-daily"].prompt == "Run continue daily." and not found["continue-daily"].enabled
    assert found["prime"].skills == ["brain-prime"]
    assert found["demiurge-sync"] == Routine(every="2h", script="demiurge-sync.sh", agent=False)
    assert found["once"].at == "2026-11-05T06:30:00-05:00" and found["once"].workdir == "/elsewhere"
    text = schedule.path(config).read_bytes()
    with pytest.raises(SystemExit, match="--force"):
        cli(config, "import")
    assert schedule.path(config).read_bytes() == text
    cli(config, "import", "--force")
    assert schedule.path(config).read_bytes() == text


def test_import_keeps_paused_unrun_once_and_repeating_migration(config):
    jobs(config, [{"name": "waiting", "schedule": {"kind": "once", "at": "2026-11-05T12:00:00Z"}, "enabled": False},
                  {"name": "brain-migration", "schedule": {"kind": "interval", "minutes": 15}}])
    cli(config, "import")
    assert set(schedule.load(config)) == {"waiting", "brain-migration"}
    assert not schedule.load(config)["waiting"].enabled


@pytest.mark.parametrize("values", [
    [{"name": "same", "schedule": {"kind": "interval", "minutes": 5}}] * 2,
    [{"name": "unknown", "schedule": {"kind": "bad"}}],
    [{"name": "invalid", "schedule": {"kind": "cron", "expr": "bad"}}],
    [{"name": "invalid", "schedule": {"kind": "interval", "minutes": 0}}],
    [{"name": "invalid", "schedule": {"kind": "once"}}],
    [{"name": "invalid", "schedule": {"kind": "interval", "minutes": 5}, "noAgent": True}],
])
def test_invalid_import_does_not_replace_existing_file(config, values):
    schedule.save(config, {"original": Routine(every="1h")})
    before = schedule.path(config).read_bytes()
    jobs(config, values)
    with pytest.raises(SystemExit):
        cli(config, "import", "--force")
    assert schedule.path(config).read_bytes() == before


def test_schedule_hygiene_and_integration(config):
    vault = Vault(config.vault_path)
    assert validate.schedule_hygiene(vault) == []
    jobs(config, [{"name": "watch"}])
    missing = validate.schedule_hygiene(vault)
    assert len(missing) == 1 and "brain schedule import" in missing[0]
    assert missing[0] in validate.hygiene(vault)
    write(config, '[routines.x]\nevery="0m"')
    assert validate.schedule_hygiene(vault) == schedule.problems(config)
    assert "brain schedule import" not in "\n".join(validate.schedule_hygiene(vault))
    schedule.save(config, {"x": Routine(every="1m")})
    assert validate.schedule_hygiene(vault) == []


def test_save_omits_default_workdir_and_preserves_unicode_and_controls(config):
    routine = Routine(every="1h", workdir=str(config.repo), prompt="Café 🌙\x7f\nA new line")
    schedule.save(config, {"watch": routine})
    assert "workdir" not in schedule.path(config).read_text()
    assert schedule.load(config)["watch"] == replace(routine, workdir=None)


def test_failed_atomic_replace_preserves_file_and_removes_temporary(config, monkeypatch):
    from pathlib import Path

    schedule.save(config, {"watch": Routine(every="1h")})
    before = schedule.path(config).read_bytes()
    files_before = set(schedule.path(config).parent.iterdir())

    def fail_replace(source, target):
        assert source.parent == target.parent
        assert source != target and source.read_text().startswith(schedule.HEADER)
        assert target.read_bytes() == before
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        schedule.save(config, {"watch": Routine(every="2h")})
    assert schedule.path(config).read_bytes() == before
    assert set(schedule.path(config).parent.iterdir()) == files_before


def test_cli_holds_vault_lock_across_read_edit_and_write(config, monkeypatch):
    from contextlib import contextmanager

    held = False
    original_load, original_save = schedule.load, schedule._save

    @contextmanager
    def lock(cfg):
        nonlocal held
        assert cfg == config and not held
        held = True
        try:
            yield
        finally:
            held = False

    def load(cfg):
        assert held
        return original_load(cfg)

    def save(cfg, routines):
        assert held
        return original_save(cfg, routines)

    monkeypatch.setattr(nightly, "vault_lock", lock)
    monkeypatch.setattr(schedule, "vault_lock", lock)
    monkeypatch.setattr(schedule, "load", load)
    monkeypatch.setattr(schedule, "_save", save)
    schedule.save(config, {"watch": Routine(every="1h")})
    cli(config, "set", "watch", "--every", "2h")
    assert not held and original_load(config)["watch"].every == "2h"


def test_import_omits_vault_workdir_through_symlink(config, tmp_path):
    alias = tmp_path / "vault-alias"
    alias.symlink_to(config.repo, target_is_directory=True)
    jobs(config, [{"name": "watch", "schedule": {"kind": "interval", "minutes": 15}, "workdir": str(alias)}])
    cli(config, "import")
    assert schedule.load(config)["watch"].workdir is None
    assert "workdir" not in schedule.path(config).read_text()


def test_a_migration_with_a_schedule_is_a_one_shot_named_for_its_version_and_done_removes_it(config, monkeypatch):
    from brain import install
    schedule.save(config, {"watch": schedule.Routine(cron="*/15 * * * *", skills=["brain-watch"])})
    monkeypatch.setattr(install, "installed_version", lambda name: "0.30.0")
    from conftest import START_MIGRATION   # the real one; a shared fixture stubs it so no test makes cron jobs
    assert START_MIGRATION(config)
    routines = schedule.load(config)
    run = routines["brain-migration-0-30-0"]
    assert run.at and run.prompt == install.MIGRATION_RUN and "watch" in routines
    install.migrate_done(config)
    assert set(schedule.load(config)) == {"watch"}
