from datetime import date

import pytest

from brain import cli, evidence, nightly, sources
from brain.config import Config
from brain.sources import calendar, mail
from test_channel_files import calendar_file, event, message, synced  # noqa: F401

TODAY = date(2026, 11, 4)


def ctx(vault):
    return sources.Context(TODAY, config=Config.load(vault))


def test_mail_rows_are_the_owners_side_by_headers_only(synced):
    vault, root = synced
    message(root, id="s1", labels=["SENT"], **{"from": "mira@example.com", "to": "Kim Lee <kim@example.com>",
                                                "subject": "your code is 123456"})
    message(root, id="s2", day="2026-11-02", labels=["SENT"], date="Mon, 2 Nov 2026 09:00:00 -0800",
            **{"from": "mira@example.com", "to": "kim@example.com"})
    message(root, id="r1", labels=["CATEGORY_UPDATES"], **{"from": "Resy <no-reply@resy.com>", "subject": "Table for two"})
    message(root, id="r2", day="2026-11-01", labels=["CATEGORY_UPDATES"], date="Sun, 1 Nov 2026 09:00:00 -0800",
            **{"from": "Resy <reservations@email.resy.com>", "subject": "Reminder"})   # another address, the same sender
    message(root, id="v1", labels=["CATEGORY_UPDATES"], **{"from": "Venmo <venmo@venmo.com>", "subject": "You paid"})
    message(root, id="v2", day="2026-11-03", labels=["CATEGORY_UPDATES"], date="Tue, 3 Nov 2026 09:00:00 -0800",
            **{"from": "Venmo <venmo@venmo.com>", "subject": "You paid"})
    message(root, id="o1", labels=["CATEGORY_UPDATES"], **{"from": "Welcome <hi@once.example.org>", "subject": "Welcome"})
    message(root, id="p1", **{"from": "Alice <alice@example.com>", "subject": "Private plans"})
    rows = mail.extract(ctx(vault))
    rendered = "\n".join(evidence.render(row) for row in rows)
    kim = next(row for row in rows if row.verb == "talks")
    # example.com is not a consumer mail service, so the fixture's accounts read as work accounts
    assert (kim.fact, kim.date, kim.hints) == ("Wrote to Kim Lee 2 times, last 2026-11-04", TODAY, ("people", "work"))
    resy = next(row for row in rows if "resy.com" in row.fact)
    assert resy.fact == "2 notices from resy.com, last 2026-11-04"
    assert (resy.verb, resy.hints, resy.whose) == ("booked", ("food & drink", "work"), "own")
    assert next(row for row in rows if "venmo.com" in row.fact).hints[0] == "money"   # said-only: never judged
    assert not any("example.org" in row.fact for row in rows)   # heard from once: a sign-up, not a part of their life
    assert "123456" not in rendered and "Table for two" not in rendered and "BODY_MUST_NEVER_APPEAR" not in rendered
    assert "alice" not in rendered.lower() and "kim@example.com" not in rendered


def test_calendar_rows_say_went_or_plans_with_where_and_how_often(synced):
    vault, root = synced
    calendar_file(root, event("Climbing", "2026-11-03 14:00", "2026-11-03 15:00", "series", location="Boulder Barn")
                  + event("Climbing", "2026-11-10 14:00", "2026-11-10 15:00", "series", location="Boulder Barn")
                  + event("Dinner with Kim", "2026-11-06 19:00", "2026-11-06 21:00", "dinner", location=""))
    rows = {row.fact.split(",")[0].split(" at ")[0]: row for row in calendar.extract(ctx(vault))}
    climbing, dinner = rows["Climbing"], rows["Dinner with Kim"]
    assert climbing.fact == "Climbing at Boulder Barn, 2 times, last 2026-11-03, next 2026-11-10"
    assert (climbing.verb, climbing.date, climbing.hints) == ("went", date(2026, 11, 3), ("play", "work"))
    assert (dinner.verb, dinner.date, dinner.hints) == ("plans", date(2026, 11, 6), ("food & drink", "work"))


def test_the_night_extracts_runtime_sources_once_and_a_preview_keeps_none(synced, tmp_path, monkeypatch):
    vault, root = synced
    calendar_file(root, event("Climbing", "2026-11-03 14:00", "2026-11-03 15:00", "series"))
    message(root, id="s1", labels=["SENT"], **{"from": "mira@example.com", "to": "kim@example.com"})
    cli.main(["nightly", "--vault", str(vault), "--date", "2026-11-04"])   # a preview
    assert evidence.read() == []
    first = nightly.run(vault, TODAY)
    assert any(line.startswith("evidence: calendar: 1 new") for line in first)
    assert any(line.startswith("evidence: mail: 1 new") for line in first)
    assert not any(line.startswith("evidence:") for line in nightly.run(vault, TODAY))   # nothing new, nothing said
    assert {row.source for row in evidence.read()} == {"calendar", "mail"}


