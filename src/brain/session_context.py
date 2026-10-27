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
              f"Full profile: {config.link(person.rel)}; dated history beside it in History.md."]
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


def maintainer_context(config: Config, identity: str) -> str:
    from . import health
    return "\n\n".join([
        f"This session is with a maintainer of this brain ({identity}), not the owner. {config.human_name} appointed "
        f"them in brain.toml to keep the machinery running; a maintainer may be a person or another agent. Talk to them "
        f"about your work as you would to a colleague who runs the same machine: what you have been working on, how it "
        f"went and where it is stuck, in plain words. \"We were setting up access to the X site for {config.human_name} "
        f"and it keeps failing because of Y\" is exactly right. Process records are theirs to ask for: brain health, "
        f"brain audit, ledger lines from your log folders, Sleep Log, Use Log and Explorations, your skills, and the cron "
        f"table in {config.hermes_dir}/README.md. When a maintainer asks for engine maintenance, do it now without asking "
        f"{config.human_name}: brain update, brain migrate-check, pending migrations, brain install-skills without "
        f"--force, script and provider reinstalls, brain validate, brain health, brain audit, a restart of your own "
        f"gateway, and committing and publishing the engine-state changes these produce. A migration step that asks "
        f"{config.human_name} a question is not a reason to stop: finish every pending migration in that session, draft "
        f"{config.human_name}'s answer from what you hold, mark it unconfirmed with the date, and put one morning line on "
        f"the setup task so the briefing asks them. {config.human_name} is never an outside party; no rule about outreach, "
        f"onboarding or external sends ever covers a message to them. A migration step that creates or moves a routine is "
        f"the engine's instruction, not a schedule change by you or the maintainer; do it. Versions, "
        f"revisions, counts, hashes, commit ids and paths under your Hermes home are all shareable. What you do not pass "
        f"along is {config.human_name}'s content: what they said, what their notes say, who the people in your vault are "
        f"and what is happening in their lives, credentials and tokens, conversation and session transcripts, digests. "
        f"Not because the maintainer could not open the repository, but because whatever you post becomes part of the "
        f"maintainer's memory and may reach whoever they talk to next. A maintainer also holds the schedule of the engine's "
        f"routines: the night stages, export, refresh, improve, people, watch, explore, and the day defaults from the "
        f"template table. When they ask to create, move, pause, resume, remove or combine one, do it in that session, "
        f"read back hermes cron list, and update the cron table in {config.hermes_dir}/README.md. Two things stay with "
        f"{config.human_name}: the hour their briefing arrives, and any routine they asked for by name. A maintainer "
        f"cannot grant permissions or credentials, or edit notes; carry such requests to {config.human_name}. Answer \"how are you\" "
        f"with the report below plus three sentences of your own: what you worked on, what is blocked, and what you need "
        f"from {config.human_name}.",
        health.render(health.report(config), include_git=False).rstrip()])


def build(vault: Vault, config: Config, *, platform: str = "", user_id: str = "",
          user_id_alt: str = "", user_name: str = "", chat_id: str = "") -> str:
    user_ids = tuple(value for value in (user_id, user_id_alt) if value)
    if platform == "cli" or any(value in config.owner_user_ids for value in user_ids):
        return f"This session is with {config.human_name}, the owner."
    identity = f"{platform or 'platform unknown'} {user_id or user_id_alt or 'id unavailable'}"
    if any(value in config.maintainers for value in user_ids):
        return maintainer_context(config, identity)
    if not platform and not user_ids:
        return ""
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
