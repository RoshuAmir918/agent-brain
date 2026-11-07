import json

import pytest

from brain import cli, fm, retrieve, triggers
from brain.vault import Vault
from conftest import snapshot


@pytest.fixture
def retrieval_vault(vault):
    notes = {
        "Acme Context.md": "# Acme project\nQuiet description.\n## Build pipelines\n[[Linked]]\n",
        "Body.md": "# Report\nQuiet description.\n\nacme acme\n",
        "Linked.md": "# Linked\nQuiet description.\n[[Second Hop]]\n",
        "Second Hop.md": "# Second hop\nQuiet description.\n",
        "people/Alice_Example/Profile.md": "# Contact\nQuiet description.\n",
        "Conversations/Session.md": "# Acme\nQuiet description.\n",
        "agent/Use Log/2026-10.md": "# Acme\nQuiet description.\n",
        "agent/Primed/2026-10-05.md": "# Acme\nQuiet description.\n",
        "agent/Dreams/2026-10-05.md": "# Acme\nQuiet description.\n",
    }
    for rel, body in notes.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, {"tier": "warm", "index_line": "Quiet description."}, body)
    fm.write(vault / "Custom Log.md", {"kind": "log", "tier": "hot"}, "# Acme\n")
    fm.write(vault / "Body.md", {"tier": "hot", "pinned": True}, notes["Body.md"])
    return Vault(vault)


def test_title_beats_body_and_pinning_has_no_bonus(retrieval_vault):
    candidates = retrieve.search(retrieval_vault, "acme")
    assert candidates[0].path == "memory/Acme Context.md"
    assert candidates[0].score == 3.0 and "title/path: acme" in candidates[0].reason
    body = next(candidate for candidate in candidates if candidate.path.endswith("/Body.md"))
    assert body.score == 0.5 and body.reason == "body match: acme"


def test_one_hop_raises_a_linked_note_but_does_not_spread_twice(retrieval_vault):
    candidates = retrieve.search(retrieval_vault, "acme")
    linked = next(candidate for candidate in candidates if candidate.path.endswith("/Linked.md"))
    assert linked.score == 1.0 and "linked from memory/Acme Context.md" in linked.reason
    assert not any(candidate.path.endswith("/Second Hop.md") for candidate in candidates)


def test_excluded_notes_do_not_score_or_receive_link_spread(retrieval_vault):
    retrieval_vault.notes["Acme Context.md"].body += "[[agent/Primed/2026-10-05]] [[Custom Log]] [[Conversations/Session]]"
    paths = {candidate.path for candidate in retrieve.search(retrieval_vault, "acme", 100)}
    assert paths == {"memory/Acme Context.md", "memory/Body.md", "memory/Linked.md"}


def test_people_directory_tokens_and_heading_and_index_weights(retrieval_vault):
    profile = retrieve.search(retrieval_vault, "alice")[0]
    assert profile.path == "memory/people/Alice_Example/Profile.md"
    assert profile.score == 3.0 and "title/path" in profile.reason
    heading = retrieve.search(retrieval_vault, "pipelines")[0]
    assert heading.score == 1.0 and heading.reason == "heading: pipelines"
    retrieval_vault.notes["Body.md"].data["index_line"] = "Compiler reliability"
    index_hit = retrieve.search(retrieval_vault, "compiler")[0]
    assert index_hit.score == 3.0 and index_hit.reason == "index line: compiler"


def test_tier_factors_do_not_depend_on_pin(retrieval_vault):
    for tier in ("hot", "warm", "cold"):
        fm.write(retrieval_vault.root / f"Starforge {tier}.md",
                 {"tier": tier, "pinned": tier == "cold"}, "# Record\nQuiet description.\n")
    retrieval_vault.load()
    candidates = retrieve.search(retrieval_vault, "starforge")
    assert [(candidate.tier, candidate.score) for candidate in candidates] == [
        ("hot", 4.5), ("warm", 3.0), ("cold", pytest.approx(1.8))]


def test_task_id_matches_register_section(retrieval_vault):
    candidate = retrieve.search(retrieval_vault, "t-001")[0]
    assert candidate.path == "memory/agent/Tasks.md#T-001 — Sample"
    assert candidate.score == 5.0 and candidate.reason == "ID match: T-001 — Sample"


