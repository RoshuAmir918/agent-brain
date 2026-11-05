import json

from brain import nightly, record


def test_record_spec_matches_the_harness_constants(vault, capsys):
    nightly.main(["record-spec", "--json", "--vault", str(vault)])
    found = json.loads(capsys.readouterr().out)
    names = {"system": "SYSTEM", "conversation": "CONVERSATION", "schema": "RECORD_SCHEMA",
             "transcriptCap": "TRANSCRIPT_CAP", "routine": "ROUTINE", "routineSchema": "ROUTINE_SCHEMA",
             "nightStages": "NIGHT_STAGES"}
    assert set(found) == set(names)
    for key, name in names.items():
        value = getattr(record, name)
        assert found[key] == (sorted(value) if key == "nightStages" else value)
