"""One owner queue and the nightly acceptance of unanswered confirms."""
from dataclasses import asdict, replace
from datetime import date
import json
import re

import pytest

from brain import cli, fm, ideas, needs, nightly, triggers
from brain.config import Config
from brain.tasks import Task, load_tasks, write_archive
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
@pytest.mark.parametrize("day, since", [("2026-10-26", "2026-10-26"), ("October 26", "2026-10-26"),
                                       ("October 26, 2026", "2026-10-26")])
def test_improvement_status_and_heading_dates(vault, status, included, day, since):
    heading = day + " — Active supervision rather than status collection"
    fm.write(vault / "Supervision.md", {}, "# Supervision\n\n## Improvements\n\n" + improvement(heading, status))
    assert needs.items(Config.load(vault), TODAY) == []
    result = ideas.items(Config.load(vault), TODAY)
    if not included:
        assert result == []
        return
    item, = result
    assert item.kind == "idea" and item.origin == "improvement"
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
    assert needs.items(Config.load(vault), TODAY) == []
    result = ideas.items(Config.load(vault), TODAY)
    # All three proposals are open ideas; shown() chooses only one.
    assert [item.text for item in result] == ["Adopt? Undated proposal", "Adopt? Better — owner decisions", "Adopt? Bad date still readable"]
    assert all(item.since is None for item in result)


def test_triggers_are_not_owner_items(vault):
    row = "| TR-001 | commitment | 2026-10-28 | Owner-facing commitment | T-001 | active | 2026-10-27 |\n"
    fm.write(vault / "agent/Triggers.md", {}, triggers.TEMPLATE.replace("## Fired", row + "\n## Fired"))
    assert needs.items(Config.load(vault), TODAY) == []


def test_order_is_ranked_asks_then_confirms_with_ties_in_written_order(vault):
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
    # Ranked: what blocks work (tasks waiting on the owner) first, then asks newest first, ties in written order;
    # confirms last.
    assert [item.text for item in needs.items(Config.load(vault), TODAY)] == [
        "Register first", "Register second", "Archive T-003", "Archive T-005",
        "Current first.", "Current second.",
        "Confirm first.", "Confirm second."]


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
        "- 2026-10-24 · confirm · Four days stays.",
        "- 2026-10-23 · confirm · Five days accepted. · [[Source]]",
        "- 2026-10-22 · confirm · Six days accepted.",
        "- 2020-01-01 · ask · Asks never expire.",
        "- 2026-10-29 · confirm · Future stays.",
        "- 2026-02-30 · confirm · Malformed stays.  ", "",
        "## Other", "- 2026-10-01 · confirm · Other section stays.",
    ]
    before = newline.join(lines).encode("utf-8")
    path.write_bytes(before)
    assert needs.expire_confirms(Config.load(vault), TODAY) == ["Five days accepted.", "Six days accepted."]
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
                  "- 2026-10-24 · confirm · Still pending.\n"
                  "- 2026-10-25 · ask · Still waiting.\n")
    cli.main(nightly_argv(vault, TODAY, "--write"))
    output = capsys.readouterr().out
    body = fm.read(vault / "CURRENT.md")[1]
    log = vault / "agent/Sleep Log/2026-10.md"
    for text in ("First accepted.", "Second accepted."):
        line = "needs-you: confirm accepted after 5 days: " + text
        assert line in output and line in log.read_text()
        assert text not in body
    assert "Still pending." in body and "Still waiting." in body
    cli.main(nightly_argv(vault, TODAY, "--write"))
    assert "needs-you:" not in capsys.readouterr().out
    assert log.read_text().count("needs-you: confirm accepted after 5 days:") == 2


