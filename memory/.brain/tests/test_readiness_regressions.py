"""Fabricated diagnostic cases; no scheduled-execution credit."""
from datetime import date

import pytest

from brain import fm, nightly, validate
from brain.tasks import Task, archived_open_tasks, bounded_hot_body, load_tasks, parse_register, read_archive_body, write_archive
from brain.vault import Vault
from conftest import make_vault, nightly_argv, snapshot


def fixture(root):
    root = make_vault(root)
    fm.write(root / "Receipt.md", {"kind": "hub", "uses": []}, "# DIAGNOSTIC receipt\nFabricated validation evidence.\n")
    return root


def long_task(tier="hot", evidence_first=False, combined=False):
    fields = (
        "- **Status:** open\n"
        f"- **{'Owner / scope' if combined else 'Owner'}:** Diagnostic owner\n"
        f"- **{'Next / blocker' if combined else 'Next action'}:** inspect diagnostic artifact\n"
        "- **Blocker:** none\n"
        "- **Completion condition:** diagnostic artifact checked\n"
        "- **Latest evidence:** 2026-10-05 [[memory/Receipt|diagnostic source]]\n"
    )
    evidence = "- **Evidence:** " + " ".join(f"DIAGNOSTIC_{n}" for n in range(1000)) + "\n"
    body = evidence + fields if evidence_first else fields + evidence
    return Task("T-099", "Diagnostic oversized", body,
                {"status": "open", "uses": "2026-10-05", "tier": tier})


@pytest.mark.parametrize("label", ["Owner", "Owner / scope"])
def test_colon_outside_bold_current_field_after_long_evidence_is_preserved(tmp_path, label):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    task.body = task.body.replace("- **Owner:** Diagnostic owner\n", "")
    current = f"- **{label}**: CURRENT_OWNER"
    task.body = task.body.replace("DIAGNOSTIC_999\n", "DIAGNOSTIC_999\n" + current + "\n")
    original = task.body
    fields = [line for line in original.splitlines() if not line.startswith("- **Evidence:**")]
    nightly.place(root, task, date(2026, 10, 6), [], [])
    assert current in task.body.splitlines()
    assert all(line in task.body.splitlines() for line in fields)
    assert task.word_count() <= 150
    assert read_archive_body(root, task.tid).strip() == original.strip()
    write_archive(root, task, "diagnostic repeated archive", date(2026, 10, 7))
    assert read_archive_body(root, task.tid).strip() == original.strip()


@pytest.mark.parametrize("location", ["register", "archive"])
@pytest.mark.parametrize("evidence_first", [False, True])
@pytest.mark.parametrize("combined", [False, True])
def test_long_evidence_line_is_archived_and_hot_register_stays_bounded(tmp_path, location, evidence_first, combined):
    root = fixture(tmp_path / "memory")
    task = long_task("cold" if location == "archive" else "hot", evidence_first, combined)
    full_body = task.body
    fields = [line for line in full_body.splitlines() if not line.startswith("- **Evidence:**")]
    if location == "archive":
        write_archive(root, task, "cold", date(2026, 10, 5))
    else:
        data, body = fm.read(root / "agent/Tasks.md")
        fm.write(root / "agent/Tasks.md", data, body + "\n" + task.render())
    nightly.process_tasks(root, date(2026, 10, 6), *load_tasks(root), [])
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    retained = next(t for t in tasks if t.tid == task.tid)
    assert retained.word_count() <= 150
    assert retained.state["status"] == "open"
    assert "inspect diagnostic artifact" in retained.body
    assert all(line in retained.body.splitlines() for line in fields)
    assert retained.first_status() == "Status: open"
    assert "Tasks Archive/T-099|" in retained.body
    assert read_archive_body(root, task.tid).strip() == full_body.strip()
    # Repeat processing must preserve the full recoverable source, then a
    # warm/cold cycle and reactivation must restore the bounded current view.
    nightly.process_tasks(root, date(2026, 10, 7), *load_tasks(root), [])
    assert read_archive_body(root, task.tid).strip() == full_body.strip()
    _, repeat = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert next(t for t in repeat if t.tid == task.tid).body == retained.body
    nightly.process_tasks(root, date(2026, 11, 6), *load_tasks(root), [])
    archived = next(t for t in archived_open_tasks(root) if t.tid == task.tid)
    archived.add_use("2026-11-07")
    write_archive(root, archived, "cold", date(2026, 11, 7))
    nightly.process_tasks(root, date(2026, 11, 7), *load_tasks(root), [])
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    retained = next(t for t in tasks if t.tid == task.tid)
    assert retained.word_count() <= 150
    assert retained.state["status"] == "open"
    assert all(line in retained.body.splitlines() for line in fields)
    assert read_archive_body(root, task.tid).strip() == full_body.strip()


