"""A matter's kept state, and what the app is given for it."""
from datetime import date, datetime

import pytest

from brain import fm, journal, loops, matters, record, state
from brain.config import Config
from brain.tasks import Task

TODAY = date(2026, 11, 11)
NOW = ("## Now\n- **Now:** flights booked; still need a place · 2026-11-10\n"
       "- **Soon:** 2026-11-20 · fly to Lisbon\n- **Last:** 2026-11-09 · talked dates with Rohan\n")


def test_state_reads_the_three_lines():
    assert matters.state("# Lisbon\n" + NOW + "## Findings\n- **Now:** not this one · 2026-11-11\n", TODAY) == {
        "now": {"text": "flights booked; still need a place", "date": "2026-11-10"},
        "soon": {"text": "fly to Lisbon", "date": "2026-11-20"},
        "last": {"text": "talked dates with Rohan", "date": "2026-11-09"}}
    assert matters.state("# Lisbon\n", TODAY) == {"now": None, "soon": None, "last": None}
    assert matters.state("## Now\n- **Now:** no date\n- **Soon:** 2026-13-40 · bad\n", TODAY)["now"] is None


def test_a_stale_now_is_given_as_last():
    body = "## Now\n- **Now:** looking at flights · 2026-10-20\n- **Last:** 2026-10-15 · first talked\n"
    assert matters.state(body, TODAY) == {"now": None, "soon": None,
                                          "last": {"text": "looking at flights", "date": "2026-10-20"}}
    later = "## Now\n- **Now:** looking at flights · 2026-10-20\n- **Last:** 2026-10-25 · booked\n"
    assert matters.state(later, TODAY)["last"] == {"text": "booked", "date": "2026-10-25"}


@pytest.mark.parametrize("body, open_work, expected", [
    (NOW, False, "current"),
    ("## Now\n- **Soon:** 2026-11-30 · exam\n", False, "soon"),
    ("## Now\n- **Soon:** 2026-12-20 · exam\n", False, "fading"),
    ("## Now\n- **Soon:** 2026-11-05 · flew\n", False, "current"),
    ("## Now\n- **Last:** 2026-10-01 · long ago\n", False, "fading"),
    ("## Now\n- **Last:** 2026-10-01 · long ago\n", True, "current"),
    ("", False, "fading"),
])
def test_phase(body, open_work, expected):
    assert matters.phase(matters.state(body, TODAY), TODAY, open_work) == expected


def test_loops_keep_their_hub_line():
    item = loops.Item("waiting", "Hear back on the flat", "2026-11-11 09:00", "Conversations", "Rohan", hub="Trips/Lisbon.md")
    rendered = loops.render(item)
    assert "\n  - hub: Trips/Lisbon.md" in rendered
    assert loops.parse_section("## Open\n" + rendered) == [item]


