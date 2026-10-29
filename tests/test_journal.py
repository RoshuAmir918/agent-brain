"""The journal's structured rows, the asks they raise, and the ledger rows cron runs leave."""
import io
import json
from datetime import date, datetime

import pytest

from brain import fm, journal, ledger, needs, nightly, state
from brain.config import Config

TODAY = date(2026, 10, 28)
AT = datetime(2026, 10, 28, 14, 5)


@pytest.fixture
def config(vault):
    fm.write(vault / "Lisbon trip.md", {"kind": "hub", "tier": "hot"}, "# Lisbon trip\n")
    return Config.load(vault)


def record(**overrides):
    return {"headline": "Compared March fares to Lisbon.", "outcome": "progress", "next": "Check garden rooms.",
            **overrides}


def test_entry_round_trips_with_details_and_keeps_only_touched_notes_that_exist(config):
    path = journal.append_entry(config, record(touched=["Lisbon trip", "Nowhere"], thought="Unsure | worth it.",
                                               needsYou="Book the 16th?"), "demiurge", AT)
    assert path == config.vault_path / "agent/Journal/2026-10.md"
    assert fm.read(path)[0] == {"kind": "log", "tier": "cold"}
    [entry] = journal.parse(path.read_text(encoding="utf-8"))
    assert (entry.when, entry.platform, entry.outcome, entry.headline, entry.next) == (
        "2026-10-28 14:05", "demiurge", "progress", "Compared March fares to Lisbon.", "Check garden rooms.")
    assert entry.touched == ["Lisbon trip"] and entry.thought == "Unsure / worth it." and entry.needs_you == "Book the 16th?"


def test_needs_you_becomes_an_ask_in_current_with_the_first_touched_note(config):
    journal.append_entry(config, record(touched=["Lisbon trip"], needsYou="Book the 16th?"), "demiurge", AT)
    journal.append_entry(config, record(needsYou="Which hotel?"), "slack", AT)
    items = [(item.kind, item.text, item.source) for item in needs.items(config, TODAY) if item.origin == "current"]
    assert items == [("ask", "Book the 16th?", "Lisbon trip"), ("ask", "Which hotel?", "CURRENT.md")]
    body = fm.read(config.vault_path / "CURRENT.md")[1]
    assert body.count("## Needs you") == 1 and nightly.MARK_START in body


def test_ask_lands_inside_an_existing_section_before_the_next_heading(config):
    path = config.vault_path / "CURRENT.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n## Needs you\n\n- 2026-10-27 · ask · Earlier. · [[Lisbon trip]]\n\n## Later\nTail.\n")
    journal.add_ask(config, TODAY, "Newer.", None)
    body = fm.read(path)[1]
    assert "- 2026-10-27 · ask · Earlier. · [[Lisbon trip]]\n- 2026-10-28 · ask · Newer.\n\n## Later\nTail." in body


@pytest.mark.parametrize("bad", [record(outcome="maybe"), record(headline="  ")])
def test_append_rejects_bad_records_without_writing(config, bad):
    with pytest.raises(ValueError):
        journal.append_entry(config, bad, "demiurge", AT)
    assert not (config.vault_path / "agent/Journal").exists()


def test_synthesis_sections_are_not_entries_and_recent_reaches_last_month(config):
    journal.append_entry(config, record(headline="September work."), "slack", datetime(2026, 9, 30, 9, 0))
    journal.append_synthesis(config, "A paragraph about the month.", datetime(2026, 9, 30, 10, 0))
    journal.append_entry(config, record(), "demiurge", AT)
    assert [entry.headline for entry in journal.recent(config, TODAY)] == ["September work.",
                                                                           "Compared March fares to Lisbon."]
    assert "## Synthesis 2026-09-30 10:00" in (config.vault_path / "agent/Journal/2026-09.md").read_text()


