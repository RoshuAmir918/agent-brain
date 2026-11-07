"""Consults route consequential choices, keep people's words and never stall on silence."""
from dataclasses import asdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from brain import cli, consults, directory, fm, needs, nightly, owners, routing, state, tasks, validate
from brain.config import Config
from brain.lock import vault_lock
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 12)
NOAH = "slack:U01NOAH"
ROSHAN = "slack:U02ROSHAN"
KIM = "slack:U03KIM"
LEE = "slack:U04LEE"
QUESTION = "Touch the staging cluster during this release week?"
DEFAULT = "Leave staging alone and test on a branch environment."
WHY = "The release freeze may cover staging; a deploy there could break QA's run."
TABLE = """| area | owner | backup | covers | wait | source |
| --- | --- | --- | --- | --- | --- |
| infrastructure | Noah (slack:U01NOAH) | Roshan (slack:U02ROSHAN) | terraform, AWS, CI, deploys | 4 | stated 2026-11-10 |
| analytics | Roshan (slack:U02ROSHAN) | | dashboards, warehouse | | stated 2026-11-10 |
| product direction | Noah (slack:U01NOAH) | | roadmap | 8 | proposed 2026-11-10 |
"""


def clock(monkeypatch, at):
    moment = datetime.fromisoformat(at)
    monkeypatch.setattr(Config, "now", lambda self, moment=None: datetime.fromisoformat(at).replace(tzinfo=self.zone))
    monkeypatch.setenv("BRAIN_TODAY", moment.date().isoformat())


@pytest.fixture
def enterprise(vault, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    (vault / "agent/Owners.md").write_text(TABLE)
    clock(monkeypatch, "2026-11-12 10:03")
    return Config.load(vault)


def ask(config, **kwargs):
    values = dict(to=NOAH, area="infrastructure", text=QUESTION, default=DEFAULT, why=WHY)
    values.update(kwargs)
    values.setdefault("not_covered", "This choice needs a fresh answer for a different scope.")
    return consults.ask(config, **values)


def task(config):
    return next(entry for entry in tasks.load_tasks(config)[0] if entry.tid == "T-001")


def rest(config):
    tasks.set_fields(config, "T-001", TODAY, values={"resume": "2026-11-16 13:00"})


def test_owners_parse_list_and_all_rows_are_hints(enterprise, capsys):
    rows = owners.load(enterprise)
    assert rows[0] == owners.Owner("infrastructure", NOAH, "Noah", ROSHAN, "Roshan",
                                   "terraform, AWS, CI, deploys", 4, "stated 2026-11-10")
    assert rows[1].backup == rows[1].backup_name == ""
    assert rows[1].wait_hours == owners.WAIT_HOURS
    assert [row.area for row in rows] == ["infrastructure", "analytics", "product direction"]
    assert owners.problems(enterprise) == []
    assert not any("Owners.md" in problem for problem in validate.checks(Vault(enterprise.vault_path)))
    cli.main(["owners", "--vault", str(enterprise.vault_path), "--json"])
    assert json.loads(capsys.readouterr().out) == [asdict(row) for row in rows]
    cli.main(["owners", "--vault", str(enterprise.vault_path)])
    assert "Noah (slack:U01NOAH)" in capsys.readouterr().out


@pytest.mark.parametrize("old,new,message", [
    ("Noah (slack:U01NOAH)", "Noah (U01NOAH)", "Name"),
    ("Roshan (slack:U02ROSHAN)", "Roshan (slack:bad/id)", "Name"),
    ("| 4 |", "| four |", "integer"),
    ("| infrastructure |", "| ../outside |", "area must"),
    ("| analytics |", "| INFRASTRUCTURE |", "duplicate area"),
    ("| analytics |", "| analytics | extra |", "columns"),
])
def test_owners_validation(enterprise, old, new, message):
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE.replace(old, new))
    assert any(message in problem and "Owners.md:" in problem for problem in validate.checks(Vault(enterprise.vault_path)))
    bad = {problem.split(": ", 1)[0] for problem in owners.problems(enterprise)}
    assert bad and len(owners.load(enterprise)) == 3 - len(bad)


def test_a_bad_row_never_stalls_consults(enterprise, monkeypatch, capsys):
    entry = ask(enterprise)
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE.replace("Roshan (slack:U02ROSHAN) | terraform", "Roshan | terraform")
                    + "\n## Restricted\n- infrastructure\n")
    assert consults.candidates(enterprise, "deploys") == []
    clock(monkeypatch, "2026-11-12 15:00")
    assert consults.lapse(enterprise) == [f"{entry.id}: went ahead on the default and left a confirm"]
    with pytest.raises(ValueError, match="Owners.md names nobody for the area"):
        ask(enterprise, text="Rotate the production database credentials now?")
    cli.main(["owners", "--vault", str(enterprise.vault_path)])
    assert "skipped:" in capsys.readouterr().err


def test_ask_any_person_without_map_and_proposed_rows_are_hints(enterprise):
    assert ask(enterprise, to=KIM, area="unknown").to == KIM
    assert ask(enterprise, area="product direction").until == "2026-11-13 10:03"
    (enterprise.vault_path / "agent/Owners.md").unlink()
    assert owners.load(enterprise) == owners.problems(enterprise) == []
    entry = ask(enterprise, text="Use the new export pipeline for reporting?", area="")
    assert entry.to == NOAH and entry.area == "general"
    assert entry.until == "2026-11-12 14:03"


@pytest.mark.parametrize("field,value,message", [
    ("text", "Proceed?", "name the subject"),
    ("text", "", "too short"),
    ("text", "word " * 17, "ask must be at most 16"),
    ("default", " \n ", "give a default"),
    ("default", "word " * 41, "default must be at most 40"),
    ("why", "word " * 61, "why must be at most 60"),
    ("why", "", "give --why"),
    ("to", "Noah", "<platform>:<id>"),
    ("task", "T-999", "not an open task"),
])
def test_ask_refusals_are_read_only(enterprise, field, value, message):
    values = {"to": NOAH, "text": QUESTION, "default": DEFAULT, "why": WHY}
    values[field] = value
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match=message):
        consults.ask(enterprise, area="infrastructure", **values)
    assert snapshot(enterprise.vault_path) == before


def test_closed_task_refused(enterprise):
    tasks.close(enterprise, "T-001", TODAY, True, "Finished it.")
    with pytest.raises(ValueError, match="not an open task"):
        ask(enterprise, task="T-001")


