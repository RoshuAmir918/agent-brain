import shutil
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from brain.config import Config
from brain import cli, digest, fm, nightly
from brain.vault import Vault
from conftest import snapshot, harness_session, message

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


def test_jsonl_preview_filters_roles_preserves_text_and_counts(vault, digest_sessions, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-21")
    before = snapshot(vault.parent)
    cli.main(["digest", "--vault", str(vault), "--date", str(DAY)])
    captured = capsys.readouterr()
    text = captured.out
    assert f"Digest for {DAY}, generated 2026-10-21 by brain from 2 sessions, 22 words" in text
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
    assert "truncated:" not in digest.build(Config.load(vault), DAY, max_words=22)[0]


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


@pytest.mark.parametrize("value", ["2026-10-20T07:01:00Z", "2026-10-20T00:01:00-0700",
                                  "2026-10-20 00:01", 1792479660, 1792479660000, "1792479660000"])
def test_iso_epoch_seconds_milliseconds_and_naive_in_the_given_zone(value):
    assert digest.parse_timestamp(value, ZoneInfo("America/Los_Angeles")).isoformat() == "2026-10-20T00:01:00-07:00"


@pytest.mark.parametrize("value", [True, False, None, {}, [], "", "bad", "NaN", "inf", 10**99,
                                  "2026-02-30T00:00:00Z"])
def test_invalid_timestamps_are_unusable(value):
    assert digest.parse_timestamp(value, ZoneInfo("America/Los_Angeles")) is None


def test_dst_fold_sorts_by_instant_not_repeated_wall_clock(template_config):
    record = harness_session("dst", [message("assistant", "Later", "2026-11-01T01:10:00-08:00"),
                                     message("user", "Earlier", "2026-11-01T01:50:00-07:00")])
    session = digest.select_session(record, "dst", date(2026, 11, 1), template_config)
    assert [turn.text for turn in session.turns] == ["Earlier", "Later"]


def test_digests_are_log_kind_and_excluded_from_mechanical_updates(vault, digest_sessions):
    text, _ = digest.build(Config.load(vault), DAY)
    path = digest.write(Config.load(vault), DAY, text)
    loaded = Vault(vault)
    relative = path.relative_to(vault).as_posix()
    assert loaded.kind_for(relative) == "log" and not nightly.mechanical(loaded, relative)
    assert loaded.kind_for("Conversations/Sessions/session.md") == "log"
    assert loaded.kind_for("Conversations/2026-10-20.md") == "episode"


def test_a_non_owner_without_a_name_is_labelled_by_id_never_as_the_owner(template_config):
    record = harness_session("stranger", [message("user", "Hello there", "2026-10-20T15:00:00Z")], platform="slack", owner=False)
    record["speaker"]["userId"] = "U9"
    [turn] = digest.select_session(record, "stranger.json", DAY, template_config).turns
    assert turn.speaker == "slack:U9"
    del record["speaker"]["userId"]
    [turn] = digest.select_session(record, "stranger.json", DAY, template_config).turns
    assert turn.speaker == "unknown speaker"
