"""One owner queue and the nightly acceptance of unanswered confirms."""
from dataclasses import asdict, replace
from datetime import date
import json
import re

import pytest

from brain import fm, needs, nightly, triggers
from brain.config import Config
from brain.tasks import Task, write_archive
from brain.vault import Vault
from conftest import nightly_argv, snapshot


TODAY = date(2026, 10, 28)


def write_current(root, section):
    path = root / "CURRENT.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n## Needs you\n\n" + section)


def write_tasks(root, *tasks):
    path = root / "agent/Tasks.md"
    data, _ = fm.read(path)
    fm.write(path, data, "# Tasks\n\n## Active work\n\n" + "\n".join(task.render() for task in tasks))


def improvement(heading, status):
    return (f"### {heading}\n- **Gap:** An observed gap.\n- **Change:** A concrete change.\n"
            f"- **Status:** {status}\n\n")


def test_parse_section_preserves_bullet_order_and_optional_sources():
    body = (
        "# Current\n- 2026-10-28 · ask · Outside the section.\n"
        "## Needs you\n\n"
        "- 2026-10-28 · confirm · Booked the fare. · [[Lisbon trip]]\n"
        "- 2026-10-27 · ask · Which return?\n"
        "### More detail\n"
        "- 2026-10-26 · ask · Approve? · [[Travel/Lisbon#Return|trip]]\n"
        "## Other\n- 2026-10-28 · ask · Not an owner item.\n"
    )
    parsed = needs.parse_section(body)
    assert [(item.kind, item.text, item.since, item.source) for item in parsed] == [
        ("confirm", "Booked the fare.", "2026-10-28", "Lisbon trip"),
        ("ask", "Which return?", "2026-10-27", "CURRENT.md"),
        ("ask", "Approve?", "2026-10-26", "Travel/Lisbon#Return"),
    ]
    assert all(item.origin == "current" for item in parsed)
    assert needs.KINDS == ("ask", "confirm")


@pytest.mark.parametrize("line", [
    "", "Ordinary prose", "- Undated question?", "- 2026-10-28 · ask",
    "- 2026-10-28 · wait · Unknown kind", "- 2026-02-30 · ask · Invalid date",
    "- 20261028 · ask · Not ISO", "- yesterday · ask · Not a date",
    "- 2026-10-28 · ask · ", "- 2026-10-28 · ask · Text · not a wikilink",
    "- 2026-10-28 · confirm · Text · [[]]", "- 2026-10-28 · ask · Text · [[|label]]",
    "- 2026-10-28 · ask · Text · [[Hub]] · Extra field",
])
def test_malformed_bullets_are_skipped(line):
    assert needs.parse_section("## Needs you\n" + line + "\n") == []


@pytest.mark.parametrize("body", ["", "## Needs you later\n", "### Needs you\n"])
def test_missing_section_is_empty(body):
    assert needs.parse_section(body + "- 2026-10-28 · ask · Not in the section.\n") == []


def test_current_items_are_computed_without_changing_files(vault):
    write_current(vault, "- 2026-10-01 · confirm · Already done.\n"
                  "- 2026-10-28 · ask · Need a decision.\n")
    before = snapshot(vault.parent)
    result = needs.items(Config.load(vault), TODAY)
    assert [(item.kind, item.text, item.origin) for item in result] == [
        ("ask", "Need a decision.", "current"), ("confirm", "Already done.", "current")]
    assert snapshot(vault.parent) == before


def test_waiting_tasks_use_effective_state_and_latest_evidence(vault):
    states = [
        {"state": "waiting_for_owner", "status": "completed"},
        {"state": "ready", "status": "waiting_for_owner"},
        {"state": "in_progress"}, {"state": "blocked"},
        {"state": "completed"}, {"state": "cancelled"},
        {"status": "waiting_for_owner"}, {"status": "completed"}, {},
        {"state": "waiting_for_owner"},
    ]
    tasks = [Task(f"T-{index:03}", f"Task {index}",
                  "- **Status:** Awaiting owner.\n" + (
                      "- **Evidence:** 2026-10-27 latest; 2026-10-24 earlier.\n" if index == 1 else ""),
                  state) for index, state in enumerate(states, 1)]
    write_tasks(vault, *tasks)
    result = needs.items(Vault(vault), TODAY)
    assert [(item.text, item.since, item.source) for item in result] == [
        ("Task 1", "2026-10-27", "agent/Tasks.md#T-001"),
        ("Task 10", None, "agent/Tasks.md#T-010"),
    ]
    assert all(item.kind == "ask" and item.origin == "task" for item in result)


