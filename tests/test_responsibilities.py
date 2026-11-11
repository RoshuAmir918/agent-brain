"""Responsibility ids, schedule ownership and state compatibility."""
import io
import shutil
from datetime import date

import pytest

from brain import cli, fm, nightly, responsibilities, schedule, state, validate
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
         "needs": [], "work": None, "delivers": None, "folder": False}]
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
        "needs": ["computer"], "work": "flock", "delivers": "Healthy chickens", "folder": True,
        "triggers": [{"kind": "schedule", "when": "every 2h"}], "version": 1,
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
def test_derive_timing(vault, when, timing):
    folder(vault, triggers=[{"kind": "schedule", "when": when}])
    loaded = Vault(vault)
    actual = responsibilities.derive(loaded.config, responsibilities.discover(loaded)[0])
    assert actual == {"chicken-alerts": schedule.Routine(**timing, responsibility="chicken-alerts", deliver="demiurge",
        prompt="Your responsibility Chicken alerts: read Responsibilities/chicken-alerts/RESPONSIBILITY.md and do what it says. "
               "Its state between runs lives in Responsibilities/chicken-alerts/records.")}


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
    assert first.script == "Responsibilities/chicken-alerts/scripts/check.py"
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
    before = path.read_bytes()
    changes = responsibilities.sync(loaded)
    assert "updated routine chicken-alerts" in changes
    assert {"removed routine chicken-alerts-2", "removed routine custom-alias", "removed routine gone",
            "removed routine gone-2", "removed routine gone-10"} <= set(changes)
    actual = schedule.load(config)
    assert set(actual) == {"legacy", "legacy-2", "engine", "chicken-alerts", "gone-manual"}
    assert actual["legacy"] == actual["legacy-2"] == legacy
    assert actual["chicken-alerts"].every == "2h" and actual["chicken-alerts"].enabled is False
    assert path.read_bytes() == before
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
    assert schedule.load(Config.load(vault))["chicken-alerts"].script == "Responsibilities/chicken-alerts/scripts/check.py"
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


@pytest.mark.parametrize("name", ["add", "sync"])
def test_legacy_creation_keeps_names_that_match_new_commands(vault, tmp_path, monkeypatch, name):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    cli.main(["responsibility", name, "--skill", f"brain-{name}", "--vault", str(vault)])
    assert (vault / f"{name}.md").is_file()
    assert (vault / f"Runtime/skills/brain-{name}/SKILL.md").is_file()
    assert (tmp_path / "agent-home/records" / name).is_dir()
    assert not (vault / "Responsibilities").exists()