def test_nightly_preview_does_not_expire_in_real_vault(vault, capsys):
    write_current(vault, "- 2026-10-21 · confirm · Preview only.\n")
    before = snapshot(vault.parent)
    cli.main(nightly_argv(vault, TODAY))
    assert snapshot(vault.parent) == before
    output = capsys.readouterr().out
    assert "needs-you: confirm accepted after 5 days: Preview only." in output
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

    monkeypatch.setattr(cli, "sh", fake_git)
    cli.main(nightly_argv(vault, TODAY, "--write", "--commit"))
    assert "Included in commit." not in fm.read(vault / "CURRENT.md")[1]
    assert commands[0] == ("add", "-A", "--", "memory", ":(exclude)memory/.brain", ".context.md")
    assert commands[2][:3] == ("commit", "-q", "-m")
    assert "needs-you: confirm accepted after 5 days: Included in commit." in commands[2][3]
    assert "commit fixture-sha" in capsys.readouterr().out


def test_expiry_runs_before_validation_and_is_recorded_on_failure(vault, monkeypatch, capsys):
    write_current(vault, "- 2026-10-21 · confirm · Accepted before validation.\n")

    def fail_validation(loaded, changed):
        assert "CURRENT.md" in changed
        assert "Accepted before validation." not in loaded.notes["CURRENT.md"].body
        return ["fixture validation failure"]

    monkeypatch.setattr(nightly.validate, "run", fail_validation)
    with pytest.raises(SystemExit, match="1"):
        cli.main(nightly_argv(vault, TODAY, "--write"))
    assert "needs-you: confirm accepted after 5 days: Accepted before validation." in capsys.readouterr().out
    log = (vault / "agent/Sleep Log/2026-10.md").read_text()
    assert "write, validation failed, not committed" in log
    assert "needs-you: confirm accepted after 5 days: Accepted before validation." in log


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
    cli.main(["needs-you", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines() == [   # ranked: what blocks work, then newest first, confirms last
        "ask · Waiting task · agent/Tasks.md#T-002", "ask · Owner decision. · Trip",
        "confirm · Went ahead. · CURRENT.md"]
    cli.main(["needs-you", "--json", "--vault", str(vault)])
    result = json.loads(capsys.readouterr().out)
    assert result == [asdict(item) for item in needs.items(Config.load(vault), TODAY)]
    assert set(result[0]) == {"id", "kind", "text", "origin", "since", "source", "detail", "context", "needs", "task", "owner",
                              "default", "area", "goal", "hours", "away"}
    assert snapshot(vault.parent) == before


def test_empty_cli(vault, capsys):
    cli.main(["needs-you", "--vault", str(vault)])
    assert capsys.readouterr().out == ""
    cli.main(["needs-you", "--vault", str(vault), "--json"])
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
        cli.main(["needs-you", "--vault", str(vault), "--ask", "Still renew the lease?", "--source", "agent/Tasks#T-004"])
    asks = [item for item in needs.parse_section(fm.read(vault / "CURRENT.md")[1]) if item.kind == "ask"]
    assert [(item.text, item.source) for item in asks] == [("Still renew the lease?", "agent/Tasks#T-004")]


def test_cli_stale_tasks(vault, capsys):
    write_tasks(vault, Task("T-001", "Old", "- **Status:** parked by nobody\n", {"state": "ready", "uses": "2020-01-01", "tier": "cold"}),
                Task("T-002", "Set aside", "", {"state": "set_aside", "uses": "2020-01-01"}))
    cli.main(["stale-tasks", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines() == [
        "T-001 · ready · cold · last touched 2020-01-01 · Old · Status: parked by nobody"]


def test_cli_ask_is_not_suppressed_by_a_confirm_with_the_same_words(vault):
    write_current(vault, "- 2026-10-28 · confirm · Renew the lease.\n")
    cli.main(["needs-you", "--vault", str(vault), "--ask", "Renew the lease."])
    assert [item.kind for item in needs.parse_section(fm.read(vault / "CURRENT.md")[1])] == ["confirm", "ask"]


@pytest.mark.parametrize("argv", [["--json", "--ask", "x"], ["--source", "agent/Tasks#T-001"]])
def test_cli_ask_rejects_conflicting_arguments(vault, argv):
    before = snapshot(vault.parent)
    with pytest.raises(SystemExit):
        cli.main(["needs-you", "--vault", str(vault), *argv])
    assert snapshot(vault.parent) == before



def test_an_ask_unanswered_for_five_days_is_retired_and_reported_but_a_tasks_ask_waits(vault, capsys):
    write_current(vault, "- 2026-10-23 · ask · Five days old.\n"
                  "- 2026-10-24 · ask · Four days old.\n"
                  "- 2026-09-30 · ask · The task's own question. · [[agent/Tasks#T-004]]\n")
    cli.main(nightly_argv(vault, TODAY, "--write"))
    output = capsys.readouterr().out
    body = fm.read(vault / "CURRENT.md")[1]
    line = "needs-you: ask unanswered 5 days, retired: Five days old."
    assert line in output and line in (vault / "agent/Sleep Log/2026-10.md").read_text()
    assert "Five days old." not in body and "Four days old." in body and "The task's own question." in body


def test_a_sixth_ask_is_added_and_the_lowest_ranked_overflow_goes_to_the_evening(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    write_current(vault, "".join(f"- 2026-10-2{n} · ask · Question {n}.\n" for n in range(5))
                  + "- 2026-10-27 · confirm · Did a thing.\n")
    cli.main(["needs-you", "--vault", str(vault), "--ask", "Question 1."])   # a repeat is not a new ask
    cli.main(["needs-you", "--vault", str(vault), "--ask", "Renew the registration by November 1?"])
    assert "now holds 6 asks; tonight's evening decides the lowest-ranked down to 5" in capsys.readouterr().err
    body = fm.read(vault / "CURRENT.md")[1]
    assert "Renew the registration" in body and body.count("· ask ·") == 6
    ranked = [item.text for item in needs.items(Config.load(vault), TODAY)]
    assert ranked[0] == "Renew the registration by November 1?"          # a date this week first
    assert ranked[1:6] == ["Question 4.", "Question 3.", "Question 2.", "Question 1.", "Question 0."]   # then newest first
    assert ranked[-1] == "Did a thing."
    cli.main(["needs-you", "--vault", str(vault), "--overflow"])
    assert capsys.readouterr().out.splitlines() == ["2026-10-20 · Question 0. · CURRENT.md"]
    from brain import validate
    from brain.vault import Vault
    assert validate.needs_ceiling(Vault(vault)) == ["CURRENT.md: 6 open asks in Needs you; the evening decides the lowest-ranked down to 5 (`brain needs-you --overflow`)"]

def test_stale_lists_asks_three_days_old_oldest_first_but_not_a_tasks(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    write_current(vault, "- 2026-10-26 · ask · Fresh.\n- 2026-10-25 · ask · Three days.\n- 2026-10-01 · ask · Older.\n"
                  "- 2026-10-01 · ask · A task's. · [[agent/Tasks#T-009]]\n- 2026-10-01 · confirm · Not an ask.\n")
    cli.main(["needs-you", "--vault", str(vault), "--stale"])
    assert capsys.readouterr().out.splitlines() == ["2026-10-01 · Older. · CURRENT.md", "2026-10-25 · Three days. · CURRENT.md"]


def test_a_newer_ask_for_a_task_replaces_its_older_one_and_items_name_the_task(vault):
    from brain import journal
    write_current(vault, "- 2026-10-26 · ask · More listings, or tighter commutes? · [[agent/Tasks#T-004]]\n"
                  "- 2026-10-26 · ask · Reconnect Supabase? · [[agent/Tasks#T-003]]\n"
                  "- 2026-10-26 · ask · Book the dinner?\n")
    config = Config.load(vault)
    journal._add_ask(config, TODAY, "What would done look like for the apartment site?", "agent/Tasks#T-004")
    body = fm.read(vault / "CURRENT.md")[1]
    assert "More listings" not in body and "What would done look like" in body
    assert "Reconnect Supabase?" in body and "Book the dinner?" in body
    answered = (vault / "agent/Answered/2026-10.md").read_text()
    assert "More listings, or tighter commutes? · because a newer question about T-004 replaced it" in answered
    by_text = {item.text: item for item in needs.items(config, TODAY)}
    assert by_text["What would done look like for the apartment site?"].task == "T-004"
    assert by_text["Reconnect Supabase?"].task == "T-003"
    assert by_text["Book the dinner?"].task == ""
    # a task's ask ranks with what blocks work, ahead of an unlinked one from the same day
    ranked = [item.text for item in needs.items(config, TODAY)]
    assert ranked.index("Reconnect Supabase?") < ranked.index("Book the dinner?")



def test_all_proposals_are_ideas_ranked_newest_first(vault):
    fm.write(vault / "Hub.md", {}, "# Hub\n\n## Improvements\n\n"
             + improvement("September 20 — Too old to show", "proposed")
             + improvement("October 20 — Third newest", "proposed")
             + improvement("October 27 — Newest", "proposed")
             + improvement("October 25 — Second newest", "proposed"))
    assert [item.text for item in ideas.items(Config.load(vault), TODAY)] == ["Adopt? Newest", "Adopt? Second newest", "Adopt? Third newest", "Adopt? Too old to show"]



def test_resolve_removes_the_answered_item_and_records_the_evidence(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-03")
    write_current(vault, "- 2026-10-28 · ask · Accept Kevin's Tailscale share for Hands? · [[agent/Tasks#T-037]]\n"
                  "- 2026-10-29 · ask · Book the dinner for Saturday?\n"
                  "- 2026-10-30 · confirm · Moved the dentist to Thursday.\n")
    write_tasks(vault, Task("T-009", "Waiting task", "", {"state": "waiting_for_owner"}))
    cli.main(["needs-you", "--vault", str(vault), "--resolve", "tailscale share", "--because", "tailscale status shows Kevin's share accepted on Nov 2"])
    assert capsys.readouterr().out == "resolved ask: Accept Kevin's Tailscale share for Hands?\n"
    body = fm.read(vault / "CURRENT.md")[1]
    assert "Tailscale" not in body and "Book the dinner" in body and "Moved the dentist" in body
    log = (vault / "agent/Answered/2026-11.md").read_text()
    assert log.startswith("---\nkind: log\ntier: cold\n---\n# Answered")
    assert "- 2026-11-03 · ask · Accept Kevin's Tailscale share for Hands? · because tailscale status shows Kevin's share accepted on Nov 2 · [[agent/Tasks#T-037]]" in log
    confirm, = [item for item in needs.items(Config.load(vault), date(2026, 11, 3)) if item.kind == "confirm"]
    cli.main(["needs-you", "--vault", str(vault), "--resolve", confirm.id, "--because", "Roshan said Thursday works"])
    assert "Moved the dentist" not in fm.read(vault / "CURRENT.md")[1]
    assert log.count("\n- ") + 1 == (vault / "agent/Answered/2026-11.md").read_text().count("\n- ")
    from brain.vault import Vault
    assert Vault(vault).kind_for("agent/Answered/2026-11.md") == "log"


@pytest.mark.parametrize("which, because, message", [
    ("nothing like this", "x", "no open item matches"),
    ("?", "x", "open items match"),
    ("Waiting task", "x", "change the task's state"),
    ("dinner", "", "say what answered it"),
])
def test_resolve_refuses_what_it_cannot_settle(vault, capsys, which, because, message):
    write_current(vault, "- 2026-10-29 · ask · Book the dinner for Saturday?\n- 2026-10-29 · ask · Call the plumber?\n")
    write_tasks(vault, Task("T-009", "Waiting task", "", {"state": "waiting_for_owner"}))
    args = ["needs-you", "--vault", str(vault), "--resolve", which]
    if because:
        args += ["--because", because]
    with pytest.raises(SystemExit):
        cli.main(args)
    out = capsys.readouterr()
    assert message in out.err + str(out.out) or because == ""
    assert "Book the dinner" in fm.read(vault / "CURRENT.md")[1]


def test_dismiss_no_sets_the_task_aside_and_the_item_never_returns(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-03")
    write_tasks(vault, Task("T-011", "Hands share", "- **Status:** waiting\n", {"state": "ready"}))
    write_current(vault, "- 2026-10-28 · ask · Accept Kevin's share for Hands? · [[agent/Tasks#T-011]]\n")
    cli.main(["needs-you", "--vault", str(vault), "--dismiss", "Kevin's share"])
    assert capsys.readouterr().out == "dismissed (no) ask: Accept Kevin's share for Hands?\n"
    assert "Kevin's share" not in fm.read(vault / "CURRENT.md")[1]
    task, = [t for t in load_tasks(Config.load(vault))[0] if t.tid == "T-011"]
    assert task.task_state == "set_aside" and "- **Reviewed:** 2026-11-03 — set aside: dismissed by the owner in their app" in task.body
    log = (vault / "agent/Answered/2026-11.md").read_text()
    assert "· dismissed no · ask · Accept Kevin's share for Hands? · id:" in log


def test_dismiss_done_completes_a_waiting_task(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-03")
    write_tasks(vault, Task("T-012", "Renew registration", "- **Status:** waiting on Roshan\n", {"state": "waiting_for_owner"}))
    item, = needs.items(Config.load(vault), date(2026, 11, 3))
    cli.main(["needs-you", "--vault", str(vault), "--dismiss", item.id, "--reason", "done"])
    task, = [t for t in load_tasks(Config.load(vault))[0] if t.tid == "T-012"]
    assert task.task_state == "completed" and "the owner said they did it, in their app" in task.body
    assert needs.items(Config.load(vault), date(2026, 11, 3)) == []


def test_ideas_declines_a_proposal_and_stop_asking_is_logged_for_the_people_review(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    fm.write(vault / "Hub.md", {}, "# Hub\n\n## Improvements\n\n" + improvement("October 27 — Weekly digest", "proposed"))
    item, = ideas.items(Config.load(vault), TODAY)
    ideas.respond(Config.load(vault), item.id, TODAY, "stop-asking")
    assert "`declined`, 2026-10-28" in (vault / "Hub.md").read_text()
    assert ideas.items(Config.load(vault), TODAY) == []
    assert needs.items(Config.load(vault), TODAY) == []
    assert "· dismissed stop-asking · idea · Adopt? Weekly digest · id:" in (vault / "agent/Answered/2026-10.md").read_text()
    with pytest.raises(ValueError):
        ideas.respond(Config.load(vault), "anything", TODAY, "maybe")



def test_items_carry_the_question_why_it_needs_the_owner_and_what_it_is_about(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    cli.main(["needs-you", "--vault", str(vault), "--ask", "Book the Saturday dinner for Mira's visit?",
                  "--why", "Two places have tables at 7; I'll book the one you pick, or neither."])
    cli.main(["needs-you", "--vault", str(vault), "--ask", "Keep the refundable fare?"])
    write_tasks(vault, Task("T-030", "Connect Roshan's Google accounts",
                            "- **Status:** the Gmail link is ready\n- **Blocker:** T-030 step 3 needs an account pick\n"
                            "- **Ask:** Which of your two Gmails should I read first?\n"
                            "- **Why:** I'll start your morning summary from that one.\n",
                            {"state": "waiting_for_owner"}),
                Task("T-031", "Untitled wait", "- **Status:** no blocker written\n", {"state": "waiting_for_owner"}))
    fm.write(vault / "Correspondence.md", {}, "# Correspondence\n\n## Improvements\n\n"
             "### October 27 — Recheck changed correspondence, not every old letter\n"
             "- **Gap:** Each morning I reread every thread, even ones that have not changed.\n"
             "- **Change:** Only reread threads with a new message since yesterday.\n"
             "- **Ask:** Want me to only reread the mail threads that changed?\n"
             "- **Why:** " + "Mornings come faster. " * 20 + "\n"
             "- **Status:** proposed\n\n")
    found = {item.text: item for item in needs.items(Config.load(vault), TODAY)}
    dinner = found["Book the Saturday dinner for Mira's visit?"]
    assert dinner.detail == "Two places have tables at 7; I'll book the one you pick, or neither."
    assert found["Keep the refundable fare?"].detail == ""
    google = found["Which of your two Gmails should I read first?"]
    assert google.context == "Connect Roshan's Google accounts"
    assert google.detail == "I'll start your morning summary from that one."   # working notes never shown
    untitled = found["Untitled wait"]                                 # no Ask: the title alone
    assert untitled.context == "" and untitled.detail == ""
    idea, = ideas.items(Config.load(vault), TODAY)
    assert idea.text not in found
    assert idea.context == "Correspondence"
    assert len(idea.detail.split()) == needs.WHY_WORDS and idea.detail.endswith("…")
    from brain import validate
    from brain.vault import Vault
    assert validate.owner_questions(Vault(vault)) == [
        "T-031: no Ask line, the proposition the owner sees (and a Why line, what they get)",
        "Correspondence.md · October 27 — Recheck changed correspondence, not every old letter: Why line is 60 words; at most 40"]


def test_a_task_items_id_holds_when_its_ask_is_reworded_and_removing_an_ask_takes_its_why(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    write_tasks(vault, Task("T-040", "Hands share", "- **Ask:** Accept Kevin's share?\n", {"state": "waiting_for_owner"}))
    first, = needs.items(Config.load(vault), TODAY)
    write_tasks(vault, Task("T-040", "Hands share", "- **Ask:** Accept Kevin's share for Hands, once I check it's his Mac?\n",
                            {"state": "waiting_for_owner"}))
    second, = needs.items(Config.load(vault), TODAY)
    assert first.id == second.id and first.text != second.text
    write_current(vault, "- 2026-10-27 · ask · First?\n  - why: the first reason\n- 2026-10-27 · ask · Second?\n  - why: the second reason\n")
    needs.resolve(Config.load(vault), "First?", "answered", TODAY)
    body = fm.read(vault / "CURRENT.md")[1]
    assert "First?" not in body and "the first reason" not in body
    assert "Second?" in body and "the second reason" in body
    second_ask, = [item for item in needs.items(Config.load(vault), TODAY) if item.origin == "current"]
    assert second_ask.detail == "the second reason"


def test_a_current_bullet_repeating_a_waiting_task_is_shown_once_and_validate_names_it(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    write_tasks(vault, Task("T-003", "Apartment token", "- **Ask:** Can you replace the apartment site's token?\n",
                            {"state": "waiting_for_owner"}))
    write_current(vault, "- 2026-10-27 · ask · Can you replace the apartment site's token? · [[agent/Tasks Archive/T-003]]\n"
                         "- 2026-10-27 · ask · Same task, other words · [[agent/Tasks#T-003]]\n"
                         "- 2026-10-27 · confirm · Migration draft: the goals stay empty until you write them · [[CURRENT]]\n"
                         "- 2026-10-27 · ask · Book the Saturday dinner? · [[CURRENT]]\n")
    found = needs.items(Config.load(vault), TODAY)
    assert [(i.origin, i.text) for i in found if "token" in i.text or "other words" in i.text] == [
        ("task", "Can you replace the apartment site's token?")]
    from brain import validate
    from brain.vault import Vault
    problems = validate.needs_hygiene(Vault(vault))
    assert len(problems) == 3
    assert sum("repeats T-003" in p for p in problems) == 2
    assert any("about the brain itself" in p and "Migration draft" in p for p in problems)


def test_a_hidden_duplicate_bullet_can_still_be_resolved(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    write_tasks(vault, Task("T-003", "Apartment token", "- **Ask:** Can you replace the apartment site's token?\n",
                            {"state": "waiting_for_owner"}))
    write_current(vault, "- 2026-10-27 · ask · Can you replace the apartment site's token? · [[agent/Tasks Archive/T-003]]\n")
    needs.resolve(Config.load(vault), "Can you replace the apartment site's token?", "repeats T-003", TODAY)
    assert "apartment" not in fm.read(vault / "CURRENT.md")[1]
    from brain import validate
    from brain.vault import Vault
    assert validate.needs_hygiene(Vault(vault)) == []
    assert [i.origin for i in needs.items(Config.load(vault), TODAY)] == ["task"]


def test_health_counts_the_needs_you_problems_validate_names(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-28")
    from brain import health
    clean = health.report(Config.load(vault))["vault"]["validation_problems"]
    write_current(vault, "- 2026-10-27 · confirm · Migration draft: the goals stay empty · [[CURRENT]]\n")
    assert health.report(Config.load(vault))["vault"]["validation_problems"] == clean + 1


def test_current_items_dated_over_two_weeks_ago_are_named_and_history_is_not(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-03")
    fm.write(vault / "CURRENT.md", {"kind": "orientation"},
             "# Current\n\n## Needs you\n- 2026-10-01 · ask · Old ask? · [[CURRENT]]\n\n"
             "## Active\n<!-- brain:active-threads start -->\n- Thread from 2026-10-01\n<!-- brain:active-threads end -->\n\n"
             "October 12: Reach held for promotion.\n\nOctober 30: Demiurge is chat-first.\n\n"
             "- Flight to Madison on 2026-10-29, back 2026-11-01.\n\n"
             "## History\nOctober 1: the vault was created.\n")
    from brain import validate
    from brain.vault import Vault
    problems = validate.stale_current(Vault(vault))
    assert len(problems) == 1 and "2026-10-12" in problems[0] and "Reach held" in problems[0]



def test_current_presenting_a_closed_task_as_live_is_named(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-03")
    write_tasks(vault, Task("T-010", "Migrate", "- **Status:** done\n", {"state": "completed"}),
                Task("T-011", "Still going", "- **Status:** going\n", {"state": "in_progress"}))
    fm.write(vault / "CURRENT.md", {"kind": "orientation"},
             "# Current\n\nT-010 migration is in progress.\n\nT-011 is moving.\n\nT-010 completed on Nov 2.\n\n"
             "<!-- T-010 note -->\n\n## History\nT-010 started in October.\n")
    from brain import validate
    from brain.vault import Vault
    problems = validate.closed_task_mentions(Vault(vault))
    assert len(problems) == 1 and "T-010" in problems[0] and "in progress" in problems[0]


def test_a_task_field_written_for_a_maintainer_is_named(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-07")
    write_tasks(vault,
                Task("T-020", "Prepare the Mac", "- **Next:** Maintainer: inspect the diagnostic in thread `1792692172.812869`.\n", {"state": "blocked"}),
                Task("T-021", "Book the hotel", "- **Next:** Ask both hotels about a garden room, then hold the better one.\n", {"state": "in_progress"}),
                Task("T-022", "Fix sync", "- **Blockers:** brain continue fails reading agent/Tasks.md\n", {"state": "blocked"}),
                Task("T-023", "Old", "- **Next:** Maintainer: done already.\n", {"state": "completed"}))
    from brain import validate
    from brain.vault import Vault
    problems = validate.owner_facing_tasks(Vault(vault))
    assert [p.split(":")[0] for p in problems] == ["T-020", "T-022"]
    assert "Next" in problems[0] and "Blockers" in problems[1]
