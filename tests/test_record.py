import json

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
    journal.add_ask(config, config.today(), "Book the 16th?", None)
    loops.add(config, config.now(), "owed", "Send Pat the fares.", "Conversations", "Pat", "")
    cli.main(["record-spec", "--json", "--vault", str(vault)])
    [ask] = [item for item in needs.items(config, config.today()) if item.kind == "ask"]
    [loop] = loops.open_loops(config)
    held = json.loads(capsys.readouterr().out)["held"]
    assert all(f"- {item} · " in held for item in (ask.id, loop.id, "T-001"))
