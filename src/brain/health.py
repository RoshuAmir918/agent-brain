"""Operational report for a vault: stage runs, engine state, export freshness and counts. Never note contents."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path

from . import fm, install, validate
from .config import Config
from .tasks import is_open, parse_register
from .vault import Vault

STAGES = ("archive", "attribution", "sleep1", "rem", "sleep2", "people", "context")
STAGE_RE = re.compile(r"^(archive|attribution|sleep1|rem|sleep2|people|context)\((\d{4}-\d{2}-\d{2})\)")
COVERAGE_RE = re.compile(r"^coverage: (.*)$", re.M)
KNOWLEDGE = ("hub", "profile", "reflection", "episode")


def git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def stages(repo: Path, today: date, now: datetime) -> dict[str, dict]:
    """Last recorded run of each night stage, from commit subjects; ok when it covers yesterday or later."""
    last: dict[str, str] = {}
    for line in (git(repo, "log", f"--since={(now - timedelta(days=21)).isoformat(timespec='seconds')}", "--format=%s") or "").splitlines():
        match = STAGE_RE.match(line)
        if match and match.group(1) not in last:
            last[match.group(1)] = match.group(2)
    floor = (today - timedelta(days=1)).isoformat()
    return {stage: {"last": last.get(stage),
                    "state": "never" if stage not in last else ("ok" if last[stage] >= floor else "stale")}
            for stage in STAGES}


def report(config: Config, today: date | None = None) -> dict:
    today = today or config.today()
    vault = Vault(config.vault_path)
    yesterday = (today - timedelta(days=1)).isoformat()
    digest = config.vault_path / "Conversations" / "Digests" / f"{yesterday}.md"
    coverage = None
    if digest.exists():
        match = COVERAGE_RE.search(digest.read_text(encoding="utf-8"))
        coverage = match.group(1) if match else "no coverage line"
    tiers = {"hot": 0, "warm": 0, "cold": 0}
    for rel, note in vault.notes.items():
        if vault.kind_for(rel) in KNOWLEDGE and note.data.get("tier") in tiers:
            tiers[note.data["tier"]] += 1
    tasks = config.vault_path / config.agent("Tasks.md")
    open_tasks = sum(1 for task in parse_register(fm.read(tasks)[1])[1] if is_open(task)) if tasks.exists() else 0
    pending = config.vault_path / config.hermes_dir / "pending-migration.md"
    pending_since = None
    if pending.exists():
        rel = pending.relative_to(config.repo).as_posix()
        # The commit that added this file, not the last one touching the path: that can be the deletion of an older one.
        tracked = not (git(config.repo, "status", "--porcelain", "--", rel) or "").startswith(("??", "A"))
        pending_since = (tracked and git(config.repo, "log", "-1", "--diff-filter=A", "--format=%cs", "--", rel)) or "uncommitted"
    try:
        installed = installed_version("agent-brain")
    except PackageNotFoundError:
        installed = "unknown"
    status = git(config.repo, "status", "--porcelain")
    counts = (git(config.repo, "rev-list", "--left-right", "--count", "origin/main...HEAD") or "? ?").split()
    return {
        "agent": config.agent_name,
        "date": today.isoformat(),
        "stages": stages(config.repo, today, config.now()),
        "engine": {"installed": installed, "recorded": install.recorded_version(config),
                   "pending_migration_since": pending_since},
        "export": {"expected": yesterday, "digest": yesterday if digest.exists() else None, "coverage": coverage},
        "vault": {"validation_problems": len(validate.run(vault, set(vault.notes))), "notes": tiers,
                  "open_tasks": open_tasks},
        "git": {"dirty": None if status is None else len(status.splitlines()), "behind": counts[0], "ahead": counts[1]},
    }


def render(data: dict, *, include_git: bool = True) -> str:
    stage_text = ", ".join(f"{stage} {entry['last'] or 'never'} {entry['state']}" for stage, entry in data["stages"].items())
    engine = data["engine"]
    engine_text = f"{engine['installed']} installed, {engine['recorded']} recorded"
    if engine["pending_migration_since"]:
        engine_text += f", migration pending since {engine['pending_migration_since']}"
    export = data["export"]
    export_text = f"digest {export['digest']}, {export['coverage']}" if export["digest"] else f"no digest for {export['expected']}"
    vault = data["vault"]
    notes = vault["notes"]
    lines = [f"# Health: {data['agent']}, {data['date']}", "",
             f"Stages: {stage_text}",
             f"Engine: {engine_text}",
             f"Export: {export_text}",
             f"Vault: {vault['validation_problems']} validation problems; {notes['hot']} hot, {notes['warm']} warm, "
             f"{notes['cold']} cold notes; {vault['open_tasks']} open tasks"]
    if include_git:
        git_state = data["git"]
        tree = "no git repository" if git_state["dirty"] is None else (
            "clean" if git_state["dirty"] == 0 else f"{git_state['dirty']} uncommitted")
        lines.append(f"Git: {tree}, {git_state['ahead']} ahead, {git_state['behind']} behind origin/main")
    return "\n".join(lines) + "\n"


def write(config: Config, data: dict) -> Path:
    """The committed copy: stable across the hour, so refreshes only commit real change."""
    path = config.vault_path / config.hermes_dir / "health.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\nkind: log\n---\n" + render(data, include_git=False)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def to_json(data: dict) -> str:
    return json.dumps(data, indent=2)
