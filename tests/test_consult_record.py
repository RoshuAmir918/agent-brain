"""The record answers first and keeps decisions in the speaker's words."""
from datetime import datetime
import json
import re

import pytest

from brain import cli, consults, fm, needs, routing, tasks
from brain.config import Config
from conftest import snapshot

KIM = "chat:kim"
LEE = "chat:lee"
ANA = "chat:ana"
QUESTION = "Touch the staging cluster during release week?"
DEFAULT = "Leave staging alone."
WHY = "Kim coordinates the release."
TABLE = """| area | owner | backup | covers | wait | source |
| --- | --- | --- | --- | --- | --- |
| infrastructure | Kim (chat:kim) | Lee (chat:lee) | staging, deployments | 4 | team meeting |
| analytics | Lee (chat:lee) | | warehouse, exports | 4 | team meeting |
"""


def clock(monkeypatch, at):
    monkeypatch.setattr(Config, "now", lambda self: datetime.fromisoformat(at).replace(tzinfo=self.zone))
    monkeypatch.setenv("BRAIN_TODAY", at[:10])


@pytest.fixture
def enterprise(vault, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    (vault / "agent/Owners.md").write_text(TABLE)
    clock(monkeypatch, "2026-11-12 10:00")
    return Config.load(vault)


def ask(config, **kwargs):
    values = dict(to=KIM, text=QUESTION, default=DEFAULT, why=WHY, area="infrastructure")
    values.update(kwargs)
    return consults.ask(config, **values)


def decide(config, **kwargs):
    values = dict(area="infrastructure", by=KIM, words="Leave staging alone during release week.",
                  source="team channel, November 12")
    values.update(kwargs)
    return consults.decide(config, **values)


def run(config, *args):
    cli.main(["consult", *args, "--vault", str(config.vault_path)])


def test_decisions_ranking_status_sources_and_read_only(enterprise, monkeypatch, capsys):
    clock(monkeypatch, "2026-11-09 10:00")
    first = ask(enterprise, task="T-001")
    consults.answer(enterprise, first.id, "confirmed", "Keep the cluster unchanged.", KIM)
    clock(monkeypatch, "2026-11-10 10:00")
    second = decide(enterprise, words="Leave staging unchanged.")
    consults.overrule(enterprise, second, "Use the isolated cluster instead.", LEE)
    clock(monkeypatch, "2026-11-11 10:00")
    third = decide(enterprise, words="Keep staging cluster logs.", supersedes=first.id)
    clock(monkeypatch, "2026-11-12 10:00")
    fourth = decide(enterprise, words="Keep staging cluster logs during release week.")
    decide(enterprise, area="analytics", words="Keep unrelated warehouse exports.")
    before = snapshot(enterprise.repo)
    run(enterprise, "decisions", "--about", "staging cluster release week", "--json")
    entries = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in entries] == [fourth, first.id, third, second]
    assert [row["status"] for row in entries] == ["stood", "superseded", "stood", "overruled"]
    assert entries[1] == dict(id=first.id, area="infrastructure", date="2026-11-09", name="Kim",
                             words="Keep the cluster unchanged.", question=QUESTION, source="agent/Tasks#T-001",
                             status="superseded", overrules=[],
                             superseded=dict(date="2026-11-11", id=third, name="Kim"),
                             link="agent/Areas/infrastructure.md")
    assert entries[-1]["overrules"] == [dict(date="2026-11-10", name="Lee", words="Use the isolated cluster instead.")]
    assert consults.decisions(enterprise, "staging cluster release week", limit=2) == entries[:2]
    assert len(consults.decisions(enterprise, "staging cluster release week", "analytics")) == 5
    assert consults.decisions(enterprise, "unrelated query", "analytics")[0]["area"] == "analytics"
    run(enterprise, "decisions", "--about", "staging cluster release week")
    output = capsys.readouterr().out
    assert output.index(fourth) < output.index(first.id)
    assert "overruled 2026-11-10 by Lee: Use the isolated cluster instead." in output
    assert f"superseded 2026-11-11 by {third}: Kim" in output
    assert "source: agent/Tasks#T-001" in output and "[[agent/Areas/infrastructure.md]]" in output
    assert snapshot(enterprise.repo) == before
    with pytest.raises(ValueError, match="positive"):
        consults.decisions(enterprise, "staging", limit=0)


