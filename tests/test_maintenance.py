"""Engine CLI commands remain usable over SSH without an agent environment."""
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from brain.config import Config
from conftest import snapshot


@pytest.fixture
def maintenance(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_HOME", raising=False)
    (tmp_path / "brain.toml").write_text('[brain]\nvault = "memory"\n')
    (tmp_path / "memory/Runtime").mkdir(parents=True)
    (tmp_path / "memory/Runtime/schedule.toml").write_text('[routines]\n')
    return Config.load(cwd=tmp_path)


def run(config, *args):
    return subprocess.run([sys.executable, "-m", "brain.cli", *args],
                          cwd=config.repo, env=os.environ.copy(), text=True, capture_output=True)


def test_version_without_runtime(maintenance):
    result = run(maintenance, "version")
    assert result.returncode == 0
    assert result.stdout == version("agent-brain") + "\n"
    assert result.stderr == ""


@pytest.mark.parametrize("name", ["soul", "skills", "scripts", "records", "vault", "repo", "python", "schedule"])
def test_paths_without_runtime(maintenance, name):
    before = snapshot(maintenance.repo)
    result = run(maintenance, "paths", name)
    if name in ("scripts", "records"):
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "AGENT_HOME is not set\n"
    else:
        expected = {"soul": maintenance.soul_path, "skills": maintenance.skills_path,
                    "vault": maintenance.vault_path, "repo": maintenance.repo,
                    "python": Path(sys.executable),
                    "schedule": maintenance.vault_path / "Runtime/schedule.toml"}[name]
        assert result.returncode == 0
        assert result.stdout == str(expected.resolve()) + "\n"
        assert result.stderr == ""
    assert snapshot(maintenance.repo) == before


def test_validate_without_runtime(maintenance):
    before = snapshot(maintenance.repo)
    result = run(maintenance, "validate")
    assert result.returncode == 0
    assert result.stdout == "0 problems\n"
    assert result.stderr == ""
    assert snapshot(maintenance.repo) == before


def test_validate_without_runtime_still_reports_invalid_schedule(maintenance):
    (maintenance.vault_path / "Runtime/schedule.toml").write_text('[routines.bad]\nevery = "invalid"\n')
    result = run(maintenance, "validate")
    assert result.returncode == 1
    assert "every" in result.stdout and result.stdout.endswith("1 problems\n")
    assert result.stderr == ""
