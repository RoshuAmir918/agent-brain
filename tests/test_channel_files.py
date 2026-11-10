import json
from datetime import datetime

import pytest

from brain import channel_files, channels, cli, compile_context, fm
from brain.config import Config
from brain.vault import Vault


@pytest.fixture
def synced(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-04")
    rows = [
        ("Primary", "owner", "Mira <mira@example.com>", "$AGENT_HOME/channels/wrong@example.com/", "live"),
        ("Other", "owner", "Mira", "`$AGENT_HOME/channels/other@example.com/`", "live"),
        ("Duplicate", "owner", "mira@example.com", "", "live"),
        ("Missing", "owner", "missing@example.com", "", "live"),
        ("Agent", "own", "echo@example.com", "", "live"),
        ("Shared", "shared", "friend@example.com", "", "live"),
        ("Pending", "owner", "pending@example.com", "", "pending"),
        ("Revoked", "owner", "revoked@example.com", "", "revoked"),
    ]
    table = channels.HEADER + "\n" + "\n".join(channels.Channel(name, whose, identity, reach, "", "", "", status).render()
                                               for name, whose, identity, reach, status in rows)
    fm.write(vault / "agent/Channels.md", {"kind": "orientation"}, table)
    sync_job(tmp_path / "agent-home", Config.load(vault).now().isoformat())
    return vault, tmp_path / "agent-home/channels"


def sync_job(home, last_run_at, status="ok", error=None):
    (home / "harness").mkdir(parents=True, exist_ok=True)
    (home / "harness/jobs.json").write_text(json.dumps({"jobs": [{"name": "demiurge-sync", "script": "demiurge-sync.sh",
                                                               "lastRunAt": last_run_at, "lastStatus": status,
                                                               "lastError": error}]}), encoding="utf-8")


def event(title="Lunch", start="2026-11-04 14:00", end="2026-11-04 15:00", id="lunch", location="Cafe, Fresno", attendee="mira@example.com (you): accepted", extra=""):
    return f"## {title}\n\ntime: {start} — {end}\nlocation: {location}\nid: {id}\nattendee: {attendee}\n{extra}\n"


def calendar_file(root, text, account="mira@example.com", day="2026-11-04"):
    path = root / account / "calendar" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Calendar: {day}\n\n{text}")
    return path


def message(root, id="m1", account="mira@example.com", day="2026-11-04", **headers):
    path = root / account / "mail" / day / f"{id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"from": "Alice <alice@example.com>", "to": account, "cc": "", "date": "Wed, 4 Nov 2026 14:00:00 -0800",
            "subject": "Lunch today?", "labels": ["INBOX", "UNREAD"], "thread": "t1", "id": id, "account": account}
    fm.write(path, data | headers, "BODY_MUST_NEVER_APPEAR\nPrivate correspondence.\n")
    return path


def test_owner_accounts_use_identity_then_reach_and_only_live_owner_rows(synced):
    vault, root = synced
    assert channel_files.owner_accounts(Vault(vault)) == ["mira@example.com", "missing@example.com", "other@example.com"]
    for account in ("mira@example.com", "other@example.com", "echo@example.com", "friend@example.com", "pending@example.com", "revoked@example.com", "wrong@example.com"):
        calendar_file(root, event(title=account, id=account), account)
        message(root, account=account)
    text = channel_files.calendar(Vault(vault))
    assert "mira@example.com" in text and "other@example.com" in text
    assert all(account not in text for account in ("echo@example.com", "friend@example.com", "pending@example.com", "revoked@example.com", "wrong@example.com"))
    data = channel_files.mail(Vault(vault), datetime(2026, 11, 4))
    # mail covers every live account in the registry, the agent's own and shared ones too; the calendar is the owner's
    assert {(item["account"], item["whose"]) for item in data["messages"]} == {
        ("mira@example.com", "owner"), ("other@example.com", "owner"), ("echo@example.com", "own"), ("friend@example.com", "shared")}


