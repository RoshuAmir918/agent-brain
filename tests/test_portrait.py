import pytest
import json
from datetime import date

from brain import cli, compile_context, coverage, fm, image, portrait, state, validate
from brain.config import Config
from brain.vault import Vault

BODY = """# Portrait

## After
- Move to Lisbon within the year · said · high · 2026-10-30 · [[START]] ^3f2a
  - horizon: year
  - for: apartments, neighbourhoods, visas
- Wants to start climbing · guess · low · 2026-11-02 · [[START]]
  - horizon: now
  - test: hold a beginner gym shortlist near the office; offer once
  - against: hasn't mentioned it since Oct 20

## Taste
- Short essays over long books · seen · medium · 2026-11-01 · [[START]]

## Not
- Not a runner · said · high · 2026-09-12 · [[START]]
"""


def write_portrait(root, body=BODY):
    (root / "human").mkdir(exist_ok=True)
    fm.write(root / "human/Portrait.md", {"kind": "orientation", "pinned": True, "tier": "hot"}, body)
    return root / "human/Portrait.md"


def test_lines_parse_in_rank_order_with_their_fields():
    lines, problems = portrait.parse(BODY)
    assert problems == []
    lisbon, climbing, essays, runner = lines
    assert (lisbon.id, lisbon.section, lisbon.rank, lisbon.basis, lisbon.horizon) == ("3f2a", "After", 1, "said", "year")
    assert lisbon.for_ == "apartments, neighbourhoods, visas" and lisbon.source == "[[START]]"
    assert (climbing.id, climbing.rank, climbing.test) == ("", 2, "hold a beginner gym shortlist near the office; offer once")
    assert essays.rank == 1 and runner.section == "Not"
    assert "against" not in climbing.public() and climbing.public()["for"] == ""


def test_malformed_lines_and_misplaced_fields_are_named():
    body = ("## After\n- no fields at all\n- Thing · maybe · high · 2026-11-01 · [[S]]\n"
            "## Taste\n- Jazz · said · sure · 11/01 · [[S]]\n  - for: records\n  - mood: good\n## Hobbies\n- x · said · high · 2026-11-01 · [[S]]\n")
    _, problems = portrait.parse(body)
    text = "\n".join(problems)
    assert "After: malformed line '- no fields at all'" in text
    assert "basis 'maybe'" in text and "confidence 'sure'" in text and "evidence date '11/01'" in text
    assert "Taste · Jazz: for belongs to After lines only" in text
    assert "unknown field 'mood'" in text and "unknown section 'Hobbies'" in text


def test_hygiene_names_caps_missing_horizons_stale_guesses_and_reused_ids():
    after = "".join(f"- Aim {i} · said · high · 2026-11-01 · [[S]]\n  - horizon: now\n" for i in range(9))
    body = ("## After\n" + after + "- Unscoped · said · high · 2026-11-01 · [[S]] ^aaaa\n"
            "## Taste\n- Old hunch · guess · low · 2026-09-01 · [[S]] ^aaaa\n- Fresh hunch · guess · low · 2026-10-20 · [[S]]\n")
    found = "\n".join(portrait.hygiene(body, date(2026, 11, 6), "human/Portrait.md"))
    assert "After: 10 lines; at most 8" in found
    assert "After · Unscoped: no horizon line" in found
    assert "Taste · Old hunch: a guess with no evidence since 2026-09-01" in found and "Fresh hunch" not in found
    assert "id ^aaaa is used twice" in found
    seeded = ("## Taste\n- Seeded hunch · guess · low · 2026-08-01 · [[agent/Seeds/2026-10-20 me@x.com]]\n"
              "- Old seeded hunch · guess · low · 2026-08-01 · [[agent/Seeds/2026-09-01 me@x.com]]\n")
    stale = "\n".join(portrait.hygiene(seeded, date(2026, 11, 6), "P"))
    assert "Old seeded hunch" in stale and "Seeded hunch: " not in stale   # the clock starts at the seed
    long = "## Not\n" + "- " + "word " * 700 + "· said · high · 2026-11-01 · [[S]]\n"
    assert any("words; keep it under 700" in p for p in portrait.hygiene(long, date(2026, 11, 6), "P"))


