"""Responsibility identities and ownership of declared routines."""
from __future__ import annotations

import re
import shutil
from contextlib import nullcontext
from copy import copy
from dataclasses import replace
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from .config import Config
from . import fm
from .lock import vault_lock
from .vault import Note, Vault

if TYPE_CHECKING:
    from .schedule import Routine

ID_PATTERN = r"[a-z0-9][a-z0-9-]{0,62}"


def identity(config: Config, note: Note) -> str:
    value = note.data.get("records") or (note.data.get("skill") or "").removeprefix(config.skill_prefix) or note.stem
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:63].rstrip("-")


def discover(vault: Vault) -> list[dict]:
    out = []
    try:   # `path` is from the repository root, where the runtime and the agent work
        base = PurePosixPath(vault.config.vault_path.resolve().relative_to(vault.config.repo.resolve()).as_posix())
    except ValueError:
        base = PurePosixPath(vault.config.vault_path.as_posix())
    for source, note in sorted(vault.notes.items()):
        path = PurePosixPath(source)
        if len(path.parts) == 3 and path.parts[0] == "Responsibilities" and path.name == "RESPONSIBILITY.md":
            ident = path.parent.name
            out.append({"id": ident, "title": note.data.get("title") or ident.replace("-", " ").capitalize(),
                        "hub": source, "skill": note.data.get("skill"),
                        "records": note.data.get("records", f"Responsibilities/{ident}/records"),
                        "routines": [], "needs": note.data.get("needs", []), "work": note.data.get("work"),
                        "delivers": note.data.get("delivers"), "folder": True, "path": (base / source).as_posix(),
                        **{key: note.data.get(key) for key in ("triggers", "asked_by", "goal", "role", "version")},
                        **({"learned": note.data["learned"]} if note.data.get("learned") else {})})
            continue
        if vault.kind_for(source) != "hub" or note.data.get("kind", "hub") != "hub":
            continue
        # A responsibility is a hub with its own skill (the engine's definition everywhere else); a hub without one is
        # a folder index or a context note, never something the intellect keeps up.
        if not note.data.get("skill"):
            continue
        out.append({"id": identity(vault.config, note), "title": note.stem, "hub": source,
                    "skill": note.data.get("skill"), "records": note.data.get("records"),
                    "routines": [], "needs": [], "work": note.data.get("work"), "delivers": note.data.get("delivers"),
                    "folder": False, "path": (base / source).as_posix()})
    return out


