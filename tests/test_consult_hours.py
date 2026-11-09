"""Colleagues' stated working hours and absences live in the ownership map."""
from dataclasses import replace
from datetime import datetime
import json

import pytest

from brain import cli, consults, fm, needs, owners, routing, state, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

KIM = "slack:U01KIM"
LEE = "slack:U02LEE"
ANA = "slack:U03ANA"
TABLE = f"""| area | owner | backup | covers | wait | source |
| --- | --- | --- | --- | --- | --- |
| infrastructure | Kim ({KIM}) | Lee ({LEE}) | staging, deploys | 4 | team meeting |
"""
PEOPLE = "\n## People\n\n| person | hours | away |\n| --- | --- | --- |\n"
SCHEDULE = dict(days=[2, 3, 4], start="10:00", end="14:00", zone="America/New_York")


def clock(monkeypatch, at):
    monkeypatch.setattr(Config, "now", lambda self: datetime.fromisoformat(at).replace(tzinfo=self.zone))
    monkeypatch.setenv("BRAIN_TODAY", at[:10])


@pytest.fixture
def enterprise(vault, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    (vault / "agent/Owners.md").write_text(TABLE)
    clock(monkeypatch, "2026-11-12 10:00")
    return Config.load(vault)


def people(config, rows):
    (config.vault_path / config.agent("Owners.md")).write_text(TABLE + PEOPLE + rows)


def ask(config, **kwargs):
    values = dict(to=KIM, text="Touch the staging cluster during release week?", default="Leave staging alone.",
                  why="Kim coordinates the release.", area="infrastructure")
    values.update(kwargs)
    return consults.ask(config, **values)


def profile(config):
    path = config.vault_path / config.people_dir / "Kim/Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {}, "# Kim\n- **Slack:** U01KIM\n- **GitHub:** kim\n- **Email:** kim@example.com\n")


@pytest.mark.parametrize("cell,days,start,end,zone", [
    ("", [1, 2, 3, 4, 5], "09:00", "17:00", "America/Los_Angeles"),
    ("Mon-Fri 09:00-17:00", [1, 2, 3, 4, 5], "09:00", "17:00", "America/Los_Angeles"),
    ("Tue-Thu 10:00-14:00 America/New_York", [2, 3, 4], "10:00", "14:00", "America/New_York"),
    ("Mon,Tue,Thu 08:30-13:15 Europe/London", [1, 2, 4], "08:30", "13:15", "Europe/London"),
    ("Fri-Mon 10:00-14:00", [1, 5, 6, 7], "10:00", "14:00", "America/Los_Angeles"),
    ("Sun 00:00-23:59 Asia/Tokyo", [7], "00:00", "23:59", "Asia/Tokyo"),
])
def test_people_parse_ranges_lists_zones_and_defaults(enterprise, cell, days, start, end, zone):
    people(enterprise, f"| Kim ({KIM}) | {cell} | |\n")
    hours, = owners.people(enterprise).values()
    assert hours.schedule() == dict(days=days, start=start, end=end, zone=zone)
    assert hours.name == "Kim" and hours.away == hours.away_start == ""
    assert owners.problems(enterprise) == []
    assert owners.load(enterprise) == [owners.Owner("infrastructure", KIM, "Kim", LEE, "Lee",
                                                     "staging, deploys", 4, "team meeting")]


@pytest.mark.parametrize("cell,start,end", [
    ("", "", ""),
    ("2026-11-12 to 2026-11-16", "2026-11-12", "2026-11-16"),
    ("2026-11-12 to 2026-11-12", "2026-11-12", "2026-11-12"),
    ("until 2026-11-16", "", "2026-11-16"),
])
def test_away_forms(enterprise, cell, start, end):
    people(enterprise, f"| Kim ({KIM}) | | {cell} |\n")
    hours = owners.people(enterprise)[KIM]
    assert (hours.away_start, hours.away) == (start, end)
    assert hours.away_on(enterprise.local()) == end


def test_blank_hours_and_omitted_zone_follow_config(enterprise):
    people(enterprise, f"| Kim ({KIM}) | | |\n| Lee ({LEE}) | Tue-Thu 10:00-14:00 | |\n")
    config = replace(enterprise, timezone="Europe/London")
    assert {hours.zone for hours in owners.people(config).values()} == {"Europe/London"}
    assert owners.hours_for(config, ANA).zone == "Europe/London"


@pytest.mark.parametrize("row,message", [
    ("| Kim | | |", "Name"),
    (f"| Kim ({KIM}) | weekdays 09:00-17:00 | |", "days"),
    (f"| Kim ({KIM}) | Mon,Foo 09:00-17:00 | |", "days"),
    (f"| Kim ({KIM}) | Mon--Fri 09:00-17:00 | |", "days"),
    (f"| Kim ({KIM}) | Mon-Fri 9:00-17:00 | |", "hours"),
    (f"| Kim ({KIM}) | Mon-Fri 09:60-17:00 | |", "hours"),
    (f"| Kim ({KIM}) | Mon-Fri 09:00-24:00 | |", "hours"),
    (f"| Kim ({KIM}) | Mon-Fri 17:00-09:00 | |", "hours"),
    (f"| Kim ({KIM}) | Mon-Fri 09:00-09:00 | |", "hours"),
    (f"| Kim ({KIM}) | Mon-Fri 09:00-17:00 Unknown/Zone | |", "zone"),
    (f"| Kim ({KIM}) | | tomorrow |", "away"),
    (f"| Kim ({KIM}) | | until 2026-02-30 |", "away"),
    (f"| Kim ({KIM}) | | 2026-11-16 to 2026-11-12 |", "away"),
    (f"| Kim ({KIM}) | | | extra |", "columns"),
])
def test_bad_people_rows_reported_and_skipped_without_stalling(enterprise, row, message):
    people(enterprise, row + f"\n| Lee ({LEE}) | | |\n")
    assert set(owners.people(enterprise)) == {LEE}
    problem, = owners.problems(enterprise)
    assert "Owners.md:9:" in problem and message in problem
    assert problem in validate.checks(Vault(enterprise.vault_path))
    assert len(owners.load(enterprise)) == 1
    assert ask(enterprise).until == "2026-11-12 14:00"


def test_only_first_area_table_and_people_table_are_read(enterprise):
    path = enterprise.vault_path / "agent/Owners.md"
    assert owners.people(enterprise) == {}
    people(enterprise, f"| Kim ({KIM}) | | |\n")
    path.write_text(path.read_text() + "\n## Notes\n| unrelated | table |\n| --- | --- |\n| text | text |\n"
                    + "\n## Restricted\n- infrastructure\n")
    assert len(owners.load(enterprise)) == len(owners.people(enterprise)) == 1
    assert owners.problems(enterprise) == []
    assert owners.restricted(enterprise) == {"infrastructure"}
    path.write_text(PEOPLE + f"| Kim ({KIM}) | | |\n\n## Areas\n" + TABLE)
    assert len(owners.load(enterprise)) == len(owners.people(enterprise)) == 1
    path.unlink()
    assert owners.load(enterprise) == owners.problems(enterprise) == []
    assert owners.people(enterprise) == {}


@pytest.mark.parametrize("person", [KIM, "github:kim", "email:kim@example.com"])
def test_alias_lookup_for_waits_and_exports(enterprise, monkeypatch, person):
    profile(enterprise)
    people(enterprise, "| Kim (github:kim) | Tue-Thu 10:00-14:00 America/New_York | |\n")
    before = snapshot(enterprise.repo)
    assert set(owners.people(enterprise)) == {KIM}
    assert owners.hours_for(enterprise, person).schedule() == SCHEDULE
    assert snapshot(enterprise.repo) == before
    clock(monkeypatch, "2026-11-13 16:00")
    entry = ask(enterprise, to=person)
    assert entry.asked == "2026-11-13 16:00" and entry.until == "2026-11-17 11:00"
    item, = consults.items(enterprise)
    assert item.owner == person and item.hours == SCHEDULE


def test_duplicate_alias_row_reported_and_skipped(enterprise):
    profile(enterprise)
    people(enterprise, f"| Kim ({KIM}) | Tue-Thu 10:00-14:00 | |\n"
           "| Kim (github:kim) | Mon-Fri 09:00-17:00 | |\n")
    assert owners.people(enterprise)[KIM].days == (2, 3, 4)
    assert len(owners.people(enterprise)) == 1
    assert "duplicate person" in owners.problems(enterprise)[0]


@pytest.mark.parametrize("asked,wait,until", [
    ("2026-11-13 16:00", 4, "2026-11-17 11:00"),
    ("2026-11-12 10:00", 4, "2026-11-17 10:00"),
    ("2026-11-17 06:00", 4, "2026-11-17 11:00"),
    ("2026-11-17 07:00", 8, "2026-11-18 11:00"),
    ("2026-11-17 11:00", 4, "2026-11-18 11:00"),
    ("2026-10-30 16:00", 4, "2026-11-03 11:00"),
])
def test_working_until_in_recipient_days_hours_and_zone(enterprise, asked, wait, until):
    people(enterprise, f"| Kim ({KIM}) | Tue-Thu 10:00-14:00 America/New_York | |\n")
    moment = enterprise.local(datetime.fromisoformat(asked))
    result = consults.working_until(moment, wait, owners.people(enterprise)[KIM])
    assert result.strftime("%Y-%m-%d %H:%M") == until
    assert result.tzinfo == enterprise.zone


def test_wait_carries_partial_hours_and_future_absences(enterprise, monkeypatch):
    people(enterprise, f"| Kim ({KIM}) | Mon,Tue,Thu 10:30-14:15 | 2026-11-16 to 2026-11-17 |\n")
    clock(monkeypatch, "2026-11-12 13:45")
    entry = ask(enterprise)
    assert entry.until == "2026-11-19 14:00"


def test_redirect_and_lapse_use_new_recipients_hours(enterprise, monkeypatch):
    people(enterprise, f"| Lee ({LEE}) | Tue-Thu 10:00-14:00 America/New_York | |\n"
           f"| Ana ({ANA}) | Mon 12:30-16:30 Asia/Tokyo | |\n")
    entry = ask(enterprise)
    clock(monkeypatch, "2026-11-13 16:00")
    moved = consults.answer(enterprise, entry.id, "redirected", "Ask Lee about staging.", KIM, LEE)
    assert moved.to == LEE and moved.until == "2026-11-17 11:00"
    assert consults.items(enterprise)[0].hours == SCHEDULE
    consults.add_evidence(enterprise, "infrastructure", enterprise.today(), ANA, "deferred-to", "Ana decides on staging.")
    clock(monkeypatch, "2026-11-17 11:00")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {ANA}"]
    moved, = consults.load(enterprise)
    assert moved.asked == "2026-11-17 11:00" and moved.until == "2026-11-22 23:30"
    assert consults.items(enterprise)[0].hours == dict(days=[1], start="12:30", end="16:30", zone="Asia/Tokyo")


def test_away_refusal_names_return_and_next_available_unmuted_candidate(enterprise, capsys):
    profile(enterprise)
    people(enterprise, "| Kim (github:kim) | | until 2026-11-16 |\n"
           f"| Lee ({LEE}) | | 2026-11-12 to 2026-11-12 |\n")
    consults.add_evidence(enterprise, "infrastructure", enterprise.today(), ANA, "reviewed", "Ana reviewed staging.")
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match=f"away through 2026-11-16; back 2026-11-17; try {ANA}"):
        ask(enterprise)
    with pytest.raises(SystemExit, match=f"try {ANA}"):
        cli.main(["consult", "ask", "--vault", str(enterprise.vault_path), "--to", "email:kim@example.com",
                  "--ask", "Touch the staging cluster during release week?", "--default", "Leave staging alone.",
                  "--why", "Kim coordinates the release."])
    assert snapshot(enterprise.repo) == before
    ranked = consults.candidates(enterprise, "", "infrastructure")
    assert [entry.person for entry in ranked] == [ANA, KIM, LEE]
    assert [entry.away for entry in ranked] == ["", "2026-11-16", "2026-11-12"]
    cli.main(["consult", "candidates", "--vault", str(enterprise.vault_path), "--area", "infrastructure", "--about", "staging"])
    assert "away: 2026-11-16" in capsys.readouterr().out
    cli.main(["consult", "candidates", "--vault", str(enterprise.vault_path), "--area", "infrastructure", "--about", "staging", "--json"])
    assert json.loads(capsys.readouterr().out)[1]["away"] == "2026-11-16"
    path = routing.area_path(enterprise, "infrastructure")
    fm.write(path, {}, f"## Not asked\n- 2026-11-12 · {ANA} · stop-asking\n")
    with pytest.raises(ValueError, match="no available unmuted candidate"):
        ask(enterprise)


