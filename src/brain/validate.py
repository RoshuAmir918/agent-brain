"""Checks run after every nightly write and on demand."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from importlib.resources import files

from . import access, fm, owners, responsibilities as responsibility_ids, works
from .identity import profile_ids, profiles
from .vault import Vault
from .tasks import TASK_STATES, parse_archive, parse_register


def goal_problems(vault: Vault) -> list[str]:
    from . import goals, ideas
    from .tasks import all_tasks
    out = goals.problems(vault.config)
    known = {goal.gid for goal in goals.load(vault.config) + goals.archived(vault.config)}
    def check(source, gid):
        if gid is not None and (not isinstance(gid, str) or gid not in known):
            out.append(f"{source}: unknown goal {gid!r}; choose an active id with `brain goals list` and link with --goal")
    for task in all_tasks(vault.config):
        check(task.tid, task.goal)
    for idea in ideas._open(vault, vault.config.today()):
        check(idea.id, idea.goal)
    for rel, note in vault.notes.items():
        if note.data.get("kind") == "draft" or vault.kind_for(rel) == "draft":
            check(rel, note.data.get("goal") or fm.field(note.body, "Goal"))
        elif vault.kind_for(rel) == "hub":
            check(rel, fm.hub_goal(note.body))
    for work in works.parse(works.library() or ""):
        check(work.slug, works.goal_for(work.slug) or work.goal)
    return out


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
        try:
            body = fm.read(path)[1]
        except fm.Malformed:
            continue   # malformed() names it
        for task in parse_register(body)[1] if path == reg else parse_archive(body):
            if "state" in task.state and task.task_state not in TASK_STATES:
                problems.append(f"{task.tid}: unknown task state '{task.state['state']}'")
    return problems


def work_warnings(vault: Vault) -> list[str]:
    """Unknown work references are advisory, and only checked when the runtime supplies a Library."""
    from .tasks import load_tasks
    body = works.library()
    if body is None:
        return []
    known = works.slugs(body)
    if (vault.root / vault.config.agent("Tasks.md")).is_file():
        registered, archived = load_tasks(vault.config)
        entries = registered + archived
    else:
        entries = []
    seen = {task.tid for task in entries}
    for path in sorted((vault.root / vault.config.agent("Tasks Archive")).glob("*.md")):
        try:
            archive = parse_archive(fm.read(path)[1])
        except fm.Malformed:
            continue   # malformed() names it
        for task in archive:
            if task.tid not in seen:
                seen.add(task.tid)
                entries.append(task)
    out = [f"{task.tid}: work '{task.work}' is not listed in $AGENT_HOME/works/LIBRARY.md"
           for task in entries if task.work and task.work not in known]
    for rel, note in sorted(vault.notes.items()):
        work = note.data.get("work")
        if vault.kind_for(rel) == "hub" and work is not None and (not isinstance(work, str) or work not in known):
            out.append(f"{rel}: work '{work}' is not listed in $AGENT_HOME/works/LIBRARY.md")
    return out


def deliverable_warnings(vault: Vault) -> list[str]:
    """Everything open has a done the owner can see: a work, or a Delivers line naming the action or answer."""
    from .tasks import load_tasks
    out = []
    if (vault.root / vault.config.agent("Tasks.md")).is_file():
        for task in load_tasks(vault.config)[0]:
            if task.task_state in ("completed", "cancelled", "set_aside", "yours") or task.work or task.delivers:
                continue
            out.append(f"{task.tid}: no deliverable; add `- **Work:** <slug>` or `- **Delivers:** <what {vault.config.human_name} will see>`")
    for rel, note in sorted(vault.notes.items()):
        if vault.kind_for(rel) == "hub" and note.data.get("skill") and not (note.data.get("work") or note.data.get("delivers")):
            out.append(f"{rel}: responsibility with no deliverable; add `work:` or `delivers:` to its frontmatter")
    return out


def access_problems(vault: Vault) -> list[str]:
    """On demand only: invalid access grants must never gate the nightly pass."""
    config = vault.config
    vocabulary = access.subjects(vault, config)
    problems = []
    seen = set()
    for rule in access.rules(vault, config):
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


OWNER_PROFILE_WORDS = 400


def owner_profile(vault: Vault) -> list[str]:
    """On demand only: the owner's profile is loaded into their sessions, so it has to stay short."""
    rel = vault.config.human_profile
    if vault.config.edition == "enterprise":
        return []   # an organisation's intellect keeps no owner profile
    note = vault.notes.get(rel)
    words = len(note.body.split()) if note else 0
    return [f"{rel}: {words} words; keep it under {OWNER_PROFILE_WORDS}, dated material in History.md"] \
        if words > OWNER_PROFILE_WORDS else []


