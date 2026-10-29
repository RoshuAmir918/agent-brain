import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date, datetime

import pytest

from brain import fm, health, needs, nightly, state, triggers
from brain.config import Config
from brain.tasks import Task, write_archive
from conftest import git, snapshot


TODAY = date(2026, 10, 28)
KEYS = ["schema", "engine", "version", "tasks", "hubs", "routines", "triggers", "ledger", "journal", "needsYou", "outputs"]


def note(root, source, body="", **data):
    path = root / source
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, body)
    return path


@pytest.fixture
def work(vault):
    config = Config.load(vault)
    registered = [
        Task("T-004", "Working café", "- **Status:** **Moving** [[Evidence|proof]]\n"
             "- **Next action:** **Ship** [[Draft]]\n- **Blockers:** **None**\n"
             "- **Hub:** [[Work/Watch#Scope|Watch]]\n", {"state": "in_progress", "status": "completed", "tier": "hot"}),
        Task("T-001", "Legacy completed", "- **Hub:** [[WATCH]]\n", {"status": "completed"}),
        Task("T-006", "Cancelled", "- **Hub:** [[Watch]]\n", {"state": "cancelled"}),
        Task("T-002", "Legacy unknown", "- **Next:** **Investigate**\n", {"status": "open"}),
    ]
    warm = Task("T-007", "Warm wait", "- **Status:** Warm detail\n- **Hub:** [[Watch]]\n"
                "- **Evidence:** 2026-10-26 owner asked.\n", {"state": "waiting_for_owner", "tier": "warm"})
    write_archive(config, warm, "warm", TODAY)
    note(vault, "agent/Tasks.md", "# Tasks\n\n" + "\n".join(task.render() for task in registered) +
         warm.render_line(config))
    write_archive(config, Task("T-005", "Archived wait", "- **Hub:** [[WORK/WATCH]]\n",
                              {"state": "waiting_for_owner", "tier": "cold"}), "cold", TODAY)
    write_archive(config, Task("T-004", "Stale duplicate", "- **Hub:** [[Watch]]\n",
                              {"state": "completed"}), "cold", TODAY)
    note(vault, "agent/Tasks Archive/T-003.md", "# T-003 — Hand-archived done\n"
         "<!-- brain: status=completed; tier=cold -->\n- **Status:** **Finished**\n- **Hub:** [[Work/Watch]]\n")
    note(vault, "agent/Tasks Archive/T-008.md", "## T-008 — Hand-archived cancelled\n"
         "<!-- brain: state=cancelled -->\n- **Hub:** [[Watch]]\n")
    note(vault, "agent/Tasks Archive/T-009.md", "# T-009 — Legacy archive\n- **Status:** Still open\n")
    note(vault, "Work/Watch.md", "# Watch\n## Open questions\n- Which [[Inbox]]?\n* Who owns delivery?\n"
         "## Improvements\n### 2026-10-27 — Better query\n- **Status:** awaiting `proposed` approval\n"
         "### October 26 — Adopt cache\n- **Status:** adopted yesterday\n"
         "### Unscheduled\n- **Status:** `rejected`\n### No status\nNothing yet.\n"
         "## History\n- Not an open question\n### Not an improvement\n- **Status:** `proposed`\n",
         kind="hub", skill="brain-watch", records="receipts")
    note(vault, "Maintenance.md", "# Maintenance\n", skill="brain-maintain", records="upkeep")
    note(vault, "Partial.md", "# Partial\n", skill="brain-partial")
    note(vault, "Topics/Other.md", "# Other\n")
    note(vault, "CURRENT.md", "# Current\n## Needs you\n"
         "- 2026-10-27 · confirm · Already sent.\n- 2026-10-28 · ask · Choose a route.\n")
    trigger_rows = [
        triggers.Trigger("TR-002", "intention", "When inbox changes", "Read it", "[[Watch]]", "active", "2026-10-27"),
        triggers.Trigger("TR-001", "commitment", "2026-10-20", "Call", "[[T-001]]", "fired 2026-10-20", "2026-10-01"),
    ]
    note(vault, "agent/Triggers.md", triggers.TEMPLATE + "\n".join(trigger.row() for trigger in trigger_rows))
    note(vault, "agent/Receipts/2026-10.md",
         "2026-10-28 12:30 | WATCH | progress | Café checked. | Ship.\n"
         "2026-10-28 12:30 | watch | done | Second tie. | Wait.\n"
         "2026-10-27 08:30–22:30 | watch | nothing | 14 runs, nothing new. | Preserve waits.\n"
         "2026-09-29 | watch | Legacy boundary. | Keep.\n"
         "2026-09-28 | watch | done | Too old. | Wait.\n"
         "2026-10-29 | futureRoutine | done | Future. | Wait.\n", kind="log")
    note(vault, "agent/OldWatch/2026-08.md", "2026-08-01 | Watch | Old spelling. | Wait.\n", kind="log")
    note(vault, "agent/Elsewhere/2026-08.md", "2026-08-01 | OldRoutine | Historical only. | Wait.\n", kind="log")
    note(vault, "agent/Only/2026-10.md", "2026-10-28 | lone | failed | No hub or skill. | Retry.\n", kind="log")
    note(vault, "agent/A/2026-10.md", "2026-10-28 12:30 | lone | blocked | Source tie. | Wait.\n", kind="log")
    note(vault, "agent/Fallback/2026-10.md", "2026-10-28 11:00 | fallback | waiting | No hub. | Wait.\n", kind="log")
    (vault / "hermes/skills/brain-fallback").mkdir(parents=True)
    note(vault, "Drafts/Café launch.md", kind="draft", created=TODAY)
    note(vault, "Drafts/Tie.md", kind="draft", created="2026-10-28")
    note(vault, "Drafts/Undated.md", kind="draft")
    note(vault, "Drafts/2026-01-01 Override.md", kind="reflection", created=datetime(2026, 10, 28, 14, 30))
    note(vault, "agent/Reflections/2026-10-27 Notes.md", kind="reflection")
    note(vault, "agent/Reflections/Implicit.md")
    return config


