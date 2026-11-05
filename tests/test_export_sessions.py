"""Archive the harness's own JSON and Markdown readable by the night."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from brain import attribute, digest, fm, install, nightly, sessions, validate
from brain.config import Config
from brain.vault import Vault
from conftest import harness_session, message, snapshot, tool, write_shards


@pytest.fixture
def harness(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    folder = home / "harness/sessions/2026-10-20"
    shutil.copytree(Path(__file__).parent / "fixtures/sessions", folder)
    routine = harness_session("night", [message("user", "Run sleep1."), message("assistant", "Done.")],
                              platform="cron", user_name="sleep1")
    (folder / "night.json").write_text(json.dumps(routine, indent=2))
    return Config.load(vault), home, folder


def test_export_conversations_routines_raw_json_and_unchanged_mtimes(harness, capsys):
    config, home, source = harness
    args = ["export-sessions", "--vault", str(config.vault_path), "--date", "2026-10-20"]
    nightly.main(args)
    assert capsys.readouterr().out == 'exported: 6 files written, 0 unchanged\n'
    out = config.vault_path / "Conversations/Sessions"
    assert sorted(path.name for path in out.iterdir()) == ["cli-session.md", "slack-dm.md"]
    records = home / "records/sessions"
    assert (records / "night.md").exists() and not (out / "night.md").exists()
    for original in source.glob("*.json"):
        assert (records / "jsonl" / original.name).read_bytes() == original.read_bytes()
    transcript = (out / "slack-dm.md").read_text()
    assert transcript.startswith('# slack-dm\n\n- platform: slack\n- speaker: owner (owner)\n- started: ')
    assert '## user · 2026-10-20T07:00:00Z\n\nSlack morning question.' in transcript
    times = {path: path.stat().st_mtime_ns for path in list(out.glob("*.md")) + list(records.rglob("*")) if path.is_file()}
    nightly.main(args)
    assert capsys.readouterr().out == 'exported: 0 files written, 6 unchanged\n'
    assert all(path.stat().st_mtime_ns == stamp for path, stamp in times.items())
    updated = json.loads((source / "slack-dm.json").read_text())
    updated["entries"].append(message("assistant", "Another turn.", "2026-10-21T07:01:00Z"))
    (source / "slack-dm.json").write_text(json.dumps(updated))
    nightly.main(args)
    assert capsys.readouterr().out == 'exported: 2 files written, 4 unchanged\n'
    assert all(path.stat().st_mtime_ns == stamp for path, stamp in times.items() if path.stem != 'slack-dm')
    assert (source / "night.json").read_bytes() == (records / "jsonl/night.json").read_bytes()


def test_export_date_includes_cross_midnight_sessions_and_default_exports_all(harness):
    config, home, source = harness
    assert sessions.export(config, date(2026, 10, 22)) == (0, 0)
    assert sessions.export(config, date(2026, 10, 21)) == (2, 0)
    assert sorted(path.name for path in (config.vault_path / "Conversations/Sessions").iterdir()) == ["slack-dm.md"]
    assert sessions.export(config) == (4, 2)


def test_said_and_native_tool_paths_reach_digest_and_attribution(harness, monkeypatch):
    config, home, source = harness
    monkeypatch.setenv("BRAIN_HOT_WORDS", "0")
    fm.write(config.vault_path / "Harbor Context.md", {"kind": "hub", "tier": "warm"}, '# Harbor\n')
    record = harness_session("said", [message("user", "SYSTEM CONTEXT\nQuestion", said="Question"),
        tool("read_file", {"path": str(config.vault_path / "Harbor Context.md")}),
        tool("terminal", {"command": 'brain retrieve --json "harbor CI"'}),
        message("assistant", "Answer.\nSources: [[Harbor Context]]")])
    (source / "said.json").write_text(json.dumps(record))
    sessions.export(config)
    text, _ = digest.build(config, date(2026, 10, 20))
    assert 'SYSTEM CONTEXT' not in text and '**Human** 00:01\nQuestion' in text
    assert 'reads: memory/Harbor Context.md' in text and 'retrieve: "harbor CI"' in text
    uses = attribute.sessions(Vault(config.vault_path), home / "records/sessions")
    assert any(row.source == 'observed' and row.target == 'Harbor Context.md' for row in uses)
    assert any(row.source == 'explicit' and row.reason == 'cited in session said' for row in uses)
    md = config.vault_path / 'Conversations/Sessions/said.md'
    legacy = digest.markdown_session(md, md.parent, date(2026, 10, 20), config)
    assert legacy.turns[0].text.strip() == 'Question'
    assert any(use.target == 'Harbor Context.md' for use in attribute.session_rows(Vault(config.vault_path), md))


def test_digest_coverage_routines_evaluations_and_owner_timezone(vault, tmp_path):
    (vault.parent / 'brain.toml').write_text('[brain]\ntimezone = "America/New_York"\n')
    config = Config.load(vault)
    stamp = '2026-10-26T23:30:00-04:00'
    directory = tmp_path / 'archives'
    write_shards(directory,
        harness_session('chat', [message('user', 'still up', stamp), message('assistant', 'so am I', stamp)]),
        harness_session('routine', [message('user', 'Run sleep1.', stamp)], platform='cron'),
        harness_session('check', [message('user', 'Evaluation fixture: Q09', stamp)]))
    text, _ = digest.build(config, date(2026, 10, 26), directory)
    assert '**Human** 23:30\nstill up' in text
    assert 'Run sleep1.' not in text and 'Evaluation fixture: Q09' not in text
    assert '1 routine sessions and 1 evaluation sessions excluded' in text
    text, _ = digest.build(config, date(2026, 10, 26), directory, include_routines=True)
    assert 'Run sleep1.' in text and 'Evaluation fixture: Q09' not in text
    text, _ = digest.build(Config(tmp_path), date(2026, 10, 26), directory)
    assert '**Human** 20:30\nstill up' in text


def test_nonowner_speaker_is_explicit_and_brackets_are_plain_text(vault, tmp_path):
    directory = tmp_path / 'archives'
    write_shards(directory, harness_session('friend', [message('user', '[draft] hello')],
                                           owner=False, user_name='Kevin'))
    text, _ = digest.build(Config.load(vault), date(2026, 10, 20), directory)
    assert '**Kevin** 00:01\n[draft] hello' in text


def test_legacy_jsonl_is_ignored_and_old_markdown_remains_readable(vault, tmp_path, monkeypatch):
    directory = vault / 'Conversations/Sessions'
    directory.mkdir(parents=True)
    (directory / '2026-10-20.md').write_text('## user\nQuestion\n## assistant\nAnswer\nSources: T-001\n')
    (directory / 'all.jsonl').write_text('invalid legacy data\n')
    monkeypatch.delenv('AGENT_HOME', raising=False)
    monkeypatch.delenv('HERMES_HOME', raising=False)
    config = Config.load(vault)
    text, _ = digest.build(config, date(2026, 10, 20))
    assert 'Question' in text and 'Answer' in text
    assert [use.target for use in attribute.sessions(Vault(vault), digest.default_sessions_dir(config))] == ['T-001']
    assert validate.schedule_hygiene(Vault(vault)) == []


def test_no_schedule_migration_is_reported_without_starting_a_process(vault, monkeypatch, capsys):
    from conftest import START_MIGRATION
    def unexpected(*args, **kwargs):
        raise AssertionError('no runtime command may be started')
    monkeypatch.setattr(install.subprocess, 'run', unexpected)
    config = Config.load(vault)
    assert START_MIGRATION(config) is False
    assert f'no declared schedule at {config.skills_path.parent / "schedule.toml"}' in capsys.readouterr().err


def test_install_paths_require_an_explicit_runtime(vault, monkeypatch, capsys):
    monkeypatch.delenv('AGENT_HOME', raising=False)
    monkeypatch.delenv('HERMES_HOME', raising=False)
    with pytest.raises(RuntimeError, match='AGENT_HOME is not set'):
        install.scripts_path()
    nightly.main(['paths', 'vault', '--vault', str(vault)])
    assert capsys.readouterr().out == str(vault) + '\n'


def test_export_rejects_malformed_shard_before_writing(harness):
    config, home, source = harness
    (source / 'z-invalid.json').write_text('invalid')
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError, match='invalid session JSON'):
        sessions.export(config)
    assert snapshot(config.vault_path) == before and not (home / 'records').exists()
