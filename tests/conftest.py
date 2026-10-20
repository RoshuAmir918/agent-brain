"""Small synthetic vaults; Git fixtures never use the real checkout or remote."""
import hashlib
import os
import subprocess

import pytest

from brain import fm, nightly, triggers
from brain.config import Config
from brain.tasks import Task


def make_vault(root, uses="2026-09-20"):
    (root / "agent").mkdir(parents=True)
    (root.parent / "brain.toml").write_text('[brain]\nvault = "' + root.name + '"\n')
    notes = {
        "START.md": "# Start",
        "CURRENT.md": "# Current\n" + nightly.MARK_START + "\n" + nightly.MARK_END,
        "agent/Activity Log.md": "# Activity",
        "agent/Triggers.md": triggers.TEMPLATE,
        "agent/Tasks.md": "# Tasks\n## Active work\n" + Task(
            "T-001", "Sample",
            "- **Status:** open\n- **Next action:** ORIGINAL_FULL_BODY\n"
            "- **Evidence:** original evidence details\n",
            {"uses": uses, "tier": "hot", "status": "open"}).render(),
    }
    for rel, body in notes.items():
        fm.write(root / rel, {"tier": "hot", "pinned": True}, body + "\n")
    return root


@pytest.fixture
def vault(tmp_path):
    return make_vault(tmp_path / "memory")


@pytest.fixture
def template_config(tmp_path):
    """Explicit template defaults for parser and renderer unit tests."""
    return Config(tmp_path)


def nightly_argv(root, date, *extra):
    return ["nightly", "--vault", str(root), "--date", str(date), *extra]


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Ignore host identity, hooks, signing and Git routing; write config locally.
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "repo"
    root = make_vault(work / "memory", uses="2026-10-05")
    git(work, "init", "-b", "main")
    git(work, "config", "user.email", "fixture@example.invalid")
    git(work, "config", "user.name", "Fixture")
    git(work, "add", "memory", "brain.toml")
    git(work, "commit", "-m", "fixture")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "--initial-branch=main")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "origin", "HEAD:main")
    return root, remote
