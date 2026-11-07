from datetime import date

import pytest

from brain import cli, fm, image, portrait, state, validate
from brain.config import Config
from brain.lock import vault_lock
from brain.vault import Vault

BODY = """# Portrait

## After
- Move to Lisbon within the year · said · high · 2026-10-30 · [[START]] ^3f2a
  - horizon: year
  - for: apartments, neighbourhoods, visas
- Wants to start climbing · guess · low · 2026-11-02 · [[START]] ^c1a0
  - horizon: now
  - test: hold a beginner gym shortlist near the office; offer once
  - against: hasn't mentioned it since Oct 20

## Taste
- Short essays over long books · seen · medium · 2026-11-01 · [[START]] ^e55a

## Not
- Not a runner · said · high · 2026-09-12 · [[START]] ^0bad
"""


@pytest.fixture
def config(vault):
    (vault / "human").mkdir(exist_ok=True)
    fm.write(vault / "human/Portrait.md", {"kind": "orientation", "pinned": True, "tier": "hot", "audience": "owner"}, BODY)
    return Config.load(vault)


def move(config, ident, facet):
    with vault_lock(config):
        return image.move(config, ident, facet)


def sort_into_facets(config):
    for ident, facet in (("3f2a", "Places"), ("c1a0", "Play"), ("e55a", "Reading & ideas"), ("0bad", "Play")):
        move(config, ident, facet)


def test_sorting_the_portrait_into_facets_keeps_every_line_and_id_and_then_compiles_it(config, vault):
    move(config, "3f2a", "Places")
    places = fm.read(vault / "human/Portrait/Places.md")
    assert places[0]["facet"] == "Places" and places[0]["rank"] == 1 and places[0]["audience"] == "owner"
    assert "## After\n- Move to Lisbon within the year · said · high · 2026-10-30 · [[START]] ^3f2a\n  - horizon: year" in places[1]
    assert "Lisbon" not in (vault / "human/Portrait.md").read_text()   # moved, not copied
    with pytest.raises(ValueError, match="3 line"), vault_lock(config):
        image.compile(config)   # half sorted: compiling now would drop the rest
    for ident, facet in (("c1a0", "Play"), ("e55a", "Reading & ideas"), ("0bad", "Play")):
        move(config, ident, facet)
    data, body = fm.read(vault / "human/Portrait.md")
    assert data["compiled"] == image.digest(body) and data["audience"] == "owner" and data["pinned"] is True
    assert "- Move to Lisbon within the year · said · high · 2026-10-30 · [[memory/human/Portrait/Places]] ^3f2a\n" \
           "  - horizon: year\n  - for: apartments, neighbourhoods, visas" in body
    assert "against" not in body and "test: hold a beginner gym" in body
    assert [line.id for line in portrait.load(config)] == ["3f2a", "c1a0", "e55a", "0bad"]
    assert {line.id: line.facet for line in image.lines(config)} == {"3f2a": "Places", "c1a0": "Play", "0bad": "Play",
                                                                         "e55a": "Reading & ideas"}
    play = fm.read(vault / "human/Portrait/Play.md")[1]
    assert play.index("## After") < play.index("## Not") and "against: hasn't mentioned it" in play
    assert validate.portrait_hygiene(Vault(vault)) == []


def test_the_compiled_portrait_takes_each_facets_best_lines_first_within_the_caps(config, vault):
    sort_into_facets(config)
    lines = "".join(f"- Aim {i} · seen · medium · 2026-11-01 · [[S]] ^a{i:03d}\n  - horizon: now\n" for i in range(8))
    fm.write(vault / "human/Portrait/Work.md", {"kind": "hub", "tier": "warm", "facet": "Work", "rank": 1}, "## After\n" + lines)
    with vault_lock(config):
        assert image.compile(config) == 10
    after = [line for line in portrait.load(config) if line.section == "After"]
    # each facet's first After line before any facet's second; among equals, facet rank, then confidence
    assert [line.id for line in after][:4] == ["3f2a", "a000", "c1a0", "a001"]
    assert len(after) == portrait.SECTION_LINES
    big = "".join(f"- {'word ' * 60}{i} · said · high · 2026-11-01 · [[S]] ^b{i:03d}\n" for i in range(8))
    fm.write(vault / "human/Portrait/Music.md", {"kind": "hub", "tier": "warm", "facet": "Music", "rank": 2}, "## Taste\n" + big)
    with vault_lock(config):
        image.compile(config)
    assert len(fm.read(vault / "human/Portrait.md")[1].split()) <= portrait.WORDS