def test_tasks_use_engine_state_and_register_precedence(work):
    result = state.build(work, TODAY)["tasks"]
    assert [task["id"] for task in result] == ["T-004", "T-001", "T-006", "T-002", "T-007",
                                                "T-003", "T-005", "T-008", "T-009"]
    assert result[0] == {"id": "T-004", "title": "Working café", "state": "in_progress", "open": True,
                         "detail": "Moving [[Evidence|proof]]", "next": "Ship [[Draft]]", "blockers": "None",
                         "hub": "Work/Watch", "tier": "hot", "archived": False, "source": "agent/Tasks.md#T-004"}
    assert [(task["state"], task["open"]) for task in result] == [
        ("in_progress", True), ("completed", False), ("cancelled", False), (None, True),
        ("waiting_for_owner", True), ("completed", False), ("waiting_for_owner", True), ("cancelled", False), (None, True)]
    assert result[1]["detail"] is None
    assert result[1]["tier"] is None
    assert result[3]["next"] == "Investigate"
    assert result[4]["detail"] == "Warm detail"
    assert result[5]["detail"] == "Finished"
    assert all(task["archived"] for task in result[5:])
    assert result[5]["source"] == "agent/Tasks Archive/T-003.md#T-003"


def test_hubs_sections_and_progress(work):
    hubs = state.build(work, TODAY)["hubs"]
    assert [hub["source"] for hub in hubs] == sorted(hub["source"] for hub in hubs)
    watch = next(hub for hub in hubs if hub["title"] == "Watch")
    assert watch == {"title": "Watch", "source": "Work/Watch.md",
                     "responsibility": {"skill": "brain-watch", "records": "receipts"},
                     "openQuestions": ["Which [[Inbox]]?", "Who owns delivery?"],
                     "improvements": [
                         {"title": "Better query", "status": "proposed", "date": "2026-10-27",
                          "source": "Work/Watch.md#2026-10-27 — Better query"},
                         {"title": "Adopt cache", "status": "adopted", "date": "October 26",
                          "source": "Work/Watch.md#October 26 — Adopt cache"},
                         {"title": "Unscheduled", "status": "rejected", "date": None,
                          "source": "Work/Watch.md#Unscheduled"}], "progress": {"done": 2, "total": 5}}
    other = next(hub for hub in hubs if hub["title"] == "Other")
    assert other["responsibility"] is None
    assert other["openQuestions"] == other["improvements"] == []
    assert other["progress"] is None
    assert next(hub for hub in hubs if hub["title"] == "Partial")["responsibility"] is None