def test_archived_waiting_task_and_register_precedence(vault):
    config = Config.load(vault)
    archived = Task("T-002", "Archived wait", "- **Evidence:** 2026-10-20 owner asked.\n",
                    {"state": "waiting_for_owner", "tier": "cold"})
    write_archive(config, archived, "cold", TODAY)
    shadowed = Task("T-001", "Old waiting state", "Archived body.\n",
                    {"state": "waiting_for_owner"})
    write_archive(config, shadowed, "cold", TODAY)
    closed = Task("T-003", "Closed archive", "Completed.\n", {"state": "completed"})
    write_archive(config, closed, "cold", TODAY)
    result = needs.items(config, TODAY)
    assert [(item.text, item.since, item.source) for item in result] == [
        ("Archived wait", "2026-10-20", "agent/Tasks Archive/T-002.md")]


def test_warm_waiting_task_uses_recovered_body(vault):
    config = Config.load(vault)
    task = Task("T-002", "Warm wait", "- **Evidence:** 2026-10-22 owner asked.\n",
                {"state": "waiting_for_owner", "tier": "warm"})
    write_archive(config, task, "warm", TODAY)
    path = vault / "agent/Tasks.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n" + task.render_line(config))
    item, = needs.items(config, TODAY)
    assert item.since == "2026-10-22"
    assert item.source == "agent/Tasks.md#T-002"


@pytest.mark.parametrize("status, included", [
    ("`proposed`", True), ("proposed", True),
    ("proposed, awaiting the owner", True),
    ("`proposed`, assessed October 28, 2026 against evidence", True),
    ("`adopted`, assessed October 28, 2026 against `proposed`", False),
    ("`trialing`", False), ("dropped", False), ("adopted", False),
    ("", False), ("proposed was replaced by `adopted`", False),
    ("`trialing` then `proposed`", False),
])
@pytest.mark.parametrize("day, since", [("2026-10-26", "2026-10-26"), ("October 26", None)])
def test_improvement_status_and_heading_dates(vault, status, included, day, since):
    heading = day + " — Active supervision rather than status collection"
    fm.write(vault / "Supervision.md", {}, "# Supervision\n\n## Improvements\n\n" + improvement(heading, status))
    result = needs.items(Config.load(vault), TODAY)
    if not included:
        assert result == []
        return
    item, = result
    assert item.kind == "ask" and item.origin == "improvement"
    assert item.text == "Adopt? Active supervision rather than status collection"
    assert item.since == since
    assert item.source == "Supervision.md#" + heading


def test_improvement_scope_and_undated_headings(vault):
    body = (
        "# Hub\n\n" + improvement("Outside improvements", "proposed")
        + "## Improvements\n\n"
        + improvement("Better — owner decisions", "proposed")
        + "### No status of its own\n- **Change:** Nothing to adopt yet.\n\n"
        + improvement("2026-02-30 — Bad date still readable", "proposed")
        + "## Notes\n\n" + improvement("After improvements", "proposed")
    )
    fm.write(vault / "Topic.md", {}, body)
    fm.write(vault / "Responsibility.md", {"skill": "own-skill", "records": "own-records"},
             "# Responsibility\n\n## Improvements\n\n" + improvement("Undated proposal", "proposed"))
    fm.write(vault / "Record.md", {"kind": "log"}, body)
    fm.write(vault / "START.md", {}, body)
    log = vault / "agent/Improvements/2026-10.md"
    log.parent.mkdir(parents=True)
    fm.write(log, {}, body)
    result = needs.items(Config.load(vault), TODAY)
    assert [item.text for item in result] == [
        "Adopt? Undated proposal", "Adopt? Better — owner decisions", "Adopt? Bad date still readable"]
    assert all(item.since is None for item in result)


def test_triggers_are_not_owner_items(vault):
    row = "| TR-001 | commitment | 2026-10-28 | Owner-facing commitment | T-001 | active | 2026-10-27 |\n"
    fm.write(vault / "agent/Triggers.md", {}, triggers.TEMPLATE.replace("## Fired", row + "\n## Fired"))
    assert needs.items(Config.load(vault), TODAY) == []


