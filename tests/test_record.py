import json

from brain import cli, journal, loops, needs, record
from brain.config import Config


def test_closures_require_a_loop_outcome_and_short_reason():
    assert record.RECORD_SCHEMA["properties"]["closed"] == {
        "type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["loop", "outcome", "why"], "additionalProperties": False,
            "properties": {"loop": {"type": "string"}, "outcome": {"enum": ["done", "dropped"]},
                           "why": {"type": "string", "maxLength": 120}}}}
    assert "closed" not in record.ROUTINE_SCHEMA["properties"]


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
    assert json.loads(capsys.readouterr().out)["held"] == (
        "\nAlready waiting on your owner; leave needsYou null if the conversation only raised one of these again, in any "
        f"wording; list answered ones in resolved:\n- {ask.id} · ask · Book the 16th?"
        f"\nOpen loops; close these when finished, do not add them again:\n- {loop.id} · owed · Send Pat the fares."
        "\nOpen tasks; each carries its own follow-up, so add no loop for their work or their reports:\n- T-001 · Sample")