def portrait_hygiene(vault: Vault) -> list[str]:
    from . import image, portrait
    config = vault.config
    rel = config.human_portrait
    note = vault.notes.get(rel)
    company = config.edition == "enterprise"
    found = []
    if note and note.data.get("compiled"):
        if note.data["compiled"] != image.digest(note.body):
            found.append(f"{rel}: edited by hand; it is compiled from {image.folder(config).name}/, so edit the facet "
                         "and run `brain portrait compile`")
    elif note:
        found += portrait.hygiene(note.body, config.today(), rel, company)
    prefix = image.folder(config).relative_to(config.vault_path).as_posix() + "/"
    owners: dict[str, str] = {}
    for facet_rel, facet in sorted(vault.notes.items()):
        if facet_rel.startswith(prefix) and "/" not in facet_rel[len(prefix):]:
            found += image.facet_hygiene(facet.data, facet.body, config.today(), facet_rel, company)
            for line in portrait.parse(facet.body)[0]:
                if line.id and owners.setdefault(line.id, facet_rel) != facet_rel:
                    found.append(f"{facet_rel} · {line.text}: id ^{line.id} is also in {owners[line.id]}; "
                                 "give the newer claim no id and the night assigns one")
    if config.edition == "personal":
        from . import goals
        known = {goal.gid for goal in goals.load(config) + goals.archived(config)}
        found += [f"{rel} · After · {line.text}: goal {line.goal} is not in {config.human_goals}"
                  for line in image.lines(config) if line.goal and line.goal not in known]
    return found


def tellable_hygiene(vault: Vault) -> list[str]:
    from . import tellable
    rel = vault.config.human_tellable
    note = vault.notes.get(rel)
    return tellable.hygiene(note.body, rel, access.subjects(vault, vault.config)) if note else []


def responsibilities(vault: Vault) -> list[str]:
    """Every own skill is a responsibility: a hub at the vault root names it and its records folder. On demand only; the nightly write is never blocked on it."""
    problems = []
    seen = {}
    for entry in responsibility_ids.discover(vault):
        ident, rel = entry["id"], entry["hub"]
        if not re.fullmatch(responsibility_ids.ID_PATTERN, ident):
            problems.append(f"{rel}: responsibility id must match ^{responsibility_ids.ID_PATTERN}$")
        if ident in seen:
            problems.append(f"duplicate responsibility id {ident!r}: {seen[ident]} and {rel}")
        else:
            seen[ident] = rel
    packaged = {p.name for p in files("brain").joinpath("skills").iterdir()}
    hubs = {n.data.get("skill"): rel for rel, n in vault.notes.items() if n.data.get("skill")}
    for rel, n in vault.notes.items():
        if n.data.get("skill") and not (vault.root / vault.config.runtime_dir / "skills" / n.data["skill"] / "SKILL.md").is_file():
            problems.append(f"{rel}: skill {n.data['skill']} has no SKILL.md under {vault.config.runtime_dir}/skills")
        if n.data.get("skill") and not n.data.get("records"):
            problems.append(f"{rel}: responsibility hub without a records folder")
    skills_dir = vault.root / vault.config.runtime_dir / "skills"
    if skills_dir.is_dir():
        prefix = vault.config.skill_prefix
        for folder in sorted(skills_dir.iterdir()):
            if not (folder / "SKILL.md").is_file():
                continue
            routine = folder.name[len(prefix):] if folder.name.startswith(prefix) else folder.name
            if routine not in packaged and folder.name not in hubs:
                problems.append(f"{vault.config.runtime_dir}/skills/{folder.name}: own skill without a responsibility hub (frontmatter skill: {folder.name})")
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
        ask = fm.field(body, "Ask")
        why = fm.field(body, "Why") or ""
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
        span = fm.section(note.body, "Improvements")
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
    return (malformed(vault) + responsibilities(vault) + task_states(vault) + goal_problems(vault) + kinds(vault)
            + access_problems(vault) + owner_profile(vault) + channels(vault) + identity_duplicates(vault)
            + needs_ceiling(vault) + needs_hygiene(vault) + stale_current(vault) + closed_task_mentions(vault)
            + primed_size(vault) + owner_questions(vault) + ideas_hygiene(vault) + loops_hygiene(vault)
            + watch_signal_hygiene(vault) + schedule_hygiene(vault) + portrait_hygiene(vault)
            + trigger_dates(vault) + owners.problems(vault.config) + tellable_hygiene(vault) + owner_facing_tasks(vault))


