"""A role's actions resolved against the connections the runtime lists."""
import json
import subprocess

import pytest

from brain import cli, roles
from brain.config import Config


@pytest.fixture
def org(vault, tmp_path, monkeypatch):
    toml = vault.parent / "brain.toml"
    toml.write_text(toml.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    config = Config.load(vault)
    roles.add(config, "Engineering", "engineering")
    return config


LISTED = [{"kind": "github", "account": "acme", "folder": "acme", "write": False, "sources": [],
           "implements": [{"action": "git.push_branch", "use": "push", "ready": False},
                          {"action": "git.open_draft_pr", "use": "open_draft_pr", "ready": False}]},
          {"kind": "google", "account": "x@y.z", "folder": "x@y.z", "write": True, "sources": []}]


def test_each_action_is_ready_switched_off_or_missing(org):
    resolved = {entry["action"]: entry for entry in roles.actions(org, "Engineering", LISTED)}
    assert resolved["git.push_branch"] == {"action": "git.push_branch", "status": "switched off",
                                           "by": [{"connection": "github:acme", "use": "push", "ready": False}]}
    assert resolved["ci.run"]["status"] == "missing"
    ready = [dict(entry, implements=[dict(item, ready=True) for item in entry.get("implements", [])]) for entry in LISTED]
    assert roles.actions(org, "Engineering", ready)[0]["status"] == "ready"


def test_the_command_line_reads_the_runtimes_listing(org, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(LISTED), ""))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["roles", "actions", "Engineering", "--vault", str(org.vault_path)])
    out = capsys.readouterr().out
    assert stopped.value.code == 1
    assert "git.push_branch · switched off · use github:acme push (switched off)" in out
    assert "ci.run · missing · no connection does this" in out
