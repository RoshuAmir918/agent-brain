"""Declared routines and CLI edits."""
import json
from dataclasses import replace

import pytest

from brain import cli as brain_cli, fm, install, responsibilities, schedule, validate
from brain.config import Config
from brain.schedule import Routine
from brain.vault import Vault
from conftest import snapshot


@pytest.fixture
def config(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    return Config.load(vault)


def cli(config, *args):
    return brain_cli.main(["schedule", *args, "--vault", str(config.vault_path)])


def write(config, text):
    target = schedule.path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


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
responsibility = "chicken-alerts"
needs = ["computer"]

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
    assert keys == ["cron", "skills", "prompt", "deliver", "monitor", "script", "agent", "workdir", "enabled", "owner", "responsibility", "needs"]
    assert parsed["z-watch"].responsibility == "chicken-alerts"
    assert parsed["z-watch"].needs == ["computer"]
    assert parsed["a-once"].responsibility is None and parsed["a-once"].needs == []


def test_folder_routines_refuse_set_remove_and_keep_pause_through_sync(config):
    path = config.vault_path / "Responsibilities/alerts/RESPONSIBILITY.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {"version": 1, "asked_by": "Owner", "triggers": [
        {"kind": "schedule", "when": "every 1h"}, {"kind": "schedule", "when": "0 8 * * *"}]}, "Check.\n")
    responsibilities.sync(Vault(config.vault_path))
    before = schedule.path(config).read_bytes()
    for args in (("set", "alerts", "--every", "2h"), ("remove", "alerts-2"),
                 ("set", "alerts", "--responsibility", ""),
                 ("set", "alias", "--every", "2h", "--responsibility", "alerts")):
        with pytest.raises(SystemExit, match="edit Responsibilities/alerts/RESPONSIBILITY.md and run `brain responsibility sync`"):
            cli(config, *args)
        assert schedule.path(config).read_bytes() == before
    cli(config, "pause", "alerts")
    data, body = fm.read(path)
    data["triggers"][0]["when"] = "every 2h"
    fm.write(path, data, body)
    responsibilities.sync(Vault(config.vault_path))
    assert schedule.load(config)["alerts"].enabled is False
    assert schedule.load(config)["alerts"].every == "2h"
    cli(config, "resume", "alerts")
    assert responsibilities.sync(Vault(config.vault_path)) == []
    assert schedule.load(config)["alerts"].enabled is True


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
    ('[routines.x]\nevery="1h"\nneeds="computer"', 'list of strings'),
    ('[routines.x]\nevery="1h"\nneeds=[1]', 'list of strings'),
    ('[routines.x]\nevery="1h"\nresponsibility=1', 'must be a string'),
    *[(f'[routines.x]\nevery="1h"\nresponsibility="{value}"', 'must match')
      for value in ('', 'Bad_ID', '-bad', 'a' * 64)],
    *[(f'[routines.x]\nevery="1h"\n{key}=1', f'{key}: must be a string')
      for key in ('prompt', 'deliver', 'monitor', 'script', 'workdir')],
    *[(f'[routines.x]\nevery="1h"\n{key}="false"', f'{key}: must be a boolean')
      for key in ('agent', 'enabled', 'owner')],
    ('[routines.x]\nevery="1h"\ndeliver="email"', 'local, demiurge or demiurge:<id>'),
    ('[routines.x]\nevery="1h"\ndeliver="demiurge:"', 'local, demiurge or demiurge:<id>'),
    *[(f'[routines.x]\nevery="1h"\n{key}=""', f'{key}: must be a nonempty string')
      for key in ('monitor', 'script', 'workdir')],
    ('[routines.x]\nevery="1h"\nagent=false', 'agent=false requires script'),
    ('[routines.x]\nafter="missing"', "no routine named 'missing'"),
    ('[routines.x]\nafter="x"', 'leads back to x'),
    ('[routines.x]\nafter="y"\n[routines.y]\nafter="x"', 'leads back to'),
    ('[routines.x]\nafter="y"\ncron="* * * * *"\n[routines.y]\nevery="1h"', 'exactly one'),
])
def test_validation_problems(config, source, expected):
    write(config, source)
    assert expected in "\n".join(schedule.problems(config))
    with pytest.raises(ValueError):
        schedule.load(config)