def test_away_uses_inclusive_local_dates_and_allows_future_absence(enterprise, monkeypatch):
    people(enterprise, f"| Kim ({KIM}) | Mon-Fri 09:00-17:00 Asia/Tokyo | 2026-11-13 to 2026-11-13 |\n")
    hours = owners.people(enterprise)[KIM]
    clock(monkeypatch, "2026-11-12 06:59")   # November 12 at 23:59 in Tokyo
    assert hours.away_on(enterprise.local()) == ""
    ask(enterprise)
    clock(monkeypatch, "2026-11-12 07:00")   # November 13 at 00:00 in Tokyo
    with pytest.raises(ValueError, match="back 2026-11-14"):
        ask(enterprise, text="Use the new export pipeline for reporting?")
    clock(monkeypatch, "2026-11-13 06:59")
    assert hours.away_on(enterprise.local()) == "2026-11-13"
    clock(monkeypatch, "2026-11-13 07:00")
    assert hours.away_on(enterprise.local()) == ""
    assert ask(enterprise, text="Use the new export pipeline for reporting?").to == KIM


def test_redirect_refuses_away_without_writing(enterprise):
    entry = ask(enterprise)
    people(enterprise, f"| Lee ({LEE}) | | until 2026-11-16 |\n")
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match="back 2026-11-17"):
        consults.answer(enterprise, entry.id, "redirected", "Ask Lee.", KIM, LEE)
    assert snapshot(enterprise.repo) == before


