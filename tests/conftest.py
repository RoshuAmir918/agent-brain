"""Small synthetic vaults; Git fixtures never use the real checkout or remote."""
import hashlib
import os
import subprocess
import sys

import pytest

from brain import fm, nightly
from brain.config import Config
from brain.tasks import Task


@pytest.fixture(autouse=True)
def isolated_cli_env(monkeypatch):
    monkeypatch.setenv("PATH", os.path.dirname(sys.executable), prepend=os.pathsep)
    for name in ("DEMIURGE_CONVERSATION", "DEMIURGE_TASK", "DEMIURGE_WORKERS", "BRAIN_SPEAKER", "AGENT_HOME"):
        monkeypatch.delenv(name, raising=False)


def make_vault(root, uses="2026-09-20"):
    (root / "agent").mkdir(parents=True)
    (root.parent / "brain.toml").write_text('[brain]\nvault = "' + root.name + '"\n')
    notes = {
        "START.md": "# Start",
        "CURRENT.md": "# Current\n" + nightly.MARK_START + "\n" + nightly.MARK_END,
        "agent/Activity Log.md": "# Activity",
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


@pytest.fixture(scope="session")
def fixture_lock_home(tmp_path_factory):
    return tmp_path_factory.mktemp("locks")


@pytest.fixture(autouse=True)
def fixture_locks(fixture_lock_home, monkeypatch):
    """Fixture writers share real flock locks, without writing to the user's home."""
    monkeypatch.setenv("HOME", str(fixture_lock_home))
    class LockPath(type(fixture_lock_home)):
        @classmethod
        def home(cls):
            return fixture_lock_home
    monkeypatch.setattr("brain.lock.Path", LockPath)


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


from brain import install as _install

START_MIGRATION = _install.start_migration


@pytest.fixture(autouse=True)
def migration_runs(monkeypatch):
    """No test schedules a real Runtime run; each records what it would have started."""
    started = []
    monkeypatch.setattr(_install, "start_migration", lambda config: started.append(config) or True)
    return started


def message(role, text, at="2026-10-20T07:01:00Z", *, said=None):
    entry = {"at": at, "item": {"type": "message", "role": role, "text": text}}
    if said is not None:
        entry["said"] = said
    return entry


def tool(name, arguments, at="2026-10-20T07:01:00Z"):
    import json
    return {"at": at, "item": {"type": "tool_call", "id": "call", "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


def harness_session(sid, entries, *, platform="demiurge", owner=True, user_name=None):
    return {"id": sid, "day": "2026-10-20", "speaker": {"platform": platform, **({"userName": user_name} if user_name else {})},
            "startedAt": "2026-10-20T07:00:00Z", "timezone": "America/Los_Angeles", "system": "",
            "sessionBlock": "", "owner": owner, "peopleSeen": [], "entries": entries,
            "recorded": 0, "usage": {"inputTokens": 0, "outputTokens": 0, "cachedInputTokens": 0, "reasoningTokens": 0}}


def write_shards(directory, *records):
    import json
    folder = directory / "jsonl"
    folder.mkdir(parents=True, exist_ok=True)
    for record in records:
        (folder / (record["id"] + ".json")).write_text(json.dumps(record) + "\n")