def test_decisions_newest_breaks_equal_word_counts(enterprise, monkeypatch):
    first = decide(enterprise)
    clock(monkeypatch, "2026-11-13 10:00")
    second = decide(enterprise)
    assert [row["id"] for row in consults.decisions(enterprise, "staging release")] == [second, first]
    assert consults.decisions(enterprise, "unmatched") == []


def test_ask_checks_record_and_keeps_gap_out_of_recipient_detail(enterprise, capsys):
    ident = decide(enterprise)
    before = snapshot(enterprise.repo)
    arguments = ("ask", "--to", KIM, "--ask", QUESTION, "--default", DEFAULT, "--why", WHY,
                 "--area", "infrastructure")
    with pytest.raises(SystemExit) as error:
        run(enterprise, *arguments)
    assert error.value.code != 0
    message = str(error.value)
    assert f"{ident} · 2026-11-12 · Kim · Leave staging alone during release week." in message
    assert "brain consult cite" in message and '--not-covered "<why the record does not answer this>"' in message
    assert snapshot(enterprise.repo) == before
    reason = "The record covers releases, but this is an isolated test cluster."
    run(enterprise, *arguments, "--not-covered", reason)
    new = capsys.readouterr().out.strip()
    entry, = consults.load(enterprise)
    assert entry.id == new and entry.not_covered == reason
    assert f"  - not-covered: {reason}" in consults.path_for(enterprise).read_text()
    assert entry.item().detail == WHY and needs.items(enterprise, enterprise.today())[0].detail == WHY
    assert reason not in (enterprise.vault_path / "agent/Answered/2026-11.md").read_text()
    consults.answer(enterprise, new, "redirected", "Ask Lee about test clusters.", KIM, LEE)
    assert consults.load(enterprise)[0].not_covered == reason
    needs.dismiss(enterprise, new, enterprise.today())
    assert consults.load(enterprise)[0].not_covered == reason


def test_ask_matches_other_relevant_area_and_asked_words(enterprise):
    ident = decide(enterprise, area="analytics", words="Leave staging alone during release week.")
    with pytest.raises(ValueError, match=ident):
        ask(enterprise)
    first = ask(enterprise, text="Keep warehouse exports in daily batches?", area="analytics")
    consults.answer(enterprise, first.id, "confirmed", "Yes.", LEE)
    with pytest.raises(ValueError, match=first.id):
        ask(enterprise, text="Keep warehouse exports in hourly batches?", area="analytics")


def test_ask_without_two_shared_words_still_creates_once(enterprise):
    decide(enterprise, words="Keep staging unchanged.")
    entry = ask(enterprise, text="Rotate staging credentials before the weekend?")
    before = snapshot(enterprise.repo)
    assert ask(enterprise, text=entry.text).id == entry.id
    assert snapshot(enterprise.repo) == before
    assert entry.not_covered == ""
    asked, = routing.responses(enterprise)
    assert asked["outcome"] == "asked" and asked["id"] == entry.id and asked["hours"] is None


@pytest.mark.parametrize("call,value,match", [
    ("ask", " ", "nonempty"),
    ("cite", "one\ntwo", "single-line"),
    ("cite-task", "T-999", "not an open task"),
])
def test_ask_and_cite_refuse_bad_fields_without_writing(enterprise, call, value, match):
    ident = decide(enterprise) if call != "ask" else None
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match=match):
        if call == "ask":
            ask(enterprise, not_covered=value)
        elif call == "cite":
            consults.cite(enterprise, ident, value)
        else:
            consults.cite(enterprise, ident, "Run isolated tests.", task=value)
    assert snapshot(enterprise.repo) == before


def test_cite_logs_seven_parts_and_wakes_named_task(enterprise, capsys):
    first = ask(enterprise)
    words = "Keep staging unchanged during release week."
    consults.answer(enterprise, first.id, "confirmed", words, KIM)
    tasks.set_fields(enterprise, "T-001", enterprise.today(), values={"resume": "2026-11-16 13:00"})
    run(enterprise, "cite", first.id, "--task", "T-001", "--for", "Run the release tests in an isolated environment.")
    assert f"{first.id}: citation recorded" in capsys.readouterr().out
    line = (enterprise.vault_path / "agent/Answered/2026-11.md").read_text().splitlines()[-1]
    assert line == (f"- 2026-11-12 · consult cited · Run the release tests in an isolated environment. · by {KIM}"
                    f" · infrastructure · - · id:{first.id}")
    assert len(line.split(" · ")) == 7
    task = next(row for row in tasks.all_tasks(enterprise) if row.tid == "T-001")
    assert task.resume is None
    assert fm.field(task.body, "Latest evidence") == f"2026-11-12 — Per Kim's decision of November 12: {words}"
    assert routing.responses(enterprise)[-1]["outcome"] == "cited"