def test_ask_format_dedupe_and_optional_task(enterprise):
    entry = ask(enterprise, task="T-001")
    data, body = fm.read(consults.path_for(enterprise))
    assert data == {"kind": "orientation", "tier": "warm", "pinned": False}
    assert f"- {TODAY} · infrastructure · {QUESTION} · [[agent/Tasks#T-001]]" in body
    assert f"  - to: {NOAH}\n  - asked: 2026-11-12 10:03\n  - until: 2026-11-12 14:03" in body
    assert f"  - tried: {NOAH}\n  - restricted: false" in body
    assert f"  - default: {DEFAULT}\n  - why: {WHY}" in body
    before = snapshot(enterprise.vault_path)
    duplicate = ask(enterprise, area=" INFRASTRUCTURE ", text="  TOUCH the staging\ncluster during this release week? ",
                    default="A different default.")
    assert duplicate.id == entry.id
    assert snapshot(enterprise.vault_path) == before
    optional = ask(enterprise, area="analytics", text="Move dashboards to the new warehouse?", default="Keep the current warehouse.")
    assert optional.source == "" and optional.why == WHY
    assert len(consults.load(enterprise)) == 2


@pytest.mark.parametrize("asked,hours,until", [
    ("2026-11-12 10:03", 4, "2026-11-12 14:03"),
    ("2026-11-12 16:03", 4, "2026-11-13 12:03"),
    ("2026-11-13 19:00", 4, "2026-11-16 13:00"),
    ("2026-11-13 16:00", 4, "2026-11-16 12:00"),
    ("2026-11-14 10:03", 4, "2026-11-16 13:00"),
    ("2026-11-16 07:00", 4, "2026-11-16 13:00"),
    ("2026-11-16 09:00", 8, "2026-11-16 17:00"),
    ("2026-11-16 17:00", 4, "2026-11-17 13:00"),
    ("2026-11-16 16:00", 17, "2026-11-18 17:00"),
    ("2026-10-30 19:00", 4, "2026-11-02 13:00"),
])
def test_working_hours_including_friday_evening(enterprise, asked, hours, until):
    moment = datetime.fromisoformat(asked).replace(tzinfo=enterprise.zone)
    assert consults.working_until(moment, hours).strftime("%Y-%m-%d %H:%M") == until


def test_ask_uses_local_zone_and_row_wait(enterprise, monkeypatch):
    monkeypatch.setattr(Config, "now", lambda self: datetime.fromisoformat("2026-11-14T03:00:00+00:00"))
    entry = ask(enterprise)
    assert entry.asked == "2026-11-13 19:00"
    assert entry.until == "2026-11-16 13:00"


def test_decision_filename_preserves_spaces_and_custom_wait(enterprise):
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE.replace("proposed", "stated"))
    entry = ask(enterprise, area="Product Direction", text="Build the reporting service before migrating dashboards?")
    assert entry.until == "2026-11-13 10:03"
    consults.answer(enterprise, entry.id, "confirmed", "Build it first.", NOAH)
    assert "Noah · Build it first." in (enterprise.vault_path / "agent/Areas/product direction.md").read_text()


def test_answer_decision_log_evidence_and_resume(enterprise, monkeypatch):
    rest(enterprise)
    entry = ask(enterprise, task="T-001")
    clock(monkeypatch, "2026-11-12 12:33")
    words = "No staging changes during a release week."
    consults.answer(enterprise, entry.id, "changed", words, NOAH)
    assert consults.load(enterprise) == []
    decision = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert f"- {TODAY} · Noah · {words} · [[agent/Tasks#T-001]] · id:{entry.id}\n  - asked: {QUESTION}" in decision
    log = (enterprise.vault_path / "agent/Answered/2026-11.md").read_text()
    assert f"- {TODAY} · consult changed · {QUESTION} · by {NOAH} · infrastructure · 2.5h · id:{entry.id}" in log
    assert fm.field(task(enterprise).body, "Latest evidence") == f"{TODAY} — Noah answered the consult: {words}"
    assert task(enterprise).resume is None
    with pytest.raises(ValueError, match="no open consult"):
        consults.answer(enterprise, entry.id, "changed", words, NOAH)


def test_answer_text_matching_exact_words_and_unknown_person(enterprise):
    entry = ask(enterprise)
    consults.answer(enterprise, "STAGING CLUSTER", "confirmed", "Yes,  leave it alone · please.", "email:guest@example.com")
    decision = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert "email:guest@example.com · Yes,  leave it alone · please." in decision
    assert f"id:{entry.id}" in (enterprise.vault_path / "agent/Answered/2026-11.md").read_text()


def test_answer_refuses_bad_person_empty_words_and_ambiguous_match(enterprise):
    ask(enterprise)
    ask(enterprise, to=ROSHAN, area="analytics")
    for which, outcome, words, by, match in [
        ("staging", "confirmed", "Yes.", "Noah", "<platform>:<id>"),
        ("staging", "confirmed", " ", NOAH, "own words"),
        ("staging", "inferred", "Yes.", NOAH, "changed, confirmed or redirected"),
        ("staging", "confirmed", "Yes.", NOAH, "2 open consults match"),
    ]:
        before = snapshot(enterprise.vault_path)
        with pytest.raises(ValueError, match=match):
            consults.answer(enterprise, which, outcome, words, by)
        assert snapshot(enterprise.vault_path) == before


def test_lapse_escalates_once_then_goes_ahead_and_wakes_task(enterprise, monkeypatch):
    rest(enterprise)
    entry = ask(enterprise, task="T-001")
    clock(monkeypatch, "2026-11-12 14:03")
    before = snapshot(enterprise.vault_path)
    assert consults.lapse(enterprise, dry_run=True) == [f"{entry.id}: reassigned to {ROSHAN}"]
    assert snapshot(enterprise.vault_path) == before
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {ROSHAN}"]
    backup, = consults.load(enterprise)
    assert backup.id == entry.id and backup.to == ROSHAN
    surfaced, = needs.items(enterprise, TODAY)
    assert surfaced.id == entry.id and surfaced.owner == ROSHAN
    assert backup.asked == "2026-11-12 14:03" and backup.until == "2026-11-13 10:03"
    assert task(enterprise).resume
    assert consults.lapse(enterprise) == []
    clock(monkeypatch, "2026-11-13 10:04")
    before = snapshot(enterprise.vault_path)
    assert "went ahead" in consults.lapse(enterprise, dry_run=True)[0]
    assert snapshot(enterprise.vault_path) == before
    assert "went ahead" in consults.lapse(enterprise)[0]
    went, = consults.load(enterprise)
    assert went.kind == "confirm" and went.to == NOAH and went.default == DEFAULT
    assert "No answer from Roshan; went ahead on the default: " + DEFAULT in task(enterprise).body
    assert not task(enterprise).resume
    assert f"consult unanswered · {QUESTION} · by {ROSHAN} · infrastructure · 20.0h · id:{entry.id}" in (
        enterprise.vault_path / "agent/Answered/2026-11.md").read_text()
    before = snapshot(enterprise.vault_path)
    assert consults.lapse(enterprise) == []
    assert snapshot(enterprise.vault_path) == before


def test_lapse_without_backup(enterprise, monkeypatch):
    entry = ask(enterprise, to=ROSHAN, area="analytics", text="Move dashboards to the new warehouse?")
    clock(monkeypatch, "2026-11-12 14:04")
    assert consults.lapse(enterprise) == [f"{entry.id}: went ahead on the default and left a confirm"]
    went, = consults.load(enterprise)
    assert went.to == ROSHAN
    assert not (enterprise.vault_path / "agent/Areas").exists()


