import json

from brain import cli, record


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
    assert set(found) == set(names)
    for key, name in names.items():
        value = getattr(record, name)
        assert found[key] == (sorted(value) if key == "nightStages" else value)
