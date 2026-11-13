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
    journal.add_ask(config, config.today(), "Book the hotel?", "Trip", why="The booking needs your choice; yes reserves it, no leaves it open.")
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


def test_asks_confirms_and_ideas_are_written_with_when_they_act(config):
    journal.add_ask(config, TODAY, "Book the hotel?", "Trip", why="The booking needs your choice; yes reserves it, no leaves it open.")
    journal.add_ask(config, TODAY, "Moved the dentist to Friday.", "Trip", kind="confirm")
    ideas.add(config, TODAY, "A packing list", "Trip")
    ask, confirm = needs.parse_section(fm.read(config.vault_path / "CURRENT.md")[1])
    [idea] = ideas._open(config, TODAY)
    assert (ask.wake, confirm.wake, idea.wake) == ("2026-10-31", "2026-11-02", "2026-11-11")
    assert needs.stale(config, date(2026, 10, 30)) == [] and [i.id for i in needs.stale(config, date(2026, 10, 31))] == [ask.id]
    path = config.vault_path / "CURRENT.md"
    reword(path, "  - wake: 2026-11-02", "  - wake: 2026-11-09")   # a confirm given longer by hand waits longer
    assert needs.expire_confirms(config, date(2026, 11, 8)) == []
    assert needs.expire_confirms(config, date(2026, 11, 9)) == ["Moved the dentist to Friday."]


def test_every_item_says_who_it_waits_on(config):
    loops.add(config, AT, "waiting", "Kevin's answer on the venue", "Trip", "Kevin")
    loops.add(config, AT, "promised", "Send the link", "Trip", "Kevin")
    journal.add_ask(config, TODAY, "Book the hotel?", "Trip", why="The booking needs your choice; yes reserves it, no leaves it open.")
    ideas.add(config, TODAY, "A packing list", "Trip")
    assert [loops.as_dict(loop)["on"] for loop in loops.open_loops(config)] == ["Kevin", ""]
    assert [item.on for item in needs.items(config, TODAY)] == ["owner"]
    assert [idea.on for idea in ideas._open(config, TODAY)] == ["owner"]


@pytest.mark.parametrize("bad,reason", [
    ("Ask about T-2?", "internal id"),
    ("Wait for (TR-007).", "internal id"),
    ("Advance G-1?", "internal id"),
    ("Review [[Trip|the trip]]?", "wikilink"),
    ("Review Trip.md?", "vault path"),
    ("Review People/Mira?", "vault path"),
    ("Review 2026/Plans?", "vault path"),
    ("Review 旅行/Plans?", "vault path"),
    ("Review /Trip?", "vault path"),
    ("Review ./Trip/Plans?", "vault path"),
    ("Review ./2026/11?", "vault path"),
    ("Run brain task?", "brain command"),
    ("word " * 26, "at most 25"),
])
@pytest.mark.parametrize("kind", ["ask", "idea", "loop"])
def test_owner_item_writers_refuse_internal_or_long_text(config, kind, bad, reason):
    before = {p: p.read_bytes() for p in config.vault_path.rglob("*.md")}
    with pytest.raises(ValueError, match=reason):
        if kind == "ask":
            journal.add_ask(config, TODAY, bad, "Trip", why="Yes books the hotel; no leaves it open.")
        elif kind == "idea":
            ideas.add(config, TODAY, bad, "Trip", why="You get a packing list.")
        else:
            loops.add(config, AT, "promised", bad, "Trip")
    assert {p: p.read_bytes() for p in config.vault_path.rglob("*.md")} == before


@pytest.mark.parametrize("bad,reason", [
    ("It serves T-2.", "internal id"),
    ("Read [[Trip]].", "wikilink"),
    ("Read Trip.md.", "vault path"),
    ("Read agent/Tasks.", "vault path"),
    ("Use brain loops.", "brain command"),
    ("word " * 61, "at most 60"),
])
@pytest.mark.parametrize("kind", ["ask", "idea"])
def test_owner_item_writers_refuse_internal_or_long_detail(config, kind, bad, reason):
    with pytest.raises(ValueError, match=reason):
        if kind == "ask":
            journal.add_ask(config, TODAY, "Book the hotel?", "Trip", why=bad)
        else:
            ideas.add(config, TODAY, "Want a packing list?", "Trip", why=bad)


def test_owner_item_validator_accepts_lowercase_slash_prose():
    items.validate_owner_item("Pick either/or on the pick-up/drop-off, open 24/7?", "Yes keeps it; no drops it.", kind="ask")


def test_owner_item_validator_accepts_plain_words_prices_and_caps():
    items.validate_owner_item("Book the 16th return before Thursday? The fare is held until then.",
                              "The hold on the $412 fare lapses Thursday at noon. Yes books it on your card; "
                              "no lets it lapse and I'll look again next week.", kind="ask")
    items.validate_owner_item("Want me to draft replies to your landlord's emails?",
                              "Three are waiting and they repeat themselves; I'd draft, you'd send.", kind="idea")
    items.validate_owner_item("Tell Mira which hotel has rooms.", kind="loop")
    items.validate_owner_item("word " * 25, "word " * 60, kind="ask")
    items.validate_owner_item("Use the $25/month plan with 24/7 support?", "Yes changes plans; no keeps the free one.", kind="ask")
    items.validate_owner_item("Send Mira the yes/no survey?", "Yes sends it; no keeps it unsent.", kind="ask")
    items.validate_owner_item("Draft hotel and/or flight options?", "Yes drafts the options you name; no keeps the current plan.", kind="ask")
    items.validate_owner_item("Compare AT-12 and G-4th editions?", "Yes compares them; no keeps this edition.", kind="ask")


def test_legacy_items_remain_readable_and_exportable(config, capsys):
    from brain import cli
    from conftest import snapshot
    current = config.vault_path / "CURRENT.md"
    data, body = fm.read(current)
    fm.write(current, data, body + "\n## Needs you\n- 2026-10-28 · ask · Review T-1 in [[Trip]]?\n")
    path = loops.path_for(config)
    fm.write(path, {}, "## Open\n" + loops.render(loops.Item("promised", "Read agent/Tasks.md for TR-1.",
                                                             "2026-10-28 12:00", "Trip")))
    before = snapshot(config.vault_path)
    for command in ("needs-you", "loops", "state"):
        cli.main([command, "--json", "--vault", str(config.vault_path)])
        assert capsys.readouterr().out
    assert needs.parse_section(fm.read(current)[1])[0].detail == ""
    assert loops.open_loops(config)[0].text == "Read agent/Tasks.md for TR-1."
    assert snapshot(config.vault_path) == before
