import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from brain import attribute, digest, fm, nightly, session_tools
from brain.tasks import load_tasks
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

DAY = date(2026, 10, 20)
HARBOR = "memory/Harbor Context.md"
PROFILE = "memory/people/Alice_Example/Profile.md"


@pytest.fixture
def observed_sessions(vault, tmp_path):
    for relative in ("Harbor Context.md", "people/Alice_Example/Profile.md",
                     "Automatic Note.md", "On Demand Note.md", "Result Only.md"):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"tier": "hot", "pinned": relative == "Automatic Note.md",
                        "uses": [], "uses_inferred": []}, "# Fixture\n")
    (vault / "Hot Set.md").write_text(
        "# Hot set\n\n## Automatic load\n\n- [[memory/Automatic Note|Automatic]]\n"
        "\n## On demand (effective warm)\n\n- [[memory/On Demand Note|Overflow]]\n")
    directory = tmp_path / "exports"
    directory.mkdir()
    shutil.copyfile(Path(__file__).parent / "fixtures/all.jsonl", directory / "all.jsonl")
    (directory / "ignored.md").write_text("## assistant\nSources: T-999\n")
    return directory


def test_digest_appends_ordered_reads_and_retrieval_without_changing_text(vault, observed_sessions):
    text, words = digest.build(Config.load(vault), DAY, observed_sessions)
    assert words == 22
    assert ("CLI morning answer.\nreads: " + PROFILE + ", memory/Automatic Note.md, "
            + HARBOR + '\nretrieve: "harbor CI"') in text
    assert text.count("\nreads:") == 1 and text.count("\nretrieve:") == 1
    assert "Result Only.md" not in text


def test_jsonl_observed_rows_exclude_automatic_load_and_tool_results(vault, observed_sessions):
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
    path = observed_sessions / "all.jsonl"
    path.write_text(path.read_text().replace("cli-session", "renamed-session"))
    nightly.main(args)
    assert capsys.readouterr().out == "" and log.read_bytes() == before


def test_observed_deduplicates_across_sessions_but_not_dates(vault, observed_sessions):
    attribute.sessions(Vault(vault), observed_sessions, write=True)
    record = {"id": "another-session", "messages": [
        {"role": "assistant", "tool_name": "read_file", "arguments": {"path": HARBOR},
         "timestamp": "2026-10-20T23:00:00Z"},
        {"role": "assistant", "tool_name": "read_file", "arguments": {"path": HARBOR},
         "timestamp": "2026-10-21T08:00:00Z"},
    ]}
    with (observed_sessions / "all.jsonl").open("a") as stream:
        stream.write(json.dumps(record) + "\n")
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
    record = {"id": "excluded", "messages": [{"role": "assistant", "tool_name": "read_file",
              "arguments": {"path": "memory/" + relative}, "timestamp": "2026-10-20T12:00:00Z"}]}
    (observed_sessions / "all.jsonl").write_text(json.dumps(record) + "\n")
    assert attribute.sessions(Vault(vault), observed_sessions) == []


def test_hot_set_overflow_is_still_on_demand(vault, observed_sessions):
    record = {"id": "overflow", "messages": [{"role": "assistant", "tool_name": "search_files",
              "arguments": {"nested": {"path": "memory/On Demand Note.md"}},
              "timestamp": "2026-10-20T12:00:00Z"}]}
    (observed_sessions / "all.jsonl").write_text(json.dumps(record) + "\n")
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [(row.target, row.source) for row in rows] == [("On Demand Note.md", "observed")]


@pytest.mark.parametrize("call,reads,retrievals", [
    ({"tool_calls": [{"function": {"name": "read_file", "arguments": json.dumps({"path": PROFILE})}}]},
     [PROFILE], []),
    ({"tool_name": "read_file", "arguments": {"path": HARBOR}}, [HARBOR], []),
    ({"tool_name": "execute_code", "arguments": {HARBOR: {"path": PROFILE}}}, [HARBOR, PROFILE], []),
    ({"tool_name": "terminal", "arguments": 'cat "' + HARBOR + '"'}, [HARBOR], []),
    ({"tool_name": "terminal", "arguments": 'python3 -m brain retrieve --vault ../ "harbor CI"'},
     [], ["harbor CI"]),
    ({"tool_calls": [{"name": "search_files", "args": {"paths": [HARBOR, PROFILE, HARBOR]}}]},
     [HARBOR, PROFILE], []),
    ({"content": [{"type": "tool_use", "name": "execute_code", "input": {"code": 'open("' + HARBOR + '")'}}]},
     [HARBOR], []),
    ({"function_call": {"name": "mcp__vault__read", "parameters": {"nested": [json.dumps({"path": PROFILE})]}}},
     [PROFILE], []),
    ({"tool_name": "read_file", "arguments": '{"path": "' + HARBOR + '"'}, [HARBOR], []),
    ({"tool_name": "session_search", "arguments": "memory/missing-prefix " + PROFILE},
     [PROFILE], []),
    ({"tool_name": "terminal", "arguments": {"command": 'brain retrieve --json "' + HARBOR + '"', "path": PROFILE}},
     [], [HARBOR]),
])
def test_defensive_tool_shapes(call, reads, retrievals, template_config):
    assert session_tools.activity({"role": "assistant", **call}, template_config) == (reads, retrievals)