def test_order_is_asks_then_confirms_with_source_order_preserved(vault):
    write_current(vault, "- 2026-10-28 · confirm · Confirm first.\n"
                  "- 2026-10-28 · ask · Current first.\n"
                  "- 2026-10-28 · ask · Current second.\n"
                  "- 2026-10-28 · confirm · Confirm second.\n")
    write_tasks(vault, Task("T-004", "Register first", "", {"state": "waiting_for_owner"}),
                Task("T-002", "Register second", "", {"state": "waiting_for_owner"}))
    for tid in ("T-005", "T-003"):
        write_archive(Config.load(vault), Task(tid, "Archive " + tid, "", {"state": "waiting_for_owner"}),
                      "cold", TODAY)
    for name in ("Z topic", "A topic"):
        fm.write(vault / (name + ".md"), {}, "## Improvements\n"
                 + improvement("October 27 — " + name + " newest", "proposed")
                 + improvement("October 26 — " + name + " older", "proposed"))
    assert [item.text for item in needs.items(Config.load(vault), TODAY)] == [
        "Current first.", "Current second.", "Register first", "Register second",
        "Archive T-003", "Archive T-005", "Adopt? A topic newest", "Adopt? A topic older",
        "Adopt? Z topic newest", "Adopt? Z topic older", "Confirm first.", "Confirm second."]


def test_ids_are_stable_and_depend_on_origin_source_and_text(vault):
    write_current(vault, "- 2026-10-27 · ask · A stable question.\n")
    item, = needs.items(Config.load(vault), TODAY)
    assert re.fullmatch(r"[0-9a-f]{12}", item.id)
    assert needs.items(Vault(vault), date(2026, 10, 29))[0].id == item.id
    assert replace(item, since="2026-10-28").id == item.id
    assert replace(item, kind="confirm").id == item.id
    assert len({item.id, replace(item, text="Different question.").id,
                replace(item, source="Hub.md").id, replace(item, origin="task").id}) == 4
    path = vault / "CURRENT.md"
    data, body = fm.read(path)
    body = body.replace("2026-10-27 · ask", "2026-10-28 · ask")
    body = body.replace("## Needs you\n", "## Needs you\n- 2026-10-28 · ask · Another question.\n")
    fm.write(path, data, body)
    assert needs.items(Config.load(vault), TODAY)[1].id == item.id


def test_configured_task_and_archive_paths(vault):
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nagent_dir = "Orphan"\n')
    (vault / "agent").rename(vault / "Orphan")
    config = Config.load(vault)
    task = Task("T-002", "Registered wait", "", {"state": "waiting_for_owner"})
    path = vault / "Orphan/Tasks.md"
    data, body = fm.read(path)
    fm.write(path, data, body + "\n" + task.render())
    write_archive(config, Task("T-003", "Archived wait", "", {"state": "waiting_for_owner"}), "cold", TODAY)
    assert [item.source for item in needs.items(config, TODAY)] == [
        "Orphan/Tasks.md#T-002", "Orphan/Tasks Archive/T-003.md"]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_expiry_boundary_asks_and_idempotence(vault, newline):
    path = vault / "CURRENT.md"
    lines = [
        "---", "# Keep this comment and quoting", 'kind: "orientation"', "---", "# Current",
        "- 2026-10-01 · confirm · Outside the section.", "## Needs you", "",
        "- 2026-10-22 · confirm · Six days stays.",
        "- 2026-10-21 · confirm · Seven days accepted. · [[Source]]",
        "- 2026-10-20 · confirm · Eight days accepted.",
        "- 2020-01-01 · ask · Asks never expire.",
        "- 2026-10-29 · confirm · Future stays.",
        "- 2026-02-30 · confirm · Malformed stays.  ", "",
        "## Other", "- 2026-10-01 · confirm · Other section stays.",
    ]
    before = newline.join(lines).encode("utf-8")
    path.write_bytes(before)
    assert needs.expire_confirms(Config.load(vault), TODAY) == ["Seven days accepted.", "Eight days accepted."]
    expected = before
    for line in lines[9:11]:
        expected = expected.replace((line + newline).encode("utf-8"), b"")
    assert path.read_bytes() == expected
    modified = path.stat().st_mtime_ns
    assert needs.expire_confirms(Config.load(vault), TODAY) == []
    assert path.read_bytes() == expected
    assert path.stat().st_mtime_ns == modified


