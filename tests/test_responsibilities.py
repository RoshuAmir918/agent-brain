"""Responsibility ids, schedule ownership and state compatibility."""
import io
import json
import shutil
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files

import pytest

from brain import cli, compile_context, fm, install, matters, nightly, responsibilities, schedule, session_context, state, validate
from brain.config import Config
from brain.vault import Vault


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def hub(root, title, **data):
    path = root / f"{title}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"kind": "hub", "tier": "hot", **data}, f"# {title}\n")
    return path


@pytest.mark.parametrize("title, data, expected", [
    ("Ignored", {"records": "  Chicken__ALERTS! ", "skill": "own-other"}, "chicken-alerts"),
    ("Ignored", {"skill": "own-Chicken_Alerts"}, "chicken-alerts"),
    (" Chicken & Alerts! ", {}, "chicken-alerts"),
    ("Title", {"records": "", "skill": "own-"}, "title"),
    ("123 Numbers", {}, "123-numbers"),
    ("A" * 70, {}, "a" * 63),
    ("A" * 62 + " and more", {}, "a" * 62),
])
def test_identity_derivation_and_normalization(vault, title, data, expected):
    (vault.parent / "brain.toml").write_text('[brain]\nskill_prefix="own-"\n')
    path = hub(vault, title, **data)
    loaded = Vault(vault)
    assert responsibilities.identity(loaded.config, loaded.notes[path.name]) == expected


def test_validate_duplicate_ids_names_both_hubs_and_invalid_slug(vault):
    hub(vault, "First", records="Chicken_alerts", skill="brain-first")
    hub(vault, "Second", records="chicken-alerts", skill="brain-second")
    hub(vault, "!!!", skill="!!!")
    problems = validate.responsibilities(Vault(vault))
    assert "duplicate responsibility id 'chicken-alerts': First.md and Second.md" in problems
    assert any("!!!.md: responsibility id must match" in problem for problem in problems)
    assert set(problems) <= set(validate.checks(Vault(vault)))


def test_state_responsibilities_come_from_schedule_without_ledger(vault):
    config = Config.load(vault)
    hub(vault, "Chicken alerts", skill="brain-chicken-alerts", records="chicken-alerts")
    schedule.save(config, {"chicken-alerts": schedule.Routine(every="1h")})
    snapshot = state.build(config, date(2026, 11, 6))
    assert snapshot["responsibilities"] == [
        {"id": "chicken-alerts", "title": "Chicken alerts", "hub": "Chicken alerts.md",
         "skill": "brain-chicken-alerts", "records": "chicken-alerts", "routines": ["chicken-alerts"],
         "needs": [], "work": None, "delivers": None, "folder": False, "path": "memory/Chicken alerts.md"}]
    assert snapshot["routines"] == [
        {"name": "chicken-alerts", "skill": "brain-chicken-alerts", "hub": "Chicken alerts", "ledger": None,
         "responsibility": "chicken-alerts"}]


def test_state_explicit_overrides_fallback_and_unions_needs(vault):
    config = Config.load(vault)
    hub(vault, "Alpha", skill="brain-alpha", records="alpha", work="living-work", delivers="A weekly report")
    hub(vault, "Beta", skill="brain-beta", records="beta")
    hub(vault, "Gamma", skill="brain-gamma", records="gamma")
    schedule.save(config, {
        "alpha": schedule.Routine(every="1h", skills=["brain-alpha"], responsibility="beta", needs=["computer"]),
        "alpha-explicit": schedule.Routine(every="1h", responsibility="alpha", needs=["computer"]),
        "alpha-extra": schedule.Routine(every="1h", responsibility="alpha", needs=["computer"]),
        "alpha-by-skill": schedule.Routine(every="1h", skills=["brain-alpha"]),
        "beta": schedule.Routine(every="1h", skills=["brain-beta"]),
        "gamma": schedule.Routine(every="1h"),
        "gamma-extra": schedule.Routine(every="1h", skills=["brain-gamma"], needs=["computer"], enabled=False),
        "unknown": schedule.Routine(every="1h", skills=["brain-gamma"], responsibility="missing"),
    })
    # The ledger spelling and all existing routine fields survive the schedule join.
    ledger = vault / "agent/Alpha/2026-11.md"
    ledger.parent.mkdir(parents=True)
    fm.write(ledger, {"kind": "log"}, "2026-11-06 | ALPHA | done | Checked. | Wait.\n")
    snapshot = state.build(config, date(2026, 11, 6))
    entries = {entry["id"]: entry for entry in snapshot["responsibilities"]}
    assert entries["alpha"]["routines"] == ["alpha-explicit", "alpha-extra"]
    assert entries["alpha"]["needs"] == ["computer"]
    assert entries["alpha"]["work"] == "living-work" and entries["alpha"]["delivers"] == "A weekly report"
    assert entries["beta"]["routines"] == ["alpha"]
    assert entries["beta"]["needs"] == ["computer"]
    assert entries["gamma"]["routines"] == ["gamma", "gamma-extra"]
    assert entries["gamma"]["needs"] == ["computer"]
    row = next(row for row in snapshot["routines"] if row["name"] == "ALPHA")
    assert row == {"name": "ALPHA", "skill": "brain-alpha", "hub": "Alpha", "ledger": "agent/Alpha", "responsibility": "beta"}
    names = [name for entry in entries.values() for name in entry["routines"]]
    assert len(names) == len(set(names))
    assert not {"alpha-by-skill", "beta", "unknown"} & set(names)


def test_fallback_name_wins_over_shared_skill_and_ties_use_hub_path(vault):
    config = Config.load(vault)
    hub(vault, "A first", skill="brain-shared", records="first")
    hub(vault, "B second", skill="brain-shared", records="second")
    schedule.save(config, {"second": schedule.Routine(every="1h", skills=["brain-shared"]),
                           "shared": schedule.Routine(every="1h", skills=["brain-shared"])})
    entries = state.build(config, date(2026, 11, 6))["responsibilities"]
    assert [entry["routines"] for entry in entries] == [["shared"], ["second"]]


def test_discovery_includes_hubs_with_a_skill_but_not_title_only_hubs_folder_readmes_or_ordinary_notes(vault):
    hub(vault, "Title only")
    hub(vault, "people/README")
    hub(vault, "agents/README")
    fm.write(vault / "Legacy.md", {"skill": "brain-legacy"}, "")
    fm.write(vault / "Ordinary.md", {"tier": "hot"}, "")
    hub(vault, "Draft", kind="draft", skill="brain-draft")
    hub(vault, "Chickens", skill="brain-chickens", records="chickens")
    # A hub with no skill (a folder README, a context note) is not something the intellect keeps up.
    assert [entry["id"] for entry in responsibilities.discover(Vault(vault))] == ["chickens", "legacy"]
    assert not [p for p in validate.responsibilities(Vault(vault)) if "duplicate" in p]


def test_private_responsibility_owns_routine_without_exporting_its_id(vault):
    config = Config.load(vault)
    hub(vault, "Visible", skill="brain-shared", records="visible")
    hub(vault, "Private", skill="brain-shared", records="secret", tier="private")
    schedule.save(config, {"visible": schedule.Routine(every="1h", responsibility="secret")})
    snapshot = state.build(config, date(2026, 11, 6))
    assert [entry["id"] for entry in snapshot["responsibilities"]] == ["visible"]
    assert snapshot["responsibilities"][0]["routines"] == []
    assert snapshot["routines"][0]["responsibility"] is None


def folder(root, ident="chicken-alerts", **data):
    path = root / "Responsibilities" / ident / "RESPONSIBILITY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"version": 1, "asked_by": "Owner, to keep an eye on chickens",
                    "triggers": [{"kind": "schedule", "when": "every 2h"}], **data}, "# Wake up\n\nCheck the chickens.\n")
    return path


