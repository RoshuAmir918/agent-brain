"""Held work, its after-conversation capture, and the owner's view of it."""
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

from brain import fm, journal, ledger, loops, needs, nightly, state, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

AT = datetime(2026, 10, 28, 12, 0)


@pytest.fixture
def config(vault):
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm"}, "# Trip\n")
    return Config.load(vault)


def test_parse_format_and_stable_ids():
    item = loops.Item("owed", "Relay the answer", "2026-10-28 12:00", "Trip", "Kevin", "2026-10-29 09:30")
    body = loops.TEMPLATE + loops.render(item)  # only the Open section should be read
    assert loops.parse_section(body) == []
    body = "## Open\n\n" + loops.render(item) + "\n\n## Closed\n"
    assert loops.parse_section(body) == [item]
    item.closed, item.outcome, item.because = "2026-10-29 10:00", "done", "Answer delivered"
    assert loops.parse_section("## Closed\n" + loops.render(item), "Closed") == [item]
    equivalent = loops.Item("owed", " RELAY   the ANSWER ", "2026-11-01 01:00", "Elsewhere")
    assert equivalent.id == item.id
    assert loops.Item("promised", item.text, item.since, item.source).id != item.id


@pytest.mark.parametrize("line", [
    "- 2026-02-30 12:00 · owed · Answer · [[Trip]]",
    "- 2026-10-28 25:00 · owed · Answer · [[Trip]]",
    "- 2026-10-28 · owed · Answer · [[Trip]]",
    "- 2026-10-28 12:00 · ask · Answer · [[Trip]]",
    "- 2026-10-28 12:00 · owed · Answer · [[ ]]",
    "- 2026-10-28 12:00 · owed · Answer · more · [[Trip]]",
])
def test_malformed_bullets_are_not_open_loops(line):
    assert loops.parse_section("## Open\n" + line) == []


def test_add_deduplicates_open_text_without_changing_original(config):
    assert loops.add(config, AT, "promised", "Send the link", "Trip", "Kevin", "2026-10-29")
    before = snapshot(config.vault_path)
    assert not loops.add(config, AT, "owed", "  SEND\n the   link ", "Elsewhere")
    assert snapshot(config.vault_path) == before
    data, body = fm.read(loops.path_for(config))
    assert data == {"kind": "orientation", "tier": "warm", "pinned": False}
    assert body.startswith("# Loops\n") and "## Open" in body and "## Closed" in body
    [item] = loops.open_loops(config)
    assert (item.with_, item.due, item.source) == ("Kevin", "2026-10-29", "Trip")


@pytest.mark.parametrize("kwargs", [
    {"kind": "ask"}, {"text": " "}, {"source": "[[Trip]]"}, {"source": "x\ny"},
    {"due": "tomorrow"}, {"due": "2026-02-30"}, {"due": "2026-10-29T09:00"},
])
def test_bad_add_writes_nothing(config, kwargs):
    args = {"kind": "owed", "text": "Send answer", "source": "Trip", **kwargs}
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError):
        loops.add(config, AT, **args)
    assert snapshot(config.vault_path) == before


@pytest.mark.parametrize("outcome", ["done", "dropped"])
def test_close_moves_all_details_to_closed_and_allows_reopening(config, outcome):
    loops.add(config, AT, "owed", "Send answer", "Trip", "Kevin", "2026-10-29")
    [item] = loops.open_loops(config)
    closed = loops.close(config, item.id, AT + timedelta(days=1), outcome, "The reason")
    assert loops.open_loops(config) == []
    assert loops.closed_recent(config) == [closed]
    assert (closed.id, closed.closed, closed.outcome, closed.because) == (
        item.id, "2026-10-29 12:00", outcome, "The reason")
    assert (closed.with_, closed.due) == ("Kevin", "2026-10-29")
    assert loops.add(config, AT + timedelta(days=2), "owed", "Send answer", "Trip")
    assert loops.open_loops(config)[0].id == item.id