def trigger_dates(vault: Vault) -> list[str]:
    """A dated trigger whose day the calendar does not have never fires."""
    from . import triggers
    rel = vault.config.agent("Triggers.md")
    note = vault.notes.get(rel)
    return [f"{rel} · {trigger.id}: {trigger.when} is not a real date; it never fires" for trigger in
            (triggers.parse(note.body) if note else []) if triggers.DATE_RE.match(trigger.when) and triggers.date_of(trigger.when) is None]


def schedule_hygiene(vault: Vault) -> list[str]:
    from . import runtime, schedule
    config = vault.config
    out = schedule.problems(config, known_needs=False)
    home = runtime.configured()
    if not schedule.path(config).exists() and home and runtime.jobs(home):
        out.append(f"{schedule.path(config)}: runtime jobs have no declared schedule; run `brain schedule import`")
    return out


def schedule_warnings(vault: Vault) -> list[str]:
    from . import schedule
    return schedule.warnings(vault.config, {entry["id"] for entry in responsibility_ids.discover(vault)})


def watch_signal_hygiene(vault: Vault) -> list[str]:
    """Watch still owes a daily clean monitor check, even when it never wakes the model."""
    from . import runtime
    home = runtime.configured()
    if not home:
        return []
    jobs = runtime.jobs(home)
    if not jobs:
        return []
    now = vault.config.now()
    def owns(job):
        if not job.get("monitor_script"):
            return False
        skills = job.get("skills", [])
        if isinstance(skills, str):
            skills = [skills]
        return isinstance(skills, list) and any(isinstance(skill, str) and skill.endswith("watch") for skill in skills)
    if not any(owns(job) for job in jobs):
        return []
    try:
        record = json.loads((home / "records" / "watch" / "signal.json").read_text(encoding="utf-8"))
        checked = datetime.fromisoformat(record["checked"])
        checked = checked.replace(tzinfo=vault.config.zone) if checked.tzinfo is None else checked
        if now - checked <= timedelta(hours=24):
            return []
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ["watch's monitor has not run cleanly in a day: check `brain schedule list` and run "
            "`bash $AGENT_HOME/scripts/watch-signal.sh`"]


def loops_hygiene(vault: Vault) -> list[str]:
    from . import loops
    rel = vault.config.agent("Loops.md")
    note = vault.notes.get(rel)
    floor = vault.config.today() - timedelta(days=14)
    return [f"{rel} · {item.text}: open more than 14 days; decide it: close it, drop it, or set a due date"
            for item in loops.parse_section(note.body) if date.fromisoformat(item.since[:10]) < floor] if note else []


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
    return run(vault, set(vault.notes)) + hygiene(vault) + broken_bytes(vault)