def test_calendar_parses_orders_and_deduplicates_stays_across_days_and_accounts(synced):
    vault, root = synced
    stay = event("Stay at Sonesta ES Suites Fresno", "2026-11-02 (all day)", "2026-11-05 (all day)", "stay",
                 "5322 N Diana St, Fresno, CA, 93710 United States of America")
    studio = event("Stay at Studio 6 Fresno, CA - Extended Stay", "2026-11-03 (all day)", "2026-11-10 (all day)", "studio")
    lunch = event(start="2026-11-04T14:00:00-08:00", end="2026-11-04T15:00:00-08:00")
    tomorrow = event("Tomorrow", "2026-11-05 09:00", "2026-11-05 10:00", "tomorrow")
    calendar_file(root, tomorrow + studio + stay + lunch)
    calendar_file(root, studio + tomorrow, day="2026-11-05")
    calendar_file(root, lunch + stay, account="other@example.com")
    text = channel_files.calendar(Vault(vault))
    assert text.count("Stay at Sonesta") == text.count("Stay at Studio") == text.count("Lunch") == 1
    assert text.index("Lunch") < text.index("Stay at Sonesta") < text.index("Stay at Studio") < text.index("Tomorrow")
    assert "Wed Nov 4 · 14:00–15:00 · Lunch (Cafe, Fresno) · accepted" in text
    assert "Mon Nov 2 – Wed Nov 4 · now, through Wed Nov 4 · Stay at Sonesta ES Suites Fresno (Fresno)" in text
    assert "Tue Nov 3 – Mon Nov 9 · now, through Mon Nov 9" in text
    assert "5322" not in text and "93710" not in text


def test_calendar_skips_declines_cancellations_invalid_times_and_ended_events(synced):
    vault, root = synced
    calendar_file(root, event(title="Declined self", id="d1", attendee="mira@example.com (you): declined")
                  + event(title="Declined address", id="d2", attendee="mira@example.com: declined")
                  + event(title="Cancelled", id="c", extra="cancelled: yes")
                  + event(title="Bad time", id="b", start="bad")
                  + event(title="Ended", id="e", start="2026-11-02 (all day)", end="2026-11-04 (all day)")
                  + event(title="Valid", id="v", attendee="friend@example.com: declined"))
    text = channel_files.calendar(Vault(vault))
    assert "Valid" in text
    assert all(title not in text for title in ("Declined", "Cancelled", "Bad time", "Ended"))


def test_calendar_all_day_midnight_boundaries_and_timezone(synced):
    vault, root = synced
    calendar_file(root, event("Holiday", "2026-11-04 (all day)", "2026-11-05 (all day)", "day")
                  + event("UTC call", "2026-11-05T01:00:00Z", "2026-11-05T02:00:00Z", "utc")
                  + event("Overnight", "2026-11-03 20:00", "2026-11-04 00:00", "overnight"))
    text = channel_files.calendar(Vault(vault))
    assert "Wed Nov 4 · all day · Holiday" in text
    assert "Wed Nov 4 · 17:00–18:00 · UTC call" in text
    assert "Overnight" not in text


def test_calendar_horizon_and_cap_leave_note_dates_unchanged(synced):
    vault, root = synced
    fm.write(vault / "Trip.md", {"kind": "hub"}, "A trip starts November 6.\n")
    before = compile_context.upcoming(Vault(vault)).split("\n\n## Calendar:")[0]
    calendar_file(root, event("Horizon", "2026-11-11 (all day)", "2026-11-12 (all day)", "horizon"), day="2026-11-11")
    calendar_file(root, event("Outside", "2026-11-12 (all day)", "2026-11-13 (all day)", "outside"), day="2026-11-12")
    assert "Horizon" in channel_files.calendar(Vault(vault)) and "Outside" not in channel_files.calendar(Vault(vault))
    calendar_file(root, "".join(event(title=f"Event {n:02}", id=str(n)) for n in range(20)))
    text = compile_context.upcoming(Vault(vault))
    assert text.split("\n\n## Calendar:")[0] == before
    assert text.count(" · Event ") == 12 and "Horizon" not in text


@pytest.mark.parametrize("value, expected", [
    ("5322 N Diana St, Fresno, CA, 93710 United States of America", "Fresno"),
    ("Sonesta, 5322 N Diana St, Fresno, CA 93710", "Sonesta, Fresno"),
    ("Cafe, Fresno, CA", "Cafe, Fresno"), ("Zoom", "Zoom"), ("", ""),
    ("123 Main St", ""), ("A" * 100, "A" * 60),
])
def test_location_shortening(value, expected):
    assert channel_files.short_location(value) == expected


