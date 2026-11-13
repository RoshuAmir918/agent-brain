import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime

import pytest

from brain import cli, fm, health, ledger, needs, nightly, reception, responsibilities, state, validate
from brain.config import Config
from brain.tasks import Task, write_archive
from brain.vault import Vault
from conftest import git, snapshot


TODAY = date(2026, 10, 28)
KEYS = ["schema", "engine", "version", "tasks", "hubs", "goals", "routines", "ledger", "needsYou", "outputs", "prepared", "loops", "loopsClosed"]


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
    (vault / "Runtime/skills/brain-fallback").mkdir(parents=True)
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
                         "detail": "Moving [[Evidence|proof]]", "next": "Ship [[Draft]]", "handoff": None, "pace": "regular", "later": False, "blockers": "None",
                         "hub": "Work/Watch", "work": None, "after": None, "from": None, "delivers": None, "deliversProposed": False, "archived": False, "source": "agent/Tasks.md#T-004",
                         "ask": None, "why": None, "evidence": None, "resume": None, "report": None,
                         "lastChunk": None, "chunksToday": None, "closed": None, "outcome": None, "left": None, "due": None, "then": [], "view": None, "said": None, "owner": None, "goal": None}
    assert [(task["state"], task["open"]) for task in result] == [
        ("in_progress", True), ("completed", False), ("cancelled", False), (None, True),
        ("waiting_for_owner", True), ("completed", False), ("waiting_for_owner", True), ("cancelled", False), (None, True)]
    assert result[1]["detail"] is None
    assert result[3]["next"] == "Investigate"
    assert result[4]["detail"] == "Warm detail"
    assert result[5]["detail"] == "Finished"
    assert all(task["archived"] for task in result[5:])
    assert result[5]["source"] == "agent/Tasks Archive/T-003.md#T-003"


def test_hubs_sections_and_progress(work):
    hubs = state.build(work, TODAY)["hubs"]
    assert [hub["source"] for hub in hubs] == sorted(hub["source"] for hub in hubs)
    watch = next(hub for hub in hubs if hub["title"] == "Watch")
    assert watch == {"title": "Watch", "source": "Work/Watch.md", "work": None, "delivers": None,
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
        {"name": "fallback", "skill": "brain-fallback", "hub": None, "ledger": "agent/Fallback", "responsibility": None},
        {"name": "futureRoutine", "skill": None, "hub": None, "ledger": "agent/Receipts", "responsibility": None},
        {"name": "lone", "skill": None, "hub": None, "ledger": "agent/A", "responsibility": None},
        {"name": "upkeep", "skill": "brain-maintain", "hub": "Maintenance", "ledger": None, "responsibility": None},
        {"name": "WATCH", "skill": "brain-watch", "hub": "Watch", "ledger": "agent/Receipts", "responsibility": None},
    ]


