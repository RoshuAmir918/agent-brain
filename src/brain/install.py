"""Install package-owned Hermes routines without changing global config."""
from __future__ import annotations

import os
import subprocess
import sys
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
        staged = target.with_name(target.name + ".tmp")
        staged.write_bytes(script.read_bytes())
        staged.chmod(0o755)
        os.replace(staged, target)   # atomic, so a script can replace itself while it runs
        installed.append(target)
    return installed


def update(config: Config) -> list[Path]:
    """Upgrade the package with the interpreter that owns it, then reinstall the cron scripts."""
    if not config.update_source:
        return []
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", config.update_source], check=True)
    return scripts()
