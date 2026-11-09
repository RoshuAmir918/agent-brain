"""Ideas stay available without competing with decisions or tiring the owner."""
from dataclasses import asdict
from datetime import date, timedelta
import json

import pytest

from brain import cli, compile_context, fm, health, ideas, needs, state, validate
from brain.config import Config
from brain.tasks import Task, due, load_tasks, write_archive
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 3)


def write_ideas(root, bullets):
    path = root / Config.load(root).agent("Ideas.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ideas.TEMPLATE + bullets, encoding="utf-8")
    return path


def bullet(text, day=TODAY, source="Topic", why="A useful result.", needs=""):
    return (f"- {day} · {text} · [[{source}]]\n" + (f"  - why: {why}\n" if why else "")
            + (f"  - needs: {needs}\n" if needs else ""))


def owner(vault, monkeypatch, age=100, hubs=3):
    roots = None if age is None else f"{TODAY.isoformat()}\n{(TODAY - timedelta(days=age)).isoformat()}"   # the oldest root counts
    monkeypatch.setattr(health, "git", lambda repo, *args: roots if args == ("log", "--max-parents=0", "--format=%cs") else None)
    for number in range(hubs):
        fm.write(vault / f"Hub {number}.md", {"skill": f"own-{number}", "records": f"records-{number}"}, "# Hub\n")
    return Config.load(vault)


def test_parse_rank_and_shown_are_read_only(vault):
    write_ideas(vault, bullet("Older?", TODAY - timedelta(days=2))
                + bullet("Newest?", source="memory/agent/Channels", needs="a Gmail app password")
                + "  - why: Another sentence.\n"
                + bullet("Middle?", TODAY - timedelta(days=1), why="")
                + "## Closed\n" + bullet("Outside?"))
    config = Config.load(vault)
    before = snapshot(vault.parent)
    result = ideas.items(config, TODAY)
    assert [item.text for item in result] == ["Newest?", "Middle?", "Older?"]
    newest = result[0]
    assert (newest.kind, newest.origin, newest.since, newest.source) == (
        "idea", "ideas", "2026-11-03", "memory/agent/Channels")
    assert newest.detail == "A useful result. Another sentence."
    assert newest.needs == "a Gmail app password"
    assert result == ideas.items(Vault(vault), TODAY)
    assert ideas.shown(config, TODAY) == newest
    assert snapshot(vault.parent) == before


@pytest.mark.parametrize("line", [
    "", "- Missing date", "- 2026-02-30 · Bad day? · [[Topic]]", "- 2026-11-03 · Bad link? · Topic",
    "- 2026-11-03 · Empty link? · [[]]", "- 2026-11-03 · Empty target? · [[|alias]]",
    "- 2026-11-03 ·   · [[Topic]]", "- 2026-11-03 · Text? · [[Topic]] · Extra",
])
def test_parse_skips_malformed_bullets(line):
    assert ideas.parse_section("## Open\n" + line) == []
    assert ideas.parse_section("## Other\n" + line) == []


@pytest.mark.parametrize("age,hubs,expected", [(0, 3, True), (20, 3, True), (21, 3, False),
                                              (100, 0, False), (100, 3, False), (None, 3, True)])
def test_new_owner_by_repo_age_alone(vault, monkeypatch, age, hubs, expected):
    config = owner(vault, monkeypatch, age, hubs)
    fm.write(vault / "Ordinary.md", {}, "# Not a responsibility\n")
    fm.write(vault / "agent/Nested.md", {"skill": "nested"}, "# Not at the root\n")
    fm.write(vault / "Log.md", {"skill": "record", "kind": "log"}, "# Not a hub\n")
    assert ideas.new_owner(config, TODAY) is expected


@pytest.mark.parametrize("answer", ideas.ANSWERS)
@pytest.mark.parametrize("age,days", [(5, 1), (100, 7)])
def test_every_response_starts_the_owner_cooldown(vault, monkeypatch, answer, age, days):
    config = owner(vault, monkeypatch, age)
    write_ideas(vault, bullet("First?") + bullet("Second?", TODAY - timedelta(days=1)))
    ideas.respond(config, "First?", TODAY, answer)
    assert len(ideas.items(config, TODAY)) == 1
    assert ideas.shown(config, TODAY) is None
    assert ideas.cooldown_until(config, TODAY) == TODAY + timedelta(days=days)
    assert ideas.shown(config, TODAY + timedelta(days=days - 1)) is None
    assert ideas.shown(config, TODAY + timedelta(days=days)).text == "Second?"
    assert ideas.cooldown_until(config, TODAY + timedelta(days=days)) is None


def test_cooldown_uses_latest_idea_response_across_months(vault):
    config = Config.load(vault)
    write_ideas(vault, bullet("Still open?"))
    needs.answered(config, date(2026, 10, 31), "- 2026-10-31 · idea · Other? · yes · T-002")
    needs.answered(config, TODAY, "- 2026-11-03 · dismissed no · idea · Latest? · id:123456789abc")
    needs.answered(config, TODAY, "- 2026-11-03 · dismissed no · ask · Decision? · id:abcdef123456")
    assert ideas.cooldown_until(config, TODAY) == date(2026, 11, 4)
    assert ideas.shown(config, date(2026, 11, 4)).text == "Still open?"


@pytest.mark.parametrize("age,days", [(5, 1), (100, 7)])
def test_add_spacing_and_duplicate_refusal(vault, monkeypatch, age, days):
    config = owner(vault, monkeypatch, age)
    ideas.add(config, TODAY, "Watch the inbox?", "agent/Channels", "Save the owner time.", "Gmail access")
    data, body = fm.read(vault / "agent/Ideas.md")
    assert data == {"kind": "orientation", "tier": "warm", "pinned": False}
    assert Vault(vault).kind_for("agent/Ideas.md") == "orientation"
    assert "## Open" in body and "  - needs: Gmail access" in body
    with pytest.raises(ValueError, match="next idea can be added"):
        ideas.add(config, TODAY + timedelta(days=days - 1), "Another idea?", "Topic")
    ideas.add(config, TODAY + timedelta(days=days), "Another idea?", "Topic")
    with pytest.raises(ValueError, match="already open"):
        ideas.add(config, TODAY + timedelta(days=30), "  WATCH   the\ninbox?  ", "Different source")


def test_add_uses_newest_bullet_date_and_preserves_other_sections(vault):
    path = write_ideas(vault, bullet("Newer?", TODAY) + bullet("Older?", TODAY - timedelta(days=4))
                       + "\n## Notes\nPreserve me.\n")
    config = Config.load(vault)
    with pytest.raises(ValueError):
        ideas.add(config, TODAY, "Fresh?", "Topic")
    ideas.add(config, TODAY + timedelta(days=1), "Fresh?", "Topic")
    assert path.read_text().endswith("## Notes\nPreserve me.\n")
    assert len(ideas.items(config, TODAY + timedelta(days=1))) == 3


def test_add_creates_missing_open_section_and_rejects_empty_input(vault):
    path = write_ideas(vault, "")
    path.write_text(path.read_text().replace("## Open", "## Notes"))
    config = Config.load(vault)
    with pytest.raises(ValueError):
        ideas.add(config, TODAY, " \n ", "Topic")
    with pytest.raises(ValueError, match="source must name a note"):
        ideas.add(config, TODAY, "Fresh?", "[[Topic]]")
    ideas.add(config, TODAY, "Fresh?", "Topic")
    assert "## Notes" in path.read_text()
    assert ideas.items(config, TODAY)[0].text == "Fresh?"


def test_yes_creates_ready_handoff_using_ids_from_closed_archives(vault):
    config = Config.load(vault)
    write_archive(config, Task("T-041", "Old work", "", {"state": "completed"}), "cold", TODAY)
    path = write_ideas(vault, bullet("Want me to watch your inbox?", needs="Gmail access")
                       + bullet("Keep this?", TODAY - timedelta(days=1), why="Keep its explanation."))
    item = ideas.items(config, TODAY)[0]
    ideas.respond(config, item.id, TODAY, "yes")
    task, = [task for task in load_tasks(config)[0] if task.tid == "T-042"]
    assert task.title == item.text and task.task_state == "ready"
    assert task.handoff == ("2026-11-03", "Home")
    assert due(load_tasks(config)[0]) == []   # the accepted offer still needs a Delivers line
    assert "- **Status:** A useful result." in task.body
    assert "- **Needs:** Gmail access" in task.body and "- **Source:** [[Topic]]" in task.body
    assert "Gmail access" not in path.read_text()
    assert "Keep its explanation." in path.read_text()
    assert "· idea · Want me to watch your inbox? · yes · T-042" in (vault / "agent/Answered/2026-11.md").read_text()
    assert task.state["uses"] == "2026-11-03"
    write_ideas(vault, bullet("No reason given?", why=""))
    ideas.respond(config, "No reason given?", TODAY, "yes")
    assert "**Status:**" not in [task for task in load_tasks(config)[0] if task.tid == "T-043"][0].body


def test_not_now_snoozes_fourteen_days_and_remains_reachable(vault):
    config = Config.load(vault)
    path = write_ideas(vault, bullet("Later?"))
    before = path.read_bytes()
    item, = ideas.items(config, TODAY)
    ideas.respond(config, item.id, TODAY, "not-now")
    assert path.read_bytes() == before
    assert ideas.items(config, TODAY + timedelta(days=13)) == []
    assert ideas.shown(config, TODAY + timedelta(days=13)) is None
    assert ideas.items(config, TODAY + timedelta(days=14)) == [item]
    with pytest.raises(ValueError):
        ideas.add(config, TODAY + timedelta(days=1), "later?", "Topic")
    ideas.respond(config, item.text, TODAY + timedelta(days=1), "yes")
    assert ideas._open(config, TODAY) == []


@pytest.mark.parametrize("answer", ["no", "stop-asking"])
def test_rejection_removes_details_and_permanently_blocks_text(vault, answer):
    config = Config.load(vault)
    path = write_ideas(vault, bullet("Watch the inbox?", needs="a password"))
    item, = ideas.items(config, TODAY)
    ideas.respond(config, item.id, TODAY, answer)
    assert ideas.items(config, TODAY) == []
    assert "a password" not in path.read_text() and "A useful result." not in path.read_text()
    assert item.id in needs.dismissed_ids(config)
    with pytest.raises(ValueError):
        ideas.add(config, TODAY + timedelta(days=100), " WATCH   THE inbox? ", "Another source")
    assert "already dismissed" in ideas.add_refusal(config, TODAY, "Watch the inbox?")
    with path.open("a") as stream:
        stream.write(bullet("Watch the inbox?", source="Another source"))
    assert ideas.items(config, TODAY + timedelta(days=100)) == []
    assert f"· dismissed {answer} · idea · Watch the inbox? · id:{item.id}" in (
        vault / "agent/Answered/2026-11.md").read_text()


@pytest.mark.parametrize("answer,status", [("yes", "accepted"), ("no", "declined"),
                                           ("stop-asking", "declined"), ("not-now", "proposed")])
@pytest.mark.parametrize("inline", [False, True])
def test_improvements_are_only_ideas_and_respond_in_their_hub(vault, answer, status, inline):
    entry = ("## Improvements\n### October 30 — Inbox summary\n- **Ask:** Want a short morning summary?\n"
             "- **Why:** Find important mail quickly.\n"
             + ("- `status: proposed`\n" if inline else "- **Status:** `proposed`, awaiting owner\n")
             + "### October 29 — Other\n- **Status:** adopted\n\n## Notes\nKeep me.\n")
    path = vault / "Correspondence.md"
    fm.write(path, {"skill": "own-mail", "records": "mail"}, entry)
    config = Config.load(vault)
    item, = ideas.items(config, TODAY)
    assert needs.items(config, TODAY) == []
    assert (item.kind, item.origin, item.text, item.detail, item.context) == (
        "idea", "improvement", "Want a short morning summary?", "Find important mail quickly.", "Correspondence")
    assert item.id == needs.improvements(entry, "Correspondence.md", TODAY)[0].id
    ideas.respond(config, item.id, TODAY, answer)
    section = path.read_text().split("### October 30 — Inbox summary", 1)[1].split("### October 29", 1)[0]
    assert needs.improvement_status(section) == status
    assert "## Notes\nKeep me." in path.read_text() and "- **Status:** adopted" in path.read_text()
    assert ideas.items(config, TODAY) == []
    if answer != "not-now":
        assert "2026-11-03" in section
    if answer == "yes":
        assert any(task.title == item.text and task.handoff for task in load_tasks(config)[0])
        assert due(load_tasks(config)[0]) == []   # no background chunks before Delivers is specified
    if answer in ("no", "stop-asking"):
        with pytest.raises(ValueError):
            ideas.add(config, TODAY + timedelta(days=100), item.text.upper(), "Topic")


def test_response_matching_errors_do_not_write(vault):
    write_ideas(vault, bullet("Watch inbox?") + bullet("Watch calendar?"))
    config = Config.load(vault)
    before = snapshot(vault.parent)
    for which, answer, message in (("Watch", "no", "2 open ideas match"), ("missing", "no", "no open idea"),
                                   ("Watch inbox?", "maybe", "answer must be")):
        with pytest.raises(ValueError, match=message):
            ideas.respond(config, which, TODAY, answer)
    assert snapshot(vault.parent) == before


def test_state_exports_one_idea_with_a_normalized_source_and_withholds_private_ideas(vault):
    write_ideas(vault, bullet("First?") + bullet("Second?", TODAY - timedelta(days=1)))
    config = Config.load(vault)
    result = state.build(config, TODAY)
    assert result["idea"] == asdict(ideas.shown(config, TODAY))
    assert result["needsYou"] == []
    ideas.respond(config, "First?", TODAY, "not-now")
    result = state.build(config, TODAY)
    assert result["idea"] is None
    assert state.build(config, TODAY + timedelta(days=1))["idea"]["text"] == "Second?"
    fm.write(vault / "Topic.md", {}, "# Topic")
    write_ideas(vault, bullet("Public?", source="memory/Topic#Part"))
    later = TODAY + timedelta(days=1)
    result = state.build(config, later)
    assert result["idea"]["source"] == "Topic.md#Part"
    assert result["idea"]["id"] == ideas.shown(config, later).id
    fm.write(vault / "Topic.md", {"tier": "private"}, "# Topic")
    assert state.build(config, later)["idea"] is None


def test_ideas_hygiene_checks_uncut_why_and_is_part_of_hygiene(vault):
    write_ideas(vault, bullet("No explanation?", why="")
                + bullet("word " * (needs.ASK_WORDS + 1), why="reason " * (needs.WHY_WORDS + 1))
                + bullet("Good?"))
    problems = validate.ideas_hygiene(Vault(vault))
    assert len(problems) == 3
    assert all("agent/Ideas.md" in problem for problem in problems)
    assert "no why line" in problems[0] and "17 words" in problems[1] and "41 words" in problems[2]
    assert all(problem in validate.hygiene(Vault(vault)) for problem in problems)
    assert len(ideas.items(Config.load(vault), TODAY)[1].detail.split()) == needs.WHY_WORDS


def test_ideas_are_not_added_to_compiled_context(vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    write_ideas(vault, bullet("Prepare a unique secret summary?"))
    context = compile_context.build(Vault(vault))
    assert "unique secret summary" not in context and "Ideas.md" not in context


def test_cli_shown_all_json_add_refusal_and_response(vault, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    argv = ["ideas", "--vault", str(vault)]
    cli.main(argv + ["--add", "Watch the inbox?", "--source", "agent/Channels", "--why", "Save time.",
                         "--needs", "Gmail access"])
    assert "added idea" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="next idea can be added 2026-11-04"):
        cli.main(argv + ["--add", "Another?", "--source", "Topic"])
    cli.main(argv)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2 and "Watch the inbox?" in lines[0] and lines[1] == "1 open, 0 snoozed; new owner"
    cli.main(argv + ["--respond", "Watch the inbox?", "--answer", "not-now"])
    capsys.readouterr()
    cli.main(argv + ["--json"])
    assert json.loads(capsys.readouterr().out) == {"shown": None, "open": 1, "snoozed": 1,
                                               "newOwner": True, "cooldownUntil": "2026-11-04"}
    cli.main(argv)
    assert capsys.readouterr().out == "1 open, 1 snoozed; new owner; next idea can show 2026-11-04\n"
    cli.main(argv + ["--all"])
    assert "Watch the inbox?" in capsys.readouterr().out


@pytest.mark.parametrize("options,message", [(["--source", "Topic"], "go with --add"),
                                            (["--add", "Idea?"], "needs --source"),
                                            (["--respond", "Idea?"], "needs --answer"),
                                            (["--answer", "no"], "goes with --respond")])
def test_cli_reports_invalid_options(vault, options, message):
    with pytest.raises(SystemExit, match=message):
        cli.main(["ideas", "--vault", str(vault), *options])