def test_routines_join_responsibilities_and_all_months_but_not_unknown_legacy_rows(work):
    assert state.build(work, TODAY)["routines"] == [
        {"name": "fallback", "skill": "brain-fallback", "hub": None, "ledger": "agent/Fallback"},
        {"name": "futureRoutine", "skill": None, "hub": None, "ledger": "agent/Receipts"},
        {"name": "lone", "skill": None, "hub": None, "ledger": "agent/A"},
        {"name": "upkeep", "skill": "brain-maintain", "hub": "Maintenance", "ledger": None},
        {"name": "WATCH", "skill": "brain-watch", "hub": "Watch", "ledger": "agent/Receipts"},
    ]


def test_triggers_preserve_file_order_and_point_to_register(work):
    assert state.build(work, TODAY)["triggers"] == [
        {"id": "TR-002", "class": "intention", "when": "When inbox changes", "do": "Read it", "status": "active",
         "created": "2026-10-27", "source": "agent/Triggers.md"},
        {"id": "TR-001", "class": "commitment", "when": "2026-10-20", "do": "Call", "status": "fired 2026-10-20",
         "created": "2026-10-01", "source": "agent/Triggers.md"},
    ]


def test_ledger_window_order_collapsed_rows_and_raw_ids(work):
    result = state.build(work, TODAY)["ledger"]
    assert [(row["at"], row["account"]) for row in result] == [
        ("2026-10-28T12:30", "Source tie."), ("2026-10-28T12:30", "Café checked."),
        ("2026-10-28T12:30", "Second tie."), ("2026-10-28T11:00", "No hub."),
        ("2026-10-28", "No hub or skill."), ("2026-10-27T08:30", "14 runs, nothing new."),
        ("2026-09-29", "Legacy boundary."),
    ]
    assert result[-1]["outcome"] is None
    raw = "2026-10-28 12:30 | WATCH | progress | Café checked. | Ship."
    assert result[1] == {"id": hashlib.sha256(("agent/Receipts/2026-10.md\n" + raw).encode()).hexdigest()[:12],
                         "at": "2026-10-28T12:30", "routine": "WATCH", "outcome": "progress",
                         "account": "Café checked.", "next": "Ship.", "source": "agent/Receipts/2026-10.md"}
    tomorrow = state.build(work, date(2026, 10, 29))["ledger"]
    assert tomorrow[0]["account"] == "Future."
    assert all(row["account"] != "Legacy boundary." for row in tomorrow)


def test_needs_you_passthrough(work):
    expected = [asdict(item) for item in needs.items(work, TODAY)]
    assert state.build(work, TODAY)["needsYou"] == expected
    assert [item["origin"] for item in expected] == ["current", "task", "task", "improvement", "current"]