def test_nothing_connected_is_quiet(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from brain.vault import Vault
    assert sources.nightly(Vault(vault), TODAY) == []
    assert set(sources.registry()) >= {"imessage", "mail", "calendar"}


def test_the_registry_declares_every_source_once_and_coverage_lists_an_editions_own(vault, capsys, monkeypatch):
    from brain import coverage
    from brain.vault import Vault
    found = sources.registry()
    assert {"site", "reviews", "plans", "crm", "mail", "calendar", "imessage"} <= set(found)
    assert found["site"].extract is None and found["mail"].extract is not None and found["crm"].source.needs == "crm"
    personal = [source.id for source in sources.declared("personal")]
    assert personal == ["mail", "calendar", "imessage"]
    org = sources.declared("enterprise")
    assert [source.id for source in org][:2] == ["site", "writing"] and next(s for s in org if s.id == "mail").where == "internal"
    data = coverage.coverage(Vault(vault), TODAY)
    assert [(s["source"], s["where"], s["needs"]) for s in data["sources"]] == [
        ("mail", "own", "google"), ("calendar", "own", "google"), ("imessage", "own", "")]
    with pytest.raises(SystemExit, match="the agent reads this one with its own tools"):
        cli.main(["source", "site", "extract", "--home", str(vault.parent / "home"), "--vault", str(vault)])
    cli.main(["source", "list", "--json", "--vault", str(vault)])
    listed = {entry["id"]: entry for entry in __import__("json").loads(capsys.readouterr().out)}
    assert set(listed) == {"mail", "calendar", "imessage"}   # a person's brain lists a person's sources
    assert listed["mail"]["needs"] == "google" and listed["mail"]["extracts"] is True


def test_a_source_declared_twice_is_refused(monkeypatch):
    from brain.sources import company
    monkeypatch.setattr(company, "SOURCES", (*company.SOURCES, company.SOURCES[0]))
    with pytest.raises(ValueError, match="'site' is declared twice"):
        sources.registry()


def test_a_correspondent_goes_by_the_newest_name_the_owner_used(synced):
    vault, root = synced
    message(root, id="o1", day="2026-11-01", labels=["SENT"], date="Sun, 1 Nov 2026 09:00:00 -0800",
            **{"from": "mira@example.com", "to": "Old Name <alex@example.com>"})
    message(root, id="o2", labels=["SENT"], **{"from": "mira@example.com", "to": "New Name <alex@example.com>"})
    message(root, id="o3", day="2026-11-03", labels=["SENT"], date="Tue, 3 Nov 2026 09:00:00 -0800",
            **{"from": "mira@example.com", "to": "alex@example.com"})
    row, = [row for row in mail.extract(ctx(vault)) if row.verb == "talks"]
    assert row.fact == "Wrote to New Name 3 times, last 2026-11-04"


def test_the_reading_list_reports_problems_account_by_account(synced):
    from brain import seed
    from brain.vault import Vault
    vault, root = synced
    bad = calendar_file(root, "")
    bad.write_bytes(b"\xff\xfe not text")
    message(root, account="other@example.com", date="not a date")
    problems = seed.personal(Vault(vault), TODAY)["problems"]
    assert [p.split(": ", 1)[1] for p in problems] == ["unreadable calendar", "unreadable mail headers (date, from, to, cc, subject, labels)"]
    assert "mira@example.com" in problems[0] and "other@example.com" in problems[1]


def test_where_a_sender_or_an_event_belongs():
    from brain.sources import hinting
    assert [hinting.domain(host) for host in ("email.venmo.com", "venmo.com", "mail.example-books.co.uk", "alerts.example-monitor.com")] == \
        ["venmo.com", "venmo.com", "example-books.co.uk", "example-monitor.com"]
    assert hinting.work_account("sam@studio-example.com") and not hinting.work_account("sam.rivera@gmail.com")
    assert hinting.hints("Reservation at a corner steakhouse", "home & routine") == ("food & drink",)
    assert hinting.hints("Atlas weekly product meeting", "home & routine") == ("work",)
    assert hinting.hints("linear.app", "shopping") == ("work",) and hinting.hints("example-bags.com", "shopping") == ("shopping",)
    assert hinting.hints("Stay at a suites hotel downtown", "home & routine") == ("places",)
    assert hinting.hints("delta.com", "shopping") == ("places",) and hinting.hints("bigdataa.com", "shopping") == ("shopping",)