@pytest.mark.parametrize("role", ["tool", "function", "user", "system", "developer"])
def test_only_assistant_tool_calls_are_reads(role, template_config):
    message = {"role": role, "tool_name": "read_file", "arguments": {"path": HARBOR}}
    assert session_tools.activity(message, template_config) == ([], [])


@pytest.mark.parametrize("compacted", [True, 1, "true", "1"])
def test_compacted_tool_calls_are_not_observed(compacted, template_config):
    message = {"role": "assistant", "compacted": compacted,
               "tool_name": "read_file", "arguments": {"path": HARBOR}}
    assert session_tools.activity(message, template_config) == ([], [])


def test_digest_resets_reads_at_text_turns_and_keeps_each_retrieval(template_config):
    record = {"started_at": str(DAY), "messages": [
        {"role": "assistant", "tool_name": "read_file", "arguments": {"path": HARBOR}},
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "First"},
        {"role": "assistant", "tool_name": "terminal", "arguments": 'brain retrieve "harbor CI"'},
        {"role": "assistant", "tool_name": "terminal", "arguments": 'brain retrieve "people"'},
        {"role": "assistant", "content": "Second", "tool_name": "read_file", "arguments": {"path": PROFILE}},
        {"role": "assistant", "content": "Third"},
    ]}
    turns = digest.select_session(record, "fixture", DAY, template_config).turns
    assert [(turn.text, turn.reads, turn.retrievals) for turn in turns] == [
        ("Question", (), ()), ("First", (HARBOR,), ()),
        ("Second", (PROFILE,), ("harbor CI", "people")), ("Third", (), ())]


def test_empty_jsonl_takes_precedence_over_markdown_attribution(vault, observed_sessions):
    (observed_sessions / "all.jsonl").write_text("")
    assert attribute.sessions(Vault(vault), observed_sessions) == []


def test_jsonl_explicit_sources_keep_session_reason_and_orientation_exclusion(vault, observed_sessions):
    record = {"session_id": "citations", "messages": [
        {"role": "user", "content": "Sources: T-999", "timestamp": "2026-10-20T12:00:00Z"},
        {"role": "assistant", "content": "Sources: [[START]] [[Harbor Context]] T-001 TR-002; no record found"},
        {"role": "tool", "content": "Sources: T-998"},
    ]}
    (observed_sessions / "all.jsonl").write_text(json.dumps(record) + "\n")
    rows = attribute.sessions(Vault(vault), observed_sessions)
    assert [row.target for row in rows] == ["Harbor Context.md", "T-001", "TR-002", "MISS"]
    assert all(row.ts == "2026-10-20T05:00:00-07:00" and row.source == "explicit"
               and row.reason == "cited in session citations" for row in rows)


@pytest.mark.parametrize("bounds", [{"started_at": "2026-10-20T12:00:00Z"}, {}])
def test_jsonl_observed_timestamp_fallbacks(vault, observed_sessions, bounds):
    record = {**bounds, "id": "undated", "messages": [
        {"role": "assistant", "tool_name": "read_file", "arguments": {"path": HARBOR}}]}
    path = observed_sessions / "all.jsonl"
    path.write_text(json.dumps(record) + "\n")
    row, = attribute.sessions(Vault(vault), observed_sessions)
    expected = "2026-10-20T05:00:00-07:00" if bounds else attribute.fallback_date(path)
    assert row.ts == expected


def test_malformed_jsonl_attribution_never_partially_writes(vault, observed_sessions):
    with (observed_sessions / "all.jsonl").open("a") as stream:
        stream.write("invalid JSON\n")
    before = snapshot(vault.parent)
    with pytest.raises(ValueError, match="all.jsonl:3: invalid session JSON"):
        attribute.sessions(Vault(vault), observed_sessions, write=True)
    assert snapshot(vault.parent) == before


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


def test_jsonl_nightly_attributes_before_fold_and_is_idempotent(vault, observed_sessions):
    nightly.run(vault, DAY, observed_sessions)
    assert fm.read(vault / "Harbor Context.md")[0]["uses_inferred"] == [str(DAY)]
    assert fm.read(vault / "Automatic Note.md")[0]["uses_inferred"] == []
    assert load_tasks(Config.load(vault))[0][0].uses == [str(DAY), "2026-09-20"]
    before = snapshot(vault.parent)
    nightly.run(vault, DAY, observed_sessions)
    after = snapshot(vault.parent)
    assert {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)} == {
        "memory/agent/Sleep Log/2026-10.md"}


def test_session_kind_classifies_conversation_routine_and_evaluation():
    from brain import session_tools
    user = [{"role": "user", "content": "hello"}]
    assert session_tools.session_kind([{"title": "chat with kevin", "source": "slack"}], user) == "conversation"
    assert session_tools.session_kind([{"id": "cron_abc_20261021", "source": "cron"}], user) == "routine"
    assert session_tools.session_kind([{"title": "Evaluation fixture: Q09", "source": "slack"}], user) == "evaluation"
    assert session_tools.session_kind([{"title": "x"}], [{"role": "user", "content": "Evaluation fixture: what is the capital"}]) == "evaluation"
    assert session_tools.session_kind([{"title": "retrieval-test RC-04"}], user) == "evaluation"


def test_evaluation_kind_matches_retrieval_test_with_a_space_like_the_markdown_filename():
    from brain import session_tools
    user = [{"role": "user", "content": "answer only"}]
    assert session_tools.session_kind([{"title": "Subagent: read-only memory retrieval test. Answer only"}], user) == "evaluation"