def test_missing_sync_adds_nothing(synced, monkeypatch):
    vault, root = synced
    baseline = compile_context.upcoming(Vault(vault)).split("\n\n## Calendar:")[0]
    assert ("I can't see the calendar: nothing has synced for mira@example.com, missing@example.com, other@example.com."
            in channel_files.calendar(Vault(vault)))
    data = channel_files.mail(Vault(vault), datetime(2026, 11, 4))
    assert data["messages"] == [] and "nothing has synced for echo@example.com" in data["coverage"]["problem"]
    calendar_file(root, event())
    message(root)
    monkeypatch.delenv("AGENT_HOME")
    assert compile_context.upcoming(Vault(vault)) == baseline
    assert channel_files.mail(Vault(vault), datetime(2026, 11, 4))["messages"] == []


def test_mail_filters_categories_sent_and_cross_account_senders(synced):
    vault, root = synced
    for label in channel_files.EXCLUDED_LABELS:
        message(root, id=label, labels=["INBOX", label])
    message(root, id="sent", labels=["SENT"])
    message(root, id="self", **{"from": '"Mira" <MIRA@EXAMPLE.COM>'})
    message(root, id="other", **{"from": "Mira <other@example.com>"})
    message(root, id="missing", **{"from": "missing@example.com"})
    message(root, id="good")
    data = channel_files.mail(Vault(vault), datetime(2026, 11, 4))
    assert [item["path"].split("/")[-1] for item in data["messages"]] == ["good.md"]
    assert data["filtered"] == {"category": 5, "sent": 4, "before_since": 0, "invalid": 0}
    assert "BODY_MUST_NEVER_APPEAR" not in json.dumps(data)


def test_mail_only_reads_since_folders_and_compares_header_times(synced, monkeypatch):
    vault, root = synced
    message(root, id="old-folder", day="2026-11-02")
    message(root, id="host-day", day="2026-11-03", date="2026-11-04T18:00:00Z")   # the host's date, a day behind
    message(root, id="early", date="2026-11-04T08:00:00-08:00")
    message(root, id="equal", date="2026-11-04T17:00:00Z")
    message(root, id="late", date="2026-11-04T20:00:00-08:00")
    message(root, id="bad", date="unparseable")
    original = fm.read
    def read(path):
        assert path.parent.name != "2026-11-02"
        return original(path)
    v = Vault(vault)
    monkeypatch.setattr(fm, "read", read)
    data = channel_files.mail(v, datetime.fromisoformat("2026-11-04T17:00:00+00:00"))
    assert [item["path"].split("/")[-1] for item in data["messages"]] == ["late.md", "host-day.md", "equal.md"]
    assert data["filtered"]["before_since"] == 1 and data["filtered"]["invalid"] == 1


def test_mail_cli_text_json_cap_and_no_bodies(synced, capsys):
    vault, root = synced
    for n in range(45):
        message(root, id=f"m{n:02}", date=f"2026-11-04T14:{n:02}:00-08:00", subject="Lunch\nchanged")
    argv = ["channels", "mail", "--since", "2026-11-04", "--vault", str(vault)]
    cli.main(argv + ["--json"])
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert set(data) == {"coverage", "messages", "notices", "filtered", "omitted"}
    assert len(data["messages"]) == 40 and data["omitted"] == 5
    assert data["messages"][0]["date"] == "2026-11-04T14:44:00-08:00"
    assert set(data["messages"][0]) == {"date", "account", "whose", "from", "subject", "labels", "path"}
    assert "BODY_MUST_NEVER_APPEAR" not in raw
    cli.main(argv)
    text = capsys.readouterr().out
    assert len(text.splitlines()) == 44 and "Lunch changed" in text
    assert "40 from people · 0 notices from 0 senders · filtered: 0 category, 0 sent, 0 before since, 0 invalid · 5 over cap" in text
    assert "BODY_MUST_NEVER_APPEAR" not in text


@pytest.mark.parametrize("sender, automated", [
    ('"claude[bot]" <notifications@github.com>', True), ("GitHub <noreply@github.com>", True),
    ("Bank <do-not-reply@bank.com>", True), ("Mail Delivery <mailer-daemon@google.com>", True),
    ("Alex <alex@example.com>", False), ("Nora Reply <nora.replyman@example.com>", False),
    ("Kevin <kevin.notifier.fan@example.com>", False),
])
def test_automated_sender_is_recognized_and_a_person_is_kept(sender, automated):
    assert bool(channel_files.AUTOMATED.search(sender)) == automated