@pytest.mark.parametrize("skill", [None, "brain-watch"])
def test_folder_responsibilities_export_fields_routines_and_records(vault, skill):
    config = Config.load(vault)
    source = "Responsibilities/chicken-alerts/RESPONSIBILITY.md"
    triggers = [{"kind": "schedule", "when": "every 2h", "needs": ["computer"]},
                {"kind": "mail", "filter": "chickens"}, {"kind": "schedule", "when": "0 8 * * *"}]
    note(vault, source, "Check the flock.\n", version=1, title="Flock check", skill=skill, asked_by="Owner, in Home",
         goal="G-1", role="R-1", delivers="Healthy chickens", work="flock", triggers=triggers)
    responsibilities.sync(Vault(vault))
    result = state.build(config, TODAY)
    assert result["responsibilities"] == [{
        "id": "chicken-alerts", "title": "Flock check", "hub": source, "skill": skill,
        "records": "Responsibilities/chicken-alerts/records", "routines": ["chicken-alerts", "chicken-alerts-2"],
        "needs": ["computer"], "work": "flock", "delivers": "Healthy chickens", "folder": True, "path": "memory/Responsibilities/chicken-alerts/RESPONSIBILITY.md",
        "triggers": triggers, "asked_by": "Owner, in Home", "goal": "G-1", "role": "R-1", "version": 1,
        "status": "active", "ended": None, "reason": None}]
    assert result["routines"] == [
        {"name": name, "skill": skill, "hub": "Flock check", "ledger": None, "responsibility": "chicken-alerts"}
        for name in ("chicken-alerts", "chicken-alerts-2")]
    records = note(vault, "Responsibilities/chicken-alerts/records/2026-10.md",
                   "2026-10-28 | CHICKEN-ALERTS-2 | done | Checked flock. | Wait.\n")
    assert ledger.discover(config) == [records]
    result = state.build(config, TODAY)
    second = next(routine for routine in result["routines"] if routine["name"] == "CHICKEN-ALERTS-2")
    assert second == {"name": "CHICKEN-ALERTS-2", "skill": skill, "hub": "Flock check",
                      "ledger": "Responsibilities/chicken-alerts/records", "responsibility": "chicken-alerts"}
    assert result["ledger"][0]["account"] == "Checked flock."
    assert not any(hub["title"] == "2026-10" for hub in result["hubs"])


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


def test_outputs_dates_kinds_and_ties(work):
    expected = [
        {"title": "2026-01-01 Override", "kind": "reflection", "created": "2026-10-28T14:30:00",
         "source": "Drafts/2026-01-01 Override.md"},
        {"title": "Café launch", "kind": "draft", "created": "2026-10-28", "source": "Drafts/Café launch.md"},
        {"title": "Tie", "kind": "draft", "created": "2026-10-28", "source": "Drafts/Tie.md"},
        {"title": "2026-10-27 Notes", "kind": "reflection", "created": "2026-10-27",
         "source": "agent/Reflections/2026-10-27 Notes.md"},
        {"title": "Undated", "kind": "draft", "created": None, "source": "Drafts/Undated.md"},
    ]
    for output in expected:
        output.update(id=hashlib.sha256(output["source"].encode("utf-8")).hexdigest()[:12], task=None)
    assert state.build(work, TODAY)["outputs"] == expected


