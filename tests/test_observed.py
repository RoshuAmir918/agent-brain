import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from brain import attribute, digest, fm, nightly, session_tools
from brain.tasks import load_tasks
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot, harness_session, message, tool, write_shards

DAY = date(2026, 10, 20)
HARBOR = "memory/Harbor Context.md"
PROFILE = "memory/people/Alice_Example/Profile.md"


@pytest.fixture
def observed_sessions(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOT_WORDS", "0")
    for relative in ("Harbor Context.md", "people/Alice_Example/Profile.md", "Automatic Note.md", "On Demand Note.md", "Result Only.md"):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"tier": "hot", "pinned": relative == "Automatic Note.md", "uses": [], "uses_inferred": []}, "# Fixture\n")
    directory = tmp_path / "exports"
    shutil.copytree(Path(__file__).parent / "fixtures/sessions", directory / "jsonl")
    (directory / "ignored.md").write_text("## assistant\nSources: T-999\n")
    return directory


def replace_shards(directory, *records):
    shutil.rmtree(directory / "jsonl")
    write_shards(directory, *records)


def test_digest_appends_ordered_reads_and_retrieval_without_changing_text(vault, observed_sessions):
    text, words = digest.build(Config.load(vault), DAY, observed_sessions)
    assert words == 22
    assert ("CLI morning answer.\nreads: " + PROFILE + ", memory/Automatic Note.md, "
            + HARBOR + '\nretrieve: "harbor CI"') in text
    assert text.count("\nreads:") == 1 and text.count("\nretrieve:") == 1
    assert "Result Only.md" not in text


def test_harness_observed_rows_exclude_automatic_load_and_tool_results(vault, observed_sessions):
    before = snapshot(vault.parent)
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [row.line() for row in rows if row.source == "observed"] == [
        "2026-10-20T00:01:00-07:00 | people/Alice_Example/Profile.md | "
        "opened on demand in session cli-session | observed",
        "2026-10-20T00:01:00-07:00 | Harbor Context.md | "
        "opened on demand in session cli-session | observed",
    ]
    assert [(row.target, row.source) for row in rows if row.source != "observed"] == [("T-001", "explicit")]
    assert snapshot(vault.parent) == before


def test_observed_cli_write_is_idempotent_even_after_session_id_changes(vault, observed_sessions, capsys):
    args = ["attribute", "--vault", str(vault), "--sessions-dir", str(observed_sessions), "--write"]
    nightly.main(args)
    assert len(capsys.readouterr().out.splitlines()) == 3
    log = vault / "agent/Use Log/2026-10.md"
    before = log.read_bytes()
    nightly.main(args)
    assert capsys.readouterr().out == "" and log.read_bytes() == before
    path = observed_sessions / "jsonl/cli-session.json"
    path.write_text(path.read_text().replace("cli-session", "renamed-session"))
    nightly.main(args)
    assert capsys.readouterr().out == "" and log.read_bytes() == before


def test_observed_deduplicates_across_sessions_but_not_dates(vault, observed_sessions):
    attribute.sessions(Vault(vault), observed_sessions, write=True)
    record = harness_session("another-session", [
        tool("read_file", {"path": HARBOR}, "2026-10-20T23:00:00Z"),
        tool("read_file", {"path": HARBOR}, "2026-10-21T08:00:00Z")])
    write_shards(observed_sessions, record)
    rows = attribute.sessions(Vault(vault), observed_sessions, write=True)
    assert [row.line() for row in rows] == [
        "2026-10-21T01:00:00-07:00 | Harbor Context.md | "
        "opened on demand in session another-session | observed"]
    assert attribute.sessions(Vault(vault), observed_sessions) == []


@pytest.mark.parametrize("source", ["explicit", "inferred", "live"])
def test_observed_deduplication_is_source_specific(vault, observed_sessions, source):
    attribute.append_log(vault / "agent/Use Log/2026-10.md", [attribute.Use(
        "2026-10-20T00:00:00-07:00", "Harbor Context.md", "opened on demand in session cli-session", source)])
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert len([row for row in rows if row.target == "Harbor Context.md" and row.source == "observed"]) == 1


@pytest.mark.parametrize("relative", sorted(Config(Path('.')).orientation) + [
    "Conversations/2026-10-19.md", "agent/Dreams/2026-10-19.md",
    "agent/Explorations/Note.md", "agent/Use Log/2026-10.md",
    "agent/Sleep Log/2026-10.md", "agent/Tasks Archive/T-001.md",
])
def test_observed_never_attributes_excluded_paths(vault, observed_sessions, relative):
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Excluded\n")
    record = harness_session("excluded", [tool("read_file", {"path": "memory/" + relative}, "2026-10-20T12:00:00Z")])
    replace_shards(observed_sessions, record)
    assert attribute.sessions(Vault(vault), observed_sessions) == []


