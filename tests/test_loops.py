"""Held work, its after-conversation capture, and the owner's view of it."""
import json
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from brain import cli, fm, journal, ledger, loops, needs, state, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

AT = datetime(2026, 10, 28, 12, 0)


@pytest.fixture
def config(vault):
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm"}, "# Trip\n")
    return Config.load(vault)


def test_parse_format_and_stable_ids():
    item = loops.Item("owed", "Relay the answer", "2026-10-28 12:00", "Trip", "Kevin", "2026-10-29 09:30")
    body = loops.TEMPLATE + loops.render(item)  # only the Open section should be read
    assert loops.parse_section(body) == []
    body = "## Open\n\n" + loops.render(item) + "\n\n## Closed\n"
    assert loops.parse_section(body) == [item]
    item.closed, item.outcome, item.because = "2026-10-29 10:00", "done", "Answer delivered"
    assert loops.parse_section("## Closed\n" + loops.render(item), "Closed") == [item]
    equivalent = loops.Item("owed", " RELAY   the ANSWER ", "2026-11-01 01:00", "Elsewhere")
    assert equivalent.id == item.id
    assert loops.Item("promised", item.text, item.since, item.source).id != item.id


@pytest.mark.parametrize("line", [
    "- 2026-02-30 12:00 · owed · Answer · [[Trip]]",
    "- 2026-10-28 25:00 · owed · Answer · [[Trip]]",
    "- 2026-10-28 · owed · Answer · [[Trip]]",
    "- 2026-10-28 12:00 · ask · Answer · [[Trip]]",
    "- 2026-10-28 12:00 · owed · Answer · [[ ]]",
    "- 2026-10-28 12:00 · owed · Answer · more · [[Trip]]",
])
def test_malformed_bullets_are_not_open_loops(line):
    assert loops.parse_section("## Open\n" + line) == []


def test_add_deduplicates_open_text_without_changing_original(config):
    assert loops.add(config, AT, "promised", "Send the link", "Trip", "Kevin", "2026-10-29")
    before = snapshot(config.vault_path)
    assert not loops.add(config, AT, "owed", "  SEND\n the   link ", "Elsewhere", "Kevin")
    assert snapshot(config.vault_path) == before
    data, body = fm.read(loops.path_for(config))
    assert data == {"kind": "orientation", "tier": "warm", "pinned": False}
    assert body.startswith("# Loops\n") and "## Open" in body and "## Closed" in body
    [item] = loops.open_loops(config)
    assert (item.with_, item.due, item.source) == ("Kevin", "2026-10-29", "Trip")


@pytest.mark.parametrize("kwargs", [
    {"kind": "ask"}, {"text": " "}, {"source": "[[Trip]]"}, {"source": "x\ny"},
    {"due": "tomorrow"}, {"due": "2026-02-30"}, {"due": "2026-10-29T09:00"},
])
def test_bad_add_writes_nothing(config, kwargs):
    args = {"kind": "owed", "text": "Send answer", "source": "Trip", "with_": "Kevin", **kwargs}
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError):
        loops.add(config, AT, **args)
    assert snapshot(config.vault_path) == before


@pytest.fixture
def known_config(config):
    for rel, title, aliases in (
        ("people/Kevin_Smith/Profile.md", "Kevin Jonoah Smith", ["Kev", "KJ"]),
        ("agents/Ada-Logic/Profile.md", "Ada Lovelace", "Analyst"),
    ):
        path = config.vault_path / rel
        path.parent.mkdir(parents=True)
        fm.write(path, {"kind": "profile", "aliases": aliases}, f"# {title}\n")
    return config


@pytest.mark.parametrize("who", ["mira chen", "MIRA"])
def test_owner_full_and_first_names_are_refused_even_in_a_bare_vault(config, who):
    config = replace(config, human_name="Mira Chen")
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError) as error:
        loops.add(config, AT, "waiting", "Await a decision", "Trip", who)
    assert str(error.value) == f"{who} is your owner: put it in Needs you as an ask, not a loop"
    assert snapshot(config.vault_path) == before
    assert loops.add(config, AT, "promised", "Have the plan by morning", "Trip", who)


