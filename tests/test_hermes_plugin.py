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


def test_is_available_wherever_the_cli_runs_whatever_the_vault(provider_repo, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    (provider_repo / "brain.toml").unlink()
    assert hermes_plugin.BrainMemoryProvider().is_available()
    assert calls == [["brain", "--help"]]

    def missing(command, **kwargs):
        raise FileNotFoundError("brain")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", missing)
    assert not hermes_plugin.BrainMemoryProvider().is_available()


@pytest.mark.parametrize("query", ["", "the and for you", "gardens", "gardens and the"])
def test_prefetch_skips_fewer_than_two_non_stopword_tokens(provider, monkeypatch, query):
    calls = []

    def run(command, **kwargs):
        if command[1] == "retrieve":
            pytest.fail("Short and stopword-only queries must not start a retrieve")
        calls.append(command)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider._context = "This session is with Mira, the owner."
    assert provider.prefetch(query) == ""
    assert [command[1] for command in calls] == ([] if query in ("", "the and for you") else ["people-mentioned"])


def test_people_pointer_names_each_person_once_per_session(provider, monkeypatch):
    calls = []
    pointer = ("People named in this message, from their profiles:\n"
               "- Hope Dobbelaere: Roshan's partner. (memory/people/Hope_Dobbelaere)\n"
               "- Faisal Shaikh: close friend in Madison. (memory/people/Faisal_Shaikh)")

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=pointer if command[1] == "people-mentioned" else "[]")

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider._context = "This session is with Mira, the owner."
    assert provider.prefetch("hope texted me about faisal") == pointer
    assert calls[0] == ["brain", "people-mentioned", "--form", "card", "--", "hope texted me about faisal"]
    assert provider.prefetch("hope again please") == ""   # both already named this session
    provider._people_seen.clear()   # what initialize does for the next session
    assert provider.prefetch("hope texted me about faisal") == pointer


def test_people_pointer_never_runs_in_anyone_elses_session(provider, monkeypatch):
    calls = []
    monkeypatch.setattr(hermes_plugin.subprocess, "run",
                        lambda command, **kwargs: (calls.append(command), SimpleNamespace(stdout="[]"))[1])
    for context in ("", "This session is with Alice (slack U_ALICE). Their profile and the disclosure rules that apply follow.",
                    "This session is with an unknown person (demiurge k57who, name shown as Kevin). No profile.",
                    "This session is with Nameless (demiurge k57n), an intellect registered under memory/agents/Nameless/Profile.md"):
        provider._context = context
        provider.prefetch("is hope around?")
    assert all(command[1] == "retrieve" for command in calls)
    provider._context = "Engine migration pending; see the file.\n\nThis session is with Mira, the owner."
    provider.prefetch("is hope around?")
    assert calls[-2][1] == "people-mentioned"


def test_initialize_resets_the_people_seen_for_each_session(provider, monkeypatch):
    monkeypatch.setattr(hermes_plugin.subprocess, "run", lambda command, **kwargs: SimpleNamespace(stdout=""))
    provider._people_seen = {"Hope Dobbelaere"}
    provider.initialize("session-2", platform="demiurge", user_id="mira")
    assert provider._people_seen == set()


def test_people_pointer_form_from_toml_env_and_off(provider, prefetch_env):
    calls = []
    prefetch_env.setattr(hermes_plugin.subprocess, "run",
                         lambda command, **kwargs: (calls.append(command), SimpleNamespace(stdout=""))[1])
    provider._context = "This session is with Mira, the owner."
    (provider.repo / "brain.toml").write_text('[brain]\n\n[context]\npeople = "line"\n')
    provider.prefetch("hope texted me")
    assert calls[0][1:4] == ["people-mentioned", "--form", "line"]
    prefetch_env.setenv("BRAIN_PEOPLE", "off")
    calls.clear()
    provider.prefetch("hope texted me")
    assert [command[1] for command in calls] == ["retrieve"]
    prefetch_env.setenv("BRAIN_PEOPLE", "faces")
    assert provider.prefetch("hope texted me") == ""


