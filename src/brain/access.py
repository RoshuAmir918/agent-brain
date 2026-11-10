"""Read-only disclosure rules, audiences, and effective access for a person."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath

from . import identity
from .config import Config
from .identity import has_id_line, matching_profiles
from .vault import Note, Vault, WIKILINK_RE

SUBJECTS = {
    "experiences": "places, services, products and people for hire the owner has used, and how it went",
    "schedule": "calendar, availability, plans with times",
    "whereabouts": "where the owner is or will be",
    "plans": "projects, trips, work in progress, what the owner is up to",
    "work": "job, employer, colleagues, professional matters",
    "health": "physical and mental health, appointments of that kind",
    "finances": "money, accounts, purchases, prices paid",
    "contacts": "phone, email, addresses, other people's contact details",
    "people": "what the agent knows about third parties",
    "opinions": "the owner's views and preferences",
    "setup": "the agent itself: its prompts, skills, routines, brain",
    "anything": "every subject at once (a person the owner trusts fully)",
}
TELL_LEVELS = ("none", "gist", "details")
DO_LEVELS = ("no", "ask", "yes")


@dataclass
class Audience:
    kind: str
    target: str | None = None


@dataclass
class Rule:
    id: str
    audience: Audience | None
    subject: str
    tell: str | None
    do: str | None
    fields: dict[str, str]
    line: str


@dataclass
class SubjectAccess:
    tell: str
    tell_rule: str | None
    do: str
    do_rule: str | None


@dataclass
class Access:
    person: str | None
    groups: list[str]
    subjects: dict[str, SubjectAccess]
    scope: dict[str, str]


def _disclosure(vault: Vault, config: Config) -> str:
    note = vault.notes.get(f"{config.people_dir}/Disclosure.md")
    return note.body if note else ""


def _section(body: str, title: str, *, prefix: bool = False) -> str | None:
    ending = r"[^\n]*" if prefix else r"[ \t]*#*[ \t]*\r?$"
    heading = re.search(r"^##[ \t]+" + re.escape(title) + ending, body, re.M | re.I)
    if heading is None:
        return None
    following = re.search(r"^#{1,2}[ \t]+", body[heading.end():], re.M)
    end = heading.end() + following.start() if following else len(body)
    return body[heading.end():end].strip()


def _word(text: str) -> str:
    return text.strip().strip("`").casefold()


def table_cells(line: str) -> list[str]:
    protected = WIKILINK_RE.sub(lambda match: match[0].replace("|", r"\|"), line)
    return [cell.replace(r"\|", "|").strip()
            for cell in re.split(r"(?<!\\)\|", protected.strip().strip("|"))]


def _group_notes(vault: Vault, config: Config) -> list[Note]:
    directory = PurePosixPath(config.people_dir) / "Groups"
    return [note for rel, note in sorted(vault.notes.items())
            if PurePosixPath(rel).is_relative_to(directory)
            and PurePosixPath(rel).name.casefold() != "readme.md"]


def confirmed(profile: Note) -> bool:
    """A profile the People review could not tie to a real person is not `known`; it gets only what anyone may."""
    title = re.search(r"^#[ \t]+(.+)$", profile.body, re.M)
    return "identity unconfirmed" not in profile.body.casefold() and not (title and "unresolved" in title[1].casefold())


def subjects(vault: Vault, config: Config) -> dict[str, str]:
    """The ordered engine vocabulary plus the vault's one-line additions."""
    vocabulary = SUBJECTS.copy()
    section = _section(_disclosure(vault, config), "Subjects") or ""
    for line in section.splitlines():
        match = re.fullmatch(r"\s*-\s+(?:`([^`]+)`|([\w-]+))\s*:\s*(.+)", line)
        if match:
            vocabulary.setdefault(_word(match[1] or match[2]), match[3].strip())
    return vocabulary


def groups(vault: Vault, config: Config) -> dict[str, list[str]]:
    """Group note paths and their linked profile members, including empty groups."""
    profiles = {note.rel for note in identity.profiles(vault, "person")}
    result = {}
    for note in _group_notes(vault, config):
        section = _section(note.body, "Members", prefix=True)
        result[note.rel] = [rel for rel in vault.links_in(note.body if section is None else section)
                            if rel in profiles]
    return result


