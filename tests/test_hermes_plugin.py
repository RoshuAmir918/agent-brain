import builtins
import importlib.util
import json
import subprocess
from types import SimpleNamespace

import pytest

from brain import hermes_plugin


@pytest.fixture
def provider_repo(tmp_path, monkeypatch):
    repo = tmp_path / "vault-repo"
    repo.mkdir()
    (repo / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("VAULT_REPO", str(repo))
    return repo


@pytest.fixture
def provider(provider_repo):
    instance = hermes_plugin.BrainMemoryProvider()
    instance.repo = provider_repo
    return instance


def test_plugin_imports_standalone_without_hermes_or_brain_imports(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "agent.memory_provider":
            raise ImportError("Hermes is not installed")
        if name == "brain" or name.startswith("brain."):
            pytest.fail("The standalone plugin must not import brain")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec = importlib.util.spec_from_file_location("standalone_brain_provider", hermes_plugin.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.MemoryProvider is object
    assert module.BrainMemoryProvider().name == "brain"


def test_initialize_calls_session_context_once_and_caches_output(provider_repo, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="This session is with Mira, the owner.\n")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider = hermes_plugin.BrainMemoryProvider()
    provider.initialize("session-1", cwd=str(provider_repo), platform="slack", user_id="U_OWNER",
                        user_id_alt="12345", user_name="Mira", chat_id="C_CHAT", hermes_home="ignored")
    assert calls == [([
        "brain", "session-context", "--platform", "slack", "--user-id", "U_OWNER",
        "--user-id-alt", "12345", "--user-name", "Mira", "--chat-id", "C_CHAT",
    ], {"cwd": provider_repo, "capture_output": True, "text": True, "check": True, "timeout": 5})]
    assert (provider.platform, provider.user_id, provider.user_id_alt, provider.user_name, provider.chat_id,
            provider.session_id, provider.repo) == ("slack", "U_OWNER", "12345", "Mira", "C_CHAT", "session-1", provider_repo)
    assert provider.system_prompt_block() == "This session is with Mira, the owner."
    assert provider.system_prompt_block() == "This session is with Mira, the owner."
    assert len(calls) == 1


def test_reinitialization_drops_old_identity_and_context(provider, monkeypatch):
    monkeypatch.setattr(hermes_plugin.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="owner"))
    provider.initialize("first", platform="cli", user_id="U_OWNER")

    def fail(*args, **kwargs):
        raise FileNotFoundError("brain is missing")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", fail)
    provider.initialize("second", platform="slack", user_id="NOPE")
    assert provider.system_prompt_block() == ""
    assert provider.user_id == "NOPE" and provider.user_id_alt == ""


def test_repository_discovery_prefers_cwd_then_environment_then_home(provider_repo, tmp_path, monkeypatch):
    cwd_repo = tmp_path / "cwd-repo"
    cwd_repo.mkdir()
    (cwd_repo / "brain.toml").write_text("[brain]\n")
    nested = cwd_repo / "memory/people"
    nested.mkdir(parents=True)
    home_repo = tmp_path / "brain"
    home_repo.mkdir()
    (home_repo / "brain.toml").write_text("[brain]\n")
    assert hermes_plugin.find_repo(str(nested)) == cwd_repo
    assert hermes_plugin.find_repo(str(tmp_path / "not-a-repo")) == provider_repo
    monkeypatch.delenv("VAULT_REPO")
    assert hermes_plugin.find_repo() == home_repo
    (home_repo / "brain.toml").unlink()
    assert hermes_plugin.find_repo() is None


def test_is_available_requires_config_and_successful_help(provider_repo, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider = hermes_plugin.BrainMemoryProvider()
    assert provider.is_available()
    assert calls[0][0] == ["brain", "--help"]
    assert calls[0][1]["cwd"] == provider_repo and calls[0][1]["timeout"] == 5
    (provider_repo / "brain.toml").unlink()
    assert not provider.is_available()
    assert len(calls) == 1


@pytest.mark.parametrize("query", ["", "the and for you", "gardens", "gardens and the"])
def test_prefetch_skips_fewer_than_two_non_stopword_tokens(provider, monkeypatch, query):
    def unexpected_call(*args, **kwargs):
        pytest.fail("Short and stopword-only queries must not start a subprocess")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", unexpected_call)
    assert provider.prefetch(query) == ""


def test_prefetch_formats_candidates_and_truncates_the_query(provider, monkeypatch):
    calls = []
    candidates = [{"path": "memory/Harbor.md", "tier": "warm", "score": 3.0, "reason": "index line: harbor"},
                  {"path": "memory/Deploy.md", "tier": "hot", "reason": "title/path: deployment"}]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(candidates))

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    query = "harbor deployment " + "x" * 400
    assert provider.prefetch(query, session_id="session-1") == (
        "Vault notes that match this message (names and index lines; open what you need):\n"
        "- memory/Harbor.md (warm): index line: harbor\n"
        "- memory/Deploy.md (hot): title/path: deployment")
    assert calls[0][0] == ["brain", "retrieve", "--json", "--limit", "6", "--", query[:300]]
    assert calls[0][1] == {"cwd": provider.repo, "capture_output": True, "text": True, "check": True, "timeout": 5}


def test_prefetch_caps_the_whole_block_at_1500_characters(provider, monkeypatch):
    candidate = {"path": "memory/Long.md", "tier": "cold", "reason": "long " * 400}
    monkeypatch.setattr(hermes_plugin.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps([candidate])))
    block = provider.prefetch("long query")
    assert block.startswith(hermes_plugin.RECALL_HEADER)
    assert len(block) == 1500


@pytest.mark.parametrize("output", ["[]", "{}", "not JSON", "[{}]", '[{"path": 1, "tier": "warm", "reason": "x"}]'])
def test_prefetch_returns_empty_for_no_candidates_or_invalid_output(provider, monkeypatch, output):
    monkeypatch.setattr(hermes_plugin.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))
    assert provider.prefetch("harbor deployment") == ""


@pytest.mark.parametrize("failure", [
    FileNotFoundError("brain not found"), subprocess.CalledProcessError(1, ["brain"]),
    subprocess.TimeoutExpired(["brain"], 5), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
])
def test_subprocess_failures_never_escape_to_hermes(provider, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(hermes_plugin.subprocess, "run", fail)
    assert not provider.is_available()
    provider.initialize("session-1", platform="slack", user_id="NOPE")
    assert provider.system_prompt_block() == ""
    assert provider.prefetch("harbor deployment") == ""


def test_provider_without_a_repository_is_unavailable_and_injects_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VAULT_REPO", raising=False)
    provider = hermes_plugin.BrainMemoryProvider()
    assert not provider.is_available()
    provider.initialize("session-1", platform="slack", user_id="NOPE")
    assert provider.system_prompt_block() == ""
    assert provider.prefetch("harbor deployment") == ""


def test_other_hooks_are_noops_and_registration_supplies_the_provider(provider):
    assert provider.get_tool_schemas() == []
    assert provider.get_config_schema() == []
    assert provider.handle_tool_call("unknown", {}) is None
    assert provider.save_config({}, "/unused") is None
    assert provider.sync_turn("user", "assistant", session_id="session-1", messages=[]) is None
    assert provider.on_session_end([]) is None
    assert provider.shutdown() is None
    registered = []
    hermes_plugin.register(SimpleNamespace(register_memory_provider=registered.append))
    assert len(registered) == 1
    assert registered[0].name == "brain"