def test_folder_discovery_and_note_kinds(vault):
    path = folder(vault, title="Chicken watch", skill="brain-watch", work="flock", delivers="Healthy chickens",
                  needs=["computer"], goal="G-1", role="R-2", extra={"keep": "this"})
    fallback = folder(vault, "daily-check")
    hub(vault, "Legacy", skill="brain-legacy", records="legacy")
    records = path.parent / "records/2026-11.md"
    records.parent.mkdir()
    records.write_text("2026-11-11 | chicken-alerts | done | Checked. | Wait.\n")
    loaded = Vault(vault)
    entries = {entry["id"]: entry for entry in responsibilities.discover(loaded)}
    assert entries["chicken-alerts"] == {
        "id": "chicken-alerts", "title": "Chicken watch", "hub": path.relative_to(vault).as_posix(),
        "skill": "brain-watch", "records": "Responsibilities/chicken-alerts/records", "routines": [],
        "needs": ["computer"], "work": "flock", "delivers": "Healthy chickens", "folder": True, "path": "memory/Responsibilities/chicken-alerts/RESPONSIBILITY.md",
        "triggers": [{"kind": "schedule", "when": "every 2h"}], "version": 1,
        "status": "active", "ended": None, "reason": None,
        "asked_by": "Owner, to keep an eye on chickens", "goal": "G-1", "role": "R-2"}
    assert entries["daily-check"]["title"] == "Daily check"
    assert entries["legacy"]["folder"] is False
    assert loaded.kind_for(path.relative_to(vault).as_posix()) == "responsibility"
    assert loaded.kind_for(records.relative_to(vault).as_posix()) == "log"
    assert fallback.relative_to(vault).as_posix() in loaded.notes


@pytest.mark.parametrize("when, timing", [
    ("0 8 * * MON-FRI", {"cron": "0 8 * * MON-FRI"}),
    ("every 15m", {"every": "15m"}),
    ("every 2h", {"every": "2h"}),
    ("once 2026-11-12 19:00", {"at": "2026-11-12T19:00:00-08:00"}),
    ("once 2026-07-12T19:00", {"at": "2026-07-12T19:00:00-07:00"}),
    ("once 2026-11-12T19:00:00+02:00", {"at": "2026-11-12T19:00:00+02:00"}),
    ("once 2026-11-12T19:00:00Z", {"at": "2026-11-12T19:00:00+00:00"}),
])
def test_derive_timing(vault, when, timing, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-07-01")
    folder(vault, triggers=[{"kind": "schedule", "when": when}])
    loaded = Vault(vault)
    actual = responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])
    assert actual == {"chicken-alerts": schedule.Routine(**timing, responsibility="chicken-alerts", deliver="demiurge",
        prompt="Your responsibility Chicken alerts: read memory/Responsibilities/chicken-alerts/RESPONSIBILITY.md and do what it says. "
               "Its state between runs lives in memory/Responsibilities/chicken-alerts/records.")}


def test_derive_names_fields_and_owner_timezone(vault):
    (vault.parent / "brain.toml").write_text('[brain]\ntimezone="Asia/Kolkata"\n')
    path = folder(vault, skill="brain-watch", needs=["computer"], triggers=[
        {"kind": "mail", "filter": "chickens"},
        {"kind": "schedule", "when": "every 1h", "script": "scripts/check.py", "agent": False,
         "deliver": "local", "needs": ["computer"], "prompt": "Check the feed."},
        {"kind": "slack"}, {"kind": "schedule", "when": "once 2026-11-12 19:00"},
        {"kind": "schedule", "when": "0 8 * * *"}])
    loaded = Vault(vault)
    actual = responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])
    assert list(actual) == ["chicken-alerts", "chicken-alerts-2", "chicken-alerts-3"]
    first = actual["chicken-alerts"]
    assert first.script == "memory/Responsibilities/chicken-alerts/scripts/check.py"
    assert first.agent is False and first.deliver == "local"
    assert first.prompt.endswith("\n\nCheck the feed.")
    assert first.skills == ["brain-watch"] and first.needs == ["computer"]
    assert actual["chicken-alerts-2"].at == "2026-11-12T19:00:00+05:30"
    assert all(routine.responsibility == "chicken-alerts" for routine in actual.values())
    assert fm.read(path)[0]["triggers"][0] == {"kind": "mail", "filter": "chickens"}


def test_sync_updates_preserves_pause_removes_obsolete_and_deleted_but_keeps_legacy(vault):
    path = folder(vault)
    deleted = folder(vault, "gone")
    config = Config.load(vault)
    legacy = schedule.Routine(every="3h", responsibility="legacy")
    hub(vault, "Legacy", skill="brain-legacy", records="legacy")
    schedule.save(config, {"legacy": legacy, "legacy-2": legacy,
                           "engine": schedule.Routine(every="1h"),
                           "chicken-alerts": schedule.Routine(cron="0 8 * * *", responsibility="chicken-alerts", enabled=False),
                           "chicken-alerts-2": schedule.Routine(every="1h", responsibility="chicken-alerts"),
                           "custom-alias": schedule.Routine(every="1h", responsibility="chicken-alerts"),
                           "gone": schedule.Routine(every="1h", responsibility="gone"),
                           "gone-2": schedule.Routine(every="1h", responsibility="gone"),
                           "gone-10": schedule.Routine(every="1h", responsibility="gone"),
                           "gone-manual": schedule.Routine(every="1h", responsibility="gone")})
    loaded = Vault(vault)
    shutil.rmtree(deleted.parent)
    before_data, before_body = fm.read(path)
    changes = responsibilities.sync(loaded)
    assert "updated routine chicken-alerts" in changes
    assert {"removed routine chicken-alerts-2", "removed routine custom-alias", "removed routine gone",
            "removed routine gone-2", "removed routine gone-10"} <= set(changes)
    actual = schedule.load(config)
    assert set(actual) == {"legacy", "legacy-2", "engine", "chicken-alerts", "gone-manual"}
    assert actual["legacy"] == actual["legacy-2"] == legacy
    assert actual["chicken-alerts"].every == "2h" and actual["chicken-alerts"].enabled is False
    assert fm.read(path) == ({**before_data, "status": "paused"}, before_body)
    stamp = schedule.path(config).stat().st_mtime_ns
    assert responsibilities.sync(loaded) == []
    assert schedule.path(config).stat().st_mtime_ns == stamp
    path.unlink()
    assert responsibilities.sync(loaded) == []


def test_sync_keeps_a_broken_document_as_it_was_and_syncs_the_rest(vault):
    path = folder(vault, triggers=[{"kind": "schedule", "when": "tomorrow morning"}], own_key={"nested": [1, 2]})
    folder(vault, "deals")
    config = Config.load(vault)
    old = schedule.Routine(cron="0 8 * * *", responsibility="chicken-alerts")
    schedule.save(config, {"engine": schedule.Routine(every="1h"), "chicken-alerts": old})
    before = path.read_bytes()
    changes = responsibilities.sync(Vault(vault))
    assert any(change.startswith("kept chicken-alerts as it was:") and "when" in change for change in changes)
    assert "added routine deals" in changes
    actual = schedule.load(config)
    assert actual["chicken-alerts"] == old and "deals" in actual
    assert path.read_bytes() == before


def test_sync_keeps_a_responsibility_whose_routine_names_collide(vault):
    folder(vault, "alerts", triggers=[{"kind": "schedule", "when": "every 1h"},
                                      {"kind": "schedule", "when": "every 2h"}])
    folder(vault, "alerts-2")
    config = Config.load(vault)
    schedule.save(config, {"engine": schedule.Routine(every="1h")})
    changes = responsibilities.sync(Vault(vault))
    assert any("derived routine name is already used" in change for change in changes)
    assert set(schedule.load(config)) == {"engine", "alerts-2"}


@pytest.mark.parametrize("data, expected", [
    ({"version": None}, "version"), ({"version": 2}, "version"), ({"version": True}, "version"),
    ({"triggers": None}, "triggers must be a list"),
    ({"triggers": ["every 1h"]}, "map with a kind"),
    ({"triggers": [{}]}, "map with a kind"),
    ({"triggers": [{"kind": "schedule", "when": "every day 8am"}]}, "when"),
    ({"triggers": [{"kind": "schedule", "when": "every 0m"}]}, "when"),
    ({"triggers": [{"kind": "schedule", "when": "once 2026-02-30 08:00"}]}, "when"),
    ({"triggers": [{"kind": "schedule", "when": "once 2026-11-12"}]}, "when"),
    ({"triggers": [{"kind": "schedule", "when": "99 8 * * *"}]}, "when"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "script": "scripts/missing.py"}]}, "missing on disk"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "agent": False}]}, "agent=false requires script"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "agent": "false"}]}, "must be a boolean"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "deliver": None}]}, "deliver: must be a string"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "script": "../outside.py"}]}, "relative to"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "script": "/outside.py"}]}, "relative to"),
    ({"asked_by": None}, "names its reason"),
    ({"asked_by": "  "}, "names its reason"),
    ({"skill": []}, "skill must be a nonempty string"),
    ({"goal": "G-999"}, "unknown goal"), ({"role": "R-999"}, "unknown role"),
])
def test_validate_folder_problems(vault, data, expected):
    folder(vault, **data)
    assert expected in "\n".join(validate.responsibilities(Vault(vault)))


