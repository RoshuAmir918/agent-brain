"""The shared item grammar: stored ids survive rewording, the stamp keeps every existing id, removal takes the whole block."""
from datetime import date, datetime

import pytest

from brain import fm, ideas, items, journal, loops, needs
from brain.config import Config

AT = datetime(2026, 10, 28, 12, 0)
TODAY = date(2026, 10, 28)


@pytest.fixture
def config(vault):
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm"}, "# Trip\n")
    return Config.load(vault)


def reword(path, old, new):
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_a_reworded_loop_keeps_its_id_and_closes_whole(config):
    loops.add(config, AT, "waiting", "Kevin's answer on the venue", "Trip", "Kevin")
    [before] = loops.open_loops(config)
    reword(loops.path_for(config), "the venue", "the venue· for Saturday")
    path = loops.path_for(config)
    path.write_text(path.read_text().replace("  - with: Kevin\n", "  - with: Kevin\n    wrapped note the agent left\n"))
    [after] = loops.open_loops(config)
    assert (after.id, after.text) == (before.id, "Kevin's answer on the venue· for Saturday")
    loops.close(config, before.id, AT, "done", "he answered")
    body = fm.read(path)[1]
    assert loops.open_loops(config) == [] and "wrapped note" not in body
    entry = (f"- 2026-10-28 12:00 · loop done · waiting · Kevin's answer on the venue- for Saturday"
             f" · because he answered · id:{before.id} · [[Trip]]")
    assert entry in (config.vault_path / config.agent("Answered/2026-10.md")).read_text()
    assert loops.closed_recent(config)[0].id == before.id
    assert "## Closed" not in body and before.id not in body


def test_stamp_stores_the_id_each_item_already_has(config):
    current = config.vault_path / "CURRENT.md"
    data, body = fm.read(current)
    fm.write(current, data, body + "\n## Needs you\n\n- 2026-10-27 · ask · Book the hotel? · [[Trip]]\n  - why: Prices rise Friday.\n")
    path = config.vault_path / config.agent("Ideas.md")
    fm.write(path, {}, "# Ideas\n\n## Open\n\n- 2026-10-27 · A packing list · [[Trip]]\n")
    [ask], [idea] = needs.parse_section(fm.read(current)[1]), ideas.parse_section(fm.read(path)[1])
    assert items.stamp(config) == 2
    assert f"  - why: Prices rise Friday.\n  - id: {ask.id}\n" in current.read_text()
    assert f"- 2026-10-27 · A packing list · [[Trip]]\n  - id: {idea.id}\n" in path.read_text()
    assert items.stamp(config) == 0
    reword(current, "Book the hotel?", "Book the hotel by Friday?")
    assert [item.id for item in needs.parse_section(fm.read(current)[1])] == [ask.id]


def test_writers_store_the_id_they_derive(config):
    journal.add_ask(config, TODAY, "Book the hotel?", "Trip", why="Prices rise Friday.")
    ideas.add(config, TODAY, "A packing list", "Trip")
    assert items.stamp(config) == 0
    [ask] = needs.parse_section(fm.read(config.vault_path / "CURRENT.md")[1])
    assert ask.id == needs.Item("ask", "Book the hotel?", "current", TODAY.isoformat(), "Trip").id


def test_pick_prefers_exact_words_over_a_substring():
    rows = [loops.Item("promised", text, "2026-10-28 12:00", "Trip") for text in ("Send the link", "Send the link to Kevin")]
    assert items.pick(rows, "send the link", "loop", "brain loops --json") is rows[0]
    with pytest.raises(ValueError, match="2 open loops match"):
        items.pick(rows, "the link", "loop", "brain loops --json")


def test_one_answer_settles_whatever_the_item_is(config):
    loops.add(config, AT, "waiting", "Kevin's answer on the venue", "Trip", "Kevin")
    journal.add_ask(config, config.today(), "Book the hotel?", "Trip")
    ideas.add(config, config.today(), "A packing list", "Trip")
    [loop], [ask], [idea] = (loops.open_loops(config), needs.parse_section(fm.read(config.vault_path / "CURRENT.md")[1]),
                             ideas._open(config, config.today()))
    with pytest.raises(ValueError, match="a loop's answer is one of drop"):
        items.answer(config, loop.id, "yes")
    items.answer(config, loop.id, "drop")
    items.answer(config, ask.id, "no")
    items.answer(config, idea.id, "not-now")
    assert loops.open_loops(config) == [] and loops.closed_recent(config)[0].outcome == "dropped"
    assert needs.parse_section(fm.read(config.vault_path / "CURRENT.md")[1]) == []
    assert [item.id for item in ideas.items(config, config.today())] == []   # snoozed, not gone
    with pytest.raises(ValueError, match="no open item"):
        items.answer(config, ask.id, "no")