def test_unknown_need_and_responsibility_are_strict_only_in_check_and_set(config, capsys, monkeypatch):
    # An unknown id may be declared before its hub is created.
    cli(config, "set", "alerts", "--every", "1h", "--responsibility", "unknown")
    assert schedule.load(config)["alerts"].responsibility == "unknown"
    write(config, '[routines.alerts]\nevery="1h"\nresponsibility="unknown"\nneeds=["phone"]\n[routines.y]\nevery="1h"\n')
    assert "unknown need" in "\n".join(schedule.problems(config))
    assert schedule.load(config)["alerts"].needs == ["phone"]
    cli(config, "pause", "y")
    assert not schedule.load(config)["y"].enabled
    vault = Vault(config.vault_path)
    assert validate.schedule_hygiene(vault) == []
    warnings = validate.schedule_warnings(vault)
    assert len(warnings) == 2
    assert "unknown responsibility 'unknown'" in warnings[0]
    assert "unknown need 'phone'" in warnings[1]
    monkeypatch.setattr(validate, "checks", lambda vault: validate.schedule_hygiene(vault))
    monkeypatch.setattr(validate, "work_warnings", lambda vault: [])
    monkeypatch.setattr(validate, "deliverable_warnings", lambda vault: [])
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        brain_cli.main(["validate", "--vault", str(config.vault_path)])
    assert error.value.code == 0
    assert capsys.readouterr().out.count("warning:") == 2


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
                                  ("set", "watch", "--needs", "computer,phone"),
                                  ("set", "watch", "--responsibility", "Bad_ID"),
                                  ("pause", "missing"), ("resume", "missing")])
def test_invalid_mutation_never_writes(config, args):
    with pytest.raises(SystemExit):
        cli(config, *args)
    assert not schedule.path(config).exists()
    schedule.save(config, {"watch": Routine(every="15m")})
    before = schedule.path(config).read_bytes()
    with pytest.raises(SystemExit) as error:
        cli(config, *args)
    assert error.value.code
    assert schedule.path(config).read_bytes() == before


def test_cli_responsibility_and_needs_set_merge_list_clear(config, capsys):
    cli(config, "set", "alerts", "--every", "1h", "--responsibility", "chicken-alerts", "--needs", "computer")
    cli(config, "set", "alerts", "--prompt", "Check alerts")
    capsys.readouterr()
    cli(config, "list", "--json")
    row = json.loads(capsys.readouterr().out)["alerts"]
    assert row["responsibility"] == "chicken-alerts" and row["needs"] == ["computer"]
    assert schedule.load(config)["alerts"].prompt == "Check alerts"
    cli(config, "set", "pings", "--every", "2h")
    capsys.readouterr()
    cli(config, "list", "--json")
    assert json.loads(capsys.readouterr().out)["pings"]["prompt"] == "Run pings."
    cli(config, "set", "alerts", "--responsibility", "", "--needs", "")
    routine = schedule.load(config)["alerts"]
    assert routine.responsibility is None and routine.needs == []
    text = schedule.path(config).read_text()
    assert "responsibility =" not in text and "needs =" not in text


def test_cli_set_clears_monitor_script_workdir_and_owner(config, capsys):
    cli(config, "set", "alerts", "--every", "1h", "--monitor", "watch.sh", "--script", "run.sh", "--workdir", "/tmp",
        "--owner")
    cli(config, "set", "alerts", "--monitor", "", "--script", "", "--workdir", "", "--no-owner")
    routine = schedule.load(config)["alerts"]
    assert (routine.monitor, routine.script, routine.workdir, routine.owner) == (None, None, None, False)


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


def test_save_omits_default_workdir_and_preserves_unicode_and_controls(config):
    with pytest.raises(ValueError):
        schedule.save(config, {"new": Routine()})
    assert not schedule.path(config).exists()
    routine = Routine(every="1h", workdir=str(config.repo), prompt="Café 🌙\x7f\nA new line")
    schedule.save(config, {"watch": routine})
    assert "workdir" not in schedule.path(config).read_text()
    assert schedule.load(config)["watch"] == replace(routine, workdir=None)