def test_notices_are_kept_and_grouped_by_sender_with_the_newest_shown(synced, capsys):
    vault, root = synced
    for n in range(5):
        message(root, id=f"gh{n}", date=f"2026-11-04T1{n}:00:00-08:00", subject=f"PR {n}",
                **{"from": '"claude[bot]" <notifications@github.com>'})
    message(root, id="ally", subject="Reminder: 11/5 is your scheduled payment date",
            labels=["CATEGORY_UPDATES", "INBOX"], **{"from": "Ally Auto <email@alert.ally.com>"})
    message(root, id="promo", labels=["CATEGORY_PROMOTIONS", "INBOX"], **{"from": "Shop <no-reply@shop.com>"})
    message(root, id="person")
    data = channel_files.mail(Vault(vault), datetime(2026, 11, 4))
    assert [item["path"].split("/")[-1] for item in data["messages"]] == ["person.md"]
    groups = {notice["from"]: notice for notice in data["notices"]}
    assert groups['"claude[bot]" <notifications@github.com>']["count"] == 5
    assert groups['"claude[bot]" <notifications@github.com>']["subject"] == "PR 4"
    assert groups["Ally Auto <email@alert.ally.com>"]["count"] == 1
    assert data["filtered"]["category"] == 1
    cli.main(["channels", "mail", "--since", "2026-11-04", "--vault", str(vault)])
    text = capsys.readouterr().out
    assert "notifications@github.com> ×5 · PR 4" in text and "1 from people · 6 notices from 2 senders" in text
    assert "BODY_MUST_NEVER_APPEAR" not in text


def test_calendar_says_when_nothing_is_on_it_or_it_cannot_be_seen(synced, tmp_path):
    vault, root = synced
    (root / "mira@example.com" / "calendar").mkdir(parents=True)
    (root / "other@example.com" / "mail").mkdir(parents=True)   # synced, but without the Calendar scope
    block = channel_files.calendar(Vault(vault))
    assert "other@example.com" not in block
    assert "Nothing on the calendar for the next 7 days." in block
    assert "- Note: nothing has synced for missing@example.com, so its calendar is not here." in block
    (tmp_path / "agent-home/harness/jobs.json").unlink()
    assert "I can't see the calendar: the mail and calendar sync is not set up." in channel_files.calendar(Vault(vault))
    path = calendar_file(root, event())
    shown = channel_files.calendar(Vault(vault))   # what synced before the job went missing is still the calendar
    assert "Lunch" in shown and "- Note: the mail and calendar sync is not set up." in shown and "can't see" not in shown
    path.unlink()
    sync_job(tmp_path / "agent-home", "2026-11-01T08:00:00-08:00")
    assert "Note: the mail and calendar sync last ran Sun Nov 1 08:00" in channel_files.calendar(Vault(vault))
    sync_job(tmp_path / "agent-home", Config.load(vault).now().isoformat(), status="error", error="401 token\nexpired")
    assert "the last mail and calendar sync failed (401 token expired)" in channel_files.calendar(Vault(vault))


def test_no_connected_account_is_said_and_a_vault_without_runtime_says_nothing(synced, monkeypatch):
    vault, root = synced
    fm.write(vault / "agent/Channels.md", {"kind": "orientation"}, channels.HEADER)
    assert "I can't see the calendar: no Google account of the owner's is connected." in channel_files.calendar(Vault(vault))
    assert channel_files.render_mail(channel_files.mail(Vault(vault), datetime(2026, 11, 4))).startswith(
        "coverage: I can't see the mail: no mail or calendar account is connected")
    monkeypatch.delenv("AGENT_HOME")
    assert channel_files.calendar(Vault(vault)) == "" and channel_files.coverage(Vault(vault)) is None


def register_school(vault, host, status="live"):
    path = vault / "agent/Channels.md"
    data, body = fm.read(path)
    row = channels.Channel("Canvas", "owner", host, f"`$AGENT_HOME/channels/{host}/`", "", "", "", status)
    fm.write(path, data, body.rstrip("\n") + "\n" + row.render() + "\n")


