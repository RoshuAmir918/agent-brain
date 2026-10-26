import os
from datetime import datetime

import pytest

from brain.config import Config
from brain import attribute, fm, nightly
from brain.tasks import load_tasks
from brain.vault import Vault
from conftest import nightly_argv, snapshot


@pytest.fixture
def exported_sessions(vault, tmp_path):
    fm.write(vault / "Harbor Context.md", {"uses": ["2026-01-01"], "tier": "cold"},
             "# Harbor\nDeployment context.\n")
    profile = vault / "people/Alice_Example/Profile.md"
    profile.parent.mkdir(parents=True)
    fm.write(profile, {"tier": "warm"}, "# Contact\nFixture contact.\n")
    sessions = tmp_path / "exports"
    nested = sessions / "nested"
    nested.mkdir(parents=True)
    (nested / "marked.md").write_text(
        "Sources: T-998\n## user\n2026-10-04T12:00:00Z\nSources: T-999\n"
        "## assistant\n2026-10-05T01:02:03Z\n"
        "Sources: [[Harbor Context]] T-001 TR-002 memory/people/Alice_Example/Profile.md\n"
        "**Assistant**\n2026-10-05T02:03:04Z\nSources: [[Harbor Context]] T-001\n"
        "assistant:\n2026-10-05T03:04:05-07:00\nSources: T-001\n"
        "role: user\nSources: T-997\n"
        "role: assistant\n2026-11-01T04:05:06+00:00\nSources: T-001\n")
    (sessions / "session_2026-10-06.md").write_text(
        "# Export without role markers\nAn answer.\n"
        "Sources: [[Harbor Context]] TR-002\nSources: [[Harbor Context]]\n")
    (sessions / "manifest.jsonl").write_text('{"path":"nested/marked.md","sha256":"fixture"}\n')
    return sessions


def test_two_session_exports_produce_expected_rows_without_writing(vault, exported_sessions):
    before = snapshot(vault)
    rows = attribute.sessions(Vault(vault), exported_sessions)
    assert {(row.ts, row.target, row.reason, row.source) for row in rows} == {
        ("2026-10-05T01:02:03Z", "Harbor Context.md", "cited in session marked", "explicit"),
        ("2026-10-05T01:02:03Z", "people/Alice_Example/Profile.md", "cited in session marked", "explicit"),
        ("2026-10-05T01:02:03Z", "T-001", "cited in session marked", "explicit"),
        ("2026-10-05T01:02:03Z", "TR-002", "cited in session marked", "explicit"),
        ("2026-11-01T04:05:06+00:00", "T-001", "cited in session marked", "explicit"),
        ("2026-10-06", "Harbor Context.md", "cited in session session_2026-10-06", "explicit"),
        ("2026-10-06", "TR-002", "cited in session session_2026-10-06", "explicit"),
    }
    assert len(rows) == 7
    assert snapshot(vault) == before


def test_sessions_cli_writes_monthly_logs_and_rerun_adds_no_duplicates(vault, exported_sessions, capsys):
    args = ["attribute", "--vault", str(vault), "--sessions-dir", str(exported_sessions), "--write"]
    nightly.main(args)
    printed = capsys.readouterr().out
    assert len(printed.splitlines()) == 7 and "cited in session marked | explicit" in printed
    october = vault / "agent/Use Log/2026-10.md"
    november = vault / "agent/Use Log/2026-11.md"
    assert len(attribute.read_log(october)) == 6
    assert len(attribute.read_log(november)) == 1
    before = snapshot(vault)
    nightly.main(args)
    assert capsys.readouterr().out == ""
    assert snapshot(vault) == before


def test_existing_log_deduplication_uses_date_target_and_reason(vault, exported_sessions):
    log = vault / "agent/Use Log/2026-10.md"
    attribute.append_log(log, [attribute.Use("2026-10-05T00:00:00Z", "T-001", "cited in session marked", "inferred")])
    rows = attribute.sessions(Vault(vault), exported_sessions)
    assert len(rows) == 6
    assert not any(row.target == "T-001" and row.ts.startswith("2026-10-05") for row in rows)


@pytest.mark.parametrize("assistant,user", [
    ("## assistant", "## user"), ("**ASSISTANT**", "**USER**"),
    ("assistant:", "user:"), ("### Assistant", "### User"),
    ("role: assistant", "role: user"), ("- role: 'assistant'", "- role: 'user'"),
])
def test_standard_role_markers_gate_sources(vault, tmp_path, assistant, user):
    path = tmp_path / "2026-10-05_export.md"
    path.write_text(f"Sources: T-998\n{assistant}\nSources: T-001\n{user}\nSources: T-999\n")
    rows = attribute.session_rows(Vault(vault), path)
    assert [row.target for row in rows] == ["T-001"]
    assert rows[0].ts == "2026-10-05"


def test_nearest_valid_iso_timestamp_precedes_each_source_line(vault, tmp_path):
    path = tmp_path / "session.md"
    path.write_text("## Assistant\nmetadata: 2026-10-05T12:00Z\n"
                    "Sources: T-001 at 2030-01-01T00:00:00Z\n"
                    "metadata: 2026-02-30T12:00:00Z\n"
                    "metadata: 2026-10-06 13:14:15.123-0700\nSources: TR-002\n")
    rows = attribute.session_rows(Vault(vault), path)
    assert [row.ts for row in rows] == ["2026-10-05T12:00Z", "2026-10-06 13:14:15.123-0700"]


