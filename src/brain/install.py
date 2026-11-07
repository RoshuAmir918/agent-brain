"""Install package-owned routines without changing global config."""
from __future__ import annotations

import re
import shutil

import subprocess
import sys
from importlib.metadata import version as installed_version
from importlib.resources import files
from pathlib import Path

from . import fm, runtime
from .config import Config
from .lock import vault_lock


def scripts_path() -> Path:
    return runtime.home().resolve() / "scripts"


def records_path() -> Path:
    """Run payloads for this profile, outside the vault: one workspace per agent, so one records tree per agent."""
    return runtime.home().resolve() / "records"


def sessions_records_path() -> Path:
    return records_path() / "sessions"


# Day routines that were the vault's own until 0.18: the first render keeps the vault's copy beside the new one, as
# previous.md, so its own steps can be moved under `## Local` and nothing the owner asked for is lost.
ADOPTED = frozenset({"morning", "evening"})

LOCAL_HEADING = re.compile(r"^## Local[ \t]*$", re.M)
EDITIONS_LINE = re.compile(r"^\s+editions:\s*\[([^\]]*)\]\s*$", re.M)
# A part of a skill for one edition only: `<!-- personal -->` … `<!-- /personal -->` on lines of their own.
EDITION_BLOCK = re.compile(r"^<!-- (personal|enterprise) -->\n(.*?)^<!-- /\1 -->\n", re.M | re.S)


def editions(text: str) -> set[str]:
    """The editions a skill is for, from `metadata.runtime.editions` in its frontmatter; every edition when unsaid."""
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    match = EDITIONS_LINE.search(head)
    return {part.strip() for part in match[1].split(",") if part.strip()} if match else {"personal", "enterprise"}


def for_edition(text: str, edition: str) -> str:
    """The skill as this edition reads it: other editions' parts removed, this edition's kept without their markers."""
    return EDITION_BLOCK.sub(lambda m: m[2] if m[1] == edition else "", text)


def local_section(text: str) -> str:
    """The vault's own additions to a skill: from a `## Local` heading to the end of the file."""
    match = LOCAL_HEADING.search(text)
    return text[match.start():].rstrip("\n") + "\n" if match else ""


def render(text: str, values: dict[str, str]) -> str:
    """A template with each `{token}` replaced by its value."""
    for token, value in values.items():
        text = text.replace("{" + token + "}", value)
    return text


def render_skill(config: Config, name: str) -> str:
    """The package's current text of one routine, rendered for this vault: what `install-skills` writes before keeping the
    vault's `## Local` section."""
    template = files("brain").joinpath("skills", name, "SKILL.md")
    if not template.is_file():
        raise ValueError(f"no such routine: {name}; the package has " + ", ".join(
            sorted(p.name for p in files("brain").joinpath("skills").iterdir() if p.joinpath("SKILL.md").is_file())))
    return render(for_edition(template.read_text(encoding="utf-8"), config.edition), config.template_values())


def skills(config: Config) -> Path:
    """Skills and the Memory Spec keep the vault's own `## Local` sections, read and rewritten under the vault lock."""
    with vault_lock(config):
        for routine in sorted(files("brain").joinpath("skills").iterdir(), key=lambda path: path.name):
            template = routine.joinpath("SKILL.md")
            if not template.is_file():
                continue
            target = config.skills_path / (config.skill_prefix + routine.name) / "SKILL.md"
            if config.edition not in editions(template.read_text(encoding="utf-8")):
                # Not this edition's: an earlier render (before the vault changed edition) is removed with its folder.
                if target.exists():
                    shutil.rmtree(target.parent)
                continue
            text = render_skill(config, routine.name)
            if target.exists():
                current = target.read_text(encoding="utf-8")
                previous = target.with_name("previous.md")
                if routine.name in ADOPTED and not previous.exists() and not local_section(current) and current != text:
                    previous.write_text(current, encoding="utf-8")
                local = local_section(current)
                if local:
                    text = render(text.rstrip("\n") + "\n\n" + local, config.template_values())
            target.parent.mkdir(parents=True, exist_ok=True)
            fm.write_text(target, text)
        memory_spec(config)
    return config.skills_path


def memory_spec(config: Config) -> Path:
    """The Memory Spec is the engine's description of how memory works, so it follows the engine: re-rendered with the
    skills, keeping the vault's frontmatter and its `## Local` section."""
    target = config.vault_path / config.agent("Memory Spec.md")
    text = render(files("brain").joinpath("templates", "memory-spec.md").read_text(encoding="utf-8"), config.template_values())
    data = {"kind": "orientation", "pinned": True, "tier": "hot", "uses": [], "uses_inferred": []}
    if target.exists():
        data, current = fm.read(target)
        local = local_section(current)
        if local:
            text = render(text.rstrip("\n") + "\n\n" + local, config.template_values())
        if current.strip() == text.strip():
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fm.write(target, data, text)
    return target


