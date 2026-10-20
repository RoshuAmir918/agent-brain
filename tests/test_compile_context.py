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
    positions = [text.index(f"## memory/{source}") for source in Config(Path('.')).context_sources]
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