def test_evidence_only_task_requires_parent_judgment_without_pointer_only_replacement(tmp_path):
    root = fixture(tmp_path / "memory")
    task = long_task()
    task.body = "- **Evidence:** " + " ".join(f"DIAGNOSTIC_{n}" for n in range(1000)) + "\n"
    data, body = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, body + "\n" + task.render())
    before = snapshot(root)
    with pytest.raises(ValueError, match="T-099: parent judgment required.*no labeled current fields"):
        nightly.process_tasks(root, date(2026, 10, 6), *load_tasks(root), [])
    assert snapshot(root) == before


@pytest.mark.parametrize("label", ["Status", "Owner", "Next action", "Blocker", "Completion condition", "Latest evidence", "Owner / scope", "Next / blocker", "Custom current field"])
def test_required_field_overflow_fails_before_task_or_archive_mutation(tmp_path, label):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    task.body += f"- **{label}:** " + " ".join("DIAGNOSTIC_REQUIRED" for _ in range(151)) + "\n"
    write_archive(root, task, "diagnostic original", date(2026, 10, 5))
    before = snapshot(root)
    original = task.body
    with pytest.raises(ValueError, match="T-099: parent judgment required.*current fields.*150-word"):
        nightly.place(root, task, date(2026, 10, 6), [], [])
    assert task.body == original
    assert snapshot(root) == before


@pytest.mark.parametrize("overflow", [False, True])
def test_indented_current_field_continuation_is_preserved_or_requires_judgment(tmp_path, overflow):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    continuation = "  " + " ".join("DIAGNOSTIC_CONTINUATION" for _ in range(151 if overflow else 8))
    task.body = task.body.replace("- **Blocker:**", continuation + "\n- **Blocker:**")
    original = task.body
    if overflow:
        with pytest.raises(ValueError, match="parent judgment required"):
            nightly.place(root, task, date(2026, 10, 6), [], [])
        assert task.body == original
        assert not (root / "agent/Tasks Archive/T-099.md").exists()
    else:
        nightly.place(root, task, date(2026, 10, 6), [], [])
        assert continuation in task.body.splitlines()
        assert task.word_count() <= 150
        assert read_archive_body(root, task.tid).strip() == original.strip()


@pytest.mark.parametrize("current_words", [144, 145])
def test_current_fields_reserve_the_archive_pointer_at_the_exact_limit(tmp_path, current_words):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    fields = "\n".join(line for line in task.body.splitlines() if not line.startswith("- **Evidence:**"))
    padding = " ".join("DIAGNOSTIC_REQUIRED" for _ in range(current_words - len(fields.split())))
    task.body = task.body.replace("inspect diagnostic artifact", "inspect diagnostic artifact " + padding)
    original = task.body
    if current_words == 145:
        with pytest.raises(ValueError, match="parent judgment required"):
            nightly.place(root, task, date(2026, 10, 6), [], [])
        assert task.body == original
        assert not (root / "agent/Tasks Archive/T-099.md").exists()
    else:
        nightly.place(root, task, date(2026, 10, 6), [], [])
        assert task.word_count() == 150
        assert read_archive_body(root, task.tid).strip() == original.strip()


@pytest.mark.parametrize("position", ["start", "end"])
@pytest.mark.parametrize("receipt", ["https://example.invalid/diagnostic-receipt", "[[memory/Receipt]]", "[receipt](https://example.invalid/diagnostic-receipt)"])
def test_only_receipt_link_in_long_evidence_requires_parent_judgment(tmp_path, position, receipt):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    task.body = "\n".join(line for line in task.body.splitlines() if not line.startswith("- **Latest evidence:**")) + "\n"
    if position == "start":
        task.body = task.body.replace("- **Evidence:**", "- **Evidence:** " + receipt)
    else:
        task.body = task.body.replace("DIAGNOSTIC_999\n", "DIAGNOSTIC_999 " + receipt + "\n")
    original = task.body
    before = snapshot(root)
    with pytest.raises(ValueError, match="T-099: parent judgment required"):
        nightly.place(root, task, date(2026, 10, 6), [], [])
    assert task.body == original
    assert snapshot(root) == before