def test_the_owners_answer_lands_on_the_facet_line_and_the_portrait_follows(config, vault):
    sort_into_facets(config)
    cli.main(["portrait", "mark", "c1a0", "wrong", "--words", "Bouldering maybe, not climbing", "--vault", str(vault)])
    play = (vault / "human/Portrait/Play.md").read_text()
    assert "Wants to start climbing" not in play
    assert "- Bouldering maybe, not climbing · said · high · " in play.split("## Not", 1)[1]
    assert "Wants to start climbing" in (vault / "human/History.md").read_text()
    compiled = (vault / "human/Portrait.md").read_text()
    assert "Wants to start climbing" not in compiled and "Bouldering maybe" in compiled
    cli.main(["portrait", "mark", "e55a", "right", "--vault", str(vault)])
    line = next(line for line in portrait.load(config) if line.id == "e55a")
    assert (line.basis, line.confidence) == ("said", "high")
    with pytest.raises(SystemExit, match="no portrait line \\^ffff"):
        cli.main(["portrait", "mark", "ffff", "right", "--vault", str(vault)])


def test_new_facet_lines_get_ids_unique_across_the_folder_and_reach_the_portrait(config, vault, monkeypatch):
    sort_into_facets(config)
    path = vault / "human/Portrait/Music.md"
    fm.write(path, {"kind": "hub", "tier": "warm", "facet": "Music", "rank": 3},
             "## Taste\n- Late-night electronic while working · seen · high · 2026-11-02 · [[S]]\n")
    tokens = iter(["3f2a", "0bad", "9a9a"])
    monkeypatch.setattr(image.secrets, "token_hex", lambda n: next(tokens))
    assert image.stamp(config) == 1
    assert path.read_text().rstrip().endswith("^9a9a")
    assert "Late-night electronic while working · seen · high · 2026-11-02 · [[memory/human/Portrait/Music]] ^9a9a" \
        in (vault / "human/Portrait.md").read_text()


def test_validate_names_hand_edits_said_only_breaches_shared_ids_and_bad_ranks(config, vault):
    sort_into_facets(config)
    path = vault / "human/Portrait.md"
    path.write_text(path.read_text().replace("Short essays", "Long essays"))
    fm.write(vault / "human/Portrait/Health.md", {"kind": "hub", "tier": "warm", "facet": "Health", "rank": "high",
                                                   "sensitivity": "said-only"},
             "## Energy\n- Sleeps badly before launches · guess · low · 2026-11-05 · [[S]] ^3f2a\n")
    found = "\n".join(validate.portrait_hygiene(Vault(vault)))
    assert "human/Portrait.md: edited by hand; it is compiled from Portrait/" in found
    assert "Health.md: rank 'high'" in found
    assert "Energy · Sleeps badly before launches: a said-only facet takes only what was said, not guess" in found
    assert "id ^3f2a is also in human/Portrait/Health.md" in found or "id ^3f2a is also in human/Portrait/Places.md" in found
    assert image.new_facet(config, "Money")["sensitivity"] == "said-only"


def test_a_facet_holds_more_than_the_portrait_but_not_without_limit(config, vault):
    sections = ("After", "Cares about", "Taste", "How they decide", "Energy", "Not")
    body = "".join(f"## {s}\n" + "".join(f"- {s} {i} · said · high · 2026-11-01 · [[S]]\n" for i in range(7)) for s in sections)
    found = image.facet_hygiene({"rank": 1}, body, date(2026, 11, 6), "F")
    assert any("42 lines; at most 40" in p for p in found)
    assert not any("words" in p for p in found)


def test_the_night_compiles_marks_facets_for_the_owner_and_says_why_when_it_cannot(config, vault):
    move(config, "3f2a", "Places")
    fm.write(vault / "human/Portrait/Places.md", {"kind": "hub", "tier": "warm", "facet": "Places", "rank": 1},
             fm.read(vault / "human/Portrait/Places.md")[1])
    cli.main(["nightly", "--vault", str(vault), "--date", "2026-11-06", "--write"])
    assert fm.read(vault / "human/Portrait/Places.md")[0]["audience"] == "owner"
    assert not fm.read(vault / "human/Portrait.md")[0].get("compiled")   # three lines still unsorted
    for ident, facet in (("c1a0", "Play"), ("e55a", "Reading & ideas"), ("0bad", "Play")):
        move(config, ident, facet)
    (vault / "human/Portrait/Places.md").write_text((vault / "human/Portrait/Places.md").read_text().replace("Lisbon", "Porto"))
    cli.main(["nightly", "--vault", str(vault), "--date", "2026-11-06", "--write"])
    assert "Move to Porto" in (vault / "human/Portrait.md").read_text()