def test_validate_folder_id_and_unbuilt_kinds_are_warnings(vault, capsys):
    path = folder(vault, "Bad_ID", triggers=[{"kind": "mail"}, {"kind": "webhook"}])
    assert any("responsibility id must match" in problem for problem in validate.responsibilities(Vault(vault)))
    path.parent.rename(path.parent.with_name("valid-id"))
    loaded = Vault(vault)
    assert validate.responsibilities(loaded) == []
    assert validate.schedule_warnings(loaded) == [
        "Responsibilities/valid-id/RESPONSIBILITY.md: trigger kind mail is not built yet",
        "Responsibilities/valid-id/RESPONSIBILITY.md: trigger kind webhook is not built yet"]
    with pytest.raises(SystemExit) as error:
        cli.main(["validate", "--vault", str(vault)])
    assert error.value.code == 0
    assert "warning: Responsibilities/valid-id/RESPONSIBILITY.md: trigger kind mail is not built yet" in capsys.readouterr().out


def test_folder_without_records_and_nightly_never_rewrite_wakeup_document(vault):
    path = folder(vault, skill="brain-watch", unknown={"leave": "alone"})
    skill = vault / "Runtime/skills/brain-watch/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Watch.\n")
    before = path.read_bytes()
    assert validate.responsibilities(Vault(vault)) == []
    nightly.run(vault, date(2026, 11, 11))
    assert path.read_bytes() == before
    assert not (path.parent / "records").exists()


def test_add_requires_reason_and_known_ids_before_creating_anything(vault):
    base = ["responsibility", "add", "Chicken alerts", "--when", "every 2h", "--vault", str(vault)]
    for extra, message in (([], "a responsibility names its reason: --goal, --role or --asked-by"),
                           (["--goal", "G-999"], "unknown goal"), (["--role", "R-999"], "unknown role")):
        with pytest.raises(SystemExit, match=message):
            cli.main(base + extra)
        assert not (vault / "Responsibilities").exists()


def test_add_from_stdin_sync_and_existing_folder_refusal(vault, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("# Chicken alerts\n\nCheck each chicken.\n"))
    args = ["responsibility", "add", "Chicken alerts", "--when", "every 2h", "--asked-by", "Owner, in Home",
            "--script", "scripts/check.py", "--prompt", "Count them.", "--delivers", "A healthy flock", "--body", "-",
            "--vault", str(vault)]
    cli.main(args)
    assert "added routine chicken-alerts" in capsys.readouterr().out
    path = vault / "Responsibilities/chicken-alerts/RESPONSIBILITY.md"
    data, body = fm.read(path)
    assert data == {"version": 1, "title": "Chicken alerts", "asked_by": "Owner, in Home", "delivers": "A healthy flock",
                    "triggers": [{"kind": "schedule", "when": "every 2h", "script": "scripts/check.py", "prompt": "Count them."}]}
    assert body == "# Chicken alerts\n\nCheck each chicken.\n"
    assert schedule.load(Config.load(vault))["chicken-alerts"].script == "memory/Responsibilities/chicken-alerts/scripts/check.py"
    assert not (path.parent / "records").exists()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(args)
    assert fm.read(path) == (data, body)
    cli.main(["responsibility", "sync", "--vault", str(vault)])
    assert capsys.readouterr().out == ""


def test_add_with_known_goal_or_role_and_placeholder(vault):
    goals = vault / "human/Goals.md"
    goals.parent.mkdir()
    goals.write_text("# Goals\n\n## Active\n\n### G-1 — Healthy chickens\n")
    role = vault / "Roles/Engineering/Role.md"
    role.parent.mkdir(parents=True)
    fm.write(role, {"id": "R-1"}, "")
    for option, ident, title in (("--goal", "G-1", "Healthy chickens"), ("--role", "R-1", "Team check")):
        cli.main(["responsibility", "add", title, "--when", "0 8 * * *", option, ident, "--vault", str(vault)])
    assert validate.responsibilities(Vault(vault)) == []
    path = vault / "Responsibilities/healthy-chickens/RESPONSIBILITY.md"
    assert "Write what this responsibility is for" in fm.read(path)[1]
    assert fm.read(path)[0]["goal"] == "G-1"


@pytest.mark.parametrize("name", ["add", "adopt", "status", "sync"])
def test_legacy_creation_keeps_names_that_match_new_commands(vault, tmp_path, monkeypatch, name):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    cli.main(["responsibility", name, "--skill", f"brain-{name}", "--vault", str(vault)])
    assert (vault / f"{name}.md").is_file()
    assert (vault / f"Runtime/skills/brain-{name}/SKILL.md").is_file()
    assert (tmp_path / "agent-home/records" / name).is_dir()
    assert not (vault / "Responsibilities").exists()



def test_spent_one_off_leaves_the_schedule_and_keeps_later_names(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-20")
    folder(vault, triggers=[{"kind": "schedule", "when": "once 2026-11-12 19:00"},
                            {"kind": "schedule", "when": "once 2026-11-20 08:00"},
                            {"kind": "schedule", "when": "0 8 * * *"}])
    loaded = Vault(vault)
    actual = responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])
    assert list(actual) == ["chicken-alerts-2", "chicken-alerts-3"]


def own_skill(root, name="brain-housekeeping", body="Keep every trade receipt.\n"):
    path = Config.load(root).skills_path / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {"name": name, "description": "A local method"}, body)
    return path


def tree(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777) if path.is_file() else None
            for path in root.rglob("*")}


def adopt(root, *args):
    cli.main(["responsibility", "adopt", *args, "--vault", str(root)])


def test_adopt_routines_copies_methods_scripts_and_timing_under_one_lock(vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-11")
    config = Config.load(vault)
    skill = own_skill(vault)
    script = install.scripts_path() / "trades.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho trades\n")
    script.chmod(0o751)
    schedule.save(config, {
        "housekeeping": schedule.Routine(cron="0 8 * * MON-FRI", prompt="Keep the house clean.",
                                         skills=[skill.parent.name, "brain-watch", "brain-explore"], deliver="demiurge"),
        "housekeeping-trades": schedule.Routine(every="2h", prompt="", script="trades.sh", agent=False,
                                                needs=["computer"], enabled=False),
        "reminder": schedule.Routine(at="2026-11-12T19:00:00+02:00", prompt="Run reminder.", deliver="demiurge"),
        "unrelated": schedule.Routine(every="5h"),
    })
    schedule_path = schedule.path(config)
    schedule_path.write_text(schedule_path.read_text().replace('[routines.reminder]', '[routines.reminder]\nprompt = "Run reminder."'))
    before_skill, before_script = skill.read_bytes(), script.read_bytes()
    locks, syncs = [], []
    real_lock, real_sync = responsibilities.vault_lock, responsibilities.sync

    @contextmanager
    def counted_lock(config):
        locks.append(config)
        with real_lock(config):
            yield

    def checked_sync(vault, *, locked=False):
        syncs.append(locked)
        return real_sync(vault, locked=locked)

    def no_git(*args, **kwargs):
        pytest.fail("adoption must not invoke git")

    monkeypatch.setattr(responsibilities, "vault_lock", counted_lock)
    monkeypatch.setattr(responsibilities, "sync", checked_sync)
    monkeypatch.setattr("subprocess.check_output", no_git)
    monkeypatch.setattr("subprocess.run", no_git)
    adopt(vault, "housekeeping", "housekeeping-trades", "reminder", "--title", "Housekeeping & trades",
          "--asked-by", "Owner, Conversations/2026-11-01.md: keep trade receipts")
    assert len(locks) == 1 and syncs == [True]
    path = vault / "Responsibilities/housekeeping-trades/RESPONSIBILITY.md"
    data, body = fm.read(path)
    assert data == {
        "version": 1, "title": "Housekeeping & trades", "skill": "brain-watch",
        "asked_by": "Owner, Conversations/2026-11-01.md: keep trade receipts",
        "triggers": [
            {"kind": "schedule", "when": "0 8 * * MON-FRI", "prompt": "Keep the house clean."},
            {"kind": "schedule", "when": "every 2h", "deliver": "local", "needs": ["computer"],
             "agent": False, "script": "scripts/trades.sh", "paused": True},
            {"kind": "schedule", "when": "once 2026-11-12T19:00:00+02:00"},
        ]}
    assert body.startswith("# Housekeeping & trades\n\nAdopted from the routines housekeeping, housekeeping-trades, reminder on 2026-11-11;")
    assert "## Prompt (housekeeping)\n\nKeep the house clean." in body
    assert "## Prompt (reminder)\n\nRun reminder." in body
    assert "## Prompt (housekeeping-trades)" not in body
    assert "## Method (from brain-housekeeping)\n\nKeep every trade receipt." in body
    assert "description:" not in body and "`brain-explore`" in body
    copied = path.parent / "scripts/trades.sh"
    assert copied.read_bytes() == before_script and copied.stat().st_mode & 0o777 == 0o751
    assert skill.read_bytes() == before_skill and script.read_bytes() == before_script
    actual = schedule.load(config)
    assert set(actual) == {"housekeeping-trades", "housekeeping-trades-2", "housekeeping-trades-3", "unrelated"}
    assert all(actual[name].responsibility == "housekeeping-trades" for name in actual if name != "unrelated")
    assert actual["housekeeping-trades"].enabled and not actual["housekeeping-trades-2"].enabled
    assert actual["housekeeping-trades-3"].enabled
    assert actual["housekeeping-trades-2"].script == "memory/Responsibilities/housekeeping-trades/scripts/trades.sh"
    assert actual["housekeeping-trades-3"].at == "2026-11-12T19:00:00+02:00"
    output = capsys.readouterr().out
    for written in (path, copied, schedule.path(config)):
        assert f"wrote {written}" in output
    assert responsibilities.sync(Vault(vault), locked=True) == []