def test_short_evidence_keeps_its_direct_receipt_without_a_latest_field(tmp_path):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    fields = "\n".join(line for line in task.body.splitlines() if not line.startswith(("- **Evidence:**", "- **Latest evidence:**")))
    receipt = "- **Evidence:** https://example.invalid/diagnostic-receipt"
    task.body = fields + "\n" + receipt + "\n- **Earlier evidence:** " + " ".join("DIAGNOSTIC_OLD" for _ in range(1000)) + "\n"
    original = task.body
    nightly.place(root, task, date(2026, 10, 6), [], [])
    assert receipt in task.body.splitlines()
    assert task.word_count() <= 150
    assert read_archive_body(root, task.tid).strip() == original.strip()


@pytest.mark.parametrize("archive_link", ["[[memory/agent/Tasks Archive/T-099|archive]]", "[archive](agent/Tasks Archive/T-099.md)"])
def test_own_archive_pointer_is_not_a_direct_latest_receipt(tmp_path, archive_link):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    task.body = task.body.replace("[[memory/Receipt|diagnostic source]]", archive_link)
    task.body = task.body.replace("- **Evidence:**", "- **Evidence:** https://example.invalid/diagnostic-receipt")
    original = task.body
    before = snapshot(root)
    with pytest.raises(ValueError, match="T-099: parent judgment required"):
        nightly.place(root, task, date(2026, 10, 6), [], [])
    assert task.body == original
    assert snapshot(root) == before


def test_repeated_generated_warm_view_stays_canonical(tmp_path):
    root = fixture(tmp_path / "memory")
    task = long_task()
    task.state["uses"] = "2026-09-20"
    data, body = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, body + "\n" + task.render())
    nightly.process_tasks(root, date(2026, 10, 5), *load_tasks(root), [])
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    canonical = next(t for t in tasks if t.tid == task.tid).body
    for day in (date(2026, 10, 6), date(2026, 10, 7)):
        nightly.process_tasks(root, day, *load_tasks(root), [])
        _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
        assert next(t for t in tasks if t.tid == task.tid).body == canonical
        assert read_archive_body(root, task.tid).strip() == task.body.strip()


@pytest.mark.parametrize("previous", ["warm", "cold"])
def test_changed_compact_current_fields_never_restore_an_older_longer_archive(tmp_path, previous):
    root = fixture(tmp_path / "memory")
    old = long_task(evidence_first=True)
    old.body = old.body.replace("Diagnostic owner", "OLD_OWNER").replace("inspect diagnostic artifact", "OLD_NEXT")
    write_archive(root, old, "diagnostic old source", date(2026, 10, 5))
    archive = root / "agent/Tasks Archive/T-099.md"
    before = archive.read_bytes()
    current = Task(old.tid, old.title, nightly.bounded_hot_body(old).replace("OLD_OWNER", "NEW_OWNER").replace("OLD_NEXT", "NEW_NEXT"),
                   {"status": "open", "tier": previous, "uses": "2026-10-06"})
    data, body = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, body + "\n" + current.render())
    for day in (date(2026, 10, 6), date(2026, 10, 7)):
        nightly.process_tasks(root, day, *load_tasks(root), [])
        register = fm.read(root / "agent/Tasks.md")[1]
        _, tasks = parse_register(register)
        actual = next(t for t in tasks if t.tid == old.tid)
        assert actual.body.strip() == current.body.strip()
        assert "OLD_OWNER" not in register and "OLD_NEXT" not in register
        assert archive.read_bytes() == before
    nightly.process_tasks(root, date(2026, 11, 6), *load_tasks(root), [])
    archived = next(t for t in archived_open_tasks(root) if t.tid == old.tid)
    assert "NEW_OWNER" in archived.body and "NEW_NEXT" in archived.body
    assert "DIAGNOSTIC_999" in archive.read_text()
    archived.add_use("2026-11-07")
    write_archive(root, archived, "cold", date(2026, 11, 7))
    nightly.process_tasks(root, date(2026, 11, 7), *load_tasks(root), [])
    register = fm.read(root / "agent/Tasks.md")[1]
    assert "NEW_OWNER" in register and "NEW_NEXT" in register
    assert "OLD_OWNER" not in register and "OLD_NEXT" not in register
    assert "DIAGNOSTIC_999" in archive.read_text()


