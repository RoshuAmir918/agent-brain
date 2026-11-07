import json
from datetime import date

import pytest

from brain import cli, fm, install, seed
from brain.config import Config

TODAY = date(2026, 11, 6)


def review(folder, number, author, merged, *comments, title="A change"):
    fm.write(folder / f"{number}.md", {"pr": number, "title": title, "author": author, "merged": merged},
             "".join(f"- {line}\n" for line in comments))


@pytest.fixture
def reviews(tmp_path):
    folder = tmp_path / "reviews"
    folder.mkdir()
    review(folder, 101, "kim", "2026-10-20",
           "2026-10-19 · noah · apps/api/src/routers/study.ts · Move this query into the service layer.",
           "2026-10-19 · kim · apps/api/src/routers/study.ts · Done.",
           "2026-10-19 · review-bot · apps/api/src/routers/study.ts · Router performs a query directly.",
           "2026-10-19 · roshan · apps/web/src/study.tsx · Use the shared table component.")
    review(folder, 102, "sam", "2026-10-28",
           "2026-10-27 · noah · apps/api/src/routers/site.ts · Same here: queries belong in the service.",
           "2026-10-27 · roshan ·  · Please split the migration into its own PR.", title="Sites")
    review(folder, 103, "kim", "2026-11-01")
    review(folder, 90, "sam", "2026-06-01", "2026-06-01 · noah · infra/main.tf · Too old to count.")
    return folder


def test_reading_counts_corrections_by_others_inside_the_window(reviews):
    data = seed.reading(reviews, TODAY)
    assert data["window"] == {"from": "2026-08-08", "to": "2026-11-06"}
    assert (data["prs"], data["withComments"], data["comments"]) == (3, 2, 5)
    assert data["reviewers"] == [{"who": "noah", "comments": 2, "bot": False},
                                 {"who": "roshan", "comments": 2, "bot": False},
                                 {"who": "review-bot", "comments": 1, "bot": True}]
    assert data["areas"] == [{"area": "apps/api", "comments": 3, "prs": 2}, {"area": "apps/web", "comments": 1, "prs": 1},
                             {"area": "(whole PR)", "comments": 1, "prs": 1}]
    assert [item["pr"] for item in data["read"]] == [101, 102] and data["byPeople"] == 4
    assert [item["byPeople"] for item in data["read"]] == [2, 2]
    assert data["problems"] == []


def test_unreadable_files_and_comments_are_named_not_fatal(reviews):
    (reviews / "bad.md").write_text("---\ntitle: no number\n---\n")
    review(reviews, 104, "kim", "2026-11-02", "noah said this without a date")
    problems = seed.reading(reviews, TODAY)["problems"]
    assert "bad.md: needs frontmatter pr, author and merged (YYYY-MM-DD)" in problems
    assert any(p.startswith("104.md: malformed comment") for p in problems)


def test_area_is_the_first_two_folders():
    assert seed.area("apps/api/src/x.ts") == "apps/api"
    assert seed.area("README.md") == "(top level)"
    assert seed.area("") == "(whole PR)"