def test_lapse_skips_away_and_handles_new_absence_only_at_until(enterprise, monkeypatch):
    entry = ask(enterprise)
    people(enterprise, f"| Kim ({KIM}) | | until 2026-11-16 |\n"
           f"| Lee ({LEE}) | | until 2026-11-16 |\n")
    consults.add_evidence(enterprise, "infrastructure", enterprise.today(), ANA, "reviewed", "Ana reviewed staging.")
    clock(monkeypatch, "2026-11-12 13:59")
    assert consults.lapse(enterprise) == []
    clock(monkeypatch, "2026-11-12 14:00")
    before = snapshot(enterprise.repo)
    assert consults.lapse(enterprise, dry_run=True) == [f"{entry.id}: reassigned to {ANA}"]
    assert snapshot(enterprise.repo) == before
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {ANA}"]
    assert consults.load(enterprise)[0].tried == (KIM, ANA)


@pytest.mark.parametrize("restricted", [False, True])
def test_lapse_exhaustion_does_not_move_to_absent_owner(enterprise, monkeypatch, restricted):
    entry = ask(enterprise, to=LEE, restricted=restricted)
    people(enterprise, f"| Kim ({KIM}) | | until 2026-11-16 |\n")
    clock(monkeypatch, "2026-11-12 14:00")
    consults.lapse(enterprise)
    held, = consults.load(enterprise)
    assert held.to == LEE and held.kind == ("ask" if restricted else "confirm")
    assert held.until == "" and held.id == entry.id


