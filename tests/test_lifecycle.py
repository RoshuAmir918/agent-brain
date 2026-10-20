from conftest import make_vault, nightly_argv, snapshot
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from brain.config import Config
from brain import fm, nightly, triggers, validate
from brain.tasks import Task, archived_open_tasks, load_tasks, parse_register, read_archive_body, seed_uses_from_body, write_archive


def test_full_body_survives_repeated_warm_cold_hot_cycles(tmp_path):
    root = make_vault(tmp_path / "memory")
    for day in (date(2026, 10, 5), date(2026, 10, 6), date(2026, 11, 1)):
        nightly.process_tasks(Config.load(root), day, *load_tasks(Config.load(root)), [])
        assert "ORIGINAL_FULL_BODY" in read_archive_body(Config.load(root), "T-001")
    assert "T-001" not in fm.read(root / "agent/Tasks.md")[1]
    archived = archived_open_tasks(Config.load(root))[0]
    archived.add_use("2026-11-02")
    write_archive(Config.load(root), archived, "cold", date(2026, 11, 2))
    nightly.process_tasks(Config.load(root), date(2026, 11, 2), *load_tasks(Config.load(root)), [])
    assert "ORIGINAL_FULL_BODY" in fm.read(root / "agent/Tasks.md")[1]
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert tasks[0].state["status"] == "open"


def test_recover_legacy_archive_with_summary_above_full_body(tmp_path):
    root = make_vault(tmp_path / "memory")
    task = Task("T-001", "Sample", "ORIGINAL_FULL_BODY", {"status": "open"})
    write_archive(Config.load(root), task, "warm", date(2026, 10, 5))
    p = root / "agent/Tasks Archive/T-001.md"
    text = p.read_text()
    p.write_text(text.replace("ORIGINAL_FULL_BODY", "- **Warm:** derived summary\n\n## Earlier archive\n\nORIGINAL_FULL_BODY"))
    assert "ORIGINAL_FULL_BODY" in read_archive_body(Config.load(root), "T-001")
    write_archive(Config.load(root), Task("T-001", "Sample", "- **Warm:** summary", task.state), "cold", date(2026, 11, 1))
    assert "ORIGINAL_FULL_BODY" in read_archive_body(Config.load(root), "T-001")


def test_short_full_body_restores_from_longer_warm_summary(tmp_path):
    root = make_vault(tmp_path / "memory")
    data, _ = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, Task("T-001", "Short", "ORIGINAL_FULL_BODY", {"uses": "2026-09-20"}).render())
    nightly.process_tasks(Config.load(root), date(2026, 10, 5), *load_tasks(Config.load(root)), [])
    data, body = fm.read(root / "agent/Tasks.md")
    _, tasks = parse_register(body)
    tasks[0].add_use("2026-10-06")
    fm.write(root / "agent/Tasks.md", data, tasks[0].render())
    nightly.process_tasks(Config.load(root), date(2026, 10, 6), *load_tasks(Config.load(root)), [])
    assert "ORIGINAL_FULL_BODY" in fm.read(root / "agent/Tasks.md")[1]


def test_report_only_resolves_human_links_without_reading_human_contents(tmp_path):
    root = make_vault(tmp_path / "memory")
    human = tmp_path / "sources"
    human.mkdir()
    (human / "Human.md").write_text("PRIVATE_FIXTURE_CONTENT")
    (root / "Hub.md").write_text("# Hub\n[[sources/Human]]\n")
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(human), "Human contents must not be read"
        return original(path, *args, **kwargs)

    with patch.object(Path, "read_text", guarded_read):
        nightly.main(nightly_argv(root, "2026-10-05"))


def test_task_uses_only_past_dated_evidence(template_config):
    today = date(2026, 10, 5)
    task = Task("T-001", "Sample", """- **Deadline:** 2030-01-01
- **Status:** mentions 2020-01-01
- **Latest evidence:** 2026-10-04 owner statement
- **Evidence:** 2026-10-05 receipt
- **Verified progress:** 2030-01-02 future
- **Evidence:** 2026-02-30 invalid
""")
    seed_uses_from_body(task, today, template_config)
    assert task.uses == ["2026-10-05", "2026-10-04"]


@pytest.mark.parametrize("archived", [False, True])
def test_due_commitment_persists_and_reheats_before_current(tmp_path, archived):
    root = make_vault(tmp_path / "memory", uses="2026-08-01")
    if archived:
        nightly.process_tasks(Config.load(root), date(2026, 10, 4), *load_tasks(Config.load(root)), [])
    body = triggers.TEMPLATE.replace("| --- | --- | --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- | --- | --- |\n| TR-001 | commitment | 2026-10-05 | revisit task | T-001 | active | 2026-10-04 |")
    fm.write(root / "agent/Triggers.md", {"tier": "hot", "pinned": True}, body)
    nightly.main(nightly_argv(root, "2026-10-05", "--write"))
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert "2026-10-05" in tasks[0].uses
    assert tasks[0].state["tier"] == "hot" and tasks[0].state["status"] == "open"
    assert "T-001" in (root / "CURRENT.md").read_text()
    nightly.main(nightly_argv(root, "2026-10-06", "--write"))
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert "2026-10-06" not in tasks[0].uses
    assert "fired 2026-10-05" in (root / "agent/Triggers.md").read_text()


