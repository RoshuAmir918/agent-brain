import os
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from brain.config import Config
from brain import digest, fm, nightly
from brain.vault import Vault
from conftest import snapshot

DAY = date(2026, 10, 20)


@pytest.fixture
def digest_sessions(vault):
    directory = vault / "Conversations/Sessions"
    directory.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "fixtures/all.jsonl", directory / "all.jsonl")
    (directory / "ignored.md").write_text("## user 2026-10-20T12:00:00-07:00\nMARKDOWN duplicate\n")
    (directory / "manifest.jsonl").write_text("not a session object\n")
    return directory


def test_jsonl_preview_filters_roles_preserves_text_and_counts(vault, digest_sessions, capsys):
    before = snapshot(vault.parent)
    nightly.main(["digest", "--vault", str(vault), "--date", str(DAY)])
    captured = capsys.readouterr()
    text = captured.out
    generated = datetime.now(digest.PACIFIC).date()
    assert f"Digest for {DAY}, generated {generated} by brain from 2 sessions, 22 words" in text
    assert text.startswith(digest.FRONTMATTER)
    assert fm.split(text)[0] == {"kind": "log", "tier": "cold", "pinned": False, "uses": [], "uses_inferred": []}
    assert text.index("## Slack DM (slack)") < text.index("## cli-session (cli)")
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
    nightly.main(args)
    output = vault / "Conversations/Digests/2026-10-20.md"
    assert capsys.readouterr().out == f"{output}: 22 words\n"
    after = snapshot(vault.parent)
    assert set(after) - set(before) == {"memory/Conversations/Digests/2026-10-20.md"}
    assert all(after[path] == checksum for path, checksum in before.items())
    modified = output.stat().st_mtime_ns
    nightly.main(args)
    assert capsys.readouterr().out == f"{output}: 22 words\n"
    assert snapshot(vault.parent) == after and output.stat().st_mtime_ns == modified


def test_sessions_directory_override(vault, digest_sessions, tmp_path, capsys):
    external = tmp_path / "exports"
    digest_sessions.rename(external)
    nightly.main(["digest", "--vault", str(vault), "--date", str(DAY), "--sessions-dir", str(external)])
    assert "from 2 sessions, 22 words" in capsys.readouterr().out
    assert not (vault / "Conversations/Digests").exists()


@pytest.mark.parametrize("key", ["timestamp", "created_at", "created", "ts", "time", "sent_at"])
@pytest.mark.parametrize("nested", [False, True])
def test_timestamp_aliases_and_timings(key, nested):
    fields = {key: "2026-10-20T07:01:00Z"}
    if nested:
        fields = {"timestamp": "invalid", "timings": fields}
    assert digest.timestamp_in(fields).isoformat() == "2026-10-20T00:01:00-07:00"


@pytest.mark.parametrize("value", ["2026-10-20T07:01:00Z", "2026-10-20T00:01:00-0700",
                                  "2026-10-20 00:01", 1792479660, 1792479660000, "1792479660000"])
def test_iso_epoch_seconds_milliseconds_and_naive_pacific(value):
    assert digest.parse_timestamp(value).isoformat() == "2026-10-20T00:01:00-07:00"


@pytest.mark.parametrize("value", [True, False, None, {}, [], "", "bad", "NaN", "inf", 10**99,
                                  "2026-02-30T00:00:00Z"])
def test_invalid_timestamps_are_unusable(value):
    assert digest.parse_timestamp(value) is None


def test_dst_fold_sorts_by_instant_not_repeated_wall_clock(template_config):
    record = {"messages": [
        {"role": "assistant", "content": "Later", "timestamp": "2026-11-01T01:10:00-08:00"},
        {"role": "user", "content": "Earlier", "timestamp": "2026-11-01T01:50:00-07:00"},
    ]}
    session = digest.select_session(record, "dst", date(2026, 11, 1), template_config)
    assert [turn.text for turn in session.turns] == ["Earlier", "Later"]


def test_inheritance_includes_skipped_messages_but_not_leading_undated_turns(template_config):
    record = {"started_at": "2026-10-20", "ended_at": "2026-10-21", "messages": [
        {"role": "user", "content": "Leading unknown"},
        {"role": "system", "timestamp": "2026-10-20T06:59:00Z"},
        {"role": "assistant", "content": "Still yesterday"},
        {"role": "tool", "timestamp": "2026-10-20T07:00:00Z"},
        {"role": "user", "content": "Inherited today"},
        {"role": "assistant", "content": "Compacted", "compacted": "true", "ts": "2026-10-20T07:02:00Z"},
        {"role": "assistant", "content": "Inherited compacted time", "active": False},
    ]}
    session = digest.select_session(record, "inheritance", DAY, template_config)
    assert [turn.text for turn in session.turns] == ["Inherited today", "Inherited compacted time"]
    assert [turn.timestamp.strftime("%H:%M") for turn in session.turns] == ["00:00", "00:02"]
    assert session.note == ""