@pytest.mark.parametrize("extra, reason", [
    ([], {"asked_by": "origin unknown"}),
    (["--goal", "G-1"], {"goal": "G-1"}),
    (["--role", "R-1"], {"role": "R-1"}),
])
def test_adopt_defaults_and_reason_flags(vault, extra, reason):
    config = Config.load(vault)
    goals = vault / "human/Goals.md"
    goals.parent.mkdir()
    goals.write_text("# Goals\n\n## Active\n\n### G-1 — Healthy chickens\n")
    role = vault / "Roles/Engineering/Role.md"
    role.parent.mkdir(parents=True)
    fm.write(role, {"id": "R-1"}, "")
    schedule.save(config, {"alerts": schedule.Routine(every="2h", enabled=False)})
    adopt(vault, "alerts", *extra)
    data, body = fm.read(vault / "Responsibilities/alerts/RESPONSIBILITY.md")
    assert data == {"version": 1, "title": "alerts", "triggers": [
        {"kind": "schedule", "when": "every 2h", "deliver": "local", "paused": True}], **reason}
    assert "Run alerts." not in body
    assert schedule.load(config)["alerts"].enabled is False


@pytest.mark.parametrize("case, message", [
    ("unknown", "unknown routine"), ("owned", "already has a responsibility"),
    ("fallback", "already has a responsibility"), ("engine", "engine routine"),
    ("migration", "engine routine"), ("after", "after routines are engine plumbing"),
    ("monitor", "monitor or classify routines stay plain"), ("classify", "monitor or classify routines stay plain"),
    ("workdir", "needs a hand conversion"), ("missing-script", "missing script file"),
    ("script-path", "script must be a bare name"), ("existing", "folder already exists"),
    ("collision", "derived routine name already exists"), ("duplicate", "only once"),
    ("bad-skill", "invalid skill name"),
    ("goal", "unknown goal"), ("role", "unknown role"), ("invalid-title", "responsibility id must match"),
    ("hub-id", "responsibility id already exists"),
])
def test_adopt_refusals_leave_everything_unchanged(vault, tmp_path, monkeypatch, case, message):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    routine = schedule.Routine(every="2h")
    name, extra, others = "alerts", [], {}
    if case == "owned":
        folder(vault, "other")
        routine = replace(routine, responsibility="other")
    elif case == "fallback":
        hub(vault, "Legacy", skill="brain-alerts", records="legacy")
        routine = replace(routine, skills=["brain-alerts"])
    elif case == "engine":
        name = "morning"
        (vault.parent / "brain.toml").write_text('[brain]\nedition="enterprise"\n')
        config = Config.load(vault)
    elif case == "migration":
        name = install.MIGRATION_ROUTINE + "0-148-0"
    elif case == "after":
        routine = schedule.Routine(after="base")
        others["base"] = schedule.Routine(every="2h")
    elif case == "monitor":
        routine = replace(routine, monitor="signal.sh")
    elif case == "classify":
        routine = replace(routine, classify=["roles", "screen"], agent=False)
    elif case == "workdir":
        routine = replace(routine, workdir=str(tmp_path / "elsewhere"))
    elif case == "missing-script":
        routine = replace(routine, script="gone.sh")
    elif case == "script-path":
        routine = replace(routine, script="somewhere/check.sh")
    elif case == "existing":
        (vault / "Responsibilities/alerts").mkdir(parents=True)
    elif case == "collision":
        others["bundle"] = schedule.Routine(every="5h")
        extra = ["--title", "Bundle"]
    elif case == "duplicate":
        extra = [name]
    elif case == "bad-skill":
        routine = replace(routine, skills=["../outside"])
    elif case in ("goal", "role"):
        extra = ["--" + case, "G-999" if case == "goal" else "R-999"]
    elif case == "invalid-title":
        extra = ["--title", "!!!"]
    elif case == "hub-id":
        hub(vault, "Legacy", skill="brain-other", records="alerts")
        routine = replace(routine, responsibility=None)
        others["explicit-owner"] = replace(routine, responsibility="alerts")
    schedule.save(config, {**others, name: routine})
    before = tree(tmp_path)
    with pytest.raises(SystemExit, match=message):
        adopt(vault, "unknown" if case == "unknown" else name, *extra)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_adopt_refuses_every_engine_default(vault, edition):
    config = Config.load(vault)
    schedule.save(config, schedule.defaults(replace(config, edition=edition)))
    before = tree(vault)
    for name in schedule.defaults(replace(config, edition=edition)):
        with pytest.raises(SystemExit, match="engine routine"):
            adopt(vault, name)
    assert tree(vault) == before


def test_adopt_repo_workdir_is_allowed_and_pause_follows_renamed_source(vault):
    config = Config.load(vault)
    schedule.save(config, {"alerts": schedule.Routine(every="2h", enabled=False),
                           "other": schedule.Routine(every="3h")})
    path = schedule.path(config)
    path.write_text(path.read_text().replace('[routines.alerts]', f'[routines.alerts]\nworkdir = "{config.repo}"'))
    adopt(vault, "alerts", "other", "--title", "Bundle")
    actual = schedule.load(config)
    assert not actual["bundle"].enabled and actual["bundle-2"].enabled
    assert actual["bundle"].workdir is None


