"""Repository-local layout and identity, discovered from brain.toml."""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path


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
    skill_prefix: str = "brain-"
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
            if not isinstance(value, str) or (not value and name != "skill_prefix"):
                raise ValueError(f"brain.{name} must be a nonempty string")
            if name.endswith("_dir") or name in ("vault", "human_profile"):
                path = Path(value)
                if path.is_absolute() or ".." in path.parts or path == Path("."):
                    raise ValueError(f"brain.{name} must be a relative path without '..'")
                paths[name] = path.as_posix()
        if any(char in config.skill_prefix for char in "/\\") or config.skill_prefix in (".", ".."):
            raise ValueError("brain.skill_prefix must be a name prefix, not a path")
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
    def orientation(self) -> set[str]:
        return {"START.md", "CURRENT.md", "Hot Set.md", "Warm Index.md",
                f"{self.hermes_dir}/README.md", *(self.agent(name) for name in
                ("SOUL.md", "Triggers.md", "Tasks.md", "Channels.md", "Memory Spec.md", "Retrieval Checks.md"))}

    @property
    def context_sources(self) -> tuple[str, ...]:
        return ("START.md", "CURRENT.md", self.agent("Triggers.md"), self.agent("Tasks.md"), "Hot Set.md")

    @property
    def generated_views(self) -> set[str]:
        return {"CURRENT.md", "Hot Set.md", "Warm Index.md", self.agent("Tasks.md"), self.agent("Triggers.md")}

    @property
    def observed_excluded(self) -> tuple[str, ...]:
        return ("Conversations/", *(self.agent(name + "/") for name in
                ("Dreams", "Explorations", "Use Log", "Sleep Log", "Tasks Archive")))

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
                 "sources_dir", "hermes_dir", "human_name", "agent_name")} | {"prefix": self.skill_prefix}
