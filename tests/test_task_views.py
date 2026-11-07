"""A task's view is data in the app's kit: checked here so whatever reaches the app can be drawn, kept beside the task,
and carried by the state export."""
import io
import json

import pytest

from brain import cli, state, views
from brain.config import Config
from brain.vault import Vault


@pytest.fixture(autouse=True)
def no_conversation(monkeypatch):
    monkeypatch.delenv("DEMIURGE_CONVERSATION", raising=False)
    monkeypatch.delenv("DEMIURGE_TASK", raising=False)


def view(vault, capsys, monkeypatch, spec, tid="T-001"):
    monkeypatch.setattr("sys.stdin", io.StringIO(spec if isinstance(spec, str) else json.dumps(spec)))
    cli.main(["task", tid, "view", "--vault", str(vault)])
    return capsys.readouterr().out.strip()


SHORTLIST = {"pieces": [
    {"kind": "numbers", "items": [{"label": "Seen", "value": "41"}, {"label": "Shortlisted", "value": "6", "detail": "under $3,200"}]},
    {"kind": "table", "title": "Shortlist", "columns": ["Address", "Rent"], "rows": [
        {"cells": ["12 Bedford Ave", "$3,100"], "state": "good", "link": "https://example.com/12"},
        {"cells": ["40 Kent St", "$2,950"], "state": "held"}]},
    {"kind": "progress", "title": "Neighborhoods checked", "done": 4, "total": 7},
    {"kind": "checklist", "title": "Before applying", "items": [{"text": "Guarantor letter", "state": "waiting"}]},
    {"kind": "places", "title": "On the map", "items": [{"name": "12 Bedford Ave", "query": "12 Bedford Ave Brooklyn NY"}]},
    {"kind": "links", "items": [{"label": "Search notes", "href": "memory/NYC Apartment Search"}]},
]}


def test_a_view_in_the_kit_is_kept_beside_the_task_and_exported(vault, capsys, monkeypatch):
    assert view(vault, capsys, monkeypatch, SHORTLIST) == "T-001 view saved: 6 pieces"
    exported = next(t for t in state._tasks(Vault(Config.load(vault).vault_path)) if t["id"] == "T-001")
    assert exported["view"]["pieces"][1]["rows"][0] == {"cells": ["12 Bedford Ave", "$3,100"], "state": "good", "link": "https://example.com/12"}


@pytest.mark.parametrize("bad, says", [
    ({"pieces": [{"kind": "html", "body": "<b>hi</b>"}]}, "kind must be"),
    ({"pieces": [{"kind": "table", "title": "T", "columns": ["A", "B"], "rows": [{"cells": ["only one"]}]}]}, "1 cells for 2 columns"),
    ({"pieces": [{"kind": "progress", "title": "P", "done": 5, "total": 3}]}, "between 0 and total"),
    ({"pieces": [{"kind": "links", "items": [{"label": "x", "href": "javascript:alert(1)"}]}]}, "https URL or a vault note"),
    ({"pieces": [{"kind": "checklist", "title": "C", "items": [{"text": "x", "state": "maybe"}]}]}, "done, open, waiting, blocked"),
    ({"pieces": [{"kind": "numbers", "items": [{"label": "x" * 300, "value": "1"}, {"label": "y", "value": "2"}]}]}, "at most 200"),
    ("not json", "not JSON"),
])
def test_anything_outside_the_kit_is_refused_with_the_kit_or_the_reason(vault, capsys, monkeypatch, bad, says):
    with pytest.raises(SystemExit):
        view(vault, capsys, monkeypatch, bad)
    assert says in capsys.readouterr().err
    assert views.read(Config.load(vault), "T-001") is None


def test_an_empty_view_removes_it_and_only_open_tasks_take_one(vault, capsys, monkeypatch):
    view(vault, capsys, monkeypatch, SHORTLIST)
    assert view(vault, capsys, monkeypatch, {"pieces": []}) == "T-001 view removed"
    assert views.read(Config.load(vault), "T-001") is None
    with pytest.raises(SystemExit):
        view(vault, capsys, monkeypatch, SHORTLIST, tid="T-777")
    assert "not an open task" in capsys.readouterr().err


def test_a_view_too_big_to_carry_is_refused(vault, capsys, monkeypatch):
    rows = [{"cells": ["r" * 190, "s" * 190, "t" * 190]} for _ in range(40)]
    with pytest.raises(SystemExit):
        view(vault, capsys, monkeypatch, {"pieces": [{"kind": "table", "title": "T", "columns": ["A", "B", "C"], "rows": rows}]})
    assert "the limit is 16000" in capsys.readouterr().err