def test_cite_stated_person_and_historical_name_fallback(enterprise):
    ident = decide(enterprise, by=ANA)
    consults.cite(enterprise, ident, "Use an isolated test environment.")
    assert routing.responses(enterprise)[-1]["person"] == ANA
    path = routing.area_path(enterprise, "infrastructure")
    body = path.read_text() + "\n- 2026-11-10 · Ana · Keep the deployment isolated. · id:123456abcdef\n"
    path.write_text(body)
    consults.cite(enterprise, "123456abcdef", "Use isolated deployment tests.")
    assert routing.responses(enterprise)[-1]["person"] == "Ana"


@pytest.mark.parametrize("status", ["unknown", "overruled", "superseded"])
def test_cite_refuses_invalid_decisions_without_writing(enterprise, status):
    ident = decide(enterprise)
    replacement = ""
    if status == "unknown":
        ident = "000000000000"
    elif status == "overruled":
        consults.overrule(enterprise, ident, "Use the isolated cluster.", LEE)
        replacement = "Lee: Use the isolated cluster."
    else:
        replacement = decide(enterprise, by=LEE, supersedes=ident)
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError) as error:
        consults.cite(enterprise, ident, "Run isolated deployment tests.", task="T-001")
    assert ("no unique recorded decision" if status == "unknown" else status) in str(error.value)
    assert replacement in str(error.value)
    assert snapshot(enterprise.repo) == before


def test_decide_exact_words_source_id_and_supersession(enterprise, capsys):
    words = "Keep  staging alone · until release checks pass."
    run(enterprise, "decide", "--area", "Infrastructure", "--by", KIM, "--words", words,
        "--source", "team channel, November 12")
    ident = capsys.readouterr().out.strip()
    assert re.fullmatch(r"[0-9a-f]{12}", ident)
    path = routing.area_path(enterprise, "infrastructure")
    assert f"- 2026-11-12 · Kim · {words} · team channel, November 12 · id:{ident}\n  - by: {KIM}" in path.read_text()
    first, = consults.decisions(enterprise, "staging")
    assert first["words"] == words and first["source"] == "team channel, November 12"
    replacement = decide(enterprise, by=LEE, source="agent/Tasks#T-001", supersedes=ident)
    assert f"  - superseded 2026-11-12 by {replacement}: Lee" in path.read_text()
    assert f"· [[agent/Tasks#T-001]] · id:{replacement}" in path.read_text()
    entries = {row.ident: row for row in routing.decisions(fm.read(path)[1], "infrastructure")}
    assert entries[ident].status == "superseded"
    assert entries[replacement].status == "stood" and entries[replacement].source == "agent/Tasks#T-001"
    assert entries[ident].superseded == dict(date="2026-11-12", id=replacement, name="Lee")
    assert not (enterprise.vault_path / "agent/Answered").exists()
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match="no unique recorded decision"):
        decide(enterprise, supersedes="000000000000")
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("values,match", [
    (dict(words=""), "own words"), (dict(words="one\ntwo"), "single-line"),
    (dict(source=""), "--source must name"), (dict(source="one\ntwo"), "single-line"),
    (dict(source="channel · meeting"), "--source must name"), (dict(by="Kim"), "<platform>:<id>"),
])
def test_decide_refuses_invalid_fields_without_writing(enterprise, values, match):
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match=match):
        decide(enterprise, **values)
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("source,rendered", [("Tasks#T-001", "[[Tasks#T-001]]"),
                                           ("https://example.com/thread/1", "https://example.com/thread/1")])
def test_decide_sources_round_trip(enterprise, source, rendered):
    ident = decide(enterprise, source=source)
    assert f" · {rendered} · id:{ident}" in routing.area_path(enterprise, "infrastructure").read_text()
    entry, = consults.decisions(enterprise, "staging")
    assert entry["source"] == source