@pytest.mark.parametrize("day", ["2026-10-05", "2026-11-01"])
def test_report_only_previews_all_transforms_without_writing(tmp_path, day, capsys):
    root = make_vault(tmp_path / "memory")
    fm.write(root / "agent/Triggers.md", {"tier": "hot", "pinned": True}, triggers.TEMPLATE)
    old_log = root / "agent/Sleep Log/2026-10.md"
    old_log.parent.mkdir()
    old_log.write_text("# Sleep log — 2026-10\n\nOLD_REPORT_SENTINEL\n")
    before = snapshot(root)
    with patch.object(nightly, "process_tasks", wraps=nightly.process_tasks) as tasks, \
         patch.object(nightly, "rebuild_current", wraps=nightly.rebuild_current) as current, \
         patch.object(triggers, "process", wraps=triggers.process) as trigger_processing, \
         patch.object(validate, "run", wraps=validate.run) as validation:
        nightly.main(nightly_argv(root, day))
    assert snapshot(root) == before
    assert old_log.read_text().startswith("# Sleep log — 2026-10\n\nOLD_REPORT_SENTINEL\n")
    assert tasks.call_count == current.call_count == trigger_processing.call_count == validation.call_count == 1
    report = capsys.readouterr().out
    assert "(report only; nothing written)" in report
    assert "task: T-001" in report and "current: active threads rebuilt" in report


def test_report_only_returns_validation_failure_without_applying_changes(tmp_path, capsys):
    root = make_vault(tmp_path / "memory")
    (root / "Broken.md").write_text("# Broken\n[[missing]]\n")
    before = snapshot(root)
    with pytest.raises(SystemExit) as caught:
        nightly.main(nightly_argv(root, "2026-10-05"))
    assert caught.value.code == 1
    assert snapshot(root) == before
    assert "VALIDATION: Broken.md: unresolved wikilinks ['missing']" in capsys.readouterr().out


def test_write_returns_validation_failure_and_logs_problems(vault, capsys):
    (vault / "Broken.md").write_text("# Broken\n[[missing]]\n")
    with pytest.raises(SystemExit) as caught:
        nightly.main(nightly_argv(vault, "2026-10-05", "--write"))
    assert caught.value.code == 1
    problem = "VALIDATION: Broken.md: unresolved wikilinks ['missing']"
    assert problem in capsys.readouterr().out
    log = (vault / "agent/Sleep Log/2026-10.md").read_text()
    assert "write, validation failed, not committed" in log
    assert problem in log


def test_preview_skips_runtime_directories(vault, monkeypatch):
    excluded = [".git", ".brain", ".obsidian", "__pycache__", ".pytest_cache"]
    for name in excluded:
        path = vault / name / "private.bin"
        path.parent.mkdir()
        path.write_bytes(b"EXCLUDED_FIXTURE_CONTENT")
    before = snapshot(vault)
    original = nightly.shutil.copyfile
    copied = []

    def guarded_copy(source, target, *args, **kwargs):
        rel = Path(source).relative_to(vault)
        assert rel.parts[0] not in excluded
        copied.append(rel.as_posix())
        return original(source, target, *args, **kwargs)

    monkeypatch.setattr(nightly.shutil, "copyfile", guarded_copy)
    nightly.main(nightly_argv(vault, "2026-11-01"))
    assert "agent/Tasks.md" in copied
    assert snapshot(vault) == before


def test_changed_hot_excerpt_remains_current_through_warm_and_hot(tmp_path):
    root = make_vault(tmp_path / "memory", uses="2026-10-04")
    full = "- **Next action:** OLD_ACTION\n- **Latest evidence:** OLD_EVIDENCE https://example.invalid/diagnostic-receipt\n- **Earlier evidence:**\n" + "\n".join(f"old detail {i}" for i in range(100))
    data, _ = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, Task("T-001", "Long", full, {"uses": "2026-10-04", "tier": "hot", "status": "open"}).render())
    nightly.process_tasks(Config.load(root), date(2026, 10, 5), *load_tasks(Config.load(root)), [])
    data, body = fm.read(root / "agent/Tasks.md")
    assert "- **Earlier evidence:**" in body
    body = body.replace("OLD_ACTION", "NEW_ACTION").replace("OLD_EVIDENCE", "NEW_EVIDENCE")
    fm.write(root / "agent/Tasks.md", data, body)
    nightly.process_tasks(Config.load(root), date(2026, 10, 20), *load_tasks(Config.load(root)), [])
    current_archive = read_archive_body(Config.load(root), "T-001")
    assert "NEW_ACTION" in current_archive and "NEW_EVIDENCE" in current_archive
    raw = (root / "agent/Tasks Archive/T-001.md").read_text()
    assert "OLD_EVIDENCE" in raw and "old detail 99" in raw
    data, body = fm.read(root / "agent/Tasks.md")
    _, tasks = parse_register(body)
    tasks[0].add_use("2026-10-21")
    fm.write(root / "agent/Tasks.md", data, tasks[0].render())
    nightly.process_tasks(Config.load(root), date(2026, 10, 21), *load_tasks(Config.load(root)), [])
    restored = fm.read(root / "agent/Tasks.md")[1]
    assert "NEW_ACTION" in restored and "NEW_EVIDENCE" in restored
    assert "status=open" in restored


def test_unchanged_hot_excerpt_keeps_original_full_archive(tmp_path):
    root = make_vault(tmp_path / "memory", uses="2026-10-04")
    full = "- **Next action:** SAME_ACTION\n- **Latest evidence:** https://example.invalid/diagnostic-receipt\n- **Earlier evidence:**\n" + "\n".join(f"original evidence {i}" for i in range(100))
    data, _ = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, Task("T-001", "Long", full, {"uses": "2026-10-04", "tier": "hot"}).render())
    nightly.process_tasks(Config.load(root), date(2026, 10, 5), *load_tasks(Config.load(root)), [])
    nightly.process_tasks(Config.load(root), date(2026, 10, 20), *load_tasks(Config.load(root)), [])
    assert "original evidence 99" in read_archive_body(Config.load(root), "T-001")