def test_prefetch_formats_candidates_and_truncates_the_query(provider, monkeypatch):
    calls = []
    candidates = [{"path": "memory/Harbor.md", "tier": "warm", "score": 3.0, "reason": "index line: harbor"},
                  {"path": "memory/Deploy.md", "tier": "hot", "reason": "title/path: deployment"}]

    def run(command, **kwargs):
        if command[1] == "people-mentioned":
            return SimpleNamespace(stdout="")
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(candidates))

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    query = "harbor deployment " + "x" * 400
    assert provider.prefetch(query, session_id="session-1") == (
        "Vault notes that match this message (names and index lines; open one only if what is loaded does not answer):\n"
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


def test_without_a_vault_anywhere_the_session_runs_without_the_brain_and_says_so(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VAULT_REPO", raising=False)
    provider = hermes_plugin.BrainMemoryProvider()
    provider.initialize("session-1", platform="slack", user_id="NOPE", hermes_home=str(tmp_path / "nohome"))
    assert provider.repo is None and "no vault found" in caplog.text
    assert provider.system_prompt_block() == ""
    assert provider.prefetch("harbor deployment") == ""


def test_the_profiles_terminal_cwd_finds_the_vault_without_vault_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VAULT_REPO", raising=False)
    vault = tmp_path / "amicus"
    vault.mkdir()
    (vault / "brain.toml").write_text("[brain]\n", encoding="utf-8")
    home = tmp_path / ".hermes" / "profiles" / "amicus"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(f"model: x\nterminal:\n  backend: local\n  cwd: \"{vault}\"  # the vault\n", encoding="utf-8")
    monkeypatch.setattr(hermes_plugin.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    provider = hermes_plugin.BrainMemoryProvider()
    provider.initialize("session-1", platform="slack", user_id="U1", hermes_home=str(home))
    assert provider.repo == vault.resolve()


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


# ── after the session ────────────────────────────────────────────────────────

def chat(*pairs):
    return [message for user, assistant in pairs
            for message in ({"role": "user", "content": user}, {"role": "assistant", "content": assistant})]


class FakeLlm:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(parsed=result if isinstance(result, dict) else None,
                               text=result if isinstance(result, str) else json.dumps(result))


open_asks = [""]


@pytest.fixture
def writes(provider, monkeypatch):
    calls = []
    open_asks[0] = ""

    def run(command, **kwargs):
        if command[1:] == ["needs-you"]:
            return SimpleNamespace(stdout=open_asks[0], returncode=0)   # a read, not a write
        calls.append((command[1:], json.loads(kwargs["input"]) if kwargs.get("input", "").startswith("{") else kwargs.get("input")))
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider.session_id, provider.platform = "20261028_140500_abc", "demiurge"
    return calls


RECORD = {"headline": "Compared fares.", "outcome": "progress", "needsYou": "Book the 16th?", "next": None}


def test_a_quiet_conversation_becomes_one_journal_row_and_only_new_turns_are_recorded_again(provider, writes):
    provider.llm = FakeLlm(RECORD, {**RECORD, "headline": "Booked it."})
    provider.sync_turn("a", "b", messages=chat(("Find fares", "Found two")))
    provider._cancel()
    provider._settled(provider._generation)
    assert writes == [(["journal", "append", "--platform", "demiurge"], RECORD)]
    provider.on_session_end(None)
    assert len(writes) == 1   # nothing new since the row
    provider.sync_turn("c", "d", messages=chat(("Find fares", "Found two"), ("Book it", "Booked")))
    provider.on_session_end(None)
    assert writes[-1][1]["headline"] == "Booked it."
    assert "Book it" in provider.llm.calls[-1]["input"][0]["text"] and "Find fares" not in provider.llm.calls[-1]["input"][0]["text"]


def test_a_newer_turn_supersedes_a_pending_settle(provider, writes, monkeypatch):
    started = []
    monkeypatch.setattr(hermes_plugin.threading, "Timer", lambda delay, fn, args: SimpleNamespace(
        daemon=False, start=lambda: started.append((delay, args)), cancel=lambda: None))
    provider.llm = FakeLlm(RECORD)
    provider.sync_turn("a", "b", messages=chat(("one", "two")))
    provider.sync_turn("c", "d", messages=chat(("one", "two"), ("three", "four")))
    assert [delay for delay, _ in started] == [hermes_plugin.SETTLE_SECONDS] * 2
    provider._settled(started[0][1][0])
    assert writes == []
    provider._settled(started[1][1][0])
    assert len(writes) == 1


@pytest.mark.parametrize("messages", [[], chat(("hi", ""))])
def test_too_little_said_writes_nothing_and_asks_nothing(provider, writes, messages):
    provider.llm = FakeLlm()
    provider.sync_turn("a", "b", messages=messages)
    provider.on_session_end(None)
    assert writes == [] and provider.llm.calls == []


def test_nothing_outcome_writes_no_journal_row(provider, writes):
    provider.llm = FakeLlm({"headline": "Small talk.", "outcome": "nothing"})
    provider.on_session_end(chat(("hi", "hello")))
    assert writes == []


def test_invalid_json_is_retried_then_falls_back_to_its_first_line(provider, writes):
    provider.llm = FakeLlm(RuntimeError("timeout"), "I compared the fares\nand more")
    provider.on_session_end(chat(("fares?", "compared")))
    assert writes == [(["journal", "append", "--platform", "demiurge"],
                       {"headline": "I compared the fares and more", "outcome": "progress"})]


def test_without_a_host_llm_nothing_is_written(provider, writes):
    provider.llm = None
    provider.on_session_end(chat(("fares?", "compared")))
    assert writes == []


def test_a_cron_run_leaves_one_ledger_row_named_by_its_skill(provider, writes, monkeypatch):
    jobs = SimpleNamespace(get_job=lambda job_id: {"skills": ["brain-recall", "brain-watch"]} if job_id == "j1" else None)
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs", jobs)
    monkeypatch.setitem(__import__("sys").modules, "cron", SimpleNamespace(jobs=jobs))
    provider.session_id, provider._cron_job = "cron_j1_20261028_090000", "j1"
    provider.llm = FakeLlm({"headline": "Mail checked; nothing new.", "outcome": "nothing", "next": None})
    provider.sync_turn("run", "done", messages=chat(("run watch", "checked mail")))
    assert provider._timer is None   # a routine is recorded when it is torn down, not when it goes quiet
    provider.on_session_end(None)
    assert writes == []
    provider.shutdown()
    provider.shutdown()
    assert writes == [(["ledger", "append", "--json"],
                       {"skill": "brain-watch", "outcome": "nothing", "account": "Mail checked; nothing new.", "next": ""})]
    assert provider.llm.calls[0]["instructions"] == hermes_plugin.ROUTINE


def test_a_cron_run_without_a_known_job_writes_nothing(provider, writes, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs", SimpleNamespace(get_job=lambda job_id: None))
    provider.session_id, provider._cron_job = "cron_gone_20261028_090000", "gone"
    provider.llm = FakeLlm()
    provider.on_session_end(chat(("run", "done")))
    provider.shutdown()
    assert writes == [] and provider.llm.calls == []


def test_register_hands_the_host_llm_to_the_provider():
    registered = []
    hermes_plugin.register(SimpleNamespace(llm="host-llm", register_memory_provider=registered.append))
    assert registered[0].llm == "host-llm"


# ── what the reviews found ───────────────────────────────────────────────────

class Collector:
    """The shape Hermes gives a memory provider: only register_* calls reach the real context."""
    def __init__(self):
        self.registered = []

    def register_memory_provider(self, provider):
        self.registered.append(provider)

    def __getattr__(self, attr):
        raise AttributeError(attr)


def test_under_the_real_collector_the_provider_builds_the_host_model_itself(provider, writes, monkeypatch):
    built = []
    fake = SimpleNamespace(PluginLlm=lambda plugin_id: built.append(plugin_id) or FakeLlm(RECORD))
    monkeypatch.setitem(__import__("sys").modules, "agent.plugin_llm", fake)
    collector = Collector()
    hermes_plugin.register(collector)
    registered = collector.registered[0]
    assert registered.llm is None
    provider.llm = None
    provider.on_session_end(chat(("fares?", "compared")))
    assert built == ["brain"] and len(writes) == 1


def test_a_recording_in_flight_keeps_its_own_vault_when_a_new_session_starts(provider, writes, tmp_path):
    started, release = __import__("threading").Event(), __import__("threading").Event()

    class Slow(FakeLlm):
        def complete_structured(self, **kwargs):
            started.set()
            release.wait(5)
            return SimpleNamespace(parsed=RECORD, text="")

    provider.llm = Slow()
    old_repo = provider.repo
    provider.sync_turn("a", "b", messages=chat(("fares?", "compared")))
    worker = __import__("threading").Thread(target=provider.on_session_end, args=(None,))
    worker.start()
    assert started.wait(5)
    provider.repo = tmp_path / "other-vault"
    provider._new_session(reset=True)
    release.set()
    worker.join(5)
    assert len(writes) == 1 and provider._recorded == 0
    provider.sync_turn("c", "d", messages=chat(("new", "session")))
    assert provider._messages[provider._recorded:] == chat(("new", "session"))
    assert old_repo != provider.repo


def test_a_settle_and_a_session_end_at_once_write_one_row(provider, writes):
    threading = __import__("threading")
    inside = threading.Event()

    class Slow(FakeLlm):
        def complete_structured(self, **kwargs):
            inside.set()
            __import__("time").sleep(0.2)
            return SimpleNamespace(parsed=RECORD, text="")

    provider.llm = Slow()
    provider.sync_turn("a", "b", messages=chat(("fares?", "compared")))
    provider._cancel()
    first = threading.Thread(target=provider._record_conversation)
    first.start()
    assert inside.wait(5)
    provider.on_session_end(None)
    first.join(5)
    assert len(writes) == 1


def test_a_failed_model_call_leaves_the_turns_for_the_next_attempt(provider, writes):
    provider.llm = FakeLlm(RuntimeError("down"), RuntimeError("down"), RECORD)
    provider.on_session_end(chat(("fares?", "compared")))
    assert writes == []
    provider.on_session_end(None)
    assert len(writes) == 1


def test_a_failed_write_leaves_the_turns_too(provider, monkeypatch):
    calls = []
    def run(command, **kwargs):
        if command[1:] == ["needs-you"]:
            return SimpleNamespace(stdout="", returncode=0)
        calls.append(1)
        raise OSError()

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    provider.session_id = "s"
    provider.llm = FakeLlm(RECORD, RECORD)
    provider.on_session_end(chat(("fares?", "compared")))
    provider.on_session_end(None)
    assert len(calls) == 2


def test_a_timer_that_cannot_start_never_raises_into_hermes(provider, writes, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(hermes_plugin.threading, "Timer", broken)
    provider.sync_turn("a", "b", messages=chat(("fares?", "compared")))
    provider.on_session_switch("next", reset=False)


def test_after_compression_only_turns_after_it_are_new(provider, writes):
    provider.llm = FakeLlm(RECORD)
    provider.sync_turn("a", "b", messages=chat(("one", "1"), ("two", "2"), ("three", "3")))
    provider._recorded = 6
    provider.on_session_switch("compressed", reset=False)
    provider.sync_turn("c", "d", messages=[{"role": "user", "content": "summary of before"}] + chat(("four", "4")))
    provider.on_session_end(None)
    sent = provider.llm.calls[0]["input"][0]["text"]
    assert "four" in sent and "summary of before" not in sent


def test_night_stages_cost_no_model_call(provider, writes, monkeypatch):
    jobs = SimpleNamespace(get_job=lambda job_id: {"skills": ["orphan-sleep1"]})
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs", jobs)
    provider.session_id, provider._cron_job = "cron_n1_20261028_023000", "n1"
    provider.llm = FakeLlm()
    provider.on_session_end(chat(("consolidate", "done")))
    provider.shutdown()
    assert provider.llm.calls == [] and writes == []


def test_a_cron_run_that_compresses_still_leaves_one_row_and_never_a_journal_row(provider, writes, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs",
                        SimpleNamespace(get_job=lambda job_id: {"skills": ["brain-watch"]}))
    provider.session_id, provider._cron_job = "cron_j1_20261028_090000", "j1"
    provider.llm = FakeLlm({"headline": "Mail checked.", "outcome": "done"})
    provider.sync_turn("a", "b", messages=chat(("run watch", "checking"), ("more", "still")))
    provider.on_session_end(None)                     # compression ends the session partway
    provider.on_session_switch("20261028_090500_ab12", reset=False)
    provider.sync_turn("c", "d", messages=[{"role": "user", "content": "summary"}] + chat(("go on", "done")))
    assert provider._timer is None                     # still a routine after the new id
    provider.on_session_end(None)
    provider.shutdown()
    assert [args for args, _ in writes] == [["ledger", "append", "--json"]]
    assert provider.llm.calls[0]["timeout"] == hermes_plugin.ROUTINE_TIMEOUT


def test_a_session_end_does_not_wait_on_a_recording_already_running(provider, writes):
    provider.llm = FakeLlm(RECORD)
    provider.sync_turn("a", "b", messages=chat(("fares?", "compared")))
    provider._recording.acquire()
    try:
        provider.on_session_end(None)
    finally:
        provider._recording.release()
    assert writes == [] and provider._recorded == 0   # the turns wait for the next writer
    provider.on_session_end(None)
    assert len(writes) == 1


def test_a_prose_nothing_stays_nothing(provider, writes):
    provider.llm = FakeLlm("Nothing worth recording.", "Nothing worth recording.")
    provider.on_session_end(chat(("hi", "hello")))
    assert writes == []


def test_a_compressed_transcript_is_not_journaled_again_at_session_end(provider, writes):
    provider.llm = FakeLlm(RECORD)
    provider.sync_turn("a", "b", messages=chat(("one", "1"), ("two", "2")))
    provider._recorded = 4
    provider.on_session_switch("compressed", reset=False)
    provider.on_session_end([{"role": "user", "content": "summary of one and two"}] + chat(("three", "3")))
    sent = provider.llm.calls[0]["input"][0]["text"]
    assert "three" in sent and "summary of one and two" not in sent


def test_the_record_is_told_what_already_waits_on_the_owner(provider, writes):
    open_asks[0] = ("ask · Approve the Tailscale share? · CURRENT.md\n"
                    "confirm · Set states on 3 tasks. · agent/Tasks.md\n"
                    "ask · Adopt? Shorter briefings · Briefing.md#Oct 27\n")
    provider.llm = FakeLlm(RECORD)
    provider.on_session_end(chat(("fares?", "compared")))
    instructions = provider.llm.calls[0]["instructions"]
    assert "Approve the Tailscale share?" in instructions and "Adopt? Shorter briefings" in instructions
    assert "Set states on 3 tasks." not in instructions   # a confirm is not a question to avoid repeating


def test_access_schema_is_optional_bounded_and_conversation_only():
    schema = hermes_plugin.RECORD_SCHEMA
    assert "access" not in schema["required"]
    access = schema["properties"]["access"]
    assert access["type"] == "array" and access["maxItems"] == 5
    entry = access["items"]
    assert entry["type"] == "object" and entry["additionalProperties"] is False
    assert set(entry["required"]) == {"subject", "asked", "outcome", "rule"}
    assert entry["properties"] == {
        "subject": {"type": "string"}, "asked": {"type": "string", "maxLength": 120},
        "outcome": {"enum": ["told gist", "told details", "declined", "asked owner", "did"]},
        "rule": {"type": ["string", "null"]},
    }
    assert "access" not in hermes_plugin.ROUTINE_SCHEMA["properties"]


def test_access_is_written_even_when_nothing_was_worth_a_journal_headline(provider, writes):
    record = {"headline": "A request answered in passing.", "outcome": "nothing", "access": [
        {"subject": "schedule", "asked": "tomorrow's plans", "outcome": "declined", "rule": None}]}
    provider.user_id, provider.user_id_alt = "k57kevin", "other-id"
    provider._context = "This session is with Kevin.\n\nPrivate profile text."
    provider.llm = FakeLlm(record)
    provider.on_session_end(chat(("tomorrow?", "sorry")))
    assert writes == [(["journal", "append", "--platform", "demiurge", "--user-id", "k57kevin",
                       "--user-id-alt", "other-id"], record)]
    call = provider.llm.calls[0]
    assert call["json_schema"] is hermes_plugin.RECORD_SCHEMA
    assert "This session is with Kevin." in call["instructions"]
    assert "Private profile text" not in call["instructions"]
    provider.on_session_end(None)
    assert len(writes) == 1


def test_routine_uses_a_schema_without_access_and_never_writes_access(provider, writes, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "cron.jobs",
                        SimpleNamespace(get_job=lambda job_id: {"skills": ["brain-watch"]}))
    provider._cron_job = "j1"
    provider.llm = FakeLlm({"headline": "Checked.", "outcome": "nothing", "access": [
        {"subject": "schedule", "asked": "plans", "outcome": "declined", "rule": None}]})
    provider.on_session_end(chat(("run", "done")))
    provider.shutdown()
    assert provider.llm.calls[0]["json_schema"] is hermes_plugin.ROUTINE_SCHEMA
    assert writes == [(["ledger", "append", "--json"],
                       {"skill": "brain-watch", "outcome": "nothing", "account": "Checked.", "next": ""})]


@pytest.fixture
def prefetch_env(monkeypatch):
    for env, _ in hermes_plugin.PREFETCH.values():
        monkeypatch.delenv(env, raising=False)
    return monkeypatch


def recall(provider, monkeypatch, candidates):
    calls = []

    def run(command, **kwargs):
        if command[1] == "people-mentioned":
            return SimpleNamespace(stdout="")
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(candidates))

    monkeypatch.setattr(hermes_plugin.subprocess, "run", run)
    return provider.prefetch("harbor deployment"), calls


CANDIDATES = [{"path": f"memory/Note {n}.md", "tier": "warm", "reason": f"title/path: {n}", "text": f"Body {n}."}
              for n in range(4)]


def test_prefetch_settings_default_to_todays_values(provider, prefetch_env):
    assert hermes_plugin.prefetch_settings(provider.repo) == {
        "prefetch": True, "prefetch_limit": 6, "prefetch_chars": 1500, "prefetch_form": "names", "people": "card"}
    assert hermes_plugin.prefetch_settings(None)["prefetch_limit"] == 6


def test_prefetch_off_starts_no_subprocess(provider, prefetch_env):
    prefetch_env.setenv("BRAIN_PREFETCH", "0")
    prefetch_env.setattr(hermes_plugin.subprocess, "run",
                         lambda command, **k: pytest.fail("prefetch is off") if command[1] == "retrieve" else SimpleNamespace(stdout=""))
    assert provider.prefetch("harbor deployment") == ""


def test_prefetch_limit_and_chars_from_toml_and_env_wins(provider, prefetch_env):
    (provider.repo / "brain.toml").write_text("[brain]\n\n[context]\nprefetch_limit = 3\nprefetch_chars = 120\n")
    block, calls = recall(provider, prefetch_env, CANDIDATES)
    assert calls[0] == ["brain", "retrieve", "--json", "--limit", "3", "--", "harbor deployment"]
    assert block == "\n".join([hermes_plugin.RECALL_HEADER, *(f"- memory/Note {n}.md (warm): title/path: {n}"
                                                              for n in range(3))])[:120]
    prefetch_env.setenv("BRAIN_PREFETCH_LIMIT", "2")
    prefetch_env.setenv("BRAIN_PREFETCH_CHARS", "1500")
    block, calls = recall(provider, prefetch_env, CANDIDATES)
    assert calls[0][4] == "2" and block.count("\n- ") == 2


def test_prefetch_snippet_and_full_forms(provider, prefetch_env):
    (provider.repo / "brain.toml").write_text('[brain]\n\n[context]\nprefetch_form = "snippet"\nprefetch_limit = 2\n')
    block, calls = recall(provider, prefetch_env, CANDIDATES)
    assert calls[0] == ["brain", "retrieve", "--json", "--limit", "2", "--form", "snippet", "--", "harbor deployment"]
    assert block == hermes_plugin.RECALL_HEADER + "\n- memory/Note 0.md (warm): Body 0.\n- memory/Note 1.md (warm): Body 1."
    prefetch_env.setenv("BRAIN_PREFETCH_FORM", "full")
    block, calls = recall(provider, prefetch_env, CANDIDATES)
    assert calls[0][5:7] == ["--form", "full"]
    assert block == (hermes_plugin.FULL_HEADER + "\n\n## memory/Note 0.md (warm)\n\nBody 0.\n\n"
                     "## memory/Note 1.md (warm)\n\nBody 1.")


@pytest.mark.parametrize("env, value", [("BRAIN_PREFETCH", "on"), ("BRAIN_PREFETCH_LIMIT", "six"),
                                        ("BRAIN_PREFETCH_CHARS", "-5"), ("BRAIN_PREFETCH_FORM", "summary")])
def test_invalid_prefetch_settings_log_an_error_and_recall_nothing(provider, prefetch_env, caplog, env, value):
    prefetch_env.setenv(env, value)
    prefetch_env.setattr(hermes_plugin.subprocess, "run", lambda *a, **k: pytest.fail("invalid settings never run"))
    assert provider.prefetch("harbor deployment") == ""
    assert env in caplog.text and "invalid prefetch setting" in caplog.text
    with pytest.raises(ValueError, match=env):
        hermes_plugin.prefetch_settings(provider.repo)