@pytest.mark.parametrize("hub_arg", ["title", "relative", "absolute"])
def test_adopt_hub_keeps_id_records_frontmatter_and_replaces_hub(vault, tmp_path, monkeypatch, capsys, hub_arg):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    reference = skill.parent / "references/tips.md"
    reference.parent.mkdir()
    reference.write_text("Old reference.\n")
    snapshot = skill.parent / "scripts/waiver-snapshot.mjs"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"#!/usr/bin/env node\nconsole.log('snapshot');\n")
    snapshot.chmod(0o751)
    assets = {path.relative_to(skill.parent): (path.read_bytes(), path.stat().st_mode & 0o777)
              for path in (reference, snapshot)}
    data = {"kind": "hub", "tier": "hot", "skill": skill.parent.name, "records": "trade-history",
            "work": "portfolio", "delivers": "Receipts", "asked_by": "Owner, dated request", "goal": "G-1", "role": "R-1",
            "uses": ["2026-11-10"], "uses_inferred": [], "activation": "night", "custom": {"keep": [1, 2]}, "title": "Old title"}
    path = vault / "Trade.jobs.md"
    fm.write(path, data, "# Trade jobs\n\nKeep the original instructions.\n")
    records = install.records_path() / "trade-history"
    records.mkdir(parents=True)
    (records / "receipt.md").write_text("Old receipt.\n")
    script = install.scripts_path() / "trades.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho trades\n")
    script.chmod(0o755)
    schedule.save(config, {"old-trades": schedule.Routine(every="2h", responsibility="trade-history", skills=[skill.parent.name],
                                                       prompt="Check trades.", enabled=False, script="trades.sh"),
                           "rollup": schedule.Routine(cron="0 8 * * MON", responsibility="trade-history"),
                           "engine": schedule.Routine(every="5h")})
    before_records = tree(records)
    argument = {"title": path.stem, "relative": path.name, "absolute": str(path)}[hub_arg]
    adopt(vault, "--hub", argument)
    target = vault / "Responsibilities/trade-history/RESPONSIBILITY.md"
    adopted_data, body = fm.read(target)
    assert adopted_data["version"] == 1 and adopted_data["title"] == "Trade.jobs"
    assert adopted_data["records"] == str(records.resolve()) and tree(records) == before_records
    for key in ("work", "delivers", "asked_by", "goal", "role", "custom"):
        assert adopted_data[key] == data[key]
    assert not {"kind", "tier", "skill", *nightly.NIGHT_KEYS} & adopted_data.keys()
    assert body == "# Trade jobs\n\nKeep the original instructions.\n\n## Method\n\nKeep every trade receipt.\n"
    assert fm.read(path) == ({key: value for key, value in data.items() if key not in ("skill", "records")},
                             "See [[Responsibilities/trade-history/RESPONSIBILITY]].\n")
    assert not skill.parent.exists() and script.exists()
    assert (target.parent / "scripts/trades.sh").read_bytes() == script.read_bytes()
    for relative, (content, mode) in assets.items():
        copied = target.parent / relative
        assert copied.read_bytes() == content and copied.stat().st_mode & 0o777 == mode
    assert not (target.parent / "SKILL.md").exists()
    loaded = Vault(vault)
    entries = responsibilities.discover(loaded)
    assert len(entries) == 1 and entries[0]["id"] == "trade-history"
    assert entries[0]["records"] == str(records.resolve())
    actual = schedule.load(config)
    assert set(actual) == {"engine", "trade-history", "trade-history-2"}
    assert not actual["trade-history"].enabled and actual["trade-history-2"].enabled
    assert all(actual[name].responsibility == "trade-history" for name in actual if name != "engine")
    assert str(records.resolve()) in actual["trade-history"].prompt
    output = capsys.readouterr().out
    for removed in (skill, reference, snapshot):
        assert f"removed {removed}" in output
    for written in (path, target, target.parent / "scripts/trades.sh", schedule.path(config),
                    *(target.parent / relative for relative in assets)):
        assert f"wrote {written}" in output


@pytest.mark.parametrize("conflict", [False, True])
def test_adopt_hub_checks_skill_assets_against_routine_scripts_before_writing(vault, tmp_path, monkeypatch, capsys, conflict):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    hub(vault, "Legacy", skill=skill.parent.name, records="history")
    asset = skill.parent / "scripts/check.sh"
    asset.parent.mkdir()
    asset.write_text("#!/bin/sh\necho skill\n")
    asset.chmod(0o751)
    script = install.scripts_path() / asset.name
    script.parent.mkdir(parents=True)
    script.write_bytes(b"different script\n" if conflict else asset.read_bytes())
    schedule.save(config, {"history": schedule.Routine(every="2h", script=script.name)})
    before = tree(tmp_path)
    if conflict:
        def no_writes(*args, **kwargs):
            pytest.fail("a file conflict must refuse before writing anything")

        monkeypatch.setattr(schedule, "save", no_writes)
        monkeypatch.setattr(fm, "write_text", no_writes)
        with pytest.raises(SystemExit, match="conflicting file content"):
            adopt(vault, "--hub", "Legacy")
        assert tree(tmp_path) == before
        assert capsys.readouterr().out == ""
    else:
        adopt(vault, "--hub", "Legacy")
        copied = vault / "Responsibilities/history/scripts/check.sh"
        assert copied.read_bytes() == script.read_bytes() and copied.stat().st_mode & 0o777 == 0o751
        assert capsys.readouterr().out.count(f"wrote {copied}\n") == 1


