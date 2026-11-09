"""Earned autonomy describes the intellect's calibration, with a person's safety controls."""
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
import json
import re

import pytest

from brain import cli, consults, fm, needs, routing, tasks
from brain.config import Config
from brain.lock import vault_lock
from conftest import snapshot

TODAY = date(2026, 11, 12)
KIM, LEE, ANA, NOAH = "slack:U01KIM", "slack:U02LEE", "slack:U03ANA", "slack:U04NOAH"
TABLE = """| area | owner | backup | covers | wait | source |
| --- | --- | --- | --- | --- | --- |
| infrastructure | Kim (slack:U01KIM) | Lee (slack:U02LEE) | staging, releases | | stated |
| analytics | Ana (slack:U03ANA) | | dashboards | | stated |
"""


@pytest.fixture
def enterprise(vault, monkeypatch):
    (vault / "agent/Owners.md").write_text(TABLE)
    monkeypatch.setattr(Config, "now", lambda self, moment=None: datetime(2026, 11, 12, 10, tzinfo=self.zone))
    monkeypatch.setenv("BRAIN_TODAY", str(TODAY))
    return replace(Config.load(vault), edition="enterprise", owner_user_ids=[NOAH])


def log(config, outcome, ident, day=TODAY, area="infrastructure", person=KIM, text="Update the staging plan", source=""):
    bullet = f"- {day} · consult {outcome} · {text} · by {person} · {area} · - · id:{ident:012x}"
    if source:
        bullet += f"\n  - source: [[{source}]]"
    needs.answered(config, day, bullet)


def outcomes(config, values, *, start=1, day=TODAY, area="infrastructure"):
    for ident, value in enumerate(values, start):
        log(config, value, ident, day, area)


def control(config, kind, rung, day=TODAY, area="infrastructure", by=KIM):
    consults._area_note(config, area, "Autonomy", f"- {day} · Kim · {kind}: {rung} · by:{by}")


def derived(config, area="infrastructure"):
    return consults.autonomy(config, area)[0]


def earn(config, climbs=2, area="infrastructure"):
    outcomes(config, ["confirmed"] * (10 * climbs), area=area)


def command(config, *args):
    cli.main(["consult", *args, "--vault", str(config.vault_path)])


def cli_edition(config):
    path = config.repo / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\nowner_user_ids = ["slack:U04NOAH"]\n')


@pytest.mark.parametrize("confirmed, expected", [(7, "ask"), (8, "ask-on-big"), (10, "ask-on-big")])
def test_full_window_threshold(enterprise, confirmed, expected):
    outcomes(enterprise, ["changed"] * (10 - confirmed) + ["confirmed"] * confirmed)
    result = derived(enterprise)
    assert result["rung"] == expected
    assert result["ceiling"] == consults.DEFAULT_CEILING == "act-confirm"
    if confirmed >= 8:
        assert result["window"] == []
        move, = result["events"]
        assert move["kind"] == "climb" and move["date"] == str(TODAY)
        assert len(move["window"]) == 10 and move["link"] == "agent/Answered/2026-11.md"
    else:
        assert len(result["window"]) == 10 and result["events"] == []


def test_partial_window_and_explanation(enterprise):
    outcomes(enterprise, ["changed"] + ["confirmed"] * 5)
    result = derived(enterprise)
    assert result["rung"] == "ask"
    assert "3 more confirmations in the next 4" in result["needed"]
    outcomes(enterprise, ["confirmed"] * 3, start=7)
    assert derived(enterprise)["rung"] == "ask"   # eight confirmations still need a full window


def test_rolling_changes_block_then_age_out(enterprise):
    outcomes(enterprise, ["changed"] * 10 + ["confirmed"] * 7)
    result = derived(enterprise)
    assert result["rung"] == "ask"
    assert [row["id"] for row in result["window"]] == [f"{i:012x}" for i in range(8, 18)]
    log(enterprise, "confirmed", 18)
    assert derived(enterprise)["rung"] == "ask-on-big"
    assert derived(enterprise)["window"] == []


def test_multiple_climbs_clear_window_and_default_ceiling(enterprise):
    outcomes(enterprise, ["confirmed"] * 19)
    result = derived(enterprise)
    assert result["rung"] == "ask-on-big" and len(result["window"]) == 9
    outcomes(enterprise, ["confirmed"] * 21, start=20)
    result = derived(enterprise)
    assert result["rung"] == "act-confirm" and len(result["window"]) == 10
    assert [event["to"] for event in result["events"]] == ["ask-on-big", "act-confirm"]
    assert "at ceiling act-confirm" in result["needed"]