def canvas_school(root, host="school.instructure.com"):
    school = root / host
    (school / "courses" / "10").mkdir(parents=True, exist_ok=True)
    (school / "courses.md").write_text("# Courses\n\n## Math\n\nid: 10\n")
    (school / "courses" / "10" / "assignments.md").write_text(
        "# Assignments: Math (10)\n\n"
        "## Essay\n\nid: 1\ndue: 2026-11-06 23:59 -08:00\npoints: 20\nsubmission: unsubmitted\nscore: \ngraded: \nurl: u\n\n"
        "## Quiz\n\nid: 2\ndue: 2026-11-05 10:00 -08:00\npoints: 10\nsubmission: submitted\nscore: \ngraded: \nurl: u\n\n"
        "## Lab 3\n\nid: 3\ndue: 2026-11-01 10:00 -08:00\npoints: 20\nsubmission: graded, late\nscore: 18\ngraded: 2026-11-03 09:30 -08:00\nurl: u\n\n"
        "## Old lab\n\nid: 4\ndue: 2026-10-01 10:00 -08:00\npoints: 20\nsubmission: graded\nscore: 19\ngraded: 2026-10-02 09:30 -08:00\nurl: u\n\n"
        "## Reading log\n\nid: 5\ndue: 2026-10-30 10:00 -08:00\npoints: 5\nsubmission: unsubmitted, missing\nscore: \ngraded: \nurl: u\n\n"
        "## Far essay\n\nid: 6\ndue: 2026-11-30 10:00 -08:00\npoints: 5\nsubmission: unsubmitted\nscore: \ngraded: \nurl: u\n\n")
    return school


def announcement(school, day, id, title, posted):
    path = school / "courses" / "10" / "announcements" / day / f"{id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"title": title, "author": "Prof", "posted": posted, "course": "Math", "id": id, "url": "u"}, "BODY_MUST_NEVER_APPEAR")


def test_canvas_lists_due_missing_new_grades_and_recent_announcements(synced):
    vault, root = synced
    school = canvas_school(root)
    register_school(vault, "school.instructure.com")
    announcement(school, "2026-11-03", 7, "Midterm moved", "2026-11-03 12:00 -08:00")
    announcement(school, "2026-10-20", 8, "Old news", "2026-10-20 12:00 -08:00")
    text = channel_files.canvas(Vault(vault))
    assert text.startswith("## Canvas: due in the next 7 days, and what changed")
    assert "Fri Nov 6 23:59 · Essay (Math) · unsubmitted" in text
    assert "Quiz" not in text and "Far essay" not in text            # handed in, or beyond the window
    assert "- Missing: Reading log (Math)" in text
    assert "Graded Tue Nov 3: Lab 3 (Math) · 18/20" in text
    assert "Old lab" not in text                                    # graded long ago is not news
    assert "Announcement Tue Nov 3: Midterm moved (Math)" in text
    assert "Old news" not in text and "BODY_MUST_NEVER_APPEAR" not in text


def test_canvas_is_silent_without_a_school_and_says_so_when_nothing_is_due(synced):
    vault, root = synced
    assert channel_files.canvas(Vault(vault)) == ""
    (root / "mira@example.com").mkdir(parents=True)                  # a Google folder is not a school
    assert channel_files.canvas(Vault(vault)) == ""
    school = root / "empty.instructure.com"
    (school / "courses").mkdir(parents=True)
    (school / "courses.md").write_text("# Courses\n\nNo active courses.\n")
    assert channel_files.canvas(Vault(vault)) == ""                  # files no live row reaches are never read
    register_school(vault, "empty.instructure.com", status="revoked")
    assert channel_files.canvas(Vault(vault)) == ""
    register_school(vault, "empty.instructure.com")
    assert "Nothing due on Canvas in the next 7 days" in channel_files.canvas(Vault(vault))


def test_canvas_block_reaches_the_session_context_beside_the_calendar(synced):
    vault, root = synced
    canvas_school(root)
    register_school(vault, "school.instructure.com")
    calendar_file(root, event())
    text = compile_context.upcoming(Vault(vault))
    assert "## Calendar" in text and "## Canvas" in text


def test_calendar_reads_files_named_for_a_host_date_a_day_off(synced):
    vault, root = synced
    calendar_file(root, event(title="Late dinner", start="2026-11-04 21:00", end="2026-11-04 22:00", id="late"), day="2026-11-03")
    calendar_file(root, event(title="Horizon", start="2026-11-11 09:00", end="2026-11-11 10:00", id="edge"), day="2026-11-12")
    block = channel_files.calendar(Vault(vault))
    assert "Late dinner" in block and "Horizon" in block