def test_adopt_hub_leaves_linked_one_offs_in_schedule_without_triggers(vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-11")
    config = Config.load(vault)
    skill = own_skill(vault)
    hub(vault, "Legacy", skill=skill.parent.name, records="history")
    one_offs = {
        "game-expired": schedule.Routine(at="2026-11-08T19:00:00-08:00", responsibility="history",
                                         prompt="Check that game.", skills=[skill.parent.name], enabled=False),
        "game-window": schedule.Routine(at="2026-11-12T19:00:00-08:00", responsibility="history",
                                        script="one-off.sh", needs=["computer"], agent=False),
    }
    schedule.save(config, {**one_offs, "recurring": schedule.Routine(every="2h", responsibility="history", enabled=False)})
    adopt(vault, "--hub", "Legacy")
    data, _ = fm.read(vault / "Responsibilities/history/RESPONSIBILITY.md")
    assert data["triggers"] == [{"kind": "schedule", "when": "every 2h", "deliver": "local", "paused": True}]
    actual = schedule.load(config)
    assert set(actual) == {*one_offs, "history"} and not actual["history"].enabled
    assert {name: actual[name] for name in one_offs} == one_offs
    output = capsys.readouterr().out
    assert all(f"removed routine {name}" not in output for name in one_offs)
    assert responsibilities.sync(Vault(vault)) == []
    cli.main(["schedule", "prune", "--vault", str(vault)])
    assert "game-expired" not in schedule.load(config) and schedule.load(config)["game-window"] == one_offs["game-window"]


def test_adopt_into_appends_triggers_and_text_preserves_document_and_enabled_states(vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-11")
    config = Config.load(vault)
    path = folder(vault, "history")
    data = {"custom": {"keep": [1, 2]}, "triggers": [
        {"kind": "mail", "filter": "trades"},
        {"kind": "schedule", "when": "once 2026-11-01T08:00:00-08:00"},
        {"kind": "schedule", "when": "every 2h", "prompt": "Keep existing instructions.", "paused": True},
        {"kind": "webhook"}, {"kind": "schedule", "when": "0 8 * * *"}],
        "title": "Trading history", "version": 1, "asked_by": "Owner, original request",
        "records": "agent/trade-history", "needs": ["computer"], "work": "portfolio", "delivers": "Receipts"}
    original_body = "# Trading history\n\nKeep this body exactly.\n\n"
    fm.write(path, data, original_body)
    path.chmod(0o640)
    record = path.parent / "records/receipt.md"
    record.parent.mkdir()
    record.write_text("Keep the receipt.\n")
    skill = own_skill(vault)
    script = install.scripts_path() / "trades.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho trades\n")
    script.chmod(0o751)
    responsibilities.sync(Vault(vault))
    existing = schedule.load(config)
    sources = {
        "alerts": schedule.Routine(cron="0 9 * * *", skills=[skill.parent.name, "brain-watch"],
                                    script=script.name, prompt="Check trades.", deliver="demiurge"),
        "history-4": schedule.Routine(at="2026-11-12T19:00:00-08:00", enabled=False, prompt="Check that window."),
    }
    schedule.save(config, {**existing, **sources, "unrelated": schedule.Routine(every="5h")})
    before_skill, before_records = tree(skill.parent), tree(record.parent)
    adopt(vault, *sources, "--into", "history")
    actual_data, body = fm.read(path)
    assert list(actual_data) == list(data)
    assert {key: value for key, value in actual_data.items() if key != "triggers"} == {
        key: value for key, value in data.items() if key != "triggers"}
    assert actual_data["triggers"] == data["triggers"] + [
        {"kind": "schedule", "when": "0 9 * * *", "prompt": "Check trades.", "script": "scripts/trades.sh"},
        {"kind": "schedule", "when": "once 2026-11-12T19:00:00-08:00", "prompt": "Check that window.", "deliver": "local", "paused": True}]
    assert body.startswith(original_body)
    assert "## Prompt (alerts)\n\nCheck trades." in body
    assert "## Prompt (history-4)\n\nCheck that window." in body
    assert "## Method (from brain-housekeeping)\n\nKeep every trade receipt." in body
    assert "`brain-watch`" in body and "skill" not in actual_data
    assert path.stat().st_mode & 0o777 == 0o640
    assert tree(skill.parent) == before_skill and tree(record.parent) == before_records
    copied = path.parent / "scripts/trades.sh"
    assert copied.read_bytes() == script.read_bytes() and copied.stat().st_mode & 0o777 == 0o751
    actual = schedule.load(config)
    assert set(actual) == {"history-2", "history-3", "history-4", "history-5", "unrelated"}
    assert all(actual[name] == routine for name, routine in existing.items())
    assert actual["history-4"].enabled and not actual["history-5"].enabled
    assert actual["history-4"].script == "memory/Responsibilities/history/scripts/trades.sh"
    assert actual["history-5"].at == sources["history-4"].at
    output = capsys.readouterr().out
    for written in (path, copied, schedule.path(config)):
        assert f"wrote {written}" in output
    assert responsibilities.sync(Vault(vault)) == []


@pytest.mark.parametrize("conflict", [False, True])
def test_adopt_into_checks_existing_script_content_before_writing(vault, tmp_path, monkeypatch, conflict):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    path = folder(vault, "history")
    destination = path.parent / "scripts/check.sh"
    destination.parent.mkdir()
    destination.write_text("Keep this script.\n")
    script = install.scripts_path() / destination.name
    script.parent.mkdir(parents=True)
    script.write_bytes(b"Different script.\n" if conflict else destination.read_bytes())
    script.chmod(0o751)
    schedule.save(config, {"alerts": schedule.Routine(every="2h", script=script.name)})
    before = tree(tmp_path)
    if conflict:
        def no_writes(*args, **kwargs):
            pytest.fail("a file conflict must refuse before writing anything")

        monkeypatch.setattr(schedule, "save", no_writes)
        monkeypatch.setattr(fm, "write_text", no_writes)
        with pytest.raises(SystemExit, match="conflicting file content"):
            adopt(vault, "alerts", "--into", "history")
        assert tree(tmp_path) == before
    else:
        adopt(vault, "alerts", "--into", "history")
        assert destination.read_bytes() == script.read_bytes() and destination.stat().st_mode & 0o777 == 0o751
        assert schedule.load(config)["history-2"].script == "memory/Responsibilities/history/scripts/check.sh"


@pytest.mark.parametrize("case, message", [
    ("unknown", "unknown folder responsibility"), ("legacy", "unknown folder responsibility"),
    ("no-document", "unknown folder responsibility"), ("broken", "triggers must be a list"),
    ("title", "--into takes no --title or reason flags"), ("asked-by", "--into takes no --title or reason flags"),
    ("goal", "--into takes no --title or reason flags"), ("role", "--into takes no --title or reason flags"),
    ("collision", "derived routine name already exists"), ("schedule-collision", "derived routine name already exists"),
    ("unknown-routine", "unknown routine"), ("owned", "already has a responsibility"),
    ("fallback", "already has a responsibility"), ("engine", "engine routine"),
    ("migration", "engine routine"), ("after", "after routines are engine plumbing"),
    ("monitor", "monitor or classify routines stay plain"), ("classify", "monitor or classify routines stay plain"),
    ("workdir", "needs a hand conversion"), ("missing-script", "missing script file"),
    ("script-path", "script must be a bare name"), ("duplicate", "only once"),
    ("bad-skill", "invalid skill name"),
])
def test_adopt_into_refusals_leave_everything_unchanged(vault, tmp_path, monkeypatch, case, message):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    name, extra, others = "alerts", [], {}
    routine = schedule.Routine(every="2h")
    if case == "legacy":
        hub(vault, "Legacy", skill="brain-other", records="history")
    elif case == "no-document":
        (vault / "Responsibilities/history").mkdir(parents=True)
    elif case != "unknown":
        folder(vault, "history", **({"triggers": None} if case == "broken" else {}))
    if case in ("title", "asked-by", "goal", "role"):
        extra = ["--" + case, "Override"]
    if case == "collision":
        folder(vault, "history-2")
    elif case == "schedule-collision":
        others["history-2"] = schedule.Routine(every="3h")
    elif case == "owned":
        routine = replace(routine, responsibility="history")
    elif case == "fallback":
        folder(vault, "history", skill="brain-alerts")
        routine = replace(routine, skills=["brain-alerts"])
    elif case == "engine":
        name = "demiurge-sync"
    elif case == "migration":
        name = install.MIGRATION_ROUTINE + "0-148-0"
    elif case == "after":
        others["base"] = routine
        routine = schedule.Routine(after="base")
    elif case == "monitor":
        routine = replace(routine, monitor="signal.sh")
    elif case == "classify":
        routine = replace(routine, classify=["roles", "screen"], agent=False)
    elif case == "workdir":
        routine = replace(routine, workdir=str(tmp_path / "elsewhere"))
    elif case == "missing-script":
        routine = replace(routine, script="gone.sh")
    elif case == "script-path":
        routine = replace(routine, script="somewhere/check.sh")
    elif case == "duplicate":
        extra = [name]
    elif case == "bad-skill":
        routine = replace(routine, skills=["../outside"])
    schedule.save(config, {**others, name: routine})
    if case == "unknown-routine":
        name = "unknown"
    before = tree(tmp_path)
    with pytest.raises(SystemExit, match=message):
        adopt(vault, name, *extra, "--into", "history")
    assert tree(tmp_path) == before


@pytest.mark.parametrize("reason", [{}, {"goal": "G-1"}])
def test_adopt_hub_without_records_uses_folder_default_and_preserves_reason(vault, tmp_path, monkeypatch, reason):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    skill = own_skill(vault)
    hub(vault, "Legacy", skill=skill.parent.name, records="missing-records", **reason)
    adopt(vault, "--hub", "Legacy")
    data, body = fm.read(vault / "Responsibilities/missing-records/RESPONSIBILITY.md")
    assert "records" not in data
    assert ("asked_by" in data) == (not reason)
    if not reason:
        assert data["asked_by"] == "origin unknown"
    entry, = responsibilities.discover(Vault(vault))
    assert entry["records"] == "Responsibilities/missing-records/records"
    assert entry["triggers"] == []


def test_adopt_hub_links_legacy_routines_by_name_and_skill(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    hub(vault, "Legacy", skill=skill.parent.name, records="history")
    schedule.save(config, {"history": schedule.Routine(every="2h", enabled=False),
                           "trade-extra": schedule.Routine(every="3h", skills=[skill.parent.name])})
    adopt(vault, "--hub", "Legacy")
    actual = schedule.load(config)
    assert set(actual) == {"history", "history-2"}
    assert all(routine.responsibility == "history" for routine in actual.values())
    assert not actual["history"].enabled


@pytest.mark.parametrize("case, message", [
    ("unknown", "no old-style responsibility hub"), ("no-skill", "no old-style responsibility hub"),
    ("engine-skill", "must name an own skill"), ("missing-skill", "has no SKILL.md"),
    ("nested", "at the vault root"), ("outside", "at the vault root"),
    ("existing", "folder already exists"), ("missing-script", "missing script file"),
    ("malformed-skill", "frontmatter"),
])
def test_adopt_hub_refusals_preserve_hub_and_skill(vault, tmp_path, monkeypatch, case, message):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    source = hub(vault, "Legacy", skill=skill.parent.name, records="history")
    argument = "Legacy"
    if case == "unknown":
        argument = "Unknown"
    elif case == "no-skill":
        fm.write(source, {"kind": "hub"}, "Keep me.\n")
    elif case == "engine-skill":
        fm.write(source, {"kind": "hub", "skill": "brain-watch", "records": "history"}, "Keep me.\n")
    elif case == "missing-skill":
        skill.unlink()
    elif case == "nested":
        nested = vault / "Nested/Legacy.md"
        nested.parent.mkdir()
        source.rename(nested)
        argument = "Nested/Legacy.md"
    elif case == "outside":
        argument = str(tmp_path / "outside.md")
    elif case == "existing":
        (vault / "Responsibilities/history").mkdir(parents=True)
    elif case == "missing-script":
        schedule.save(config, {"history": schedule.Routine(every="2h", responsibility="history", script="missing.sh")})
    elif case == "malformed-skill":
        skill.write_text("---\n[bad, header]\n---\nKeep me.\n")
    before = tree(tmp_path)
    with pytest.raises(SystemExit, match=message):
        adopt(vault, "--hub", argument)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("args, message", [
    ([], "requires at least one routine"),
    (["alerts", "--goal", "G-1", "--asked-by", "Owner"], "only one of"),
    (["alerts", "--asked-by", " "], "must name a reason"),
    (["alerts", "--when", "every 2h"], "adopt takes routines"),
    (["alerts", "--hub", "Legacy"], "--hub takes no routines"),
    (["--hub", "Legacy", "--title", "Other"], "--hub takes no routines"),
    (["--hub", "Legacy", "--asked-by", "Owner"], "--hub takes no routines"),
    (["--hub", "Legacy", "--into", "history"], "--hub takes no routines"),
    (["--into", "history"], "requires at least one routine"),
])
def test_adopt_rejects_invalid_argument_combinations_without_writing(vault, args, message):
    config = Config.load(vault)
    schedule.save(config, {"alerts": schedule.Routine(every="2h")})
    before = tree(vault)
    with pytest.raises(SystemExit, match=message):
        adopt(vault, *args)
    assert tree(vault) == before


@pytest.mark.parametrize("fail_sync", [False, True])
def test_adopt_hub_with_symlinked_skill_keeps_target_and_restores_link_on_error(vault, tmp_path, monkeypatch, fail_sync):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    directory = skill.parent
    asset = directory / "references/tips.md"
    asset.parent.mkdir()
    asset.write_text("Keep the reference.\n")
    asset.chmod(0o640)
    original = tmp_path / "own-method"
    directory.rename(original)
    directory.symlink_to(original, target_is_directory=True)
    hub(vault, "Legacy", skill=directory.name, records="history")
    schedule.save(config, {"history": schedule.Routine(every="2h")})
    before, target_before = tree(vault), tree(original)
    if fail_sync:
        def broken_sync(*args, **kwargs):
            raise ValueError("injected sync error")

        monkeypatch.setattr(responsibilities, "sync", broken_sync)
        with pytest.raises(SystemExit, match="injected sync error"):
            adopt(vault, "--hub", "Legacy")
        assert directory.is_symlink() and directory.readlink() == original
        assert tree(vault) == before
    else:
        adopt(vault, "--hub", "Legacy")
        assert not directory.exists() and not directory.is_symlink()
        assert "Keep every trade receipt." in fm.read(vault / "Responsibilities/history/RESPONSIBILITY.md")[1]
        copied = vault / "Responsibilities/history/references/tips.md"
        assert copied.read_bytes() == (original / "references/tips.md").read_bytes()
        assert copied.stat().st_mode & 0o777 == 0o640
    assert tree(original) == target_before


@pytest.mark.parametrize("mode", ["group", "hub", "into"])
@pytest.mark.parametrize("failure", ["write", "copy", "sync", "schedule"])
def test_adopt_rolls_back_write_and_sync_errors(vault, tmp_path, monkeypatch, capsys, mode, failure):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    config = Config.load(vault)
    skill = own_skill(vault)
    asset = skill.parent / "scripts/waiver-snapshot.mjs"
    asset.parent.mkdir()
    asset.write_bytes(b"#!/usr/bin/env node\nconsole.log('snapshot');\n")
    asset.chmod(0o751)
    script = install.scripts_path() / "run.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho run\n")
    script.chmod(0o751)
    if mode == "hub":
        hub(vault, "Legacy", skill=skill.parent.name, records="alerts")
    elif mode == "into":
        target = folder(vault, "history", custom={"keep": [1, 2]})
        target.chmod(0o640)
        existing = target.parent / "scripts" / script.name
        existing.parent.mkdir()
        existing.write_bytes(script.read_bytes())
        existing.chmod(0o600)
        records = target.parent / "records/receipt.md"
        records.parent.mkdir()
        records.write_text("Keep the receipt.\n")
    folder(vault, "unrelated")
    schedule.save(config, {"alerts": schedule.Routine(every="2h", enabled=False, skills=[skill.parent.name], script=script.name),
                           "unrelated": schedule.Routine(every="1h", responsibility="unrelated", enabled=False)})
    before = tree(tmp_path)
    if failure in ("write", "copy"):
        real_write = fm.write_text

        def fail_write(path, *args, **kwargs):
            if failure == "write" and path.name == "RESPONSIBILITY.md":
                raise OSError("injected write error")
            result = real_write(path, *args, **kwargs)
            if failure == "copy" and path.name == script.name:
                raise OSError("injected copy error")
            return result

        monkeypatch.setattr(fm, "write_text", fail_write)
    elif failure == "sync":
        def fail_sync(*args, **kwargs):
            raise ValueError("injected sync error")

        monkeypatch.setattr(responsibilities, "sync", fail_sync)
    else:
        real_save, calls = schedule.save, []

        def fail_schedule(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise OSError("injected schedule error")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(schedule, "save", fail_schedule)
    with pytest.raises(SystemExit, match="injected"):
        adopt(vault, *({"hub": ["--hub", "Legacy"], "group": ["alerts"], "into": ["alerts", "--into", "history"]}[mode]))
    assert tree(tmp_path) == before
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_schedule_prune_only_removes_named_dead_entries(vault, monkeypatch, capsys, extra):
    config = Config.load(vault)
    moment = datetime(2026, 11, 11, 12, tzinfo=config.zone)
    monkeypatch.setattr(Config, "local", lambda self: moment)
    routines = {
        "expired": schedule.Routine(at=(moment - timedelta(days=1, seconds=1)).isoformat()),
        "expired-paused": schedule.Routine(at=(moment - timedelta(days=2)).isoformat(), enabled=False),
        "recent": schedule.Routine(at=(moment - timedelta(hours=23)).isoformat()),
        "boundary": schedule.Routine(at=(moment - timedelta(days=1)).isoformat()),
        "future": schedule.Routine(at=(moment + timedelta(days=1)).isoformat()),
        "owned-expired": schedule.Routine(at=(moment - timedelta(days=2)).isoformat(), responsibility="alerts"),
        "paused": schedule.Routine(every="2h", enabled=False),
        "active": schedule.Routine(every="2h"),
        "demiurge-whoami": schedule.Routine(every="2h"),
        "brain-migration": schedule.Routine(every="2h", enabled=False),
        "brain-migration-0-148-0": schedule.Routine(at=(moment + timedelta(days=1)).isoformat()),
    }
    schedule.save(config, routines)
    before = schedule.path(config).read_bytes()
    cli.main(["schedule", "prune", *extra, "--vault", str(vault)])
    dead = {"expired", "expired-paused", "owned-expired", "demiurge-whoami", "brain-migration"}
    assert schedule.load(config) == (routines if extra else {name: routine for name, routine in routines.items() if name not in dead})
    if extra:
        assert schedule.path(config).read_bytes() == before
    output = capsys.readouterr().out
    assert len(output.splitlines()) == len(dead)
    for name in dead:
        assert f"{'would remove' if extra else 'removed'} routine {name} in" in output


def test_adoption_migration_renders_owner_and_stays_under_word_limit(vault, monkeypatch):
    config = Config.load(vault)
    source = files("brain").joinpath("migrations/0.148.0.md").read_text()
    assert fm.split(source)[0] == {"unattended": True}
    assert len(source.split()) < 300
    install.record_version(config, "0.147.0")
    monkeypatch.setattr(install, "installed_version", lambda name: "0.148.0")
    version, prompt = install.pending_migrations(config)[0]
    assert version == "0.148.0" and "{human_name}" not in prompt
    assert f"Tell {config.human_name}: your recurring jobs" in prompt
    for command in ("brain install-skills", "brain schedule prune", "brain responsibility adopt --hub",
                    "brain responsibility sync", "brain validate", "brain loops --add", "brain migrate-done"):
        assert command in prompt
    assert "by skill, name or what its prompt does" in prompt and "--into <id>" in prompt


def test_adopt_is_not_blocked_by_another_broken_responsibility(vault):
    folder(vault, "broken", triggers=[{"kind": "schedule", "when": "tomorrow morning"}])
    config = Config.load(vault)
    schedule.save(config, {"food-deals": schedule.Routine(cron="0 9 * * *", prompt="Check the deals.")})
    responsibilities.adopt(config, ["food-deals"])
    assert schedule.load(config)["food-deals"].responsibility == "food-deals"


def test_one_off_runs_for_a_folder_responsibility_survive_sync_and_prune_when_spent(vault, monkeypatch, capsys):
    folder(vault)
    config = Config.load(vault)
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-11")
    responsibilities.sync(Vault(vault))
    for name, at in (("window-old", "2026-11-08T08:15:00-05:00"), ("window-new", "2026-11-12T19:00:00-05:00")):
        cli.main(["schedule", "set", name, "--at", at, "--responsibility", "chicken-alerts", "--vault", str(vault)])
    with pytest.raises(SystemExit):
        cli.main(["schedule", "set", "window-new", "--cron", "0 8 * * *", "--vault", str(vault)])
    responsibilities.sync(Vault(vault))
    assert {"window-old", "window-new"} <= set(schedule.load(config))
    cli.main(["schedule", "prune", "--vault", str(vault)])
    assert "window-old" not in schedule.load(config) and "window-new" in schedule.load(config)


@pytest.mark.parametrize("status, enabled", [(None, [True, False]), ("active", [True, False]),
                                             ("paused", [False, False]), ("retired", [])])
def test_derive_responsibility_and_trigger_status(vault, status, enabled):
    data = {"status": status} if status is not None else {}
    if status == "retired":
        data["ended"] = date(2026, 11, 10)
    folder(vault, triggers=[{"kind": "schedule", "when": "every 1h"}, {"kind": "mail"},
                            {"kind": "schedule", "when": "0 8 * * *", "paused": True}], **data)
    loaded = Vault(vault)
    derived = responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])
    assert [routine.enabled for routine in derived.values()] == enabled


