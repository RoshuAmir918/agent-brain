import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from brain.config import Config
from brain import cli, digest, fm, nightly
from brain.vault import Vault
from conftest import snapshot, harness_session, message, tool, write_shards

DAY = date(2026, 10, 20)


@pytest.fixture
def digest_sessions(vault, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    directory = home / "records/sessions"
    directory.mkdir(parents=True)
    shutil.copytree(Path(__file__).parent / "fixtures/sessions", directory / "jsonl")
    (directory / "ignored.md").write_text("## user 2026-10-20T12:00:00-07:00\nMARKDOWN duplicate\n")
    (directory / "manifest.jsonl").write_text("not a session object\n")
    return directory


def test_jsonl_preview_filters_roles_preserves_text_and_counts(vault, digest_sessions, capsys):
    before = snapshot(vault.parent)
    cli.main(["digest", "--vault", str(vault), "--date", str(DAY)])
    captured = capsys.readouterr()
    text = captured.out
    generated = datetime.now(digest.PACIFIC).date()
    assert f"Digest for {DAY}, generated {generated} by brain from 2 sessions, 22 words" in text
    assert text.startswith(digest.FRONTMATTER)
    assert fm.split(text)[0] == {"kind": "log", "tier": "cold", "pinned": False, "uses": [], "uses_inferred": []}
    assert text.index("## slack-dm (slack)") < text.index("## cli-session (cli)")
    assert text.index("**Human** 00:00") < text.index("**Agent** 00:02") < text.index("**Human** 00:03")
    assert "**Human** 00:01\nCLI morning question." in text
    assert "**Agent** 00:03\nCLI morning answer." in text
    assert "Slack morning answer.\nSources: T-001" in text
    assert "  Spacing stays.  \n\nSources: Keep exactly.  \n" in text
    assert all(word not in text for word in ("SYSTEM", "TOOL", "IMAGE", "FUNCTION", "DEVELOPER", "COMPACTED",
                                             "Yesterday", "Tomorrow", "MARKDOWN", "truncated:"))
    assert captured.err == "" and snapshot(vault.parent) == before


@pytest.mark.parametrize("human_name,agent_name", [
    (None, None), ("Alice Example", "Helper"), ("Alice Example", None), (None, "Helper"),
])
def test_digest_turn_labels_follow_environment(vault, digest_sessions, human_name, agent_name):
    environment = dict(os.environ)
    for key, value in (("BRAIN_HUMAN_NAME", human_name), ("BRAIN_AGENT_NAME", agent_name)):
        environment.pop(key, None)
        if value is not None:
            environment[key] = value
    result = subprocess.run(
        [sys.executable, "-m", "brain", "digest", "--vault", str(vault), "--date", str(DAY)],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, check=True)
    assert f"**{human_name or 'Human'}** 00:00\nSlack morning question." in result.stdout
    assert f"**{agent_name or 'Agent'}** 00:02\nSlack morning answer." in result.stdout


@pytest.mark.parametrize("day,expected,excluded", [
    (date(2026, 10, 19), "**Human** 23:59\nYesterday only.", "Slack morning"),
    (date(2026, 10, 21), "**Human** 00:00\nTomorrow only.", "Yesterday"),
])
def test_pacific_midnight_filters_both_sides(vault, digest_sessions, day, expected, excluded):
    text, words = digest.build(Config.load(vault), day, today=DAY)
    assert expected in text and excluded not in text
    assert f"from 1 sessions, {words} words" in text and words == 2


@pytest.mark.parametrize("cap,words,sessions,omitted", [(6, 6, 2, 4), (4, 3, 1, 5), (0, 0, 0, 6)])
def test_truncation_is_a_global_chronological_prefix(vault, digest_sessions, cap, words, sessions, omitted):
    text, count = digest.build(Config.load(vault), DAY, max_words=cap)
    assert count == words and f"from {sessions} sessions, {words} words" in text
    assert text.endswith(f"truncated: {omitted} further turns omitted; read the session export for the rest\n")
    assert "Slack morning answer." not in text
    assert ("CLI morning question." in text) == (cap == 6)
    assert ("Slack morning question." in text) == (cap > 0)


def test_exact_word_cap_keeps_every_turn(vault, digest_sessions):
    text, words = digest.build(Config.load(vault), DAY, max_words=22)
    assert words == 22 and "truncated:" not in text


def test_write_only_changes_digest_and_is_idempotent(vault, digest_sessions, capsys):
    args = ["digest", "--vault", str(vault), "--date", str(DAY), "--write"]
    before = snapshot(vault.parent)
    cli.main(args)
    output = vault / "Conversations/Digests/2026-10-20.md"
    assert capsys.readouterr().out == f"{output}: 22 words\n"
    after = snapshot(vault.parent)
    assert set(after) - set(before) == {"memory/Conversations/Digests/2026-10-20.md"}
    assert all(after[path] == checksum for path, checksum in before.items())
    modified = output.stat().st_mtime_ns
    cli.main(args)
    assert capsys.readouterr().out == f"{output}: 22 words\n"
    assert snapshot(vault.parent) == after and output.stat().st_mtime_ns == modified


def test_sessions_directory_override(vault, digest_sessions, tmp_path, capsys):
    external = tmp_path / "exports"
    digest_sessions.rename(external)
    cli.main(["digest", "--vault", str(vault), "--date", str(DAY), "--sessions-dir", str(external)])
    assert "from 2 sessions, 22 words" in capsys.readouterr().out
    assert not (vault / "Conversations/Digests").exists()


@pytest.mark.parametrize("value", ["2026-10-20T07:01:00Z", "2026-10-20T00:01:00-0700",
                                  "2026-10-20 00:01", 1792479660, 1792479660000, "1792479660000"])
def test_iso_epoch_seconds_milliseconds_and_naive_pacific(value):
    assert digest.parse_timestamp(value).isoformat() == "2026-10-20T00:01:00-07:00"


@pytest.mark.parametrize("value", [True, False, None, {}, [], "", "bad", "NaN", "inf", 10**99,
                                  "2026-02-30T00:00:00Z"])
def test_invalid_timestamps_are_unusable(value):
    assert digest.parse_timestamp(value) is None


def test_dst_fold_sorts_by_instant_not_repeated_wall_clock(template_config):
    record = harness_session("dst", [message("assistant", "Later", "2026-11-01T01:10:00-08:00"),
                                     message("user", "Earlier", "2026-11-01T01:50:00-07:00")])
    session = digest.select_session(record, "dst", date(2026, 11, 1), template_config)
    assert [turn.text for turn in session.turns] == ["Earlier", "Later"]


def test_markdown_fallback_is_recursive_filters_dates_and_preserves_sources(vault, digest_sessions):
    shutil.rmtree(digest_sessions / "jsonl")
    (digest_sessions / "ignored.md").unlink()
    nested = digest_sessions / "nested"
    nested.mkdir()
    (nested / "z.md").write_text("# Markdown DM\n## system\nSYSTEM\n"
        "## user 2026-10-20T06:59:00Z\nYesterday\n"
        "## user 2026-10-20T07:00:00Z\n  Keep this spacing.  \n\n"
        "## assistant\n2026-10-20T07:01:00Z\n**An answer.**\nSources: T-001\n"
        "## tool\n2026-10-20T07:02:00Z\nTOOL\n")
    (digest_sessions / "a.md").write_text("# Later session\n## user 2026-10-20T07:03:00Z\nLater question.\n")
    text, words = digest.build(Config.load(vault), DAY)
    assert text.index("## Markdown DM (nested/z.md)") < text.index("## Later session (a.md)")
    assert "**Human** 00:00\n  Keep this spacing.  \n\n" in text
    assert "**Agent** 00:01\n**An answer.**\nSources: T-001\n" in text
    assert words == 9 and "Yesterday" not in text and "TOOL" not in text and "SYSTEM" not in text


@pytest.mark.parametrize("user,assistant", [("## user", "### Assistant"), ("**USER**", "**assistant**"),
    ("user:", "assistant:"), ("role: user", "role: assistant"), ("- role: 'user'", '- role: "assistant"'),
    ("## human", "## assistant")])
def test_markdown_role_and_timestamp_markers(user, assistant, template_config):
    text = f"2026-10-20T07:01:00Z\n{user}\nQuestion\n{assistant}\nTimestamp: 2026-10-20T07:02:00Z\nAnswer\n"
    path = template_config.repo / "markdown.md"
    path.write_text(text)
    session = digest.markdown_session(path, path.parent, DAY, template_config)
    assert [(turn.role, turn.text, turn.timestamp.strftime("%H:%M")) for turn in session.turns] == [
        ("user", "Question\n", "00:01"), ("assistant", "Answer\n", "00:02")]


def test_markdown_keeps_inline_text_fences_and_body_timestamps():
    body = "```md\n## user\n2026-10-21T07:00:00Z\n```\n2026-10-22T07:00:00Z\nSources: keep\n"
    text = "2026-10-20T07:00:00Z\nuser: Inline question\n**assistant**: Inline answer\n" + body
    messages = digest.markdown_messages(text)
    assert len(messages) == 2
    assert messages[0]["content"] == "Inline question\n"
    assert messages[1]["content"] == "Inline answer\n" + body
    assert messages[1]["timestamp"] == "2026-10-20T07:00:00Z"


@pytest.mark.parametrize("relative", ["session_2026-10-20.md", "2026-10-20/session.md", "session.md"])
def test_markdown_undated_turns_use_filename_parent_or_mtime(vault, tmp_path, relative):
    directory = tmp_path / "exports"
    path = directory / relative
    path.parent.mkdir(parents=True)
    path.write_text("## user\nUntimed question\n## assistant\nUntimed answer\n")
    stamp = datetime(2026, 10, 20, 12).timestamp()
    os.utime(path, (stamp, stamp))
    text, words = digest.build(Config.load(vault), DAY, directory)
    assert words == 4 and "file mtime date" in text and "**Human** 00:00" in text


def test_unmarked_markdown_is_not_assigned_a_role(vault, tmp_path):
    directory = tmp_path / "exports"
    directory.mkdir()
    (directory / "2026-10-20.md").write_text("# Session\nUnmarked conversation\nSources: T-001\n")
    text, words = digest.build(Config.load(vault), DAY, directory)
    assert words == 0 and "from 0 sessions" in text and "Unmarked conversation" not in text


@pytest.mark.parametrize("extra", [[], ["--date", "not-a-date"]])
def test_digest_requires_a_valid_date(vault, extra):
    with pytest.raises(SystemExit) as caught:
        cli.main(["digest", "--vault", str(vault), *extra])
    assert caught.value.code == 2


def test_digests_are_log_kind_and_excluded_from_mechanical_updates(vault, digest_sessions):
    text, _ = digest.build(Config.load(vault), DAY)
    path = digest.write(Config.load(vault), DAY, text)
    loaded = Vault(vault)
    relative = path.relative_to(vault).as_posix()
    assert loaded.kind_for(relative) == "log" and not nightly.mechanical(loaded, relative)
    assert loaded.kind_for("Conversations/Sessions/session.md") == "log"
    assert loaded.kind_for("Conversations/2026-10-20.md") == "episode"


def test_default_sessions_dir_falls_back_to_the_vault_until_records_exist(tmp_path, monkeypatch):
    from brain import digest
    from brain.config import Config
    (tmp_path / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    config = Config.load(tmp_path / "memory")
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    legacy = tmp_path / "memory" / "Conversations" / "Sessions"
    assert digest.default_sessions_dir(config) == home / "records" / "sessions"
    legacy.mkdir(parents=True)
    assert digest.default_sessions_dir(config) == legacy
    (home / "records" / "sessions").mkdir(parents=True)
    assert digest.default_sessions_dir(config) == home / "records" / "sessions"
