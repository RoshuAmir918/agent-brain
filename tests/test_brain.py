from conftest import make_vault as base_vault
import math
from datetime import date
from pathlib import Path

import pytest

from brain import activation as act, fm, triggers, attribute, validate
from brain.tasks import parse_register
from brain.vault import Vault

TODAY = date(2026, 10, 5)


@pytest.mark.parametrize("uses, pinned, tier", [
    ([7], False, "hot"), ([8], False, "warm"), ([28], False, "warm"), ([29], False, "cold"), (None, True, "hot")])
def test_tier_for_activation(uses, pinned, tier):
    score = act.activation(uses) if uses else -99
    assert act.tier_for(score, pinned=pinned) == tier


def test_inferred_half_weight():
    assert math.isclose(act.activation([], [0]), act.activation([0]) + math.log(0.5))


def test_register_parse_and_state():
    text = "## Active\n\n### T-001 — Alpha\n<!-- brain: uses=2026-10-01; tier=hot -->\n- **Status:** doing it\n\n### T-002 — Beta\n- **Status:** waiting\n\n## Other\n"
    spans, tasks = parse_register(text)
    assert [t.tid for t in tasks] == ["T-001", "T-002"]
    assert tasks[0].uses == ["2026-10-01"] and tasks[0].state["tier"] == "hot"
    assert "brain:" not in tasks[0].body
    tasks[1].add_use("2026-10-05")
    assert tasks[1].render().startswith("### T-002 — Beta\n<!-- brain: uses=2026-10-05 -->")


def test_triggers_fire_and_retire(template_config):
    text = triggers.TEMPLATE.replace("| --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- |\n| TR-001 | commitment | 2026-10-01 | check vendor | [[agent/Tasks#T-003]] | active | 2026-09-01 |\n| TR-002 | intention | Acme CI fails | mention timeout | [[Acme Context]] | active | 2026-09-01 |\n| TR-003 | commitment | 2030-01-01 | far future | [[agent/Tasks#T-004]] | active | 2026-09-01 |")
    new, rep = triggers.process(text, TODAY, {"Acme Context.md": "cold"}, template_config)
    assert [t.id for t in rep["fired"]] == ["TR-001"]
    assert [t.id for t in rep["mentioned"]] == ["TR-002"]
    assert "TR-003 | commitment | 2030-01-01" in new
    # next night the mentioned intention retires; the commitment never does
    new2, rep2 = triggers.process(new, date(2026, 10, 6), {"Acme Context.md": "cold"}, template_config)
    assert [t.id for t in rep2["retired"]] == ["TR-002"]
    assert "retired" not in [t.status for t in triggers.parse(new2) if t.id == "TR-003"]


def test_an_impossible_trigger_date_never_fires_and_is_named_by_validate(vault, template_config):
    text = triggers.TEMPLATE.replace("| --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- |\n| TR-001 | commitment | 2026-02-30 | check vendor | [[START]] | active | 2026-01-01 |")
    assert triggers.date_of("2026-02-30") is None
    new, rep = triggers.process(text, TODAY, {}, template_config)
    assert rep["fired"] == [] and "| TR-001 | commitment | 2026-02-30 |" in new
    fm.write(vault / "agent/Triggers.md", {"tier": "hot", "pinned": True}, text)
    assert validate.trigger_dates(Vault(vault)) == ["agent/Triggers.md · TR-001: 2026-02-30 is not a real date; it never fires"]


def make_vault(tmp_path: Path) -> Path:
    root = base_vault(tmp_path / "memory")
    (root / "people" / "Alice_Example").mkdir(parents=True)
    (root / "START.md").write_text("# Start\n")
    (root / "Acme Context.md").write_text("# Acme\n\nAcme context note. See [[people/Alice_Example/Profile|Alice]].\n")
    (root / "people" / "Alice_Example" / "Profile.md").write_text("# Alice\n\nprofile\n")
    (root / "agent" / "Tasks.md").write_text("# Tasks\n\n## Active work\n\n### T-001 — Ship thing\n<!-- brain: uses=2026-10-04; status=open -->\n- **Status:** in progress 2026-10-04\n")
    return root


@pytest.mark.parametrize("text, expected", [
    ("Read [[START]] and [[Acme Context]]; Alice said hi; T-001 moved. I found no record of the invoice.",
     {"Acme Context.md", "T-001", "MISS"}),
    ("Here is the answer.\n\n<!-- Sources: [[Acme Context]] · T-001 -->", {"Acme Context.md", "T-001"}),
])
def test_explicit_attribution_ignores_orientation_and_names(tmp_path, text, expected):
    uses = attribute.explicit_uses(Vault(make_vault(tmp_path)), text, "2026-10-05T12:00Z", "r")
    assert {u.target for u in uses} == expected
