"""Structural transforms must retain prior evidence and routine ownership."""
from datetime import date

import pytest

from brain.config import Config
from brain import attribute, fm, nightly, triggers
from brain.tasks import Task, archive_path, read_archive_body, write_archive
from brain.vault import Vault


def test_existing_historical_archive_survives_repeated_register_saves(tmp_path):
    original = (
        "---\nkind: log\ntier: cold\n---\n# Historical register evidence\n\n"
        "Superseded waits are not current instructions.\n\n"
        "### T-014 — Example\n<!-- brain:  -->\n"
        "- **Evidence:** ORIGINAL_FULL_RECEIPT\n"
        "#### Earlier checkpoint\nKeep provider failure and no-retry stop.\n"
    )
    path = archive_path(Config.load(tmp_path), "T-014")
    path.parent.mkdir(parents=True)
    path.write_text(original)
    task = Task("T-014", "Example", "- **Status:** open\n"
                "- **Evidence:** [[memory/agent/Tasks Archive/T-014]]\n",
                {"status": "open", "tier": "warm"})
    write_archive(Config.load(tmp_path), task, "warm", date(2026, 10, 9))
    first = path.read_text()
    assert original in first
    assert "Moved 2026-10-09" not in first
    assert read_archive_body(Config.load(tmp_path), "T-014") == task.body
    write_archive(Config.load(tmp_path), task, "warm", date(2026, 10, 10))
    assert path.read_text() == first
    assert path.read_text().count(original) == 1


def test_original_move_date_survives_repeated_archive_state_updates(tmp_path):
    task = Task("T-018", "Example", "FULL_HISTORICAL_BODY\n", {"status": "open"})
    write_archive(Config.load(tmp_path), task, "warm", date(2026, 10, 6))
    task.add_use("2026-10-09")
    write_archive(Config.load(tmp_path), task, "cold", date(2026, 10, 9))
    text = archive_path(Config.load(tmp_path), "T-018").read_text()
    assert "Moved 2026-10-06" in text
    assert "Moved 2026-10-09" not in text
    assert "uses=2026-10-09" in text
    assert "FULL_HISTORICAL_BODY" in text
    write_archive(Config.load(tmp_path), task, "cold", date(2026, 10, 10))
    assert archive_path(Config.load(tmp_path), "T-018").read_text() == text


@pytest.mark.parametrize("status", ["fulfilled/fired: read-only recheck",
                                    "fulfilled", "fired 2026-10-08"])
@pytest.mark.parametrize("cls", ["commitment", "intention"])
def test_terminal_trigger_keeps_fired_section_and_evidence(status, cls, template_config):
    row = f"| TR-001 | {cls} | 2026-10-08 | recheck only | [[Source]] | {status} | 2026-10-05 |\n"
    evidence = "\nRecheck evidence: confirmed source; no transaction or new permission.\n\n"
    text = triggers.TEMPLATE.replace("## Fired\n\n", "## Fired\n\n" + row + evidence)
    result, report = triggers.process(text, date(2026, 10, 9), {"Source": "cold"}, template_config)
    assert result == text
    assert report == {"fired": [], "mentioned": [], "retired": []}
    result2, report2 = triggers.process(result, date(2026, 10, 10), {"Source": "cold"}, template_config)
    assert result2 == text
    assert report2 == report


def test_firing_moves_attached_prose_without_losing_other_sections(template_config):
    row = "| TR-001 | commitment | 2026-10-08 | recheck | [[Source]] | active | 2026-10-05 |\n"
    evidence = "\nOriginal supporting sentence; do not repeat the transaction.\n\n"
    text = triggers.TEMPLATE.replace("## Fired", row + evidence + "## Fired")
    text += "\n## Operator notes\nPreserve this unrelated tail.\n"
    result, report = triggers.process(text, date(2026, 10, 9), {}, template_config)
    assert [t.id for t in report["fired"]] == ["TR-001"]
    assert result.index("## Fired") < result.index("| TR-001") < result.index(evidence)
    assert result.endswith("\n## Operator notes\nPreserve this unrelated tail.\n")
    again, report2 = triggers.process(result, date(2026, 10, 10), {}, template_config)
    assert again == result
    assert not report2["fired"]


def test_fulfilled_row_in_active_table_is_classified_without_refiring(template_config):
    row = "| TR-001 | commitment | 2026-10-08 | done | [[Source]] | fulfilled/fired: recheck | 2026-10-05 |\n"
    text = triggers.TEMPLATE.replace("## Fired", row + "\nEvidence retained.\n\n## Fired")
    result, report = triggers.process(text, date(2026, 10, 9), {}, template_config)
    assert result.index("## Fired") < result.index("| TR-001")
    assert "Evidence retained." in result
    assert not report["fired"]


@pytest.mark.parametrize("transform", ["defaults", "tiers", "fold"])
def test_sleep1_preserves_dream_bytes_including_frontmatter(tmp_path, transform):
    root = tmp_path / "memory"
    dream = root / "agent/Dreams/2026-10-08.md"
    dream.parent.mkdir(parents=True)
    fm.write(dream, {"kind": "dream", "fiction": True, "tier": "cold"}, "# Synthetic fiction fixture\n")
    original = dream.read_bytes()
    vault = Vault(root)
    report = []
    if transform == "defaults":
        changed = nightly.ensure_frontmatter(vault, date(2026, 10, 9), report)
    elif transform == "tiers":
        changed = nightly.retier(vault, date(2026, 10, 9), report)
    else:
        log = root / "agent/Use Log/2026-10.md"
        log.parent.mkdir()
        log.write_text(attribute.Use("2026-10-08", "agent/Dreams/2026-10-08.md", "fixture", "explicit").line() + "\n")
        changed = nightly.fold_uses(vault, [], date(2026, 10, 9), report)
    assert "agent/Dreams/2026-10-08.md" not in changed
    assert "agent/Dreams/2026-10-08.md" not in vault.notes
    assert vault.resolve("agent/Dreams/2026-10-08") == "agent/Dreams/2026-10-08.md"
    assert dream.read_bytes() == original