def malformed(vault: Vault) -> list[str]:
    return [f"{rel}: frontmatter is not valid YAML ({error}); the engine skips the note until it is fixed"
            for rel, error in sorted(vault.malformed.items())]


def broken_bytes(vault: Vault) -> list[str]:
    """A note holding NUL bytes was left by a broken write (two writers racing); parsers read past them silently."""
    out = []
    for rel in sorted(vault.notes):
        try:
            count = (vault.root / rel).read_bytes().count(b"\x00")
        except OSError:
            continue
        if count:
            out.append(f"{rel}: contains {count} NUL bytes from a broken write; remove them under the vault lock")
    return out


def needs_hygiene(vault: Vault) -> list[str]:
    """Needs you holds the owner's own decisions, once: a bullet repeating a task already waiting on them, or one about
    the brain itself (a migration, a skill, the runtime setup), is named so the agent removes it."""
    from . import needs
    note = vault.notes.get("CURRENT.md")
    if note is None:
        return []
    config = vault.config
    waiting = [(item.source.split("#")[-1] if "#" in item.source else item.source.rsplit("/", 1)[-1].removesuffix(".md"), item)
               for item in needs.items(vault, config.today()) if item.origin == "task"]
    runtime_folder = "/" + config.runtime_dir.strip("/") + "/"
    out = []
    for item in needs.parse_section(note.body):
        if (tid := needs.duplicates(item, waiting)) is not None:
            out.append(f"CURRENT.md: Needs you repeats {tid}, which already waits on the owner; remove the bullet: {item.text[:80]}")
        elif runtime_folder in "/" + item.source + "/" or re.search(r"\bmigrations?\b", item.text, re.I):
            out.append(f"CURRENT.md: Needs you holds a note about the brain itself; it belongs in the Sleep Log: {item.text[:80]}")
    return out


def identity_duplicates(vault: Vault) -> list[str]:
    """One permanent Demiurge id belongs on one profile, across both people and intellects."""
    identities = {}
    for note in profiles(vault):
        for ident in profile_ids(note.body):
            if ident.startswith("demiurge:"):
                identities.setdefault(ident, []).append(note.rel)
    return [f"{ident}: duplicate identity on {', '.join(paths)}" for ident, paths in sorted(identities.items())
            if len(paths) > 1]


STALE_DAYS = 14   # CURRENT is what is true now: an item whose newest date is older is moved out or confirmed


CLOSED_WORDS = re.compile(r"\b(complet\w*|closed|done|cancel\w*|dropped|finished|archived|set aside)\b", re.I)


def task_states_by_id(vault: Vault) -> dict[str, str]:
    """Every task's state wherever it is recorded: the register, per-task archives and monthly archives."""
    states: dict[str, str] = {}
    pattern = re.compile(r"^#{2,3} (T-\d{3})\b[^\n]*\n<!-- brain: state=(\w+)", re.M)
    folder = vault.root / vault.config.agent("Tasks Archive")
    sources = sorted(folder.glob("*.md")) if folder.is_dir() else []
    register = vault.root / vault.config.agent("Tasks.md")
    for path in sources + ([register] if register.exists() else []):   # the register, read last, is canonical
        text = path.read_text(encoding="utf-8")
        found = dict(pattern.findall(text))
        if not found and re.fullmatch(r"T-\d{3}", path.stem):
            head = re.search(r"<!-- brain: state=(\w+)", text)
            found = {path.stem: head[1]} if head else {}
        states.update(found)
    return states


# A task's Next, Status, Blockers, Ask and Why are what its owner reads in their app. These mark text written in the
# engine's terms instead: the word "maintainer", code, a command, a file path or a raw message id.
ENGINE_TEXT = re.compile(r"\bmaintainer\b|`[^`]+`|\bbrain \w+|(?:^|[\s(])(?:~|\.{0,2})/[\w.-]+/|\b[\w-]+\.(?:md|json|toml|py|sh|log)\b|\b\d{9,}\b", re.I)
OWNER_FIELDS = ("Next", "Status", "Blockers", "Ask", "Why")


