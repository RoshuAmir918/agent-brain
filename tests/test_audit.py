import json
from datetime import date, datetime

import pytest

from brain import audit, fm, nightly
from brain.config import Config

DAY = date(2026, 10, 26)


@pytest.fixture
def audited(tmp_path, monkeypatch):
    (tmp_path / "brain.toml").write_text('[brain]\nlog_dirs = ["Results"]\n', encoding="utf-8")
    vault = tmp_path / "memory"; (vault / "agent").mkdir(parents=True)
    fm.write(vault / "Hot Set.md", {}, "# Hot set\n## Automatic load\none two three\n")
    fm.write(vault / "Warm Index.md", {}, "# Warm\n- a\n- b\n")
    fm.write(vault / "Harbor.md", {"kind": "hub", "tier": "warm"}, "# Harbor\nfour five six seven\n")
    (vault / "agent/Explorations").mkdir()
    fm.write(vault / "agent/Explorations/2026-10.md", {"kind": "log"}, "# Explorations\n\n- 10-20 | inbound | nothing new | nothing found\n- 10-21 | mail | filed one note | filed\n")
    (vault / "agent/Results").mkdir()
    fm.write(vault / "agent/Results/2026-10-20.md", {"kind": "log"}, "receipt one\n")
    fm.write(vault / "agent/Results/2026-10-21.md", {"kind": "log"}, "receipt two\n")
    (vault / "Conversations").mkdir()
    fm.write(vault / "Conversations/2026-10-20 Secret lunch with Alice.md", {"kind": "episode", "tier": "warm"}, "# Secret lunch\n" + "word " * 1600)
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    shards = home / "records/sessions/jsonl"; shards.mkdir(parents=True)
    started = datetime(2026, 10, 20, 5).timestamp()
    runs = [
        {"id": "cron_abc_20261024_050000", "source": "cron", "title": "explore · Oct 24 05:00", "started_at": started, "ended_at": started + 600,
         "message_count": 90, "tool_call_count": 60, "input_tokens": 100000, "output_tokens": 9000, "end_reason": "cron_complete", "messages": []},
        {"id": "cron_abc_20261025_050000", "source": "cron", "title": "explore · Oct 25 05:00", "started_at": started + 86400, "ended_at": started + 86400 + 300,
         "message_count": 10, "tool_call_count": 4, "input_tokens": 20000, "output_tokens": 1000, "end_reason": "cron_complete", "messages": []},
        {"id": "conv1", "source": "slack", "title": "chat", "started_at": started, "message_count": 3, "messages": []},
    ]
    for run in runs:
        (shards / f"{run['id']}.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")
    return Config.load(vault)


def test_audit_counts_folders_ledgers_episodes_and_routines_without_contents(audited):
    data = audit.report(audited, DAY, days=14)
    assert data["notes"]["log"]["count"] == 3 and data["notes"]["hub"]["count"] == 1
    results = next(f for f in data["folders"] if f["folder"] == "Results")
    assert results == {"folder": "Results", "files": 2, "dated_files": 2, "words": 4}
    assert data["ledgers"] == [{"ledger": "agent/Explorations/2026-10.md", "rows": 2, "mean_words_per_row": 10.0}]
    assert data["episodes"]["count"] == 1 and data["episodes"]["over_1500_words"] == 1
    assert data["context"]["hot_set_words"] == 9 and data["context"]["warm_index_entries"] == 2
    [job] = data["routines"]
    assert job["job"] == "explore" and job["runs"] == 2 and job["mean_tool_calls"] == 32.0 and job["mean_minutes"] == 7.5
    assert job["end_reasons"] == {"cron_complete": 2}
    text = audit.render(data)
    assert "explore: 2 runs" in text and "Results 2 files, 2 dated" in text
    assert "Secret" not in text and "Alice" not in text and "Harbor" not in text


def test_audit_cli_json_and_window(audited, capsys):
    nightly.main(["audit", "--vault", str(audited.vault_path), "--date", "2026-10-26", "--days", "3", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["routines"] == [] and data["days"] == 3
    nightly.main(["audit", "--vault", str(audited.vault_path), "--date", "2026-10-26"])
    assert capsys.readouterr().out.startswith("# Audit: Agent, 2026-10-26, last 14 days\n")