def test_reversal_drops_and_clears(enterprise):
    earn(enterprise)
    outcomes(enterprise, ["confirmed"] * 9, start=21)
    log(enterprise, "reversed", 40, person="-")
    result = derived(enterprise)
    assert result["rung"] == "ask-on-big" and result["window"] == []
    event = result["events"][-1]
    assert event["kind"] == "reversed" and event["id"] == f"{40:012x}"
    outcomes(enterprise, ["confirmed"] * 9, start=41)
    assert derived(enterprise)["rung"] == "ask-on-big"


def test_controls_replay_at_their_dates_and_keep_note_order(enterprise):
    early = TODAY - timedelta(days=20)
    earn(enterprise)
    control(enterprise, "ceiling", "ask", early)
    control(enterprise, "ceiling", "act-report", early + timedelta(days=1))
    control(enterprise, "lower", "ask", TODAY + timedelta(days=1), by=ANA)
    outcomes(enterprise, ["confirmed"] * 10, start=21, day=TODAY + timedelta(days=2))
    result = derived(enterprise)
    assert result["rung"] == "ask-on-big" and result["ceiling"] == "act-report"
    assert [event["kind"] for event in result["events"]] == ["ceiling", "ceiling", "climb", "climb", "lower", "climb"]
    control(enterprise, "lower", "ask", TODAY + timedelta(days=2))
    control(enterprise, "ceiling", "act-confirm", TODAY + timedelta(days=2))
    assert derived(enterprise)["rung"] == "ask"   # same-date person controls follow log events


def test_explicit_report_ceiling_and_ceiling_clamps_immediately(enterprise):
    control(enterprise, "ceiling", "act-report", TODAY - timedelta(days=1))
    earn(enterprise, 3)
    result = derived(enterprise)
    assert result["rung"] == result["ceiling"] == "act-report"
    control(enterprise, "ceiling", "ask-on-big")
    result = derived(enterprise)
    assert result["rung"] == result["ceiling"] == "ask-on-big"


def test_higher_ceiling_uses_a_qualifying_window_earned_at_the_cap(enterprise):
    earn(enterprise, 3)
    assert derived(enterprise)["rung"] == "act-confirm"
    consults.ceiling(enterprise, "infrastructure", "act-report", KIM)
    result = derived(enterprise)
    assert result["rung"] == "act-report" and result["window"] == []
    assert [event["kind"] for event in result["events"][-2:]] == ["ceiling", "climb"]
    assert len(result["events"][-1]["window"]) == 10


def test_restricted_stays_ask_despite_record(enterprise):
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE + "\n## Restricted\n\n- infrastructure\n")
    control(enterprise, "ceiling", "act-report", TODAY - timedelta(days=1))
    earn(enterprise, 4)
    result = derived(enterprise)
    assert result["rung"] == "ask" and result["restricted"]
    assert not any(event["kind"] == "climb" for event in result["events"])
    with pytest.raises(ValueError, match="rung ask; restricted"):
        consults.act(enterprise, "infrastructure", "Update the staging plan")


def test_ignored_outcomes_and_area_discovery(enterprise):
    for ident, outcome in enumerate(("asked", "cited", "redirected", "unanswered", "overruled", "acted"), 1):
        log(enterprise, outcome, ident, area="operations")
    control(enterprise, "lower", "ask", area="design")
    consults._area_note(enterprise, "unused", "Evidence", "Nothing about autonomy")
    results = consults.autonomy(enterprise)
    assert [row["area"] for row in results] == ["analytics", "design", "infrastructure", "operations"]
    assert all(row["rung"] == "ask" and row["window"] == [] for row in results)
    assert derived(enterprise, "new area")["rung"] == "ask"


def test_deterministic_read_only_and_no_people_counts(enterprise, monkeypatch):
    earn(enterprise)
    control(enterprise, "lower", "ask-on-big", by=ANA)
    before = snapshot(enterprise.repo)
    monkeypatch.setattr(consults, "vault_lock", lambda config: pytest.fail("read-only derivation took a writer lock"))
    first = consults.autonomy(enterprise)
    assert consults.autonomy(enterprise) == first
    assert snapshot(enterprise.repo) == before
    output = json.dumps(first)
    assert all(value not in output for value in ("people", "person", KIM, ANA, "Kim", "Ana", "score"))


@pytest.mark.parametrize("climbs, rung", [(0, "ask"), (1, "ask-on-big")])
def test_act_refused_below_act_confirm(enterprise, climbs, rung):
    earn(enterprise, climbs)
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match=f"rung {rung}; .*confirmations"):
        consults.act(enterprise, "infrastructure", "Update the staging plan")
    assert snapshot(enterprise.repo) == before


