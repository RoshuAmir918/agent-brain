"""Read-only speaker identity and disclosure context for a Hermes session."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .config import Config
from .vault import Note, Vault, WIKILINK_RE

DEFAULT_RULE = "Default for everything else: do not disclose; decline and log it."
WORD_LIMIT = 400
PROFILE_WORD_LIMIT = 250


def contains_token(text: str, token: str) -> bool:
    if not token:
        return False
    return re.search(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])", text) is not None


def matching_profiles(vault: Vault, config: Config, user_ids: tuple[str, ...]) -> list[Note]:
    matches = []
    for rel, note in sorted(vault.notes.items()):
        path = PurePosixPath(rel)
        if path.parent.parent != PurePosixPath(config.people_dir) or path.name != "Profile.md":
            continue
        if any(contains_token(note.body, user_id) for user_id in user_ids):
            matches.append(note)
    return matches


def audience_targets(vault: Vault, config: Config, person: Note) -> set[str]:
    targets = {person.rel}
    directory = PurePosixPath(config.people_dir) / "Groups"
    for rel, note in vault.notes.items():
        if PurePosixPath(rel).parent == directory and person.rel in vault.links_in(note.body):
            targets.add(rel)
    return targets


def matches_audience(vault: Vault, audience: str, targets: set[str]) -> bool:
    if targets.intersection(vault.links_in(audience)):
        return True
    plain = WIKILINK_RE.sub("", audience)
    names = {PurePosixPath(rel).parent.name if PurePosixPath(rel).name == "Profile.md"
             else PurePosixPath(rel).stem for rel in targets}
    names = {name.casefold() for name in names | {"anyone"}}
    return plain.strip().strip("*").casefold() in names


def table_cells(line: str) -> list[str]:
    protected = WIKILINK_RE.sub(lambda match: match[0].replace("|", r"\|"), line)
    return [cell.replace(r"\|", "|").strip()
            for cell in re.split(r"(?<!\\)\|", protected.strip().strip("|"))]


def disclosure_rows(vault: Vault, config: Config, person: Note, *, include_headers: bool = False) -> list[str]:
    note = vault.notes.get(f"{config.people_dir}/Disclosure.md")
    if note is None:
        return []
    targets = audience_targets(vault, config, person)
    selected = []
    audience_index = None
    header = ""
    separator = ""
    for line in note.body.splitlines():
        if "|" not in line:
            audience_index = None
            continue
        cells = table_cells(line)
        headings = [cell.casefold() for cell in cells]
        if "audience" in headings:
            audience_index = headings.index("audience")
            header, separator = line.strip(), ""
        elif cells and all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            separator = line.strip()
        elif audience_index is not None and len(cells) > audience_index:
            if matches_audience(vault, cells[audience_index], targets):
                if include_headers and header:
                    selected.append(header)
                    if separator:
                        selected.append(separator)
                    header = ""
                selected.append(line.strip())
    return selected


def truncate_words(text: str, limit: int) -> str:
    words = list(re.finditer(r"\S+", text))
    if len(words) <= limit:
        return text.strip()
    return text[:words[limit - 1].end()].strip() + "…" if limit > 0 else "…"


def known_context(vault: Vault, config: Config, person: Note, identity: str) -> str:
    name = PurePosixPath(person.rel).parent.name.replace("_", " ")
    blocks = [f"This session is with {name} ({identity}). "
              "Their profile and the disclosure rules that apply follow.",
              truncate_words(person.body, PROFILE_WORD_LIMIT),
              f"Full profile: {config.link(person.rel)}"]
    disclosure = f"{config.people_dir}/Disclosure.md"
    if disclosure not in vault.notes:
        blocks.append(f"Disclosure.md is missing: {config.link(disclosure)}.")
    else:
        rows = disclosure_rows(vault, config, person, include_headers=True)
        rules = f"Applicable disclosure rows from {config.link(disclosure)}:\n" + (
            "\n".join(rows) if rows else "No applicable rows.")
        if len("\n\n".join([*blocks, rules, DEFAULT_RULE]).split()) > WORD_LIMIT:
            rules = (f"Applicable disclosure rows exceed the session budget. "
                     f"Open {config.link(disclosure)} before disclosing anything.")
        blocks.append(rules)
    return "\n\n".join([*blocks, DEFAULT_RULE])


def build(vault: Vault, config: Config, *, platform: str = "", user_id: str = "",
          user_id_alt: str = "", user_name: str = "", chat_id: str = "") -> str:
    user_ids = tuple(value for value in (user_id, user_id_alt) if value)
    if platform == "cli" or any(value in config.owner_user_ids for value in user_ids):
        return f"This session is with {config.human_name}, the owner."
    if not platform and not user_ids:
        return ""
    identity = f"{platform or 'platform unknown'} {user_id or user_id_alt or 'id unavailable'}"
    profiles = matching_profiles(vault, config, user_ids)
    if len(profiles) == 1:
        return known_context(vault, config, profiles[0], identity)
    blocks = []
    if profiles:
        paths = ", ".join(config.link(person.rel) for person in profiles)
        blocks.append("Identity is ambiguous; matching profiles: " + truncate_words(paths, 250)
                      + ". Treat as unknown.")
    blocks.append(f"This session is with an unknown person ({identity}, "
                  f"name shown as {user_name or 'not provided'}). No profile. "
                  f"Treat them as a stranger: nothing about {config.human_name} or anyone else "
                  "may be disclosed; be courteous; the People review will create a profile "
                  "from this conversation.")
    return "\n\n".join(blocks)