@pytest.mark.parametrize("data, error", [
    ({"status": "ended"}, "status must be"), ({"status": None}, "status must be"),
    ({"status": ["active"]}, "status must be"),
    ({"status": "retired"}, "ended: YYYY-MM-DD"),
    ({"status": "retired", "ended": "2026-02-30"}, "ended: YYYY-MM-DD"),
    ({"status": "retired", "ended": "20261110"}, "ended: YYYY-MM-DD"),
    ({"reason": 12}, "reason must be"),
    ({"triggers": [{"kind": "schedule", "when": "every 1h", "paused": "true"}]}, "paused: must be a boolean"),
])
def test_derive_rejects_invalid_status_fields(vault, data, error):
    folder(vault, **data)
    loaded = Vault(vault)
    with pytest.raises(ValueError, match=error):
        responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])


def test_sync_enabled_comes_only_from_document_after_conversion(vault):
    path = folder(vault)
    hub(vault, "Legacy", skill="brain-legacy", records="legacy")
    loaded = Vault(vault)
    responsibilities.sync(loaded)
    routines = schedule.load(loaded.config)
    routines["chicken-alerts"] = replace(routines["chicken-alerts"], enabled=False)
    routines["legacy"] = schedule.Routine(every="1h", responsibility="legacy", enabled=False)
    schedule.save(loaded.config, routines)
    responsibilities.sync(loaded)
    assert schedule.load(loaded.config)["chicken-alerts"].enabled
    assert not schedule.load(loaded.config)["legacy"].enabled
    data, body = fm.read(path)
    fm.write(path, {**data, "status": "paused"}, body)
    responsibilities.sync(loaded)
    assert not schedule.load(loaded.config)["chicken-alerts"].enabled
    fm.write(path, data, body)
    responsibilities.sync(loaded)
    assert schedule.load(loaded.config)["chicken-alerts"].enabled


