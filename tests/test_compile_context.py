from pathlib import Path
from datetime import date

import pytest

from brain.vault import Vault
from brain import cli, compile_context, fm
from brain.config import Config
from brain.tasks import Task, write_archive
from conftest import git, nightly_argv, snapshot


@pytest.fixture
def context_vault(vault):
    fm.write(vault / "START.md", {"tier": "hot", "pinned": True}, "# Start\nSTART_BODY\n")
    path = vault / "agent/Tasks.md"
    data, body = fm.read(path)
    warm = Task("T-002", "Warm task", "ARCHIVED_FULL_BODY\n", {"status": "open", "tier": "warm"})
    write_archive(Config.load(vault), warm, "warm", date(2026, 10, 5))
    closed = Task("T-003", "Closed task", "CLOSED_BODY\n", {"status": "completed"})
    unstated = Task("T-004", "No status comment", "UNSTATED_BODY\n")
    fm.write(path, data, body + "\n" + warm.render_line(Config.load(vault)) + "\n" + closed.render()
             + "\n" + unstated.render() + "\n## Other\nREGISTER_TAIL\n")
    fm.write(vault / "Active.md", {"tier": "hot", "activation": 3, "index_line": "AUTOMATIC_ENTRY"},
             "# Active\nActive note body.\n")
    fm.write(vault / "Overflow.md", {"tier": "hot", "activation": 2, "index_line": "OVERFLOW_ENTRY"},
             "word " * 6001)
    return vault


def test_context_contains_source_bodies_and_only_open_register_sections(context_vault):
    text = compile_context.build(Vault(context_vault))
    assert "START_BODY" in text and "# Current" in text and "# Triggers" in text
    assert "pinned:" not in text and "tier:" not in text
    assert "ORIGINAL_FULL_BODY" in text and "### T-001" in text
    assert "### T-002" in text and "- **Warm:**" in text
    assert "ARCHIVED_FULL_BODY" not in text
    assert all(excluded not in text for excluded in
               ("### T-003", "### T-004", "CLOSED_BODY", "UNSTATED_BODY", "REGISTER_TAIL"))
    directory = text.split("## Directory: what the vault knows\n\n", 1)[1]
    assert directory.startswith("Most active, with what each holds:\n\n")
    assert "- [[memory/Active|Active]] — AUTOMATIC_ENTRY" in directory
    assert directory.index("AUTOMATIC_ENTRY") < directory.index("- Hubs and topics:")
    assert "OVERFLOW_ENTRY" not in text and "## Automatic load" not in text
    assert "Hot Set" not in text and "Warm Index" not in text
    positions = [text.index(f"## memory/{source}") for source in Config(Path('.')).context_sources
                 if (context_vault / source).is_file()]
    assert positions == sorted(positions)
    assert text.splitlines()[0] == "Generated nightly by `brain`."
    assert text.splitlines()[2].startswith("Edit the source notes instead:")
    assert text.endswith("Conversations and everything else are retrieved on demand with `brain retrieve`.\n")


def test_context_preview_does_not_write_even_with_out(context_vault, capsys):
    out = context_vault.parent / "custom.md"
    before = snapshot(context_vault.parent)
    cli.main(["compile-context", "--vault", str(context_vault), "--out", str(out)])
    captured = capsys.readouterr()
    assert captured.out == compile_context.build(Vault(context_vault))
    assert captured.err == compile_context.summary(captured.out) + "\n"
    assert snapshot(context_vault.parent) == before


def test_context_write_defaults_to_parent_without_git(context_vault, capsys):
    cli.main(["compile-context", "--vault", str(context_vault), "--write"])
    path = context_vault.parent / ".context.md"
    assert path.read_text() == compile_context.build(Vault(context_vault))
    assert not (context_vault / ".context.md").exists()
    assert capsys.readouterr().out == ""


