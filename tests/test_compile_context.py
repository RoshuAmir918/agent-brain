from pathlib import Path
from datetime import date

import pytest

from brain.vault import Vault
from brain import compile_context, fm, nightly
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
    fm.write(vault / "Hot Set.md", {}, "# Hot set\nHOT_PREAMBLE\n\n## Automatic load\n\n"
             "- [[memory/START|Start]] — AUTOMATIC_ENTRY\n\n"
             "## On demand (effective warm)\n\nOVERFLOW_ENTRY\n")
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
    assert "## Automatic load\n\n- [[memory/START|Start]] — AUTOMATIC_ENTRY" in text
    assert "OVERFLOW_ENTRY" not in text and "HOT_PREAMBLE" not in text
    positions = [text.index(f"## memory/{source}") for source in Config(Path('.')).context_sources
                 if (context_vault / source).is_file()]
    assert positions == sorted(positions)
    assert text.splitlines()[0] == "Generated nightly by `brain`."
    assert text.splitlines()[2].startswith("Edit the source notes instead:")
    assert text.endswith("Warm Index, Conversations and everything else are retrieved on demand with `brain retrieve`.\n")


def test_context_preview_does_not_write_even_with_out(context_vault, capsys):
    out = context_vault.parent / "custom.md"
    before = snapshot(context_vault.parent)
    nightly.main(["compile-context", "--vault", str(context_vault), "--out", str(out)])
    captured = capsys.readouterr()
    assert captured.out == compile_context.build(Vault(context_vault))
    assert captured.err == compile_context.summary(captured.out) + "\n"
    assert snapshot(context_vault.parent) == before


def test_context_write_defaults_to_parent_without_git(context_vault, capsys):
    nightly.main(["compile-context", "--vault", str(context_vault), "--write"])
    path = context_vault.parent / ".hermes.md"
    assert path.read_text() == compile_context.build(Vault(context_vault))
    assert not (context_vault / ".hermes.md").exists()
    assert capsys.readouterr().out == ""


def test_context_custom_out_and_word_warning(context_vault, capsys):
    (context_vault / "START.md").write_text("# Start\n" + "word " * 6001)
    out = context_vault.parent / "custom.md"
    nightly.main(["compile-context", "--vault", str(context_vault), "--out", str(out), "--write"])
    assert out.read_text().startswith("Generated nightly")
    assert "WARNING: over 6000 words" in capsys.readouterr().err
    assert not (context_vault.parent / ".hermes.md").exists()


def test_repo_root_detects_git_above_vault_parent(repo):
    root, _ = repo
    nested = root.parent / "nested/memory"
    nested.mkdir(parents=True)
    assert compile_context.repo_root(Config.load(nested)) == root.parent


def test_nightly_context_is_regenerated_and_committed_at_repo_root(repo):
    root, _ = repo
    (root.parent / ".hermes.md").write_text("STALE_CONTEXT\n")
    nightly.main(nightly_argv(root, "2026-10-05", "--write", "--commit"))
    context = root.parent / ".hermes.md"
    assert context.read_text() == compile_context.build(Vault(root))
    assert "STALE_CONTEXT" not in context.read_text()
    assert "T-001 — Sample" in context.read_text()
    assert git(root.parent, "show", "HEAD:.hermes.md") == context.read_text().strip()
    assert ".hermes.md" in git(root.parent, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()


def test_nightly_preview_keeps_real_context_untouched(repo):
    root, _ = repo
    context = root.parent / ".hermes.md"
    context.write_text("ORIGINAL_CONTEXT\n")
    before = snapshot(root)
    nightly.main(nightly_argv(root, "2026-10-05"))
    assert context.read_text() == "ORIGINAL_CONTEXT\n"
    assert snapshot(root) == before


def test_context_directory_lists_hubs_people_and_open_tasks(context_vault):
    text = compile_context.build(Vault(context_vault))
    assert "## Directory: what the vault knows" in text
    body = text.split("## Directory: what the vault knows", 1)[1]
    assert "- Hubs and topics: " in body
    assert "- Open tasks: " in body
    assert "Hot Set" not in body.split("- Hubs and topics: ", 1)[1].split("\n", 1)[0]


def test_context_includes_goals_when_present_and_skips_them_until_then(context_vault):
    text = compile_context.build(Vault(context_vault))
    assert "## memory/human/Goals.md" not in text
    (context_vault / "human").mkdir()
    fm.write(context_vault / "human/Goals.md", {"kind": "orientation", "pinned": True, "tier": "hot"},
             "# Goals\n## Active goals\nGOALS_BODY\n")
    vault = Vault(context_vault)
    text = compile_context.build(vault)
    assert "## memory/human/Goals.md\n\n# Goals" in text and "GOALS_BODY" in text
    assert text.index("# Current") < text.index("GOALS_BODY") < text.index("# Triggers")
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
    fm.write(context_vault / "Harbor.md", {"kind": "hub", "tier": "warm"},
             "# Harbor\nBenefits enrollment is due October 30. The offsite is November 4.\n"
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
    assert "- Fri Oct 30 · Benefits enrollment is due October 30. · [[Harbor]]" in block
    assert "- Wed Nov 4 · Benefits enrollment is due October 30. The offsite is November 4. · [[Harbor]]" in block
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
