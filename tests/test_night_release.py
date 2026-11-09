"""The 0.119 night chain, selective reset and single judging passes."""
import json
import os
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from importlib.resources import files

import pytest

from brain import cli, fm, goals, install, ledger, needs, nightly, schedule, seed, tasks, validate
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_default_chains_have_installed_skills_and_valid_predecessors(vault, edition):
    config = replace(Config.load(vault), edition=edition, skill_prefix="own-")
    routines = schedule.defaults(config)
    chain = ["session-export", "attribution", "night-before", "sleep1"]
    if edition == "personal":
        chain.append("people")
    chain += ["image", "night-after"]
    assert routines[chain[0]].cron == "30 0 * * *"
    for predecessor, name in zip(chain, chain[1:]):
        assert routines[name].after == predecessor
        assert routines[name].cron is None
    assert routines["sleep1"].skills == ["own-sleep1"]
    assert routines["image"].skills == ["own-image", "own-portrait"]
    assert routines["image"].prompt == "Run the image pass."
    for name in ("session-export", "attribution", "night-before", "night-after", "upgrade"):
        assert not routines[name].agent and routines[name].script == name.replace("session-export", "export-sessions") + ".sh"
    assert routines["improve"].cron == "0 7 * * *"
    assert routines["upgrade"].cron == "0 9 * * *"
    assert routines["explore"].cron == "30 8-18/2 * * *"
    assert not {"sleep2", "rem", "continue", "continue-daily"} & routines.keys()
    if edition == "personal":
        assert routines["people"].skills == ["own-people"]
        assert routines["prime"].after == "improve"
        assert routines["morning"].cron == "0 8 * * *"
    else:
        assert not {"people", "prime", "morning"} & routines.keys()
    schedule.save(config, routines)
    assert schedule.problems(config) == []
    assert schedule.load(config) == routines
    install.skills(config)
    for routine in routines.values():
        for skill in routine.skills:
            assert (config.skills_path / skill / "SKILL.md").is_file()


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
def test_reset_night_keeps_own_routines_and_three_clocks_and_is_idempotent(vault, edition, capsys):
    (vault.parent / "brain.toml").write_text(f'[brain]\nedition="{edition}"\nskill_prefix="own-"\n')
    config = Config.load(vault)
    own = {
        "study-decks": schedule.Routine(cron="40 3 * * *", skills=["own-decks"], owner=True, enabled=False),
        "fantasy-football": schedule.Routine(every="2h", prompt="Check the lineup", deliver="demiurge:football"),
        "watch": schedule.Routine(cron="0 9-21 * * *", skills=["own-watch"], monitor="custom.sh"),
        "explore": schedule.Routine(cron="0 10,15 * * *", prompt="Custom work"),
        "context-refresh": schedule.Routine(cron="25 * * * *", script="refresh.sh", agent=False),
    }
    clocks = {"session-export": "30 3 * * *", "improve": "20 7 * * *"}
    if edition == "personal":
        clocks["morning"] = "0 11 * * *"
    old = own | {name: schedule.Routine(cron=cron, prompt="Old prompt", enabled=False) for name, cron in clocks.items()}
    retired = ("sleep2", "rem", "brain-attribute", "continue", "continue-daily")
    old |= {name: schedule.Routine(every="1h") for name in retired}
    old["sleep1"] = schedule.Routine(cron="30 5 * * *", skills=["old-sleep1"])
    schedule.save(config, old)
    before_notes = {path: path.read_bytes() for path in vault.rglob("*.md")}
    cli.main(["schedule", "init", "--reset-night", "--vault", str(vault)])
    output = capsys.readouterr().out
    assert "reset sleep1" in output and "added image" in output and "removed sleep2" in output
    actual = schedule.load(config)
    assert {name: actual[name] for name in own} == own
    assert not set(retired) & actual.keys()
    defaults = schedule.defaults(config)
    for name in set(defaults) - own.keys() - {"evening"}:
        expected = replace(defaults[name], cron=clocks[name]) if name in clocks else defaults[name]
        assert actual[name] == expected
    assert all(path.read_bytes() == content for path, content in before_notes.items())
    assert schedule.problems(config) == []
    before = snapshot(vault)
    cli.main(["schedule", "init", "--reset-night", "--vault", str(vault)])
    assert snapshot(vault) == before
    assert capsys.readouterr().out == "night routines already match defaults\n"