def test_act_confirm_log_owner_and_task_evidence(enterprise):
    earn(enterprise)
    tasks.set_fields(enterprise, "T-001", TODAY, values={"resume": "2026-11-16 13:00"})
    entry = consults.act(enterprise, "INFRASTRUCTURE", "Update the staging plan", "T-001")
    assert re.fullmatch(r"[0-9a-f]{12}", entry.id)
    confirm, = consults.load(enterprise)
    assert confirm.kind == "confirm" and confirm.to == KIM and confirm.id == entry.id
    assert confirm.text == "Acted without asking: Update the staging plan"
    assert confirm.source == "agent/Tasks#T-001" and confirm.default == "Update the staging plan"
    response = routing.responses(enterprise)[-1]
    assert response["outcome"] == "acted" and response["person"] == "-" and response["hours"] is None
    assert response["id"] == entry.id and response["source"] == confirm.source
    line = next(line for line in (enterprise.vault_path / response["link"]).read_text().splitlines() if "consult acted" in line)
    assert len(line.split(" · ")) == 7 and " · - · id:" in line
    task = tasks.load_tasks(enterprise)[0][0]
    assert "Acted on its own in infrastructure (rung act-confirm): Update the staging plan" in task.body
    assert "Resume" not in task.body


def test_act_report_has_only_log_and_later_reversal_keeps_task_source(enterprise):
    control(enterprise, "ceiling", "act-report", TODAY - timedelta(days=1))
    earn(enterprise, 3)
    entry = consults.act(enterprise, "infrastructure", "Update the staging plan", "T-001")
    assert consults.load(enterprise) == [] and not consults.path_for(enterprise).exists()
    consults.reverse(enterprise, entry.id, ANA, "Please restore the earlier plan")
    assert derived(enterprise)["rung"] == "act-confirm"
    assert "Please restore the earlier plan" in tasks.load_tasks(enterprise)[0][0].body


def test_act_owner_fallback_and_missing_recipient(enterprise):
    earn(enterprise, area="operations")
    entry = consults.act(enterprise, "operations", "Update the staging plan")
    assert entry.to == NOAH
    config = replace(enterprise, owner_user_ids=[])
    with pytest.raises(ValueError, match="needs an area owner"):
        consults.act(config, "operations", "Update the release plan")


@pytest.mark.parametrize("text,task,match", [
    ("Update\nthe plan", "", "single-line"),
    ("Update the plan", "T-999", "not an open task"),
])
def test_act_refusals_write_nothing(enterprise, text, task, match):
    earn(enterprise)
    before = snapshot(enterprise.repo)
    with pytest.raises(ValueError, match=match):
        consults.act(enterprise, "infrastructure", text, task)
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("reason,by,reversed_", [("no", ANA, True), ("no", "", True), ("done", ANA, False)])
def test_dismiss_act_confirm(enterprise, reason, by, reversed_):
    earn(enterprise)
    entry = consults.act(enterprise, "infrastructure", "Update the staging plan", "T-001")
    before = routing.responses(enterprise)
    with vault_lock(enterprise):
        needs.dismiss(enterprise, entry.id, TODAY, reason, by=by)
    assert consults.load(enterprise) == []
    reversals = [row for row in routing.responses(enterprise) if row["outcome"] == "reversed"]
    assert len(reversals) == int(reversed_)
    result = derived(enterprise)
    assert result["rung"] == ("ask-on-big" if reversed_ else "act-confirm")
    if reversed_:
        assert reversals[0]["person"] == (by or "-") and reversals[0]["id"] == entry.id
        assert reversals[0]["text"] == "Update the staging plan" and reversals[0]["hours"] is None
        assert result["window"] == []
    else:
        assert routing.responses(enterprise) == before


def test_expiry_accepts_and_reverse_after_expiry_updates_task(enterprise):
    earn(enterprise)
    entry = consults.act(enterprise, "infrastructure", "Update the staging plan", "T-001")
    before = routing.responses(enterprise)
    assert consults.expire_confirms(enterprise, TODAY + timedelta(days=needs.CONFIRM_DAYS))
    assert routing.responses(enterprise) == before
    assert derived(enterprise)["rung"] == "act-confirm"
    consults.reverse(enterprise, entry.id, ANA, "Restore the earlier plan")
    assert derived(enterprise)["rung"] == "ask-on-big"
    assert "Restore the earlier plan" in tasks.load_tasks(enterprise)[0][0].body
    with pytest.raises(ValueError, match="already reversed"):
        consults.reverse(enterprise, entry.id, ANA, "Restore the earlier plan")


def test_reverse_command_and_unknown_id(enterprise, capsys):
    cli_edition(enterprise)
    earn(enterprise)
    command(enterprise, "act", "--area", "infrastructure", "--for", "Update the staging plan", "--task", "T-001")
    ident = capsys.readouterr().out.strip()
    command(enterprise, "reverse", ident, "--by", ANA, "--answer", "Restore the earlier plan")
    assert "reversal recorded" in capsys.readouterr().out
    assert consults.load(enterprise) == []
    response = routing.responses(enterprise)[-1]
    assert response["answer"] == "Restore the earlier plan" and response["person"] == ANA
    assert response["id"] == ident and response["outcome"] == "reversed"
    assert "Restore the earlier plan" in tasks.load_tasks(enterprise)[0][0].body
    with pytest.raises(SystemExit, match="no unique autonomous act"):
        command(enterprise, "reverse", "00000000ffff", "--by", ANA, "--answer", "Restore the plan")