def derive(config: Config, entry: dict) -> dict[str, Routine]:
    # The runtime and the agent work from the repository root, which can hold the vault in a subfolder (memory/).
    try:
        base = PurePosixPath(config.vault_path.resolve().relative_to(config.repo.resolve()).as_posix())
    except ValueError:
        base = PurePosixPath(config.vault_path.as_posix())

    def repo_path(relative) -> str:
        return (base / relative).as_posix()

    from . import schedule
    ident = entry["id"]
    if not re.fullmatch(ID_PATTERN, ident):
        raise ValueError(f"responsibility id must match ^{ID_PATTERN}$")
    if type(entry.get("version")) is not int or entry["version"] != 1:
        raise ValueError("version: required and must be 1")
    triggers = entry.get("triggers")
    if not isinstance(triggers, list):
        raise ValueError("triggers must be a list of maps with a kind")
    needs = entry.get("needs", [])
    if not isinstance(needs, list) or not all(isinstance(need, str) for need in needs):
        raise ValueError("needs must be a list of strings")
    skill = entry.get("skill")
    if skill is not None and (not isinstance(skill, str) or not skill.strip()):
        raise ValueError("skill must be a nonempty string")
    learned = entry.get("learned") or []
    if not isinstance(learned, list) or not all(isinstance(name, str) and name.strip() for name in learned):
        raise ValueError("learned must be a list of field names")
    out, scheduled = {}, 0
    for index, trigger in enumerate(triggers, 1):
        where = f"triggers[{index}]"
        if not isinstance(trigger, dict) or not isinstance(trigger.get("kind"), str) or not trigger["kind"].strip():
            raise ValueError(f"{where}: must be a map with a kind")
        if trigger["kind"] != "schedule":
            continue
        scheduled += 1
        when = trigger.get("when")
        timing = {}
        if isinstance(when, str):
            if schedule._cron(when):
                timing = {"cron": when}
            elif re.fullmatch(r"every [1-9][0-9]*[mh]", when):
                timing = {"every": when.removeprefix("every ")}
            elif when.startswith("once "):
                value = when.removeprefix("once ")
                try:
                    moment = datetime.fromisoformat(value)
                    if not re.match(r"\d{4}-\d{2}-\d{2}[Tt ]", value):
                        raise ValueError
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=config.zone)
                    timing = {"at": moment.isoformat()}
                    # A one-off a day past its time has run (or never will): its routine goes, the trigger stays as
                    # history. A day's grace lets a harness that was down catch up first.
                    if moment < config.local() - timedelta(days=1):
                        timing = {"spent": True}
                except ValueError:
                    pass
        if not timing:
            raise ValueError(f"{where}.when: use a five-field cron expression, every <n>m/<n>h or once <ISO datetime>")
        name = ident if scheduled == 1 else f"{ident}-{scheduled}"
        if timing.get("spent"):
            continue
        script = trigger.get("script")
        if script is not None:
            if (not isinstance(script, str) or not script.strip() or PurePosixPath(script).is_absolute()
                    or ".." in PurePosixPath(script).parts or PurePosixPath(script) == PurePosixPath(".")):
                raise ValueError(f"{where}.script: must be a path relative to the responsibility folder")
            script = repo_path(PurePosixPath(entry["hub"]).parent / script)
        prompt = (f"Your responsibility {entry['title']}: read {repo_path(entry['hub'])} and do what it says. "
                  f"Its state between runs lives in {repo_path(entry['records'])}."
                  + (" How to do this work well, from what you have learned: "
                     + ", ".join(f"`brain learn show \"{name}\"`" for name in learned) + "; read them first." if learned else ""))
        if "prompt" in trigger:
            if not isinstance(trigger["prompt"], str):
                raise ValueError(f"{where}.prompt: must be a string")
            if trigger["prompt"]:
                prompt += "\n\n" + trigger["prompt"]
        extra_needs = trigger.get("needs", [])
        if not isinstance(extra_needs, list) or not all(isinstance(need, str) for need in extra_needs):
            raise ValueError(f"{where}.needs: must be a list of strings")
        deliver = trigger.get("deliver", "demiurge")
        if not isinstance(deliver, str):
            raise ValueError(f"{where}.deliver: must be a string")
        out[name] = schedule.Routine(**timing, responsibility=ident, skills=[skill] if skill else [],
                                     prompt=prompt, deliver=deliver,
                                     needs=sorted(set(needs + extra_needs)), script=script, agent=trigger.get("agent", True))
    schedule.render(out)
    return out


def sync(vault: Vault, *, locked: bool = False) -> list[str]:
    """Sync under the vault lock, or reuse the caller's lock when locked=True."""
    from . import schedule
    with nullcontext() if locked else vault_lock(vault.config):
        vault.load()
        routines, changes = _synced(vault, schedule.load(vault.config))
        if any(not change.startswith("kept ") for change in changes):
            schedule.save(vault.config, routines)
        return changes


