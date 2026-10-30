"""Repository-local layout and identity, discovered from brain.toml."""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SECTIONS = ("start", "current", "goals", "triggers", "tasks", "hot", "directory")   # the parts of .hermes.md, in order
FORMS = ("names", "snippet", "full")


def pinned_today() -> date | None:
    """BRAIN_TODAY=YYYY-MM-DD pins the engine's today, for evaluations against throwaway vaults."""
    value = os.environ.get("BRAIN_TODAY")
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"BRAIN_TODAY must be a date as YYYY-MM-DD: {value!r}")


@dataclass(frozen=True)
class Context:
    """What enters an agent's context: brain.toml's [context], each key overridden by its BRAIN_* variable."""
    hot_words: int = 6000
    sections: tuple[str, ...] = SECTIONS
    prefetch: bool = True
    prefetch_limit: int = 6
    prefetch_chars: int = 1500
    prefetch_form: str = "names"
    hot_threshold: int = 7
    warm_threshold: int = 28

    ENV = {"hot_words": "BRAIN_HOT_WORDS", "sections": "BRAIN_CONTEXT_SECTIONS", "prefetch": "BRAIN_PREFETCH",
           "prefetch_limit": "BRAIN_PREFETCH_LIMIT", "prefetch_chars": "BRAIN_PREFETCH_CHARS",
           "prefetch_form": "BRAIN_PREFETCH_FORM", "hot_threshold": "BRAIN_HOT_THRESHOLD",
           "warm_threshold": "BRAIN_WARM_THRESHOLD"}

    @classmethod
    def load(cls, values) -> Context:
        if not isinstance(values, dict) or set(values) - set(cls.ENV):
            raise ValueError("brain.toml [context] must contain only supported context keys")
        settings = {}
        for name, env in cls.ENV.items():
            raw = os.environ.get(env)
            if raw is None and name not in values:
                continue
            where, value = (env, raw) if raw is not None else (f"context.{name}", values[name])
            if name == "sections":
                value = [part.strip() for part in value.split(",") if part.strip()] if raw is not None else value
                if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                    raise ValueError(f"{where} must be a list of section names")
                unknown = [part for part in value if part not in SECTIONS]
                if unknown:
                    raise ValueError(f"{where} has unknown sections {', '.join(unknown)}; known: {', '.join(SECTIONS)}")
                value = tuple(value)
            elif name == "prefetch":
                if raw is not None and raw not in ("0", "1") or raw is None and not isinstance(value, bool):
                    raise ValueError(f"{where} must be 0 or 1 (true or false in brain.toml)")
                value = raw == "1" if raw is not None else value
            elif name == "prefetch_form":
                if value not in FORMS:
                    raise ValueError(f"{where} must be one of {', '.join(FORMS)}")
            else:
                if raw is not None:
                    value = int(raw) if raw.strip().isdigit() else None
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{where} must be a non-negative integer")
            settings[name] = value
        context = cls(**settings)
        if context.hot_threshold > context.warm_threshold:
            raise ValueError("the hot threshold cannot exceed the warm threshold")
        return context