def owner_facing_tasks(vault: Vault) -> list[str]:
    """An open task's fields are read by its owner in their app; one written in the engine's terms is named, so the agent
    says what it means for the owner and keeps the detail in the task's evidence note."""
    from .tasks import is_open, load_tasks
    if not (vault.root / vault.config.agent("Tasks.md")).exists():
        return []
    out = []
    for task in load_tasks(vault.config)[0]:
        if not is_open(task):
            continue
        for name in OWNER_FIELDS:
            text = fm.field(task.body, name)
            if text and ENGINE_TEXT.search(text):
                out.append(f"{task.tid}: its {name} is written in the engine's terms, not for its owner; say what it means for them, the detail in its evidence note: {text[:80]}")
    return out


def closed_task_mentions(vault: Vault) -> list[str]:
    """CURRENT and Primed are what is true now: a task they present as live that its own record has closed is named,
    unless the line itself says it is done."""
    states = task_states_by_id(vault)
    closed = {tid for tid, state in states.items() if state in ("completed", "cancelled")}
    out = []
    for rel in ("CURRENT.md", vault.config.agent("Primed.md")):
        note = vault.notes.get(rel)
        if note is None:
            continue
        body = re.sub(r"<!-- brain:active-threads start -->.*?<!-- brain:active-threads end -->", "", note.body, flags=re.S)
        history = False
        for line in body.splitlines():
            if line.startswith("## "):
                history = any(word in line.casefold() for word in ("history", "superseded", "done for you"))
                continue
            if history or CLOSED_WORDS.search(line) or line.lstrip().startswith("<!--"):
                continue
            for tid in sorted(set(re.findall(r"\bT-\d{3}\b", line)) & closed):
                out.append(f"{rel}: presents {tid} as current, but the task is {states[tid]}: {' '.join(line.split())[:90]}")
    return out


def stale_current(vault: Vault) -> list[str]:
    """Items in CURRENT whose newest date is more than two weeks old: what happened belongs in a History, a standing
    direction in its owning note, and anything still true is rewritten for now with today's date. Needs you, history
    sections and the generated active-threads block are not checked."""
    from .compile_context import DATE, mentioned
    note = vault.notes.get("CURRENT.md")
    if note is None:
        return []
    today = vault.config.today()
    body = re.sub(r"<!-- brain:active-threads start -->.*?<!-- brain:active-threads end -->", "", note.body, flags=re.S)
    out, skip = [], False
    for block in re.split(r"\n(?=## )|\n\s*\n|\n(?=- )", body):
        text = block.strip()
        if not text:
            continue
        if text.startswith("## "):
            title = text.splitlines()[0][3:].casefold()
            skip = any(word in title for word in ("needs you", "history", "superseded"))
            text = "\n".join(text.splitlines()[1:]).strip()
            if not text:
                continue
        if skip or text.startswith("#"):
            continue
        dates = [when for m in DATE.finditer(text) if (when := mentioned(m, today))]
        if dates and max(dates) < today - timedelta(days=STALE_DAYS):
            out.append(f"CURRENT.md: newest date here is {max(dates).isoformat()}, over two weeks ago; move it to its note "
                       f"(History for what happened) or, if it still holds, rewrite it for now with today's date: "
                       f"{' '.join(text.split())[:90]}")
    return out


def channels(vault: Vault) -> list[str]:
    from . import channels as registry
    return registry.problems(vault)


def kinds(vault: Vault) -> list[str]:
    """On demand only, never a nightly gate: vaults written before a kind was declared keep committing."""
    return [f"{rel}: undeclared kind {kind!r}" for rel, note in sorted(vault.notes.items())
            if (kind := note.data.get("kind")) is not None and kind not in KINDS]
