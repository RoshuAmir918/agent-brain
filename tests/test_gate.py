"""The decision gate and decision rights: who decides comes from the people's records, a correction about who decides is
filed there, and the next plan's decision in that area is an ask."""
import json
from datetime import date

import pytest

from brain import cli, corrections, fm, gate, owners, roles, screen
from brain.config import Config

TODAY = date(2026, 11, 10)


@pytest.fixture
def org(vault, tmp_path, monkeypatch):
    toml = vault.parent / "brain.toml"
    toml.write_text(toml.read_text() + 'edition = "enterprise"\nowner_user_ids = ["slack:U01RA"]\n')
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    config = Config.load(vault)
    roles.add(config, "Engineering", "engineering")
    for name, slack, github in (("Kim", "U01KIM", "kim"), ("Roshan Amir", "U01RA", "roshan-gh"), ("Lee", "U01LEE", "lee")):
        profile = config.vault_path / config.people_dir / f"{name}/Profile.md"
        profile.parent.mkdir(parents=True)
        fm.write(profile, {}, f"# {name}\n- **Slack:** {slack}\n- **GitHub:** {github}\n")
    (config.vault_path / "agent/Owners.md").write_text(
        "# Owners\n\n| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n"
        "| exports | Kim (slack:U01KIM) | | export files customers download | | |\n\n"
        "## People\n\n| person | hours | away |\n| --- | --- | --- |\n| Kim (slack:U01KIM) | | |\n")
    corrections.add_work(config, "Engineering", "reviews:acme-app#40", "Add a notes column")
    return config


SCHEMA = {"decision": "add a nullable notes column to the forms table schema", "type": "data_change", "reversible": True,
          "recommendation": "nullable, no backfill", "default": "nullable, no backfill"}


def verdict(config, decision):
    return gate.sort(config, "Engineering", [decision])[0]


def test_always_confirm_schema_changes_with_a_person_is_learned_and_the_next_plan_asks(org):
    before = verdict(org, SCHEMA)
    assert before.verdict == "flag" and not before.person   # a kind people own, nobody on record
    said = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "roshan-gh", "2026-11-09",
                                 "you should always confirm schema changes with Roshan Amir")
    assert corrections.file(org, "Engineering", said.id, decides="schema changes", person="slack:U01RA", always=True) \
        == "schema changes: Roshan Amir decides, always asked"
    assert "schema changes" in owners.restricted(org)
    row = next(row for row in owners.load(org) if row.area == "schema changes")
    assert (row.owner, row.source) == ("slack:U01RA", "review on acme-app#40, roshan-gh, 2026-11-09")
    assert owners.problems(org) == []
    after = verdict(org, SCHEMA)
    assert (after.verdict, after.person, after.area) == ("ask", "slack:U01RA", "schema changes")
    assert "always ask Roshan Amir" in after.reason
    assert verdict(org, {**SCHEMA, "decision": "rename a variable in the export writer", "type": "craft"}).verdict == "decide"


def test_the_fixed_rule(org):
    exports = {"decision": "change the order of export file columns", "type": "customer_contract",
               "recommendation": "append new columns at the end", "default": "append at the end"}
    assert verdict(org, {**exports, "reversible": True}).verdict == "flag"
    assert verdict(org, {**exports, "reversible": False}).verdict == "ask"
    assert verdict(org, {**exports, "reversible": False, "findable": True}).verdict == "decide"
    assert verdict(org, {"decision": "name the helper", "type": "craft"}).verdict == "decide"
    with pytest.raises(ValueError, match="give a recommendation and a default"):
        verdict(org, {"decision": "change the order of export columns", "type": "customer_contract", "reversible": False})
    with pytest.raises(ValueError, match="decision types are"):
        verdict(org, {"decision": "x", "type": "vibes"})


def test_an_area_someone_lowered_to_ask_is_asked_even_when_reversible(org):
    from brain import consults
    consults.lower(org, "exports", "slack:U01KIM", "ask")
    assert verdict(org, {"decision": "change the export file columns", "type": "customer_contract", "reversible": True,
                         "recommendation": "a", "default": "b"}).verdict == "ask"


def test_who_decides_is_stated_by_an_owner_or_the_person_themselves(org):
    teammate = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "lee", "2026-11-09",
                                     "always ask Kim about migrations")
    with pytest.raises(ValueError, match="can't assign who decides"):
        corrections.file(org, "Engineering", teammate.id, decides="migrations", person="slack:U01KIM")
    myself = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "lee", "2026-11-09",
                                   "check with me before touching the queue", source_id="2")
    assert corrections.file(org, "Engineering", myself.id, decides="queues", person="slack:U01LEE") == "queues: Lee decides"
    assert "queues" not in owners.restricted(org)