def test_stop_asking_records_person_and_refuses_future_asks(enterprise):
    rest(enterprise)
    entry = ask(enterprise, task="T-001")
    needs.dismiss(enterprise, entry.id, TODAY, "stop-asking")
    note = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert f"## Not asked\n\n- {TODAY} · {NOAH} · stop-asking" in note
    assert not task(enterprise).resume
    assert task(enterprise).task_state != "set_aside"
    assert [entry.kind for entry in consults.load(enterprise)] == ["confirm"]
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match=f"stop-asking.*try {ROSHAN}"):
        ask(enterprise, text="Rebuild the staging cluster after release week?")
    assert snapshot(enterprise.vault_path) == before
    assert "## Decisions" not in note
    assert consults.stats(enterprise)["areas"]["infrastructure"]["asked"] == 1


@pytest.mark.parametrize("reason", needs.REASONS)
def test_dismiss_ask_and_confirm(enterprise, reason):
    entry = ask(enterprise, task="T-001")
    if reason == "done":
        with pytest.raises(ValueError, match="brain consult answer"):
            needs.dismiss(enterprise, entry.id, TODAY, reason)
        assert consults.load(enterprise)[0].kind == "ask"
        return
    needs.dismiss(enterprise, entry.id, TODAY, reason)
    went, = consults.load(enterprise)
    assert went.kind == "confirm" and task(enterprise).task_state != "set_aside"
    needs.dismiss(enterprise, went.id, TODAY, "done")
    assert consults.load(enterprise) == []


def test_resolve_directs_caller_to_answer(enterprise):
    entry = ask(enterprise)
    with pytest.raises(ValueError, match="answer it with brain consult answer"):
        needs.resolve(enterprise, entry.id, "I inferred it.", TODAY)
    assert consults.load(enterprise)[0].id == entry.id


def test_needs_surface_owner_default_area_and_state(enterprise, capsys):
    tasks.hand_off(enterprise, "T-001", TODAY, owner=ROSHAN)
    entry = ask(enterprise, task="T-001")
    item, = needs.items(enterprise, TODAY)
    assert (item.id, item.kind, item.origin, item.owner, item.context, item.detail, item.default, item.area) == (
        entry.id, "ask", "consult", NOAH, "infrastructure", WHY, DEFAULT, "infrastructure")
    assert item.task == "T-001" and task(enterprise).owner == ROSHAN
    cli.main(["needs-you", "--vault", str(enterprise.vault_path), "--json", "--for", NOAH])
    assert json.loads(capsys.readouterr().out) == [asdict(item)]
    exported = state.build(enterprise, TODAY)
    assert exported["needsYou"][0]["default"] == DEFAULT
    assert exported["needsYou"][0]["owner"] == NOAH
    needs.dismiss(enterprise, entry.id, TODAY)
    confirm, = needs.items(enterprise, TODAY)
    assert confirm.kind == "confirm" and confirm.owner == NOAH and confirm.default == DEFAULT


def test_taskless_consult_state_and_cli_list(enterprise, capsys):
    entry = ask(enterprise)
    assert state.build(enterprise, TODAY)["needsYou"][0]["source"] == "agent/Consults.md"
    cli.main(["consult", "list", "--vault", str(enterprise.vault_path), "--json", "--for", NOAH])
    listed, = json.loads(capsys.readouterr().out)
    assert listed["id"] == entry.id and listed["until"] == "2026-11-12 14:03"
    cli.main(["consult", "list", "--vault", str(enterprise.vault_path), "--json", "--for", ROSHAN])
    assert json.loads(capsys.readouterr().out) == []


def test_retire_stale_overflow_and_expiry(enterprise):
    entry = ask(enterprise)
    future = TODAY + timedelta(days=needs.RETIRE_DAYS + 1)
    before = snapshot(enterprise.vault_path)
    assert needs.stale(enterprise, future) == needs.overflow(enterprise, future, ceiling=0) == []
    assert needs.retire_asks(enterprise, future) == []
    assert snapshot(enterprise.vault_path) == before
    needs.dismiss(enterprise, entry.id, TODAY)
    still_open = ask(enterprise, to=ROSHAN, area="analytics", text="Move dashboards to the new warehouse?")
    assert consults.expire_confirms(enterprise, TODAY + timedelta(days=needs.CONFIRM_DAYS - 1)) == []
    assert consults.expire_confirms(enterprise, TODAY + timedelta(days=needs.CONFIRM_DAYS)) == [consults.WENT_AHEAD + DEFAULT]
    assert consults.expire_confirms(enterprise, future) == []
    assert [entry.id for entry in consults.load(enterprise)] == [still_open.id]


def test_nightly_expires_went_ahead(enterprise):
    entry = ask(enterprise)
    needs.dismiss(enterprise, entry.id, TODAY)
    report = nightly.run(enterprise.vault_path, TODAY + timedelta(days=needs.CONFIRM_DAYS))
    assert consults.load(enterprise) == []
    assert any("consult: confirm accepted after 5 days" in line for line in report)


@pytest.mark.parametrize("arguments", [
    ["ask", "--to", NOAH, "--why", WHY, "--area", "infrastructure", "--ask", QUESTION, "--default", DEFAULT],
    ["answer", "123", "--outcome", "confirmed", "--answer", "Yes.", "--by", NOAH],
    ["lapse"], ["list", "--json"], ["stats", "--json"],
    ["candidates", "--about", QUESTION],
    ["overrule", "123", "--answer", "Changed my mind.", "--by", KIM],
    ["delegate", "--area", "infrastructure", "--to", KIM, "--by", NOAH, "--words", "Ask Kim."],
])
def test_personal_refuses_each_consult_command(vault, arguments):
    before = snapshot(vault)
    with pytest.raises(SystemExit, match="consults need the enterprise edition"):
        cli.main(["consult", *arguments, "--vault", str(vault)])
    assert snapshot(vault) == before


def test_stats_numbers_and_cli(enterprise, monkeypatch, capsys):
    entry = ask(enterprise)
    clock(monkeypatch, "2026-11-12 11:03")
    consults.answer(enterprise, entry.id, "changed", "Keep staging unchanged.", NOAH)
    entry = ask(enterprise, text="Keep Terraform state in the infra account?")
    clock(monkeypatch, "2026-11-12 14:03")
    consults.answer(enterprise, entry.id, "confirmed", "Yes, keep it there.", NOAH)
    entry = ask(enterprise)
    needs.dismiss(enterprise, entry.id, TODAY)
    result = consults.stats(enterprise)
    assert result["areas"]["infrastructure"] == {
        "asked": 3, "changed": 1, "confirmed": 1, "redirected": 0, "overruled": 0, "unanswered": 1, "changed_share": 0.5, "median_hours": 2.0}
    assert result["people"][NOAH] == {
        "asked": 3, "changed": 1, "confirmed": 1, "redirected": 0, "overruled": 0, "unanswered": 1, "changed_share": 0.5, "median_hours": 2.0}
    cli.main(["consult", "stats", "--json", "--vault", str(enterprise.vault_path)])
    assert json.loads(capsys.readouterr().out) == result