def _synced(vault: Vault, routines: dict[str, Routine]) -> tuple[dict[str, Routine], list[str]]:
    entries = discover(vault)
    folders = {entry["id"]: entry for entry in entries if entry["folder"]}
    known = {entry["id"] for entry in entries}
    derived, changes = {}, []
    for ident, entry in list(folders.items()):
        # One broken wake-up document must not stop every other responsibility's schedule: it keeps the routines
        # it last synced to, and validate names the problem.
        try:
            declared = derive(vault.config, entry)
            if declared.keys() & derived.keys():
                raise ValueError("derived routine name is already used by another responsibility")
        except ValueError as error:
            del folders[ident]
            changes.append(f"kept {ident} as it was: {entry['hub']}: {error}")
            continue
        derived.update(declared)
    routines = dict(routines)
    for name, routine in list(routines.items()):
        ident = routine.responsibility
        if ident is None:
            continue
        removed = (ident not in known and not (vault.root / "Responsibilities" / ident).exists()
                   and re.fullmatch(re.escape(ident) + r"(?:-(?:[2-9]|[1-9][0-9]+))?", name))
        one_off = routine.at is not None and name not in derived   # scheduled by hand for one run; prune removes it
        if ident in folders and not one_off and (name not in derived or derived[name].responsibility != ident) or removed:
            del routines[name]
            changes.append(f"removed routine {name}")
    for name, routine in derived.items():
        old = routines.get(name)
        if old is not None:
            routine = replace(routine, enabled=old.enabled)
        if routine != old:
            routines[name] = routine
            changes.append(f"{'updated' if old is not None else 'added'} routine {name}")
    return routines, changes


def link(entries: list[dict], routines: dict[str, Routine]) -> dict[str, str]:
    """Explicit ownership wins; fallback names precede skills, then hub path breaks ties."""
    by_id = {}
    for entry in entries:
        by_id.setdefault(entry["id"], entry)
    explicit = {routine.responsibility for routine in routines.values() if routine.responsibility is not None}
    owners = {}
    for name, routine in sorted(routines.items()):
        if routine.responsibility is not None:
            entry = by_id.get(routine.responsibility)
        else:
            candidates = [entry for entry in by_id.values() if entry["id"] not in explicit]
            entry = next((entry for entry in candidates if entry["id"] == name), None)
            if entry is None:
                entry = next((entry for entry in candidates if entry["skill"] and entry["skill"] in routine.skills), None)
        if entry is not None:
            owners[name] = entry["id"]
            entry["routines"].append(name)
            declared = entry["needs"] if isinstance(entry["needs"], list) else []
            entry["needs"] = sorted({need for need in declared if isinstance(need, str)} | set(routine.needs))
    return owners


