from conftest import make_vault as base_vault, nightly_argv
import math
from datetime import date
from pathlib import Path

from brain import activation as act, fm, triggers, attribute
from brain.tasks import parse_register, Task
from brain.vault import Vault
from brain import nightly

TODAY = date(2026, 10, 5)


def test_thresholds_from_single_use():
    hot_t, warm_t = act.thresholds(7, 28)
    assert act.tier_for(act.activation([7])) == "hot"
    assert act.tier_for(act.activation([8])) == "warm"
    assert act.tier_for(act.activation([28])) == "warm"
    assert act.tier_for(act.activation([29])) == "cold"


def test_simulation_matches_spec_table():
    rows = dict((n, (h, w)) for n, h, w in act.simulate())
    assert rows["One use"] == (8, 29)
    assert rows["Five uses over a month"] == (28, 131)
    assert rows["Daily for 30 days"] == (225, 855)


def test_inferred_half_weight():
    b1 = act.activation([0])
    b2 = act.activation([], [0])
    assert math.isclose(b2, b1 + math.log(0.5))


def test_pinned_is_hot():
    assert act.tier_for(-99, pinned=True) == "hot"


def test_frontmatter_roundtrip(tmp_path):
    p = tmp_path / "n.md"
    fm.write(p, {"tier": "warm", "uses": ["2026-10-01"]}, "# Title\n\nbody\n")
    d, b = fm.read(p)
    assert d["tier"] == "warm" and d["uses"] == ["2026-10-01"] and b.startswith("# Title")
    assert fm.first_line(b) == "body"


def test_register_parse_and_state():
    text = "## Active\n\n### T-001 — Alpha\n<!-- brain: uses=2026-10-01; tier=hot -->\n- **Status:** doing it\n\n### T-002 — Beta\n- **Status:** waiting\n\n## Other\n"
    spans, tasks = parse_register(text)
    assert [t.tid for t in tasks] == ["T-001", "T-002"]
    assert tasks[0].uses == ["2026-10-01"] and tasks[0].state["tier"] == "hot"
    assert "brain:" not in tasks[0].body
    tasks[1].add_use("2026-10-05")
    assert tasks[1].render().startswith("### T-002 — Beta\n<!-- brain: uses=2026-10-05 -->")


def test_triggers_fire_and_retire():
    text = triggers.TEMPLATE.replace("| --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- |\n| TR-001 | commitment | 2026-10-01 | check vendor | [[agent/Tasks#T-003]] | active | 2026-09-01 |\n| TR-002 | intention | Harbor CI fails | mention timeout | [[Harbor Context]] | active | 2026-09-01 |\n| TR-003 | commitment | 2030-01-01 | far future | [[agent/Tasks#T-004]] | active | 2026-09-01 |")
    new, rep = triggers.process(text, TODAY, {"Harbor Context.md": "cold"})
    assert [t.id for t in rep["fired"]] == ["TR-001"]
    assert [t.id for t in rep["mentioned"]] == ["TR-002"]
    assert "TR-003 | commitment | 2030-01-01" in new
    # next night the mentioned intention retires; the commitment never does
    new2, rep2 = triggers.process(new, date(2026, 10, 6), {"Harbor Context.md": "cold"})
    assert [t.id for t in rep2["retired"]] == ["TR-002"]
    assert "retired" not in [t.status for t in triggers.parse(new2) if t.id == "TR-003"]