@pytest.mark.parametrize("day,included", [(date(2026, 10, 18), False), (date(2026, 10, 19), True),
                                          (DAY, True), (date(2026, 10, 21), True), (date(2026, 10, 22), False)])
def test_undated_session_uses_inclusive_session_range(day, included, template_config):
    record = {"session": {"metadata": {"started_at": "2026-10-20T06:59:00Z", "ended_at": "2026-10-21T07:01:00Z"},
                          "messages": [{"role": "user", "content": "Untimed question"},
                                       {"role": "assistant", "content": "Untimed answer"}]}}
    session = digest.select_session(record, "untimed", day, template_config)
    assert len(session.turns) == (2 if included else 0)
    if included:
        assert "no usable message timestamps; session range" in session.note
        assert all(turn.timestamp.date() == day for turn in session.turns)


@pytest.mark.parametrize("bounds,included", [({}, False), ({"started_at": "2026-10-20"}, True),
    ({"ended_at": "2026-10-20"}, True), ({"started_at": "2026-10-21", "ended_at": "2026-10-19"}, False)])
def test_missing_or_reversed_session_range(bounds, included, template_config):
    session = digest.select_session({**bounds, "messages": [{"role": "user", "content": "Untimed"}]}, "bounds", DAY, template_config)
    assert bool(session.turns) == included


@pytest.mark.parametrize("key", ["messages", "history", "turns", "conversation"])
def test_defensive_message_container_aliases(key, template_config):
    record = {"data": {"metadata": {"title": "Nested title", "source": "cli"}, key: [None, {
        "role": "USER", "text": "Nested content", "time": "2026-10-20T00:00:00-07:00"}]}}
    session = digest.select_session(record, "export", DAY, template_config)
    assert session.title == "Nested title" and session.source == "cli"
    assert [turn.text for turn in session.turns] == ["Nested content"]


def test_content_blocks_concatenate_without_stripping():
    content = [None, "  leading ", {"text": "text\n"}, {"type": "output_text", "text": {"value": "ending  "}},
               {"type": "tool_use", "text": "SKIP"}, {"type": "tool_result", "text": "SKIP"},
               {"type": "image_url", "text": "SKIP"}, {"type": "text", "text": None}]
    assert digest.content_text(content) == "  leading text\nending  "
    assert digest.content_text({"text": "not a content list"}) == ""


def test_markdown_fallback_is_recursive_filters_dates_and_preserves_sources(vault, digest_sessions):
    (digest_sessions / "all.jsonl").unlink()
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
    session = digest.select_session({"messages": digest.markdown_messages(text)}, "markdown", DAY, template_config)
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


def test_empty_jsonl_still_takes_precedence(vault, digest_sessions):
    (digest_sessions / "all.jsonl").write_text("\n[]\nnull\n{}\n")
    text, words = digest.build(Config.load(vault), DAY)
    assert words == 0 and "from 0 sessions" in text and "MARKDOWN" not in text


@pytest.mark.parametrize("failure", ["malformed", "missing", "negative"])
def test_bad_inputs_fail_without_writing(vault, digest_sessions, failure):
    args = ["digest", "--vault", str(vault), "--date", str(DAY), "--write"]
    if failure == "malformed":
        (digest_sessions / "all.jsonl").write_text("not json\n")
    elif failure == "missing":
        shutil.rmtree(digest_sessions)
    else:
        args += ["--max-words", "-1"]
    before = snapshot(vault.parent)
    with pytest.raises(SystemExit):
        nightly.main(args)
    assert snapshot(vault.parent) == before


@pytest.mark.parametrize("extra", [[], ["--date", "not-a-date"]])
def test_digest_requires_a_valid_date(vault, extra):
    with pytest.raises(SystemExit) as caught:
        nightly.main(["digest", "--vault", str(vault), *extra])
    assert caught.value.code == 2


def test_digests_are_log_kind_and_excluded_from_mechanical_updates(vault, digest_sessions):
    text, _ = digest.build(Config.load(vault), DAY)
    path = digest.write(Config.load(vault), DAY, text)
    loaded = Vault(vault)
    relative = path.relative_to(vault).as_posix()
    assert loaded.kind_for(relative) == "log" and not nightly.mechanical(loaded, relative)
    assert loaded.kind_for("Conversations/Sessions/session.md") == "log"
    assert loaded.kind_for("Conversations/2026-10-20.md") == "episode"


def test_session_ids_lists_each_session_once(tmp_path):
    from brain import digest
    path = tmp_path / "all.jsonl"
    path.write_text('{"id": "a1", "messages": []}\n{"session": {"session_id": "b2"}, "messages": []}\n{"id": "a1"}\n\n', encoding="utf-8")
    assert digest.session_ids(path) == ["a1", "b2"]