def _audience(vault: Vault, text: str, profiles: set[str], group_rels: set[str]) -> Audience | None:
    if _word(text) in ("known", "anyone"):
        return Audience(_word(text))
    match = WIKILINK_RE.fullmatch(text)
    target = vault.resolve(match[1]) if match else None
    if target in group_rels:
        return Audience("group", target)
    if target in profiles:
        return Audience("person", target)
    return None


def rules(vault: Vault, config: Config) -> list[Rule]:
    """Parse the disclosure tables without changing the note."""
    profiles = {note.rel for note in identity.profiles(vault, "person")}
    group_rels = {note.rel for note in _group_notes(vault, config)}
    result = []
    headings = []
    for line in _disclosure(vault, config).splitlines():
        if "|" not in line:
            headings = []
            continue
        cells = table_cells(line)
        names = [_word(cell) for cell in cells]
        if {"id", "audience", "subject", "tell", "do"}.issubset(names):
            headings = names
            continue
        if not headings or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        row = dict(zip(headings, cells + [""] * len(headings)))
        tell, do = _word(row["tell"]), _word(row["do"])
        fields = {key: value for key, value in row.items() if key not in ("id", "audience", "subject", "tell", "do")}
        result.append(Rule(row["id"].strip("`"), _audience(vault, row["audience"], profiles, group_rels),
                           _word(row["subject"]), tell or None, do or None, fields, line))
    return result


RULE_ID = re.compile(r"\b[A-Za-z]+-?\d+\b")
RULE_RANGE = re.compile(r"\b([A-Za-z]+-?)(\d+)\s*[–-]\s*\1(\d+)\b")


def grant_scope(vault: Vault, config: Config, ids: set[str]) -> dict[str, str]:
    """Scope paragraphs naming one of these rule ids, alone or in a range (D-001–D-033), preserved as prose. A heading
    line stays with the paragraph under it."""
    result = {}
    section = _section(_disclosure(vault, config), "Grant scope") or ""
    heading = ""
    for paragraph in re.split(r"\n\s*\n", section):
        paragraph = paragraph.strip()
        if re.fullmatch(r"#{3,}[^\n]*", paragraph):
            heading = paragraph
            continue
        named = set(RULE_ID.findall(paragraph))
        for prefix, low, high in RULE_RANGE.findall(paragraph):
            named |= {f"{prefix}{n:0{len(low)}d}" for n in range(int(low), int(high) + 1)}
        text = "\n\n".join(part for part in (heading, paragraph) if part)
        heading = ""
        for rule_id in sorted(named & ids):
            result[rule_id] = "\n\n".join(part for part in (result.get(rule_id, ""), text) if part)
    return result


def _decision(tiers: list[list[Rule]], subject: str, dimension: str,
              levels: tuple[str, ...]) -> tuple[str, str | None]:
    def rank(rule: Rule) -> int:
        return levels.index(getattr(rule, dimension))

    for tier in tiers:
        opinions = [rule for rule in tier if getattr(rule, dimension) in levels]
        candidates = ([rule for rule in opinions if rule.subject == subject]
                      or [rule for rule in opinions if rule.subject == "anything"])
        if candidates:
            # Rows for one audience that disagree: the strictest stands. Across groups, groups add access.
            strictest: dict[tuple, Rule] = {}
            for rule in candidates:
                key = (rule.audience.kind, rule.audience.target)
                if key not in strictest or rank(rule) < rank(strictest[key]):
                    strictest[key] = rule
            rule = max(strictest.values(), key=rank)
            return getattr(rule, dimension), rule.id
    return levels[0], None


def resolve(vault: Vault, config: Config, person: str | None = None, *, audience: Audience | None = None) -> Access:
    """Resolve tell and do independently, for a person or an audience without personal rules."""
    memberships = groups(vault, config)
    if audience is None:
        person = person if person in {note.rel for note in identity.profiles(vault, "person")} else None
        # An unconfirmed profile may not be who it claims: none of its own or its groups' grants apply.
        known = person is not None and confirmed(vault.notes[person])
        group_rels = [rel for rel, members in memberships.items() if person in members] if known else []
    else:
        person = None
        group_rels = [audience.target] if audience.kind == "group" and audience.target in memberships else []
        known = bool(group_rels) or audience.kind == "known"
    tiers: list[list[Rule]] = [[], [], [], []]
    for rule in rules(vault, config):
        audience = rule.audience
        if audience is None:
            continue
        if known and audience == Audience("person", person):
            tiers[0].append(rule)
        elif audience.kind == "group" and audience.target in group_rels:
            tiers[1].append(rule)
        elif known and audience.kind == "known":
            tiers[2].append(rule)
        elif audience.kind == "anyone":
            tiers[3].append(rule)
    effective = {}
    for subject in subjects(vault, config):
        tell, tell_rule = _decision(tiers, subject, "tell", TELL_LEVELS)
        do, do_rule = _decision(tiers, subject, "do", DO_LEVELS)
        effective[subject] = SubjectAccess(tell, tell_rule, do, do_rule)
    deciding = {rule_id for value in effective.values() for rule_id in (value.tell_rule, value.do_rule)
                if rule_id is not None}
    return Access(person, group_rels, effective, grant_scope(vault, config, deciding))