def test_stale_due_reason_and_grounded_evidence_clear_without_ending(vault, monkeypatch, capsys):
    today = date(2026, 11, 8)
    monkeypatch.setenv("BRAIN_TODAY", str(today))
    config = Config.load(vault)
    fm.write(vault / "Training.md", {"kind": "hub", "tier": "warm"}, "# Training\n")
    old = today - timedelta(days=30)
    goal = goals.new(config, "Marathon", "Run a marathon", inferred=True, said=str(old),
                     evidence=[f"{old} · Training began · [[Training]]"])
    assert goals.decay(config, today) == [goal.gid]
    flagged = goals.load(config)[0]
    assert flagged.state["state"] == "active" and flagged.state["stale"] == str(today)
    assert "Set-Aside" not in flagged.body
    before = snapshot(vault)
    cli.main(["goals", "due", "--json", "--vault", str(vault)])
    row, = json.loads(capsys.readouterr().out)
    assert row["id"] == goal.gid and row["stale"] == str(today)
    assert "No grounded evidence for 30 days" in row["reasons"][0]
    cli.main(["goals", "due", "--vault", str(vault)])
    assert "flagged stale" in capsys.readouterr().out
    assert snapshot(vault) == before
    assert goals.decay(config, today + timedelta(days=1)) == []
    assert goals.load(config)[0].state["stale"] == str(today)
    goals.change(config, goal.gid, "evidence", text=f"{old} · Old evidence repeated · [[Training]]")
    assert goals.load(config)[0].state["stale"] == str(today)
    goals.change(config, goal.gid, "evidence", text=f"{today} · Still training · [[Training]]")
    assert "stale" not in goals.load(config)[0].state
    assert goals.due(config) == [] and goals.decay(config, today) == []
    assert not goals.problems(config)


def test_decay_clears_stale_mark_when_existing_evidence_becomes_grounded(vault, monkeypatch):
    today = date(2026, 11, 8)
    monkeypatch.setenv("BRAIN_TODAY", str(today))
    config = Config.load(vault)
    fm.write(vault / "Training.md", {"kind": "hub"}, "# Training\n")
    goal = goals.new(config, "Running", "Run", inferred=True, said="2026-09-01",
                     evidence=["2026-09-01 · Began · [[Training]]"])
    assert goals.decay(config, today) == [goal.gid]
    goal = goals.load(config)[0]
    goal.body += f"- **Evidence:** {today} · Ran again · [[Restored]]\n"
    goals._write(config, *goals.read(config), goal)
    assert goals.decay(config, today) == []
    fm.write(vault / "Restored.md", {"kind": "hub"}, "# Restored\n")
    assert goals.decay(config, today) == [goal.gid]
    assert "stale" not in goals.load(config)[0].state
    assert goals.load(config)[0].state["state"] == "active"


@pytest.mark.parametrize("stage", ["before", "all"])
def test_night_leaves_unanswered_asks_for_evening_but_accepts_confirms(vault, monkeypatch, stage):
    today = date(2026, 11, 8)
    monkeypatch.setenv("BRAIN_TODAY", str(today))
    config = Config.load(vault)
    data, body = fm.read(vault / "CURRENT.md")
    body += "\n## Needs you\n- 2026-10-01 · ask · Book the table? · [[CURRENT]]\n"
    body += "- 2026-11-03 · confirm · Kept the plan · [[CURRENT]]\n"
    fm.write(vault / "CURRENT.md", data, body)
    report = nightly.run(vault, today, stage=stage)
    assert [item.text for item in needs.items(config, today)] == ["Book the table?"]
    assert [item.text for item in needs.stale(config, today)] == ["Book the table?"]
    assert any("confirm accepted after 5 days" in line for line in report)
    assert not any("ask unanswered" in line for line in report)