def test_outputs_dates_kinds_and_ties(work):
    assert state.build(work, TODAY)["outputs"] == [
        {"title": "2026-01-01 Override", "kind": "reflection", "created": "2026-10-28T14:30:00",
         "source": "Drafts/2026-01-01 Override.md"},
        {"title": "Café launch", "kind": "draft", "created": "2026-10-28", "source": "Drafts/Café launch.md"},
        {"title": "Tie", "kind": "draft", "created": "2026-10-28", "source": "Drafts/Tie.md"},
        {"title": "2026-10-27 Notes", "kind": "reflection", "created": "2026-10-27",
         "source": "agent/Reflections/2026-10-27 Notes.md"},
        {"title": "Undated", "kind": "draft", "created": None, "source": "Drafts/Undated.md"},
    ]


@pytest.mark.parametrize("folder,data", [
    ("sources", {}), ("human", {}), ("people", {}), ("agents", {}), ("hermes", {}),
    ("Public", {"tier": "private"}), ("Public", {"kind": "conversation"}),
])
def test_privacy_excludes_notes_outputs_routines_and_owner_proposals(work, folder, data):
    for kind in ("hub", "draft", "reflection"):
        title = "EXCLUDED_" + kind
        note(work.vault_path, f"{folder}/Nested/{title}.md", "## Open questions\n- EXCLUDED_question\n"
             "## Improvements\n### 2026-10-28 — EXCLUDED_proposal\n- **Status:** `proposed`\n",
             **{"kind": kind, "skill": "EXCLUDED_skill", "records": "EXCLUDED_records", **data})
    current = work.vault_path / "CURRENT.md"
    with current.open("a") as stream:
        stream.write("- 2026-10-28 · ask · Who is this? · [[EXCLUDED_hub]]\n")
    snapshot = state.build(work, TODAY)
    assert "EXCLUDED" not in json.dumps(snapshot, ensure_ascii=False)
    # an ask about a private note still reaches the owner; only its source is withheld
    assert {"text": "Who is this?", "source": "CURRENT.md"}.items() <= next(
        item for item in snapshot["needsYou"] if item["text"] == "Who is this?").items()


def test_needs_you_normalizes_source_paths_without_changing_ids(work):
    note(work.vault_path, "CURRENT.md", "## Needs you\n"
         "- 2026-10-28 · ask · Choose? · [[memory/Work/Watch#Scope|Watch]]\n")
    expected = asdict(needs.items(work, TODAY)[0])
    assert expected["source"] == "memory/Work/Watch#Scope"
    expected["source"] = "Work/Watch.md#Scope"
    assert state.build(work, TODAY)["needsYou"][0] == expected


def test_deterministic_and_read_only(work):
    before = snapshot(work.vault_path.parent)
    metadata = {path: path.stat().st_mtime_ns for path in work.vault_path.rglob("*")}
    first = json.dumps(state.build(work, TODAY), ensure_ascii=False, indent=2)
    second = json.dumps(state.build(work, TODAY), ensure_ascii=False, indent=2)
    assert first.encode("utf-8") == second.encode("utf-8")
    assert "café" in first
    assert snapshot(work.vault_path.parent) == before
    assert {path: path.stat().st_mtime_ns for path in work.vault_path.rglob("*")} == metadata


def test_version_outside_git_and_installed_engine(work):
    result = state.build(work, TODAY)
    assert result["schema"] == 1
    assert result["version"] is None
    assert result["engine"] == health.installed_version("agent-brain")


def test_git_version_clean_dirty_and_no_index_writes(repo):
    root, _ = repo
    config = Config.load(root)
    head = git(root, "rev-parse", "--short", "HEAD")
    before = snapshot(root.parent)
    assert state.build(config, TODAY)["version"] == head
    assert snapshot(root.parent) == before
    (root / "CURRENT.md").write_text("Dirty worktree.\n")
    assert state.build(config, TODAY)["version"] == head + "+dirty"


def test_untracked_files_mark_git_dirty(repo):
    root, _ = repo
    (root / "untracked.txt").write_text("Untracked.\n")
    assert state.build(Config.load(root), TODAY)["version"].endswith("+dirty")


