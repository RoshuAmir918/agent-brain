"""Checks run after every nightly write and on demand."""
from __future__ import annotations

from importlib.resources import files

from . import fm
from .vault import Vault
from .tasks import parse_register, read_archived_task


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
            if tid in in_register or read_archived_task(vault.config, p) is None:
                continue
            if f"Tasks Archive/{tid}|" not in index_text:
                problems.append(f"{tid}: open task archived but not listed in Warm Index")
    return problems


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
