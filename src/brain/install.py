"""Install package-owned Hermes routines without changing global config."""
from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import version as installed_version
from importlib.resources import files
from pathlib import Path

from .config import Config


def scripts_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve() / "scripts"


def provider_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve() / "plugins" / "brain"


CORE = frozenset({"attribute", "sleep1", "sleep2", "rem", "recall"})   # the memory mechanism: always overwritten
                                                                       # everything else is a default the vault owns after first install


def skills(config: Config, force: bool = False) -> Path:
    for routine in sorted(files("brain").joinpath("skills").iterdir(), key=lambda path: path.name):
        template = routine.joinpath("SKILL.md")
        if not template.is_file():
            continue
        target = config.skills_path / (config.skill_prefix + routine.name) / "SKILL.md"
        if target.exists() and routine.name not in CORE and not force:
            continue
        text = template.read_text(encoding="utf-8")
        for token, value in config.template_values().items():
            text = text.replace("{" + token + "}", value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return config.skills_path


def scripts() -> list[Path]:
    directory = scripts_path()
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for script in sorted(files("brain").joinpath("scripts").iterdir(), key=lambda path: path.name):
        if not script.name.endswith(".sh"):
            continue
        target = directory / script.name
        staged = target.with_name(target.name + ".tmp")
        staged.write_bytes(script.read_bytes())
        staged.chmod(0o755)
        os.replace(staged, target)   # atomic, so a script can replace itself while it runs
        installed.append(target)
    return installed


def provider() -> list[Path]:
    directory = provider_path()
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in ("__init__.py", "plugin.yaml", "README.md"):
        target = directory / name
        staged = target.with_name(target.name + ".tmp")
        staged.write_bytes(files("brain").joinpath("hermes_plugin", name).read_bytes())
        os.replace(staged, target)
        installed.append(target)
    return installed


def update(config: Config) -> list[Path]:
    """Upgrade with the owning interpreter, then reinstall scripts and the provider."""
    if not config.update_source:
        return []
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", config.update_source], check=True)
    return [*scripts(), *provider()]


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


def recorded_version(config: Config) -> str:
    path = config.vault_path / config.hermes_dir / "engine-version"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "0.0.0"


def record_version(config: Config, text: str) -> None:
    path = config.vault_path / config.hermes_dir / "engine-version"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def pending_migrations(config: Config) -> list[tuple[str, str]]:
    """(version, prompt) for every shipped migration newer than the recorded version, oldest first."""
    low, high = version_tuple(recorded_version(config)), version_tuple(installed_version("agent-brain"))
    found = []
    for entry in files("brain").joinpath("migrations").iterdir():
        if not entry.name.endswith(".md") or entry.name == "README.md":
            continue
        tag = entry.name[:-3]
        if low < version_tuple(tag) <= high:
            found.append((tag, entry.read_text(encoding="utf-8")))
    return sorted(found, key=lambda item: version_tuple(item[0]))


def migrate_check(config: Config) -> Path | None:
    """Record the installed version, or write the pending migration prompt and leave the version alone."""
    current = installed_version("agent-brain")
    pending = pending_migrations(config)
    if not pending:
        record_version(config, current)
        return None
    target = config.vault_path / config.hermes_dir / "pending-migration.md"
    body = ["---", "kind: log", "---", f"# Engine migration: {recorded_version(config)} to {current}", "",
            "Perform each section in order, then run `brain migrate-done`. Sections without `unattended: true` need the owner present.", ""]
    for tag, prompt in pending:
        body += [f"## {tag}", "", prompt.strip(), ""]
    target.write_text("\n".join(body), encoding="utf-8")
    return target


def migrate_done(config: Config) -> str:
    current = installed_version("agent-brain")
    record_version(config, current)
    pending = config.vault_path / config.hermes_dir / "pending-migration.md"
    if pending.exists():
        pending.unlink()
    return current