def test_directory_active_overflow_is_still_on_demand(vault, observed_sessions):
    record = harness_session("overflow", [tool("search_files", {"nested": {"path": "memory/On Demand Note.md"}})])
    replace_shards(observed_sessions, record)
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [(row.target, row.source) for row in rows] == [("On Demand Note.md", "observed")]


@pytest.mark.parametrize("sections", ["directory", "hot", ""])
def test_observed_reads_follow_enabled_directory_selection(vault, observed_sessions, monkeypatch, sections):
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", sections)
    record = harness_session("active", [tool("read_file", {"path": "memory/Automatic Note.md"})])
    replace_shards(observed_sessions, record)
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [(row.target, row.source) for row in rows] == ([] if sections else [("Automatic Note.md", "observed")])


@pytest.mark.parametrize("name,arguments,reads,retrievals", [
    ("read_file", {"path": PROFILE}, [PROFILE], []),
    ("read_file", {"path": HARBOR}, [HARBOR], []),
    ("execute_code", {HARBOR: {"path": PROFILE}}, [HARBOR, PROFILE], []),
    ("terminal", 'cat "' + HARBOR + '"', [HARBOR], []),
    ("terminal", 'python3 -m brain retrieve --vault ../ "harbor CI"', [], ["harbor CI"]),
    ("search_files", {"paths": [HARBOR, PROFILE, HARBOR]}, [HARBOR, PROFILE], []),
    ("mcp__vault__read", {"nested": [json.dumps({"path": PROFILE})]}, [PROFILE], []),
    ("exec_command", {"cmd": 'brain retrieve --json "' + HARBOR + '"', "path": PROFILE}, [], [HARBOR]),
])
def test_harness_tool_arguments(name, arguments, reads, retrievals, template_config):
    assert session_tools.activity(tool(name, arguments)["item"], template_config) == (reads, retrievals)


@pytest.mark.parametrize("kind", ["message", "tool_result", "reasoning"])
def test_only_tool_call_entries_are_reads(kind, template_config):
    assert session_tools.activity({"type": kind, "text": HARBOR}, template_config) == ([], [])


def test_digest_resets_reads_at_text_turns_and_keeps_each_retrieval(template_config):
    record = harness_session("fixture", [
        tool("read_file", {"path": HARBOR}), message("user", "Question"), message("assistant", "First"),
        tool("terminal", 'brain retrieve "harbor CI"'), tool("terminal", 'brain retrieve "people"'),
        tool("read_file", {"path": PROFILE}), message("assistant", "Second"), message("assistant", "Third")])
    turns = digest.select_session(record, "fixture", DAY, template_config).turns
    assert [(turn.text, turn.reads, turn.retrievals) for turn in turns] == [
        ("Question", (), ()), ("First", (HARBOR,), ()), ("Second", (PROFILE,), ("harbor CI", "people")), ("Third", (), ())]


def test_explicit_sources_keep_session_reason_and_orientation_exclusion(vault, observed_sessions):
    stamp = "2026-10-20T12:00:00Z"
    record = harness_session("citations", [message("user", "Sources: T-999", stamp),
        message("assistant", "Sources: [[START]] [[Harbor Context]] T-001 TR-002; no record found", stamp),
        {"at": stamp, "item": {"type": "tool_result", "callId": "call", "output": "Sources: T-998"}}])
    replace_shards(observed_sessions, record)
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [row.target for row in rows] == ["Harbor Context.md", "T-001", "TR-002", "MISS"]
    assert all(row.ts == "2026-10-20T05:00:00-07:00" and row.source == "explicit"
               and row.reason == "cited in session citations" for row in rows)


def test_malformed_shard_attribution_never_partially_writes(vault, observed_sessions):
    (observed_sessions / "jsonl/z-invalid.json").write_text("invalid JSON")
    before = snapshot(vault.parent)
    with pytest.raises(ValueError, match="z-invalid.json: invalid session JSON"):
        attribute.sessions(Vault(vault), observed_sessions, write=True)
    assert snapshot(vault.parent) == before