@pytest.mark.parametrize("location,expected", [
    ("2026-10-08/nested/session.md", "2026-10-08"),
    ("2026-10-08/2026-10-09/session_2026-10-10.md", "2026-10-10"),
    ("2026-10-08/session_2026-02-30.md", "2026-10-08"),
    ("undated/session.md", "2026-10-07"),
])
def test_session_date_falls_back_to_filename_parent_then_mtime(vault, tmp_path, location, expected):
    path = tmp_path / "exports" / location
    path.parent.mkdir(parents=True)
    path.write_text("Sources: T-001\n")
    timestamp = datetime(2026, 10, 7, 12).timestamp()
    os.utime(path, (timestamp, timestamp))
    assert attribute.session_rows(Vault(vault), path)[0].ts == expected


def test_nightly_attributes_before_folding_and_compiling(vault, exported_sessions):
    args = nightly_argv(vault, "2026-10-06", "--sessions-dir", str(exported_sessions), "--write")
    nightly.main(args)
    data, _ = fm.read(vault / "Harbor Context.md")
    assert data["uses"] == ["2026-10-06", "2026-10-05", "2026-01-01"]
    assert data["tier"] == "hot"
    task = load_tasks(Config.load(vault))[0][0]
    assert task.uses == ["2026-10-05", "2026-09-20"] and task.state["tier"] == "hot"
    context = (vault.parent / ".hermes.md").read_text()
    assert "ORIGINAL_FULL_BODY" in context and "memory/Harbor Context|" in context
    log = vault / "agent/Use Log/2026-10.md"
    before = log.read_bytes()
    nightly.main(args)
    assert log.read_bytes() == before


def test_nightly_preview_keeps_sessions_and_vault_unchanged(vault, exported_sessions):
    before = snapshot(vault)
    exports_before = snapshot(exported_sessions)
    nightly.main(nightly_argv(vault, "2026-10-06", "--sessions-dir", str(exported_sessions)))
    assert snapshot(vault) == before and snapshot(exported_sessions) == exports_before
    assert not (vault.parent / ".hermes.md").exists()


def test_legacy_file_attribution_still_uses_whole_file_and_custom_reason(vault, tmp_path, capsys):
    path = tmp_path / "record.md"
    path.write_text("No Sources footer is necessary in the legacy form: T-001\n")
    nightly.main(["attribute", "--vault", str(vault), "--file", str(path), "--ts", "2026-10-05T12:00Z",
                  "--reason", "legacy", "--write"])
    assert capsys.readouterr().out == "2026-10-05T12:00Z | T-001 | legacy | explicit\n"
    assert attribute.read_log(vault / "agent/Use Log/2026-10.md")[0].reason == "legacy"


def test_legacy_file_attribution_still_requires_timestamp(vault, tmp_path):
    with pytest.raises(SystemExit) as caught:
        nightly.main(["attribute", "--vault", str(vault), "--file", str(tmp_path / "record.md")])
    assert caught.value.code == 2


def test_attribution_ignores_evaluation_sessions_and_routine_opens(tmp_path):
    import json
    from brain import attribute, fm
    from brain.vault import Vault
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    vault = tmp_path / "memory"; (vault / "agent").mkdir(parents=True)
    fm.write(vault / "Harbor.md", {"kind": "hub", "tier": "warm"}, "# Harbor\nbody\n")
    fm.write(vault / "Hot Set.md", {}, "# Hot set\n## Automatic load\n")
    sessions = vault / "Conversations" / "Sessions"; sessions.mkdir(parents=True)
    stamp = "2026-10-21T10:00:00-07:00"
    call = {"role": "assistant", "content": "ok", "timestamp": stamp,
            "tool_calls": [{"function": {"name": "read_file", "arguments": json.dumps({"path": "memory/Harbor.md"})}}]}
    cite = {"role": "assistant", "content": "Sources: [[memory/Harbor]]", "timestamp": stamp}
    records = [
        {"id": "conv", "source": "slack", "messages": [call]},
        {"id": "cron_sleep1", "source": "cron", "messages": [call, cite]},
        {"id": "eval", "title": "Evaluation fixture: Q01", "messages": [call, cite]},
    ]
    (sessions / "all.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    rows = attribute.sessions(Vault(vault), sessions)
    kinds = sorted((u.source, u.reason.split(" in session ")[-1] if " in session " in u.reason else u.reason) for u in rows)
    assert ("observed", "conv") in kinds
    assert ("explicit", "cron_sleep1") in kinds
    assert not any("cron_sleep1" in r and s == "observed" for s, r in kinds)
    assert not any("eval" in r for _, r in kinds)


def test_attribute_defaults_to_the_profile_records_folder(tmp_path, monkeypatch, capsys):
    import json
    from brain import fm, nightly
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    vault = tmp_path / "memory"; (vault / "agent").mkdir(parents=True)
    fm.write(vault / "Harbor.md", {"kind": "hub", "tier": "warm"}, "# Harbor\nbody\n")
    fm.write(vault / "Hot Set.md", {}, "# Hot set\n## Automatic load\n")
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    sessions = home / "records" / "sessions" / "jsonl"; sessions.mkdir(parents=True)
    call = {"role": "assistant", "content": "ok", "timestamp": "2026-10-21T10:00:00-07:00",
            "tool_calls": [{"function": {"name": "read_file", "arguments": json.dumps({"path": "memory/Harbor.md"})}}]}
    (sessions / "conv.jsonl").write_text(json.dumps({"id": "conv", "source": "slack", "messages": [call]}) + "\n", encoding="utf-8")
    nightly.main(["attribute", "--vault", str(vault)])
    assert "Harbor" in capsys.readouterr().out
    assert not (vault / "Conversations").exists()