def test_stamp_ids_new_lines_once_keeps_existing_and_never_reuses_a_retired_id(vault, monkeypatch):
    path = write_portrait(vault)
    (vault / "human/History.md").write_text("- 2026-10-01 · retired: Was a chess player ^beef\n")
    tokens = iter(["3f2a", "beef", "0001", "0002", "0003"])
    monkeypatch.setattr(image.secrets, "token_hex", lambda n: next(tokens))
    config = Config.load(vault)
    assert image.stamp(config) == 3
    lines = portrait.load(config)
    assert [line.id for line in lines] == ["3f2a", "0001", "0002", "0003"]
    assert "Wants to start climbing · guess · low · 2026-11-02 · [[START]] ^0001\n  - horizon: now" in path.read_text()
    assert image.stamp(config) == 0


def test_context_carries_the_portrait_after_current_with_its_rule(vault):
    text = compile_context.build(Vault(vault))
    assert "Portrait" not in text.split("## Before answering", 1)[0]
    write_portrait(vault)
    text = compile_context.build(Vault(vault))
    assert "## memory/human/Portrait.md\n\nWho you think Human is" in text
    assert text.index("# Current") < text.index("Move to Lisbon") < text.index("## Upcoming")
    assert "never told to anyone else" in text
    assert "human/Portrait" not in compile_context.directory(Vault(vault))


COMPANY = """# Portrait

## After
- Ship the EDC migration tooling this quarter · said · high · 2026-11-01 · [[START]] ^a001
  - horizon: now
  - for: importer edge cases, migration runbooks
- Moving upmarket to larger sponsors · guess · low · 2026-11-03 · [[START]] ^a002
  - horizon: year
  - for: enterprise security questionnaires

## Taste
- Service layer owns queries; routers stay thin · seen · medium · 2026-11-04 · [[START]] ^a003
  - against: two routers query directly

## How it decides
- Founders decide scope; domain owners decide how · said · high · 2026-11-02 · [[START]] ^a004
"""


def enterprise(vault):
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nedition = "enterprise"\nhuman_name = "Acme"\n')


def test_an_organisation_has_a_portrait_in_its_context_with_the_team_rule(vault):
    enterprise(vault)
    write_portrait(vault, COMPANY)
    text = compile_context.build(Vault(vault))
    assert "What you understand of Acme" in text and "Ship the EDC migration tooling" in text
    assert "A guess about direction is never stated to the team" in text
    assert "never told to anyone else" not in text
    assert text.index("# Current") < text.index("Ship the EDC") < text.index("## Upcoming")


def test_an_organisation_may_say_how_it_decides(vault):
    lines, problems = portrait.parse(COMPANY)
    assert problems == [] and [line.section for line in lines][-1] == "How they decide"
    enterprise(vault)
    write_portrait(vault, COMPANY)
    assert validate.portrait_hygiene(Vault(vault)) == []
    write_portrait(vault, "## Hobbies\n- broken\n")
    assert any("unknown section 'Hobbies'" in p for p in validate.portrait_hygiene(Vault(vault)))
    path = write_portrait(vault, "## How it decides\n- Founders decide scope · said · high · 2026-11-02 · [[START]]\n")
    assert image.stamp(Config.load(vault)) == 1
    assert path.read_text().count(" ^") == 1


def test_the_state_export_shows_an_organisation_its_stated_and_seen_lines_only(vault):
    enterprise(vault)
    write_portrait(vault, COMPANY)
    config = Config.load(vault)
    exported = state.build(config, date(2026, 11, 6))["portrait"]
    assert [line["id"] for line in exported] == ["a001", "a003", "a004"]
    assert all("against" not in line for line in exported)