def test_cli_ask_answer_lapse_and_group_vault(enterprise, capsys, monkeypatch):
    root = str(enterprise.vault_path)
    cli.main(["consult", "--vault", root, "ask", "--area", "infrastructure", "--ask", QUESTION,
              "--default", DEFAULT, "--to", NOAH, "--why", WHY, "--task", "T-001"])
    ident = capsys.readouterr().out.strip()
    assert ident == consults.load(enterprise)[0].id
    cli.main(["consult", "answer", ident, "--vault", root, "--outcome", "confirmed",
              "--answer", "Yes, leave it alone.", "--by", NOAH])
    assert f"{ident}: confirmed; decision recorded" in capsys.readouterr().out
    cli.main(["consult", "ask", "--vault", root, "--area", "analytics", "--ask", "Move dashboards to the warehouse?",
              "--default", DEFAULT, "--to", ROSHAN, "--why", WHY])
    ident = capsys.readouterr().out.strip()
    clock(monkeypatch, "2026-11-13 10:03")
    cli.main(["consult", "lapse", "--vault", root, "--dry-run"])
    assert f"{ident}: went ahead" in capsys.readouterr().out
    assert consults.load(enterprise)[0].kind == "ask"
    cli.main(["consult", "lapse", "--vault", root])
    assert "went ahead" in capsys.readouterr().out
    cli.main(["consult", "list", "--vault", root])
    assert "confirm · analytics · slack:U02ROSHAN" in capsys.readouterr().out


def test_archived_task_wakes_and_keeps_decision(enterprise):
    entry = tasks.Task("T-002", "Archived work", "- **Resume:** 2026-11-16 13:00\n", {"state": "in_progress"})
    tasks.write_archive(enterprise, entry, "cold", TODAY)
    question = ask(enterprise, task="T-002")
    consults.answer(enterprise, question.id, "confirmed", "Keep staging as it is.", NOAH)
    archived = next(entry for entry in tasks.all_tasks(enterprise) if entry.tid == "T-002")
    assert archived.resume is None
    assert "Noah answered the consult: Keep staging as it is." in archived.body


def test_stop_asking_is_for_the_person_not_future_owners(enterprise):
    entry = ask(enterprise)
    needs.dismiss(enterprise, entry.id, TODAY, "stop-asking")
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE.replace("Noah (slack:U01NOAH)", "Kim (slack:U03KIM)"))
    new = ask(enterprise, to=KIM)
    assert new.kind == "ask" and new.to == KIM