def make_vault(tmp_path: Path) -> Path:
    root = base_vault(tmp_path / "memory")
    (root / "Conversations").mkdir()
    (root / "people" / "Alice_Example").mkdir(parents=True)
    (root / "START.md").write_text("# Start\n")
    (root / "CURRENT.md").write_text("# Current\n\n<!-- brain:active-threads start -->\n<!-- brain:active-threads end -->\n")
    (root / "Harbor Context.md").write_text("# Harbor\n\nHarbor context note. See [[people/Alice_Example/Profile|Alice]].\n")
    (root / "Old Hub.md").write_text("# Old\n\nNothing cites this.\n")
    (root / "people" / "Alice_Example" / "Profile.md").write_text("# Alice\n\nprofile\n")
    (root / "Conversations" / "2026-10-04 Historical fixture.md").write_text("# Archive\n\nWe discussed [[Harbor Context]] and T-001. Sources: [[Harbor Context]] · T-001\n")
    (root / "Conversations" / "2026-08-01 Old talk.md").write_text("# Old\n\nMentioned [[Old Hub]].\n")
    (root / "agent" / "Tasks.md").write_text("# Tasks\n\n## Active work\n\n### T-001 — Ship thing\n<!-- brain: uses=2026-10-04; status=open -->\n- **Status:** in progress 2026-10-04\n\n### T-002 — Quiet thing\n<!-- brain: uses=2026-08-01; status=open -->\n- **Status:** asked\n- **Latest evidence:** 2026-08-01 owner request\n\n## Configured routines\n")
    return root


def test_nightly_archives_cold_tasks_and_restores_logged_uses(tmp_path):
    root = make_vault(tmp_path)
    nightly.main(nightly_argv(root, "2026-10-05", "--write"))
    v = Vault(root)
    assert v.notes["Harbor Context.md"].data["tier"] == "hot"
    assert v.notes["START.md"].data["pinned"] is True
    assert (root / "Hot Set.md").exists() and (root / "Warm Index.md").exists()
    reg = fm.read(root / "agent" / "Tasks.md")[1]
    assert "### T-002" not in reg
    assert (root / "agent" / "Tasks Archive" / "T-002.md").exists()
    assert "T-002" in (root / "Warm Index.md").read_text()
    cur = (root / "CURRENT.md").read_text()
    assert "T-001 — Ship thing" in cur
    # a use log line reheats T-002 the next night
    attribute.append_log(root / "agent" / "Use Log" / "2026-10.md", [attribute.Use("2026-10-05T20:00Z", "T-002", "the owner asked", "explicit")])
    nightly.main(nightly_argv(root, "2026-10-06", "--write"))
    reg = fm.read(root / "agent" / "Tasks.md")[1]
    assert "### T-002" in reg


def test_explicit_attribution_ignores_orientation_and_names(tmp_path):
    root = make_vault(tmp_path)
    v = Vault(root)
    uses = attribute.explicit_uses(v, "Read [[START]] and [[Harbor Context]]; Alice said hi; T-001 moved. I found no record of the invoice.", "2026-10-05T12:00Z", "r")
    targets = [u.target for u in uses]
    assert "Harbor Context.md" in targets and "T-001" in targets and "MISS" in targets
    assert "START.md" not in targets
    assert "people/Alice_Example/Profile.md" not in targets


def test_sources_footer_in_html_comment(tmp_path):
    root = make_vault(tmp_path)
    v = Vault(root)
    uses = attribute.explicit_uses(v, "Here is the answer.\n\n<!-- Sources: [[Harbor Context]] · T-001 -->", "2026-10-05T12:00Z", "r")
    assert {u.target for u in uses} == {"Harbor Context.md", "T-001"}


def test_new_note_starts_hot_dream_is_untouched(tmp_path):
    root = make_vault(tmp_path)
    (root / "Fresh Hub.md").write_text("# Fresh\n\nmade today\n")
    (root / "agent" / "Dreams").mkdir(parents=True)
    (root / "agent" / "Dreams" / "2026-10-05.md").write_text("# a dream\n\nfiction\n")
    v = Vault(root); report = []
    nightly.ensure_frontmatter(v, TODAY, report)
    assert v.notes["Fresh Hub.md"].data["uses"] == ["2026-10-05"]
    # REM owns dream creation/frontmatter; Sleep 1 does not repair or retier it.
    assert "agent/Dreams/2026-10-05.md" not in v.notes
    assert (root / "agent/Dreams/2026-10-05.md").read_text() == "# a dream\n\nfiction\n"