@dataclass(frozen=True)
class Config:
    repo: Path
    vault: str = "memory"
    agent_dir: str = "agent"
    people_dir: str = "people"
    agents_dir: str = "agents"
    human_profile: str = "human/Profile.md"
    sources_dir: str = "sources"
    hermes_dir: str = "hermes"
    human_name: str = "Human"
    agent_name: str = "Agent"
    owner_user_ids: list[str] = field(default_factory=list)   # the owner's platform ids; "<platform>:<id>" matches one platform only
    maintainers: list[str] = field(default_factory=list)   # platform ids (bare or "<platform>:<id>") that keep the machinery running; never the owner
    log_dirs: list[str] = field(default_factory=list)   # folders under agent_dir holding receipts and ledgers, never notes
    skill_prefix: str = "brain-"
    timezone: str = "America/Los_Angeles"   # the owner's zone: the export's "yesterday", digest day boundaries, health and audit dates
    update_source: str = "git+https://github.com/RoshuAmir918/agent-brain"   # empty string disables brain update
    _vault_path: Path | None = None

    @classmethod
    def load(cls, vault: Path | str | None = None, *, cwd: Path | None = None) -> Config:
        explicit = Path(vault).expanduser().resolve() if vault is not None else None
        start = explicit or Path(cwd or Path.cwd()).resolve()
        for parent in (start, *start.parents):
            path = parent / "brain.toml"
            if path.is_file():
                with path.open("rb") as stream:
                    values = tomllib.load(stream).get("brain", {})
                repo = parent
                break
        else:
            values = {}
            try:
                repo = Path(subprocess.check_output(
                    ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                    text=True, stderr=subprocess.DEVNULL).strip())
            except (OSError, subprocess.CalledProcessError):
                repo = explicit.parent if explicit else start
        allowed = {item.name for item in fields(cls) if item.name not in ("repo", "_vault_path")}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("brain.toml [brain] must contain only supported config keys")
        config = cls(repo=repo, _vault_path=explicit, **values)
        paths = {}
        for name in allowed:
            value = getattr(config, name)
            if name in ("owner_user_ids", "maintainers", "log_dirs"):
                if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                    raise ValueError(f"brain.{name} must be a list of strings")
                if name == "log_dirs" and any(Path(item).is_absolute() or ".." in Path(item).parts for item in value):
                    raise ValueError("brain.log_dirs must be relative paths without '..'")
                continue
            if not isinstance(value, str) or (not value and name not in ("skill_prefix", "update_source")):
                raise ValueError(f"brain.{name} must be a nonempty string")
            if name.endswith("_dir") or name in ("vault", "human_profile"):
                path = Path(value)
                if path.is_absolute() or ".." in path.parts or path == Path("."):
                    raise ValueError(f"brain.{name} must be a relative path without '..'")
                paths[name] = path.as_posix()
        if any(char in config.skill_prefix for char in "/\\") or config.skill_prefix in (".", ".."):
            raise ValueError("brain.skill_prefix must be a name prefix, not a path")
        try:
            ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"brain.timezone is not a known zone: {config.timezone}")
        pinned_today()
        config.context   # invalid context settings fail here, not mid-command
        return replace(config,
                       **paths,
                       human_name=os.environ.get("BRAIN_HUMAN_NAME", config.human_name),
                       agent_name=os.environ.get("BRAIN_AGENT_NAME", config.agent_name))

    @property
    def vault_path(self) -> Path:
        return self._vault_path or self.repo / self.vault

    def agent(self, relative: str) -> str:
        return f"{self.agent_dir}/{relative}"

    def link(self, relative: str) -> str:
        return f"{self.vault}/{relative}"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self, moment: datetime | None = None) -> datetime:
        """The current time in the owner's zone, or `moment`, on BRAIN_TODAY's date when it is set."""
        moment = moment or datetime.now(self.zone)
        pinned = pinned_today()
        return moment if pinned is None else datetime.combine(pinned, moment.timetz())

    def today(self) -> date:
        return self.now().date()

    @cached_property
    def context(self) -> Context:
        path = self.repo / "brain.toml"
        if not path.is_file():
            return Context.load({})
        with path.open("rb") as stream:
            return Context.load(tomllib.load(stream).get("context", {}))

    @property
    def human_goals(self) -> str:
        """The owner's goals, beside their profile: aspirations, active goals, adopted practices, in their words."""
        return (Path(self.human_profile).parent / "Goals.md").as_posix()

    @property
    def orientation(self) -> set[str]:
        return {"START.md", "CURRENT.md", "Hot Set.md", "Warm Index.md", self.human_goals,
                f"{self.hermes_dir}/README.md", *(self.agent(name) for name in
                ("SOUL.md", "Triggers.md", "Tasks.md", "Channels.md", "Memory Spec.md", "Retrieval Checks.md"))}

    @property
    def context_sources(self) -> tuple[str, ...]:
        return ("START.md", "CURRENT.md", self.human_goals, self.agent("Triggers.md"), self.agent("Tasks.md"), "Hot Set.md")

    @property
    def generated_views(self) -> set[str]:
        return {"CURRENT.md", "Hot Set.md", "Warm Index.md", self.agent("Tasks.md"), self.agent("Triggers.md")}

    @property
    def log_paths(self) -> tuple[str, ...]:
        """Agent-folder prefixes whose files are logs: engine ledgers plus the vault's own receipt folders."""
        names = ("Research", "Explorations", "Improvements", "Use Log", "Sleep Log", "Tasks Archive", *self.log_dirs)
        return tuple(self.agent(Path(name).as_posix() + "/") for name in names)

    @property
    def observed_excluded(self) -> tuple[str, ...]:
        return ("Conversations/", *(self.agent(name + "/") for name in
                ("Dreams", "Explorations", "Improvements", "Use Log", "Sleep Log", "Tasks Archive", "Retrieval Checks")))

    @property
    def archive_dir(self) -> str:
        return "Archive"

    @property
    def skills_path(self) -> Path:
        return self.vault_path / self.hermes_dir / "skills"

    @property
    def soul_path(self) -> Path:
        return self.vault_path / self.agent("SOUL.md")

    def name_for(self, role: str) -> str:
        """Display name for a digest turn."""
        return self.human_name if role == "user" else self.agent_name

    def template_values(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in
                ("vault", "agent_dir", "people_dir", "agents_dir", "human_profile",
                 "sources_dir", "hermes_dir", "human_name", "agent_name")} | {"prefix": self.skill_prefix, "human_goals": self.human_goals}