@pytest.fixture
def lisbon(vault):
    config = Config.load(vault)
    (vault / "Trips").mkdir()
    fm.write(vault / "Trips/Lisbon.md", {"kind": "hub"}, "# Lisbon\n" + NOW)
    fm.write(vault / "Quiet.md", {"kind": "hub"}, "# Quiet\n## Now\n- **Last:** 2026-09-01 · done\n")
    fm.write(vault / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n" + NOW)
    fm.write(vault / "agent/Tasks.md", {}, "# Tasks\n\n" + Task("T-001", "Find a flat", "- **Hub:** [[Lisbon]]\n",
                                                                 {"state": "in_progress"}).render())
    fm.write(loops.path_for(config), {}, "## Open\n\n" + "\n".join(loops.render(item) for item in [
        loops.Item("waiting", "Flat answer", "2026-11-11 09:00", "Conversations", "Rohan", hub="[[Trips/Lisbon]]"),
        loops.Item("owed", "Send the dates", "2026-11-11 09:00", "Quiet", "Rohan", hub="Trips/Lisbon.md"),
        loops.Item("waiting", "Flat deposit", "2026-11-11 09:00", "Quiet", "Rohan", task="T-001"),
        loops.Item("owed", "Pack list", "2026-11-11 09:00", "Trips/Lisbon", "Rohan"),
        loops.Item("owed", "Something else", "2026-11-11 09:00", "Quiet", "Rohan"),
    ]) + "\n")
    fm.write(vault / "CURRENT.md", {}, "# Current\n## Needs you\n- 2026-11-11 · ask · Which neighbourhood? · [[Quiet]]\n"
             "  - hub: Trips/Lisbon\n- 2026-11-11 · ask · Unrelated? · [[Quiet]]\n")
    return config


def test_state_gives_each_matter_its_state_waiting_and_phase(lisbon):
    built = state.build(lisbon, TODAY)
    hubs = {hub["title"]: hub for hub in built["hubs"]}
    ids = {item["text"]: item["id"] for item in built["loops"] + built["needsYou"]}
    lisbon_hub = hubs["Lisbon"]
    assert lisbon_hub["now"]["text"] == "flights booked; still need a place"
    assert lisbon_hub["phase"] == "current"
    assert sorted(lisbon_hub["waiting"]) == sorted(ids[text] for text in (
        "Flat answer", "Send the dates", "Flat deposit", "Pack list", "Which neighbourhood?"))
    assert sorted(hubs["Quiet"]["waiting"]) == sorted([ids["Something else"], ids["Unrelated?"]])
    assert hubs["Quiet"]["phase"] == "current"
    assert "phase" not in hubs["Watch"] and "now" not in hubs["Watch"]


def test_a_private_hub_line_is_not_exported(lisbon, vault):
    fm.write(vault / "Secret.md", {"kind": "hub", "tier": "private"}, "# Secret\n")
    fm.write(loops.path_for(lisbon), {}, "## Open\n\n" + loops.render(
        loops.Item("owed", "Hidden", "2026-11-11 09:00", "Trips/Lisbon", "Rohan", hub="Secret.md")) + "\n")
    built = state.build(lisbon, TODAY)
    assert built["loops"][0]["hub"] == ""
    assert built["loops"][0]["id"] in next(hub for hub in built["hubs"] if hub["title"] == "Lisbon")["waiting"]


AT = datetime(2026, 11, 11, 14, 5)


@pytest.fixture
def owner(vault, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    fm.write(vault / "Lisbon trip.md", {"kind": "hub", "tier": "warm"}, "# Lisbon trip\n\n## Findings\n- Alfama is hilly.\n")
    return Config.load(vault)


def talk(config, headline, about, at=AT, **fields):
    journal.append_entry(config, {"headline": headline, "outcome": "progress", "about": about, **fields}, "cli", at)


def test_a_named_matter_gets_its_now_and_last_and_owns_what_the_record_opens(owner):
    talk(owner, "Talked dates with Rohan", [{"name": "lisbon TRIP", "now": "flights booked; still need a place"}],
         loops=[{"kind": "waiting", "text": "Rohan confirms the dates", "with": "Rohan", "due": None}],
         needsYou="Want the Alfama flat or the one in Graça?")
    body = fm.read(owner.vault_path / "Lisbon trip.md")[1]
    assert body.index("## Now") < body.index("## Findings")
    assert "- **Now:** flights booked; still need a place · 2026-11-11\n- **Last:** 2026-11-11 · Talked dates with Rohan\n" in body
    assert loops.open_loops(owner)[0].hub == "Lisbon trip.md"
    assert "  - hub: Lisbon trip.md" in (owner.vault_path / "CURRENT.md").read_text()
    talk(owner, "Picked a flat", [{"name": "Lisbon trip", "now": None}], at=datetime(2026, 11, 12, 9, 0))
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 12))
    assert kept["now"]["text"] == "flights booked; still need a place"
    assert kept["last"] == {"text": "Picked a flat", "date": "2026-11-12"}


def test_a_new_name_becomes_a_matter_on_its_second_sighting(owner):
    talk(owner, "Mentioned the Ohio move", [{"name": "Ohio move", "now": "thinking about it"}])
    assert not (owner.vault_path / "Ohio move.md").exists()
    talk(owner, "Ohio move again", [{"name": "Ohio move", "now": "picked Columbus"}], at=datetime(2026, 11, 20, 9, 0))
    data, body = fm.read(owner.vault_path / "Ohio move.md")
    assert data == {"kind": "hub", "tier": "warm"}
    assert "- **Now:** picked Columbus · 2026-11-20" in body


def test_a_sighting_a_month_old_does_not_count(owner):
    talk(owner, "Mentioned a kayak", [{"name": "Kayak", "now": None}], at=datetime(2026, 9, 1, 9, 0))
    talk(owner, "Mentioned a kayak", [{"name": "Kayak", "now": None}])
    assert not (owner.vault_path / "Kayak.md").exists()


def test_a_first_sighting_that_opens_a_loop_is_a_matter_at_once(owner):
    talk(owner, "Planned Rohan's visit", [{"name": "Rohan's visit", "now": "he lands Dec 3"}],
         loops=[{"kind": "promised", "text": "Send Rohan the address", "with": "Rohan", "due": None}])
    assert (owner.vault_path / "Rohan's visit.md").exists()
    assert loops.open_loops(owner)[0].hub == "Rohan's visit.md"