@pytest.mark.parametrize("overflow", [False, True])
def test_unindented_next_action_continuation_is_preserved_or_requires_judgment(tmp_path, overflow):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    continuation = " ".join("DIAGNOSTIC_ACTION_CONTINUATION" for _ in range(151 if overflow else 8))
    task.body = task.body.replace("- **Blocker:**", continuation + "\n- **Blocker:**")
    original = task.body
    before = snapshot(root)
    if overflow:
        with pytest.raises(ValueError, match="T-099: parent judgment required"):
            nightly.place(root, task, date(2026, 10, 6), [], [])
        assert task.body == original
        assert snapshot(root) == before
    else:
        nightly.place(root, task, date(2026, 10, 6), [], [])
        assert "inspect diagnostic artifact\n" + continuation + "\n- **Blocker:**" in task.body
        assert task.word_count() <= 150
        assert read_archive_body(root, task.tid).strip() == original.strip()


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_closed_archived_task_never_restores_or_requires_open_index(tmp_path, status):
    root = fixture(tmp_path / "memory")
    task = Task("T-099", "Diagnostic closed", "- **Evidence:** diagnostic closure\n",
                {"status": status, "uses": "2026-10-05", "tier": "hot"})
    write_archive(root, task, status, date(2026, 10, 5))
    archive = root / "agent/Tasks Archive/T-099.md"
    original = archive.read_bytes()
    assert all(t.tid != task.tid for t in archived_open_tasks(root))
    nightly.main(nightly_argv(root, "2026-10-06", "--write"))
    register = fm.read(root / "agent/Tasks.md")[1]
    assert "### T-099" not in register
    assert "T-099" not in (root / "CURRENT.md").read_text()
    assert "Tasks Archive/T-099|" not in (root / "Warm Index.md").read_text()
    assert archive.read_bytes() == original
    assert validate.run(Vault(root), set()) == []


@pytest.mark.parametrize("pinned", [False, True])
def test_previous_day_primed_expires_without_losing_uses_or_global_pin_rule(tmp_path, pinned):
    root = fixture(tmp_path / "memory")
    path = root / "agent/Primed/2026-10-05.md"
    path.parent.mkdir()
    body = "# DIAGNOSTIC old agenda\nPreserve this evidence.\n"
    uses = ["2026-10-06", "2026-10-05"]
    fm.write(path, {"kind": "primed", "tier": "hot", "pinned": pinned,
                    "uses": uses, "uses_inferred": []}, body)
    today = root / "agent/Primed/2026-10-06.md"
    fm.write(today, {"kind": "primed", "tier": "hot", "pinned": False,
                     "uses": ["2026-10-06"], "uses_inferred": []}, "# DIAGNOSTIC today\n")
    nightly.main(nightly_argv(root, "2026-10-06", "--write"))
    data, actual_body = fm.read(path)
    assert data["tier"] == ("hot" if pinned else "cold")
    assert data["uses"] == uses
    assert actual_body == body
    assert fm.read(today)[0]["tier"] == "hot"
    hot = (root / "Hot Set.md").read_text()
    assert "Primed/2026-10-05" not in hot
    assert validate.run(Vault(root), set()) == []


def test_ambiguous_indented_task_field_requires_parent_judgment():
    task = long_task(evidence_first=True)
    task.body = task.body.replace("- **Owner:**", "  - **Owner:**")
    original = task.body
    with pytest.raises(ValueError, match="parent judgment required: ambiguous indented task field"):
        bounded_hot_body(task)
    assert task.body == original


def test_edited_generated_warm_line_resolves_to_archive_body(vault):
    task = long_task()
    write_archive(vault, task, "warm", date(2026, 10, 5))
    task.state.update(tier="warm", uses="2026-10-06")
    data, body = fm.read(vault / "agent/Tasks.md")
    fm.write(vault / "agent/Tasks.md", data,
             body + "\n" + task.render_line().replace("Status: open", "Status: edited summary"))
    tasks, archived = load_tasks(vault)
    loaded = next(t for t in tasks if t.tid == task.tid)
    assert loaded.body.strip() == task.body.strip()
    assert loaded.uses == ["2026-10-06"]
    assert loaded.state["tier"] == "warm"
    assert not archived


@pytest.mark.parametrize("write", [False, True])
def test_nightly_required_field_overflow_keeps_task_and_current_bodies(tmp_path, write):
    root = fixture(tmp_path / "memory")
    task = long_task(evidence_first=True)
    task.body += "- **Next action:** " + "DIAGNOSTIC_REQUIRED " * 151 + "\n"
    data, body = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data, body + "\n" + task.render())
    current_before = fm.read(root / "CURRENT.md")[1]
    before = snapshot(root)
    args = nightly_argv(root, "2026-10-06", *(["--write"] if write else []))
    with pytest.raises(ValueError, match="T-099: parent judgment required"):
        nightly.main(args)
    assert fm.read(root / "CURRENT.md")[1] == current_before
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert next(t for t in tasks if t.tid == task.tid).body.strip() == task.body.strip()
    assert not (root / "agent/Tasks Archive/T-099.md").exists()
    if not write:
        assert snapshot(root) == before