@pytest.mark.parametrize("who", ["Miranda-Jones", "Miranda", "Mira Jane Jones", "MJ"])
def test_configured_owner_profile_names_and_aliases_are_refused(config, who):
    config = replace(config, human_profile="people/Miranda_Jones/Profile.md")
    path = config.vault_path / config.human_profile
    path.parent.mkdir(parents=True)
    fm.write(path, {"aliases": ["MJ"]}, "# Mira Jane Jones\n")
    with pytest.raises(ValueError, match="is your owner"):
        loops.add(config, AT, "waiting", "Await a decision", "Trip", who)


def test_owner_people_profile_found_by_configured_platform_id_is_refused(known_config):
    config = replace(known_config, owner_user_ids=["demiurge:owner-id"])
    path = config.vault_path / "people/Actual_Owner/Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {}, "# Actual Owner\nDemiurge: owner-id\n")
    with pytest.raises(ValueError, match="is your owner"):
        loops.add(config, AT, "waiting", "Ask for the date", "Trip", "Actual")


@pytest.mark.parametrize("who", ["kevin-smith", "Kevin Jonoah Smith", "KEVIN", "kev", "KJ",
                                 "ada_logic", "Ada Lovelace", "ADA", "analyst"])
def test_known_people_and_intellects_match_folder_title_first_name_or_alias(known_config, who):
    assert loops.add(known_config, AT, "owed", "Relay the answer", "Trip", who)
    assert loops.open_loops(known_config)[0].with_ == who


def test_profile_lookup_uses_configured_people_and_agents_directories(config):
    config = replace(config, people_dir="Contacts", agents_dir="Intellects")
    for root, name in ((config.people_dir, "Kevin"), (config.agents_dir, "Ada")):
        path = config.vault_path / root / name / "Profile.md"
        path.parent.mkdir(parents=True)
        fm.write(path, {}, f"# {name}\n")
        assert loops.add(config, AT, "waiting", f"Await {name}", "Trip", name)


def test_ambiguous_first_name_is_refused_but_full_names_still_match(known_config):
    path = known_config.vault_path / "people/Kevin_Pak/Profile.md"
    path.parent.mkdir()
    fm.write(path, {}, "# Kevin Pak\n")
    with pytest.raises(ValueError, match="is not a person or intellect you know"):
        loops.add(known_config, AT, "owed", "Relay the answer", "Trip", "Kevin")
    assert loops.add(known_config, AT, "owed", "Relay the answer", "Trip", "Kevin Pak")


def test_intellect_name_in_demiurge_directory_needs_no_vault_profile(known_config, tmp_path, monkeypatch):
    from brain import directory
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    path = directory.path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"intellects": [{"id": "j573", "name": "Tars Prime", "handle": "tars"}]}))
    assert loops.add(known_config, AT, "waiting", "Await the findings", "Trip", "tars-prime")


@pytest.mark.parametrize("who", ["test-harness maintainer", "Unknown Person"])
def test_unknown_counterpart_is_refused_without_writing(known_config, who):
    before = snapshot(known_config.vault_path)
    with pytest.raises(ValueError) as error:
        loops.add(known_config, AT, "owed", "Relay the answer", "Trip", who)
    assert str(error.value) == f"{who} is not a person or intellect you know; name who you asked or who asked you"
    assert snapshot(known_config.vault_path) == before


def test_empty_people_directory_still_validates_and_retired_profiles_are_not_identities(config):
    path = config.vault_path / "people/Old_Name/Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {"kind": "log"}, "# Old Name\nMoved elsewhere.\n")
    for who in ("Old Name", "New Person"):
        with pytest.raises(ValueError, match="is not a person or intellect you know"):
            loops.add(config, AT, "owed", "Relay the answer", "Trip", who)


