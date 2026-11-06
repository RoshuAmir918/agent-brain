"""Responsibility ids, schedule ownership and state compatibility."""
from datetime import date

import pytest

from brain import fm, responsibilities, schedule, state, validate
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
    hub(vault, "First", records="Chicken_alerts")
    hub(vault, "Second", records="chicken-alerts")
    hub(vault, "!!!")
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
         "needs": [], "work": None, "delivers": None}]
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


def test_discovery_includes_title_and_legacy_hubs_but_not_ordinary_notes(vault):
    hub(vault, "Title only")
    fm.write(vault / "Legacy.md", {"skill": "brain-legacy"}, "")
    fm.write(vault / "Ordinary.md", {"tier": "hot"}, "")
    hub(vault, "Draft", kind="draft", skill="brain-draft")
    assert [entry["id"] for entry in responsibilities.discover(Vault(vault))] == ["legacy", "title-only"]


def test_private_responsibility_owns_routine_without_exporting_its_id(vault):
    config = Config.load(vault)
    hub(vault, "Visible", skill="brain-shared", records="visible")
    hub(vault, "Private", skill="brain-shared", records="secret", tier="private")
    schedule.save(config, {"visible": schedule.Routine(every="1h", responsibility="secret")})
    snapshot = state.build(config, date(2026, 11, 6))
    assert [entry["id"] for entry in snapshot["responsibilities"]] == ["visible"]
    assert snapshot["responsibilities"][0]["routines"] == []
    assert snapshot["routines"][0]["responsibility"] is None
