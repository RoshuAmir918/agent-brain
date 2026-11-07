import json
from datetime import date, datetime, timezone

import pytest

from brain import audit, cli, fm
from brain.config import Config
from conftest import harness_session, message, tool

DAY = date(2026, 10, 26)


@pytest.fixture
def audited(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nlog_dirs = ["Results"]\n', encoding="utf-8")
    vault = tmp_path / "memory"; (vault / "agent").mkdir(parents=True)
    fm.write(vault / "Harbor.md", {"kind": "hub", "tier": "hot", "activation": 3}, "# Harbor\nfour five six seven\n")
    (vault / "agent/Explorations").mkdir()
    fm.write(vault / "agent/Explorations/2026-10.md", {"kind": "log"}, "# Explorations\n\n- 10-20 | inbound | nothing new | nothing found\n- 10-21 | mail | filed one note | filed\n")
    (vault / "agent/Results").mkdir()
    fm.write(vault / "agent/Results/2026-10-20.md", {"kind": "log"}, "receipt one\n")
    fm.write(vault / "agent/Results/2026-10-21.md", {"kind": "log"}, "receipt two\n")
    (vault / "Conversations").mkdir()
    fm.write(vault / "Conversations/2026-10-20 Secret lunch with Alice.md", {"kind": "episode", "tier": "warm"}, "# Secret lunch\n" + "word " * 1600)
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    shards = home / "records/sessions/jsonl"; shards.mkdir(parents=True)
    started = datetime(2026, 10, 20, 5).timestamp()
    runs = []
    for sid, offset, duration, messages, calls, tokens_in, tokens_out in (("run1", 0, 600, 90, 60, 100000, 9000),
                                                                      ("run2", 86400, 300, 10, 4, 20000, 1000)):
        first = datetime.fromtimestamp(started + offset, timezone.utc).isoformat()
        last = datetime.fromtimestamp(started + offset + duration, timezone.utc).isoformat()
        record = harness_session(sid, [message("user", "run", first)] * messages + [tool("read_file", {}, last)] * calls,
                                 platform="cron", user_name="explore")
        record["startedAt"] = first
        record["usage"] = {"inputTokens": tokens_in, "outputTokens": tokens_out}
        runs.append(record)
    runs.append(harness_session("conv1", [], platform="slack"))
    for run in runs:
        (shards / f"{run['id']}.json").write_text(json.dumps(run) + "\n", encoding="utf-8")
    return Config.load(vault)


def test_audit_counts_folders_ledgers_episodes_and_routines_without_contents(audited):
    data = audit.report(audited, DAY, days=14)
    assert data["notes"]["log"]["count"] == 3 and data["notes"]["hub"]["count"] == 1
    results = next(f for f in data["folders"] if f["folder"] == "Results")
    assert results == {"folder": "Results", "files": 2, "dated_files": 2, "words": 4}
    assert data["ledgers"] == [{"ledger": "agent/Explorations/2026-10.md", "rows": 2, "mean_words_per_row": 10.0}]
    assert data["episodes"]["count"] == 1 and data["episodes"]["over_1500_words"] == 1
    assert data["context"]["active_notes"] == 1
    assert data["context"]["active_note_words"] == 6
    assert data["context"]["compiled_context_words"] == 0
    [job] = data["routines"]
    assert job["job"] == "explore" and job["runs"] == 2 and job["mean_tool_calls"] == 32.0 and job["mean_minutes"] == 7.5
    assert job["mean_messages"] == 50 and job["mean_input_tokens"] == 60000 and job["mean_output_tokens"] == 5000
    assert job["end_reasons"] == {"unknown": 2}
    text = audit.render(data)
    assert "explore: 2 runs" in text and "Results 2 files, 2 dated" in text
    assert "Secret" not in text and "Alice" not in text and "Harbor" not in text


def test_audit_cli_json_and_window(audited, capsys):
    cli.main(["audit", "--vault", str(audited.vault_path), "--date", "2026-10-26", "--days", "3", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["routines"] == [] and data["days"] == 3
    cli.main(["audit", "--vault", str(audited.vault_path), "--date", "2026-10-26"])
    assert capsys.readouterr().out.startswith("# Audit: Agent, 2026-10-26, last 14 days\n")


def test_audit_counts_no_active_notes_when_directory_is_disabled(audited, monkeypatch):
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "start")
    assert audit.report(audited, DAY)["context"] == {"active_notes": 0, "active_note_words": 0,
                                                   "compiled_context_words": 0}