def test_current_hours_and_away_on_needs_state_and_list(enterprise, capsys):
    entry = ask(enterprise)
    people(enterprise, f"| Kim ({KIM}) | Tue-Thu 10:00-14:00 America/New_York | until 2026-11-16 |\n")
    before = snapshot(enterprise.repo)
    item, = needs.items(enterprise, enterprise.today())
    assert item.hours == SCHEDULE and item.away == "2026-11-16"
    assert state.build(enterprise, enterprise.today())["needsYou"][0]["hours"] == SCHEDULE
    cli.main(["consult", "list", "--vault", str(enterprise.vault_path), "--json"])
    listed, = json.loads(capsys.readouterr().out)
    assert listed["hours"] == SCHEDULE and listed["away"] == "2026-11-16"
    assert listed["id"] == entry.id and listed["until"] == "2026-11-12 14:00"
    assert snapshot(enterprise.repo) == before
    consults.lapse(enterprise)   # Before until, current hours and away do not rewrite the stored wait.
    assert consults.load(enterprise)[0].until == entry.until


def test_default_hours_and_went_ahead_exports(enterprise, monkeypatch, capsys):
    (enterprise.vault_path / "agent/Owners.md").unlink()
    entry = ask(enterprise, area="unowned")
    expected = dict(days=[1, 2, 3, 4, 5], start="09:00", end="17:00", zone=enterprise.timezone)
    assert consults.items(enterprise)[0].hours == expected
    assert consults.items(enterprise)[0].away == ""
    clock(monkeypatch, "2026-11-12 14:00")
    consults.lapse(enterprise)
    people(enterprise, f"| Kim ({KIM}) | Tue-Thu 10:00-14:00 America/New_York | until 2026-11-16 |\n")
    confirm, = consults.items(enterprise)
    assert confirm.id == entry.id and confirm.kind == "confirm" and confirm.hours == SCHEDULE
    assert confirm.away == "2026-11-16"
    cli.main(["consult", "list", "--vault", str(enterprise.vault_path), "--json"])
    assert json.loads(capsys.readouterr().out)[0]["hours"] == SCHEDULE
