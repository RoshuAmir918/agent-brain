"""The shared evidence contract, and consults writing their events into it."""
from dataclasses import replace
from datetime import date
import json

import pytest

from brain import cli, consults, evidence, seed
from brain.evidence import Evidence
from test_consults import KIM, NOAH, TODAY, ask, enterprise  # noqa: F401


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(path))
    return path


def row(day="2026-10-14", verb="went", fact="Dinner at a bistro", hints=("places", "food"), **extra):
    return Evidence(date.fromisoformat(day), verb, fact, "own", hints, "mail:3f9a", extra)


def test_render_and_parse_round_trip():
    line = evidence.render(row(outcome="confirmed", id="abc123"))
    assert line == "- 2026-10-14 · went · Dinner at a bistro · own · places, food · mail:3f9a · outcome=confirmed · id=abc123"
    assert evidence.parse(line, "mail") == replace(row(outcome="confirmed", id="abc123"), source="mail")


def test_separators_are_stripped_so_fields_stay_positional():
    line = evidence.render(Evidence(date(2026, 10, 1), "said", "a · b\nc", "other:a friend", ("x, y",), "p"))
    assert line == "- 2026-10-01 · said · a - b c · other:a friend · x y · p"
    assert evidence.parse(line).fact == "a - b c"


@pytest.mark.parametrize("bad", [dict(verb="Went"), dict(whose="kim"), dict(fact=" "), dict(extra={"a b": "1"})])
def test_render_refuses_malformed_rows(bad):
    with pytest.raises(ValueError):
        evidence.render(replace(row(), **bad))


def test_append_dedupes_by_month_and_read_filters_newest_first(home):
    rows = [row(), row("2026-11-02", "said", "Wants less news", ("reading",)), row()]
    assert evidence.append("mail", rows) == 2
    assert evidence.append("mail", rows) == 0
    assert sorted(path.name for path in (home / "evidence/mail").iterdir()) == ["2026-10.md", "2026-11.md"]
    evidence.append("imessage", [row("2026-10-20", "shared", "A gym link", ("Play",))])
    assert [entry.verb for entry in evidence.read()] == ["said", "shared", "went"]
    assert [entry.source for entry in evidence.read(hint="play")] == ["imessage"]
    assert [entry.verb for entry in evidence.read(source="mail", since=date(2026, 10, 15))] == ["said"]


def test_same_day_rows_read_newest_first(home):
    evidence.append("consults", [row(fact="first"), row(fact="second")])
    assert [entry.fact for entry in evidence.read()] == ["second", "first"]


def test_without_a_workspace_nothing_is_written(tmp_path):
    assert evidence.append("mail", [row()]) == 0
    assert evidence.read() == []


def test_source_names_are_checked(home):
    with pytest.raises(ValueError):
        evidence.append("../mail", [row()])


def test_cli_reads_json(home, vault, capsys):
    evidence.append("mail", [row(id="1")])
    cli.main(["evidence", "--vault", str(vault), "--hint", "food", "--json"])
    assert json.loads(capsys.readouterr().out) == [dict(date="2026-10-14", verb="went", fact="Dinner at a bistro", whose="own",
                                                        hints=["places", "food"], pointer="mail:3f9a", extra={"id": "1"},
                                                        source="mail")]


def test_consult_events_become_evidence(home, enterprise):
    entry = ask(enterprise)
    consults.answer(enterprise, entry.id, "redirected", "Kim owns the staging plan.", NOAH, to=KIM)
    consults.answer(enterprise, entry.id, "confirmed", "Leave staging alone.", KIM)
    ident = consults.decide(enterprise, "infrastructure", KIM, "Freeze staging in release weeks.", "standup notes")
    rows = evidence.read(source="consults")
    assert [entry.verb for entry in rows] == ["decided", "confirmed", "redirected", "asked"]
    assert all(entry.hints == ("infrastructure",) for entry in rows)
    decided, confirmed, redirected, asked = rows
    assert asked.whose == f"other:{NOAH}" and asked.pointer == f"consult:{entry.id}" and asked.extra["id"] == entry.id
    assert redirected.extra["to"] == KIM and redirected.fact.endswith("Kim owns the staging plan.")
    assert confirmed.whose == f"other:{KIM}" and "hours" in confirmed.extra
    assert decided.pointer == f"decision:{ident}" and decided.fact.endswith("Freeze staging in release weeks.")


def test_seeded_history_is_evidence_too(home, enterprise):
    rows = [seed.Row("infrastructure", TODAY.isoformat(), KIM, "approved", "#12 Terraform deploys", "github:pr/12")]
    assert consults.record_evidence(enterprise, rows) == 1
    [found] = evidence.read(source="consults")
    assert (found.verb, found.whose, found.pointer, found.fact) == ("approved", f"other:{KIM}", "github:pr/12", "#12 Terraform deploys")


def test_recall_prefers_the_specific_fresh_fact_over_the_general_one(home):
    evidence.append("consults", [
        Evidence(date(2026, 6, 1), "approved", f"Noah approved #{n}, a migration on forms", f"other:{NOAH}", ("data",),
                 f"github:pr/{n}") for n in range(100, 110)])
    evidence.append("consults", [
        Evidence(date(2026, 10, 8), "changed", "#391 creates the analytics_events table", f"other:{KIM}", ("data",), "github:pr/391"),
        Evidence(date(2026, 10, 30), "changed", "#410 writes signups to analytics_events", f"other:{KIM}", ("data",), "github:pr/410"),
        Evidence(date(2026, 9, 1), "said", "Lunch order for the offsite", "own", ("ops",), "chat:1")])
    found = evidence.recall("Add a migration backfilling `analytics_events`", evidence.read(), date(2026, 11, 7))
    assert [entry.row.pointer for entry in found[:2]] == ["github:pr/410", "github:pr/391"]
    assert "analytics_events" in found[0].shared
    assert all(entry.row.pointer != "chat:1" for entry in found)
    assert evidence.recall("the", evidence.read(), date(2026, 11, 7)) == []


def test_recall_shows_a_consult_once_with_how_it_ended(home, enterprise):
    entry = ask(enterprise, text="Backfill analytics_events before the export?")
    consults.answer(enterprise, entry.id, "redirected", "Kim made that table.", NOAH, to=KIM)
    consults.answer(enterprise, entry.id, "confirmed", "Backfill it.", KIM)
    [found] = evidence.recall("backfill analytics_events", evidence.read(), TODAY)
    assert found.outcomes == ("asked", "redirected", "confirmed") and found.row.extra["id"] == entry.id


def test_cli_recall_json(home, vault, capsys):
    evidence.append("consults", [Evidence(date(2026, 10, 30), "changed", "#410 writes to analytics_events",
                                          f"other:{KIM}", ("data",), "github:pr/410", {"id": "x1"})])
    cli.main(["evidence", "--vault", str(vault), "--about", "analytics_events", "--json"])
    [found] = json.loads(capsys.readouterr().out)
    assert found["pointer"] == "github:pr/410" and "analytics_events" in found["shared"] and found["outcomes"] == ["changed"]
