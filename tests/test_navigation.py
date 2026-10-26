from pathlib import Path
import pytest

from unittest.mock import patch

from brain.config import Config
from brain import attribute, validate
from brain.vault import Vault


def make_vault(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "Live.md").write_text("---\ntier: warm\n---\n# Live\n")
    (root / "Design Mk. 1.md").write_text("---\ntier: warm\n---\n# Design\n")
    return Vault(root)


def test_generic_layout_resolves_distinct_profiles_orientation_and_skills(vault):
    relatives = ("human/Profile.md", "people/Alice_Example/Profile.md", "hermes/README.md",
                 "hermes/skills/example/SKILL.md")
    for relative in relatives:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Fixture\n")
    sources = vault.parent / "sources"
    sources.mkdir()
    (sources / "Profile.md").touch()
    archive = vault.parent / "Archive"
    archive.mkdir()
    (archive / "Receipt.md").touch()
    loaded = Vault(vault)
    for relative in relatives:
        assert loaded.resolve("memory/" + relative) == relative
    assert loaded.resolve("sources/Profile") == "../sources/Profile.md"
    assert loaded.resolve("Archive/Receipt") == "../Archive/Receipt.md"
    assert loaded.kind_for("people/Alice_Example/Profile.md") == "profile"
    assert loaded.kind_for("human/Profile.md") == "hub"
    assert loaded.kind_for("hermes/README.md") == "orientation"
    assert "hermes/skills/example/SKILL.md" not in loaded.notes


@pytest.mark.parametrize("prefix", ["", "memory/"])
def test_plain_path_attribution_preserves_lowercase_directories(vault, prefix):
    relatives = ("human/Profile.md", "people/Alice_Example/Profile.md", "agent/Reflections/Review.md")
    for relative in relatives:
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Fixture\n")
    text = "\n".join("Sources: " + prefix + relative for relative in relatives)
    uses = attribute.explicit_uses(Vault(vault), text, "2026-10-20T12:00Z")
    assert [use.target for use in uses] == list(relatives)


def test_dotted_titles_resolve_in_both_vault_siblings(tmp_path):
    vault = make_vault(tmp_path)
    human = vault.root.parent / "sources"
    human.mkdir()
    (human / "Pipeline Mk. 1.md").touch()
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.is_relative_to(human), "Human contents must not be read"
        return original(path, *args, **kwargs)

    with patch.object(Path, "read_text", guarded_read):
        assert vault.resolve("Design Mk. 1") == "Design Mk. 1.md"
        assert vault.resolve("Design Mk. 1.md") == "Design Mk. 1.md"
        assert vault.resolve("Pipeline Mk. 1") == "../sources/Pipeline Mk. 1.md"
        assert vault.resolve("sources/Pipeline Mk. 1") == "../sources/Pipeline Mk. 1.md"
        assert vault.resolve("Pipeline Mk. 2") is None


def test_fenced_examples_and_frozen_wrappers_are_not_navigation(tmp_path):
    vault = make_vault(tmp_path)
    text = """[[Live]]
````text
[[historical-missing]]
```
[[nested-example]]
```
````
~~~markdown
[[tilde-example]]
~~~~
[[current-missing]]
"""
    assert vault.links_in(text) == ["Live.md"]
    assert vault.unresolved_links(text) == ["current-missing"]


def test_inline_formats_and_source_examples_are_literal(tmp_path):
    vault = make_vault(tmp_path)
    text = ("Format: `reflected: [[link]]`. "
            "Example: ``<!-- Sources: [[kitchen]] · `text` -->``. "
            "[[Live]] [[real-missing]]")
    assert vault.links_in(text) == ["Live.md"]
    assert vault.unresolved_links(text) == ["real-missing"]


def test_unclosed_inline_tick_does_not_hide_a_real_link(tmp_path):
    vault = make_vault(tmp_path)
    assert vault.unresolved_links("Unmatched ` and [[missing]]") == ["missing"]
    assert vault.unresolved_links("Unmatched `\n\n[[missing]] and another `") == ["missing"]
    assert vault.unresolved_links(r"Escaped \` [[missing]] \`") == ["missing"]


@pytest.mark.parametrize("footer", ["Sources: [[Live]] · [[missing]]",
                                    "<!-- Sources: [[Live]] · [[missing]] -->"])
def test_actual_source_footer_stays_navigation_and_attribution(tmp_path, footer):
    vault = make_vault(tmp_path)
    text = "Answer.\n" + footer
    assert vault.links_in(text) == ["Live.md"]
    assert vault.unresolved_links(text) == ["missing"]
    uses = attribute.explicit_uses(vault, text, "2026-10-06T01:00Z")
    assert [u.target for u in uses] == ["Live.md"]