def test_cli_prints_lines_and_the_top_after_lines_with_their_watch_lists(vault, capsys):
    write_portrait(vault)
    cli.main(["portrait", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert "^3f2a · After · Move to Lisbon within the year · said · high · 2026-10-30 · year" in out
    assert "Not · Not a runner" in out
    cli.main(["portrait", "--vault", str(vault), "--top", "1"])
    out = capsys.readouterr().out
    assert out.splitlines() == ["^3f2a · After · Move to Lisbon within the year · said · high · 2026-10-30 · year",
                                "  for: apartments, neighbourhoods, visas"]
    cli.main(["portrait", "--vault", str(vault), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert [item["text"] for item in data][:2] == ["Move to Lisbon within the year", "Wants to start climbing"]
    assert all("against" not in item for item in data)


def test_a_company_after_line_says_where_the_intellect_can_help(vault):
    enterprise(vault)
    write_portrait(vault, "## After\n- Win larger sponsors · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n")
    assert any("Win larger sponsors: no for line" in p for p in validate.portrait_hygiene(Vault(vault)))
    write_portrait(vault, "## After\n- Win larger sponsors · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n"
                          "  - for: sponsor-facing reports, the audit trail, case studies\n")
    assert validate.portrait_hygiene(Vault(vault)) == []
    assert portrait.hygiene("## After\n- Run a marathon · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n",
                            date(2026, 11, 6), "P") == []


def test_the_owner_card_leaves_the_impression_to_the_portrait_once_there_is_one(vault):
    from brain import session_context
    (vault / "human").mkdir(exist_ok=True)
    fm.write(vault / "human/Profile.md", {"kind": "profile", "tier": "hot"},
             "# Human\n## Working together\nBrief me in bullets.\n## Impression\nIMPRESSION_TEXT here.\n")
    config = Config.load(vault)
    card = session_context.owner_card(Vault(vault), config)
    assert "IMPRESSION_TEXT" in card and "Talk about your Impression only" in card
    write_portrait(vault)
    card = session_context.owner_card(Vault(vault), config)
    assert "Brief me in bullets." in card and "IMPRESSION_TEXT" not in card
    assert "Talk about your Portrait of them only with Human alone" in card


def test_coverage_shows_which_after_lines_were_served_this_week(vault, monkeypatch):
    from brain import reception
    from brain.lock import vault_lock
    from datetime import datetime
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-06")
    write_portrait(vault, BODY.replace("· [[START]]\n  - horizon: now", "· [[START]] ^91c0\n  - horizon: now"))
    (vault / "agent/Explore").mkdir()
    (vault / "agent/Explore/2026-11.md").write_text("---\nkind: log\n---\n"
        "- 2026-11-05 09:00 | explore | held | work: Lisbon shortlist, held, portrait:3f2a | none\n"
        "- 2026-10-01 09:00 | explore | held | work: too old, portrait:91c0 | none\n")
    config = Config.load(vault)
    with vault_lock(config):
        reception.record(config, "draft", "Lisbon shortlist", "START", "3f2a", at=datetime(2026, 11, 5, 9))
    data = coverage.coverage(Vault(vault), date(2026, 11, 6))
    assert [(a["id"], a["runs"], a["offers"]) for a in data["attention"]] == [("3f2a", 1, 1), ("91c0", 0, 0)]
    assert "- ^91c0 Wants to start climbing: 0 runs, 0 offers · nothing" in coverage.render_coverage(data)


def test_an_after_line_may_name_the_goal_it_reflects_and_the_goal_must_exist(vault):
    from brain import goals
    from brain.lock import vault_lock
    config = Config.load(vault)
    with vault_lock(config):
        gid = goals.new(config, "Run a marathon", "I want to run Boston", from_app=True).gid
    write_portrait(vault, f"## After\n- Run Boston · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n  - goal: {gid}\n"
                          "  - for: race plans near the office\n## Taste\n- Jazz · said · high · 2026-11-01 · [[START]]\n  - goal: G-1\n")
    lines, problems = portrait.parse((vault / "human/Portrait.md").read_text())
    assert lines[0].goal == gid and lines[0].public()["goal"] == gid
    assert "Taste · Jazz: goal belongs to After lines only" in problems
    write_portrait(vault, "## After\n- Run Boston · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n  - goal: G-99\n"
                          "- Sail · guess · low · 2026-11-01 · [[START]]\n  - horizon: longer\n  - goal: marathon\n  - test: ask\n")
    found = "\n".join(validate.portrait_hygiene(Vault(vault)))
    assert "After · Run Boston: goal G-99 is not in human/Goals.md" in found
    assert "After · Sail: goal 'marathon'; a goal id such as G-3" in found


def test_only_said_and_seen_lines_are_shown():
    body = "## Taste\n" + "".join(f"- Line {basis} · {basis} · low · 2026-11-01 · [[S]]\n"
                                  for basis in ("said", "seen", "guess", "Guess", "guessed", "sed"))
    assert [line.basis for line in portrait.shown(portrait.parse(body)[0])] == ["said", "seen"]


def test_the_owner_marks_lines_right_wrong_or_not_this(vault, capsys):
    path = write_portrait(vault, BODY.replace("· [[START]]\n  - horizon: now", "· [[START]] ^91c0\n  - horizon: now")
                          .replace("Short essays over long books · seen · medium · 2026-11-01 · [[START]]",
                                   "Short essays over long books · seen · medium · 2026-11-01 · [[START]] ^5e55"))
    cli.main(["portrait", "mark", "91c0", "right", "--vault", str(vault)])
    assert capsys.readouterr().out.startswith("right: ^91c0 Wants to start climbing")
    text = path.read_text()
    assert "- Wants to start climbing · said · high · " in text and "^91c0\n  - horizon: now\n  - against:" in text
    assert "test: hold a beginner gym" not in text
    cli.main(["portrait", "mark", "^3f2a", "wrong", "--words", "I'm staying put; Lisbon was a holiday idea", "--vault", str(vault)])
    cli.main(["portrait", "mark", "5e55", "not-this", "--vault", str(vault)])
    text = path.read_text()
    assert "Move to Lisbon" not in text and "Short essays" not in text.split("## Not")[0]
    not_section = text.split("## Not", 1)[1]
    assert "- I'm staying put; Lisbon was a holiday idea · said · high · " in not_section
    assert "- Not to draw on: Short essays over long books · said · high · " in not_section
    history = (vault / "human/History.md").read_text()
    assert "portrait line marked wrong by the owner · their words: I'm staying put; Lisbon was a holiday idea" in history
    assert "Move to Lisbon within the year · said · high · 2026-10-30 · [[START]] ^3f2a" in history
    assert "portrait line marked not-this by the owner" in history
    lines, problems = portrait.parse(text)
    assert problems == []
    with pytest.raises(SystemExit, match="no portrait line \\^ffff"):
        cli.main(["portrait", "mark", "ffff", "right", "--vault", str(vault)])


def test_a_persons_portrait_goes_out_only_as_the_owners_and_their_notes_are_marked_for_them(vault):
    from brain import nightly, state
    (vault / "human").mkdir(exist_ok=True)
    fm.write(vault / "human/Profile.md", {"kind": "profile", "tier": "hot"}, "# Human\n## Working together\nx\n")
    write_portrait(vault)
    data = state.build(Config.load(vault), date(2026, 11, 6))
    assert data["portrait"] is None and [line["text"] for line in data["ownerPortrait"]][1] == "Wants to start climbing"
    assert all("against" not in line for line in data["ownerPortrait"])
    report, loaded = [], Vault(vault)
    changed = nightly.ensure_frontmatter(loaded, date(2026, 11, 6), report)
    assert {"human/Profile.md", "human/Portrait.md"} <= changed
    assert loaded.notes["human/Profile.md"].data["audience"] == "owner"
    assert loaded.notes["human/Portrait.md"].data["audience"] == "owner"
    enterprise(vault)
    assert state.build(Config.load(vault), date(2026, 11, 6))["ownerPortrait"] is None


def test_the_night_stamps_new_lines_and_writes_the_owner_audience_to_disk(vault, capsys):
    (vault / "human").mkdir(exist_ok=True)
    fm.write(vault / "human/Profile.md", {"kind": "profile", "tier": "hot"}, "# Human\n")
    path = write_portrait(vault)
    for name in ("Goals.md", "Tellable.md"):
        fm.write(vault / "human" / name, {"kind": "orientation", "tier": "hot"}, f"# {name[:-3]}\n")
    cli.main(["nightly", "--vault", str(vault), "--date", "2026-11-06", "--write"])
    assert "portrait: 3 new line id(s)" in capsys.readouterr().out
    assert all(line.id for line in portrait.load(Config.load(vault)))
    assert path.read_text().count(" ^") == 4
    for rel in ("human/Profile.md", "human/Portrait.md", "human/Goals.md", "human/Tellable.md"):
        assert fm.read(vault / rel)[0]["audience"] == "owner"