def responsibility(config: Config, name: str, skill: str, records: str) -> list[Path]:
    """Build the three homes of a responsibility: hub at the vault root, own skill, records folder. Existing files are kept."""
    values = config.template_values() | {"name": name, "skill": skill, "records": records,
                                         "human_goals_link": config.human_goals.removesuffix(".md")}
    written = []
    for template, target in (("hub.md", config.vault_path / f"{name}.md"),
                             ("skill.md", config.skills_path / skill / "SKILL.md")):
        if target.exists():
            continue
        text = render(files("brain").joinpath("templates", template).read_text(encoding="utf-8"), values)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    (records_path() / records).mkdir(parents=True, exist_ok=True)
    return written


def scripts() -> list[Path]:
    directory = scripts_path()
    directory.mkdir(parents=True, exist_ok=True)
    installed = []
    for script in sorted(files("brain").joinpath("scripts").iterdir(), key=lambda path: path.name):
        if not script.name.endswith(".sh"):
            continue
        target = directory / script.name
        fm.write_text(target, script.read_bytes(), 0o755)   # atomic, so a script can replace itself while it runs
        installed.append(target)
    return installed


def update(config: Config) -> list[Path]:
    """Upgrade with the owning interpreter, then reinstall scripts when a runtime is configured."""
    if not config.update_source:
        return []
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", config.update_source], check=True)
    return scripts() if runtime.configured() is not None else []


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


def recorded_version(config: Config) -> str:
    path = config.vault_path / config.runtime_dir / "engine-version"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "0.0.0"


def record_version(config: Config, text: str) -> None:
    path = config.vault_path / config.runtime_dir / "engine-version"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def pending_migrations(config: Config) -> list[tuple[str, str]]:
    """(version, prompt) for every shipped migration newer than the recorded version, oldest first. A vault with no
    recorded version is new, made at the installed version, so nothing applies."""
    if not (config.vault_path / config.runtime_dir / "engine-version").exists():
        return []
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
    target = config.vault_path / config.runtime_dir / "pending-migration.md"
    body = ["---", "kind: log", "---", f"# Engine migration: {recorded_version(config)} to {current}", "",
            "Perform each section in order, then run `brain migrate-done`. Sections without `unattended: true` need the owner present.", ""]
    for tag, prompt in pending:
        body += [f"## {tag}", "", prompt.strip(), ""]
    text = "\n".join(body)
    fresh = not target.exists() or target.read_text(encoding="utf-8") != text
    target.write_text(text, encoding="utf-8")
    if fresh:
        start_migration(config)
    return target


MIGRATION_RUN = ("An engine migration is pending in this vault. Perform all of it now, as your session context "
                 "describes, and finish with `brain migrate-done`.")


MIGRATION_ROUTINE = "brain-migration-"


def start_migration(config: Config) -> bool:
    """A new migration starts now, not at the agent's next session: one run of the agent, two minutes out. A vault that
    declares its schedule gets a one-shot routine named for this version (a one-shot that already ran stays done, so a
    shared name would never run twice), which `migrate-done` removes; without a declared schedule it cannot start.
    One migration runs at a time: while an earlier one is still queued or running, it is left to do the whole pending
    file, which by then spans the newer versions too, rather than a second run working the same vault beside it."""
    from . import schedule
    if schedule.path(config).exists():
        from datetime import datetime, timedelta
        at = (datetime.now(config.zone) + timedelta(minutes=2)).replace(microsecond=0).isoformat()
        name = MIGRATION_ROUTINE + installed_version("agent-brain").replace(".", "-")
        with vault_lock(config):
            routines = schedule.load(config)
            if any(existing.startswith(MIGRATION_ROUTINE) for existing in routines):
                return True
            routines[name] = schedule.Routine(at=at, prompt=MIGRATION_RUN, workdir=str(config.repo))
            schedule.save(config, routines)
        return True
    print(f"migration not started: no declared schedule at {schedule.path(config)}", file=sys.stderr)
    return False


DEFERRED = "deferred-steps.md"


def deferred_path(config: Config) -> Path:
    return config.vault_path / config.runtime_dir / DEFERRED


def deferred_steps(config: Config) -> list[str]:
    path = deferred_path(config)
    return [line[2:].strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ")] if path.exists() else []


def migrate_done(config: Config, deferred: list[str] | None = None) -> str:
    """Records the version. A step the agent could not complete here (a protected file it may not edit unattended) is
    carried for a maintainer in <runtime_dir>/deferred-steps.md, which their sessions and `brain health` show, instead of
    holding the migration open. It is machinery, so it never goes to the owner's Needs you."""
    current = installed_version("agent-brain")
    if deferred:
        path = deferred_path(config)
        text = path.read_text(encoding="utf-8") if path.exists() else (
            "# Deferred migration steps\n\nSteps an unattended migration could not do (a protected file such as SOUL.md "
            "or another protected file). A maintainer approves each in a live session; remove the line once it is done.\n\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip("\n") + "\n" + "".join(f"- {config.today().isoformat()} · {current} · {step}\n"
                                                         for step in deferred), encoding="utf-8")
    record_version(config, current)
    pending = config.vault_path / config.runtime_dir / "pending-migration.md"
    if pending.exists():
        pending.unlink()
    from . import schedule
    with vault_lock(config):
        routines = schedule.load(config) if schedule.path(config).exists() else {}
        if any(name.startswith(MIGRATION_ROUTINE) for name in routines):
            schedule.save(config, {name: routine for name, routine in routines.items() if not name.startswith(MIGRATION_ROUTINE)})
    return current
