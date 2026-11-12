"""After-conversation capture, the asks it raises, and the ledger rows cron runs leave."""
import io
import json
from datetime import date, datetime

import pytest

from brain import cli, fm, journal, ledger, loops, needs, nightly
from brain.config import Config
from conftest import snapshot

TODAY = date(2026, 10, 28)
AT = datetime(2026, 10, 28, 14, 5)


@pytest.fixture
def config(vault):
    fm.write(vault / "Lisbon trip.md", {"kind": "hub", "tier": "hot"}, "# Lisbon trip\n")
    return Config.load(vault)


def record(**overrides):
    return {"headline": "Compared March fares to Lisbon.", "outcome": "progress", "next": "Check garden rooms.",
            **overrides}


def test_entry_captures_loops_instead_of_a_diary_row(config):
    path = journal.append_entry(config, record(touched=["Lisbon trip", "Nowhere"], thought="Unsure | worth it.",
                                               needsYou="Book the 16th?", loops=[
                                                   {"kind": "promised", "text": "Check garden rooms."}]), "demiurge", AT)
    assert path == config.vault_path / "agent/Loops.md"
    [item] = loops.open_loops(config)
    assert (item.kind, item.text, item.source) == ("promised", "Check garden rooms.", "Lisbon trip")
    assert not (config.vault_path / "agent/Journal").exists()


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
    newer = needs.parse_section(body)[1]
    assert (f"- 2026-10-27 · ask · Earlier. · [[Lisbon trip]]\n- 2026-10-28 · ask · Newer.\n  - wake: 2026-10-31\n  - id: {newer.id}\n\n## Later\nTail."
            in body)


@pytest.mark.parametrize("bad", [record(outcome="maybe"), record(headline="  ")])
def test_append_rejects_bad_records_without_writing(config, bad):
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError):
        journal.append_entry(config, bad, "demiurge", AT)
    assert snapshot(config.vault_path) == before


def test_cli_journal_append_json(config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(record(loops=[{"kind": "owed", "text": "Send fares.", "with": "Pat"}]))))
    cli.main(["journal", "append", "--vault", str(config.vault_path), "--platform", "slack", "--at", "2026-10-28 14:05"])
    assert capsys.readouterr().out.strip().endswith("agent/Loops.md")
    assert loops.open_loops(config)[0].text == "Send fares."
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    with pytest.raises(SystemExit, match="journal append"):
        cli.main(["journal", "append", "--vault", str(config.vault_path)])