def test_a_learned_owner_never_replaces_one_on_record(org):
    said = corrections.add_event(org, "Engineering", "reviews:acme-app#40", "review.comment", "roshan-gh", "2026-11-09",
                                 "ask me about exports")
    with pytest.raises(ValueError, match="already names Kim for exports"):
        corrections.file(org, "Engineering", said.id, decides="exports", person="slack:U01RA")
    assert said.id not in corrections.filed(org, "Engineering")


def test_the_gate_from_the_command_line_keeps_the_plan_with_the_work(org, monkeypatch, capsys):
    import io, sys
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([SCHEMA])))
    cli.main(["roles", "gate", "Engineering", "--work", "reviews:acme-app#41", "--vault", str(org.vault_path)])
    [out] = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "flag"
    saved = json.loads((screen.folder(org, "Engineering") / "plans/reviews-acme-app-41.json").read_text())
    assert saved["decisions"][0]["decision"] == SCHEMA["decision"]
    cli.main(["roles", "lenses", "Engineering", "--vault", str(org.vault_path)])
    lenses = json.loads(capsys.readouterr().out)
    assert lenses["lenses"][0]["lens"] == "solves_the_ask" and lenses["notes"]


def test_a_new_owners_table_is_made_before_the_first_section(org):
    path = org.vault_path / "agent/Owners.md"
    path.write_text("# Owners\n\n## Restricted\n\n- payments\n")
    assert owners.add_area(org, "schema changes", "slack:U01RA", "Roshan Amir", "schema", "review on acme-app#40")
    assert [row.area for row in owners.load(org)] == ["schema changes"]
    assert owners.restricted(org) == {"payments"} and owners.problems(org) == []


def owners_md(config, text):
    (config.vault_path / "agent/Owners.md").write_text(text)


TABLE = "| area | owner | backup | covers | wait | source |\n| --- | --- | --- | --- | --- | --- |\n"


def test_a_restricted_area_is_asked_even_with_nobody_on_record(org):
    owners_md(org, "# Owners\n\n## Restricted\n\n- payments\n")
    said = verdict(org, {"decision": "change how payments retry", "type": "data_change", "reversible": True,
                         "recommendation": "a", "default": "b"})
    assert (said.verdict, said.area, said.person) == ("ask", "payments", "")
    assert "ask the organisation's owner" in said.reason


def test_a_restricted_match_wins_whatever_the_table_order(org):
    owners_md(org, "# Owners\n\n" + TABLE + "| api | Lee (slack:U01LEE) | | | | |\n| billing | Kim (slack:U01KIM) | | | | |\n"
              "\n## Restricted\n\n- billing\n")
    said = verdict(org, {"decision": "add billing fields to the api", "type": "data_change", "recommendation": "a", "default": "b"})
    assert (said.verdict, said.area) == ("ask", "billing")


def test_a_named_area_not_on_record_is_refused_and_plurals_match(org):
    owners_md(org, "# Owners\n\n" + TABLE + "| access policies | Kim (slack:U01KIM) | | | | |\n\n## Restricted\n\n- access policies\n")
    with pytest.raises(ValueError, match="no area 'access policy'"):
        verdict(org, {"decision": "x", "type": "authorization", "area": "access policy", "recommendation": "a", "default": "b"})
    said = verdict(org, {"decision": "change the access policy for invited users", "type": "authorization",
                         "recommendation": "a", "default": "b"})
    assert (said.verdict, said.area) == ("ask", "access policies")


def test_a_persons_row_that_does_not_parse_still_blocks_a_learned_one(org):
    owners_md(org, "# Owners\n\n" + TABLE + "| exports | Kim (slack:U01KIM) | | files | customers | | |\n")
    assert owners.add_area(org, "exports", "slack:U01LEE", "Lee", "x", "y") is False


def test_learning_never_writes_into_the_frontmatter(org):
    owners_md(org, "---\ntier: warm\n## metadata comment\nkind: hub\n---\n# Owners\n")
    owners.add_area(org, "schema changes", "slack:U01RA", "Roshan Amir", "schema", "review")
    owners.restrict(org, "schema changes")
    data, body = fm.read(org.vault_path / "agent/Owners.md")
    assert data == {"tier": "warm", "kind": "hub"}
    assert [row.area for row in owners.load(org)] == ["schema changes"] and owners.restricted(org) == {"schema changes"}