def test_configured_paths_and_refresh_stage_lapse_changes(enterprise, monkeypatch, tmp_path):
    root = enterprise.vault_path
    agent = root / "intellect/records"
    agent.parent.mkdir()
    (root / "agent").rename(agent)
    config_file = enterprise.repo / "brain.toml"
    config_file.write_text(config_file.read_text() + 'agent_dir = "intellect/records"\n')
    config = Config.load(root)
    clock(monkeypatch, "2026-11-11 10:03")
    rest(config)
    consults.ask(config, ROSHAN, QUESTION, DEFAULT, WHY, area="analytics", task="T-001")
    (agent / "SOUL.md").write_text("# Fixture identity\n")
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    monkeypatch.setenv("VAULT_REPO", str(config.repo))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "git-calls.jsonl"
    fake = commands / "git"
    fake.write_text(f"#!{sys.executable}\nimport json, sys\nwith open({str(log)!r}, 'a') as stream:\n"
                    "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    script = Path(__file__).resolve().parents[1] / "src/brain/scripts/refresh.sh"
    subprocess.run(["bash", str(script)], cwd=config.repo, capture_output=True, text=True, check=True)
    went, = consults.load(config)
    assert went.kind == "confirm" and went.to == ROSHAN
    assert task(config).resume is None
    assert (agent / "Answered/2026-11.md").exists()
    staged = [path for line in log.read_text().splitlines() if "add" in (args := json.loads(line)) for path in args[args.index("--") + 1:]]
    assert str(agent / "Consults.md") in staged
    assert str(agent / "Answered") in staged
    assert str(agent / "Tasks.md") in staged
    log.write_text("")
    subprocess.run(["bash", str(script)], cwd=config.repo, capture_output=True, text=True, check=True)
    staged = [path for line in log.read_text().splitlines() if "add" in (args := json.loads(line)) for path in args[args.index("--") + 1:]]
    assert not any(path.startswith(str(agent)) for path in staged)


def test_candidates_owners_matching_limit_names_and_read_only(enterprise, capsys, monkeypatch):
    monkeypatch.setattr(directory, "lookup", lambda config, person: {"kind": "person", "name": "Directory name"})
    before = snapshot(enterprise.repo)
    ranked = consults.candidates(enterprise, "Who reviews TERRAFORM deploys?")
    assert [entry.person for entry in ranked] == [NOAH, ROSHAN]
    assert ranked[0].name == "Noah" and ranked[0].score == routing.WEIGHTS["owner"]
    assert ranked[1].score == routing.WEIGHTS["backup"]
    assert ranked[0].evidence == [dict(area="infrastructure", kind="owner", date="",
                                     text="terraform, AWS, CI, deploys; stated 2026-11-10", link="agent/Owners.md")]
    assert consults.candidates(enterprise, "An unrelated topic", "infrastructure", 1) == ranked[:1]
    assert consults.candidates(enterprise, "who do I ask for CI?") == []
    assert consults.candidates(enterprise, "roadmap")[0].person == NOAH
    cli.main(["consult", "candidates", "--about", "deploys", "--json", "--vault", str(enterprise.vault_path)])
    assert json.loads(capsys.readouterr().out) == [asdict(entry) for entry in ranked]
    cli.main(["consult", "candidates", "--about", "deploys", "--limit", "1", "--vault", str(enterprise.vault_path)])
    assert "Noah · 12.00 · median - · infrastructure: owner" in capsys.readouterr().out
    assert snapshot(enterprise.repo) == before
    with pytest.raises(ValueError, match="positive"):
        consults.candidates(enterprise, QUESTION, limit=0)
    assert routing.name(enterprise, KIM) == "Directory name"
    monkeypatch.setattr(directory, "lookup", lambda config, person: None)
    assert routing.name(enterprise, KIM) == KIM


def test_candidates_decisions_overrules_and_unanswered(enterprise):
    first = ask(enterprise, to=KIM, task="T-001")
    consults.answer(enterprise, first.id, "changed", "Use the isolated staging cluster.", KIM)
    second = ask(enterprise, to=LEE, text="Move Terraform into the infrastructure account?")
    consults.answer(enterprise, second.id, "confirmed", "Keep the export bucket in the infrastructure account.", LEE)
    kim = next(entry for entry in consults.candidates(enterprise, "staging") if entry.person == KIM)
    assert kim.score == routing.WEIGHTS["answered"]
    assert kim.evidence == [dict(area="infrastructure", kind="answered", date=str(TODAY),
                                 text="Use the isolated staging cluster.", link="agent/Tasks#T-001")]
    rest(enterprise)
    consults.overrule(enterprise, first.id, "Keep staging unchanged · until QA signs off.", ROSHAN)
    assert not task(enterprise).resume
    assert "Roshan overruled the consult: Keep staging unchanged · until QA signs off." in task(enterprise).body
    consults.overrule(enterprise, first.id, "Leave it alone all week.", LEE)
    body = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert (f"id:{first.id}\n  - asked: {QUESTION}\n"
            f"  - overruled {TODAY} by Roshan: Keep staging unchanged · until QA signs off.\n"
            f"  - overruled {TODAY} by {LEE}: Leave it alone all week.") in body
    assert body.index("by Roshan:") < body.index(f"id:{second.id}")
    assert "## Overruled" not in body
    log = (enterprise.vault_path / "agent/Answered/2026-11.md").read_text()
    assert f"consult overruled · {QUESTION} · by {ROSHAN} · infrastructure · - · id:{first.id}" in log
    ranked = {entry.person: entry for entry in consults.candidates(enterprise, "export bucket")}
    assert ranked[KIM].score == 2 * routing.WEIGHTS["overruled"]
    assert [item["kind"] for item in ranked[KIM].evidence] == ["overruled", "overruled"]
    assert ranked[LEE].score == routing.WEIGHTS["answered"] + routing.WEIGHTS["overruling"]
    assert any(item["text"] == "Keep staging unchanged · until QA signs off." for item in ranked[ROSHAN].evidence)
    unanswered = ask(enterprise, to=KIM, text="Keep the staging database for another week?")
    with vault_lock(enterprise):
        needs.dismiss(enterprise, unanswered.id, TODAY)
    ranked = {entry.person: entry for entry in consults.candidates(enterprise, "", "infrastructure")}
    assert ranked[KIM].score == 2 * routing.WEIGHTS["overruled"] + routing.WEIGHTS["unanswered"]
    assert ranked[KIM].median_hours == 0


def test_redirect_moves_logs_evidence_and_wakes_task(enterprise, monkeypatch):
    rest(enterprise)
    entry = ask(enterprise, task="T-001")
    clock(monkeypatch, "2026-11-12 11:33")
    words = "Ask Kim · she reviews these deploys."
    moved = consults.answer(enterprise, entry.id, "redirected", words, NOAH, KIM)
    assert moved.id == entry.id and moved.to == KIM and moved.tried == (NOAH, KIM)
    assert moved.asked == "2026-11-12 11:33" and moved.until == "2026-11-12 15:33"
    assert consults.load(enterprise) == [moved]
    assert not (enterprise.vault_path / "agent/Areas").exists()
    assert not task(enterprise).resume and f"Noah redirected the consult to {KIM}: {words}" in task(enterprise).body
    item, = needs.items(enterprise, TODAY)
    assert (item.id, item.owner, item.area, item.default, item.detail) == (entry.id, KIM, "infrastructure", DEFAULT, WHY)
    response, = [row for row in routing.responses(enterprise) if row["outcome"] == "redirected"]
    assert response["outcome"] == "redirected" and response["answer"] == words and response["to"] == KIM
    assert response["hours"] == 1.5
    ranked = {entry.person: entry for entry in consults.candidates(enterprise, "", "infrastructure")}
    assert ranked[KIM].score == routing.WEIGHTS["redirected-to"]
    assert ranked[NOAH].score == routing.WEIGHTS["owner"] + routing.WEIGHTS["redirected-away"]
    assert ranked[NOAH].median_hours == 1.5
    assert ranked[KIM].evidence[0]["text"] == words
    assert consults.stats(enterprise)["weekly"] == []
    consults.answer(enterprise, entry.id, "confirmed", "Keep staging untouched.", KIM)
    assert (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text().count("## Decisions") == 1


@pytest.mark.parametrize("outcome,to,match", [
    ("redirected", "", "required for redirected"),
    ("confirmed", KIM, "only used"),
    ("redirected", NOAH, "already received"),
])
def test_bad_redirect_refusals_are_read_only(enterprise, outcome, to, match):
    entry = ask(enterprise)
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match=match):
        consults.answer(enterprise, entry.id, outcome, "Ask Kim.", NOAH, to)
    assert snapshot(enterprise.vault_path) == before


def test_candidates_median_breaks_ties_and_auto_area(enterprise, monkeypatch):
    entry = ask(enterprise, to=KIM, area="export pipeline", text="Move exports to the reporting warehouse?")
    clock(monkeypatch, "2026-11-12 14:03")
    consults.answer(enterprise, entry.id, "confirmed", "Keep the nightly export job in the current warehouse.", KIM)
    entry = ask(enterprise, to=LEE, area="export pipeline", text="Use the nightly export job for reports?")
    clock(monkeypatch, "2026-11-12 15:03")
    consults.answer(enterprise, entry.id, "changed", "Run the export job hourly.", LEE)
    ranked = consults.candidates(enterprise, "NIGHTLY export")
    assert [entry.person for entry in ranked] == [LEE, KIM]
    assert ranked[0].score == ranked[1].score and [entry.median_hours for entry in ranked[:2]] == [1, 4]
    inferred = ask(enterprise, to=KIM, area="", text="Keep the nightly export job in this warehouse?")
    assert inferred.area == "export pipeline"
    assert consults.candidates(enterprise, "the and to CI is") == []


def test_seeded_evidence_dedup_decay_kinds_and_links(enterprise):
    old = TODAY - timedelta(days=routing.HALF_LIFE_DAYS)
    assert consults.add_evidence(enterprise, "Export Pipeline", old, KIM, "deferred-to", "Per Kim · she signed off.", "https://example.com/pr/1")
    assert not consults.add_evidence(enterprise, "export pipeline", str(old), KIM, "deferred-to", "Per Kim · she signed off.", "https://example.com/pr/1")
    for kind in routing.SEED_KINDS:
        consults.add_evidence(enterprise, "export pipeline", TODAY, LEE, kind, "Reviewed the export job.")
    body = (enterprise.vault_path / "agent/Areas/export pipeline.md").read_text()
    assert body.count("Per Kim") == 1 and "## Decisions" not in body
    assert f"- {old} · {KIM} · deferred-to · Per Kim · she signed off. · [[https://example.com/pr/1]]" in body
    before = snapshot(enterprise.repo)
    ranked = consults.candidates(enterprise, "export")
    assert [entry.person for entry in ranked] == [LEE, KIM]
    assert ranked[0].score == sum(routing.WEIGHTS[kind] for kind in routing.SEED_KINDS)
    assert ranked[1].score == pytest.approx(routing.WEIGHTS["deferred-to"] * math.log2(1.5))
    assert ranked[1].evidence == [dict(area="export pipeline", kind="deferred-to", date=str(old),
                                     text="Per Kim · she signed off.", link="https://example.com/pr/1")]
    assert all(item["link"] == "agent/Areas/export pipeline.md" for item in ranked[0].evidence)
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("change,match", [
    ({"area": "../outside"}, "area must"),
    ({"day": "2026-99-01"}, "month"),
    ({"person": "Kim"}, "<platform>:<id>"),
    ({"kind": "guessed"}, "evidence kind"),
    ({"text": " "}, "own words"),
    ({"text": "A line\nAnother line"}, "single-line"),
    ({"link": "[[source]]"}, "brackets"),
])
def test_bad_seeded_evidence_is_read_only(enterprise, change, match):
    values = dict(area="export pipeline", day=TODAY, person=KIM, kind="approved", text="Approved the change.")
    values.update(change)
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match=match):
        consults.add_evidence(enterprise, **values)
    assert snapshot(enterprise.vault_path) == before


