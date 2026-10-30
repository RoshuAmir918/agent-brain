"""Standalone Hermes memory provider; all vault access goes through the brain CLI."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    MemoryProvider = object

STOPWORDS = set("a an and are as at be been but by can could did do does for from had has have how "
                "i if in into is it its me my not of on or our that the their them there these they "
                "this those to was we were what when where which who why will with would you your".split())
RECALL_HEADER = "Vault notes that match this message (names and index lines; open what you need):"
FULL_HEADER = "Vault notes that match this message (their text, cut to fit):"
PREFETCH = {"prefetch": ("BRAIN_PREFETCH", True), "prefetch_limit": ("BRAIN_PREFETCH_LIMIT", 6),
            "prefetch_chars": ("BRAIN_PREFETCH_CHARS", 1500), "prefetch_form": ("BRAIN_PREFETCH_FORM", "names"),
            "people": ("BRAIN_PEOPLE", "card")}
PEOPLE_FORMS = ("off", "line", "card")
FORMS = ("names", "snippet", "full")

# After a session, one bounded side call turns the transcript into a structured record: a journal row for a
# conversation once it has been quiet this long, a ledger row for a routine run when it ends. The model judges;
# the engine writes. Night stages are skipped by the engine: git is their ledger.
SETTLE_SECONDS = 90
TRANSCRIPT_CAP = 40000
MODEL_TIMEOUT = 30        # a conversation's record; the settle thread can wait this long
ROUTINE_TIMEOUT = 20      # a routine's record runs in Hermes's cron cleanup, which it bounds (cron.cleanup_timeout_seconds)
END_WAIT = 1.0            # a session end never waits long on a recording already running: the turns keep for the next
CRON_SESSION = re.compile(r"\Acron_(.+)_\d{8}_\d{6}\Z")
RECORD_SCHEMA = {
    "type": "object",
    "required": ["headline", "outcome"],
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string", "maxLength": 90},
        "outcome": {"enum": ["done", "progress", "waiting", "blocked", "failed", "nothing"]},
        "touched": {"type": ["array", "null"], "items": {"type": "string"}},
        "progress": {"type": ["array", "null"], "items": {"type": "object", "required": ["note", "did"],
                                                "properties": {"note": {"type": "string"},
                                                               "did": {"type": "string", "maxLength": 120}}}},
        "needsYou": {"type": ["string", "null"], "maxLength": 160},
        "next": {"type": ["string", "null"], "maxLength": 120},
        "thought": {"type": ["string", "null"], "maxLength": 280},
        "access": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["subject", "asked", "outcome", "rule"], "additionalProperties": False,
            "properties": {"subject": {"type": "string"}, "asked": {"type": "string", "maxLength": 120},
                           "outcome": {"enum": ["told gist", "told details", "declined", "asked owner", "did"]},
                           "rule": {"type": ["string", "null"]}}}},
    },
}
ROUTINE_SCHEMA = {**RECORD_SCHEMA, "properties": {key: value for key, value in RECORD_SCHEMA["properties"].items()
                                               if key != "access"}}
SYSTEM = ("You are the agent whose session this was, keeping your own records. Answer only with the JSON object "
          "the schema describes.")
CONVERSATION = (
    "The conversation above has gone quiet. Record it for your journal, which your owner reads.\n"
    "- headline: what happened, past tense, one line under 90 characters.\n"
    "- outcome: done (finished), progress (moved forward), waiting (on someone), blocked, failed, or nothing "
    "(nothing worth a line: small talk, a test, a question answered in passing).\n"
    "- touched: exact titles of vault notes you worked on, if any.\n"
    "- needsYou: a decision only your owner can make, phrased as a question, or null. Not a status update.\n"
    "- next: your next step, or null.\n"
    "- thought: one or two first-person sentences about what you are thinking, or null.\n"
    "- access: list each time someone other than the owner asked for something a vault subject covers, and what you "
    "did; leave empty for the owner and small talk. Use the vault's subject, asked in a few words without sensitive "
    "specifics (at most 120 characters), the outcome, and a rule id from Disclosure.md or null.\n"
    "Record what you did, never what you did not do.")
ROUTINE = (
    "The routine run above has ended. Record it for the routine's ledger.\n"
    "- headline: what the run found or did, under 90 characters; for a quiet run, say what was checked.\n"
    "- outcome: done, progress, waiting, blocked, failed (the run could not do its job), or nothing "
    "(it ran and there was nothing to act on).\n"
    "- next: the next step it leaves, or null.\n"
    "Leave touched, progress, needsYou and thought out, or null, unless the run produced something the owner must "
    "decide.")
NIGHT_STAGES = {"attribute", "sleep1", "rem", "sleep2"}   # one commit each: git is their ledger


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def render_transcript(messages, cap: int = TRANSCRIPT_CAP) -> str:
    """User and assistant text, tool calls named but not replayed, cut from the front to ``cap`` characters."""
    lines = []
    for message in messages:
        if message.get("role") not in ("user", "assistant"):
            continue
        text = _text(message.get("content")).strip()
        calls = [((call.get("function") or {}).get("name") or call.get("name") or "tool")
                 for call in message.get("tool_calls") or []]
        if calls:
            text = f"{text}\n[used {', '.join(calls)}]".strip()
        if text:
            lines.append(f"{message['role']}: {text}")
    return "\n\n".join(lines)[-cap:]


def turns(messages) -> int:
    return sum(1 for message in messages
               if message.get("role") in ("user", "assistant") and _text(message.get("content")).strip())


def configured_cwd(hermes_home: str) -> str:
    """The profile's terminal working directory, which is where its vault lives on a Hermes host."""
    try:
        text = (Path(hermes_home) / "config.yaml").read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r"^terminal:[ \t]*\n(?:[ \t]+.*\n)*?[ \t]+cwd:[ \t]*[\"']?([^\"'\n#]+)", text, re.M)
    return match[1].strip() if match else ""


