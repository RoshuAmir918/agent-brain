"""Install package-owned Hermes routines without changing global config."""
from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

from .config import Config


def scripts_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve() / "scripts"


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
        target.write_bytes(script.read_bytes())
        target.chmod(0o755)
        installed.append(target)
    return installed