def test_a_record_of_nothing_that_captures_nothing_is_not_written(config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(record(outcome="nothing", touched=["Lisbon trip"], speaker="Pat", loops=[]))))
    cli.main(["journal", "append", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out.strip() == "skipped: nothing worth recording"
    assert journal.append_entry(config, record(outcome="nothing", needsYou="Book the 16th?"), "cli", AT) is not None
    assert [item.text for item in needs.items(config, TODAY) if item.origin == "current"] == ["Book the 16th?"]


def test_ledger_append_by_skill_finds_the_routine_and_skips_night_stages(config, monkeypatch, capsys):
    fm.write(config.vault_path / "Midday duty.md", {"kind": "hub", "tier": "hot", "skill": "brain-midday-own",
                                                   "records": "midday"}, "# Midday\n")
    assert ledger.routine_for_skill(config, "brain-midday-own") == "midday"
    assert ledger.routine_for_skill(config, "brain-watch") == "watch"
    assert ledger.routine_for_skill(config, "unprefixed") == "unprefixed"
    payload = {"skill": "brain-watch", "outcome": "nothing", "account": "Checked.", "at": "2026-10-28 09:00"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    cli.main(["ledger", "append", "--json", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out.strip().endswith("agent/Watch/2026-10.md")
    cli.main(["ledger", "append", "--vault", str(config.vault_path), "--skill", "brain-sleep1",
                  "--outcome", "done", "--account", "Consolidated."])
    assert "night stage" in capsys.readouterr().out
    assert not (config.vault_path / "agent/Sleep1").exists()


def test_two_sessions_ending_together_both_keep_their_loops(config):
    import threading
    barrier = threading.Barrier(8)

    def write(n):
        barrier.wait(5)
        journal.append_entry(config, record(loops=[{"kind": "promised", "text": f"Loop {n}."}]), "demiurge", AT)

    workers = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert sorted(item.text for item in loops.open_loops(config)) == sorted(f"Loop {n}." for n in range(8))


def test_a_private_note_is_never_named_as_touched_nor_as_an_asks_source(config):
    for rel in ("people/Pat/Profile.md", "human/Profile.md"):
        (config.vault_path / rel).parent.mkdir(parents=True, exist_ok=True)
        fm.write(config.vault_path / rel, {}, "# Private\n")
    journal.append_entry(config, record(touched=["people/Pat/Profile", "human/Profile", "Lisbon trip"],
                                        needsYou="Who is Pat · really?", loops=[{"kind": "owed", "text": "Send reply.", "with": "Pat"}]), "demiurge", AT)
    [item] = loops.open_loops(config)
    assert item.source == "Lisbon trip"
    [ask] = [item for item in needs.items(config, TODAY) if item.origin == "current"]
    assert (ask.text, ask.source) == ("Who is Pat - really?", "Lisbon trip")


def test_the_nightly_pull_stashes_rows_written_after_the_agent_committed(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "sh", lambda repo, *args: calls.append(args))
    cli.push(vault)
    assert calls[0][:3] == ("pull", "--rebase", "--autostash")


def test_the_same_question_is_asked_once(config):
    journal.append_entry(config, record(needsYou="Book the 16th?"), "demiurge", AT)
    journal.append_entry(config, record(needsYou="book the 16th?"), "slack", AT)
    asks = [item.text for item in needs.items(config, TODAY) if item.origin == "current"]
    assert asks == ["Book the 16th?"]


def test_the_owners_corrections_land_in_working_together_once_and_others_are_ignored(config):
    profile = config.vault_path / config.human_profile
    profile.parent.mkdir(parents=True, exist_ok=True)
    fm.write(profile, {"kind": "profile"}, "# Owner\n\n## Who\nSomeone.\n\n## Working together\n- 2026-10-20: Brief me in the morning.\n\n## Impression\nCurious.\n")
    journal.append_entry(config, record(workingTogether=["No em dashes; a few sentences unless I ask for detail"]), "cli", AT)
    journal.append_entry(config, record(outcome="nothing", workingTogether=["no em dashes, a few sentences unless I ask for detail"]), "cli", AT)
    journal.append_entry(config, record(workingTogether=["Call me boss"]), "demiurge", AT, user_id="stranger")
    body = fm.read(profile)[1]
    section = body.split("## Working together", 1)[1].split("## Impression", 1)[0]
    assert section.count("em dashes") == 1 and "- 2026-10-28: No em dashes" in section
    assert "Brief me in the morning" in section and "boss" not in body
    assert body.index("## Working together") < body.index("## Impression")


def test_noted_facts_go_to_the_days_conversation_note_and_others_are_quoted_as_claims(config):
    journal.append_entry(config, record(outcome="nothing", noted=["Flying to Madison at 5 am on October 29 to visit Faisal"]), "cli", AT)
    journal.append_entry(config, record(noted=["Maya's birthday is March 3"], speaker="Kevin"), "demiurge", AT, user_id="k1")
    note = config.vault_path / "Conversations/2026-10-28 Noted.md"
    data, body = fm.read(note)
    assert data["kind"] == "conversation" and data["tier"] == "warm"
    assert "- 14:05 · Flying to Madison at 5 am on October 29 to visit Faisal" in body
    assert "- 14:05 · Kevin said: Maya's birthday is March 3" in body
    assert not (config.vault_path / config.agent("Journal/2026-10.md")).exists()


def test_a_correction_that_reverses_an_earlier_line_is_kept(config):
    profile = config.vault_path / config.human_profile
    profile.parent.mkdir(parents=True, exist_ok=True)
    fm.write(profile, {"kind": "profile"}, "# Owner\n\n## Working together\n- 2026-10-20: Do not send short replies.\n")
    journal.append_entry(config, record(workingTogether=["Send short replies"]), "cli", AT)
    assert "- 2026-10-28: Send short replies" in fm.read(profile)[1]


def test_malformed_record_items_are_dropped_before_anything_is_written(config):
    journal.append_entry(config, record(loops=[{"kind": "owed"}, "Send it.", {"kind": "promised", "text": "Check rooms."}],
                                        closed=[{"loop": "x"}, 3], ready="L-1", noted="Pat moved", workingTogether=7,
                                        touched=[None, "Lisbon trip"]), "demiurge", AT)
    [item] = loops.open_loops(config)
    assert (item.text, item.source) == ("Check rooms.", "Lisbon trip")


def test_record_command_skips_malformed_fields_and_keeps_generic_ask(config, monkeypatch):
    payload = record(needsYou="Proceed?", touched=[None, "Lisbon trip"], ready="L-1",
                     loops=[{"kind": "owed"}, {"kind": "promised", "text": "Check rooms."}])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    cli.main(["journal", "append", "--vault", str(config.vault_path), "--platform", "demiurge"])
    assert [item.text for item in needs.items(config, config.today())] == ["Proceed?"]
    assert [item.text for item in loops.open_loops(config)] == ["Check rooms."]
