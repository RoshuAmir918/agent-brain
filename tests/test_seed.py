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
           "2026-10-19 · nathan · apps/api/src/routers/study.ts · Move this query into the service layer.",
           "2026-10-19 · kim · apps/api/src/routers/study.ts · Done.",
           "2026-10-19 · review-bot · apps/api/src/routers/study.ts · Router performs a query directly.",
           "2026-10-19 · roshan · apps/web/src/study.tsx · Use the shared table component.")
    review(folder, 102, "sam", "2026-10-28",
           "2026-10-27 · nathan · apps/api/src/routers/site.ts · Same here: queries belong in the service.",
           "2026-10-27 · roshan ·  · Please split the migration into its own PR.", title="Sites")
    review(folder, 103, "kim", "2026-11-01")
    review(folder, 90, "sam", "2026-06-01", "2026-06-01 · nathan · infra/main.tf · Too old to count.")
    return folder


def test_reading_counts_corrections_by_others_inside_the_window(reviews):
    data = seed.reading(reviews, TODAY)
    assert data["window"] == {"from": "2026-08-08", "to": "2026-11-06"}
    assert (data["prs"], data["withComments"], data["comments"]) == (3, 2, 5)
    assert data["reviewers"] == [{"who": "nathan", "comments": 2, "bot": False},
                                 {"who": "roshan", "comments": 2, "bot": False},
                                 {"who": "review-bot", "comments": 1, "bot": True}]
    assert data["areas"] == [{"area": "apps/api", "comments": 3, "prs": 2}, {"area": "apps/web", "comments": 1, "prs": 1},
                             {"area": "(whole PR)", "comments": 1, "prs": 1}]
    assert [item["pr"] for item in data["read"]] == [101, 102]
    assert data["problems"] == []


def test_unreadable_files_and_comments_are_named_not_fatal(reviews):
    (reviews / "bad.md").write_text("---\ntitle: no number\n---\n")
    review(reviews, 104, "kim", "2026-11-02", "nathan said this without a date")
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
    assert "3 merged PRs, 2 with review comments, 5 comments" in out
    assert "Reviewers: nathan 2, roshan 2, review-bot 1 (bot)" in out
    assert "- #101 A change · 3 · " in out and out.index("#101") < out.index("#102")
    cli.main(["portrait", "seed", "--vault", str(vault), "--reviews", str(reviews), "--days", "7", "--json"])
    assert [item["pr"] for item in json.loads(capsys.readouterr().out)["read"]] == []


def test_cli_refuses_seed_without_a_folder_and_folder_without_seed(vault, reviews):
    with pytest.raises(SystemExit, match="--reviews DIR"):
        cli.main(["portrait", "seed", "--vault", str(vault)])
    with pytest.raises(SystemExit, match="go with seed"):
        cli.main(["portrait", "--vault", str(vault), "--reviews", str(reviews)])


def test_the_seed_skill_is_an_organisations_only(vault):
    personal = Config.load(vault)
    install.skills(personal)
    assert not (personal.skills_path / "brain-seed").exists()
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\nedition = "enterprise"\n')
    config = Config.load(vault)
    install.skills(config)
    text = (config.skills_path / "brain-seed" / "SKILL.md").read_text()
    assert "human/Portrait.md" in text and "{" not in text.split("---", 2)[2].replace("```", "")