def adopt(config: Config, names: list[str], *, hub: str | None = None, title: str | None = None,
          into: str | None = None, asked_by: str | None = None, goal: str | None = None,
          role: str | None = None) -> list[str]:
    """Preflight a conversion, then write and sync it as one locked, reversible operation."""
    from . import goals, install, nightly, roles, schedule

    with vault_lock(config):
        vault = Vault(config.vault_path)
        routines = schedule.load(config)
        entries = discover(vault)
        owners = link(entries, routines)
        packaged = {path.name for path in files("brain").joinpath("skills").iterdir()}
        # demiurge-sync is the platform's own routine, installed by the deploy rather than the engine.
        engine = set().union(*(schedule.defaults(replace(config, edition=edition)) for edition in ("personal", "enterprise")),
                             {"demiurge-sync", "demiurge-whoami"})
        old_hub, own_folder, existing_note = None, None, None
        existing_derived = {}

        missing = Path()

        def own_skill(skill: str) -> Path | None:
            if not isinstance(skill, str) or not skill or Path(skill).name != skill or skill in (".", ".."):
                raise ValueError(f"invalid skill name {skill!r}; needs a hand conversion")
            if skill.removeprefix(config.skill_prefix) in packaged:
                return None
            directory = config.skills_path / skill
            if not (directory / "SKILL.md").is_file():
                return missing   # gone already, e.g. a hub's own skill that an earlier adoption folded into its folder
            return directory

        if hub is not None:
            if names or any(value is not None for value in (title, into, asked_by, goal, role)):
                raise ValueError("--hub takes no routines, --into, title or reason flags")
            source = Path(hub).expanduser()
            if source.is_absolute():
                try:
                    source = source.resolve().relative_to(vault.root)
                except ValueError:
                    raise ValueError(f"hub must be at the vault root: {hub}") from None
            source = Path(str(source) + ".md") if not source.name.endswith(".md") else source
            entry = next((entry for entry in entries if not entry["folder"] and entry["hub"] == source.as_posix()), None)
            if entry is None or len(source.parts) != 1:
                raise ValueError(f"no old-style responsibility hub at the vault root: {hub}")
            old_hub = vault.notes[entry["hub"]]
            own_folder = own_skill(entry["skill"])
            if own_folder is missing:
                raise ValueError(f"skill {entry['skill']!r} has no SKILL.md; needs a hand conversion")
            if own_folder is None:
                raise ValueError(f"{hub}: hub must name an own skill, not an engine skill")
            ident, title = entry["id"], old_hub.stem
            names = [name for name in entry["routines"] if routines[name].at is None]
            excluded = {"kind", "tier", "skill", "records", *nightly.NIGHT_KEYS}
            data = {key: value for key, value in old_hub.data.items() if key not in excluded}
            if not any(data.get(key) for key in ("asked_by", "goal", "role")):
                data["asked_by"] = "origin unknown"
            records = old_hub.data.get("records")
            if records:
                location = install.records_path() / records
                if location.is_dir():
                    data["records"] = str(location.resolve())
            body = old_hub.body.rstrip() + "\n\n## Method\n\n" + fm.read(own_folder / "SKILL.md")[1].strip() + "\n"
        else:
            if not names:
                raise ValueError("adopt requires at least one routine or --hub")
            if into is not None and any(value is not None for value in (title, asked_by, goal, role)):
                raise ValueError("--into takes no --title or reason flags; the existing document keeps its reason")
            if sum(value is not None for value in (asked_by, goal, role)) > 1:
                raise ValueError("adopt takes only one of --asked-by, --goal or --role")
            if len(set(names)) != len(names):
                raise ValueError("adopt each routine only once")
            if into is not None:
                entry = next((entry for entry in entries if entry["id"] == into and entry["folder"]), None)
                if entry is None:
                    raise ValueError(f"unknown folder responsibility: {into}")
                existing_note = vault.notes[entry["hub"]]
                existing_derived = derive(config, entry)
                ident = into
                data, body = dict(existing_note.data), existing_note.body
            else:
                title = title if title is not None else names[0]
                ident = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:63].rstrip("-")
                data = {key: value for key, value in (("asked_by", asked_by), ("goal", goal), ("role", role)) if value is not None}
                if not data:
                    data["asked_by"] = "origin unknown"
                body = (f"# {title}\n\nAdopted from the routines {', '.join(names)} on {config.today()}; "
                        "rewrite this into what it is for, how to do it, and what each run leaves visible.\n")
            if asked_by is not None and not asked_by.strip():
                raise ValueError("--asked-by must name a reason")
            if goal is not None and goal not in {item.gid for item in goals.load(config) + goals.archived(config)}:
                raise ValueError(f"unknown goal {goal!r}; use `brain goals list`")
            if role is not None:
                known = {fm.read(roles.folder(config, name) / roles.ROLE_NOTE)[0].get("id") for name in roles.names(config)}
                if not re.fullmatch(r"R-[0-9]+", role) or role not in known:
                    raise ValueError(f"unknown role {role!r}; use `brain roles list`")

        if not re.fullmatch(ID_PATTERN, ident):
            raise ValueError(f"responsibility id must match ^{ID_PATTERN}$")
        target = vault.root / "Responsibilities" / ident / "RESPONSIBILITY.md"
        if existing_note is None and target.parent.exists():
            raise ValueError(f"responsibility folder already exists: Responsibilities/{ident}")
        original_note = old_hub or existing_note
        if any(entry["id"] == ident and (original_note is None or entry["hub"] != original_note.rel) for entry in entries):
            raise ValueError(f"responsibility id already exists: {ident}")

        triggers = list(data["triggers"]) if existing_note else []
        scheduled = sum(trigger["kind"] == "schedule" for trigger in triggers)
        copied, skills = {}, []

        def copy_file(source: Path, relative: Path) -> None:
            content, mode = source.read_bytes(), source.stat().st_mode & 0o777
            if relative in copied and copied[relative][0] != content:
                raise ValueError(f"conflicting file content: {target.parent / relative}")
            copied[relative] = (content, mode)

        for name in names:
            if name not in routines:
                raise ValueError(f"unknown routine: {name}")
            routine = routines[name]
            # A routine naming a responsibility that doesn't exist (a typo, a renamed hub) has no owner to keep.
            owned = routine.responsibility is not None and any(entry["id"] == routine.responsibility for entry in entries)
            if old_hub is None and (owned or name in owners):
                raise ValueError(f"{name}: already has a responsibility")
            if old_hub is None and (name in engine or name.startswith(install.MIGRATION_ROUTINE)):
                raise ValueError(f"{name}: engine routine; stays plain")
            if routine.after is not None:
                raise ValueError(f"{name}: after routines are engine plumbing; stay plain")
            if routine.monitor is not None or routine.classify:
                raise ValueError(f"{name}: monitor or classify routines stay plain routines")
            if schedule._workdir(config, routine.workdir) is not None:
                raise ValueError(f"{name}: workdir is outside the vault repo; needs a hand conversion")
            when = routine.cron if routine.cron is not None else f"every {routine.every}" if routine.every else f"once {routine.at}"
            trigger = {"kind": "schedule", "when": when}
            if routine.prompt and routine.prompt != f"Run {name}.":
                trigger["prompt"] = routine.prompt
            if routine.deliver != "demiurge":
                trigger["deliver"] = routine.deliver
            if routine.needs:
                trigger["needs"] = routine.needs
            if not routine.agent:
                trigger["agent"] = False
            if routine.script is not None:
                script = Path(routine.script)
                if script.name != routine.script or script.name in (".", ".."):
                    raise ValueError(f"{name}: script must be a bare name; needs a hand conversion")
                source = install.scripts_path() / script
                if not source.is_file():
                    raise ValueError(f"{name}: missing script file {source}")
                copy_file(source, Path("scripts") / script)
                trigger["script"] = f"scripts/{script.name}"
            triggers.append(trigger)
            if old_hub is None and routine.prompt:
                body += f"\n## Prompt ({name})\n\n{routine.prompt}\n"
            for skill in routine.skills:
                if skill not in skills and (own_folder is None or skill != own_folder.name):
                    skills.append(skill)

        for skill in skills:
            directory = own_skill(skill)
            if directory is missing:
                body += f"\nIts routine named the skill `{skill}`, which no longer exists.\n"
            elif directory is not None:
                body += f"\n## Method (from {skill})\n\n{fm.read(directory / 'SKILL.md')[1].strip()}\n"
            elif existing_note is None and "skill" not in data:
                data["skill"] = skill
            else:
                body += f"\nAlso follow the engine skill `{skill}`.\n"
        if existing_note:
            data["triggers"] = triggers
        else:
            data.update(version=1, title=title, triggers=triggers)
        text = fm.join(data, body)
        if own_folder:
            for source in sorted(own_folder.rglob("*")):
                if source.is_file() and source != own_folder / "SKILL.md":
                    copy_file(source, source.relative_to(own_folder))
        for relative, (content, _) in copied.items():
            destination = target.parent / relative
            if (relative == Path("RESPONSIBILITY.md") or Path("RESPONSIBILITY.md") in relative.parents
                    or any(parent in copied for parent in relative.parents)
                    or destination.exists() and (not destination.is_file() or destination.read_bytes() != content)
                    or any(parent.exists() and not parent.is_dir() for parent in destination.parents)):
                raise ValueError(f"conflicting file content: {destination}")
        entry = {**data, "id": ident, "title": data.get("title") or ident.replace("-", " ").capitalize(),
                 "hub": target.relative_to(vault.root).as_posix(),
                 "records": data.get("records", f"Responsibilities/{ident}/records")}
        derived = derive(config, entry)
        remaining = {name: routine for name, routine in routines.items() if name not in names}
        schedule.render(remaining)
        collisions = derived.keys() & remaining.keys()
        collisions -= {name for name in existing_derived if name in remaining and remaining[name].responsibility == ident}
        for existing in entries:
            if existing["folder"] and existing["hub"] != entry["hub"]:
                try:
                    collisions |= derived.keys() & derive(config, existing).keys()
                except ValueError:
                    pass   # a broken document keeps the routines it last synced to, which `remaining` already holds
        if collisions:
            raise ValueError(f"derived routine name already exists: {', '.join(sorted(collisions))}")

        preview = copy(vault)
        preview.notes = dict(vault.notes)
        preview.notes[entry["hub"]] = Note(entry["hub"], target, data, body)
        hub_data = {key: value for key, value in old_hub.data.items() if key not in ("skill", "records")} if old_hub else None
        if old_hub:
            preview.notes[old_hub.rel] = replace(old_hub, data=hub_data)
        planned, changes = _synced(preview, remaining)
        # Another responsibility's broken document is validate's to report; only this one's blocks the adoption.
        if mine := [change for change in changes if change.startswith(f"kept {ident} ")]:
            raise ValueError("; ".join(mine))
        enabled = {name: routines[name].enabled if existing_note is None and name in names else routines[source].enabled
                   for index, source in enumerate(names, scheduled + 1)
                   if (name := ident if index == 1 else f"{ident}-{index}") in derived}
        schedule.render({name: replace(routine, enabled=enabled.get(name, routine.enabled)) for name, routine in planned.items()})

        schedule_path = schedule.path(config)
        originals = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
                     for path in [schedule_path, target, *(target.parent / relative for relative in copied),
                                  *([old_hub.path] if old_hub else [])]}
        missing_dirs = sorted({path for parent in {path.parent for path in originals}
                               for path in (parent, *parent.parents) if not path.exists()},
                              key=lambda path: len(path.parts), reverse=True)
        removed_files = sorted(path for path in own_folder.rglob("*") if path.is_file() or path.is_symlink()) if own_folder else []
        skill_link = own_folder.readlink() if own_folder is not None and own_folder.is_symlink() else None
        with TemporaryDirectory(prefix="brain-adopt-") as backup:
            skill_backup = Path(backup) / "skill"
            if own_folder and skill_link is None:
                shutil.copytree(own_folder, skill_backup, symlinks=True)
            try:
                schedule.save(config, remaining)
                target.parent.mkdir(parents=True, exist_ok=True)
                fm.write_text(target, text, originals[target][1] if originals[target] else None)
                for relative, (content, mode) in copied.items():
                    destination = target.parent / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    fm.write_text(destination, content, mode)
                if old_hub:
                    fm.write(old_hub.path, hub_data, f"See [[Responsibilities/{ident}/RESPONSIBILITY]].\n")
                    if skill_link is not None:
                        own_folder.unlink()
                    else:
                        shutil.rmtree(own_folder)
                changes = sync(vault, locked=True)
                if mine := [change for change in changes if change.startswith(f"kept {ident} ")]:
                    raise ValueError("; ".join(mine))
                synced = schedule.load(config)
                schedule.save(config, {name: replace(routine, enabled=enabled.get(name, routine.enabled))
                                       for name, routine in synced.items()})
            except Exception:
                if own_folder:
                    if own_folder.is_symlink():
                        own_folder.unlink()
                    elif own_folder.exists():
                        shutil.rmtree(own_folder)
                    if skill_link is not None:
                        own_folder.symlink_to(skill_link, target_is_directory=True)
                    else:
                        shutil.copytree(skill_backup, own_folder, symlinks=True)
                for path, original in originals.items():
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(original[0])
                        path.chmod(original[1])
                for directory in missing_dirs:
                    if directory.exists():
                        directory.rmdir()
                raise
        return ([f"wrote {target}"] + [f"wrote {target.parent / relative}" for relative in copied]
                + ([f"wrote {old_hub.path}"] if old_hub else [])
                + [f"removed {path}" for path in removed_files] + [f"wrote {schedule_path}"]
                + [f"removed routine {name}" for name in names] + changes)