@pytest.mark.parametrize("platform,first,sid,expected", [
    ("slack", "hello", "conversation", "conversation"),
    ("cron", "run sleep1", "routine", "routine"),
    ("cli", "hello", "cli", "conversation"),
    ("slack", "Evaluation fixture: Q09", "check", "evaluation"),
    ("slack", "answer only", "retrieval-test-RC-04", "evaluation"),
])
def test_session_kind(platform, first, sid, expected):
    assert session_tools.session_kind(harness_session(sid, [message("user", first)], platform=platform)) == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_fold_explicit_beats_observed_and_observed_never_credits_tasks(vault, reverse):
    fm.write(vault / "Hub.md", {"uses": [], "uses_inferred": []}, "# Hub\n")
    rows = [attribute.Use("2026-10-04", "Hub.md", "opened", "observed"),
            attribute.Use("2026-10-04", "Hub.md", "cited", "explicit"),
            attribute.Use("2026-10-03", "Hub.md", "opened", "observed"),
            attribute.Use("2026-10-01", "Hub.md", "inferred", "inferred"),
            attribute.Use("2026-10-04", "T-001", "opened", "observed")]
    attribute.append_log(vault / "agent/Use Log/2026-10.md", rows[::-1] if reverse else rows)
    loaded = Vault(vault)
    tasks = load_tasks(Config.load(vault))[0]
    original_uses = list(tasks[0].uses)
    assert nightly.fold_uses(loaded, tasks, DAY, []) == {"Hub.md"}
    assert loaded.notes["Hub.md"].data == {"uses": ["2026-10-04"],
                                         "uses_inferred": ["2026-10-03", "2026-10-01"]}
    assert tasks[0].uses == original_uses and tasks[0].uses_inferred == []
    assert nightly.fold_uses(loaded, tasks, DAY, []) == set()


def test_explicit_row_removes_previously_folded_observed_credit(vault):
    fm.write(vault / "Hub.md", {"uses": [], "uses_inferred": []}, "# Hub\n")
    log = vault / "agent/Use Log/2026-10.md"
    attribute.append_log(log, [attribute.Use("2026-10-04", "Hub.md", "opened", "observed")])
    loaded = Vault(vault)
    nightly.fold_uses(loaded, [], DAY, [])
    assert loaded.notes["Hub.md"].data["uses_inferred"] == ["2026-10-04"]
    loaded.notes["Hub.md"].save()
    attribute.append_log(log, [attribute.Use("2026-10-04", "Hub.md", "cited", "explicit")])
    loaded = Vault(vault)
    assert nightly.fold_uses(loaded, [], DAY, []) == {"Hub.md"}
    assert loaded.notes["Hub.md"].data == {"uses": ["2026-10-04"], "uses_inferred": []}
    assert nightly.fold_uses(loaded, [], DAY, []) == set()


@pytest.mark.parametrize("sources", [
    ["explicit", "inferred", "observed"], ["observed", "explicit", "inferred"],
    ["inferred", "observed", "explicit"],
])
def test_independent_inferred_credit_keeps_existing_semantics(vault, sources):
    fm.write(vault / "Hub.md", {"uses": [], "uses_inferred": []}, "# Hub\n")
    attribute.append_log(vault / "agent/Use Log/2026-10.md", [
        attribute.Use("2026-10-04", "Hub.md", source, source) for source in sources])
    loaded = Vault(vault)
    nightly.fold_uses(loaded, [], DAY, [])
    assert loaded.notes["Hub.md"].data == {"uses": ["2026-10-04"], "uses_inferred": ["2026-10-04"]}
    assert nightly.fold_uses(loaded, [], DAY, []) == set()


@pytest.mark.parametrize("source", ["observed", "inferred"])
def test_half_weight_dates_keep_existing_semantics_without_explicit_rows(vault, source):
    fm.write(vault / "Hub.md", {"uses": ["2026-10-04"], "uses_inferred": []}, "# Hub\n")
    attribute.append_log(vault / "agent/Use Log/2026-10.md", [
        attribute.Use("2026-10-04", "Hub.md", "fixture", source)])
    loaded = Vault(vault)
    nightly.fold_uses(loaded, [], DAY, [])
    assert loaded.notes["Hub.md"].data == {"uses": ["2026-10-04"], "uses_inferred": ["2026-10-04"]}


def test_harness_nightly_attributes_before_fold_and_is_idempotent(vault, observed_sessions):
    nightly.run(vault, DAY, observed_sessions)
    assert fm.read(vault / "Harbor Context.md")[0]["uses_inferred"] == [str(DAY)]
    assert fm.read(vault / "Automatic Note.md")[0]["uses_inferred"] == []
    assert load_tasks(Config.load(vault))[0][0].uses == [str(DAY), "2026-09-20"]
    before = snapshot(vault.parent)
    nightly.run(vault, DAY, observed_sessions)
    after = snapshot(vault.parent)
    assert {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)} == {
        "memory/agent/Sleep Log/2026-10.md"}