def test_unavailable_targets_are_errors_unless_presented_as_literal(tmp_path):
    vault = make_vault(tmp_path)
    human = vault.root.parent / "sources"
    (human / "Source Documents" / "Books").mkdir(parents=True)
    assert vault.resolve("Books") is None  # A directory is not a note.
    assert vault.unresolved_links("[[Important Codes]] [[Books]]") == ["Books", "Important Codes"]
    assert vault.unresolved_links("`Important Codes` (excluded); `Books` (unavailable)") == []


def test_frontmatter_requirement_still_applies_to_guides(tmp_path):
    vault = make_vault(tmp_path)
    guide = vault.root / "Guide.md"
    guide.write_text("# Guide\n\n`[[example]]`\n")
    vault.load()
    assert validate.run(vault, {"Guide.md"}) == [
        "Guide.md: missing frontmatter key tier"
    ]
    guide.write_text("---\ntier: warm\n---\n# Guide\n\n`[[example]]`\n")
    vault.load()
    assert validate.run(vault, {"Guide.md"}) == []


def test_relative_cli_vault_matches_absolute_without_human_reads(tmp_path, monkeypatch, capsys):
    from brain.nightly import main
    vault = make_vault(tmp_path)
    human = vault.root.parent / "sources"
    human.mkdir()
    (human / "Human Note.md").touch()
    (vault.root / "Live.md").write_text("---\ntier: warm\n---\n[[Human Note]]\n")
    work = vault.root / ".brain"
    work.mkdir()
    monkeypatch.chdir(work)
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(human), "Human contents must not be read"
        return original(path, *args, **kwargs)

    with patch.object(Path, "read_text", guarded_read):
        monkeypatch.setattr("sys.argv", ["brain", "validate", "--vault", ".."])
        with pytest.raises(SystemExit) as exit_relative:
            main()
        assert exit_relative.value.code == 0
        relative = capsys.readouterr().out
        monkeypatch.setattr("sys.argv", ["brain", "validate", "--vault", str(vault.root)])
        with pytest.raises(SystemExit) as exit_absolute:
            main()
        assert exit_absolute.value.code == 0
        absolute = capsys.readouterr().out
        assert relative == absolute == "0 problems\n"
        assert Vault(Path("..")).resolve("Human Note") == "../sources/Human Note.md"


def test_hot_budget_caps_first_unpinned_and_fits_later_candidates(tmp_path):
    from brain.index import build_hot_set
    from datetime import date
    vault = make_vault(tmp_path)
    for note in vault.notes.values():
        note.data.update(tier="cold")
    for name, count, activation in [("Oversized", 11, 3), ("Fits", 6, 2), ("Overflow", 5, 1)]:
        path = vault.root / (name + ".md")
        path.write_text("---\ntier: hot\npinned: false\nactivation: " + str(activation) + "\n---\n" + "word " * count)
    vault.load()
    oversized = vault.notes["Oversized.md"].body
    result = build_hot_set(vault, date(2026, 10, 5), 10)
    assert "- (over budget, load on demand) [[memory/Oversized" in result
    assert "- [[memory/Fits" in result
    assert "- (over budget, load on demand) [[memory/Overflow" in result
    assert vault.notes["Oversized.md"].body == oversized
    assert all(n.data["tier"] == "hot" for n in vault.notes.values() if n.stem in ("Oversized", "Fits", "Overflow"))


def test_pinned_hot_note_is_not_dropped_for_budget(tmp_path):
    from brain.index import build_hot_set
    from datetime import date
    vault = make_vault(tmp_path)
    for note in vault.notes.values():
        note.data["tier"] = "cold"
    pinned = vault.notes["Live.md"]
    pinned.data.update(tier="hot", pinned=True, activation=3)
    pinned.body = "word " * 11
    result = build_hot_set(vault, date(2026, 10, 5), 10)
    assert "- [[memory/Live" in result
    assert "over budget" not in result


