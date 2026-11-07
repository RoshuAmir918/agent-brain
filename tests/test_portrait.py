import json
from datetime import date

from brain import cli, compile_context, fm, portrait, state, validate
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
    long = "## Not\n" + "- " + "word " * 700 + "· said · high · 2026-11-01 · [[S]]\n"
    assert any("words; keep it under 700" in p for p in portrait.hygiene(long, date(2026, 11, 6), "P"))


def test_guesses_have_no_share_cap():
    body = "## Taste\n" + "".join(f"- Hunch {i} · guess · low · 2026-11-01 · [[S]]\n" for i in range(8))
    assert portrait.hygiene(body, date(2026, 11, 6), "P") == []


def test_stamp_ids_new_lines_once_keeps_existing_and_never_reuses_a_retired_id(vault, monkeypatch):
    path = write_portrait(vault)
    (vault / "human/History.md").write_text("- 2026-10-01 · retired: Was a chess player ^beef\n")
    tokens = iter(["3f2a", "beef", "0001", "0002", "0003"])
    monkeypatch.setattr(portrait.secrets, "token_hex", lambda n: next(tokens))
    config = Config.load(vault)
    assert portrait.stamp(config) == 3
    lines = portrait.load(config)
    assert [line.id for line in lines] == ["3f2a", "0001", "0002", "0003"]
    assert "Wants to start climbing · guess · low · 2026-11-02 · [[START]] ^0001\n  - horizon: now" in path.read_text()
    assert portrait.stamp(config) == 0


def test_context_carries_the_portrait_after_current_with_its_rule(vault):
    text = compile_context.build(Vault(vault))
    assert "Portrait" not in text.split("## Before answering", 1)[0]
    write_portrait(vault)
    text = compile_context.build(Vault(vault))
    assert "## memory/human/Portrait.md\n\nWho you think Human is" in text
    assert text.index("# Current") < text.index("Move to Lisbon") < text.index("# Triggers")
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
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nedition = "enterprise"\nhuman_name = "Harbor"\n')


def test_an_organisation_has_a_portrait_in_its_context_with_the_team_rule(vault):
    enterprise(vault)
    write_portrait(vault, COMPANY)
    text = compile_context.build(Vault(vault))
    assert "What you understand of Harbor" in text and "Ship the EDC migration tooling" in text
    assert "A guess about direction is never stated to the team" in text
    assert "never told to anyone else" not in text
    assert text.index("# Current") < text.index("Ship the EDC") < text.index("# Triggers")


def test_an_organisation_may_say_how_it_decides(vault):
    lines, problems = portrait.parse(COMPANY)
    assert problems == [] and [line.section for line in lines][-1] == "How they decide"
    enterprise(vault)
    write_portrait(vault, COMPANY)
    assert validate.portrait_hygiene(Vault(vault)) == []
    write_portrait(vault, "## Hobbies\n- broken\n")
    assert any("unknown section 'Hobbies'" in p for p in validate.portrait_hygiene(Vault(vault)))


def test_stamping_reads_how_it_decides_as_a_section(vault):
    enterprise(vault)
    path = write_portrait(vault, "## How it decides\n- Founders decide scope · said · high · 2026-11-02 · [[START]]\n")
    assert portrait.stamp(Config.load(vault)) == 1
    assert path.read_text().count(" ^") == 1


def test_the_state_export_shows_an_organisation_its_stated_and_seen_lines_only(vault):
    enterprise(vault)
    write_portrait(vault, COMPANY)
    config = Config.load(vault)
    exported = state.build(config, date(2026, 11, 6))["portrait"]
    assert [line["id"] for line in exported] == ["a001", "a003", "a004"]
    assert all("against" not in line for line in exported)


def test_the_state_export_never_carries_a_persons_portrait(vault):
    write_portrait(vault)
    assert state.build(Config.load(vault), date(2026, 11, 6))["portrait"] is None


def test_validate_reports_portrait_problems(vault):
    write_portrait(vault, "## After\n- broken\n")
    assert any("human/Portrait.md: After: malformed line" in p for p in validate.hygiene(Vault(vault)))


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


def test_cli_says_when_there_is_no_portrait(vault, capsys):
    cli.main(["portrait", "--vault", str(vault)])
    assert "no portrait yet: memory/human/Portrait.md" in capsys.readouterr().out


def test_nightly_stamps_new_lines(vault, capsys):
    path = write_portrait(vault)
    cli.main(["nightly", "--vault", str(vault), "--date", "2026-11-06", "--write"])
    assert "portrait: 3 new line id(s)" in capsys.readouterr().out
    assert all(line.id for line in portrait.load(Config.load(vault)))
    assert path.read_text().count(" ^") == 4


def test_a_company_after_line_says_where_the_intellect_can_help(vault):
    enterprise(vault)
    write_portrait(vault, "## After\n- Win larger sponsors · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n")
    assert any("Win larger sponsors: no for line" in p for p in validate.portrait_hygiene(Vault(vault)))
    write_portrait(vault, "## After\n- Win larger sponsors · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n"
                          "  - for: sponsor-facing reports, the audit trail, case studies\n")
    assert validate.portrait_hygiene(Vault(vault)) == []
    assert portrait.hygiene("## After\n- Run a marathon · said · high · 2026-11-01 · [[START]]\n  - horizon: year\n",
                            date(2026, 11, 6), "P") == []