@pytest.mark.parametrize("present", [True, False])
def test_expiry_without_section_or_file_is_noop(vault, present):
    if not present:
        (vault / "CURRENT.md").unlink()
    before = snapshot(vault)
    assert needs.expire_confirms(Config.load(vault), TODAY) == []
    assert snapshot(vault) == before


def test_nightly_write_expires_and_reports_each_confirm(vault, capsys):
    write_current(vault, "- 2026-10-21 · confirm · First accepted.\n"
                  "- 2026-10-20 · confirm · Second accepted.\n"
                  "- 2026-10-22 · confirm · Still pending.\n"
                  "- 2020-01-01 · ask · Still waiting.\n")
    nightly.main(nightly_argv(vault, TODAY, "--write"))
    output = capsys.readouterr().out
    body = fm.read(vault / "CURRENT.md")[1]
    log = vault / "agent/Sleep Log/2026-10.md"
    for text in ("First accepted.", "Second accepted."):
        line = "needs-you: confirm accepted after 7 days: " + text
        assert line in output and line in log.read_text()
        assert text not in body
    assert "Still pending." in body and "Still waiting." in body
    nightly.main(nightly_argv(vault, TODAY, "--write"))
    assert "needs-you:" not in capsys.readouterr().out
    assert log.read_text().count("needs-you: confirm accepted after 7 days:") == 2


def test_nightly_preview_does_not_expire_in_real_vault(vault, capsys):
    write_current(vault, "- 2026-10-21 · confirm · Preview only.\n")
    before = snapshot(vault.parent)
    nightly.main(nightly_argv(vault, TODAY))
    assert snapshot(vault.parent) == before
    output = capsys.readouterr().out
    assert "needs-you: confirm accepted after 7 days: Preview only." in output
    assert "(report only; nothing written)" in output


def test_nightly_commit_covers_expired_current_without_real_git_actions(vault, monkeypatch, capsys):
    write_current(vault, "- 2026-10-21 · confirm · Included in commit.\n")
    commands = []

    def fake_git(repo, *args):
        assert repo == vault.parent
        commands.append(args)
        if args[:2] == ("diff", "--cached"):
            return "memory/CURRENT.md"
        if args[0] == "rev-parse":
            return "fixture-sha"
        return ""

    monkeypatch.setattr(nightly, "sh", fake_git)
    nightly.main(nightly_argv(vault, TODAY, "--write", "--commit"))
    assert "Included in commit." not in fm.read(vault / "CURRENT.md")[1]
    assert commands[0] == ("add", "-A", "--", "memory", ":(exclude)memory/.brain", ".hermes.md")
    assert commands[2][:3] == ("commit", "-q", "-m")
    assert "needs-you: confirm accepted after 7 days: Included in commit." in commands[2][3]
    assert "commit fixture-sha" in capsys.readouterr().out


def test_expiry_runs_before_validation_and_is_recorded_on_failure(vault, monkeypatch, capsys):
    write_current(vault, "- 2026-10-21 · confirm · Accepted before validation.\n")

    def fail_validation(loaded, changed):
        assert "CURRENT.md" in changed
        assert "Accepted before validation." not in loaded.notes["CURRENT.md"].body
        return ["fixture validation failure"]

    monkeypatch.setattr(nightly.validate, "run", fail_validation)
    with pytest.raises(SystemExit, match="1"):
        nightly.main(nightly_argv(vault, TODAY, "--write"))
    assert "needs-you: confirm accepted after 7 days: Accepted before validation." in capsys.readouterr().out
    log = (vault / "agent/Sleep Log/2026-10.md").read_text()
    assert "write, validation failed, not committed" in log
    assert "needs-you: confirm accepted after 7 days: Accepted before validation." in log


@pytest.mark.parametrize("before_block", [True, False])
def test_rebuild_current_preserves_needs_you_section(vault, before_block):
    section = ("## Needs you\n\n- 2026-10-28 · ask · Keep this? · [[Trip]]  \n"
               "Prose, punctuation, and malformed bullets stay too.\n"
               "- 2026-10-27 · confirm · Already done.\n\n")
    block = nightly.MARK_START + "\nSTALE\n" + nightly.MARK_END + "\n\n"
    body = "# Current\n\n" + (section + block if before_block else block + section) + "## Tail\nKeep tail."
    fm.write(vault / "CURRENT.md", {"kind": "orientation"}, body)
    task = Task("T-001", "Owner task", "- **Status:** Waiting.\n", {"tier": "hot"})
    report = []
    nightly.rebuild_current(Config.load(vault), [task], TODAY, report)
    data, after = fm.read(vault / "CURRENT.md")
    assert section in after and after.rstrip().endswith("## Tail\nKeep tail.")
    assert data == {"kind": "orientation"}
    assert "STALE" not in after and "T-001" in after
    assert report == ["current: active threads rebuilt (1 hot tasks)"]
    assert [item.text for item in needs.parse_section(after)] == ["Keep this?", "Already done."]


