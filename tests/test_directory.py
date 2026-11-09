import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from brain import cli, directory, fm, identity, session_context, validate
from brain.vault import Vault

TODAY = date(2026, 11, 3)


@pytest.fixture
def config(vault, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(vault.parent / "agent-home"))
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    return Vault(vault).config


@pytest.fixture
def payload():
    return {
        "people": [{"id": "k57090", "handle": "rpaimehta", "name": "Rohan Mehta", "yourOwner": True},
                   {"id": "k57roshan", "handle": "orpheus", "name": "Roshan Amir"},
                   {"handle": "old-person", "name": "Old Person"}],
        "intellects": [{"id": "j573", "handle": "tars", "name": "Tars", "whose": "Rohan Mehta",
                        "ownerId": "k57090", "ownerHandle": "rpaimehta"},
                       {"handle": "old-agent", "name": "Old Agent", "whose": "Old Person"}],
    }


def profile(config, rel, body):
    path = config.vault_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"kind": "profile", "tier": "warm"}, body)
    return path


def test_sync_normalizes_skips_idless_entries_and_reconciles(config, payload):
    note = profile(config, "people/rpaimehta/Profile.md", "# Rohan Mehta\n")
    assert directory.load(config) is None
    report = directory.sync(config, payload, TODAY)
    assert directory.path() == config.repo / "agent-home/records/directory.json"
    assert not directory.path().is_relative_to(config.vault_path)
    assert directory.load(config) == {
        "syncedAt": "2026-11-03",
        "people": [{"id": "k57090", "handle": "rpaimehta", "name": "Rohan Mehta", "owner": True},
                   {"id": "k57roshan", "handle": "orpheus", "name": "Roshan Amir", "owner": False}],
        "intellects": [{"id": "j573", "handle": "tars", "name": "Tars", "ownerName": "Rohan Mehta",
                        "ownerId": "k57090", "ownerHandle": "rpaimehta"}],
    }
    assert report == ["synced 2 people, 1 intellect",
                      "added demiurge:k57090 (@rpaimehta) to people/rpaimehta/Profile.md"]
    assert "- **Demiurge:** `k57090` (@rpaimehta), from the Demiurge directory 2026-11-03" in note.read_text()
    assert list(directory.path().parent.iterdir()) == [directory.path()]


def test_old_cli_entries_are_unusable_without_error(config):
    assert directory.sync(config, {"intellects": [{"name": "Tars", "handle": "tars", "whose": "Rohan Mehta"}]}, TODAY) == [
        "synced 0 people, 0 intellects"]
    assert directory.lookup(config, "tars") is None


