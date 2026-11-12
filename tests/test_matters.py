"""A matter's kept state, and what the app is given for it."""
from datetime import date

import pytest

from brain import fm, loops, matters, state
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
