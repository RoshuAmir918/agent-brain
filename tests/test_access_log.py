"""Access receipts, speaker resolution, and the owner's computed follow-ups."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timezone

import pytest

from brain import access_log, fm, journal, needs, nightly, state, validate
from brain.config import Config
from brain.vault import Vault

TODAY = date(2026, 10, 29)
AT = datetime(2026, 10, 29, 14, 5)
PERSON = "People/Kevin_Pak/Profile.md"
GROUP = "People/Groups/Close friends.md"
HEADER = "| id | audience | subject | tell | do |\n| --- | --- | --- | --- | --- |\n"


@pytest.fixture
def config(vault):
    with (vault.parent / "brain.toml").open("a") as stream:
        stream.write('people_dir = "People"\nowner_user_ids = ["demiurge:OWNER", "slack:U_OWNER", "bare-owner"]\n')
    for rel, body in {
        PERSON: "# Kevin Pak\nDemiurge: k57kevin\nSlack: U_KEVIN\n",
        GROUP: f"# Close friends\n## Members\n- [[{PERSON}]]\n",
        "People/Disclosure.md": HEADER + "\n## Declined requests\n- Keep this legacy refusal.\n",
    }.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"kind": "group" if rel == GROUP else "hub", "tier": "warm"}, body)
    return Config.load(vault)


def request(**overrides):
    return {"subject": "schedule", "asked": "what you have on Thursday", "outcome": "declined", "rule": None,
            **overrides}


def append(config, at=AT, **overrides):
    return access_log.append(config, [request(**overrides)], "demiurge", at, user_id="k57kevin")


def access_items(config, today=TODAY):
    return [item for item in needs.items(config, today) if item.origin == "access"]


def test_known_person_stranger_and_round_trip(config):
    path = append(config)
    access_log.append(config, [request(outcome="asked owner")], "slack", AT, user_id="U_UNKNOWN")
    assert path == config.vault_path / "People/Access/2026-10.md"
    data, body = fm.read(path)
    assert data == {"kind": "log", "tier": "cold"}
    lines = body.strip().splitlines()
    assert lines == [
        f"- 2026-10-29 14:05 · [[memory/{PERSON}]] · schedule · declined · default · what you have on Thursday",
        "- 2026-10-29 14:05 · stranger (slack U_UNKNOWN) · schedule · asked owner · default · what you have on Thursday",
    ]
    entries = access_log.parse(body)
    assert [access_log.render(entry) for entry in entries] == lines
    assert entries[0] == access_log.AccessEntry("2026-10-29 14:05", f"[[memory/{PERSON}]]", "schedule",
                                               "declined", None, "what you have on Thursday")
    assert all(entry.source == "People/Access/2026-10.md" for entry in access_log.recent(config, TODAY, 14))
    vault = Vault(config.vault_path)
    assert vault.kind_for("People/Access/2026-10.md") == "log"
    assert validate.kinds(vault) == []


@pytest.mark.parametrize("platform,user_id,user_id_alt", [
    ("cli", "", ""), ("demiurge", "OWNER", ""), ("slack", "U_OWNER", ""),
    ("slack", "other", "U_OWNER"), ("unknown", "bare-owner", ""),
])
def test_owner_conversations_keep_no_access(config, platform, user_id, user_id_alt):
    journal.append_entry(config, {"headline": "Asked about plans.", "outcome": "done", "access": [request()]},
                         platform, AT, user_id=user_id, user_id_alt=user_id_alt)
    assert not (config.vault_path / "People/Access").exists()


def test_platform_qualified_owner_id_does_not_hide_another_speaker(config):
    access_log.append(config, [request()], "slack", AT, user_id="OWNER")
    [entry] = access_log.recent(config, TODAY, 14)
    assert entry.person == "stranger (slack OWNER)"


def test_alternate_id_resolves_a_person_and_ambiguity_is_a_stranger(config):
    access_log.append(config, [request()], "slack", AT, user_id="other", user_id_alt="U_KEVIN")
    other = config.vault_path / "People/Other/Profile.md"
    other.parent.mkdir()
    fm.write(other, {}, "Slack: U_KEVIN\n")
    access_log.append(config, [request()], "slack", AT, user_id="U_KEVIN")
    known, ambiguous = access_log.recent(config, TODAY, 14)
    assert known.person == f"[[memory/{PERSON}]]"
    assert ambiguous.person == "stranger (slack U_KEVIN)"


@pytest.mark.parametrize("entry", [
    request(subject="not-a-subject"), request(subject=[]), request(outcome="allowed"), request(outcome=None),
    request(asked="x" * 121), request(asked="  "), request(asked=None), request(rule=[]),
    {"subject": "schedule", "asked": "plans", "outcome": "declined"}, "not an entry", None,
])
def test_invalid_entries_are_dropped_without_failing_the_record(config, entry):
    journal.append_entry(config, {"headline": "Conversation recorded.", "outcome": "done", "access": [entry]},
                         "demiurge", AT, user_id="k57kevin")
    assert not (config.vault_path / "People/Access").exists()
    assert len(journal.recent(config, TODAY)) == 1


def test_custom_subject_rules_limits_and_single_line_cleaning(config):
    disclosure = config.vault_path / "People/Disclosure.md"
    fm.write(disclosure, {}, HEADER + "| D-001 | known | pets | gist | no |\n\n## Subjects\n- pets: animals\n")
    entries = [request(subject="pets", outcome=outcome, asked="cats · or\n dogs | tomorrow", rule="D-001")
               for outcome in access_log.OUTCOMES]
    access_log.append(config, entries + [request()], "demiurge", AT, user_id="k57kevin")
    parsed = access_log.recent(config, TODAY, 14)
    assert len(parsed) == 5
    assert [entry.outcome for entry in parsed] == list(access_log.OUTCOMES)
    assert all(entry.subject == "pets" and entry.rule == "D-001" and entry.asked == "cats - or dogs | tomorrow"
               for entry in parsed)
    append(config, rule="invented")
    assert access_log.recent(config, TODAY, 14)[-1].rule is None


@pytest.mark.parametrize("text", [
    "ordinary prose", "- 2026-02-30 14:05 · stranger (slack U1) · work · declined · default · job",
    "- 2026-10-29 14:05 · Kevin · work · declined · default · job",
    "- 2026-10-29 14:05 · stranger (slack U1) · work · allowed · default · job",
    "- 2026-10-29 14:05 · stranger (slack U1) · work · declined · default · job · extra",
])
def test_malformed_lines_are_ignored(text):
    assert access_log.parse(text) == []


def test_recent_crosses_months_and_filters_calendar_window(config):
    for when in ("2026-10-20 14:05", "2026-10-21 14:05", "2026-10-31 14:05", "2026-11-03 14:05", "2026-11-04 14:05"):
        append(config, datetime.strptime(when, "%Y-%m-%d %H:%M"))
    assert [entry.when for entry in access_log.recent(config, date(2026, 11, 3), 14)] == [
        "2026-10-21 14:05", "2026-10-31 14:05", "2026-11-03 14:05"]
    assert access_log.recent(config, TODAY, 0) == []
    assert access_log.recent(config, TODAY, -1) == []


def test_local_timezone_and_append_to_a_file_without_final_newline(config):
    append(config)
    path = access_log.path_for(config, "2026-10")
    path.write_text(path.read_text().rstrip("\n"))
    append(config, datetime(2026, 11, 1, 1, 5, tzinfo=timezone.utc))
    assert [entry.when for entry in access_log.parse(path.read_text())] == ["2026-10-29 14:05", "2026-10-31 18:05"]


def test_concurrent_sessions_keep_every_entry(config):
    with ThreadPoolExecutor(max_workers=6) as workers:
        pending = [workers.submit(append, config, asked=f"request {index}") for index in range(6)]
        for future in pending:
            future.result()
    entries = access_log.recent(config, TODAY, 14)
    assert sorted(entry.asked for entry in entries) == [f"request {index}" for index in range(6)]


def test_declined_friend_becomes_an_ask_without_touching_legacy_disclosure(config):
    disclosure = config.vault_path / "People/Disclosure.md"
    before = disclosure.read_bytes()
    append(config)
    [item] = access_items(config)
    assert (item.kind, item.text, item.since, item.source) == (
        "ask", "Kevin Pak asked what you have on Thursday. Let Close friends see your schedule from now on?",
        "2026-10-29", "People/Access/2026-10.md")
    assert access_items(Vault(config.vault_path)) == [item]
    assert disclosure.read_bytes() == before


@pytest.mark.parametrize("audience,subject,tell,do", [
    (f"[[{GROUP}]]", "schedule", "none", ""), (f"[[{GROUP}]]", "schedule", "details", "ask"),
    (f"[[{PERSON}]]", "schedule", "none", "no"), (f"[[{GROUP}]]", "anything", "none", ""),
    (f"[[{PERSON}]]", "schedule", "", "no"), (f"[[{PERSON}]]", "schedule", "", ""),
])
def test_person_or_group_rules_settle_the_ask_at_any_level(config, audience, subject, tell, do):
    append(config)
    assert len(access_items(config)) == 1
    fm.write(config.vault_path / "People/Disclosure.md", {},
             HEADER + f"| D-001 | {audience} | {subject} | {tell} | {do} |\n")
    assert access_items(config) == []


@pytest.mark.parametrize("audience,subject", [("known", "schedule"), ("anyone", "schedule"),
                                              (f"[[{GROUP}]]", "work")])
def test_broad_or_other_subject_rules_do_not_settle_the_ask(config, audience, subject):
    fm.write(config.vault_path / "People/Disclosure.md", {},
             HEADER + f"| D-001 | {audience} | {subject} | none | no |\n")
    append(config, rule="D-001")
    assert len(access_items(config)) == 1


def test_deduplication_since_and_stable_ids_across_changed_text_and_months(config):
    append(config, datetime(2026, 11, 1, 12), asked="your plans tomorrow")
    append(config, datetime(2026, 10, 22, 12))
    append(config, datetime(2026, 11, 2, 12), subject="health", asked="how you are")
    schedule, health = access_items(config, date(2026, 11, 4))
    assert schedule.since == "2026-10-22" and health.since == "2026-11-02"
    assert schedule.source == "People/Access/2026-10.md"
    later = access_items(config, date(2026, 11, 5))[0]
    assert later.since == "2026-11-01" and later.source == "People/Access/2026-11.md"
    assert later.text != schedule.text and later.id == schedule.id
    assert health.id != schedule.id


def test_strangers_non_declines_old_and_future_requests_are_log_only(config):
    access_log.append(config, [request()], "slack", AT, user_id="U_UNKNOWN")
    access_log.append(config, [request()], "slack", AT, user_id=f"[[memory/{PERSON}]]")
    for outcome in ("told gist", "told details", "asked owner", "did"):
        append(config, outcome=outcome)
    append(config, datetime(2026, 10, 15, 14, 5))
    append(config, datetime(2026, 10, 30, 14, 5))
    assert access_items(config) == []


def test_no_group_uses_them(config):
    (config.vault_path / GROUP).unlink()
    append(config)
    assert access_items(config)[0].text.endswith("Let them see your schedule from now on?")


def test_cli_journal_passes_identity_and_state_exports_the_access_item(config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"headline": "Declined a schedule request.", "outcome": "progress", "access": [request()]})))
    nightly.main(["journal", "append", "--vault", str(config.vault_path), "--platform", "slack",
                  "--user-id", "other", "--user-id-alt", "U_KEVIN", "--at", "2026-10-29 14:05"])
    [entry] = access_log.recent(config, TODAY, 14)
    assert entry.person == f"[[memory/{PERSON}]]"
    [item] = access_items(config)
    snapshot = state.build(config, TODAY)
    assert [value for value in snapshot["needsYou"] if value["origin"] == "access"] == [asdict(item)]
    assert not any(hub["source"].startswith("People/") for hub in snapshot["hubs"])
    assert state._source(Vault(config.vault_path), PERSON) is None
    assert capsys.readouterr().out.strip().endswith("agent/Journal/2026-10.md")



def test_a_quiet_conversation_logs_its_requests_but_keeps_no_journal_row(config):
    record = {"outcome": "nothing", "headline": "Small talk with Kevin", "access": [request()]}
    path = journal.append_entry(config, record, "demiurge", AT, user_id="k57kevin")
    assert not path.exists()
    assert [entry.subject for entry in access_log.recent(config, TODAY, 1)] == ["schedule"]