def test_close_by_exact_or_unique_text_and_ambiguous_text_is_refused(config):
    for text in ("Send answer", "Send answer to Kevin", "Check fares"):
        loops.add(config, AT, "owed", text, "Trip")
    assert loops.close(config, "SEND   answer", AT, "done", "Delivered").text == "Send answer"
    assert loops.close(config, "fares", AT, "dropped", "Trip cancelled").text == "Check fares"
    loops.add(config, AT, "owed", "Send answer to Pat", "Trip")
    before = snapshot(config.vault_path)
    for which, outcome, because in (("Send", "done", "Delivered"), ("unknown", "done", "Done"),
                                     ("Send", "failed", "Error"), ("Send", "done", " ")):
        with pytest.raises(ValueError):
            loops.close(config, which, AT, outcome, because)
    assert snapshot(config.vault_path) == before


def test_closed_archives_oldest_fifty_retained_by_closing_month(config):
    # Reopening the same text must not erase other closures with the same id.
    for index in range(53):
        at = datetime(2026, 9, 30, 23, 58) + timedelta(minutes=index)
        loops.add(config, at, "promised", "Check again", "Trip", "Kevin")
        loops.close(config, "Check again", at, "done", f"Check {index}")
    kept = loops.closed_recent(config)
    assert len(kept) == 50 and kept[0].because == "Check 52" and kept[-1].because == "Check 3"
    archives = config.vault_path / config.agent("Loops")
    september = archives / "2026-09.md"
    october = archives / "2026-10.md"
    assert fm.read(september)[0] == {"kind": "log", "tier": "cold"}
    archived = loops.parse_section("## Closed\n" + fm.read(september)[1], "Closed")
    archived += loops.parse_section("## Closed\n" + fm.read(october)[1], "Closed")
    assert [item.because for item in archived] == ["Check 0", "Check 1", "Check 2"]
    assert all(item.with_ == "Kevin" and item.outcome == "done" for item in archived)
    assert ledger.discover(config) == []   # loop archives are engine logs, never routine ledgers


@pytest.mark.parametrize("kind,due,hours,expected", [
    ("promised", "", 3.99, False), ("promised", "", 4, True), ("owed", "", 4, True),
    ("owed", "2026-10-28 13:00", 0.99, False), ("owed", "2026-10-28 13:00", 1, True),
    ("promised", "2026-10-30", 24, False), ("promised", "2026-10-29", 12, True),
    ("waiting", "", 23.99, False), ("waiting", "", 24, True),
    ("waiting", "2026-10-28 13:00", 5, False), ("waiting", "2026-10-28 13:00", 24, True),
    ("waiting", "2026-10-30", 24, False),
])
def test_due_rules(config, kind, due, hours, expected):
    loops.add(config, AT, kind, "Follow through", "Trip", due=due)
    assert bool(loops.due(config, AT + timedelta(hours=hours))) is expected


def test_due_respects_owner_zone_and_elapsed_hours_across_dst(config):
    at = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)  # local midnight, before the clocks fall back
    loops.add(config, at, "promised", "Check", "Trip")
    assert loops.open_loops(config)[0].since == "2026-11-01 00:00"
    assert not loops.due(config, at + timedelta(hours=3, minutes=59))
    assert loops.due(config, at + timedelta(hours=4))