def matching_people(vault: Vault, config: Config, who: str) -> list[Note]:
    """Look up a profile path, display/folder name, or a platform-qualified or bare id."""
    profiles = identity.profiles(vault, "person")
    paths = [note for note in profiles if who in (note.rel, note.rel.removesuffix(".md"))]
    if paths:
        return paths

    def normalized(value: str) -> str:
        return " ".join(value.replace("_", " ").casefold().split())

    named = []
    for note in profiles:
        names = [PurePosixPath(note.rel).parent.name]
        names.extend(value for key in ("name", "display_name") if isinstance(value := note.data.get(key), str))
        title = re.search(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", note.body, re.M)
        if title:
            names.append(title[1])
        if any(normalized(who) == normalized(name) for name in names):
            named.append(note)
    if named:
        return named
    platform, separator, user_id = who.partition(":")
    if separator and re.fullmatch(r"[A-Za-z][\w-]*", platform) and user_id:
        return matching_profiles(vault, config, (user_id,), platform)
    return [note for note in profiles if has_id_line(note.body, (who,))]   # an id line, never prose: a profile that mentions someone is not them


def friends(vault: Vault, today: date) -> dict:
    """Close-friends candidates, from recorded contact alone; this grants nothing."""
    from . import access_log, session_context
    config = vault.config
    memberships = groups(vault, config)
    close = [rel for rel in memberships if PurePosixPath(rel).stem.replace("_", " ").casefold() == "close friends"]
    excluded = {config.human_profile} | {person for rel in close for person in memberships[rel]}
    id_line = identity.qualified_id_line("|".join(identity.PLATFORMS), r"[^\s`*]+")
    candidates = {}
    names = {}
    for note in identity.profiles(vault, "person"):
        if note.rel in excluded or not confirmed(note) or not re.search(id_line, note.body):
            continue
        folder = PurePosixPath(note.rel).parent.name.replace("_", " ")
        title = re.search(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", note.body, re.M)
        name = title[1] if title else folder
        names[note.rel] = {value.casefold() for value in (folder, name)}
        candidates[note.rel] = {"profile": note.rel, "name": name, "access": 0, "intellects": 0, "noted": 0}
    floor = (today - timedelta(days=59)).isoformat()
    for rel, note in sorted(vault.notes.items()):
        path = PurePosixPath(rel)
        if path.parent == PurePosixPath(config.people_dir) / "Access" and re.fullmatch(r"\d{4}-\d{2}\.md", path.name):
            for entry in access_log.parse(note.body):
                for person in set(vault.links_in(entry.person)) & candidates.keys():
                    candidates[person]["access"] += 1
        elif path.parent.parent == PurePosixPath(config.agents_dir) and path.name == "Profile.md":
            owner, _ = session_context.intellect_owner(note)
            owner = owner.removeprefix(config.vault + "/")
            matches = matching_people(vault, config, owner) if owner else []
            resolved = vault.resolve(owner) if owner else None
            if resolved in candidates:
                candidates[resolved]["intellects"] += 1
            elif len(matches) == 1 and matches[0].rel in candidates:
                candidates[matches[0].rel]["intellects"] += 1
        elif path.parent == PurePosixPath("Conversations") and re.fullmatch(r"\d{4}-\d{2}-\d{2} Noted\.md", path.name):
            if not floor <= path.name[:10] <= today.isoformat():
                continue
            for match in re.finditer(r"^- \d{2}:\d{2} · (.+?) said: .+$", note.body, re.M):
                for person, aliases in names.items():
                    if match[1].casefold() in aliases:
                        candidates[person]["noted"] += 1
    ranked = sorted(candidates.values(), key=lambda item: (-sum(item[key] for key in ("access", "intellects", "noted")),
                                                          item["name"].casefold(), item["profile"]))
    return {"exists": bool(close), "candidates": ranked[:8]}