def test_status_cli_preserves_document_and_syncs_under_one_lock(vault, monkeypatch, capsys):
    path = folder(vault, custom={"keep": [1, "two"]}, learned=["Animal care"])
    data, body = fm.read(path)
    (vault.parent / "brain.toml").write_text('[brain]\ntimezone="Pacific/Honolulu"\n')
    monkeypatch.delenv("BRAIN_TODAY", raising=False)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 13, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("brain.config.datetime", Clock)
    held, locks = [], []
    real_lock, real_status, real_sync = responsibilities.vault_lock, responsibilities._status, responsibilities.sync

    @contextmanager
    def counted_lock(config):
        assert not held
        with real_lock(config):
            held.append(True)
            locks.append(True)
            yield
            held.pop()

    def checked_status(*args, **kwargs):
        assert held
        return real_status(*args, **kwargs)

    def checked_sync(vault, *, locked=False):
        assert held and locked
        return real_sync(vault, locked=locked)

    monkeypatch.setattr(responsibilities, "vault_lock", counted_lock)
    monkeypatch.setattr(responsibilities, "_status", checked_status)
    monkeypatch.setattr(responsibilities, "sync", checked_sync)

    def status(value, *extra):
        cli.main(["responsibility", "status", "chicken-alerts", value, *extra, "--vault", str(vault)])

    status("paused", "--reason", "Owner is away")
    assert fm.read(path) == ({**data, "status": "paused", "reason": "Owner is away"}, body)
    assert not schedule.load(Config.load(vault))["chicken-alerts"].enabled
    status("paused")
    assert "reason" not in fm.read(path)[0]
    status("retired", "--reason", "Work ended")
    assert fm.read(path) == ({**data, "status": "retired", "reason": "Work ended", "ended": "2026-11-12"}, body)
    assert not schedule.load(Config.load(vault))
    status("retired")
    assert "reason" not in fm.read(path)[0]
    status("active", "--reason", "ignored")
    assert fm.read(path) == ({**data, "status": "active"}, body)
    assert schedule.load(Config.load(vault))["chicken-alerts"].enabled
    assert len(locks) == 5
    assert "set responsibility chicken-alerts status retired" in capsys.readouterr().out


@pytest.mark.parametrize("args, message", [
    (["missing", "paused"], "unknown folder responsibility: missing"),
    (["chicken-alerts", "gone"], "status must be active, paused or retired"),
    (["chicken-alerts"], "use status <id>"),
    (["chicken-alerts", "active", "extra"], "use status <id>"),
])
def test_status_cli_errors_leave_document_untouched(vault, args, message):
    path = folder(vault)
    config = Config.load(vault)
    schedule.save(config, {"chicken-alerts": schedule.Routine(every="2h", responsibility="chicken-alerts", enabled=False)})
    before = path.read_bytes()
    with pytest.raises(SystemExit, match=message):
        cli.main(["responsibility", "status", *args, "--vault", str(vault)])
    assert path.read_bytes() == before
    assert not (vault / config.runtime_dir / responsibilities.STATUS_MIGRATION).exists()


def test_retired_stays_exported_and_out_of_working_context_and_matters(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-12")
    active = folder(vault, "active")
    retired = folder(vault, "retired", status="retired", ended=date(2026, 11, 10), reason="Owner ended it", tier="hot")
    data, _ = fm.read(retired)
    fm.write(retired, data, "## Strategy\n\nRetired strategy.\n\n## Now\n\nDeadline 2026-11-13.\n")
    loaded = Vault(vault)
    active_hub, retired_hub = (path.relative_to(vault).as_posix() for path in (active, retired))
    hubs, within = session_context._own_scope(loaded)
    assert active_hub in hubs and retired_hub not in within
    assert retired_hub in matters._responsibility_hubs(loaded)
    assert matters.find(loaded, retired_hub) is None
    context = compile_context.build(loaded)
    assert "Retired strategy" not in context and "Deadline 2026-11-13" not in context
    responsibilities.sync(loaded)
    cli.main(["state", "--json", "--vault", str(vault)])
    entry = next(entry for entry in json.loads(capsys.readouterr().out)["responsibilities"] if entry["id"] == "retired")
    assert {key: entry[key] for key in ("status", "ended", "reason", "routines")} == {
        "status": "retired", "ended": "2026-11-10", "reason": "Owner ended it", "routines": []}


def test_retiring_removes_declared_once_and_hand_scheduled_runs(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-12")
    folder(vault, triggers=[{"kind": "schedule", "when": "every 1h"},
                            {"kind": "schedule", "when": "once 2026-11-13T19:00:00-08:00"}])
    loaded = Vault(vault)
    responsibilities.sync(loaded)
    routines = schedule.load(loaded.config)
    routines["extra"] = schedule.Routine(at="2026-11-13T19:00:00-08:00", responsibility="chicken-alerts")
    schedule.save(loaded.config, routines)
    cli.main(["responsibility", "status", "chicken-alerts", "retired", "--vault", str(vault)])
    assert schedule.load(loaded.config) == {}


def test_adopt_takes_a_routine_with_a_dangling_responsibility_and_a_skill_that_is_gone(vault):
    folder(vault, "housing")
    config = Config.load(vault)
    responsibilities.sync(Vault(vault))
    routines = schedule.load(config)
    routines["followup"] = schedule.Routine(cron="0 9 * * *", skills=["brain-gone"], responsibility="nyc-housing")
    schedule.save(config, routines)
    responsibilities.adopt(config, ["followup"], into="housing")
    text = (vault / "Responsibilities/housing/RESPONSIBILITY.md").read_text()
    assert "`brain-gone`, which no longer exists" in text
    assert schedule.load(config)["housing-2"].responsibility == "housing"
