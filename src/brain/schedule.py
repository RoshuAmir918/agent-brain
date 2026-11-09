"""The vault's declared routines. The runtime reads this file; only the engine writes it."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from . import fm
from .config import Config
from .responsibilities import ID_PATTERN

HEADER = "# The routines this agent runs. Edit with `brain schedule`; the runtime applies changes within a minute.\n"
TIMING = ("cron", "every", "at", "after")


@dataclass(frozen=True)
class Routine:
    cron: str | None = None
    every: str | None = None
    at: str | None = None
    after: str | None = None   # runs when that routine finishes, ok or failed: the night's steps in order
    skills: list[str] = field(default_factory=list)
    prompt: str | None = None
    deliver: str = "local"
    monitor: str | None = None
    script: str | None = None
    agent: bool = True
    workdir: str | None = None
    enabled: bool = True
    owner: bool = False
    responsibility: str | None = None
    needs: list[str] = field(default_factory=list)


def path(config: Config) -> Path:
    return config.vault_path / config.runtime_dir / "schedule.toml"   # beside engine-version and pending-migration.md


def _workdir(config: Config, value: str | None) -> str | None:
    if isinstance(value, str) and value:
        directory = Path(value).expanduser()
        if (config.repo / directory).resolve() == config.repo.resolve():
            return None
    return value


def _cron(value: str) -> bool:
    """Five standard cron fields, including lists, ranges, steps and month/day names."""
    parts = value.split()
    if len(parts) != 5:
        return False
    for part, (low, high), names in zip(parts, ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7)),
                                       ((), (), (), ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
                                        ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))):
        for index, name in enumerate(names, start=low):
            part = part.upper().replace(name, str(index))
        for item in part.split(","):
            step = item.split("/")
            if len(step) > 2 or len(step) == 2 and (not step[1].isdigit() or int(step[1]) < 1):
                return False
            if step[0] == "*":
                continue
            bounds = step[0].split("-")
            if len(bounds) > 2 or any(not n.isdigit() or not low <= int(n) <= high for n in bounds):
                return False
            if len(bounds) == 2 and int(bounds[0]) > int(bounds[1]):
                return False
    return True


def _problems(raw: dict, *, known_needs: bool = True) -> list[str]:
    out = []
    if set(raw) - {"routines"}:
        out.append("only [routines.<name>] tables are supported")
    routines = raw.get("routines", {})
    if not isinstance(routines, dict):
        return out + ["routines must contain routine tables"]
    for name, values in routines.items():
        where = f"routines.{name}"
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+", name):
            out.append(f"{where}: name must match [a-z0-9-]+")
        if not isinstance(values, dict):
            out.append(f"{where}: must be a table")
            continue
        for key in sorted(values.keys() - Routine.__dataclass_fields__.keys()):
            out.append(f"{where}: unknown field {key}")
        if sum(key in values for key in TIMING) != 1:
            out.append(f"{where}: exactly one of cron, every, at or after is required")
        for key, value in values.items():
            label = f"{where}.{key}"
            if key in ("agent", "enabled", "owner"):
                if not isinstance(value, bool):
                    out.append(f"{label}: must be a boolean")
            elif key in ("skills", "needs"):
                if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                    out.append(f"{label}: must be a list of strings")
                elif key == "needs" and known_needs:
                    out.extend(f"{label}: unknown need {need!r}; only computer is supported"
                               for need in value if need != "computer")
            elif key in Routine.__dataclass_fields__:
                if not isinstance(value, str):
                    out.append(f"{label}: must be a string")
                    continue
                if key == "cron" and not _cron(value):
                    out.append(f"{label}: must be a five-field cron expression")
                elif key == "every" and not re.fullmatch(r"[1-9][0-9]*[mh]", value):
                    out.append(f"{label}: must be <n>m or <n>h with n greater than zero")
                elif key == "at":
                    try:
                        moment = datetime.fromisoformat(value)
                        if moment.tzinfo is None or not re.match(r"\d{4}-\d{2}-\d{2}[Tt ]", value):
                            raise ValueError
                    except ValueError:
                        out.append(f"{label}: must be an ISO datetime with an offset")
                elif key == "after" and value not in routines:
                    out.append(f"{label}: no routine named {value!r}")
                elif key == "deliver" and not re.fullmatch(r"local|demiurge(?::[^\s:]+)?", value):
                    out.append(f"{label}: must be local, demiurge or demiurge:<id>")
                elif key == "responsibility" and not re.fullmatch(ID_PATTERN, value):
                    out.append(f"{label}: must match ^{ID_PATTERN}$")
                elif key in ("monitor", "script", "workdir") and not value.strip():
                    out.append(f"{label}: must be a nonempty string")
        if values.get("agent") is False and not values.get("script"):
            out.append(f"{where}: agent=false requires script")
        # A chain must start from a routine with a time of its own, or nothing in it would ever run.
        seen, step = {name}, values
        while isinstance(step, dict) and isinstance(step.get("after"), str):
            if step["after"] in seen:
                out.append(f"{where}.after: a chain of after leads back to {step['after']}")
                break
            seen.add(step["after"])
            step = routines.get(step["after"])
    return out


def _parse(text: str) -> dict[str, Routine]:
    raw = tomllib.loads(text)
    errors = _problems(raw, known_needs=False)
    if errors:
        raise ValueError("; ".join(errors))
    return {name: Routine(**values) for name, values in raw.get("routines", {}).items()}


def load(config: Config) -> dict[str, Routine]:
    source = path(config)
    return _parse(source.read_text(encoding="utf-8")) if source.exists() else {}


def problems(config: Config, *, known_needs: bool = True) -> list[str]:
    try:
        source = path(config)
        if not source.exists():
            return []
        return [f"{source}: {error}" for error in _problems(tomllib.loads(source.read_text(encoding="utf-8")), known_needs=known_needs)]
    except (OSError, ValueError) as error:
        return [f"{path(config)}: {error}"]


def warnings(config: Config, responsibility_ids: set[str]) -> list[str]:
    source = path(config)
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8")).get("routines", {})
    except (OSError, ValueError):
        return []   # structural problems are reported by problems()
    out = []
    if not isinstance(raw, dict):
        return out
    for name, values in raw.items():
        if not isinstance(values, dict):
            continue
        responsibility = values.get("responsibility")
        if isinstance(responsibility, str) and re.fullmatch(ID_PATTERN, responsibility) and responsibility not in responsibility_ids:
            out.append(f"{source}: routines.{name}.responsibility: unknown responsibility {responsibility!r}")
        needs = values.get("needs", [])
        if isinstance(needs, list):
            out.extend(f"{source}: routines.{name}.needs: unknown need {need!r}; only computer is supported"
                       for need in needs if isinstance(need, str) and need != "computer")
    return out


def render(routines: dict[str, Routine]) -> str:
    raw = {"routines": {name: asdict(routine) for name, routine in routines.items()}}
    # None denotes an absent optional field, never a TOML value.
    raw["routines"] = {name: {key: value for key, value in values.items() if value is not None}
                       for name, values in raw["routines"].items()}
    errors = _problems(raw, known_needs=False)
    if errors:
        raise ValueError("; ".join(errors))
    lines = [HEADER.rstrip()]
    defaults = asdict(Routine())
    for name in sorted(routines):
        lines.extend(("", f"[routines.{name}]"))
        for key, value in raw["routines"][name].items():
            if value == defaults[key] or key == "prompt" and value == f"Run {name}.":
                continue
            # JSON strings, arrays and booleans are also valid TOML values.
            encoded = json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
            lines.append(f"{key} = {encoded}")
    return "\n".join(lines) + "\n"


def save(config: Config, routines: dict[str, Routine]) -> None:
    """Validate before touching the file; caller holds the vault lock for the entire read/edit/write."""
    routines = {name: replace(routine, workdir=_workdir(config, routine.workdir))
                for name, routine in routines.items()}
    text = render(routines)
    _parse(text)
    target = path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(target, text)


def defaults(config: Config) -> dict[str, Routine]:
    """The routines an intellect of this edition starts with: the night's memory passes and the work routines for every
    edition, and the owner's day (people, prime, the morning briefing) for a personal one. Times are the owner's zone."""
    p = config.skill_prefix
    routines = {
        "session-export": Routine(cron="30 0 * * *", prompt="", script="export-sessions.sh", agent=False),
        "attribution": Routine(after="session-export", prompt="", script="attribution.sh", agent=False),
        "night-before": Routine(after="attribution", prompt="", script="night-before.sh", agent=False),
        "sleep1": Routine(after="night-before", skills=[f"{p}sleep1"], prompt="Run Sleep 1 for yesterday."),
        "image": Routine(after="people" if config.edition == "personal" else "sleep1",
                         skills=[f"{p}image", f"{p}portrait"], prompt="Run the image pass."),
        "night-after": Routine(after="image", prompt="", script="night-after.sh", agent=False),
        "context-refresh": Routine(cron="15 * * * *", prompt="", script="refresh.sh", agent=False),
        "improve": Routine(cron="0 7 * * *", skills=[f"{p}improve"], prompt="Run Improve."),
        "upgrade": Routine(cron="0 9 * * *", prompt="", script="upgrade.sh", agent=False),
        "watch": Routine(cron="*/15 6-22 * * *", skills=[f"{p}watch"], deliver="demiurge", monitor="watch-signal.sh"),
        "explore": Routine(cron="30 8-18/2 * * *", skills=[f"{p}explore"], prompt="Run the explore pass.", deliver="demiurge"),
        "evening": Routine(cron="0 20 * * *", skills=[f"{p}evening"], prompt="Run the evening review."),
    }
    if config.edition == "personal":
        routines |= {
            "people": Routine(after="sleep1", skills=[f"{p}people"]),
            "prime": Routine(after="improve", skills=[f"{p}prime"]),
            "morning": Routine(cron="0 8 * * *", skills=[f"{p}morning"], prompt="Run the morning briefing.", deliver="demiurge"),
        }
    return routines


def from_jobs(config: Config, jobs: list[dict]) -> dict[str, Routine]:
    """Import the harness's schedule and execution fields, never its run state."""
    found, seen = {}, {}
    for job in jobs:
        name = job.get("name") or job.get("id")
        timing = job.get("schedule") or {}
        if not isinstance(name, str):
            raise ValueError("runtime job has no routine name")
        if not isinstance(timing, dict):
            raise ValueError(f"{name}: runtime schedule must be an object")
        kind = timing.get("kind")
        once = kind == "once"
        enabled = job.get("enabled", True)
        # Migration jobs and completed one-shots are not recurring routines.
        if name.startswith("brain-migration") or once and not enabled and job.get("last_run_at"):
            continue
        rank = (bool(enabled), str(job.get("last_run_at") or ""))
        if name in found:
            # A runtime may hold two jobs under one name; the enabled, most recently run one is the routine.
            print(f"schedule import: two runtime jobs named {name}; keeping the {'enabled' if rank[0] else 'latest'} one",
                  file=sys.stderr)
            if rank <= seen[name]:
                continue
        if once:
            when = {"at": timing.get("at")}
        elif kind == "after":
            when = {"after": timing.get("routine")}
        elif kind == "cron":
            when = {"cron": timing.get("expr")}
        elif kind == "interval":
            minutes = timing.get("minutes")
            if type(minutes) is not int or minutes <= 0:
                raise ValueError(f"{name}: interval minutes must be a positive integer")
            when = {"every": f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"}
        else:
            raise ValueError(f"{name}: unknown runtime schedule {timing!r}")
        prompt, workdir = job.get("prompt"), job.get("workdir")
        found[name] = Routine(**when, skills=job.get("skills") or [],
                              prompt=None if prompt == f"Run {name}." else prompt,
                              deliver=job.get("deliver", "local"), monitor=job.get("monitor_script"),
                              script=job.get("script"), agent=not job.get("no_agent", False),
                              workdir=_workdir(config, workdir), enabled=enabled)
        seen[name] = rank
    return found