def test_cli_json_order_format_and_summary(work, capsys, monkeypatch):
    monkeypatch.setattr(Config, "today", lambda config: TODAY)
    nightly.main(["state", "--json", "--vault", str(work.vault_path)])
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert list(parsed) == KEYS + ["shareable"]
    assert output == json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    nightly.main(["state", "--vault", str(work.vault_path)])
    summary = capsys.readouterr().out
    assert summary.count("\n") == 1
    assert summary == " · ".join(f"{len(parsed[key])} {key}" for key in KEYS[3:]) + "\n"
    process = subprocess.run([sys.executable, "-m", "brain", "state", "--json", "--vault", str(work.vault_path)],
                             text=True, capture_output=True, check=True)
    assert list(json.loads(process.stdout)) == KEYS + ["shareable"]
    assert process.stderr == ""


def test_empty_vault_and_missing_installed_metadata(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir()

    def missing(package):
        raise health.PackageNotFoundError(package)

    monkeypatch.setattr(health, "installed_version", missing)
    result = state.build(Config.load(root), TODAY)
    assert list(result) == KEYS + ["shareable"]
    assert result["engine"] == "unknown"
    assert all(result[key] == [] for key in KEYS[3:])
    assert result["shareable"] == {kind: {"tell": {}, "hubs": [], "receipts": []} for kind in ("known", "anyone")}


def test_configured_layout_and_records_name_matching(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nagent_dir = "Worker"\nsources_dir = "imports"\n'
                                        'human_profile = "owner/about/Profile.md"\npeople_dir = "contacts"\n'
                                        'agents_dir = "bots"\nhermes_dir = "runtime"\nskill_prefix = "own-"\n')
    root = tmp_path / "memory"
    note(root, "Work.md", "## Improvements\n### October 28 — EXCLUDED\n- **Status:** `adopted`\n",
         skill="own-worker", records="RECEIPTS")
    note(root, "Worker/Receipts/2026-10.md", "2026-10-28 | receipts | done | Done. | Wait.\n", kind="log")
    note(root, "Worker/Tasks.md", Task("T-001", "Linked", "- **Hub:** [[Work]]\n", {"state": "completed"}).render())
    for folder in ("imports", "owner/about", "contacts", "bots", "runtime"):
        note(root, f"{folder}/EXCLUDED.md", "## Improvements\n### October 28 — EXCLUDED\n- **Status:** `proposed`\n",
             kind="draft")
    note(root, "imports-public/Visible.md")
    result = state.build(Config.load(root), TODAY)
    assert result["routines"] == [{"name": "receipts", "skill": "own-worker", "hub": "Work", "ledger": "Worker/Receipts"}]
    assert result["tasks"][0]["source"] == "Worker/Tasks.md#T-001"
    assert next(hub for hub in result["hubs"] if hub["title"] == "Work")["progress"] == {"done": 1, "total": 1}
    assert any(hub["title"] == "Visible" for hub in result["hubs"])
    assert not result["outputs"]
    assert not result["needsYou"]


def test_drafts_are_outputs_not_hubs_and_a_hub_link_to_a_private_note_is_withheld(work):
    root = work.vault_path
    note(root, "Work/Draft letter.md", "# Draft\n", kind="draft", created="2026-10-27")
    note(root, "people/Pat/Profile.md", "# Pat\n")
    note(root, "agent/Tasks.md", "# Tasks\n\n" + Task("T-009", "Write to Pat", "- **Hub:** [[people/Pat/Profile]]\n",
                                                     {"state": "ready"}).render())
    snapshot = state.build(work, TODAY)
    assert "Draft letter" not in [hub["title"] for hub in snapshot["hubs"]]
    assert "Draft letter" in [output["title"] for output in snapshot["outputs"]]
    assert next(task for task in snapshot["tasks"] if task["id"] == "T-009")["hub"] is None
    assert "Pat" not in json.dumps(snapshot["tasks"], ensure_ascii=False).replace("Write to Pat", "")
