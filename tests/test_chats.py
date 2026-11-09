import json

import pytest

from brain import cli, evidence, seed, tasks
from brain.config import Config
from brain.sources import chats
from brain.vault import Vault


def export(folder, name="chatgpt", items=None):
    folder.mkdir(parents=True, exist_ok=True)
    items = items if items is not None else [
        {"id": "a", "title": "Visa for Lisbon", "at": "2026-10-02", "said": ["Do I need a visa for a hotel stay in Lisbon?"]},
        {"id": "b", "title": "Rust lifetimes", "at": "2026-11-01", "said": ["Why does this borrow fail?", "And now?"]},
        {"id": "c", "title": "", "at": "2026-09-01", "said": ["  "]},
    ]
    path = folder / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in items) + "\nnot json\n", encoding="utf-8")
    return path


def test_reading_lists_the_owners_words_newest_first_and_counts_what_it_could_not_read(tmp_path):
    data = chats.reading(export(tmp_path))
    assert (data["conversations"], data["messages"], data["from"], data["to"]) == (2, 3, "2026-10-02", "2026-11-01")
    assert [c["title"] for c in data["read"]] == ["Rust lifetimes", "Visa for Lisbon"]
    assert data["read"][0]["said"] == "Why does this borrow fail? / And now?"
    assert data["months"] == {"2026-10": 1, "2026-11": 1} and data["problems"] == ["line 4 is not a conversation"]
    assert "ChatGPT: 2 conversations, 3 messages by the owner, 2026-10-02 to 2026-11-01" in chats.render(data)


def test_rows_are_one_per_conversation_hinted_by_what_it_was_about(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    export(tmp_path / "channels" / "chats")
    cli.main(["source", "chats", "extract"])
    rows = sorted(evidence.read("chats"), key=lambda row: row.date)
    assert [(row.verb, row.fact, row.whose, row.hints) for row in rows] == [
        ("asked", "ChatGPT: Visa for Lisbon", "own", ("places",)),
        ("asked", "ChatGPT: Rust lifetimes", "own", ("reading & ideas",))]
    assert rows[0].pointer.startswith("chats:chatgpt/")


def test_a_chats_file_is_named_for_its_provider(tmp_path):
    with pytest.raises(ValueError, match="chatgpt.jsonl or claude.jsonl"):
        chats.reading(export(tmp_path, name="gemini"))


def test_hand_off_opens_a_task_until_its_export_is_read_and_clear_removes_the_export(vault, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("BRAIN_TODAY", "2026-11-09")
    path = export(tmp_path / "channels" / "chats", name="claude")
    argv = ["portrait", "seed", "--chats", str(path), "--hand-off", "--json", "--vault", str(vault)]
    cli.main(argv)
    opened = json.loads(capsys.readouterr().out)
    assert opened["status"] == "opened" and opened["account"] == "claude"
    task = next(t for t in tasks.load_tasks(Config.load(vault))[0] if t.tid == opened["task"])
    assert task.handoff and "Claude" in task.title and f"--chats {path}" in task.body
    cli.main(argv)
    assert json.loads(capsys.readouterr().out) == {**opened, "status": "reading"}
    assert seed.seeds(Vault(vault)) == [{"account": "claude", "read": None, "task": opened["task"]}]
    cli.main(["portrait", "seed", "--chats", str(path), "--clear", "--vault", str(vault)])
    assert capsys.readouterr().out.strip() == "claude.jsonl: removed" and not path.exists()


@pytest.mark.parametrize("argv, message", [
    (["seed", "--chats", "x/chatgpt.jsonl", "--hand-off", "--clear"], "--hand-off, --clear or neither"),
    (["seed", "--account", "a@example.com", "--clear"], "--clear goes with --chats"),
    (["coverage", "--chats", "x/chatgpt.jsonl"], "go with seed"),
    (["seed", "--chats", "x/chatgpt.jsonl"], "no chat export"),
])
def test_chats_argument_refusals(vault, argv, message):
    with pytest.raises(SystemExit, match=message):
        cli.main(["portrait", *argv, "--vault", str(vault)])


def test_an_organisation_never_reads_a_chat_export(vault, tmp_path):
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "memory"\nedition = "enterprise"\n')
    with pytest.raises(SystemExit, match="a person's own"):
        cli.main(["portrait", "seed", "--chats", str(export(tmp_path)), "--hand-off", "--vault", str(vault)])