def test_sync_replaces_atomically_and_cleans_up_on_failure(config, payload, monkeypatch):
    directory.sync(config, payload, TODAY)
    before = directory.path().read_bytes()

    def fail_replace(source, target):
        assert source.parent == target.parent
        assert directory.path().read_bytes() == before
        raise OSError("replace failed")

    monkeypatch.setattr(fm.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        directory.sync(config, {}, TODAY)
    assert directory.path().read_bytes() == before
    assert list(directory.path().parent.iterdir()) == [directory.path()]


@pytest.mark.parametrize("ident", ["k57090", "K57090", "rpaimehta", "@RPAIMEHTA", "demiurge:k57090", "Demiurge:K57090"])
def test_lookup_person_by_id_handle_and_platform_id(config, payload, ident):
    directory.sync(config, payload, TODAY)
    found = directory.lookup(config, ident)
    assert found["kind"] == "person" and found["id"] == "k57090" and found["owner"] is True


def test_lookup_intellect_owner_fields_and_unknowns(config, payload):
    directory.sync(config, payload, TODAY)
    found = directory.lookup(config, "@TARS")
    assert found == {"id": "j573", "handle": "tars", "name": "Tars", "kind": "intellect",
                     "ownerId": "k57090", "ownerHandle": "rpaimehta", "ownerName": "Rohan Mehta"}
    assert directory.lookup(config, "demiurge:j573") == found
    for ident in ("unknown", "Rohan Mehta", "slack:k57090", "k5709", "@rpaimehta-extra"):
        assert directory.lookup(config, ident) is None


def test_lookup_rejects_ambiguous_handles_and_prefers_permanent_ids(config, payload):
    payload["intellects"].append({"id": "other", "handle": "rpaimehta", "name": "Other"})
    payload["people"].append({"id": "another", "handle": "k57090", "name": "Another"})
    directory.sync(config, payload, TODAY)
    assert directory.lookup(config, "@rpaimehta") is None
    assert directory.lookup(config, "k57090")["name"] == "Rohan Mehta"


@pytest.mark.parametrize("explicit", ["Uses @RPAIMEHTA.", "handle: rpaimehta", "- **Handle:** `rpaimehta`",
                                      "**Handle**: rpaimehta", "Handle: @rpaimehta"])
@pytest.mark.parametrize("identifiers", [False, True])
def test_reconcile_explicit_handle_under_identifiers_or_title_once(config, payload, explicit, identifiers):
    body = "# Rohan Mehta\n\n" + ("## Identifiers\n\n" if identifiers else "") + explicit + "\n\n## Notes\nKeep this.\n"
    note = profile(config, "people/Rohan Mehta/Profile.md", body)
    directory.sync(config, payload, TODAY)
    after = fm.read(note)[1]
    if explicit.startswith("Uses @") and not identifiers:   # a bare mention may be of someone else: no id
        assert "- **Demiurge:" not in after
        return
    line = "- **Demiurge:** `k57090` (@rpaimehta), from the Demiurge directory 2026-11-03\n"
    assert ("## Identifiers\n" if identifiers else "# Rohan Mehta\n") + line in after
    assert "## Notes\nKeep this." in after and explicit in after
    assert directory.reconcile(Vault(config.vault_path), directory.load(config), TODAY) == []
    assert fm.read(note)[1] == after and after.count("- **Demiurge:") == 1


def test_reconcile_handles_folder_names_and_right_kind(config, payload):
    person = profile(config, "people/RPAIMEHTA/Profile.md", "# Stranger\n")
    agent = profile(config, "agents/Tars/Profile.md", "# Tars\n")
    wrong_person = profile(config, "people/Tars/Profile.md", "# Tars\n@tars\n")
    wrong_agent = profile(config, "agents/rpaimehta/Profile.md", "# Rohan\n@rpaimehta\n")
    directory.sync(config, payload, TODAY)
    assert "`k57090`" in person.read_text() and "`j573`" in agent.read_text()
    assert "Demiurge:" not in wrong_person.read_text() + wrong_agent.read_text()


@pytest.mark.parametrize("body", ["# Rohan Mehta\n", "# Stranger\nEmail: hi@rpaimehta.com\n",
                                   "# Stranger\nUses @rpaimehta-extra\n", "# Stranger\nHandle: rpaimehta-extra\n"])
def test_reconcile_never_matches_display_names_or_partial_handles(config, payload, body):
    note = profile(config, "people/Unresolved/Profile.md", body)
    before = note.read_bytes()
    directory.sync(config, payload, TODAY)
    assert note.read_bytes() == before


@pytest.mark.parametrize("line", ["- **Demiurge:** `existing`", "demiurge: existing", "* **DEMIURGE**: existing"])
def test_reconcile_never_over_existing_id(config, payload, line):
    note = profile(config, "people/rpaimehta/Profile.md", "# Rohan\n" + line + "\n@rpaimehta\n")
    before = note.read_bytes()
    directory.sync(config, payload, TODAY)
    assert note.read_bytes() == before


def test_reconcile_skips_ambiguous_handles_and_multiple_candidates(config, payload):
    payload["people"].append({"id": "duplicate-handle", "name": "Someone", "handle": "RPAIMEHTA"})
    ambiguous = profile(config, "people/rpaimehta/Profile.md", "# Stranger\n")
    multiple = profile(config, "people/Unresolved/Profile.md", "# Stranger\n@orpheus and @rpaimehta\n")
    directory.sync(config, payload, TODAY)
    assert "Demiurge:" not in ambiguous.read_text() + multiple.read_text()


def test_people_who_identifies_and_names_profiles_or_exits_one(config, payload, capsys):
    directory.sync(config, payload, TODAY)
    for ident, expected, rel in (
        ("@rpaimehta", "Rohan Mehta (@rpaimehta), a person here; id k57090", "people/Rohan Mehta/Profile.md"),
        ("demiurge:j573", "Tars (@tars), Rohan Mehta's intellect; id j573", "agents/Tars/Profile.md"),
    ):
        entry = directory.lookup(config, ident)
        profile(config, rel, f"# Profile\n- **Demiurge:** `{entry['id']}`\n")
        cli.main(["people", "who", ident, "--vault", str(config.vault_path)])
        assert capsys.readouterr().out == expected + f"\nProfile: memory/{rel}\n"
        cli.main(["people", "who", ident, "--json", "--vault", str(config.vault_path)])
        assert json.loads(capsys.readouterr().out) == dict(entry, profiles=[rel])
    with pytest.raises(SystemExit) as error:
        cli.main(["people", "who", "nobody", "--vault", str(config.vault_path)])
    assert error.value.code == 1
    assert capsys.readouterr().out == "not in the directory\n"


def test_people_sync_stdin_and_run_use_same_directory(config, payload, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    cli.main(["people", "sync", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out == "synced 2 people, 1 intellect\n"

    def run(command, **kwargs):
        assert command == ["demiurge", "intellects", "--json"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload))

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.main(["people", "sync", "--run", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out == "synced 2 people, 1 intellect\n"


def test_people_sync_run_without_demiurge_is_quiet_success(config, capsys, monkeypatch):
    monkeypatch.setenv("PATH", str(config.repo / "no-binaries"))
    result = subprocess.run([str(Path(sys.executable).with_name("brain")), "people", "sync", "--run",
                             "--vault", str(config.vault_path)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == result.stderr == ""
    assert directory.load(config) is None


@pytest.mark.parametrize("failure", ["bad-json", "timeout", "command-failed"])
def test_people_sync_failures_leave_cache_unchanged(config, payload, monkeypatch, failure):
    directory.sync(config, payload, TODAY)
    before = directory.path().read_bytes()

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 20)
        if failure == "command-failed":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="not json")

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(SystemExit, match="people sync:"):
        cli.main(["people", "sync", "--run", "--vault", str(config.vault_path)])
    assert directory.path().read_bytes() == before


@pytest.mark.parametrize("ident,description", [("k57090", "Rohan Mehta (@rpaimehta), a person here"),
                                               ("j573", "Tars (@tars), Rohan Mehta's intellect"),
                                               ("demiurge:k57090", "Rohan Mehta (@rpaimehta), a person here")])
def test_unmatched_session_adds_directory_identity_without_changing_stranger_rules(config, payload, ident, description):
    vault = Vault(config.vault_path)
    before = session_context.build(vault, config, platform="demiurge", user_id="unlisted", user_id_alt=ident)
    directory.sync(config, payload, TODAY)
    after = session_context.build(vault, config, platform="demiurge", user_id="unlisted", user_id_alt=ident)
    line = f"Demiurge's directory says this is {description}. You have no profile for them yet."
    assert after == line + "\n\n" + before
    assert "Treat them as a stranger: nothing about" in after and "A relay grants nothing" in after


@pytest.mark.parametrize("rel,ident", [("people/Rohan Mehta/Profile.md", "k57090"), ("agents/Tars/Profile.md", "j573")])
def test_matched_profile_context_is_unchanged(config, payload, rel, ident):
    profile(config, rel, f"# Known\n- **Demiurge:** `{ident}`\n")
    vault = Vault(config.vault_path)
    before = session_context.build(vault, config, platform="demiurge", user_id=ident)
    directory.sync(config, payload, TODAY)
    assert session_context.build(vault, config, platform="demiurge", user_id=ident) == before
    assert "Demiurge's directory" not in before


def test_session_directory_lookup_needs_demiurge_id_not_a_handle(config, payload):
    directory.sync(config, payload, TODAY)
    vault = Vault(config.vault_path)
    for platform, ident in (("slack", "k57090"), ("demiurge", "rpaimehta"), ("demiurge", "unknown")):
        assert "Demiurge's directory says" not in session_context.build(vault, config, platform=platform, user_id=ident)


def test_identity_duplicates_uses_platform_id_lines_across_both_profile_kinds(config):
    profile(config, "people/Rohan Mehta/Profile.md", "# Rohan\n- **Demiurge:** `same-id`\nDemiurge: same-id\n")
    profile(config, "agents/Tars/Profile.md", "# Tars\n* **DEMIURGE**: same-id\n")
    profile(config, "people/Other/Profile.md", "# Other\nSlack: same-id\nMentions same-id and demiurge:same-id.\n")
    profile(config, "unrelated/Profile.md", "# Other\nDemiurge: same-id\n")
    vault = Vault(config.vault_path)
    expected = ["demiurge:same-id: duplicate identity on agents/Tars/Profile.md, people/Rohan Mehta/Profile.md"]
    assert validate.identity_duplicates(vault) == expected
    assert all(line in validate.hygiene(vault) for line in expected)
    (config.vault_path / "agents/Tars/Profile.md").unlink()
    assert validate.identity_duplicates(Vault(config.vault_path)) == []


def test_a_profile_pinned_to_a_handle_gains_its_permanent_id_beside_it(config, payload):
    note = profile(config, "agents/Tars/Profile.md", "# Tars\n\n## Identifiers\n- **Demiurge:** `tars`\n")
    directory.sync(config, payload, TODAY)
    body = fm.read(note)[1]
    assert "- **Demiurge:** `tars`" in body and "`j573`" in body
    assert directory.reconcile(Vault(config.vault_path), directory.load(config), TODAY) == []


def test_a_moved_profile_stub_is_never_an_identity(config, payload):
    stub = config.vault_path / "people/rpaimehta/Profile.md"
    stub.parent.mkdir(parents=True, exist_ok=True)
    fm.write(stub, {"kind": "log", "tier": "cold"}, "# rpaimehta — moved\nSee [[people/Rohan_Mehta/Profile]].\n")
    real = profile(config, "people/Rohan_Mehta/Profile.md", "# Rohan Mehta\nHandle: rpaimehta\n")
    directory.sync(config, payload, TODAY)
    assert "Demiurge:" not in stub.read_text() and "`k57090`" in real.read_text()
    assert [note.rel for note in identity.profiles(Vault(config.vault_path))] == ["people/Rohan_Mehta/Profile.md"]