def test_muted_candidates_last_area_specific_and_lapse_ignores(enterprise, monkeypatch):
    entry = ask(enterprise)
    needs.dismiss(enterprise, entry.id, TODAY, "stop-asking")
    ranked = consults.candidates(enterprise, "", "infrastructure")
    assert [entry.person for entry in ranked] == [ROSHAN, NOAH]
    assert ranked[-1].muted and ranked[-1].score > ranked[0].score
    assert ranked[-1].evidence[0]["kind"] == "owner"
    assert any(item["kind"] == "stop-asking" for item in ranked[-1].evidence)
    assert not consults.candidates(enterprise, "", "product direction")[0].muted
    entry = ask(enterprise, to=KIM, text="Deploy a new staging cluster next week?")
    clock(monkeypatch, "2026-11-12 14:04")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {ROSHAN}"]


@pytest.mark.parametrize("area", ["../bad", "export/pipeline", "export.pipeline", "export*", "_export"])
def test_unsafe_area_names_refused(enterprise, area):
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="area must"):
        ask(enterprise, area=area)
    assert snapshot(enterprise.vault_path) == before


@pytest.mark.parametrize("by", [NOAH, ROSHAN])
def test_delegate_owner_or_backup_in_their_exact_words(enterprise, by):
    words = "Ask Kim about deploys · she can decide while I'm away."
    consults.delegate(enterprise, "Infrastructure", KIM, by, words)
    label = "Noah" if by == NOAH else "Roshan"
    body = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert f"## Delegated\n\n- {TODAY} · {label} · to {KIM} · {words}" in body
    assert "## Decisions" not in body
    assert routing.allowed(enterprise, "infrastructure") == {NOAH, ROSHAN, KIM}
    ranked = consults.candidates(enterprise, "", "infrastructure")
    assert ranked[1].person == KIM and ranked[1].score == routing.WEIGHTS["delegated"]
    assert ranked[1].evidence[0]["text"] == words
    assert ask(enterprise, to=KIM, restricted=True).restricted


@pytest.mark.parametrize("area,by", [("infrastructure", KIM), ("unknown", NOAH)])
def test_delegate_refuses_non_owner(enterprise, area, by):
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="owner or backup"):
        consults.delegate(enterprise, area, LEE, by, "Lee can decide.")
    assert snapshot(enterprise.vault_path) == before


@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("to", [NOAH, ROSHAN, KIM])
def test_restricted_flag_or_listed_area_gate(enterprise, listed, to):
    if listed:
        path = enterprise.vault_path / "agent/Owners.md"
        path.write_text(TABLE + "\n## Restricted\n- INFRASTRUCTURE\n\n## Other\n- analytics\n")
        assert owners.restricted(enterprise) == {"infrastructure"}
    before = snapshot(enterprise.vault_path)
    if to == KIM:
        with pytest.raises(ValueError, match=f"may answer: {NOAH}, {ROSHAN}"):
            ask(enterprise, to=to, restricted=not listed)
        assert snapshot(enterprise.vault_path) == before
    else:
        entry = ask(enterprise, to=to, restricted=not listed)
        assert entry.restricted and consults.load(enterprise)[0].restricted
        before = snapshot(enterprise.vault_path)
        with pytest.raises(ValueError, match="restricted consult"):
            consults.answer(enterprise, entry.id, "confirmed", "Go ahead.", KIM)
        assert snapshot(enterprise.vault_path) == before


def test_restricted_without_owner_map_refused(enterprise):
    (enterprise.vault_path / "agent/Owners.md").unlink()
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="Owners.md names nobody for the area"):
        ask(enterprise, restricted=True)
    assert snapshot(enterprise.vault_path) == before


def test_restricted_redirect_delegates_for_that_consult_only(enterprise, monkeypatch):
    entry = ask(enterprise, restricted=True)
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="restricted consult"):
        consults.answer(enterprise, entry.id, "redirected", "Ask Kim.", LEE, KIM)
    assert snapshot(enterprise.vault_path) == before
    words = "Ask Kim, she can decide about this staging change."
    consults.answer(enterprise, entry.id, "redirected", words, NOAH, KIM)
    moved, = consults.load(enterprise)
    assert moved.restricted and moved.id == entry.id and moved.to == KIM
    body = (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()
    assert f"- {TODAY} · Noah · to {KIM} · {words}\n  - consult: {entry.id} · for: {QUESTION}" in body
    assert KIM in routing.allowed(enterprise, "infrastructure", entry.id)
    assert KIM not in routing.allowed(enterprise, "infrastructure")
    before = snapshot(enterprise.vault_path)
    assert ask(enterprise, to=KIM, restricted=True).id == entry.id
    assert snapshot(enterprise.vault_path) == before
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="restricted consult"):
        ask(enterprise, to=KIM, restricted=True, text="Deploy a different staging service this week?")
    assert snapshot(enterprise.vault_path) == before
    consults.answer(enterprise, entry.id, "confirmed", "Keep staging alone.", KIM)
    # The same question asked again is a new consult; its earlier delegation does not carry over.
    another = ask(enterprise, restricted=True)
    assert another.id != entry.id
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="restricted consult"):
        consults.answer(enterprise, another.id, "confirmed", "Deploy the change.", KIM)
    assert snapshot(enterprise.vault_path) == before
    clock(monkeypatch, "2026-11-12 14:04")
    assert consults.lapse(enterprise) == [f"{another.id}: reassigned to {ROSHAN}"]
    consults.answer(enterprise, another.id, "confirmed", "Leave staging unchanged.", ROSHAN)
    consults.overrule(enterprise, another.id, "Freeze all staging deploys this month.", LEE)


def test_restricted_redirect_to_backup_needs_no_extra_delegation(enterprise):
    entry = ask(enterprise, restricted=True)
    consults.answer(enterprise, entry.id, "redirected", "Ask Roshan.", NOAH, ROSHAN)
    assert not (enterprise.vault_path / "agent/Areas").exists()
    assert consults.load(enterprise)[0].restricted