def test_failed_atomic_replace_preserves_file_and_removes_temporary(config, monkeypatch):
    schedule.save(config, {"watch": Routine(every="1h")})
    before = schedule.path(config).read_bytes()
    files_before = set(schedule.path(config).parent.iterdir())

    def fail_replace(source, target):
        assert source.parent == target.parent
        assert source != target and source.read_text().startswith(schedule.HEADER)
        assert target.read_bytes() == before
        raise OSError("replace failed")

    monkeypatch.setattr(fm.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        schedule.save(config, {"watch": Routine(every="2h")})
    assert schedule.path(config).read_bytes() == before
    assert set(schedule.path(config).parent.iterdir()) == files_before


@pytest.mark.parametrize("args", [("set", "watch", "--every", "2h"), ("init", "--reset-night")])
def test_cli_holds_vault_lock_across_read_edit_and_write(config, monkeypatch, args):
    from contextlib import contextmanager

    held = False
    original_load, original_save = schedule.load, schedule.save

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

    original_save(config, {"watch": Routine(every="1h")})
    monkeypatch.setattr(brain_cli, "vault_lock", lock)
    monkeypatch.setattr(schedule, "load", load)
    monkeypatch.setattr(schedule, "save", save)
    cli(config, *args)
    assert not held
    if args[0] == "set":
        assert original_load(config)["watch"].every == "2h"
    else:
        assert original_load(config)["night-after"].after == "image"


def test_a_migration_with_a_schedule_is_a_one_shot_named_for_its_version_and_done_removes_it(config, monkeypatch):
    schedule.save(config, {"watch": schedule.Routine(cron="*/15 * * * *", skills=["brain-watch"])})
    monkeypatch.setattr(install, "installed_version", lambda name: "0.30.0")
    from conftest import START_MIGRATION   # the real one; a shared fixture stubs it so no test makes cron jobs
    assert START_MIGRATION(config)
    routines = schedule.load(config)
    run = routines["brain-migration-0-30-0"]
    assert run.at and run.prompt == install.MIGRATION_RUN and "watch" in routines
    install.migrate_done(config)
    assert set(schedule.load(config)) == {"watch"}


def test_a_newer_release_leaves_a_queued_migration_to_do_the_whole_pending_file(config, monkeypatch):
    from conftest import START_MIGRATION
    schedule.save(config, {"watch": schedule.Routine(cron="*/15 * * * *", skills=["brain-watch"])})
    monkeypatch.setattr(install, "installed_version", lambda name: "0.66.0")
    assert START_MIGRATION(config)
    first = schedule.load(config)["brain-migration-0-66-0"]
    monkeypatch.setattr(install, "installed_version", lambda name: "0.68.0")
    assert START_MIGRATION(config)
    routines = schedule.load(config)
    assert set(routines) == {"watch", "brain-migration-0-66-0"} and routines["brain-migration-0-66-0"].at == first.at
    install.migrate_done(config)
    assert START_MIGRATION(config) and "brain-migration-0-68-0" in schedule.load(config)


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_every_default_script_and_monitor_is_installed(config, edition):
    installed = {path.name for path in install.scripts()}
    for name, routine in schedule.defaults(replace(config, edition=edition)).items():
        for script in (routine.script, routine.monitor):
            assert script is None or script in installed, (name, script)


def test_after_chains_from_a_timed_routine_and_round_trips(config, capsys):
    cli(config, "set", "close", "--cron", "30 0 * * *", "--script", "export-sessions.sh", "--no-agent")
    cli(config, "set", "before", "--after", "close", "--script", "nightly-before.sh", "--no-agent")
    cli(config, "set", "remember", "--after", "before", "--skills", "sleep1")
    assert schedule.problems(config) == []
    routines = schedule.load(config)
    assert routines["remember"].after == "before" and routines["remember"].cron is None
    assert schedule.render(routines) == schedule.path(config).read_text()
    capsys.readouterr()
    cli(config, "list", "--json")
    listed = json.loads(capsys.readouterr().out)
    assert listed["before"]["after"] == "close" and listed["close"]["after"] is None
    cli(config, "set", "remember", "--cron", "30 2 * * *")   # a time replaces after
    assert schedule.load(config)["remember"].after is None
    with pytest.raises(SystemExit):
        cli(config, "remove", "close")   # before would follow nothing
    assert "close" in schedule.load(config)


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_default_chains_have_installed_skills_and_valid_predecessors(vault, edition):
    config = replace(Config.load(vault), edition=edition, skill_prefix="own-")
    routines = schedule.defaults(config)
    chain = ["session-export", "attribution", "night-before", "sleep1"]
    if edition == "personal":
        chain.append("people")
    chain += ["image", "night-after"]
    assert routines[chain[0]].cron
    for predecessor, name in zip(chain, chain[1:]):
        assert routines[name].after == predecessor
        assert routines[name].cron is None
    assert routines["sleep1"].skills == ["own-sleep1"]
    assert routines["image"].skills == ["own-image", "own-portrait"]
    for name in ("session-export", "attribution", "night-before", "night-after", "upgrade"):
        assert not routines[name].agent and routines[name].script == name.replace("session-export", "export-sessions") + ".sh"
    assert not {"sleep2", "rem", "continue", "continue-daily"} & routines.keys()
    if edition == "personal":
        assert routines["people"].skills == ["own-people"]
        assert routines["prime"].after == "improve"
    else:
        assert not {"people", "prime", "morning"} & routines.keys()
    schedule.save(config, routines)
    assert schedule.problems(config) == []
    assert schedule.load(config) == routines
    install.skills(config)
    for routine in routines.values():
        for skill in routine.skills:
            assert (config.skills_path / skill / "SKILL.md").is_file()


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_reset_night_keeps_own_routines_and_three_clocks_and_is_idempotent(vault, edition, capsys):
    (vault.parent / "brain.toml").write_text(f'[brain]\nedition="{edition}"\nskill_prefix="own-"\n')
    config = Config.load(vault)
    own = {
        "study-decks": schedule.Routine(cron="40 3 * * *", skills=["own-decks"], owner=True, enabled=False),
        "fantasy-football": schedule.Routine(every="2h", prompt="Check the lineup", deliver="demiurge:football"),
        "watch": schedule.Routine(cron="0 9-21 * * *", skills=["own-watch"], monitor="custom.sh"),
        "explore": schedule.Routine(cron="0 10,15 * * *", prompt="Custom work"),
        "context-refresh": schedule.Routine(cron="25 * * * *", script="refresh.sh", agent=False),
    }
    clocks = {"session-export": "30 3 * * *", "improve": "20 7 * * *"}
    if edition == "personal":
        clocks["morning"] = "0 11 * * *"
    old = own | {name: schedule.Routine(cron=cron, prompt="Old prompt", enabled=False) for name, cron in clocks.items()}
    retired = ("sleep2", "rem", "brain-attribute", "continue", "continue-daily")
    old |= {name: schedule.Routine(every="1h") for name in retired}
    old["sleep1"] = schedule.Routine(cron="30 5 * * *", skills=["old-sleep1"])
    schedule.save(config, old)
    before_notes = {path: path.read_bytes() for path in vault.rglob("*.md")}
    brain_cli.main(["schedule", "init", "--reset-night", "--vault", str(vault)])
    output = capsys.readouterr().out
    assert "reset sleep1" in output and "added image" in output and "removed sleep2" in output
    actual = schedule.load(config)
    assert {name: actual[name] for name in own} == own
    assert not set(retired) & actual.keys()
    defaults = schedule.defaults(config)
    for name in set(defaults) - own.keys() - {"evening"}:
        expected = replace(defaults[name], cron=clocks[name]) if name in clocks else defaults[name]
        assert actual[name] == expected
    assert all(path.read_bytes() == content for path, content in before_notes.items())
    assert schedule.problems(config) == []
    before = snapshot(vault)
    brain_cli.main(["schedule", "init", "--reset-night", "--vault", str(vault)])
    assert snapshot(vault) == before
    assert capsys.readouterr().out == "night routines already match defaults\n"
