import json
from datetime import date, datetime

import pytest

from brain import cli, fm, ideas, journal, needs, reception
from brain.config import Config
from brain.lock import vault_lock

TODAY = date(2026, 11, 7)


@pytest.fixture
def config(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    return Config.load(vault)


def outcome(config, ident):
    return next(row.outcome for _, row in reception.rows(config) if row.id == ident)


def test_a_row_is_recorded_pending_and_its_outcome_can_be_set(config):
    with vault_lock(config):
        ident = reception.record(config, "letter", "On small · rooms", "[[Drafts/On small rooms]]", "^3f2a",
                                 at=datetime(2026, 11, 4, 9, 12))
    path = config.vault_path / "agent/Reception/2026-11.md"
    assert fm.read(path)[0]["kind"] == "log"
    assert f"- 2026-11-04 09:12 · {ident} · letter · On small - rooms · pending · [[Drafts/On small rooms]] · portrait:3f2a" \
        in path.read_text()
    assert reception.set_outcome(config, ident, "opened").outcome == "opened"
    assert reception.set_outcome(config, ident, "declined:not-now").outcome == "declined:not-now"
    for bad in ("maybe", "used:yes", "declined:"):
        with pytest.raises(ValueError):
            reception.set_outcome(config, ident, bad)
    with pytest.raises(ValueError, match="kind must be one of"):
        reception.record(config, "poem", "x", "S")


def test_ideas_are_recorded_when_offered_and_settled_by_the_answer(config):
    assert ideas.add(config, date(2026, 11, 3), "Track sponsor questions", "START", "so the sales notes stay current")
    assert ideas.add(config, TODAY, "Draft the onboarding guide", "START", "new hires ask the same things")
    rows = {row.what: row for _, row in reception.rows(config)}
    assert rows["Track sponsor questions"].kind == "idea" and rows["Track sponsor questions"].outcome == "pending"
    opened = {item.text: item.id for item in ideas._open(config, TODAY)}
    ideas.respond(config, opened["Track sponsor questions"], TODAY, "yes")
    ideas.respond(config, opened["Draft the onboarding guide"], TODAY, "stop-asking")
    rows = {row.what: row.outcome for _, row in reception.rows(config)}
    assert rows == {"Track sponsor questions": "used", "Draft the onboarding guide": "declined:stop-asking"}


def test_asks_are_recorded_and_settled_when_answered_or_dismissed(config):
    journal.add_ask(config, TODAY, "Which inbox should I watch for sponsor mail?", "START")
    journal.add_ask(config, TODAY, "Should I book the venue?", None)
    asked = {item.text: item.id for item in needs.items(config, TODAY)}
    with vault_lock(config):
        needs.resolve(config, asked["Which inbox should I watch for sponsor mail?"], "they said the shared one", TODAY)
        needs.dismiss(config, asked["Should I book the venue?"], TODAY, "no")
    rows = {row.what: (row.kind, row.outcome) for _, row in reception.rows(config)}
    assert rows == {"Which inbox should I watch for sponsor mail?": ("ask", "used"),
                    "Should I book the venue?": ("ask", "declined:no")}


def test_the_night_settles_silence_as_ignored_where_opens_show_and_unknown_where_they_cannot(config):
    with vault_lock(config):
        old_mail = reception.record(config, "message", "Parcel went to the wrong city", "START", at=datetime(2026, 10, 30, 8))
        old_app = reception.record(config, "work", "Q4 board pack", "START", at=datetime(2026, 10, 29, 8))
        fresh = reception.record(config, "brief", "Morning", "START", at=datetime(2026, 11, 5, 8))
        used = reception.record(config, "draft", "Reply to Sam", "START", at=datetime(2026, 10, 20, 8))
        reception.set_outcome(config, old_app, "delivered")
        reception.set_outcome(config, used, "used")
        settled = reception.settle(config, TODAY)
    assert {row.id for row in settled} == {old_mail, old_app}
    assert (outcome(config, old_mail), outcome(config, old_app)) == ("unknown", "ignored")
    assert (outcome(config, fresh), outcome(config, used)) == ("pending", "used")


def test_summary_gives_hit_rates_by_kind_and_portrait_line(config):
    with vault_lock(config):
        for what, kind, portrait, result in [("a", "letter", "3f2a", "used"), ("b", "letter", "3f2a", "ignored"),
                                             ("c", "idea", "", "declined:no"), ("d", "idea", "91c0", "liked"),
                                             ("e", "brief", "", "pending")]:
            ident = reception.record(config, kind, what, "START", portrait, at=datetime(2026, 11, 1, 9))
            if result != "pending":
                reception.set_outcome(config, ident, result)
    data = reception.summary(config, TODAY)
    assert data["all"]["offered"] == 5 and data["all"]["hitRate"] == 0.5
    assert data["byKind"]["letter"]["hitRate"] == 0.5 and data["byKind"]["brief"]["hitRate"] is None
    assert data["byPortrait"]["3f2a"]["offered"] == 2 and data["byPortrait"]["91c0"]["liked"] == 1
    assert "hit rate 50%" in reception.render_summary(data)


def review(folder, number, author, merged, *comments):
    fm.write(folder / f"{number}.md", {"pr": number, "title": "t", "author": author, "merged": merged},
             "".join(f"- {line}\n" for line in comments))


def test_burden_counts_peoples_corrections_on_the_intellects_prs_by_week(tmp_path):
    folder = tmp_path / "reviews"
    folder.mkdir()
    review(folder, 1, "intellect", "2026-10-27", "2026-10-27 · noah · a.ts · Use the service.",
           "2026-10-27 · noah · a.ts · And here.", "2026-10-27 · codex[bot] · a.ts · Nit.")
    review(folder, 2, "intellect", "2026-10-29", "2026-10-29 · codex[bot] · b.ts · Nit.")
    review(folder, 3, "intellect", "2026-11-03", "2026-11-03 · priya · c.ts · Name it plainly.")
    review(folder, 4, "intellect", "2026-11-05")
    review(folder, 5, "kim", "2026-11-05", "2026-11-05 · noah · d.ts · Not the intellect's PR.")
    data = reception.burden(folder, "Intellect", TODAY, weeks=3)
    assert [(w["week"], w["prs"], w["corrections"], w["perPr"], w["clean"]) for w in data["weeks"]] == [
        ("2026-10-19", 0, 0, None, None), ("2026-10-26", 2, 2, 1.0, 0.5), ("2026-11-02", 2, 1, 0.5, 0.5)]
    assert "- 2026-11-02: 2 PRs, 1 corrections, 0.5 per PR, 50% with none" in reception.render_burden(data)


def test_cli_adds_sets_summarises_and_measures(vault, config, capsys, tmp_path):
    cli.main(["reception", "add", "--vault", str(vault), "--kind", "work", "--what", "Q4 plan", "--source", "START",
              "--portrait", "^3f2a"])
    ident = capsys.readouterr().out.strip()
    cli.main(["reception", "set", ident, "liked", "--vault", str(vault)])
    assert capsys.readouterr().out.startswith(f"{ident} liked: work Q4 plan")
    cli.main(["reception", "--vault", str(vault), "--json"])
    assert json.loads(capsys.readouterr().out)["byPortrait"]["3f2a"]["liked"] == 1
    with pytest.raises(SystemExit, match="add needs --kind"):
        cli.main(["reception", "add", "--vault", str(vault), "--kind", "work"])
    with pytest.raises(SystemExit, match="no reception row"):
        cli.main(["reception", "set", "000000", "used", "--vault", str(vault)])
    (tmp_path / "reviews").mkdir()
    cli.main(["reception", "burden", "--vault", str(vault), "--reviews", str(tmp_path / "reviews"), "--author", "me",
              "--weeks", "1"])
    assert "Correction burden on me's merged PRs" in capsys.readouterr().out


def test_a_row_can_be_written_after_the_fact_with_its_real_time(vault, config, capsys):
    cli.main(["reception", "add", "--vault", str(vault), "--kind", "idea", "--what", "Old idea", "--source", "START",
              "--at", "2026-09-14 10:30"])
    ident = capsys.readouterr().out.strip()
    assert [row.at for _, row in reception.rows(config) if row.id == ident] == ["2026-09-14 10:30"]
    assert (vault / "agent/Reception/2026-09.md").exists()
