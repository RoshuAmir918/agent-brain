import json

import pytest

from brain import cli, journal, loops, needs, record
from brain.config import Config


def test_record_spec_matches_the_harness_constants(vault, capsys):
    cli.main(["record-spec", "--json", "--vault", str(vault)])
    found = json.loads(capsys.readouterr().out)
    names = {"system": "SYSTEM", "conversation": "CONVERSATION", "schema": "RECORD_SCHEMA",
             "transcriptCap": "TRANSCRIPT_CAP", "routine": "ROUTINE", "routineSchema": "ROUTINE_SCHEMA",
             "nightStages": "NIGHT_STAGES"}
    assert set(found) == set(names) | {"held"}
    for key, name in names.items():
        value = getattr(record, name)
        assert found[key] == (sorted(value) if key == "nightStages" else value)


def test_record_spec_holds_what_the_owner_already_has_open(vault, capsys):
    config = Config.load(vault)
    journal.add_ask(config, config.today(), "Book the 16th?", None, why="The booking needs your choice; yes reserves it, no leaves it open.")
    loops.add(config, config.now(), "owed", "Send Pat the fares.", "Conversations", "Pat", "")
    cli.main(["record-spec", "--json", "--vault", str(vault)])
    [ask] = [item for item in needs.items(config, config.today()) if item.kind == "ask"]
    [loop] = loops.open_loops(config)
    held = json.loads(capsys.readouterr().out)["held"]
    assert all(f"- {item} · " in held for item in (ask.id, loop.id, "T-001"))


@pytest.mark.parametrize("ask,why", [
    ("Book the hotel?", None),
    ("Review T-1?", "Yes books it; no leaves it open."),
    ("Book the hotel?", "Read [[Trip]]."),
    ("Book the hotel?", "word " * 61),
])
def test_record_logs_and_drops_invalid_items_and_keeps_valid_capture(vault, capsys, ask, why):
    config = Config.load(vault)
    journal.append_entry(config, {
        "headline": "Discussed a hotel booking.", "outcome": "progress",
        "needsYou": ask, "needsYouWhy": why,
        "loops": [{"kind": "promised", "text": "Read agent/Tasks.md for T-1."},
                  {"kind": "promised", "text": "Tell Mira which hotel has rooms."}],
        "noted": ["Mira arrives Friday."],
    }, "cli", config.now())
    assert needs.items(config, config.today()) == []
    assert [item.text for item in loops.open_loops(config)] == ["Tell Mira which hotel has rooms."]
    assert "Mira arrives Friday." in (vault / f"Conversations/{config.today()} Noted.md").read_text()
    err = capsys.readouterr().err
    assert "skipped ask:" in err and "skipped loop:" in err


def test_record_keeps_ask_why_and_loop_metadata(vault):
    config = Config.load(vault)
    why = "The fare hold ends Friday; yes books it, no lets it lapse. I recommend booking."
    journal.append_entry(config, {
        "headline": "Discussed travel.", "outcome": "progress",
        "needsYou": "Book the Friday flight to Lisbon?", "needsYouWhy": why,
        "loops": [{"kind": "waiting", "text": "Check Kevin's answer about the hotel.", "with": "Kevin", "task": "T-001"},
                  {"kind": "when", "text": "Tell Mira which hotel has rooms.", "with": "Kevin", "if": "Kevin replies"}],
    }, "cli", config.now())
    ask, = needs.items(config, config.today())
    assert ask.detail == why
    waiting, when = loops.open_loops(config)
    assert waiting.task == "T-001" and waiting.with_ == "Kevin"
    assert when.if_ == "Kevin replies" and when.with_ == "Kevin"
