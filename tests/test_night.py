"""The complete night, with local Git repositories and fabricated evidence."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from brain.config import Config
from brain import attribute, fm, nightly, triggers
from brain.tasks import Task, load_tasks, parse_register, read_archive_body
from conftest import git, nightly_argv, snapshot


DAY = "2026-10-05"


@pytest.fixture
def night_vault(repo):
    root, remote = repo
    fm.write(root / "Hot Hub.md", {"uses": ["2026-10-04"]}, "# Hot hub\nRecent work.\n")
    fm.write(root / "Warm Hub.md", {"uses": ["2026-09-20"]}, "# Warm hub\nOlder work.\n")
    hot = Task("T-001", "Ship thing", "- **Status:** in progress\n"
               "- **Next action:** ship the complete fixture\n",
               {"uses": "2026-10-04", "status": "open", "tier": "hot"})
    warm = Task("T-002", "Quiet thing", "- **Status:** waiting\n"
                "- **Next action:** retain the whole quiet task\n",
                {"uses": "2026-09-20", "status": "open", "tier": "warm"})
    data, _ = fm.read(root / "agent/Tasks.md")
    fm.write(root / "agent/Tasks.md", data,
             "# Tasks\n\n## Active work\n\n" + hot.render() + "\n" + warm.render()
             + "\n## Operator notes\nKeep this prose.\n")
    body = triggers.TEMPLATE.replace("## Fired", (
        "| TR-001 | commitment | 2026-10-05 | revisit shipping | T-001 | active | 2026-10-04 |\n"
        "\nTrigger evidence stays attached.\n\n## Fired"))
    fm.write(root / "agent/Triggers.md", {"tier": "hot", "pinned": True}, body)
    attribute.append_log(root / "agent/Use Log/2026-10.md", [
        attribute.Use("2026-10-05T01:00Z", "Hot Hub.md", "fixture reliance", "explicit"),
        attribute.Use("2026-10-04T01:00Z", "T-001", "fixture task reliance", "explicit"),
    ])
    (root / "Hot Set.md").write_text("STALE_HOT_VIEW\n")
    (root / "Warm Index.md").write_text("STALE_WARM_VIEW\n")
    current = root / "CURRENT.md"
    current.write_text(current.read_text().replace(nightly.MARK_START, nightly.MARK_START + "\nSTALE_THREAD"))
    log = root / "agent/Sleep Log/2026-10.md"
    log.parent.mkdir()
    log.write_text("# Sleep log — 2026-10\n\nPRIOR_ENTRY\n")
    return root, remote


def cli(root, *extra):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run(
        [sys.executable, "-m", "brain", *nightly_argv(root, DAY, *extra)],
        cwd=root.parent, env=env, capture_output=True, text=True)


def test_full_night_commits_only_agent_notes_and_pushes_one_commit(night_vault):
    root, remote = night_vault
    before = git(remote, "rev-parse", "main")
    (root / ".brain").mkdir()
    code = root / ".brain/fixture.py"
    code.write_text("UNRELATED_CODE\n")
    outside = root.parent / "unrelated.txt"
    outside.write_text("UNRELATED_USER_FILE\n")
    sleep = root / "agent/Sleep Log/2026-10.md"
    prior_log = sleep.read_bytes()

    result = cli(root, "--write", "--commit", "--push")
    assert result.returncode == 0, result.stdout + result.stderr
    tip = git(remote, "rev-parse", "main")
    assert tip != before
    assert git(root.parent, "rev-parse", "HEAD") == tip
    assert git(remote, "rev-parse", "main^") == before
    assert git(remote, "rev-list", "--count", f"{before}..main") == "1"
    paths = git(remote, "diff-tree", "--no-commit-id", "--name-only", "-r", tip).splitlines()
    assert paths and all(p.startswith("memory/") or p == ".hermes.md" for p in paths)
    assert not any(p.startswith("memory/.brain/") for p in paths)
    assert {".hermes.md", "memory/Hot Set.md", "memory/Warm Index.md",
            "memory/CURRENT.md", "memory/agent/Sleep Log/2026-10.md"} <= set(paths)
    assert "memory/Hot Hub|" in (root / "Hot Set.md").read_text()
    assert "memory/Warm Hub|" in (root / "Warm Index.md").read_text()
    current = (root / "CURRENT.md").read_text()
    assert "STALE_THREAD" not in current and "T-001 — Ship thing" in current
    assert "T-002" not in current
    _, tasks = parse_register(fm.read(root / "agent/Tasks.md")[1])
    assert [t.state["tier"] for t in tasks] == ["hot", "warm"]
    assert tasks[0].uses == [DAY, "2026-10-04"]
    assert tasks[1].body.strip().startswith("- **Warm:**")
    assert "retain the whole quiet task" in read_archive_body(Config.load(root), "T-002")
    assert "fired 2026-10-05" in (root / "agent/Triggers.md").read_text()
    assert sleep.read_bytes().startswith(prior_log)
    assert sleep.read_text().count(f"## {DAY} nightly script (write)") == 1
    assert "pushed" in result.stdout
    assert code.read_text() == "UNRELATED_CODE\n"
    assert outside.read_text() == "UNRELATED_USER_FILE\n"
    assert git(root.parent, "ls-files", "memory/.brain", "unrelated.txt") == ""


@pytest.mark.parametrize("cold_count", [0, 2])
def test_same_night_is_idempotent_except_sleep_log(night_vault, cold_count):
    root, _ = night_vault
    path = root / "agent/Tasks.md"
    data, body = fm.read(path)
    for n in range(cold_count):
        body += "\n" + Task(f"T-{10 + n:03d}", f"Cold task {n}", "- **Status:** waiting\n",
                            {"status": "open", "uses": "2026-01-01"}).render()
    fm.write(path, data, body)
    nightly.main(nightly_argv(root, DAY, "--write"))
    before = snapshot(root)
    log = root / "agent/Sleep Log/2026-10.md"
    previous = log.read_bytes()
    nightly.main(nightly_argv(root, DAY, "--write"))
    after = snapshot(root)
    assert {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)} == {
        "agent/Sleep Log/2026-10.md"}
    assert log.read_bytes().startswith(previous)
    assert log.read_text().count(f"## {DAY} nightly script (write)") == 2


def test_interrupted_night_converges_to_clean_run(night_vault, tmp_path, monkeypatch):
    root, _ = night_vault
    clean = tmp_path / "clean/memory"
    shutil.copytree(root, clean)
    nightly.main(nightly_argv(clean, DAY, "--write"))
    before = snapshot(root)

    def interrupted(*args, **kwargs):
        assert fm.read(root / "Hot Hub.md")[0]["uses"] == [DAY, "2026-10-04"]
        raise RuntimeError("interrupted after notes were saved")

    with monkeypatch.context() as patch:
        patch.setattr(nightly, "process_tasks", interrupted)
        with pytest.raises(RuntimeError, match="interrupted after notes were saved"):
            nightly.main(nightly_argv(root, DAY, "--write"))
    assert snapshot(root) != before
    nightly.main(nightly_argv(root, DAY, "--write"))

    def contents(tree):
        return {p.relative_to(tree).as_posix(): p.read_bytes() for p in tree.rglob("*")
                if p.is_file() and not p.relative_to(tree).as_posix().startswith("agent/Sleep Log/")}

    assert contents(root) == contents(clean)


def test_task_identity_survives_hot_warm_hot_cold_hot(repo):
    root, _ = repo
    original = load_tasks(Config.load(root))[0][0].body.strip()
    log = root / "agent/Use Log/2026-10.md"

    def check(day, tier):
        nightly.main(nightly_argv(root, day, "--write"))
        register = fm.read(root / "agent/Tasks.md")[1]
        headings = re.findall(r"^### T-001 —", register, re.M)
        assert len(headings) == (0 if tier == "cold" else 1)
        registered, archived = load_tasks(Config.load(root))
        matches = [t for t in registered + archived if t.tid == "T-001"]
        assert len(matches) == 1
        assert matches[0].state["tier"] == tier
        assert matches[0].body.strip() == original
        if tier == "hot":
            assert original in register
            assert "- **Warm:**" not in register
            assert "T-001" in (root / "CURRENT.md").read_text()
        else:
            assert read_archive_body(Config.load(root), "T-001").strip() == original
            if tier == "warm":
                _, views = parse_register(register)
                assert len(views[0].body.strip().splitlines()) == 1
                assert views[0].body.startswith("- **Warm:**")
            else:
                index = (root / "Warm Index.md").read_text().split("## Cold open tasks\n", 1)[1]
                assert "Tasks Archive/T-001|T-001]]" in index
                assert "T-001" not in (root / "CURRENT.md").read_text()

    check("2026-10-05", "hot")
    check("2026-10-13", "warm")
    attribute.append_log(log, [attribute.Use("2026-10-14T01:00Z", "T-001", "used again", "explicit")])
    check("2026-10-14", "hot")
    check("2027-01-01", "cold")
    attribute.append_log(root / "agent/Use Log/2027-01.md", [
        attribute.Use("2027-01-02T01:00Z", "T-001", "revisit cold task", "explicit")])
    check("2027-01-02", "hot")


def test_duplicate_register_id_keeps_first_entry_and_reports(repo, capsys):
    root, _ = repo
    path = root / "agent/Tasks.md"
    data, body = fm.read(path)
    duplicate = Task("T-001", "Discard duplicate", "- **Status:** DUPLICATE_BODY\n", {"uses": DAY})
    fm.write(path, data, body + "\n" + duplicate.render())
    report = []
    registered, archived = load_tasks(Config.load(root), report)
    assert len(registered) == 1 and not archived
    assert registered[0].title == "Sample"
    assert any("T-001 listed twice" in line for line in report)
    nightly.main(nightly_argv(root, DAY, "--write"))
    register = fm.read(path)[1]
    assert len(re.findall(r"^### T-001 —", register, re.M)) == 1
    assert "ORIGINAL_FULL_BODY" in register and "DUPLICATE_BODY" not in register
    assert "task: T-001 duplicate register entry removed" in capsys.readouterr().out
    registered, archived = load_tasks(Config.load(root))
    assert len(registered + archived) == 1


def test_use_log_ignores_junk_ineligible_rows_and_preserves_inferred_uses(repo):
    root, _ = repo
    fm.write(root / "Hub.md", {"uses": [], "uses_inferred": []}, "# Hub\n")
    ignored_paths = ["agent/Dreams/2026-10-04.md", "Conversations/2026-10-04.md",
                     "agent/Research/Record.md"]
    for rel in ignored_paths:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# Immutable fixture\r\nLeave every byte alone.\r\n")
    immutable = {rel: (root / rel).read_bytes() for rel in ignored_paths}
    rows = [
        "# Use log — 2026-10",
        "timestamp | target | reason | explicit/inferred/live",
        "Ordinary prose | Hub.md | ignored | explicit",
        'USE-HOLD-v1 {"target":"Hub.md","date":"2026-10-02"}',
        "2026-10-01T01:00Z | Hub.md | pending | live | unconfirmed",
        "2026-10-06T01:00Z | Hub.md | future | explicit",
        "2026-10-02T01:00Z | Hub.md | inferred reliance | inferred",
        "2026-10-03T01:00Z | Hub.md | confirmed reliance | live | confirmed",
        "2026-10-04T01:00Z | Hub.md | direct reliance | explicit",
        "2026-10-04T02:00Z | Hub.md | same day again | explicit",
        "2026-10-02T01:00Z | T-001 | inferred task reliance | inferred",
        "2026-10-01T01:00Z | T-001 | pending task | live | unconfirmed",
        "2026-10-06T01:00Z | T-001 | future task | explicit",
        "2026-10-05T01:00Z | MISS | no record | explicit",
        "2026-10-05T01:00Z | TR-001 | mentioned trigger | explicit",
        "2026-10-05T01:00Z | Missing.md | absent note | explicit",
    ] + [f"2026-10-05T01:00Z | {rel} | excluded target | explicit" for rel in ignored_paths]
    log = root / "agent/Use Log/2026-10.md"
    log.parent.mkdir()
    log.write_text("\n".join(rows) + "\n")
    assert len(attribute.read_log(log)) == len(rows) - 4
    nightly.main(nightly_argv(root, DAY, "--write"))
    data, _ = fm.read(root / "Hub.md")
    assert data["uses"] == ["2026-10-04", "2026-10-03"]
    assert data["uses_inferred"] == ["2026-10-02"]
    task = load_tasks(Config.load(root))[0][0]
    assert task.uses == [DAY]
    assert task.uses_inferred == ["2026-10-02"]
    assert {rel: (root / rel).read_bytes() for rel in ignored_paths} == immutable
    # Re-reading all rows on a later night adds eligible dates once, without a cursor.
    log.write_text(log.read_text().replace("live | unconfirmed", "live | confirmed"))
    nightly.main(nightly_argv(root, "2026-10-06", "--write"))
    data, _ = fm.read(root / "Hub.md")
    assert data["uses"] == ["2026-10-06", "2026-10-04", "2026-10-03", "2026-10-01"]
    assert data["uses_inferred"] == ["2026-10-02"]
    assert load_tasks(Config.load(root))[0][0].uses == ["2026-10-06", DAY, "2026-10-01"]


def test_push_conflict_exits_without_pushing_or_leaving_rebase(night_vault, tmp_path):
    root, remote = night_vault
    base = git(remote, "rev-parse", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", str(remote), str(other))
    git(other, "config", "user.email", "other@example.invalid")
    git(other, "config", "user.name", "Other fixture")
    # Both sides replace the same original task, forcing a real content conflict.
    path = other / "memory/agent/Tasks.md"
    path.write_text(path.read_text().replace("ORIGINAL_FULL_BODY", "REMOTE_CONFLICTING_ACTION"))
    git(other, "add", "memory/agent/Tasks.md")
    git(other, "commit", "-m", "divergent remote task edit")
    git(other, "push", "origin", "main")
    remote_tip = git(remote, "rev-parse", "main")

    result = cli(root, "--write", "--commit", "--push")
    assert result.returncode != 0
    assert "pull --rebase conflicted" in result.stderr
    assert "nothing was pushed" in result.stderr
    assert git(remote, "rev-parse", "main") == remote_tip
    assert git(root.parent, "rev-parse", "HEAD^") == base
    assert git(root.parent, "rev-parse", "HEAD") != remote_tip
    assert "ship the complete fixture" in (root / "agent/Tasks.md").read_text()
    git_dir = Path(git(root.parent, "rev-parse", "--absolute-git-dir"))
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "REBASE_HEAD").exists()
    assert git(root.parent, "status", "--porcelain") == ""