def find_repo(cwd: str = "", hermes_home: str = "") -> Path | None:
    for candidate in (cwd, os.environ.get("VAULT_REPO"), configured_cwd(hermes_home) if hermes_home else "",
                      Path.home() / "brain"):
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        for parent in (path, *path.parents):
            if (parent / "brain.toml").is_file():
                return parent
    return None


def prefetch_settings(repo) -> dict:
    """The prefetch keys of brain.toml's [context], each overridden by its BRAIN_* variable, as the engine reads them."""
    path = Path(repo) / "brain.toml" if repo is not None else None
    values = {}
    if path is not None and path.is_file():
        with path.open("rb") as stream:
            values = tomllib.load(stream).get("context", {})
    if not isinstance(values, dict):
        raise ValueError("brain.toml [context] must be a table")
    settings = {}
    for name, (env, default) in PREFETCH.items():
        raw = os.environ.get(env)
        where, value = (env, raw) if raw is not None else (f"context.{name}", values.get(name, default))
        if name == "prefetch":
            if raw is not None and raw not in ("0", "1") or raw is None and not isinstance(value, bool):
                raise ValueError(f"{where} must be 0 or 1 (true or false in brain.toml)")
            value = raw == "1" if raw is not None else value
        elif name == "prefetch_form":
            if value not in FORMS:
                raise ValueError(f"{where} must be one of {', '.join(FORMS)}")
        elif name == "people":
            if value not in PEOPLE_FORMS:
                raise ValueError(f"{where} must be one of {', '.join(PEOPLE_FORMS)}")
        else:
            if raw is not None:
                value = int(raw) if raw.strip().isdigit() else None
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{where} must be a non-negative integer")
        settings[name] = value
    return settings


