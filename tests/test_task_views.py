"""A task's view is data in the app's kit: checked here so whatever reaches the app can be drawn, kept beside the task,
and carried by the state export."""
import io
import json

import pytest

from brain import nightly, state, views
from brain.config import Config
from brain.vault import Vault


@pytest.fixture(autouse=True)
def no_conversation(monkeypatch):
    monkeypatch.delenv("DEMIURGE_CONVERSATION", raising=False)
    monkeypatch.delenv("DEMIURGE_TASK", raising=False)


def view(vault, capsys, monkeypatch, spec, tid="T-001"):
    monkeypatch.setattr("sys.stdin", io.StringIO(spec if isinstance(spec, str) else json.dumps(spec)))
    nightly.main(["task", tid, "view", "--vault", str(vault)])
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
