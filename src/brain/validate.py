"""Checks run after every nightly write and on demand."""
from __future__ import annotations

import re
from importlib.resources import files

from . import access, fm
from .vault import Vault
from .tasks import TASK_STATES, parse_register


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


def primed_size(vault: Vault) -> list[str]:
    from .compile_context import PRIMED_WORDS
    rel = vault.config.agent("Primed.md")
    note = vault.notes.get(rel)
    words = len(note.body.split()) if note else 0
    return [f"{rel}: {words} words; at most {PRIMED_WORDS}, and the context loads only that many"] if words > PRIMED_WORDS else []


def owner_questions(vault: Vault) -> list[str]:
    """What the owner sees is written for them: a task waiting on them, and a proposed improvement, carry an Ask line
    (a proposition) and a Why line (what they get), within their word caps."""
    from . import needs
    from .tasks import load_tasks
    out = []

    def check(where: str, body: str) -> None:
        ask = needs._task_field(body, "Ask")
        why = needs._task_field(body, "Why") or ""
        if not ask:
            out.append(f"{where}: no Ask line, the proposition the owner sees (and a Why line, what they get)")
            return
        if len(ask.split()) > needs.ASK_WORDS:
            out.append(f"{where}: Ask line is {len(ask.split())} words; at most {needs.ASK_WORDS}")
        if len(why.split()) > needs.WHY_WORDS:
            out.append(f"{where}: Why line is {len(why.split())} words; at most {needs.WHY_WORDS}")

    if (vault.config.vault_path / vault.config.agent("Tasks.md")).exists():
        registered, archived = load_tasks(vault.config)
        for task in registered + archived:
            if task.task_state == "waiting_for_owner":
                check(task.tid, task.body)
    for rel, note in sorted(vault.notes.items()):
        if vault.kind_for(rel) != "hub":
            continue
        span = needs._section(note.body, "Improvements")
        if span is None:
            continue
        section = note.body[span[0]:span[1]]
        headings = list(re.finditer(r"^### ([^\r\n]+)", section, re.M))
        for index, heading in enumerate(headings):
            stop = headings[index + 1].start() if index + 1 < len(headings) else len(section)
            entry = section[heading.end():stop]
            if needs.improvement_status(entry) == "proposed":
                check(f"{rel} · {heading[1].strip()}", entry)
    return out


def needs_ceiling(vault: Vault) -> list[str]:
    from . import needs
    note = vault.notes.get("CURRENT.md")
    asks = needs.open_asks(note.body) if note else []
    if len(asks) <= needs.OPEN_ASKS:
        return []
    return [f"CURRENT.md: {len(asks)} open asks in Needs you; the evening decides the lowest-ranked down to {needs.OPEN_ASKS} (`brain needs-you --overflow`)"]


def hygiene(vault: Vault) -> list[str]:
    """The checks beyond structure. The nightly reports them without gating on them, so a problem that creeps back is
    seen the next day rather than at the next migration."""
    return (responsibilities(vault) + task_states(vault) + kinds(vault) + access_problems(vault) + owner_profile(vault)
            + channels(vault) + identity_duplicates(vault) + needs_ceiling(vault) + needs_hygiene(vault)
            + primed_size(vault) + owner_questions(vault) + ideas_hygiene(vault))


def ideas_hygiene(vault: Vault) -> list[str]:
    from . import ideas, needs
    rel = vault.config.agent("Ideas.md")
    note = vault.notes.get(rel)
    out = []
    for item in ideas.parse_section(note.body, cut=False) if note else []:
        where = f"{rel} · {item.text}"
        if not item.detail:
            out.append(f"{where}: no why line, what the owner gets")
        if len(item.text.split()) > needs.ASK_WORDS:
            out.append(f"{where}: idea is {len(item.text.split())} words; at most {needs.ASK_WORDS}")
        if len(item.detail.split()) > needs.WHY_WORDS:
            out.append(f"{where}: why line is {len(item.detail.split())} words; at most {needs.WHY_WORDS}")
    return out


def checks(vault: Vault) -> list[str]:
    """Everything `brain validate` names, which `brain health` counts: structure, then hygiene."""
    return run(vault, set(vault.notes)) + hygiene(vault)


def needs_hygiene(vault: Vault) -> list[str]:
    """Needs you holds the owner's own decisions, once: a bullet repeating a task already waiting on them, or one about
    the brain itself (a migration, a skill, the Hermes setup), is named so the agent removes it."""
    from . import needs
    note = vault.notes.get("CURRENT.md")
    if note is None:
        return []
    config = vault.config
    waiting = [(item.source.split("#")[-1] if "#" in item.source else item.source.rsplit("/", 1)[-1].removesuffix(".md"), item)
               for item in needs.items(vault, config.today()) if item.origin == "task"]
    hermes = "/" + config.hermes_dir.strip("/") + "/"
    out = []
    for item in needs.parse_section(note.body):
        if (tid := needs.duplicates(item, waiting)) is not None:
            out.append(f"CURRENT.md: Needs you repeats {tid}, which already waits on the owner; remove the bullet: {item.text[:80]}")
        elif hermes in "/" + item.source + "/" or re.search(r"\bmigrations?\b", item.text, re.I):
            out.append(f"CURRENT.md: Needs you holds a note about the brain itself; it belongs in the Sleep Log: {item.text[:80]}")
    return out


def identity_duplicates(vault: Vault) -> list[str]:
    """One permanent Demiurge id belongs on one profile, across both people and intellects."""
    from . import directory
    from .session_context import profile_ids
    identities = {}
    for note in directory.profiles(vault):
        for ident in profile_ids(note.body):
            if ident.startswith("demiurge:"):
                identities.setdefault(ident, []).append(note.rel)
    return [f"{ident}: duplicate identity on {', '.join(paths)}" for ident, paths in sorted(identities.items())
            if len(paths) > 1]


def channels(vault: Vault) -> list[str]:
    from . import channels as registry
    return registry.problems(vault)


def kinds(vault: Vault) -> list[str]:
    """On demand only, never a nightly gate: vaults written before a kind was declared keep committing."""
    return [f"{rel}: undeclared kind {kind!r}" for rel, note in sorted(vault.notes.items())
            if (kind := note.data.get("kind")) is not None and kind not in KINDS]