def test_hot_exact_boundary_pinned_priority_and_overflow_reachable(tmp_path):
    from brain.index import build_hot_set, build_warm_index, hot_selection
    from datetime import date
    vault = make_vault(tmp_path)
    for n in vault.notes.values():
        n.data["tier"] = "cold"
    pinned = vault.notes["Live.md"]
    ordinary = vault.notes["Design Mk. 1.md"]
    pinned.data.update(tier="hot", pinned=True, activation=-10)
    ordinary.data.update(tier="hot", pinned=False, activation=10)
    pinned.body = "word " * 1000
    ordinary.body = "word " * 5000
    selected, overflow, total = hot_selection(vault, 6000)
    assert selected == [pinned, ordinary] and not overflow and total == 6000
    ordinary.body += "word "
    selected, overflow, total = hot_selection(vault, 6000)
    assert selected == [pinned] and overflow == [ordinary] and total == 1000
    warm = build_warm_index(vault, date(2026, 10, 5), [], 6000)
    assert "memory/Design Mk. 1" in warm
    hot = build_hot_set(vault, date(2026, 10, 5), 6000)
    assert "## Automatic load" in hot and "## On demand (effective warm)" in hot
    pinned.body = "word " * 6001
    hot = build_hot_set(vault, date(2026, 10, 5), 6000)
    assert "Pinned load exceeds the budget: 6001 words selected, 1 words over" in hot
    assert hot_selection(vault, 6000)[0] == [pinned]
    assert ordinary.data["tier"] == "hot"


def test_nondefault_budget_consistent_in_hot_and_warm_views(tmp_path):
    from brain.index import build_hot_set, build_warm_index
    from datetime import date
    vault = make_vault(tmp_path)
    for n in vault.notes.values():
        n.data["tier"] = "cold"
    note = vault.notes["Live.md"]
    note.data.update(tier="hot", pinned=False, activation=2)
    note.body = "word " * 11
    day = date(2026, 10, 5)
    assert "over budget, load on demand) [[memory/Live" in build_hot_set(vault, day, 10)
    assert "[[memory/Live" in build_warm_index(vault, day, [], 10)
    assert "over budget, load on demand) [[memory/Live" not in build_hot_set(vault, day, 11)
    assert "[[memory/Live" not in build_warm_index(vault, day, [], 11)


def test_validation_checks_links_only_in_changed_bodies(tmp_path):
    vault = make_vault(tmp_path)
    vault.notes["Live.md"].body = "[[missing]]"
    assert validate.run(vault, {"Live.md"}) == ["Live.md: unresolved wikilinks ['missing']"]
    assert validate.run(vault, {"Live.md"}, body_changed=set()) == []
    assert validate.run(vault, {"Live.md"}, body_changed={"Live.md"}) == [
        "Live.md: unresolved wikilinks ['missing']"]


def test_validation_requires_pinned_hot_and_superseded_header(tmp_path):
    vault = make_vault(tmp_path)
    note = vault.notes["Live.md"]
    note.data.update(pinned=True, superseded_by="Design Mk. 1.md")
    assert validate.run(vault, set()) == [
        "Live.md: pinned but tier=warm",
        "Live.md: superseded_by set but no 'superseded by' line at top",
    ]
    note.data["tier"] = "hot"
    note.body = "Superseded by [[Design Mk. 1]].\n" + note.body
    assert validate.run(vault, {"Live.md"}) == []


def test_open_archived_task_requires_warm_index_entry(vault):
    from datetime import date
    from brain.tasks import Task, write_archive

    task = Task("T-099", "Archived open", "- **Status:** waiting\n", {"status": "open"})
    write_archive(Config.load(vault), task, "cold", date(2026, 10, 5))
    assert validate.run(Vault(vault), set()) == ["T-099: open task archived but not listed in Warm Index"]
    (vault / "Warm Index.md").write_text("# Warm index\n[[memory/agent/Tasks Archive/T-099|T-099]]\n")
    assert validate.run(Vault(vault), {"Warm Index.md"}) == []


def test_receipt_folders_and_declared_logs_are_never_notes(tmp_path):
    (tmp_path / "brain.toml").write_text('[brain]\nlog_dirs = ["Receipts"]\n')
    root = tmp_path / "memory"
    for relative, text in {
        "agent/Receipts/2026-10-25-run.md": "---\ntier: hot\n---\n# Ran 06:30\n",
        "Conversations/2026-10-25 Routine record.md": "---\nkind: log\ntier: hot\n---\n# Record\n",
        "Conversations/2026-10-25 Chat.md": "---\ntier: hot\n---\n# Chat\n",
        "agent/Receipts/README.md": "---\nkind: log\n---\n# Receipts\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    loaded = Vault(root)
    assert loaded.kind_for("agent/Receipts/2026-10-25-run.md") == "log"
    assert loaded.kind_for("Conversations/2026-10-25 Routine record.md") == "log"
    assert loaded.kind_for("Conversations/2026-10-25 Chat.md") == "episode"
    assert loaded.kind_for("agent/Receipts/README.md") == "log"


def test_single_primed_file_is_primed(tmp_path):
    root = tmp_path / "memory"
    (root / "agent").mkdir(parents=True)
    (root / "agent/Primed.md").write_text("---\nkind: primed\ntier: hot\n---\n# Today\n")
    assert Vault(root).kind_for("agent/Primed.md") == "primed"
