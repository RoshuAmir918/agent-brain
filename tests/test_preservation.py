"""Structural transforms must retain prior evidence and routine ownership."""
from datetime import date

import pytest

from brain.config import Config
from brain import attribute, fm, nightly
from brain.tasks import Task, archive_path, read_archive_body, write_archive
from brain.vault import Vault


def test_archive_history_survives_repeated_saves(tmp_path):
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






@pytest.mark.parametrize("transform", ["defaults", "tiers", "fold"])
def test_sleep1_preserves_dream_bytes_and_new_notes_start_hot(tmp_path, transform):
    root = tmp_path / "memory"
    dream = root / "agent/Dreams/2026-10-08.md"
    dream.parent.mkdir(parents=True)
    fm.write(dream, {"kind": "dream", "fiction": True, "tier": "cold"}, "# Synthetic fiction fixture\n")
    original = dream.read_bytes()
    (root / "Fresh Hub.md").write_text("# Fresh\n\nmade today\n")
    vault = Vault(root)
    report = []
    if transform == "defaults":
        changed = nightly.ensure_frontmatter(vault, date(2026, 10, 9), report)
        assert vault.notes["Fresh Hub.md"].data["uses"] == ["2026-10-09"]
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