class BrainMemoryProvider(MemoryProvider):
    def __init__(self):
        self.platform = ""
        self.user_id = ""
        self.user_id_alt = ""
        self.user_name = ""
        self.chat_id = ""
        self.session_id = ""
        self.repo = None
        self._context = ""
        self.llm = None
        self._lock = threading.Lock()        # session state below
        self._recording = threading.Lock()   # one recording at a time: a settle and a session end never both write
        self._epoch = 0                      # bumped when the session changes; a recording started before keeps its own
        self._messages: list = []
        self._recorded = 0                   # index into _messages of what is already journaled
        self._rebase = False                 # after compression the transcript is rewritten: only new turns count
        self._cron_job: str | None = None    # decided when the session starts; compression's new id does not change it
        self._routine_done = False
        self._timer: threading.Timer | None = None
        self._generation = 0

    @property
    def name(self) -> str:
        return "brain"

    def _run(self, *args: str) -> str | None:
        if self.repo is None:
            return None
        try:
            result = subprocess.run(["brain", *args], cwd=self.repo, capture_output=True,
                                    text=True, check=True, timeout=5)
            return result.stdout.strip()
        except Exception:
            return None

    def is_available(self) -> bool:
        """Available wherever the brain CLI runs. The vault is found per session, from what Hermes passes then;
        a missing VAULT_REPO must not switch the brain off before the session says where it is."""
        try:
            subprocess.run(["brain", "--help"], capture_output=True, text=True, check=True, timeout=5)
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._context = ""
        self.repo = None
        self.session_id = session_id
        self._people_seen = set()   # once per person per session, never per process
        self._new_session(reset=True)
        match = CRON_SESSION.match(session_id or "")
        self._cron_job, self._routine_done = (match[1] if match else None), False
        try:
            arguments = ["session-context"]
            for field in ("platform", "user_id", "user_id_alt", "user_name", "chat_id"):
                value = kwargs.get(field)
                value = str(value) if value is not None else ""
                setattr(self, field, value)
                if value:
                    arguments.extend(["--" + field.replace("_", "-"), value])
            self.repo = find_repo(kwargs.get("cwd") or "", kwargs.get("hermes_home") or "")
            if self.repo is None:
                logger.warning("brain provider: no vault found (session cwd, VAULT_REPO, the profile's terminal.cwd, "
                               "~/brain); this session runs without the brain")
            self._context = self._run(*arguments) or ""
        except Exception:
            self._context = ""

    def system_prompt_block(self) -> str:
        return self._context

    def prefetch(self, query, *, session_id="") -> str:
        try:
            settings = prefetch_settings(self.repo)
        except (OSError, ValueError) as error:
            logger.error("brain provider: invalid prefetch setting, no recall this turn: %s", error)
            return ""
        return "\n\n".join(filter(None, (self._people(query, settings["people"]), self._recall(query, settings))))

    def _owner_session(self) -> bool:
        """The engine's block for the owner begins with one fixed sentence; anyone else gets a profile or a warning."""
        return re.search(r"^This session is with .+, the owner\.$", self._context, re.M) is not None

    def _people(self, query, form) -> str:
        """Who the people named in the message are, each once per session: `line` is a sentence, `card` the profile.
        Owner sessions only: a friend asking about another friend must not have that person's profile put in front of
        the model; disclosure decides what they hear, from what the agent chooses to open."""
        if form not in ("line", "card") or not self._owner_session():
            return ""
        if not any(term not in STOPWORDS for term in re.findall(r"\b[^\W_]+\b", query.lower())):
            return ""
        text = self._run("people-mentioned", "--form", form, "--", query[:2000]) or ""
        if not text.strip():
            return ""
        seen = self.__dict__.setdefault("_people_seen", set())   # a provider used without initialize still works
        parts = re.split(r"\n(?=- |### )", text.strip())
        names = [re.match(r"(?:- |### )([^:(]+)", part) for part in parts[1:]]
        fresh = [part for part, name in zip(parts[1:], names) if name and name[1].strip() not in seen]
        seen.update(name[1].strip() for name in names if name)
        return "\n".join([parts[0], *fresh]) if fresh else ""

    def _recall(self, query, settings) -> str:
        if not settings["prefetch"]:
            return ""
        limit, form = settings["prefetch_limit"], settings["prefetch_form"]
        try:
            terms = re.findall(r"\b[^\W_]+\b", query.lower())
            if sum(term not in STOPWORDS for term in terms) < 2:
                return ""
            text = self._run("retrieve", "--json", "--limit", str(limit), *(["--form", form] if form != "names" else []),
                             "--", query[:300])
            if not text:
                return ""
            candidates = json.loads(text)
            if not isinstance(candidates, list) or not candidates:
                return ""
            lines = [FULL_HEADER if form == "full" else RECALL_HEADER]
            for candidate in candidates[:limit]:
                path, tier, reason = (candidate[key] for key in ("path", "tier", "reason"))
                body = reason if form == "names" else candidate.get("text")
                if not all(isinstance(value, str) for value in (path, tier, body)):
                    return ""
                lines.append(f"## {path} ({tier})\n\n{body}" if form == "full" else f"- {path} ({tier}): {body}")
            return ("\n\n" if form == "full" else "\n").join(lines)[:settings["prefetch_chars"]]
        except Exception:
            return ""

    def get_tool_schemas(self) -> list:
        return []

    def handle_tool_call(self, tool_name, args, **kwargs):
        return None

    def get_config_schema(self) -> list:
        return []

    def save_config(self, values: dict, hermes_home: str) -> None:
        pass

    def sync_turn(self, user, assistant, *, session_id="", messages=None, **_) -> None:
        try:
            if messages is None:
                return
            with self._lock:
                self._take(messages)
                if self._cron_job:
                    return
                if self._timer is not None:
                    self._timer.cancel()
                self._generation += 1
                self._timer = threading.Timer(SETTLE_SECONDS, self._settled, args=(self._generation,))
                self._timer.daemon = True
                self._timer.start()
        except Exception:
            pass

    def on_session_end(self, messages) -> None:
        """For a conversation, flush what is new. A routine is recorded once, at teardown (``shutdown``): compression
        also ends a session, partway through a run."""
        try:
            self._cancel()
            with self._lock:
                if messages:
                    self._take(messages)
            if not self._cron_job:
                self._record_conversation(wait=END_WAIT)
        except Exception:
            pass

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **_) -> None:
        """A /new starts over; compression or /resume keeps the session's place, so only turns after it count."""
        try:
            self.session_id = new_session_id or self.session_id
            self._new_session(reset=reset)
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self._cancel()
            if self._cron_job and not self._routine_done:
                self._routine_done = True
                self._record_routine()
        except Exception:
            pass

    # ── after the session ────────────────────────────────────────────────────

    def _take(self, messages) -> None:
        """Called with ``_lock`` held."""
        self._messages = list(messages)
        if self._rebase or len(self._messages) < self._recorded:
            self._recorded, self._rebase = max(0, len(self._messages) - 2), False

    def _new_session(self, reset: bool) -> None:
        self._cancel()
        with self._lock:
            self._epoch += 1
            self._messages = []
            self._recorded, self._rebase = 0, not reset

    def _cancel(self) -> None:
        with self._lock:
            timer, self._timer = self._timer, None
            self._generation += 1
        if timer is not None:
            timer.cancel()

    def _settled(self, generation: int) -> None:
        try:
            with self._lock:
                if generation != self._generation:
                    return
            self._record_conversation()
        except Exception:
            pass

    def _record_conversation(self, wait: float | None = None) -> None:
        """Only what was said since the last row, so a long-lived chat is not journaled twice. The cursor moves only
        after a row is written or the model says there was nothing; a failed call leaves the turns for next time."""
        if not self._recording.acquire(timeout=-1 if wait is None else wait):
            return
        try:
            with self._lock:
                epoch, repo, platform = self._epoch, self.repo, self.platform
                user_id, user_id_alt, context = self.user_id, self.user_id_alt, self._context.split("\n\n", 1)[0]
                fresh, upto = self._messages[self._recorded:], len(self._messages)
            if turns(fresh) < 2:
                return
            record = self._ask(CONVERSATION + ("\n" + context if context else "") + self._open_asks(repo), fresh)
            if record is None:
                return
            arguments = ["journal", "append", "--platform", platform]
            for flag, value in (("--user-id", user_id), ("--user-id-alt", user_id_alt)):
                if value:
                    arguments.extend([flag, value])
            if (record["outcome"] != "nothing" or record.get("access")) and not self._write(
                    repo, arguments, json.dumps(record)):
                return
            with self._lock:
                if self._epoch == epoch:
                    self._recorded = max(self._recorded, upto)
        finally:
            self._recording.release()

    @staticmethod
    def _open_asks(repo) -> str:
        """What already waits on the owner, so a conversation's record raises a question only when it is new."""
        if repo is None:
            return ""
        try:
            listed = subprocess.run(["brain", "needs-you"], cwd=repo, capture_output=True, text=True, check=True,
                                    timeout=5).stdout.strip().splitlines()
        except Exception:
            return ""
        asks = [line.split(" · ", 2)[1] for line in listed if line.startswith("ask · ") and line.count(" · ") >= 2]
        if not asks:
            return ""
        return ("\nAlready waiting on your owner; leave needsYou null if the conversation only raised one of these "
                "again, in any wording:\n" + "\n".join(f"- {ask[:160]}" for ask in asks[:20]))

    def _record_routine(self) -> None:
        with self._recording:
            with self._lock:
                repo, messages = self.repo, list(self._messages)
            skill = self._cron_skill()
            if skill is None or skill.rsplit("-", 1)[-1] in NIGHT_STAGES or not turns(messages):
                return
            record = self._ask(ROUTINE, messages, timeout=ROUTINE_TIMEOUT, attempts=1, schema=ROUTINE_SCHEMA)
            if record is None:
                return
            self._write(repo, ["ledger", "append", "--json"], json.dumps(
                {"skill": skill, "outcome": record["outcome"], "account": record["headline"],
                 "next": record.get("next") or ""}))

    def _cron_skill(self) -> str | None:
        """The skill the cron job runs, from Hermes's own job store; the engine maps it to a routine."""
        if not self._cron_job:
            return None
        try:
            from cron.jobs import get_job
            job = get_job(self._cron_job) or {}
        except Exception:
            return None
        skills = [skill for skill in (job.get("skills") or [job.get("skill")]) if isinstance(skill, str) and skill]
        own = [skill for skill in skills if not skill.endswith("-recall")]
        return (own or skills or [None])[0]

    def _model(self):
        """The host's model facade. A memory provider's registration context does not expose ``ctx.llm``, so the
        provider builds the same facade the host would, once, on first use."""
        if self.llm is None:
            try:
                from agent.plugin_llm import PluginLlm
                self.llm = PluginLlm(plugin_id="brain")
            except Exception:
                return None
        return self.llm

    def _ask(self, instructions: str, messages: list, timeout: float = MODEL_TIMEOUT, attempts: int = 2,
             schema: dict = RECORD_SCHEMA) -> dict | None:
        """One structured call, retried once; a model that will not give JSON still leaves its first line."""
        complete = getattr(self._model(), "complete_structured", None)
        if complete is None:
            return None
        transcript = render_transcript(messages)
        text = ""
        for _ in range(attempts):
            try:
                result = complete(instructions=instructions, input=[{"type": "text", "text": transcript}],
                                  json_schema=schema, schema_name="after_session", system_prompt=SYSTEM,
                                  max_tokens=700, timeout=timeout, purpose="brain-after-session")
            except Exception:
                continue
            parsed = getattr(result, "parsed", None)
            if isinstance(parsed, dict) and parsed.get("outcome") in RECORD_SCHEMA["properties"]["outcome"]["enum"] \
                    and isinstance(parsed.get("headline"), str) and parsed["headline"].strip():
                return parsed
            text = text or (getattr(result, "text", "") or "")
        line = " ".join(text.split())[:90]
        if not line:
            return None
        return {"headline": line, "outcome": "nothing" if line.lower().startswith("nothing") else "progress"}

    @staticmethod
    def _write(repo, arguments: list[str], payload: str) -> bool:
        if repo is None:
            return False
        try:
            subprocess.run(["brain", *arguments], cwd=repo, input=payload, capture_output=True,
                           text=True, check=True, timeout=30)
            return True
        except Exception:
            return False


def register(ctx):
    provider = BrainMemoryProvider()
    try:
        provider.llm = ctx.llm   # a general plugin context has it; a memory provider's collector does not
    except Exception:
        provider.llm = None
    ctx.register_memory_provider(provider)
