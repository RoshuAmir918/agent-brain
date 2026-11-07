from datetime import date

import pytest

from brain import evidence, nightly, sources
from brain.config import Config
from brain.sources import plans as plan_source, reviews as review_source
from test_seed import project, review

TODAY = date(2026, 11, 4)


@pytest.fixture
def org(vault, tmp_path, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    home = tmp_path / "agent"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    repo, workspace = home / "channels/lumen-app/reviews", home / "channels/lumen/plans"
    repo.mkdir(parents=True)
    workspace.mkdir(parents=True)
    review(repo, 101, "kim", "2026-10-20",
           "2026-10-19 · noah · apps/api/src/routers/study.ts · Move this query into the service layer.",
           "2026-10-19 · kim · apps/api/src/routers/study.ts · Done.",
           "2026-10-19 · review-bot · apps/api/src/routers/study.ts · Router performs a query directly.",
           "2026-10-19 · roshan · apps/web/src/study.tsx · Posted by Codex: use the shared table component.")
    project(workspace, "Migration tooling", "started", "2026-12-15", "Win larger sponsors", "noah",
            "2026-10-20 · done · ENG-1 · Import CSV")
    project(workspace, "Old reports", "completed", "2026-09-01", "", "", "2026-10-25 · done · ENG-9 · Final fix")
    from brain import fm
    fm.write(workspace / "_unplanned.md", {}, "- 2026-10-30 · done · ENG-5 · Fix login\n")
    return Config.load(vault)


def test_a_persons_correction_is_evidence_and_a_bots_or_an_agents_is_not(org):
    rows = review_source.extract(sources.Context(TODAY, config=org))
    row, = rows
    assert (row.verb, row.whose, row.hints, row.extra) == ("corrected", "other:noah", ("engineering",), {"area": "apps/api"})
    assert row.fact == "On #101 A change: Move this query into the service layer." and row.pointer == "reviews:lumen-app#101"


def test_open_projects_and_unplanned_work_are_direction(org):
    rows = {row.pointer: row for row in plan_source.extract(sources.Context(TODAY, config=org))}
    assert set(rows) == {"plans:lumen/Migration tooling", "plans:unplanned"}   # a completed project is not direction
    tooling = rows["plans:lumen/Migration tooling"]
    assert tooling.fact == ("Migration tooling (Win larger sponsors): started, target 2026-12-15, led by noah; "
                            "1 issues done in the last 30 days")
    assert (tooling.date, tooling.hints) == (date(2026, 10, 20), ("direction",))
    assert rows["plans:unplanned"].fact == "1 issues done in no project in the last 30 days"


def test_the_night_extracts_an_organisations_sources_and_routes_them_to_its_facets(org, vault):
    from brain import image
    report = nightly.run(vault, TODAY)
    assert any(line.startswith("evidence: reviews: 1 new") for line in report)
    assert any(line.startswith("evidence: plans: 2 new") for line in report)
    assert not any(line.startswith("evidence: mail") for line in report)   # an organisation's mail is for consults
    due = {entry["facet"]: entry["new"] for entry in image.due(org)}
    assert due == {"Engineering": 1, "Direction": 2}
    assert not any(line.startswith("evidence:") for line in nightly.run(vault, TODAY))
    assert {row.source for row in evidence.read()} == {"reviews", "plans"}