def test_bare_vault_accepts_a_counterpart_without_a_profile(config):
    assert not (config.vault_path / config.people_dir).exists()
    assert loops.add(config, AT, "owed", "Relay the answer", "Trip", "New Person")
    assert loops.add(config, AT, "promised", "Check fares", "Trip")


@pytest.mark.parametrize("kind", ["owed", "waiting"])
@pytest.mark.parametrize("who", ["", " \n "])
def test_owed_and_waiting_require_a_counterpart_even_in_bare_vaults(config, kind, who):
    before = snapshot(config.vault_path)
    with pytest.raises(ValueError, match=f"{kind} loops need a counterpart"):
        loops.add(config, AT, kind, "Relay the answer", "Trip", who)
    assert snapshot(config.vault_path) == before


def test_cli_add_uses_counterpart_validation(known_config):
    with pytest.raises(SystemExit, match="brain loops: test-harness maintainer is not a person or intellect"):
        cli.main(["loops", "--vault", str(known_config.vault_path), "--add", "Relay the answer",
                      "--kind", "owed", "--source", "Trip", "--with", "test-harness maintainer"])
    assert loops.open_loops(known_config) == []


@pytest.mark.parametrize("outcome", ["done", "dropped"])
def test_close_moves_all_details_to_closed_and_allows_reopening(config, outcome):
    loops.add(config, AT, "owed", "Send answer", "Trip", "Kevin", "2026-10-29")
    [item] = loops.open_loops(config)
    closed = loops.close(config, item.id, AT + timedelta(days=1), outcome, "The reason")
    assert loops.open_loops(config) == []
    assert loops.closed_recent(config) == [closed]
    assert (closed.id, closed.closed, closed.outcome, closed.because) == (
        item.id, "2026-10-29 12:00", outcome, "The reason")
    assert (closed.with_, closed.due) == ("Kevin", "2026-10-29")
    assert loops.add(config, AT + timedelta(days=2), "owed", "Send answer", "Trip", "Kevin")
    assert loops.open_loops(config)[0].id == item.id


def test_close_by_exact_or_unique_text_and_ambiguous_text_is_refused(config):
    for text in ("Send answer", "Send answer to Kevin", "Check fares"):
        loops.add(config, AT, "owed", text, "Trip", "Kevin")
    assert loops.close(config, "SEND   answer", AT, "done", "Delivered").text == "Send answer"
    assert loops.close(config, "fares", AT, "dropped", "Trip cancelled").text == "Check fares"
    loops.add(config, AT, "owed", "Send answer to Pat", "Trip", "Pat")
    before = snapshot(config.vault_path)
    for which, outcome, because in (("Send", "done", "Delivered"), ("unknown", "done", "Done"),
                                     ("Send", "failed", "Error"), ("Send", "done", " ")):
        with pytest.raises(ValueError):
            loops.close(config, which, AT, outcome, because)
    assert snapshot(config.vault_path) == before


def test_closed_archives_oldest_fifty_retained_by_closing_month(config):
    # Reopening the same text must not erase other closures with the same id.
    for index in range(53):
        at = datetime(2026, 9, 30, 23, 58) + timedelta(minutes=index)
        loops.add(config, at, "promised", "Check again", "Trip", "Kevin")
        loops.close(config, "Check again", at, "done", f"Check {index}")
    kept = loops.closed_recent(config)
    assert len(kept) == 50 and kept[0].because == "Check 52" and kept[-1].because == "Check 3"
    archives = config.vault_path / config.agent("Loops")
    september = archives / "2026-09.md"
    october = archives / "2026-10.md"
    assert fm.read(september)[0] == {"kind": "log", "tier": "cold"}
    archived = loops.parse_section("## Closed\n" + fm.read(september)[1], "Closed")
    archived += loops.parse_section("## Closed\n" + fm.read(october)[1], "Closed")
    assert [item.because for item in archived] == ["Check 0", "Check 1", "Check 2"]
    assert all(item.with_ == "Kevin" and item.outcome == "done" for item in archived)
    assert ledger.discover(config) == []   # loop archives are engine logs, never routine ledgers


