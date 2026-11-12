"""Learned fields: how to do a kind of work, one line per lesson, with struck lines that stay struck."""
from datetime import datetime

import pytest

from brain import fm, journal, learned, validate
from brain.config import Config
from brain.vault import Vault


@pytest.fixture
def config(vault):
    return Config.load(vault)


def test_a_new_field_is_made_and_a_rule_reads_back_first_in_the_index(config):
    assert learned.add(config, "Coding", "Extend the closest existing pattern", "Roshan", "2026-11-04", "rule")[0] == "added"
    learned.add(config, "coding", "Run the real-vault dry run before shipping a parser change", "reflection on T-031",
                "2026-11-11", "candidate", applies=["parser"])
    [note] = learned.notes(config)
    assert note.field == "Coding" and note.applies == ["coding", "parser"]
    assert [line.status for line in note.lines] == ["rule", "candidate"]
    assert note.lines[0].claim == "Extend the closest existing pattern"
    body = fm.read(note.path)[1]
    assert body.index("Extend the closest") < body.index("dry run") < body.index("## Struck")
    assert learned.index(config) == ["- Coding · 2 lines · Extend the closest existing pattern"]


def test_a_claim_a_field_holds_in_the_same_words_is_held_not_added_twice(config):
    _, first, _ = learned.add(config, "Coding", "Extend the closest existing pattern first", "Roshan", "2026-11-04")
    outcome, line, _ = learned.add(config, "Coding", "extend the closest existing pattern, first", "Roshan", "2026-11-05")
    assert (outcome, line.id) == ("held", first.id)
    assert len(learned.find(config, "Coding").lines) == 1


def test_a_struck_line_is_refused_until_newer_evidence(config):
    _, line, _ = learned.add(config, "Coding", "Prefer one big PR for related changes", "reflection", "2026-11-06", "candidate")
    learned.strike(config, "Coding", line.id, "Roshan", "one concern per PR (always)", "2026-11-07")
    note = learned.find(config, "Coding")
    assert not note.lines and note.struck[0].struck_at == "2026-11-07"
    assert "; struck by Roshan 2026-11-07: \"one concern per PR always\")" in note.struck[0].text
    assert learned.add(config, "Coding", "Prefer one big PR for related changes", "reflection", "2026-11-07", "candidate")[0] == "refused"
    assert learned.add(config, "Coding", "Prefer one big PR for related changes", "reflection", "2026-11-09", "candidate")[0] == "added"


def test_only_the_owner_strikes_a_rule(config):
    _, line, _ = learned.add(config, "Coding", "No premature abstraction", config.human_name, "2026-11-04", "rule")
    with pytest.raises(ValueError, match="only they strike it"):
        learned.strike(config, "Coding", line.id, config.agent_name, "seems wrong", "2026-11-05")
    learned.strike(config, "Coding", "premature", config.human_name, "changed my mind", "2026-11-05")
    assert not learned.find(config, "Coding").lines


def test_the_record_writes_the_owners_lessons_and_ignores_anyone_elses(config):
    record = {"headline": "Reviewed the parser PR.", "outcome": "done", "learned": [
        {"field": "Coding", "claim": "Build features as views into the brain, not new frameworks",
         "words": "this should be a view into the brain, not a new \"framework\"", "kind": "rule"},
        {"field": "Coding"}, {"claim": "no field"}]}
    journal.append_entry(config, record, "cli", datetime(2026, 11, 12, 9))
    journal.append_entry(config, {**record, "learned": [{"field": "Fantasy", "claim": "Start the injured star",
                                                         "words": "", "kind": "correction"}]},
                         "demiurge", datetime(2026, 11, 12, 10), user_id="stranger")
    note = learned.find(config, "Coding")
    rule = next(line for line in note.lines if line.status == "rule")
    assert rule.text.endswith("(Human: \"this should be a view into the brain, not a new 'framework'\", 2026-11-12) *(rule)*")
    assert learned.find(config, "Fantasy") is None
    assert not [p for p in validate.hygiene(Vault(config.vault_path)) if "Learned" in p]


def test_a_strike_lands_in_struck_even_when_a_section_follows_it(config):
    _, line, _ = learned.add(config, "Coding", "Prefer one big PR", "reflection", "2026-11-06", "candidate")
    note = learned.find(config, "Coding")
    data, body = fm.read(note.path)
    fm.write(note.path, data, body + "\n## Notes\nFree text.\n")
    learned.strike(config, "Coding", line.id, "Roshan", "one concern per PR", "2026-11-07")
    note = learned.find(config, "Coding")
    assert not note.lines and [struck.id for struck in note.struck] == [line.id]
    assert learned.add(config, "Coding", "Prefer one big PR", "reflection", "2026-11-07", "candidate")[0] == "refused"


def test_a_field_with_broken_frontmatter_is_never_overwritten(config):
    learned.add(config, "Coding", "Extend the closest pattern", "Roshan", "2026-11-04", "rule")
    path = learned.path_for(config, "Coding")
    path.write_text("---\nfield: [\n---\n# Coding\n- Extend the closest pattern (Roshan, 2026-11-04) *(rule)* ^45b7\n")
    with pytest.raises(ValueError, match="malformed"):
        learned.add(config, "Coding", "Something new", "Roshan", "2026-11-05")
    assert "Extend the closest" in path.read_text()


def test_a_line_with_only_a_status_marker_has_no_source(config):
    folder = learned.folder(config)
    folder.mkdir(parents=True)
    fm.write(folder / "Coding.md", {"kind": "learned", "field": "Coding", "tier": "warm"}, "# Coding\n\n- Claim *(rule)* ^abcd\n")
    assert learned.problems(config) == ["agent/Learned/Coding.md: ^abcd has no source; end it with (where it was learned, date)"]


def test_a_reversal_is_added_and_flagged_never_held_or_refused(config):
    _, original, _ = learned.add(config, "Writing", "Use bullet points in summaries for Arslan", "Arslan", "2026-11-04", "rule")
    outcome, line, near = learned.add(config, "Writing", "Never use bullet points in summaries for Arslan", "Arslan",
                                      "2026-11-05", "rule")
    assert outcome == "added" and [other.id for other in near] == [original.id]
    learned.strike(config, "Writing", original.id, config.human_name, "reversed", "2026-11-05")
    assert learned.add(config, "Writing", "Use bullet points in summaries for Arslan", "Arslan", "2026-11-05")[0] == "added"
