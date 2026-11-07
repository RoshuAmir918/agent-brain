from datetime import date

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
    message(root, id="p1", **{"from": "Alice <alice@example.com>", "subject": "Private plans"})
    rows = mail.extract(ctx(vault))
    rendered = "\n".join(evidence.render(row) for row in rows)
    kim = next(row for row in rows if row.verb == "talks")
    assert (kim.fact, kim.date, kim.hints) == ("Wrote to Kim Lee 2 times, last 2026-11-04", TODAY, ("people",))
    resy = next(row for row in rows if "resy.com" in row.fact)
    assert resy.fact == "1 notice from resy.com, last 2026-11-04"
    assert (resy.verb, resy.hints, resy.whose) == ("booked", ("food & drink",), "own")
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
    assert (climbing.verb, climbing.date, climbing.hints) == ("went", date(2026, 11, 3), ("play",))
    assert (dinner.verb, dinner.date, dinner.hints) == ("plans", date(2026, 11, 6), ("food & drink",))


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