def test_supersession_keeps_other_annotations_and_latest_replacement(enterprise):
    first = decide(enterprise)
    second = decide(enterprise, by=LEE)
    consults.overrule(enterprise, first, "Use an isolated cluster.", LEE)
    replacement = decide(enterprise, by=ANA, supersedes=first)
    latest = decide(enterprise, by=LEE, supersedes=first)
    entries = {row["id"]: row for row in consults.decisions(enterprise, "staging")}
    assert entries[first]["overrules"] == [dict(date="2026-11-12", name="Lee", words="Use an isolated cluster.")]
    assert entries[first]["superseded"]["id"] == latest
    assert entries[second]["status"] == entries[replacement]["status"] == "stood"
    with pytest.raises(ValueError, match=latest):
        consults.cite(enterprise, first, "Run isolated tests.")


def test_candidates_replace_answered_and_count_stated_speaker(enterprise):
    first = ask(enterprise)
    consults.answer(enterprise, first.id, "confirmed", "Keep staging unchanged.", KIM)
    before = {row.person: row for row in consults.candidates(enterprise, "", "infrastructure")}
    assert before[KIM].score == routing.WEIGHTS["owner"] + routing.WEIGHTS["answered"]
    replacement = decide(enterprise, by=ANA, supersedes=first.id)
    after = {row.person: row for row in consults.candidates(enterprise, "", "infrastructure")}
    assert after[KIM].score == routing.WEIGHTS["owner"]
    assert after[ANA].score == routing.WEIGHTS["answered"]
    assert [row["kind"] for row in after[ANA].evidence] == ["answered"]
    assert after[ANA].median_hours is None
    consults.cite(enterprise, replacement, "Use an isolated environment.")
    assert {row.person: row for row in consults.candidates(enterprise, "", "infrastructure")} == after


def test_stats_weekly_record_share_preserves_existing_numbers(enterprise, monkeypatch, capsys):
    clock(monkeypatch, "2026-11-06 10:00")
    ident = decide(enterprise)
    first = ask(enterprise, area="analytics", text="Move warehouse exports to hourly batches?")
    assert ask(enterprise, area="analytics", text=first.text).id == first.id
    clock(monkeypatch, "2026-11-06 11:00")
    consults.answer(enterprise, first.id, "redirected", "Lee coordinates exports.", KIM, LEE)
    consults.cite(enterprise, ident, "Run isolated tests.")
    consults.cite(enterprise, ident, "Keep the release environment unchanged.")
    clock(monkeypatch, "2026-11-09 10:00")
    consults.answer(enterprise, first.id, "changed", "Keep daily warehouse exports.", LEE)
    ask(enterprise, text="Rotate deployment credentials before the weekend?")
    consults.cite(enterprise, ident, "Run isolated tests again.")
    result = consults.stats(enterprise)
    assert result["record_weekly"] == [dict(week="2026-W45", cited=2, asked=1, record_share=2 / 3),
                                       dict(week="2026-W46", cited=1, asked=1, record_share=0.5)]
    assert result["weekly"] == [dict(week="2026-W46", resolved=1, redirected=1, rate=1.0)]
    assert result["areas"] == {"analytics": dict(asked=2, changed=1, confirmed=0, redirected=1, overruled=0,
                                               unanswered=0, changed_share=1.0, median_hours=36)}
    assert result["people"][KIM]["asked"] == 1 and result["people"][LEE]["asked"] == 1
    run(enterprise, "stats", "--json")
    assert json.loads(capsys.readouterr().out) == result
    run(enterprise, "stats")
    assert "2026-W45 · cited 2 · asked 1 · record share 67%" in capsys.readouterr().out


def test_stats_record_only_weeks_and_iso_year(enterprise, monkeypatch):
    clock(monkeypatch, "2027-01-01 10:00")
    ident = decide(enterprise)
    consults.cite(enterprise, ident, "Run isolated tests.")
    result = consults.stats(enterprise)
    assert result["areas"] == result["people"] == {} and result["weekly"] == []
    assert result["record_weekly"] == [dict(week="2026-W53", cited=1, asked=0, record_share=1.0)]
    clock(monkeypatch, "2027-01-04 10:00")
    ask(enterprise, text="Rotate deployment credentials before the weekend?")
    assert consults.stats(enterprise)["record_weekly"][1] == dict(week="2027-W01", cited=0, asked=1, record_share=0.0)