def test_trigger_id_matches_the_row(retrieval_vault):
    row = "| TR-002 | intention | Acme CI fails | check timeout | T-001 | active | 2026-10-05 |"
    retrieval_vault.notes["agent/Triggers.md"].body = triggers.TEMPLATE + row + "\n"
    candidate = retrieve.search(retrieval_vault, "TR-002")[0]
    assert candidate.path == "memory/agent/Triggers.md"
    assert candidate.score == 5.0 and candidate.reason == f"ID match: {row}"


def test_tokens_preserve_ids_and_dates_and_exact_body_hits(retrieval_vault):
    assert retrieve.tokens("The T-023, TR-002; 2026-10-05! Alice_Example CI") == {
        "t-023", "tr-002", "2026-10-05", "alice", "example"}
    retrieval_vault.notes["Body.md"].body += "\n2026-10-05 compiler compilertools adapter\n"
    candidate = retrieve.search(retrieval_vault, "2026-10-05 compiler adapter")[0]
    assert candidate.score == 1.5 and "body match" in candidate.reason
    assert not retrieve.search(retrieval_vault, "compile")


def test_retrieval_cli_json_limit_and_no_writes(retrieval_vault, capsys):
    before = snapshot(retrieval_vault.root)
    cli.main(["retrieve", "--vault", str(retrieval_vault.root), "acme", "--limit", "2", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 2
    assert set(rows[0]) == {"path", "tier", "score", "reason"}
    assert rows[0]["path"] == "memory/Acme Context.md"
    assert snapshot(retrieval_vault.root) == before


def test_stopword_query_has_no_candidates(retrieval_vault, capsys):
    cli.main(["retrieve", "--vault", str(retrieval_vault.root), "the and of to CI"])
    assert capsys.readouterr().out == "no candidates\n"
    cli.main(["retrieve", "--vault", str(retrieval_vault.root), "the and", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_retrieval_json_forms_add_index_line_or_body(retrieval_vault, capsys):
    argv = ["retrieve", "--vault", str(retrieval_vault.root), "acme", "--limit", "1", "--json"]
    cli.main(argv + ["--form", "snippet"])
    assert json.loads(capsys.readouterr().out)[0]["text"] == "Quiet description."
    cli.main(argv + ["--form", "full"])
    assert json.loads(capsys.readouterr().out)[0]["text"] == "# Acme project\nQuiet description.\n## Build pipelines\n[[Linked]]"
    cli.main(argv + ["--form", "names"])
    assert set(json.loads(capsys.readouterr().out)[0]) == {"path", "tier", "score", "reason"}


def test_retrieval_full_form_of_a_task_match_is_its_entry(retrieval_vault, capsys):
    cli.main(["retrieve", "--vault", str(retrieval_vault.root), "t-001", "--json", "--form", "full"])
    row = json.loads(capsys.readouterr().out)[0]
    assert row["path"].startswith("memory/agent/Tasks.md#T-001") and "ORIGINAL_FULL_BODY" in row["text"]
    assert "### T-001" not in row["text"]


def test_a_warm_task_gives_its_archived_entry_and_an_archived_only_task_is_found(retrieval_vault):
    from datetime import date
    from brain.tasks import Task, write_archive
    config = retrieval_vault.config
    path = retrieval_vault.root / "agent/Tasks.md"
    data, body = fm.read(path)
    warm = Task("T-002", "Warm task", "ARCHIVED_FULL_BODY\n", {"status": "open", "tier": "warm"})
    write_archive(config, warm, "warm", date(2026, 10, 5))
    write_archive(config, Task("T-003", "Gone task", "GONE_BODY\n", {"status": "completed"}), "closed", date(2026, 10, 5))
    fm.write(path, data, body + "\n" + warm.render_line(config))
    vault = Vault(retrieval_vault.root)
    found = retrieve.search(vault, "t-002")[0]
    assert found.path == "memory/agent/Tasks.md#T-002 — Warm task"
    assert "ARCHIVED_FULL_BODY" in retrieve.text(vault, found, "full")
    [gone] = retrieve.search(vault, "t-003")
    assert gone.path == "memory/agent/Tasks Archive/T-003.md#T-003"
    assert "GONE_BODY" in retrieve.text(vault, gone, "full") and retrieve.text(vault, gone, "snippet") == "Gone task"