V2 = {"pieces": [
    {"kind": "numbers", "from": "from Mercury, checking", "items": [{"label": "Balance", "value": "$236,443", "trend": [210, 225, 219, 236], "since": "since Oct 1"},
                                                                    {"label": "Payroll", "value": "-$18,400", "since": "since Oct 1"}]},
    {"kind": "gallery", "title": "Jackets under ¥150,000", "items": [{"image": "https://img.example/a.jpg", "label": "Needles track", "value": "¥34,100", "state": "new", "link": "https://shop.example/a"}]},
    {"kind": "steps", "title": "Order", "items": [{"label": "Ordered"}, {"label": "Shipped"}, {"label": "In Tokyo"}, {"label": "Home"}], "current": 1},
    {"kind": "schedule", "title": "Saturday", "items": [{"time": "10am", "text": "Farmers market"}, {"time": "2:30", "text": "Open studio"}]},
]}


def test_kit_v2_gallery_steps_schedule_trends_and_provenance_are_kept(vault, capsys, monkeypatch):
    assert view(vault, capsys, monkeypatch, V2) == "T-001 view saved: 4 pieces"
    kept = views.read(Config.load(vault), "T-001")["pieces"]
    assert kept[0]["from"] == "from Mercury, checking" and kept[0]["items"][0]["trend"] == [210, 225, 219, 236]
    # A trend says when it starts; a since with no trend to label is dropped.
    assert kept[0]["items"][0]["since"] == "since Oct 1" and "since" not in kept[0]["items"][1]
    assert kept[2]["current"] == 1 and kept[3]["items"][1] == {"time": "2:30", "text": "Open studio"}


@pytest.mark.parametrize("bad, says", [
    ({"pieces": [{"kind": "gallery", "title": "G", "items": [{"image": "http://x/a.jpg", "label": "a"}]}]}, "image must be an https URL"),
    ({"pieces": [{"kind": "steps", "title": "S", "items": [{"label": "a"}, {"label": "b"}], "current": 2}]}, "index of one of its 2 stages"),
    ({"pieces": [{"kind": "numbers", "items": [{"label": "a", "value": "1", "trend": [1]}, {"label": "b", "value": "2"}]}]}, "2 to 30 numbers"),
    ({"pieces": [{"kind": "schedule", "title": "S", "items": [{"time": "at some point later in the afternoon maybe", "text": "x"}]}]}, "say it as people do"),
])
def test_kit_v2_refuses_shapes_it_cannot_draw(vault, capsys, monkeypatch, bad, says):
    with pytest.raises(SystemExit):
        view(vault, capsys, monkeypatch, bad)
    assert says in capsys.readouterr().err


def test_the_owners_own_words_are_exported_when_the_source_quotes_them(vault, capsys):
    config = Config.load(vault)
    cli.main(["task", "new", "Add Ledger to the Library", "--source", "Rohan in Home, Nov 5: Yes add ledger to my library", "--vault", str(vault)])
    tid = capsys.readouterr().out.split()[0]
    exported = next(t for t in state._tasks(Vault(config.vault_path)) if t["id"] == tid)
    assert exported["said"] == "Rohan in Home, Nov 5: Yes add ledger to my library"


def test_rows_and_places_can_wait_and_places_carry_value_state_and_link(vault, capsys, monkeypatch):
    located = {"pieces": [
        {"kind": "table", "title": "Applications", "columns": ["Where", "Sent"], "rows": [
            {"cells": ["A", "Mon"], "state": "waiting"}, {"cells": ["B", "Tue"], "state": "blocked"}]},
        {"kind": "places", "title": "Apartments", "items": [
            {"name": "A", "query": "A street", "value": "$2,100", "state": "good", "link": "https://listing.example/a"},
            {"name": "B", "query": "B street"}]},
    ]}
    assert view(vault, capsys, monkeypatch, located) == "T-001 view saved: 2 pieces"
    kept = views.read(Config.load(vault), "T-001")["pieces"]
    assert [r["state"] for r in kept[0]["rows"]] == ["waiting", "blocked"]
    assert kept[1]["items"][0] == {"name": "A", "query": "A street", "value": "$2,100", "state": "good", "link": "https://listing.example/a"}
    assert kept[1]["items"][1] == {"name": "B", "query": "B street"}


def test_places_refuse_what_they_cannot_draw(vault, capsys, monkeypatch):
    with pytest.raises(SystemExit):
        view(vault, capsys, monkeypatch, {"pieces": [{"kind": "places", "title": "P", "items": [{"name": "A", "query": "q", "state": "maybe"}]}]})
    assert "state must be one of" in capsys.readouterr().err