def test_cli_prints_the_reading_list_and_its_json(vault, reviews, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    cli.main(["portrait", "seed", "--vault", str(vault), "--reviews", str(reviews)])
    out = capsys.readouterr().out
    assert "3 merged PRs, 2 with review comments, 5 comments by someone other than the PR's author, 4 of them by people" in out
    assert "Reviewers: noah 2, roshan 2, review-bot 1 (bot)" in out
    assert "- #101 A change · 2 by people, 3 in all · " in out and out.index("#101") < out.index("#102")
    cli.main(["portrait", "seed", "--vault", str(vault), "--reviews", str(reviews), "--days", "7", "--json"])
    assert [item["pr"] for item in json.loads(capsys.readouterr().out)["read"]] == []


def test_cli_refuses_seed_without_a_folder_and_folder_without_seed(vault, reviews):
    with pytest.raises(SystemExit, match="--reviews DIR"):
        cli.main(["portrait", "seed", "--vault", str(vault)])
    with pytest.raises(SystemExit, match="go with seed"):
        cli.main(["portrait", "--vault", str(vault), "--reviews", str(reviews)])


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_seed_skill_renders_only_this_editions_procedure(vault, edition):
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\nedition = "{edition}"\n')
    config = Config.load(vault)
    install.skills(config)
    text = (config.skills_path / "brain-seed" / "SKILL.md").read_text()
    assert "human/Portrait.md" in text and "{" not in text.split("---", 2)[2].replace("```", "")
    assert "<!-- personal -->" not in text and "<!-- enterprise -->" not in text
    assert ("# Learn the company" in text) == (edition == "enterprise")
    assert ("brain portrait seed --account all --json" in text) == (edition == "personal")
    assert ("brain people friends --json" in text) == (edition == "personal")
    assert ("human/Tellable.md" in text) == (edition == "personal")
    recall = (config.skills_path / "brain-recall" / "SKILL.md").read_text()
    assert ("Then load `brain-seed` and run it scoped to that owner account." in recall) == (edition == "personal")


def project(folder, name, state, target="", initiative="", lead="", *issues):
    data = {"project": name, "state": state, **({"target": target} if target else {}),
            **({"initiative": initiative} if initiative else {}), **({"lead": lead} if lead else {})}
    fm.write(folder / f"{name}.md", data, "".join(f"- {line}\n" for line in issues))


@pytest.fixture
def plans(tmp_path):
    folder = tmp_path / "plans"
    folder.mkdir()
    project(folder, "Migration tooling", "started", "2026-12-15", "Win larger sponsors", "noah",
            "2026-10-20 · done · ENG-1 · Import CSV")
    project(folder, "Sponsor portal", "planned", "2027-06-30", "Win larger sponsors", "priya")
    project(folder, "Audit trail v2", "started", "2026-12-01", "", "kim")
    project(folder, "Mobile app", "backlog")
    project(folder, "Old reports", "completed", "2026-09-01", "", "", "2026-10-25 · done · ENG-9 · Final fix")
    project(folder, "Site billing", "paused", "2026-11-30")
    project(folder, "Moonshot", "planned", "2028-03-01")
    fm.write(folder / "_unplanned.md", {}, "- 2026-10-30 · done · ENG-5 · Fix login\n- 2026-11-01 · done · ENG-6 · Bump deps\n"
                                           "- 2026-11-02 · opened · ENG-7 · Flaky test\n- 2026-08-01 · done · ENG-2 · Too old\n")
    return folder


def test_horizon_is_this_quarter_this_year_or_longer():
    assert seed.horizon("2026-12-31", TODAY) == "now"
    assert seed.horizon("2027-01-01", TODAY) == "year"
    assert seed.horizon("2027-11-06", TODAY) == "year"
    assert seed.horizon("2027-11-07", TODAY) == "longer"
    assert seed.horizon("", TODAY) == ""
    assert seed.horizon("2026-03-31", date(2026, 2, 1)) == "now" and seed.horizon("2026-04-01", date(2026, 2, 1)) == "year"


def test_direction_ranks_open_projects_by_horizon_and_shows_where_effort_went(plans):
    data = seed.direction(plans, TODAY)
    assert [a["project"] for a in data["after"]] == ["Site billing", "Audit trail v2", "Migration tooling", "Sponsor portal",
                                                     "Moonshot", "Mobile app"]
    assert [a["horizon"] for a in data["after"]] == ["now", "now", "now", "year", "longer", ""]
    assert data["energy"] == [{"project": "(no project)", "done": 2, "share": 67},
                              {"project": "Migration tooling", "done": 1, "share": 33}]
    assert data["unattended"] == ["Audit trail v2"]   # due this quarter, started, nothing done; paused Site billing is not
    assert data["done"] == 3 and data["problems"] == []


def test_direction_names_unreadable_projects(plans):
    fm.write(plans / "bad.md", {"state": "started"}, "- yesterday · done · X · y\n")
    problems = seed.direction(plans, TODAY)["problems"]
    assert "bad.md: malformed issue line '- yesterday · done · X · y'" in problems
    assert "bad.md: needs frontmatter project and state, and target as YYYY-MM-DD when set" in problems


def test_cli_seeds_from_plans_and_takes_one_source_at_a_time(vault, plans, reviews, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    cli.main(["portrait", "seed", "--vault", str(vault), "--plans", str(plans)])
    out = capsys.readouterr().out
    assert "6 open projects, 3 issues done" in out
    assert "- Migration tooling (Win larger sponsors) · started · target 2026-12-15 · now · lead noah" in out
    assert "- Mobile app · backlog · no target" in out
    assert "- (no project) · 2 done · 67%" in out
    assert "Due this quarter with nothing done in the window: Audit trail v2" in out
    with pytest.raises(SystemExit, match="one of --reviews DIR"):
        cli.main(["portrait", "seed", "--vault", str(vault), "--plans", str(plans), "--reviews", str(reviews)])
    with pytest.raises(SystemExit, match="go with seed"):
        cli.main(["portrait", "--vault", str(vault), "--plans", str(plans)])


def test_coverage_shows_what_the_portrait_knows_and_what_was_never_read(vault, capsys, monkeypatch):
    from brain import channels
    from brain.vault import Vault
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    (vault / "human").mkdir()
    fm.write(vault / "human/Portrait.md", {"kind": "orientation", "tier": "hot", "pinned": True},
             "## After\n- Win larger sponsors · said · high · 2026-11-01 · [[START]]\n  - horizon: now\n"
             "- Moving upmarket · guess · low · 2026-11-02 · [[START]]\n  - horizon: year\n"
             "## Taste\n- Thin routers · seen · medium · 2026-10-26 · [[START]]\n")
    (vault / "agent/Seeds").mkdir()
    fm.write(vault / "agent/Seeds/2026-11-06 site.md", {"kind": "log", "source": "site", "read": "2026-11-06"}, "x\n")
    fm.write(vault / "agent/Seeds/2026-07-01 lumen-app.md",
             {"kind": "log", "source": "reviews", "read": "2026-07-01", "channel": "GitHub lumen/app"}, "x\n")
    fm.write(vault / "agent/Seeds/odd.md", {"kind": "log", "source": "gossip", "read": "2026-11-01"}, "x\n")
    config = Config.load(vault)
    fm.write(vault / "agent/Channels.md", {"kind": "orientation", "tier": "hot"}, "# Channels\n")
    channels.save(config, [channels.Channel("GitHub lumen/app", "shared", "lumen/app", "gh", "code", "", "2026-10-01", "live"),
                           channels.Channel("Slack", "shared", "lumen", "demiurge", "channels", "", "2026-10-01", "live"),
                           channels.Channel("Old Drive", "shared", "", "", "", "", "2026-01-01", "revoked")])
    data = seed.coverage(Vault(vault), TODAY)
    after, taste, cares = data["sections"][0], data["sections"][2], data["sections"][1]
    assert (after["lines"], after["said"], after["guess"], after["newest"]) == (2, 1, 1, "2026-11-02")
    assert taste["seen"] == 1 and cares["lines"] == 0
    status = {s["source"]: (s["read"], s["status"]) for s in data["sources"]}
    assert status["site"] == ("2026-11-06", "fresh") and status["reviews"] == ("2026-07-01", "stale")
    assert status["founders"] == ("", "never") and status["goals"] == ("", "never") and len(status) == 15
    assert data["unreadChannels"] == ["Slack"]
    assert data["problems"] == ["agent/Seeds/odd.md: needs frontmatter source (one of site, writing, founders, press, jobs, "
                                "customers, market, goals, meetings, docs, plans, chat, mail, crm, reviews) and read (YYYY-MM-DD)"]
    cli.main(["portrait", "coverage", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert "- After: 2 lines (1 said, 0 seen, 1 guess), newest 2026-11-02" in out and "- Cares about: empty" in out
    assert "- site: read 2026-11-06" in out and "- reviews: read 2026-07-01, stale" in out and "- jobs: never read" in out
    assert "Connected and never read for the portrait: Slack" in out


def test_a_persons_correction_ranks_above_a_pile_of_bot_comments(tmp_path):
    folder = tmp_path / "reviews"
    folder.mkdir()
    review(folder, 1, "kim", "2026-11-01", *[f"2026-11-01 · codex-connector[bot] · a/b/c.ts · Nit {i}." for i in range(9)])
    review(folder, 2, "kim", "2026-11-02", "2026-11-02 · noah · a/b/c.ts · Use the service.")
    data = seed.reading(folder, TODAY)
    assert [item["pr"] for item in data["read"]] == [2, 1]
    assert data["byPeople"] == 1 and data["reviewers"][0] == {"who": "codex-connector[bot]", "comments": 9, "bot": True}