def test_the_owner_gets_the_whole_image_with_the_compiled_lines_marked(config, vault):
    before = state.build(Config.load(vault), date(2026, 11, 6))["ownerPortrait"]
    assert [(line["id"], line["inPortrait"]) for line in before][:2] == [("3f2a", True), ("c1a0", True)]
    sort_into_facets(config)
    lines = "".join(f"- Aim {i} · seen · medium · 2026-11-01 · [[S]] ^a{i:03d}\n  - horizon: now\n" for i in range(9))
    fm.write(vault / "human/Portrait/Work.md", {"kind": "hub", "tier": "warm", "facet": "Work", "rank": 4}, "## After\n" + lines)
    with vault_lock(config):
        image.compile(config)
    data = state.build(Config.load(vault), date(2026, 11, 6))["ownerPortrait"]
    assert len(data) == 13 and all("against" not in line for line in data)
    by = {line["id"]: line for line in data}
    assert (by["3f2a"]["facet"], by["3f2a"]["inPortrait"], by["3f2a"]["rank"], by["3f2a"]["facetRank"]) == ("Places", True, 1, 1)
    assert by["a008"]["inPortrait"] is False and by["a008"]["facetRank"] == 9
    assert [line["facet"] for line in data][0] == "Places"   # most central facet first