def test_journal_is_not_a_routine_ledger_and_appears_in_state(config):
    journal.append_entry(config, record(touched=["Lisbon trip"]), "demiurge", AT)
    assert all("Journal" not in path.parts for path in ledger.discover(config))
    snapshot = state.build(config, TODAY)
    assert snapshot["routines"] == [] and snapshot["ledger"] == []
    [entry] = snapshot["journal"]
    assert {key: entry[key] for key in ("at", "platform", "outcome", "headline", "touched")} == {
        "at": "2026-10-28T14:05", "platform": "demiurge", "outcome": "progress",
        "headline": "Compared March fares to Lisbon.", "touched": ["Lisbon trip"]}
    assert list(snapshot).index("journal") == list(snapshot).index("ledger") + 1


def test_cli_journal_append_json_and_synthesis(config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(record())))
    nightly.main(["journal", "append", "--vault", str(config.vault_path), "--platform", "slack", "--at", "2026-10-28 14:05"])
    monkeypatch.setattr("sys.stdin", io.StringIO("Caught up."))
    nightly.main(["journal", "append", "--vault", str(config.vault_path), "--synthesis", "--at", "2026-10-28 15:00"])
    nightly.main(["journal", "recent", "--vault", str(config.vault_path)])
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "2026-10-28 14:05 | slack | progress | Compared March fares to Lisbon. | Check garden rooms."
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    with pytest.raises(SystemExit, match="journal append"):
        nightly.main(["journal", "append", "--vault", str(config.vault_path)])


def test_ledger_append_by_skill_finds_the_routine_and_skips_night_stages(config, monkeypatch, capsys):
    fm.write(config.vault_path / "Midday duty.md", {"kind": "hub", "tier": "hot", "skill": "brain-midday-own",
                                                   "records": "midday"}, "# Midday\n")
    assert ledger.routine_for_skill(config, "brain-midday-own") == "midday"
    assert ledger.routine_for_skill(config, "brain-watch") == "watch"
    assert ledger.routine_for_skill(config, "unprefixed") == "unprefixed"
    payload = {"skill": "brain-watch", "outcome": "nothing", "account": "Checked.", "at": "2026-10-28 09:00"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    nightly.main(["ledger", "append", "--json", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out.strip().endswith("agent/Watch/2026-10.md")
    nightly.main(["ledger", "append", "--vault", str(config.vault_path), "--skill", "brain-sleep1",
                  "--outcome", "done", "--account", "Consolidated."])
    assert "night stage" in capsys.readouterr().out
    assert not (config.vault_path / "agent/Sleep1").exists()


def test_two_sessions_ending_together_both_keep_their_row(config):
    import threading
    barrier = threading.Barrier(8)

    def write(n):
        barrier.wait(5)
        journal.append_entry(config, record(headline=f"Row {n}."), "demiurge", AT)

    workers = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    rows = journal.parse((config.vault_path / "agent/Journal/2026-10.md").read_text(encoding="utf-8"))
    assert sorted(entry.headline for entry in rows) == sorted(f"Row {n}." for n in range(8))


def test_a_private_note_is_never_named_as_touched_nor_as_an_asks_source(config):
    for rel in ("people/Pat/Profile.md", "human/Profile.md"):
        (config.vault_path / rel).parent.mkdir(parents=True, exist_ok=True)
        fm.write(config.vault_path / rel, {}, "# Private\n")
    journal.append_entry(config, record(touched=["people/Pat/Profile", "human/Profile", "Lisbon trip"],
                                        needsYou="Who is Pat · really?"), "demiurge", AT)
    [entry] = journal.parse((config.vault_path / "agent/Journal/2026-10.md").read_text(encoding="utf-8"))
    assert entry.touched == ["Lisbon trip"]
    [ask] = [item for item in needs.items(config, TODAY) if item.origin == "current"]
    assert (ask.text, ask.source) == ("Who is Pat - really?", "Lisbon trip")


def test_the_nightly_pull_stashes_rows_written_after_the_agent_committed(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(nightly, "sh", lambda repo, *args: calls.append(args))
    nightly.push(vault)
    assert calls[0][:3] == ("pull", "--rebase", "--autostash")