@pytest.mark.parametrize("edition", ["personal", "enterprise"])
@pytest.mark.parametrize("prefix", ["", "own-"])
def test_install_removes_retired_skills_and_keeps_own_ones(vault, edition, prefix):
    config = replace(Config.load(vault), edition=edition, skill_prefix=prefix)
    for name in ("sleep2", "rem", "attribute", "my-routine"):
        folder = config.skills_path / (prefix + name)
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("Own content\n## Local\nKeep this\n")
    install.skills(config)
    for name in ("sleep2", "rem", "attribute"):
        assert not (config.skills_path / (prefix + name)).exists()
    assert (config.skills_path / (prefix + "image") / "SKILL.md").is_file()
    assert "Keep this" in (config.skills_path / (prefix + "my-routine") / "SKILL.md").read_text()
    assert "sleep2" not in ledger.NIGHT_STAGES
    assert not any("sleep2" in issue for issue in validate.checks(Vault(vault)))


@pytest.mark.parametrize("stage", ["before", "after"])
def test_night_scripts_pass_yesterday_and_stage_and_do_not_upgrade(vault, tmp_path, monkeypatch, stage):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("VAULT_REPO", str(vault.parent))
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-01")
    scripts = {path.name: path for path in install.scripts()}
    command = tmp_path / "bin/brain"
    command.parent.mkdir()
    command.write_text('#!/bin/sh\nif [ "$1" = timezone ]; then echo America/New_York; else printf "%s\\n" "$*" >> "$AGENT_HOME/calls"; fi\n')
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(command.parent) + os.pathsep + os.environ["PATH"])
    subprocess.run(["bash", str(scripts[f"night-{stage}.sh"])], check=True)
    assert (home / "calls").read_text().splitlines() == [
        "pull", f"nightly --date 2026-10-31 --write --commit --push --stage {stage}"]
    for name in ("export-sessions.sh", "refresh.sh"):
        script = scripts[name].read_text()
        assert all(f"brain {cmd}" not in script for cmd in ("update", "migrate-check", "install-skills"))
    upgrade = scripts["upgrade.sh"].read_text()
    assert upgrade.index("brain update") < upgrade.index("brain migrate-check") < upgrade.index("brain install-skills")


def test_open_seed_tasks_are_identified_including_archived_tasks(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-08")
    config = Config.load(vault)
    task = tasks.new_task(config, "Learn the account", "seed:owner@example.com")
    rows = seed.seeds(Vault(vault))
    assert rows == [{"account": "owner@example.com", "read": None, "task": task.tid}]
    tasks.write_archive(config, task, "cold", config.today())
    fm.write(vault / config.agent("Tasks.md"), {}, "# Tasks\n## Active work\n")
    cli.main(["state", "--json", "--vault", str(vault)])
    assert json.loads(capsys.readouterr().out)["seeds"] == rows
    assert seed.seeds(Vault(vault)) == rows
    tasks.close(config, task.tid, config.today(), True, "Seed recorded")
    assert seed.seeds(Vault(vault)) == []


def test_what_the_owner_said_in_conversation_reaches_the_image_as_due_evidence(vault, tmp_path, monkeypatch, capsys):
    import io
    from brain import image
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    row = {"date": "2026-11-07", "verb": "said", "fact": "Wants to read more history", "whose": "own",
           "hints": ["reading"], "pointer": "conversation:2026-11-07"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(row) + "\n"))
    cli.main(["evidence", "import", "--source", "conversation", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "conversation: 1 rows, 1 new"
    due = {entry["facet"]: entry for entry in image.due(Config.load(vault))}
    assert any(entry["sources"] == ["conversation"] and entry["newest"] == "2026-11-07" for entry in due.values())