def test_only_the_owner_writes_matters(owner):
    journal.append_entry(owner, {"headline": "Kevin talked about Lisbon", "outcome": "progress",
                                 "about": [{"name": "Lisbon trip", "now": "cancelled"}]}, "demiurge", AT, user_id="k1")
    assert "## Now" not in fm.read(owner.vault_path / "Lisbon trip.md")[1]


def test_a_responsibility_or_unsafe_name_is_never_a_matter(owner):
    fm.write(owner.vault_path / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n")
    talk(owner, "Checked the watch", [{"name": "Watch", "now": "fine"}, {"name": "../../etc", "now": None}],
         needsYou="Want the watch to run hourly?")
    assert "## Now" not in fm.read(owner.vault_path / "Watch.md")[1]
    assert not (owner.vault_path / ".." / "etc.md").exists()
    assert (owner.vault_path / "etc.md").exists()


def test_the_record_is_shown_the_matters_by_name(owner):
    talk(owner, "Talked dates", [{"name": "Lisbon trip", "now": "flights booked"}])
    assert "Matters in your owner's life; name these exactly in about:\n- Lisbon trip" in record.held(owner)


def test_a_first_sighting_with_a_date_is_a_matter_and_keeps_the_sooner_soon(owner):
    talk(owner, "Heard Theo is visiting", [{"name": "Theo's visit", "now": None, "soon": "2026-12-03 · Theo lands"}])
    body = fm.read(owner.vault_path / "Theo's visit.md")[1]
    assert "- **Soon:** 2026-12-03 · Theo lands" in body
    talk(owner, "Theo's return flight", [{"name": "Theo's visit", "now": None, "soon": "2026-12-07 · Theo flies home"}])
    talk(owner, "Theo's dinner", [{"name": "Theo's visit", "now": None, "soon": "2026-12-01 · book dinner"}])
    assert matters.state(fm.read(owner.vault_path / "Theo's visit.md")[1], date(2026, 11, 12))["soon"] == {
        "text": "book dinner", "date": "2026-12-01"}


def test_a_vague_ask_does_not_open_a_matter(owner):
    talk(owner, "Mentioned the Ohio move", [{"name": "Ohio move", "now": None}], needsYou="Proceed?")
    assert not (owner.vault_path / "Ohio move.md").exists()


def test_a_matter_named_by_its_path_is_found(owner, vault):
    (vault / "Trips").mkdir()
    fm.write(vault / "Trips/Lisbon.md", {"kind": "hub"}, "# Lisbon\n")
    talk(owner, "Talked Lisbon", [{"name": "Trips/Lisbon", "now": "booked"}])
    assert "**Now:** booked" in fm.read(vault / "Trips/Lisbon.md")[1]
    assert not (vault / "Trips Lisbon.md").exists()


def test_a_task_serving_a_matter_moves_its_last_and_soon(owner):
    from brain import tasks
    fm.write(owner.vault_path / "agent/Tasks.md", {}, "# Tasks\n\n" + "\n".join(task.render() for task in [
        Task("T-001", "Find a flat in Alfama", "- **Hub:** [[Lisbon trip]]\n- **Due:** 2026-11-18\n", {"state": "ready"}),
        Task("T-002", "Tidy the watch", "- **Hub:** [[Watch]]\n", {"state": "ready"}),
    ]))
    fm.write(owner.vault_path / "Watch.md", {"kind": "hub", "skill": "brain-watch", "records": "receipts"}, "# Watch\n")
    tasks.hand_off(owner, "T-001", TODAY, "")
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], TODAY)
    assert kept["last"] == {"text": "handed off Find a flat in Alfama", "date": "2026-11-11"}
    assert kept["soon"] == {"text": "Find a flat in Alfama", "date": "2026-11-18"}
    tasks.close(owner, "T-001", date(2026, 11, 12), True, "Booked the flat on Rua X", by_owner=True)
    kept = matters.state(fm.read(owner.vault_path / "Lisbon trip.md")[1], date(2026, 11, 12))
    assert kept["last"] == {"text": "finished Find a flat in Alfama", "date": "2026-11-12"}
    tasks.close(owner, "T-002", TODAY, False, "No longer needed", by_owner=True)
    assert "## Now" not in fm.read(owner.vault_path / "Watch.md")[1]
