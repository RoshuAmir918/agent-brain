"""Checks run after every nightly write and on demand."""
from __future__ import annotations

import re
from importlib.resources import files

from . import access, fm
from .vault import Vault
from .tasks import TASK_STATES, is_open, parse_register, read_archived_task


def run(vault: Vault, changed: set[str], body_changed: set[str] | None = None) -> list[str]:
    problems = []
    # 1. frontmatter has the required keys and links resolve on changed notes
    for rel in sorted(changed):
        n = vault.notes.get(rel)
        if not n:
            continue
        kind = vault.kind_for(rel)
        if kind not in ("log", "orientation") and "tier" not in n.data:
            problems.append(f"{rel}: missing frontmatter key tier")
        if kind in ("log", "dream"):
            continue   # exports and fiction are never edited to keep their links alive
        if body_changed is None or rel in body_changed:
            bad = vault.unresolved_links(n.body)
            if bad:
                problems.append(f"{rel}: unresolved wikilinks {bad[:5]}")
    # 2. pinned notes are hot
    for rel, n in vault.notes.items():
        if n.data.get("pinned") and n.data.get("tier") not in (None, "hot"):
            problems.append(f"{rel}: pinned but tier={n.data.get('tier')}")
    # 3. superseded notes carry their header
    for rel, n in vault.notes.items():
        if n.data.get("superseded_by") and "superseded by" not in n.body[:400].lower():
            problems.append(f"{rel}: superseded_by set but no 'superseded by' line at top")
    # 4. no open task left the register without an archive file and an index line
    reg = vault.root / vault.config.agent("Tasks.md")
    arch_dir = vault.root / vault.config.agent("Tasks Archive")
    idx = vault.root / "Warm Index.md"
    if reg.exists() and arch_dir.exists():
        _, tasks = parse_register(fm.read(reg)[1])
        in_register = {t.tid for t in tasks}
        index_text = idx.read_text(encoding="utf-8") if idx.exists() else ""
        for p in arch_dir.glob("T-*.md"):
            tid = p.stem
            task = read_archived_task(vault.config, p)
            if tid in in_register or task is None or not is_open(task):
                continue
            if f"Tasks Archive/{tid}|" not in index_text:
                problems.append(f"{tid}: open task archived but not listed in Warm Index")
    return problems


def task_states(vault: Vault) -> list[str]:
    """On demand only, never a nightly gate: one mistyped state must not stop the night's commit."""
    cfg = vault.config
    reg = vault.root / cfg.agent("Tasks.md")
    arch_dir = vault.root / cfg.agent("Tasks Archive")
    problems = []
    for path in ([reg] if reg.exists() else []) + sorted(arch_dir.glob("*.md")):
        text = fm.read(path)[1]
        if path != reg:
            text = re.sub(r"^#{1,2} (?=T-\d{3} — )", "### ", text, flags=re.M)
        for task in parse_register(text)[1]:
            if "state" in task.state and task.task_state not in TASK_STATES:
                problems.append(f"{task.tid}: unknown task state '{task.state['state']}'")
    return problems


def access_problems(vault: Vault) -> list[str]:
    """On demand only: invalid access grants must never gate the nightly pass."""
    config = vault.config
    vocabulary = access.subjects(vault, config)
    problems = []
    seen = set()
    for rule in access.rules(vault, config):
        if rule.legacy:
            continue   # the old allow/deny rows are read as they are until the 0.14 migration rewrites them
        prefix = f"{config.people_dir}/Disclosure.md: {rule.id}"
        if rule.id in seen:
            problems.append(f"{prefix}: duplicate id")
        seen.add(rule.id)
        if rule.subject not in vocabulary:
            problems.append(f"{prefix}: unknown subject '{rule.subject}'")
        for dimension, levels in (("tell", access.TELL_LEVELS), ("do", access.DO_LEVELS)):
            value = getattr(rule, dimension)
            if value is not None and value not in levels:
                problems.append(f"{prefix}: unknown {dimension} level '{value}'")
        if rule.audience is None:
            problems.append(f"{prefix}: audience resolves to nothing: {rule.line.strip()}")
        for field in ("date", "source"):
            if not rule.fields.get(field):
                problems.append(f"{prefix}: missing {field}")
    for rel, note in vault.notes.items():
        subject = note.data.get("subject")
        if subject is not None and vault.kind_for(rel) == "hub" and subject not in vocabulary:
            problems.append(f"{rel}: unknown subject '{subject}'; add it under ## Subjects in Disclosure.md or use one there")
    for rel, members in access.groups(vault, config).items():
        if not members:
            problems.append(f"{rel}: group has no members")
    return problems


OWNER_PROFILE_WORDS = 500


def owner_profile(vault: Vault) -> list[str]:
    """On demand only: the owner's profile is loaded into their sessions, so it has to stay short."""
    rel = vault.config.human_profile
    note = vault.notes.get(rel)
    words = len(note.body.split()) if note else 0
    return [f"{rel}: {words} words; keep it under {OWNER_PROFILE_WORDS}, dated material in History.md"] \
        if words > OWNER_PROFILE_WORDS else []


def responsibilities(vault: Vault) -> list[str]:
    """Every own skill is a responsibility: a hub at the vault root names it and its records folder. On demand only; the nightly write is never blocked on it."""
    problems = []
    packaged = {p.name for p in files("brain").joinpath("skills").iterdir()}
    hubs = {n.data.get("skill"): rel for rel, n in vault.notes.items() if n.data.get("skill")}
    for rel, n in vault.notes.items():
        if n.data.get("skill") and not (vault.root / vault.config.hermes_dir / "skills" / n.data["skill"] / "SKILL.md").is_file():
            problems.append(f"{rel}: skill {n.data['skill']} has no SKILL.md under {vault.config.hermes_dir}/skills")
        if n.data.get("skill") and not n.data.get("records"):
            problems.append(f"{rel}: responsibility hub without a records folder")
    skills_dir = vault.root / vault.config.hermes_dir / "skills"
    if skills_dir.is_dir():
        prefix = vault.config.skill_prefix
        for folder in sorted(skills_dir.iterdir()):
            if not (folder / "SKILL.md").is_file():
                continue
            routine = folder.name[len(prefix):] if folder.name.startswith(prefix) else folder.name
            if routine not in packaged and folder.name not in hubs:
                problems.append(f"{vault.config.hermes_dir}/skills/{folder.name}: own skill without a responsibility hub (frontmatter skill: {folder.name})")
    return problems


KINDS = ("hub", "profile", "group", "episode", "reflection", "task", "trigger", "log", "dream", "orientation", "primed",
         "draft", "conversation", "procedure")


def kinds(vault: Vault) -> list[str]:
    """On demand only, never a nightly gate: vaults written before a kind was declared keep committing."""
    return [f"{rel}: undeclared kind {kind!r}" for rel, note in sorted(vault.notes.items())
            if (kind := note.data.get("kind")) is not None and kind not in KINDS]