def test_cli_lists_adds_closes_and_quiet_exit_codes(config, monkeypatch, capsys):
    monkeypatch.setattr(Config, "now", lambda self, moment=None: AT)
    argv = ["loops", "--vault", str(config.vault_path)]
    nightly.main(argv + ["--due"])
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit) as error:
        nightly.main(argv + ["--due", "--quiet"])
    assert error.value.code == 1 and capsys.readouterr().out == ""
    nightly.main(argv + ["--add", "Relay answer", "--kind", "owed", "--source", "Trip", "--with", "Kevin",
                         "--due", "2026-10-28 11:00"])
    assert "added loop" in capsys.readouterr().out
    nightly.main(argv)
    line = capsys.readouterr().out
    assert "owed · Relay answer · [[Trip]]" in line and "with: Kevin" in line and len(line.splitlines()) == 1
    nightly.main(argv + ["--due"])
    assert capsys.readouterr().out == line
    with pytest.raises(SystemExit) as error:
        nightly.main(argv + ["--quiet"])
    assert error.value.code == 0 and capsys.readouterr().out == ""
    nightly.main(argv + ["--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["open"] == result["due"] and result["closed_recent"] == []
    nightly.main(argv + ["--close", result["open"][0]["id"], "--outcome", "dropped", "--because", "No longer needed"])
    assert "closed (dropped)" in capsys.readouterr().out
    nightly.main(argv + ["--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["open"] == result["due"] == [] and result["closed_recent"][0]["outcome"] == "dropped"


@pytest.mark.parametrize("extra", [
    ["--add", "Check"], ["--add", "Check", "--kind", "owed", "--source", "Trip", "--due"],
    ["--close", "Check"], ["--source", "Trip"], ["--outcome", "done"], ["--due", "2026-10-29"],
])
def test_cli_rejects_incomplete_mutations(config, extra):
    with pytest.raises(SystemExit, match="brain loops:"):
        nightly.main(["loops", "--vault", str(config.vault_path), *extra])


def test_cli_empty_due_exits_zero_in_a_subprocess(config):
    result = subprocess.run([sys.executable, "-m", "brain", "loops", "--due", "--vault", str(config.vault_path)],
                            text=True, capture_output=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_conversation_adds_and_closes_without_journal_rows(config):
    loops.add(config, AT, "promised", "Check fares", "Trip")
    item = loops.open_loops(config)[0]
    record = {"headline": "Checked fares", "outcome": "nothing", "touched": ["Missing", "Trip"],
              "closed": [item.id, "already gone"], "loops": [
                  {"kind": "owed", "text": "Send the options", "with": "Kevin", "due": None}]}
    path = journal.append_entry(config, record, "cli", AT)
    assert path == loops.path_for(config)
    assert [loop.text for loop in loops.open_loops(config)] == ["Send the options"]
    assert loops.open_loops(config)[0].source == "Trip"
    assert loops.closed_recent(config)[0].because == "closed in conversation"
    assert not (config.vault_path / config.agent("Journal")).exists()
    record["closed"], record["loops"] = ["Send the options"], []
    journal.append_entry(config, record, "cli", AT)
    assert loops.open_loops(config) == []


def test_quiet_record_writes_nothing_and_default_source_is_conversations(config):
    before = snapshot(config.vault_path)
    journal.append_entry(config, {"headline": "Small talk", "outcome": "nothing"}, "cli", AT)
    assert snapshot(config.vault_path) == before
    journal.append_entry(config, {"headline": "Will check", "outcome": "progress",
                                 "loops": [{"kind": "promised", "text": "Check fares"}]}, "cli", AT)
    assert loops.open_loops(config)[0].source == "Conversations"


def test_state_keys_ten_recent_closures_and_source_privacy(config):
    loops.add(config, AT, "waiting", "Await options", "Trip", "Kevin", "2026-10-30")
    public = loops.open_loops(config)[0]
    for index in range(12):
        loops.add(config, AT, "owed", f"Answer {index}", "Trip")
        loops.close(config, f"Answer {index}", AT + timedelta(minutes=index), "done", "Delivered")
    for source in ("people/Pat/Profile", "human/Profile", "Secret", "../elsewhere"):
        loops.add(config, AT, "owed", f"Held for {source}", source)
        loops.add(config, AT, "owed", f"Closed for {source}", source)
        loops.close(config, f"Closed for {source}", AT + timedelta(days=1), "dropped", "No need")
    fm.write(config.vault_path / "Secret.md", {"kind": "hub", "tier": "private"}, "# Secret\n")
    result = state.build(config, AT.date())
    assert result["loops"] == [{**loops.as_dict(public), "source": "Trip.md"}]
    assert len(result["loopsClosed"]) == 10 and result["loopsClosed"][0]["text"] == "Answer 11"
    assert result["loopsClosed"][0]["closed"] == "2026-10-28 12:11"
    assert result["journal"] == []


def test_hygiene_names_only_open_loops_older_than_fourteen_days(config, monkeypatch):
    monkeypatch.setattr(Config, "today", lambda self: date(2026, 10, 28))
    loops.add(config, AT - timedelta(days=15), "promised", "Old promise", "Trip")
    loops.add(config, AT - timedelta(days=14), "owed", "At boundary", "Trip")
    loops.add(config, AT - timedelta(days=20), "owed", "Finished", "Trip")
    loops.close(config, "Finished", AT, "done", "Delivered")
    problems = validate.loops_hygiene(Vault(config.vault_path))
    assert len(problems) == 1 and "Old promise" in problems[0]
    assert "decide it: close it, drop it, or set a due date" in problems[0]
    assert problems[0] in validate.hygiene(Vault(config.vault_path))


@pytest.mark.parametrize("kind", loops.KINDS)
def test_ready_round_trips_and_overrides_age_and_future_deadline(config, kind, monkeypatch, capsys):
    loops.add(config, AT, kind, "Follow through", "Trip", "Kevin", "2026-12-01")
    [item] = loops.open_loops(config)
    assert loops.due(config, AT) == []
    marked = loops.ready(config, item.id, AT)
    assert marked.ready == "2026-10-28 12:00"
    assert loops.parse_section("## Open\n" + loops.render(marked)) == [marked]
    assert loops.open_loops(config) == loops.due(config, AT) == [marked]
    before = snapshot(config.vault_path)
    assert loops.ready(config, "FOLLOW  THROUGH", AT + timedelta(hours=1)) == marked
    assert snapshot(config.vault_path) == before
    monkeypatch.setattr(Config, "now", lambda self, moment=None: AT)
    nightly.main(["loops", "--json", "--vault", str(config.vault_path)])
    result = json.loads(capsys.readouterr().out)
    assert result["open"][0]["ready"] == result["due"][0]["ready"] == marked.ready
    closed = loops.close(config, item.id, AT, "done", "Relayed")
    assert loops.closed_recent(config) == [closed] and closed.ready == marked.ready
    with pytest.raises(ValueError, match="no open loop"):
        loops.ready(config, item.id, AT)


def test_ready_preserves_noncanonical_details_and_other_sections(config):
    path = loops.path_for(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(loops.TEMPLATE.replace("## Open\n", "## Open\n\n"
                    "- 2026-10-28 12:00 · owed · Relay answer · [[Trip]]\n"
                    "    - due: 2026-12-01\n  - with: Kevin\n"), encoding="utf-8")
    loops.ready(config, "Relay answer", AT)
    [item] = loops.open_loops(config)
    assert (item.ready, item.due, item.with_) == ("2026-10-28 12:00", "2026-12-01", "Kevin")
    assert "## Closed\n" in path.read_text()


@pytest.mark.parametrize("by_id", [True, False])
def test_record_resolves_asks_and_marks_ready_tolerating_retries(config, by_id):
    journal.add_ask(config, AT.date(), "Which date?", "Trip")
    [ask] = needs.items(config, AT.date())
    loops.add(config, AT, "owed", "Relay the date", "Trip", "Kevin", "2026-12-01")
    [loop] = loops.open_loops(config)
    record = {"headline": "Got the date", "outcome": "nothing",
              "resolved": [ask.id if by_id else ask.text, "gone-ask-id"],
              "ready": [loop.id if by_id else loop.text, "gone-loop-id"]}
    journal.append_entry(config, record, "cli", AT)
    assert needs.items(config, AT.date()) == []
    assert loops.open_loops(config)[0].ready == "2026-10-28 12:00"
    log = config.vault_path / config.agent("Answered/2026-10.md")
    assert "Which date? · because answered in conversation" in log.read_text()
    before = snapshot(config.vault_path)
    journal.append_entry(config, record, "cli", AT + timedelta(minutes=1))
    assert snapshot(config.vault_path) == before
    loops.close(config, loop.id, AT, "done", "Delivered")
    before = snapshot(config.vault_path)
    journal.append_entry(config, record, "cli", AT)
    assert snapshot(config.vault_path) == before


def test_record_does_not_resolve_or_ready_a_partial_text_match(config):
    journal.add_ask(config, AT.date(), "Which date for Lisbon?", "Trip")
    loops.add(config, AT, "owed", "Relay the date for Lisbon", "Trip")
    before = snapshot(config.vault_path)
    journal.append_entry(config, {"headline": "Quiet", "outcome": "nothing", "resolved": ["Which date"],
                                 "ready": ["Relay the date"]}, "cli", AT)
    assert snapshot(config.vault_path) == before