def test_deduping_a_restricted_ask_keeps_the_gate(enterprise):
    entry = ask(enterprise, to=KIM)
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="restricted consult"):
        ask(enterprise, restricted=True)
    assert snapshot(enterprise.vault_path) == before
    consults.answer(enterprise, entry.id, "confirmed", "Leave staging unchanged.", KIM)
    entry = ask(enterprise)
    duplicate = ask(enterprise, restricted=True)
    assert duplicate.id == entry.id and duplicate.restricted
    assert consults.load(enterprise)[0].restricted
    with pytest.raises(ValueError, match="restricted consult"):
        consults.answer(enterprise, entry.id, "confirmed", "Deploy it.", KIM)


def test_lapse_ranks_next_candidate_and_stops_at_max_tries(enterprise, monkeypatch):
    for number in range(4):
        consults.add_evidence(enterprise, "infrastructure", TODAY, KIM, "deferred-to", f"Kim signed off change {number}.")
    for number in range(4):
        consults.add_evidence(enterprise, "infrastructure", TODAY, LEE, "approved", f"Lee approved change {number}.")
    entry = ask(enterprise)
    clock(monkeypatch, "2026-11-12 14:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {KIM}"]
    clock(monkeypatch, "2026-11-13 10:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {LEE}"]
    moved, = consults.load(enterprise)
    assert moved.tried == (NOAH, KIM, LEE) and len(moved.tried) == consults.MAX_TRIES
    before = snapshot(enterprise.vault_path)
    with pytest.raises(ValueError, match="already tried 3 people"):
        consults.answer(enterprise, entry.id, "redirected", "Ask Roshan.", LEE, ROSHAN)
    assert snapshot(enterprise.vault_path) == before
    clock(monkeypatch, "2026-11-13 14:03")
    assert "went ahead" in consults.lapse(enterprise)[0]
    went, = consults.load(enterprise)
    assert went.to == NOAH and went.id == entry.id
    assert routing.responses(enterprise)[-1]["person"] == LEE
    assert consults.lapse(enterprise) == []


def test_restricted_lapse_uses_only_eligible_candidates(enterprise, monkeypatch):
    for number in range(5):
        consults.add_evidence(enterprise, "infrastructure", TODAY, KIM, "deferred-to", f"Per Kim {number}.")
    consults.delegate(enterprise, "infrastructure", LEE, NOAH, "Lee can decide on deploys.")
    entry = ask(enterprise, restricted=True)
    clock(monkeypatch, "2026-11-12 14:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {LEE}"]
    assert consults.load(enterprise)[0].restricted
    clock(monkeypatch, "2026-11-13 10:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: reassigned to {ROSHAN}"]
    clock(monkeypatch, "2026-11-13 14:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: restricted, held for {NOAH} without taking the default"]
    held, = consults.load(enterprise)
    assert held.kind == "ask" and held.to == NOAH and held.until == "" and held.restricted
    clock(monkeypatch, "2026-11-20 14:03")
    assert consults.lapse(enterprise) == []


def test_restricted_silence_or_dismissal_never_takes_the_default(enterprise, monkeypatch):
    entry = ask(enterprise, to=ROSHAN, area="analytics", task="T-001", restricted=True)
    clock(monkeypatch, "2026-11-13 14:03")
    assert consults.lapse(enterprise) == [f"{entry.id}: restricted, held for {ROSHAN} without taking the default"]
    assert "restricted, so the default is not taken" in task(enterprise).body
    assert not any(consult.kind == "confirm" for consult in consults.load(enterprise))
    consults.answer(enterprise, entry.id, "confirmed", "Fine, leave staging alone.", ROSHAN)
    assert "Fine, leave staging alone." in (enterprise.vault_path / "agent/Areas/analytics.md").read_text()
    other = ask(enterprise, to=ROSHAN, area="analytics", text="Run the production backfill tonight?", restricted=True)
    with vault_lock(enterprise):
        needs.dismiss(enterprise, other.id, TODAY, "no")
    assert consults.load(enterprise) == []
    assert "Went ahead" not in (enterprise.vault_path / "agent/Consults.md").read_text().split("## Went ahead", 1)[1]


def test_go_ahead_confirm_targets_owner_or_first_recipient(enterprise, monkeypatch):
    owned = ask(enterprise, to=KIM, area="analytics", text="Move dashboards to a new warehouse?")
    unowned = ask(enterprise, to=LEE, area="vendor contracts", text="Renew the annual vendor contract?")
    consults.answer(enterprise, unowned.id, "redirected", "Ask Kim about this contract.", LEE, KIM)
    clock(monkeypatch, "2026-11-12 14:03")
    consults.lapse(enterprise)
    owned = next(entry for entry in consults.load(enterprise) if entry.id == owned.id)
    assert owned.to == ROSHAN and owned.kind == "ask"
    unowned = next(entry for entry in consults.load(enterprise) if entry.id == unowned.id)
    assert unowned.to == LEE and unowned.kind == "confirm"
    clock(monkeypatch, "2026-11-13 10:03")
    consults.lapse(enterprise)
    owned = next(entry for entry in consults.load(enterprise) if entry.id == owned.id)
    assert owned.to == ROSHAN and owned.kind == "confirm"


def test_repeat_questions_have_distinct_ids_and_confirms_can_be_removed_separately(enterprise):
    first = ask(enterprise)
    needs.dismiss(enterprise, first.id, TODAY)
    second = ask(enterprise)
    needs.dismiss(enterprise, second.id, TODAY)
    assert first.id != second.id
    assert [entry.id for entry in consults.load(enterprise)] == [first.id, second.id]
    needs.dismiss(enterprise, second.id, TODAY, "done")
    assert [entry.id for entry in consults.load(enterprise)] == [first.id]
    third = ask(enterprise)
    consults.answer(enterprise, third.id, "changed", "Leave staging unchanged.", NOAH)
    fourth = ask(enterprise)
    consults.answer(enterprise, fourth.id, "changed", "Leave staging unchanged.", NOAH)
    consults.overrule(enterprise, third.id, "Freeze deploys entirely.", KIM)
    decisions = routing.decisions(fm.read(enterprise.vault_path / "agent/Areas/infrastructure.md")[1], "infrastructure")
    assert len(decisions[0].overrules) == 1 and not decisions[1].overrules