def test_cli_text_and_json(vault, capsys):
    write_current(vault, "- 2026-10-28 · confirm · Went ahead.\n"
                  "- 2026-10-28 · ask · Owner decision. · [[Trip]]\n")
    write_tasks(vault, Task("T-002", "Waiting task", "", {"state": "waiting_for_owner"}))
    fm.write(vault / "Topic.md", {}, "## Improvements\n" + improvement("October 26 — Better practice", "proposed"))
    before = snapshot(vault.parent)
    nightly.main(["needs-you", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines() == [
        "ask · Owner decision. · Trip", "ask · Waiting task · agent/Tasks.md#T-002",
        "ask · Adopt? Better practice · Topic.md#October 26 — Better practice",
        "confirm · Went ahead. · CURRENT.md"]
    nightly.main(["needs-you", "--json", "--vault", str(vault)])
    result = json.loads(capsys.readouterr().out)
    assert result == [asdict(item) for item in needs.items(Config.load(vault), TODAY)]
    assert set(result[0]) == {"id", "kind", "text", "origin", "since", "source"}
    assert snapshot(vault.parent) == before


def test_empty_cli(vault, capsys):
    nightly.main(["needs-you", "--vault", str(vault)])
    assert capsys.readouterr().out == ""
    nightly.main(["needs-you", "--vault", str(vault), "--json"])
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.parametrize("entry, status", [
    ("- **Status:** `proposed`, October 26.\n", "proposed"),
    ("- **Status:** Trialing since October 26.\n", "trialing"),
    ("Date, gap, evidence and the change, `status: proposed`.\n", "proposed"),
    ("- status: Adopted\n", "adopted"),
    ("- **Gap:** no status here.\n", None),
])
def test_improvement_status_accepts_the_field_and_the_inline_form(entry, status):
    assert needs.improvement_status(entry) == status


def test_a_set_aside_task_is_not_an_ask(vault):
    write_tasks(vault, Task("T-001", "Parked", "", {"state": "set_aside"}),
                Task("T-002", "Waiting", "", {"state": "waiting_for_owner"}))
    assert [item.text for item in needs.items(Config.load(vault), TODAY)] == ["Waiting"]


def test_cli_ask_adds_once_with_its_source(vault, capsys):
    for _ in range(2):
        nightly.main(["needs-you", "--vault", str(vault), "--ask", "Still renew the lease?", "--source", "agent/Tasks#T-004"])
    asks = [item for item in needs.parse_section(fm.read(vault / "CURRENT.md")[1]) if item.kind == "ask"]
    assert [(item.text, item.source) for item in asks] == [("Still renew the lease?", "agent/Tasks#T-004")]


def test_cli_stale_tasks(vault, capsys):
    write_tasks(vault, Task("T-001", "Old", "- **Status:** parked by nobody\n", {"state": "ready", "uses": "2020-01-01", "tier": "cold"}),
                Task("T-002", "Set aside", "", {"state": "set_aside", "uses": "2020-01-01"}))
    nightly.main(["stale-tasks", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines() == [
        "T-001 · ready · cold · last touched 2020-01-01 · Old · Status: parked by nobody"]


def test_cli_ask_is_not_suppressed_by_a_confirm_with_the_same_words(vault):
    write_current(vault, "- 2026-10-28 · confirm · Renew the lease.\n")
    nightly.main(["needs-you", "--vault", str(vault), "--ask", "Renew the lease."])
    assert [item.kind for item in needs.parse_section(fm.read(vault / "CURRENT.md")[1])] == ["confirm", "ask"]


@pytest.mark.parametrize("argv", [["--json", "--ask", "x"], ["--source", "agent/Tasks#T-001"]])
def test_cli_ask_rejects_conflicting_arguments(vault, argv):
    before = snapshot(vault.parent)
    with pytest.raises(SystemExit):
        nightly.main(["needs-you", "--vault", str(vault), *argv])
    assert snapshot(vault.parent) == before