@pytest.mark.parametrize("folder,data", [
    ("sources", {}), ("human", {}), ("people", {}), ("agents", {}), ("Runtime", {}),
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
    expected = asdict(next(item for item in needs.items(work, TODAY) if item.text == "Choose?"))
    assert expected["source"] == "memory/Work/Watch#Scope"
    expected["source"] = "Work/Watch.md#Scope"
    assert next(item for item in state.build(work, TODAY)["needsYou"] if item["text"] == "Choose?") == expected


def test_deterministic_and_read_only(work):
    before = snapshot(work.vault_path.parent)
    metadata = {path: path.stat().st_mtime_ns for path in work.vault_path.rglob("*")}
    first = json.dumps(state.build(work, TODAY), ensure_ascii=False, indent=2)
    second = json.dumps(state.build(work, TODAY), ensure_ascii=False, indent=2)
    assert first.encode("utf-8") == second.encode("utf-8")
    assert "café" in first
    assert snapshot(work.vault_path.parent) == before
    assert {path: path.stat().st_mtime_ns for path in work.vault_path.rglob("*")} == metadata


def test_git_version_clean_dirty_and_no_index_writes(repo):
    root, _ = repo
    config = Config.load(root)
    head = git(root, "rev-parse", "--short", "HEAD")
    before = snapshot(root.parent)
    assert state.build(config, TODAY)["version"] == head
    assert snapshot(root.parent) == before
    (root / "untracked.txt").write_text("Untracked.\n")
    assert state.build(config, TODAY)["version"] == head + "+dirty"
    (root / "untracked.txt").unlink()
    (root / "CURRENT.md").write_text("Dirty worktree.\n")
    assert state.build(config, TODAY)["version"] == head + "+dirty"


def test_cli_json_order_format_and_summary(work, capsys, monkeypatch):
    monkeypatch.setattr(Config, "today", lambda config: TODAY)
    cli.main(["state", "--json", "--vault", str(work.vault_path)])
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert list(parsed) == KEYS + ["idea", "ideas", "responsibilities", "portrait", "ownerPortrait", "learned", "seeds"]
    assert output == json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    cli.main(["state", "--vault", str(work.vault_path)])
    summary = capsys.readouterr().out
    assert summary.count("\n") == 1
    assert summary == " · ".join(f"{len(parsed[key])} {key}" for key in KEYS[3:]) + "\n"


def test_empty_vault_and_missing_installed_metadata(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir()

    def missing(package):
        raise health.PackageNotFoundError(package)

    monkeypatch.setattr(health, "installed_version", missing)
    result = state.build(Config.load(root), TODAY)
    assert list(result) == KEYS + ["idea", "ideas", "responsibilities", "portrait", "ownerPortrait", "learned", "seeds"]
    assert result["engine"] == "unknown" and result["version"] is None
    assert result["idea"] is None
    assert result["responsibilities"] == []
    assert all(result[key] == [] for key in KEYS[3:])


def test_configured_layout_and_records_name_matching(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nagent_dir = "Worker"\nsources_dir = "imports"\n'
                                        'human_profile = "owner/about/Profile.md"\npeople_dir = "contacts"\n'
                                        'agents_dir = "bots"\nruntime_dir = "runtime"\nskill_prefix = "own-"\n')
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
    assert result["routines"] == [{"name": "receipts", "skill": "own-worker", "hub": "Work", "ledger": "Worker/Receipts", "responsibility": None}]
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


def test_nightly_keeps_a_used_private_hub_private_and_out_of_the_export(vault):
    note(vault, "Secret.md", "# Secret\n## Open questions\n- PRIVATE_question\n",
         kind="hub", tier="private", skill="PRIVATE_skill", uses=[TODAY.isoformat()])
    nightly.run(vault, TODAY)
    assert fm.read(vault / "Secret.md")[0]["tier"] == "private"
    assert "PRIVATE" not in json.dumps(state.build(Config.load(vault), TODAY))


def test_nightly_leaves_malformed_frontmatter_alone_and_validate_names_it(vault):
    text = "---\ntier: private\nuses: [unclosed\n---\n# Broken\n## Open questions\n- MALFORMED_question\n"
    (vault / "Broken.md").write_text(text)
    nightly.run(vault, TODAY)
    assert (vault / "Broken.md").read_text() == text
    loaded = Vault(vault)
    assert "Broken.md" not in loaded.notes
    assert any(problem.startswith("Broken.md: frontmatter is not valid YAML") for problem in validate.hygiene(loaded))
    assert "MALFORMED" not in json.dumps(state.build(Config.load(vault), TODAY))


def test_one_malformed_archive_ledger_or_portrait_is_skipped_not_fatal(vault):
    config = Config.load(vault)
    broken = "---\nkind: [unclosed\n---\n"
    for rel in (config.agent("Tasks Archive/T-090.md"), config.agent("Notes/2026-10.md"), config.human_portrait):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(broken + "### T-090 — MALFORMED_task\n- **State:** ready\n")
    nightly.run(vault, TODAY)
    assert "MALFORMED" not in json.dumps(state.build(config, TODAY))
    assert {f"{rel}: frontmatter" for rel in (config.agent("Tasks Archive/T-090.md"), config.human_portrait)} <= {
        problem.split(" is not")[0] for problem in validate.hygiene(Vault(vault))}


def test_the_stamp_changes_with_what_the_state_reads_and_nothing_else(vault, capsys):
    config = Config.load(vault)

    def stamp():
        cli.main(["state", "--stamp", "--vault", str(vault)])
        return capsys.readouterr().out.strip()

    first = stamp()
    assert first == stamp() == state.stamp(config)
    (vault / ".context.md").write_text("compiled")
    assert stamp() == first
    note(vault, "Plans/Garden.md", "# Garden\n", kind="draft", tier="warm")
    second = stamp()
    assert second != first
    (vault / "Plans/Garden.md").rename(vault / "Plans/Gardens.md")
    assert stamp() not in (first, second)


def test_prepared_lists_held_unasked_drafts_with_their_offer(vault):
    config = Config.load(vault)
    note(vault, "agent/Prepared/2026-10-27 Renal deck.md", "Cards for renal.\n", kind="draft", goal="G-1")
    note(vault, "agent/Prepared/Lease summary.md", "Lease.\n", kind="draft", created=TODAY, **{"for": "the move"})
    note(vault, "agent/Prepared/Used.md", kind="draft")
    note(vault, "agent/Prepared/For a task.md", kind="draft", task="T-004")
    note(vault, "agent/Prepared/Never offered.md", kind="draft")
    note(vault, "Drafts/Elsewhere.md", kind="draft")
    note(vault, "agent/Prepared/Packing list.md", kind="draft", created="2026-10-26", hub="[[Lisbon]]")
    note(vault, "Trips/Lisbon.md", "# Lisbon\nThe [[Lease summary]] is ready.\n", kind="hub")
    note(vault, "Watch.md", "# Watch\n[[agent/Prepared/2026-10-27 Renal deck]]\n", kind="hub", skill="brain-watch", records="receipts")
    note(vault, "agent/Reception/2026-10.md", reception.HEADER +
         "- 2026-10-27 09:00 · aaaaaa · draft · Renal deck · pending · [[agent/Prepared/2026-10-27 Renal deck]]\n"
         "- 2026-10-27 10:00 · bbbbbb · draft · Renal deck again · opened · [[agent/Prepared/2026-10-27 Renal deck.md]]\n"
         "- 2026-10-28 09:00 · cccccc · draft · Lease · delivered · [[agent/Prepared/Lease summary]]\n"
         "- 2026-10-28 09:00 · dddddd · draft · Used · used · [[agent/Prepared/Used]]\n"
         "- 2026-10-28 09:00 · eeeeee · draft · Task · pending · [[agent/Prepared/For a task]]\n"
         "- 2026-10-28 09:00 · ffffff · draft · Elsewhere · pending · [[Drafts/Elsewhere]]\n"
         "- 2026-10-28 09:00 · 999999 · draft · Packing · pending · [[agent/Prepared/Packing list]]\n")
    assert state.build(config, TODAY)["prepared"] == [
        {"id": "cccccc", "title": "Lease summary", "source": "agent/Prepared/Lease summary.md", "created": "2026-10-28",
         "what": None, "why": {"kind": "reason", "text": "the move"}, "goal": None, "for": "the move", "outcome": "delivered", "hub": "Trips/Lisbon.md"},
        {"id": "bbbbbb", "title": "Renal deck", "source": "agent/Prepared/2026-10-27 Renal deck.md",
         "created": "2026-10-27", "what": None, "why": None, "goal": "G-1", "for": None, "outcome": "opened", "hub": None},  # a responsibility is no matter
        {"id": "999999", "title": "Packing list", "source": "agent/Prepared/Packing list.md", "created": "2026-10-26",
         "what": None, "why": None, "goal": None, "for": None, "outcome": "pending", "hub": "Trips/Lisbon.md"},  # named by its own hub: line
    ]
    reception.set_outcome(config, "agent/Prepared/2026-10-27 Renal deck", "used")
    assert [draft["id"] for draft in state.build(config, TODAY)["prepared"]] == ["cccccc", "999999"]


def test_a_prepared_draft_says_what_it_is(vault):
    config = Config.load(vault)
    note(vault, "agent/Prepared/2026-10-27 Meeting brief.md", kind="draft", index_line="Held background for the Nov 19 meeting", **{"for": "2026-11-19"})
    note(vault, "agent/Reception/2026-10.md", reception.HEADER + "- 2026-10-28 09:00 · aaaaaa · draft · Brief · pending · [[agent/Prepared/2026-10-27 Meeting brief]]\n")
    [draft] = state.build(config, TODAY)["prepared"]
    assert (draft["title"], draft["what"], draft["why"]) == ("Meeting brief", "Background for the Nov 19 meeting", {"kind": "date", "text": "2026-11-19"})


def test_validate_names_a_for_you_draft_that_does_not_read_on_its_own(vault):
    note(vault, "agent/Prepared/Brief.md", kind="draft", index_line="Background for the Nov 19 meeting")
    note(vault, "agent/Prepared/Long.md", kind="draft", index_line="A brief " + "with more " * 12, **{"for": "the move"})
    note(vault, "agent/Prepared/Coded.md", kind="draft", index_line="Notes for T-043")
    note(vault, "agent/Prepared/Blank.md", kind="draft")
    note(vault, "agent/Prepared/Used.md", kind="draft", index_line="Notes for T-044")
    note(vault, "agent/Reception/2026-10.md", reception.HEADER + "".join(
        f"- 2026-10-28 09:00 · {i * 6} · draft · {name} · {outcome} · [[agent/Prepared/{name}]]\n"
        for i, name, outcome in (("a", "Brief", "pending"), ("b", "Long", "pending"), ("c", "Coded", "delivered"),
                                 ("d", "Blank", "opened"), ("e", "Used", "used"))))
    problems = validate.prepared_hygiene(Vault(vault))
    assert [problem.split(":")[0] for problem in problems] == [
        "agent/Prepared/Blank.md · index_line", "agent/Prepared/Coded.md · index_line", "agent/Prepared/Long.md · index_line"]
    assert "internal id" in problems[1] and "at most 25" in problems[2]
    assert set(problems) <= set(validate.hygiene(Vault(vault)))


def test_why_a_draft_was_made_is_told_in_the_owners_terms():
    named, open_loops = {"G-1": "Pass Step 1"}, [{"id": "L-001", "text": "Book the Lisbon return fare"}]
    why = lambda goal=None, reason=None: state._why(goal, reason, named, open_loops)
    assert why("G-1", "2026-11-19") == {"kind": "goal", "text": "Pass Step 1"}
    assert why("G-9") is None
    assert why(reason='Payton, November 12 at 11:32: "we mean the housing connect" — demiurge-113127-x') == {"kind": "words", "text": "we mean the housing connect"}
    assert why(reason="loop:L-001") == {"kind": "loop", "text": "Book the Lisbon return fare"}
    assert why(reason="loop:L-404") is None
    assert why(reason="the move") == {"kind": "reason", "text": "the move"}


def test_task_ask_and_why_are_exported_from_the_open_linked_ask(vault):
    from brain import tasks
    config = Config.load(vault)
    task = tasks.find(config, "T-001")
    task.body += "- **Ask:** Stale body question?\n- **Why:** Stale body reason.\n"
    tasks.save_task(config, task, TODAY)
    exported, = state.build(config, TODAY)["tasks"]
    assert exported["ask"] is None and exported["why"] is None
    note(vault, "CURRENT.md", "## Needs you\n- 2026-10-28 · ask · Use the smaller venue? · [[Trip]]\n"
         "  - task: T-001\n  - why: The hall fits everyone.\n")
    exported, = state.build(config, TODAY)["tasks"]
    assert (exported["ask"], exported["why"]) == ("Use the smaller venue?", "The hall fits everyone.")
    needs.resolve(config, "Use the smaller venue?", "yes", TODAY)
    exported, = state.build(config, TODAY)["tasks"]
    assert exported["ask"] is None and exported["why"] is None