def test_context_custom_out_and_word_warning(context_vault, capsys):
    (context_vault / "START.md").write_text("# Start\n" + "word " * 6001)
    out = context_vault.parent / "custom.md"
    cli.main(["compile-context", "--vault", str(context_vault), "--out", str(out), "--write"])
    assert out.read_text().startswith("Generated nightly")
    assert "WARNING: over 6000 words" in capsys.readouterr().err
    assert not (context_vault.parent / ".context.md").exists()


def test_repo_root_detects_git_above_vault_parent(repo):
    root, _ = repo
    nested = root.parent / "nested/memory"
    nested.mkdir(parents=True)
    assert compile_context.repo_root(Config.load(nested)) == root.parent


def test_nightly_context_is_regenerated_and_committed_at_repo_root(repo):
    root, _ = repo
    (root.parent / ".context.md").write_text("STALE_CONTEXT\n")
    cli.main(nightly_argv(root, "2026-10-05", "--write", "--commit"))
    context = root.parent / ".context.md"
    assert context.read_text() == compile_context.build(Vault(root))
    assert "STALE_CONTEXT" not in context.read_text()
    assert "T-001 — Sample" in context.read_text()
    assert git(root.parent, "show", "HEAD:.context.md") == context.read_text().strip()
    assert ".context.md" in git(root.parent, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()


def test_nightly_preview_keeps_real_context_untouched(repo):
    root, _ = repo
    context = root.parent / ".context.md"
    context.write_text("ORIGINAL_CONTEXT\n")
    before = snapshot(root)
    cli.main(nightly_argv(root, "2026-10-05"))
    assert context.read_text() == "ORIGINAL_CONTEXT\n"
    assert snapshot(root) == before


def test_context_directory_lists_hubs_people_and_open_tasks(context_vault):
    text = compile_context.build(Vault(context_vault))
    assert "## Directory: what the vault knows" in text
    body = text.split("## Directory: what the vault knows", 1)[1]
    assert "- Hubs and topics: " in body
    assert "- Open tasks: " in body
    assert "Active, Overflow" in body.split("- Hubs and topics: ", 1)[1].split("\n", 1)[0]


def test_context_includes_active_goals_and_capture_guidance_until_then(context_vault):
    from brain import goals
    text = compile_context.build(Vault(context_vault))
    assert "No active goals" in text and f"Ask {Config.load(context_vault).human_name} once" in text
    goals.new(Config.load(context_vault), "Read widely", "Read beyond my field")
    vault = Vault(context_vault)
    text = compile_context.build(vault)
    assert "## memory/human/Goals.md\n\n- G-1 — Read widely" in text
    assert text.index("# Current") < text.index("G-1 — Read widely") < text.index("# Triggers")
    assert "human/Goals" not in compile_context.directory(vault)
    assert Config.load(context_vault).template_values()["human_goals"] == "human/Goals.md"


def test_context_keeps_open_tasks_that_carry_state_without_status(context_vault):
    vault = context_vault
    path = vault / "agent/Tasks.md"
    data, body = fm.read(path)
    stated = Task("T-005", "Stated task", "STATED_BODY\n", {"state": "waiting_for_owner"})
    done = Task("T-006", "Done task", "DONE_BODY\n", {"state": "completed"})
    fm.write(path, data, body + "\n" + stated.render() + "\n" + done.render())
    text = compile_context.build(Vault(vault))
    assert "### T-005" in text and "STATED_BODY" in text
    assert "### T-006" not in text and "DONE_BODY" not in text


def test_context_includes_primed_after_current_once_the_night_wrote_it(context_vault):
    text = compile_context.build(Vault(context_vault))
    assert "## memory/agent/Primed.md" not in text
    fm.write(context_vault / "agent/Primed.md", {"kind": "primed", "tier": "hot"},
             "# Today\nWHERE_AND_WITH_WHOM\n\ngestures: open\n")
    text = compile_context.build(Vault(context_vault))
    assert "## memory/agent/Primed.md\n\n# Today\nWHERE_AND_WITH_WHOM" in text
    assert text.index("# Current") < text.index("WHERE_AND_WITH_WHOM") < text.index("# Triggers")
    assert "memory/agent/Primed.md" in text.splitlines()[2]


def test_upcoming_lists_dates_in_the_next_week_from_hubs_and_histories_only(context_vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-30")
    fm.write(context_vault / "Acme.md", {"kind": "hub", "tier": "warm"},
             "# Acme\nBenefits enrollment is due October 30. The offsite is November 4.\n"
             "The old deadline was October 2. Planning starts 2026-11-20.\n")
    (context_vault / "people/Faisal").mkdir(parents=True)
    fm.write(context_vault / "people/Faisal/History.md", {"kind": "log", "tier": "cold"},
             "- 2026-10-28 · said he is in Madison until November 2.\n")
    (context_vault / "agent/Explorations").mkdir(exist_ok=True)
    fm.write(context_vault / "agent/Explorations/2026-10.md", {"kind": "log", "tier": "cold"},
             "2026-10-29 10:30 | work | thing | drafted for November 3 | held\n")
    (context_vault / "Conversations").mkdir(exist_ok=True)
    fm.write(context_vault / "Conversations/2026-10-29 chat.md", {"kind": "conversation", "tier": "cold"},
             "He mentioned November 1 in passing.\n")
    text = compile_context.build(Vault(context_vault))
    block = text.split("## Upcoming: the next 7 days", 1)[1].split("\n## ", 1)[0]
    assert "- Fri Oct 30 · Benefits enrollment is due October 30. · [[Acme]]" in block
    assert "- Wed Nov 4 · Benefits enrollment is due October 30. The offsite is November 4. · [[Acme]]" in block
    assert block.count("Fri Oct 30") == 1
    assert "- Mon Nov 2 · 2026-10-28 · said he is in Madison until November 2. · [[people/Faisal/History]]" in block
    assert "October 2" not in block and "November 20" not in block
    assert "November 3" not in block and "November 1" not in block
    assert text.index("# Triggers") < text.index("## Upcoming") < text.index("### T-001")


def test_upcoming_section_can_be_switched_off_and_says_when_nothing_is_dated(context_vault, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-30")
    text = compile_context.build(Vault(context_vault))
    assert "## Upcoming: the next 7 days\n\n" in text and "- nothing dated" in text
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "start, tasks")
    assert "## Upcoming" not in compile_context.build(Vault(context_vault))


def test_prepared_drafts_are_drafts_not_hubs_and_never_auto_load(context_vault):
    (context_vault / "agent/Prepared").mkdir()
    fm.write(context_vault / "agent/Prepared/2026-10-29 putting game.md", {"kind": "draft", "tier": "hot", "uses": ["2026-10-29"]},
             "# Putting game\nHELD_THING\n")
    vault = Vault(context_vault)
    assert vault.kind_for("agent/Prepared/2026-10-29 putting game.md") == "draft"
    assert "Prepared" not in compile_context.directory(vault)
    from brain import index
    selected, _, _ = index.hot_selection(vault, 6000)
    assert all("Prepared" not in n.rel for n in selected)


def test_before_answering_answers_from_the_context_and_can_be_switched_off(context_vault, monkeypatch):
    text = compile_context.build(Vault(context_vault))
    assert text.index("## Before answering") < text.index("## memory/START.md")
    assert "Answer from that in one reply." in text
    assert "Do not run retrieval the recall block already ran" in text
    assert "run `brain retrieve` only when" in text
    assert "A reminder the owner asks for is a dated Triggers row, never a Needs you line." in text
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "start, tasks")
    assert "## Before answering" not in compile_context.build(Vault(context_vault))


def test_primed_loads_at_most_its_cap_and_validate_names_the_overrun(context_vault):
    from brain import validate
    lines = [f"- line {n} " + "word " * 18 for n in range(30)]   # 20 words a line, 600 in all
    fm.write(context_vault / "agent/Primed.md", {"kind": "primed", "tier": "hot"}, "# Today\n" + "\n".join(lines) + "\n")
    text = compile_context.build(Vault(context_vault))
    block = text.split("## memory/agent/Primed.md\n\n", 1)[1].split("\n## ", 1)[0]
    assert "line 0 " in block and "line 29 " not in block
    assert "(cut at 300 words; the rest is in memory/agent/Primed.md)" in block
    assert len(block.split()) <= 300 + 12
    assert validate.primed_size(Vault(context_vault)) == ["agent/Primed.md: 632 words; at most 300, and the context loads only that many"]


def test_context_loads_only_the_triggers_still_waiting_to_fire(context_vault):
    fm.write(context_vault / "agent/Triggers.md", {"kind": "orientation", "tier": "hot", "pinned": True},
             "# Triggers\n\n| id | class | when | do | source | status | created |\n| --- | --- | --- | --- | --- | --- | --- |\n"
             "| TR-001 | commitment | ACTIVE_WHEN | act | src | active | 2026-11-01 |\n\n## Fired\n\n"
             "| TR-002 | intention | FIRED_WHEN | done | src | fired | 2026-10-01 |\n\n## Retired\n\nRETIRED_ROW\n")
    text = compile_context.build(Vault(context_vault))
    assert "ACTIVE_WHEN" in text and "FIRED_WHEN" not in text and "RETIRED_ROW" not in text
    assert "(Fired and retired triggers are in memory/agent/Triggers.md.)" in text


def test_directory_has_no_active_block_when_no_note_is_hot(context_vault):
    for path in context_vault.rglob("*.md"):
        data, body = fm.read(path)
        if data.get("tier") == "hot":
            fm.write(path, {**data, "tier": "warm", "pinned": False}, body)
    text = compile_context.directory(Vault(context_vault))
    assert "Most active" not in text and "- Hubs and topics:" in text


def test_leftover_retired_views_are_neither_notes_nor_problems(context_vault):
    from brain import validate
    (context_vault / "Hot Set.md").write_text("# Hot set\n\n## Automatic load\n", encoding="utf-8")
    (context_vault / "Warm Index.md").write_text("# Warm index\n", encoding="utf-8")
    vault = Vault(context_vault)
    assert "Hot Set.md" not in vault.notes and "Warm Index.md" not in vault.notes
    assert not [p for p in validate.run(vault, set(vault.notes)) if "Hot Set" in p or "Warm Index" in p]
    assert "Hot Set" not in compile_context.directory(vault)


def test_a_run_can_ask_for_only_some_sections_within_what_the_vault_allows(context_vault, capsys, monkeypatch):
    full = compile_context.build(Vault(context_vault))
    narrow = compile_context.build(Vault(context_vault), ("current", "triggers"))
    assert "# Current" in narrow and "# Triggers" in narrow
    assert all(absent not in narrow for absent in ("START_BODY", "### T-001", "Directory: what the vault knows", "Before answering"))
    assert len(narrow) < len(full)
    cli.main(["compile-context", "--vault", str(context_vault), "--sections", "current, triggers"])
    assert capsys.readouterr().out == narrow
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "start,current")
    within = compile_context.build(Vault(context_vault), ("current", "triggers"))
    assert "# Current" in within and "# Triggers" not in within


def test_sections_are_checked_and_never_written(context_vault):
    for bad in (["--sections", "current,nonsense"], ["--sections", ","], ["--sections", "current", "--write"]):
        with pytest.raises(SystemExit):
            cli.main(["compile-context", "--vault", str(context_vault), *bad])


def test_only_real_month_names_are_dates():
    from datetime import date
    from brain.compile_context import DATE, mentioned
    for text in ("Maybe 3 people", "the market 12 times", "decided 5 things", "Junior 2"):
        assert DATE.search(text) is None, text
    found = [mentioned(m, date(2026, 11, 1)) for m in DATE.finditer("May 3, Sept. 4th and December 25, 2026")]
    assert found == [date(2027, 5, 3), date(2026, 9, 4), date(2026, 12, 25)]
