"""Standalone Hermes memory provider; all vault access goes through the brain CLI."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    MemoryProvider = object

STOPWORDS = set("a an and are as at be been but by can could did do does for from had has have how "
                "i if in into is it its me my not of on or our that the their them there these they "
                "this those to was we were what when where which who why will with would you your".split())
RECALL_HEADER = "Vault notes that match this message (names and index lines; open what you need):"


def find_repo(cwd: str = "") -> Path | None:
    for candidate in (cwd, os.environ.get("VAULT_REPO"), Path.home() / "brain"):
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        for parent in (path, *path.parents):
            if (parent / "brain.toml").is_file():
                return parent
    return None


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
        try:
            if self.repo is None:
                self.repo = find_repo()
            return (self.repo is not None and (self.repo / "brain.toml").is_file()
                    and self._run("--help") is not None)
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._context = ""
        self.repo = None
        self.session_id = session_id
        try:
            arguments = ["session-context"]
            for field in ("platform", "user_id", "user_id_alt", "user_name", "chat_id"):
                value = kwargs.get(field)
                value = str(value) if value is not None else ""
                setattr(self, field, value)
                if value:
                    arguments.extend(["--" + field.replace("_", "-"), value])
            self.repo = find_repo(kwargs.get("cwd") or "")
            self._context = self._run(*arguments) or ""
        except Exception:
            self._context = ""

    def system_prompt_block(self) -> str:
        return self._context

    def prefetch(self, query, *, session_id="") -> str:
        try:
            terms = re.findall(r"\b[^\W_]+\b", query.lower())
            if sum(term not in STOPWORDS for term in terms) < 2:
                return ""
            text = self._run("retrieve", "--json", "--limit", "6", "--", query[:300])
            if not text:
                return ""
            candidates = json.loads(text)
            if not isinstance(candidates, list) or not candidates:
                return ""
            lines = [RECALL_HEADER]
            for candidate in candidates[:6]:
                path, tier, reason = (candidate[key] for key in ("path", "tier", "reason"))
                if not all(isinstance(value, str) for value in (path, tier, reason)):
                    return ""
                lines.append(f"- {path} ({tier}): {reason}")
            return "\n".join(lines)[:1500]
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

    def sync_turn(self, user, assistant, *, session_id="", messages=None) -> None:
        pass

    def on_session_end(self, messages) -> None:
        pass

    def shutdown(self) -> None:
        pass


def register(ctx):
    ctx.register_memory_provider(BrainMemoryProvider())