@pytest.mark.parametrize("kind,due,hours,expected", [
    ("promised", "", 3.99, False), ("promised", "", 4, True), ("owed", "", 4, True),
    ("owed", "2026-10-28 13:00", 0.99, False), ("owed", "2026-10-28 13:00", 1, True),
    ("promised", "2026-10-30", 24, False), ("promised", "2026-10-29", 12, True),
    ("waiting", "", 23.99, False), ("waiting", "", 24, True),
    ("waiting", "2026-10-28 13:00", 5, False), ("waiting", "2026-10-28 13:00", 24, True),
    ("waiting", "2026-10-30", 24, False),
])
def test_due_rules(config, kind, due, hours, expected):
    loops.add(config, AT, kind, "Follow through", "Trip", "Kevin", due=due)
    assert bool(loops.due(config, AT + timedelta(hours=hours))) is expected


def test_due_respects_owner_zone_and_elapsed_hours_across_dst(config):
    at = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)  # local midnight, before the clocks fall back
    loops.add(config, at, "promised", "Check", "Trip")
    assert loops.open_loops(config)[0].since == "2026-11-01 00:00"
    assert not loops.due(config, at + timedelta(hours=3, minutes=59))
    assert loops.due(config, at + timedelta(hours=4))


def test_cli_lists_adds_closes_and_quiet_exit_codes(config, monkeypatch, capsys):
    monkeypatch.setattr(Config, "now", lambda self, moment=None: AT)
    argv = ["loops", "--vault", str(config.vault_path)]
    cli.main(argv + ["--due"])
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit) as error:
        cli.main(argv + ["--due", "--quiet"])
    assert error.value.code == 1 and capsys.readouterr().out == ""
    cli.main(argv + ["--add", "Relay answer", "--kind", "owed", "--source", "Trip", "--with", "Kevin",
                         "--due", "2026-10-28 11:00"])
    assert "added loop" in capsys.readouterr().out
    cli.main(argv)
    line = capsys.readouterr().out
    assert "owed · Relay answer · [[Trip]]" in line and "with: Kevin" in line and len(line.splitlines()) == 1
    cli.main(argv + ["--due"])
    assert capsys.readouterr().out == line
    with pytest.raises(SystemExit) as error:
        cli.main(argv + ["--quiet"])
    assert error.value.code == 0 and capsys.readouterr().out == ""
    cli.main(argv + ["--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["open"] == result["due"] and result["closed_recent"] == []
    cli.main(argv + ["--close", result["open"][0]["id"], "--outcome", "dropped", "--because", "No longer needed"])
    assert "closed (dropped)" in capsys.readouterr().out
    cli.main(argv + ["--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["open"] == result["due"] == [] and result["closed_recent"][0]["outcome"] == "dropped"


@pytest.mark.parametrize("extra", [
    ["--add", "Check"], ["--add", "Check", "--kind", "owed", "--source", "Trip", "--due"],
    ["--close", "Check"], ["--source", "Trip"], ["--outcome", "done"], ["--due", "2026-10-29"],
])
def test_cli_rejects_incomplete_mutations(config, extra):
    with pytest.raises(SystemExit, match="brain loops:"):
        cli.main(["loops", "--vault", str(config.vault_path), *extra])


def test_cli_empty_due_exits_zero_in_a_subprocess(config):
    result = subprocess.run([sys.executable, "-m", "brain", "loops", "--due", "--vault", str(config.vault_path)],
                            text=True, capture_output=True)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_conversation_adds_and_closes_without_journal_rows(config):
    loops.add(config, AT, "promised", "Check fares", "Trip")
    item = loops.open_loops(config)[0]
    record = {"headline": "Checked fares", "outcome": "nothing", "touched": ["Missing", "Trip"],
              "closed": [item.id, "already gone"], "loops": [
                  {"kind": "owed", "text": "Send the options", "with": "Kevin", "due": None}]}
    path = journal.append_entry(config, record, "cli", AT)
    assert path == loops.path_for(config)
    assert [loop.text for loop in loops.open_loops(config)] == ["Send the options"]
    assert loops.open_loops(config)[0].source == "Trip"
    assert (loops.closed_recent(config)[0].outcome, loops.closed_recent(config)[0].because) == (
        "done", "closed in conversation")
    assert not (config.vault_path / config.agent("Journal")).exists()
    record["closed"], record["loops"] = ["Send the options"], []
    journal.append_entry(config, record, "cli", AT)
    assert loops.open_loops(config) == []


def test_record_closures_keep_each_outcome_and_reason_and_tolerate_retries(config):
    for text in ("Send fares", "Book rooms"):
        loops.add(config, AT, "promised", text, "Trip")
    send, book = loops.open_loops(config)
    record = {"headline": "Sent fares; rooms no longer needed", "outcome": "done", "closed": [
        {"loop": send.id, "outcome": "done", "why": "Sent the fare comparison"},
        {"loop": book.text, "outcome": "dropped", "why": "They decided not to travel"},
        {"loop": "already gone", "outcome": "dropped", "why": "No longer needed"}]}
    journal.append_entry(config, record, "cli", AT)
    assert loops.open_loops(config) == []
    assert {(item.text, item.outcome, item.because) for item in loops.closed_recent(config)} == {
        ("Send fares", "done", "Sent the fare comparison"),
        ("Book rooms", "dropped", "They decided not to travel")}
    before = snapshot(config.vault_path)
    journal.append_entry(config, record, "cli", AT + timedelta(minutes=1))
    assert snapshot(config.vault_path) == before


@pytest.mark.parametrize("bad,reason", [
    ({"kind": "waiting", "text": "Await the decision", "with": "Human"},
     "Human is your owner: put it in Needs you as an ask, not a loop"),
    ({"kind": "owed", "text": "Relay the answer", "with": "test-harness maintainer"},
     "test-harness maintainer is not a person or intellect you know; name who you asked or who asked you"),
    ({"kind": "waiting", "text": "Await the date"}, "waiting loops need a counterpart (with)"),
    ({"kind": "promised", "text": "Check", "due": "tomorrow"},
     "date must be YYYY-MM-DD or YYYY-MM-DD HH:MM"),
])
def test_record_skips_one_refused_loop_and_applies_the_rest(known_config, capsys, bad, reason):
    config = known_config
    loops.add(config, AT, "promised", "Book rooms", "Trip")
    record = {"headline": "Follow-up recorded", "outcome": "progress", "needsYou": "Which date?",
              "loops": [{"kind": "promised", "text": "Check fares"}, bad,
                        {"kind": "owed", "text": "Send options", "with": "Kev", "due": "2026-10-29 09:30"}],
              "closed": [{"loop": "Book rooms", "outcome": "dropped", "why": "Trip cancelled"}]}
    journal.append_entry(config, record, "cli", AT)
    assert capsys.readouterr().err == f"skipped loop: {reason}\n"
    assert [item.text for item in loops.open_loops(config)] == ["Check fares", "Send options"]
    assert loops.open_loops(config)[1].due == "2026-10-29 09:30"
    assert [item.text for item in needs.items(config, AT.date())] == ["Which date?"]
    assert loops.closed_recent(config)[0].outcome == "dropped"


def test_quiet_record_writes_nothing_and_default_source_is_conversations(config):
    before = snapshot(config.vault_path)
    journal.append_entry(config, {"headline": "Small talk", "outcome": "nothing"}, "cli", AT)
    assert snapshot(config.vault_path) == before
    journal.append_entry(config, {"headline": "Will check", "outcome": "progress",
                                 "loops": [{"kind": "promised", "text": "Check fares"}]}, "cli", AT)
    assert loops.open_loops(config)[0].source == "Conversations"


def test_state_keys_ten_recent_closures_and_source_privacy(config):
    loops.add(config, AT, "waiting", "Await options", "Trip", "Kevin", "2026-10-30")
    public = loops.open_loops(config)[0]
    for index in range(12):
        loops.add(config, AT, "owed", f"Answer {index}", "Trip", "Kevin")
        loops.close(config, f"Answer {index}", AT + timedelta(minutes=index), "done", "Delivered")
    for source in ("people/Pat/Profile", "human/Profile", "Secret", "../elsewhere"):
        loops.add(config, AT, "owed", f"Held for {source}", source, "Kevin")
        loops.add(config, AT, "owed", f"Closed for {source}", source, "Kevin")
        loops.close(config, f"Closed for {source}", AT + timedelta(days=1), "dropped", "No need")
    fm.write(config.vault_path / "Secret.md", {"kind": "hub", "tier": "private"}, "# Secret\n")
    result = state.build(config, AT.date())
    assert result["loops"] == [{**loops.as_dict(public), "source": "Trip.md"}]
    assert len(result["loopsClosed"]) == 10 and result["loopsClosed"][0]["text"] == "Answer 11"
    assert result["loopsClosed"][0]["closed"] == "2026-10-28 12:11"


def test_hygiene_names_only_open_loops_older_than_fourteen_days(config, monkeypatch):
    monkeypatch.setattr(Config, "today", lambda self: date(2026, 10, 28))
    loops.add(config, AT - timedelta(days=15), "promised", "Old promise", "Trip")
    loops.add(config, AT - timedelta(days=14), "owed", "At boundary", "Trip", "Kevin")
    loops.add(config, AT - timedelta(days=20), "owed", "Finished", "Trip", "Kevin")
    loops.close(config, "Finished", AT, "done", "Delivered")
    problems = validate.loops_hygiene(Vault(config.vault_path))
    assert len(problems) == 1 and "Old promise" in problems[0]
    assert "decide it: close it, drop it, or set a due date" in problems[0]
    assert problems[0] in validate.hygiene(Vault(config.vault_path))


@pytest.mark.parametrize("kind", loops.KINDS)
def test_ready_round_trips_and_overrides_age_and_future_deadline(config, kind, monkeypatch, capsys):
    loops.add(config, AT, kind, "Follow through", "Trip", "Kevin", "2026-12-01")
    [item] = loops.open_loops(config)
    assert loops.due(config, AT) == []
    marked = loops.ready(config, item.id, AT)
    assert marked.ready == "2026-10-28 12:00"
    assert loops.parse_section("## Open\n" + loops.render(marked)) == [marked]
    assert loops.open_loops(config) == loops.due(config, AT) == [marked]
    before = snapshot(config.vault_path)
    assert loops.ready(config, "FOLLOW  THROUGH", AT + timedelta(hours=1)) == marked
    assert snapshot(config.vault_path) == before
    monkeypatch.setattr(Config, "now", lambda self, moment=None: AT)
    cli.main(["loops", "--json", "--vault", str(config.vault_path)])
    result = json.loads(capsys.readouterr().out)
    assert result["open"][0]["ready"] == result["due"][0]["ready"] == marked.ready
    closed = loops.close(config, item.id, AT, "done", "Relayed")
    assert loops.closed_recent(config) == [closed] and closed.ready == marked.ready
    with pytest.raises(ValueError, match="no open loop"):
        loops.ready(config, item.id, AT)


def test_ready_preserves_noncanonical_details_and_other_sections(config):
    path = loops.path_for(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(loops.TEMPLATE.replace("## Open\n", "## Open\n\n"
                    "- 2026-10-28 12:00 · owed · Relay answer · [[Trip]]\n"
                    "    - due: 2026-12-01\n  - with: Kevin\n"), encoding="utf-8")
    loops.ready(config, "Relay answer", AT)
    [item] = loops.open_loops(config)
    assert (item.ready, item.due, item.with_) == ("2026-10-28 12:00", "2026-12-01", "Kevin")
    assert "## Closed\n" in path.read_text()


@pytest.mark.parametrize("by_id", [True, False])
def test_record_resolves_asks_and_marks_ready_tolerating_retries(config, by_id):
    journal.add_ask(config, AT.date(), "Which date?", "Trip")
    [ask] = needs.items(config, AT.date())
    loops.add(config, AT, "owed", "Relay the date", "Trip", "Kevin", "2026-12-01")
    [loop] = loops.open_loops(config)
    record = {"headline": "Got the date", "outcome": "nothing",
              "resolved": [ask.id if by_id else ask.text, "gone-ask-id"],
              "ready": [loop.id if by_id else loop.text, "gone-loop-id"]}
    journal.append_entry(config, record, "cli", AT)
    assert needs.items(config, AT.date()) == []
    assert loops.open_loops(config)[0].ready == "2026-10-28 12:00"
    log = config.vault_path / config.agent("Answered/2026-10.md")
    assert "Which date? · because answered in conversation" in log.read_text()
    before = snapshot(config.vault_path)
    journal.append_entry(config, record, "cli", AT + timedelta(minutes=1))
    assert snapshot(config.vault_path) == before
    loops.close(config, loop.id, AT, "done", "Delivered")
    before = snapshot(config.vault_path)
    journal.append_entry(config, record, "cli", AT)
    assert snapshot(config.vault_path) == before


def test_record_resolves_a_confirm_the_owner_confirmed(config):
    journal.add_ask(config, AT.date(), "Which date?", "Trip")
    path = config.vault_path / "CURRENT.md"
    path.write_text(path.read_text().replace("## Needs you\n", "## Needs you\n- 2026-10-28 · confirm · Booked the fare. · [[Trip]]\n", 1))
    confirm = next(item for item in needs.items(config, AT.date()) if item.kind == "confirm")
    journal.append_entry(config, {"headline": "Confirmed", "outcome": "nothing", "resolved": [confirm.id]}, "cli", AT)
    assert [item.kind for item in needs.items(config, AT.date())] == ["ask"]


def test_record_does_not_resolve_or_ready_a_partial_text_match(config):
    journal.add_ask(config, AT.date(), "Which date for Lisbon?", "Trip")
    loops.add(config, AT, "owed", "Relay the date for Lisbon", "Trip", "Kevin")
    before = snapshot(config.vault_path)
    journal.append_entry(config, {"headline": "Quiet", "outcome": "nothing", "resolved": ["Which date"],
                                 "ready": ["Relay the date"]}, "cli", AT)
    assert snapshot(config.vault_path) == before


def test_stale_lists_loops_two_days_past_due_oldest_first_and_never_a_ready_one(config):
    loops.add(config, AT, "promised", "Send the link", "Trip", "Kevin")                        # due 4 hours after AT
    loops.add(config, AT, "waiting", "Hear back on the dates", "Trip", "Kevin")               # due a day after AT
    loops.add(config, AT, "owed", "Tell Kevin the plan", "Trip", "Kevin", "2026-11-05")      # due on its date
    loops.add(config, AT, "owed", "Relay the answer", "Trip", "Kevin")
    loops.ready(config, "Relay the answer", AT)
    texts = lambda now: [item.text for item in loops.stale(config, now)]
    assert texts(AT + timedelta(days=2, hours=3)) == []
    assert texts(AT + timedelta(days=2, hours=4)) == ["Send the link"]
    assert texts(AT + timedelta(days=3)) == ["Send the link", "Hear back on the dates"]
    assert texts(datetime(2026, 11, 7, 0, 0)) == ["Send the link", "Hear back on the dates", "Tell Kevin the plan"]


def test_cli_stale_prints_only_stale_loops_and_json_carries_them(config, capsys):
    loops.add(config, AT, "promised", "Send the link", "Trip", "Kevin")
    loops.add(config, datetime.now(), "promised", "Fresh promise", "Trip", "Kevin")
    cli.main(["loops", "--stale", "--vault", str(config.vault_path)])
    out = capsys.readouterr().out
    assert "Send the link" in out and "Fresh promise" not in out
    cli.main(["loops", "--json", "--vault", str(config.vault_path)])
    assert [item["text"] for item in json.loads(capsys.readouterr().out)["stale"]] == ["Send the link"]