def test_cli_lists_moves_and_compiles(config, vault, capsys):
    cli.main(["portrait", "facets", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "no facets yet"
    cli.main(["portrait", "compile", "--vault", str(vault)])
    assert "no facets yet" in capsys.readouterr().out
    cli.main(["portrait", "move", "3f2a", "Places", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "^3f2a Move to Lisbon within the year -> Places"
    with pytest.raises(SystemExit, match="are in no facet yet"):
        cli.main(["portrait", "compile", "--vault", str(vault)])
    with pytest.raises(SystemExit, match="already in Places"):
        cli.main(["portrait", "move", "3f2a", "Places", "--vault", str(vault)])
    with pytest.raises(SystemExit, match="a plain name"):
        cli.main(["portrait", "move", "c1a0", "../x", "--vault", str(vault)])
    for ident, facet in (("c1a0", "Play"), ("e55a", "Reading & ideas"), ("0bad", "Play")):
        cli.main(["portrait", "move", ident, facet, "--vault", str(vault)])
    capsys.readouterr()
    cli.main(["portrait", "facets", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines() == ["1 · Places · 1 lines · open · newest 2026-10-30",
                                                    "2 · Play · 2 lines · open · newest 2026-11-02",
                                                    "3 · Reading & ideas · 1 lines · open · newest 2026-11-01"]
    cli.main(["portrait", "compile", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "compiled 4 line(s) from 3 facet(s)"
    cli.main(["portrait", "coverage", "--vault", str(vault)])
    assert "Facets:\n- Places: 1 lines, newest 2026-10-30" in capsys.readouterr().out


def test_nothing_is_lost_to_unstamped_lines_letter_case_or_a_blank_line_before_details(config, vault):
    fm.write(vault / "human/Portrait.md", {"kind": "orientation", "tier": "hot"},
             "## After\n- Unstamped aim · said · high · 2026-11-01 · [[S]]\n\n  - horizon: year\n  - for: visas\n"
             "- Stamped aim · said · high · 2026-11-01 · [[S]] ^aaaa\n  - horizon: now\n")
    (vault / "human/Portrait").mkdir()
    fm.write(vault / "human/Portrait/Work.md", {"kind": "hub", "tier": "warm", "facet": "Work", "rank": 1},
             "## Taste\n- Another unstamped line · seen · low · 2026-11-01 · [[S]]\n")
    with vault_lock(config), pytest.raises(ValueError, match="2 line"):
        image.compile(config)
    move(config, "aaaa", "Work")
    with pytest.raises(ValueError, match="already in Work"):
        move(config, "aaaa", "work")
    assert "Stamped aim" in (vault / "human/Portrait/Work.md").read_text()
    image.stamp(config)
    ident = next(line.id for line in portrait.load(config) if line.text == "Unstamped aim")
    move(config, ident, "Places")
    places = (vault / "human/Portrait/Places.md").read_text()
    assert "  - horizon: year\n  - for: visas" in places
    assert "Unstamped aim" in (vault / "human/Portrait.md").read_text() and "visas" in (vault / "human/Portrait.md").read_text()


def test_due_lists_facets_with_new_evidence_and_judged_moves_the_mark(config, vault, tmp_path, monkeypatch, capsys):
    from brain import evidence
    from brain.evidence import Evidence
    home = tmp_path / "agent"
    monkeypatch.setenv("AGENT_HOME", str(home))
    sort_into_facets(config)
    evidence.append("imessage", [
        Evidence(date(2026, 11, 1), "talks", "Texts with Kim most days", "own", ("people",), "imessage:person/1"),
        Evidence(date(2026, 11, 3), "shared", "Link to a bouldering gym", "other:Climbing crew", ("play", "places"), "imessage:link/2"),
        Evidence(date(2026, 11, 4), "shared", "Link to a clinic", "own", ("health",), "imessage:link/3"),
        Evidence(date(2026, 11, 5), "talks", "Group chat Climbing crew", "own", ("people",), "imessage:group/4")])
    found = {entry["facet"]: entry for entry in image.due(config)}
    assert set(found) == {"People", "Play", "Places"}   # health is said-only, never from evidence
    assert (found["People"]["exists"], found["People"]["new"], found["People"]["newest"]) == (False, 2, "2026-11-05")
    assert found["Play"]["exists"] and found["Play"]["since"] == "" and found["Play"]["sources"] == ["imessage"]
    assert image.due(config)[0]["facet"] == "People"
    cli.main(["portrait", "judged", "people", "--through", "2026-11-03", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "People: judged through 2026-11-03"
    data = fm.read(vault / "human/Portrait/People.md")[0]
    assert data["judged"] == "2026-11-03" and data["facet"] == "People" and data["audience"] == "owner"
    assert {entry["facet"]: entry["new"] for entry in image.due(config)}["People"] == 1
    cli.main(["portrait", "judged", "Play", "--vault", str(vault)])
    cli.main(["portrait", "due", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert "Play" not in out.split("\n", 1)[1] and "People · 1 rows since 2026-11-03 · newest 2026-11-05 · imessage" in out
    assert validate.portrait_hygiene(Vault(vault)) == []


def test_a_facet_that_claims_hints_takes_those_rows_from_the_broader_one(config, vault, tmp_path, monkeypatch, capsys):
    from brain import evidence
    from brain.evidence import Evidence
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    sort_into_facets(config)
    fm.write(vault / "human/Portrait/Climbing.md", {"kind": "hub", "tier": "warm", "facet": "Climbing", "rank": 5,
                                                     "hints": ["Boulder", "climb"]}, "# Climbing\n")
    fm.write(vault / "human/Portrait/Health.md", {"kind": "hub", "tier": "warm", "facet": "Health", "rank": 6,
                                                   "sensitivity": "said-only", "hints": ["physio"]}, "# Health\n")
    evidence.append("calendar", [
        Evidence(date(2026, 11, 1), "went", "Bouldering at the Barn, 4 times", "own", ("play",), "calendar:1"),
        Evidence(date(2026, 11, 2), "went", "Tennis, twice", "own", ("play",), "calendar:2"),
        Evidence(date(2026, 11, 3), "booked", "Climbing gym day pass", "own", ("places",), "calendar:3"),
        Evidence(date(2026, 11, 4), "went", "Physio for the shoulder", "own", ("health",), "calendar:4")])
    routed = image.route(evidence.read(), image.facets(config))
    assert {name: [row.pointer for row in rows] for name, rows in routed.items()} == {
        "Climbing": ["calendar:3", "calendar:1"], "Play": ["calendar:2"]}   # a fact's words claim too; health takes none
    assert [entry["facet"] for entry in image.due(config)] == ["Climbing", "Play"]
    cli.main(["portrait", "evidence", "climbing", "--vault", str(vault)])
    assert capsys.readouterr().out.splitlines()[0].startswith("calendar: 2026-11-03 · booked · Climbing gym day pass")
    cli.main(["portrait", "judged", "Climbing", "--through", "2026-11-03", "--vault", str(vault)])
    capsys.readouterr()
    cli.main(["portrait", "evidence", "Climbing", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "no new evidence for Climbing"
    cli.main(["portrait", "evidence", "Climbing", "--all", "--json", "--vault", str(vault)])
    assert len(__import__("json").loads(capsys.readouterr().out)) == 2
    assert image.evidence_for(config, "Places") == []   # the day pass went to Climbing, not Places


def test_claims_match_whole_words_never_open_a_said_only_row_and_names_ignore_case(config, vault, tmp_path, monkeypatch):
    from brain import evidence
    from brain.evidence import Evidence
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    sort_into_facets(config)
    fm.write(vault / "human/Portrait/Climbing.md", {"kind": "hub", "tier": "warm", "facet": "Climbing", "rank": 5,
                                                     "hints": ["climb", "run"]}, "# Climbing\n")
    evidence.append("mail", [
        Evidence(date(2026, 11, 1), "went", "Climbing injury treatment", "own", ("health",), "m:1"),
        Evidence(date(2026, 11, 2), "received", "3 notices from runtime.example.com", "own", ("home & routine",), "m:2"),
        Evidence(date(2026, 11, 3), "booked", "Table at a bistro", "own", ("food & drink",), "m:3")])
    routed = image.route(evidence.read(), image.facets(config))
    assert {name: [row.pointer for row in rows] for name, rows in routed.items()} == {
        "Home & routine": ["m:2"], "Food & drink": ["m:3"]}
    assert [row.pointer for row in image.evidence_for(config, "FOOD & DRINK")] == ["m:3"]
