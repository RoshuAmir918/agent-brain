"""Library summaries use the task-view kit and live atomically inside existing work checkouts."""
import io
import json

import pytest

from brain import cli, fm, views
from conftest import snapshot


SUMMARY = {"pieces": [
    {"kind": "numbers", "from": "  from venue   listings ", "items": [
        {"label": " Offers ", "value": 87, "trend": [80, 85, 87], "since": "since Nov 1"},
        {"label": "Venues", "value": 46},
    ]},
    {"kind": "table", "title": " Expiring this week ", "columns": ["Venue", "Offer"], "rows": [
        {"cells": ["Café", "  Two for one\nMonday  "], "state": "new", "link": "https://example.com/offer"},
    ]},
]}


@pytest.fixture
def work(tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    folder = home / "works" / "offers"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# Offers\n")
    monkeypatch.setenv("AGENT_HOME", str(home))
    return folder


def view(vault, monkeypatch, spec, slug="offers"):
    monkeypatch.setattr("sys.stdin", io.StringIO(spec if isinstance(spec, str) else json.dumps(spec)))
    cli.main(["works", "view", slug, "--vault", str(vault)])


def test_summary_is_clean_json_in_the_work(vault, work, monkeypatch, capsys):
    view(vault, monkeypatch, SUMMARY)
    assert capsys.readouterr().out == "offers view saved: 2 pieces\n"
    text = (work / "view.json").read_text()
    assert text.endswith("\n") and "Café" in text
    clean = json.loads(text)
    assert clean["pieces"][0] == {
        "kind": "numbers", "from": "from venue listings", "items": [
            {"label": "Offers", "value": "87", "trend": [80, 85, 87], "since": "since Nov 1"},
            {"label": "Venues", "value": "46"},
        ],
    }
    assert clean["pieces"][1]["title"] == "Expiring this week"
    assert clean["pieces"][1]["rows"][0]["cells"] == ["Café", "Two for one Monday"]
    assert (work / "README.md").read_text() == "# Offers\n"
    assert sorted(p.name for p in work.iterdir()) == ["README.md", "view.json"]


@pytest.mark.parametrize("bad,reason", [
    ("not json", "not JSON"),
    ([], 'a view is {"pieces": [...]}'),
    ({"extra": []}, 'a view is {"pieces": [...]}'),
    ({"pieces": "table"}, "pieces must be a list"),
    ({"pieces": SUMMARY["pieces"] * 4}, "at most 6"),
    ({"pieces": [{"kind": "html"}]}, "kind must be"),
    ({"pieces": [{"kind": "progress", "title": "Venues", "done": 5, "total": 3}]}, "between 0 and total"),
    ({"pieces": [{"kind": "links", "items": [{"label": "x", "href": "javascript:alert(1)"}]}]}, "https URL or a vault note"),
    ({"pieces": [{"kind": "table", "title": "T", "columns": ["A", "B", "C"], "rows": [
        {"cells": ["r" * 190, "s" * 190, "t" * 190]} for _ in range(40)]}]}, "the limit is 16000"),
])
def test_invalid_summary_prints_the_kit_and_preserves_existing_files(vault, work, monkeypatch, capsys, bad, reason):
    view(vault, monkeypatch, SUMMARY)
    capsys.readouterr()
    before = snapshot(work.parent.parent)
    with pytest.raises(SystemExit) as error:
        view(vault, monkeypatch, bad)
    assert error.value.code == 1
    output = capsys.readouterr()
    assert not output.out
    assert reason in output.err and output.err.count(views.KIT) == 1
    assert snapshot(work.parent.parent) == before


@pytest.mark.parametrize("empty", [{}, {"pieces": []}])
def test_empty_summary_removes_only_the_view_and_can_be_repeated(vault, work, monkeypatch, capsys, empty):
    view(vault, monkeypatch, SUMMARY)
    capsys.readouterr()
    for _ in range(2):
        view(vault, monkeypatch, empty)
        assert capsys.readouterr().out == "offers view removed\n"
        assert list(work.iterdir()) == [work / "README.md"]


@pytest.mark.parametrize("spec", [SUMMARY, {}])
@pytest.mark.parametrize("file_instead", [False, True])
def test_work_folder_must_already_exist(vault, work, monkeypatch, capsys, spec, file_instead):
    if file_instead:
        (work.parent / "missing").write_text("not a folder")
    before = snapshot(work.parent.parent)
    with pytest.raises(SystemExit) as error:
        view(vault, monkeypatch, spec, "missing")
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "work folder does not exist" in output.err and "demiurge works new" in output.err
    assert views.KIT in output.err
    assert snapshot(work.parent.parent) == before


@pytest.mark.parametrize("slug", ["../offers", "Offers", "bad_slug", "two words", "", "/offers"])
def test_invalid_slug_cannot_write_outside_a_work(vault, work, monkeypatch, capsys, slug):
    before = snapshot(work.parent.parent)
    with pytest.raises(SystemExit) as error:
        view(vault, monkeypatch, SUMMARY, slug)
    assert error.value.code == 1
    assert "work slug must match [a-z0-9-]+" in capsys.readouterr().err
    assert snapshot(work.parent.parent) == before


def test_summary_needs_an_agent_home(vault, work, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_HOME")
    with pytest.raises(SystemExit) as error:
        view(vault, monkeypatch, SUMMARY)
    assert error.value.code == 1
    assert "AGENT_HOME is not set" in capsys.readouterr().err
    assert not (work / "view.json").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_replace_is_atomic_and_a_failure_preserves_the_old_summary(vault, work, monkeypatch, capsys, fail):
    view(vault, monkeypatch, SUMMARY)
    capsys.readouterr()
    path = work / "view.json"
    before = path.read_bytes()
    replace = fm.os.replace
    replaced = []

    def checked_replace(source, target):
        assert target == path and source.parent == work
        assert path.read_bytes() == before
        assert json.loads(source.read_text())["pieces"][0]["title"] == "Updated"
        replaced.append(target)
        if fail:
            raise OSError("interrupted summary replace")
        replace(source, target)

    monkeypatch.setattr(fm.os, "replace", checked_replace)
    updated = {"pieces": [{"kind": "progress", "title": "Updated", "done": 2, "total": 3}]}
    if fail:
        with pytest.raises(SystemExit) as error:
            view(vault, monkeypatch, updated)
        assert error.value.code == 1
        assert "interrupted summary replace" in capsys.readouterr().err
        assert path.read_bytes() == before
    else:
        view(vault, monkeypatch, updated)
        assert capsys.readouterr().out == "offers view saved: 1 pieces\n"
        assert json.loads(path.read_text()) == updated
    assert replaced == [path]
    assert sorted(p.name for p in work.iterdir()) == ["README.md", "view.json"]