def test_stats_weekly_redirect_rate_uses_final_resolution_week(enterprise, monkeypatch, capsys):
    clock(monkeypatch, "2026-11-06 10:00")
    redirected = ask(enterprise)
    clock(monkeypatch, "2026-11-06 11:00")
    consults.answer(enterprise, redirected.id, "redirected", "Ask Kim.", NOAH, KIM)
    clock(monkeypatch, "2026-11-09 10:00")
    consults.answer(enterprise, redirected.id, "redirected", "Ask Lee.", KIM, LEE)
    clock(monkeypatch, "2026-11-09 12:00")
    consults.answer(enterprise, redirected.id, "changed", "Freeze deploys.", LEE)
    direct = ask(enterprise)
    clock(monkeypatch, "2026-11-09 13:00")
    consults.answer(enterprise, direct.id, "confirmed", "Leave staging unchanged.", NOAH)
    clock(monkeypatch, "2026-11-16 10:00")
    unanswered = ask(enterprise)
    needs.dismiss(enterprise, unanswered.id, date(2026, 11, 16))
    consults.overrule(enterprise, direct.id, "Freeze all deployments this month.", KIM)
    pending = ask(enterprise)
    consults.answer(enterprise, pending.id, "redirected", "Ask Kim again.", NOAH, KIM)
    result = consults.stats(enterprise)
    assert result["weekly"] == [dict(week="2026-W46", resolved=2, redirected=1, rate=0.5),
                                dict(week="2026-W47", resolved=1, redirected=0, rate=0.0)]
    assert result["areas"]["infrastructure"] == dict(asked=6, changed=1, confirmed=1, redirected=3, overruled=1,
                                                     unanswered=1, changed_share=0.5, median_hours=1.0)
    assert result["people"][KIM]["overruled"] == 1
    before = snapshot(enterprise.repo)
    cli.main(["consult", "stats", "--vault", str(enterprise.vault_path)])
    output = capsys.readouterr().out
    assert "2026-W46 · resolved 2 · redirected 1 · 50%" in output
    assert output.index("2026-W46") < output.index("2026-W47")
    assert snapshot(enterprise.repo) == before


def test_consult_writes_hold_the_vault_lock(enterprise, monkeypatch):
    held = False
    original_write = fm.write

    @contextmanager
    def checked_lock(config):
        nonlocal held
        with vault_lock(config):
            held = True
            try:
                yield
            finally:
                held = False

    def write(*args, **kwargs):
        assert held
        return original_write(*args, **kwargs)

    monkeypatch.setattr(consults, "vault_lock", checked_lock)
    monkeypatch.setattr(fm, "write", write)
    consults.add_evidence(enterprise, "infrastructure", TODAY, KIM, "approved", "Approved deploy changes.")
    consults.delegate(enterprise, "infrastructure", KIM, NOAH, "Ask Kim about deploys.")
    entry = ask(enterprise)
    consults.answer(enterprise, entry.id, "redirected", "Ask Kim.", NOAH, KIM)
    consults.answer(enterprise, entry.id, "changed", "Keep staging as it is.", KIM)
    consults.overrule(enterprise, entry.id, "Freeze deploys entirely.", LEE)
    ask(enterprise, area="unowned")
    clock(monkeypatch, "2026-11-12 14:04")
    consults.lapse(enterprise)


def test_personal_add_evidence_refused(vault):
    before = snapshot(vault)
    with pytest.raises(ValueError, match="enterprise edition"):
        consults.add_evidence(Config.load(vault), "infrastructure", TODAY, KIM, "approved", "Approved change.")
    assert snapshot(vault) == before


def test_cli_delegation_restricted_redirect_answer_and_overrule(enterprise, capsys):
    root = str(enterprise.vault_path)
    cli.main(["consult", "delegate", "--vault", root, "--area", "infrastructure", "--to", KIM,
              "--by", NOAH, "--words", "Kim can decide about deploys."])
    assert "delegation recorded" in capsys.readouterr().out
    cli.main(["consult", "ask", "--vault", root, "--to", KIM, "--area", "infrastructure", "--restricted",
              "--why", "Noah delegated deploy questions to you.", "--ask", QUESTION, "--default", DEFAULT])
    ident = capsys.readouterr().out.strip()
    cli.main(["consult", "answer", ident, "--vault", root, "--by", KIM, "--outcome", "redirected",
              "--to", LEE, "--answer", "Ask Lee about this staging deploy."])
    assert f"redirected; moved to {LEE}" in capsys.readouterr().out
    cli.main(["consult", "answer", ident, "--vault", root, "--by", LEE, "--outcome", "confirmed",
              "--answer", "Leave staging as it is."])
    assert "confirmed; decision recorded" in capsys.readouterr().out
    cli.main(["consult", "overrule", ident, "--vault", root, "--by", ROSHAN,
              "--answer", "Freeze all staging deploys."])
    assert f"{ident}: overrule recorded" in capsys.readouterr().out
    assert consults.load(enterprise) == []
    assert consults.stats(enterprise)["weekly"] == [dict(week="2026-W46", resolved=1, redirected=1, rate=1.0)]


def test_evidence_counts_by_how_well_its_area_matches(enterprise):
    (enterprise.vault_path / "agent/Owners.md").write_text(
        TABLE.replace("terraform, AWS, CI, deploys", "terraform, AWS, CI, deploys, staging")
        + "| export pipeline | Kim (slack:U03KIM) | | export job retries, failures | | stated 2026-11-10 |\n")
    about = "Should the export job retry on staging failures?"
    weights = routing.relevance(enterprise, about)
    assert next(iter(weights)) == "export pipeline" and weights["export pipeline"] == 1.0
    assert 0 < weights["infrastructure"] < 1
    ranked = {entry.person: entry.score for entry in consults.candidates(enterprise, about)}
    assert ranked[KIM] == routing.WEIGHTS["owner"]
    assert ranked[NOAH] == pytest.approx(routing.WEIGHTS["owner"] * weights["infrastructure"])
    assert ranked[KIM] > ranked[NOAH]
    named = {entry.person: entry.score for entry in consults.candidates(enterprise, about, "infrastructure")}
    assert named[NOAH] == routing.WEIGHTS["owner"]


@pytest.mark.parametrize("by,acts", [(LEE, False), (ROSHAN, True)])
def test_overrule_in_restricted_area_needs_a_permitted_word_to_act(enterprise, by, acts):
    path = enterprise.vault_path / "agent/Owners.md"
    path.write_text(TABLE + "\n## Restricted\n- infrastructure\n")
    entry = ask(enterprise, task="T-001")
    consults.answer(enterprise, entry.id, "confirmed", "Go ahead with staging.", NOAH)
    consults.overrule(enterprise, entry.id, "Leave staging alone.", by)
    body = task(enterprise).body
    assert "overruled the consult: Leave staging alone." in body
    assert ("act on it only once one of" in body) is not acts
    assert "overruled" in (enterprise.vault_path / "agent/Areas/infrastructure.md").read_text()


def test_seeded_volume_counts_with_diminishing_returns(enterprise):
    rows = [routing.Row("infrastructure", TODAY.isoformat(), KIM, "approved", f"#{number} Terraform deploys") for number in range(40)]
    assert consults.record_evidence(enterprise, rows) == 40
    ranked = {entry.person: entry.score for entry in consults.candidates(enterprise, "terraform deploys")}
    assert ranked[KIM] == pytest.approx(routing.WEIGHTS["approved"] * math.log2(41))
    assert routing.WEIGHTS["owner"] < ranked[KIM] < 2 * routing.WEIGHTS["owner"]
    assert ranked[NOAH] == routing.WEIGHTS["owner"]