def test_cli_dismiss_carries_known_speaker(enterprise, monkeypatch, capsys):
    cli_edition(enterprise)
    earn(enterprise)
    entry = consults.act(enterprise, "infrastructure", "Update the staging plan")
    monkeypatch.setenv("BRAIN_SPEAKER", ANA)
    cli.main(["needs-you", "--vault", str(enterprise.vault_path), "--dismiss", entry.id, "--reason", "no"])
    assert "dismissed" in capsys.readouterr().out
    assert routing.responses(enterprise)[-1]["person"] == ANA


def test_lower_by_anyone_defaults_one_step_and_never_raises(enterprise, capsys):
    cli_edition(enterprise)
    earn(enterprise)
    command(enterprise, "lower", "--area", "infrastructure", "--by", ANA)
    assert "lower recorded" in capsys.readouterr().out
    assert derived(enterprise)["rung"] == "ask-on-big" and derived(enterprise)["window"] == []
    body = fm.read(routing.area_path(enterprise, "infrastructure"))[1]
    assert f"- {TODAY} · Ana · lower: ask-on-big · by:{ANA}" in body
    consults.lower(enterprise, "infrastructure", ANA, "act-report")
    assert derived(enterprise)["rung"] == "ask-on-big"
    consults.lower(enterprise, "infrastructure", ANA, "ask")
    consults.lower(enterprise, "infrastructure", ANA)
    assert derived(enterprise)["rung"] == "ask"


@pytest.mark.parametrize("person", [KIM, LEE, NOAH])
def test_ceiling_owner_backup_operator_allowed(enterprise, person):
    consults.ceiling(enterprise, "infrastructure", "act-report", person)
    assert derived(enterprise)["ceiling"] == "act-report"
    assert derived(enterprise)["rung"] == "ask"   # a ceiling never grants a rung
    assert f"ceiling: act-report · by:{person}" in fm.read(routing.area_path(enterprise, "infrastructure"))[1]


def test_ceiling_alias_and_bare_operator_id(enterprise):
    path = enterprise.vault_path / "people/Kim/Profile.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"# Kim\n- **Slack:** {KIM.split(':')[1]}\n- **GitHub:** kim\n")
    consults.ceiling(enterprise, "infrastructure", "act-report", "github:kim")
    assert derived(enterprise)["ceiling"] == "act-report"
    consults.ceiling(replace(enterprise, owner_user_ids=["U04NOAH"]), "operations", "act-report", NOAH)
    assert derived(enterprise, "operations")["ceiling"] == "act-report"


def test_ceiling_command_refuses_non_owner_and_allows_owner(enterprise, capsys):
    cli_edition(enterprise)
    before = snapshot(enterprise.repo)
    with pytest.raises(SystemExit, match="owner or backup"):
        command(enterprise, "ceiling", "--area", "infrastructure", "--rung", "act-report", "--by", ANA)
    assert snapshot(enterprise.repo) == before
    command(enterprise, "ceiling", "--area", "infrastructure", "--rung", "act-report", "--by", KIM)
    assert "ceiling recorded" in capsys.readouterr().out


def test_existing_stats_and_candidates_unchanged_by_acts_and_reversals(enterprise):
    earn(enterprise)
    before_stats = consults.stats(enterprise)
    before_candidates = [asdict(row) for row in consults.candidates(enterprise, "Update the staging plan")]
    entry = consults.act(enterprise, "infrastructure", "Update the staging plan")
    consults.reverse(enterprise, entry.id, ANA, "Restore the staging plan")
    log(enterprise, "acted", 100, area="operations", person="-", text="Update the staging plan")
    log(enterprise, "reversed", 100, area="operations", person="-", text="Update the staging plan")
    assert consults.stats(enterprise) == before_stats
    assert [asdict(row) for row in consults.candidates(enterprise, "Update the staging plan")] == before_candidates


def test_autonomy_cli_json_text_and_area_filter(enterprise, capsys):
    cli_edition(enterprise)
    earn(enterprise)
    command(enterprise, "autonomy", "--area", "infrastructure", "--json")
    assert json.loads(capsys.readouterr().out) == consults.autonomy(enterprise, "infrastructure")
    command(enterprise, "autonomy")
    output = capsys.readouterr().out
    assert "analytics" in output and "act-confirm" in output and "[[agent/Answered/2026-11.md]]" in output
    assert all(value not in output for value in (KIM, "Kim", "people", "score"))
