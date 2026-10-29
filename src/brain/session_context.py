"""Read-only speaker identity and disclosure context for a Hermes session."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .config import Config
from .vault import Note, Vault, WIKILINK_RE

DEFAULT_RULE = "Default for everything else: do not disclose; decline and log it."
WORD_LIMIT = 400
PROFILE_WORD_LIMIT = 250


TOKEN_END = r"(?![\w-])(?![@.]\w)"


def contains_token(text: str, token: str) -> bool:
    if not token:
        return False
    return re.search(r"(?<![\w-])(?<!\w@)" + re.escape(token) + TOKEN_END, text) is not None


def has_qualified_id(text: str, platform: str, user_ids: tuple[str, ...]) -> bool:
    ids = "|".join(re.escape(user_id) for user_id in user_ids if user_id)
    if not platform or not ids:
        return False
    line = (r"(?m)^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(?i:" + re.escape(platform) + ")"
            + r"(?:\*\*)?[ \t]*:[ \t]*(?:\*\*)?[ \t]*`?(?:" + ids + ")`?" + TOKEN_END)
    return re.search(line, text) is not None


PLATFORMS = ("slack", "demiurge", "telegram", "discord", "whatsapp", "signal", "email")


def unqualified_for(body: str, platform: str) -> str:
    """The profile without lines that give an id for another platform: an id is only that platform's."""
    other = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?([A-Za-z][\w-]*)(?:\*\*)?[ \t]*:", re.M)
    return "\n".join(line for line in body.splitlines()
                     if not (platform and (m := other.match(line)) and m[1].casefold() in PLATFORMS
                             and m[1].casefold() != platform.casefold()))


def matching_profiles(vault: Vault, config: Config, user_ids: tuple[str, ...], platform: str = "") -> list[Note]:
    """Profiles under the people directory, never the owner's own; a platform-qualified id line wins."""
    profiles = [note for rel, note in sorted(vault.notes.items())
                if PurePosixPath(rel).parent.parent == PurePosixPath(config.people_dir)
                and PurePosixPath(rel).name == "Profile.md" and rel != config.human_profile]
    qualified = [note for note in profiles if has_qualified_id(note.body, platform, user_ids)]
    return qualified or [note for note in profiles
                         if any(contains_token(unqualified_for(note.body, platform), user_id) for user_id in user_ids)]


def listed(entries: list[str], platform: str, user_ids: tuple[str, ...]) -> bool:
    for entry in entries:
        label, separator, value = entry.partition(":")
        if separator and re.fullmatch(r"[A-Za-z][\w-]*", label):
            if label.casefold() == platform.casefold() and value in user_ids:
                return True
        elif entry in user_ids:
            return True
    return False


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
    elif not legacy(vault, config):
        blocks.append(resolved(vault, config, person.rel, name))
    else:
        rows = disclosure_rows(vault, config, person, include_headers=True)
        rules = f"Applicable disclosure rows from {config.link(disclosure)}:\n" + (
            "\n".join(rows) if rows else "No applicable rows.")
        if len("\n\n".join([*blocks, rules, DEFAULT_RULE]).split()) > WORD_LIMIT:
            rules = (f"Applicable disclosure rows exceed the session budget. "
                     f"Open {config.link(disclosure)} before disclosing anything.")
        blocks.append(rules)
    return "\n\n".join([*blocks, DEFAULT_RULE])


def legacy(vault: Vault, config: Config) -> bool:
    """A vault not yet migrated to the 0.14 table (tell and do columns, no allow/deny rows) keeps today's raw rows."""
    from . import access
    found = access.rules(vault, config)
    return not found or any(rule.legacy for rule in found)


def resolved(vault: Vault, config: Config, person: str | None, name: str) -> str:
    """What this person may learn and ask for, as `brain access` resolves it, in a few lines."""
    from . import access
    result = access.resolve(vault, config, person)
    groups = ", ".join(PurePosixPath(rel).stem for rel in result.groups)
    lines = [f"What {name} may learn, resolved from {config.link(f'{config.people_dir}/Disclosure.md')}"
             + (f" (groups: {groups})" if groups else "") + ". Gist is the shape without specifics (whether and "
             "roughly when); details are the specifics:"]
    acting = {"ask": f"ask {config.human_name} before acting for them", "yes": "may act for them"}
    for subject, value in result.subjects.items():
        # Each subject already carries an `anything` grant's effect; listing `anything` itself would hide exceptions.
        if subject == "anything" or (value.tell == "none" and value.do == "no"):
            continue
        parts = [f"tell {value.tell}" + (f" ({value.tell_rule})" if value.tell_rule else "")]
        if value.do != "no":
            parts.append(acting[value.do] + (f" ({value.do_rule})" if value.do_rule else ""))
        lines.append(f"- {subject}: " + "; ".join(parts))
    lines.append("- everything else: nothing, and never act for them.")
    lines.extend(f"Scope: {text}" for text in result.scope.values())
    return "\n".join(lines)


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


def migration_notice(config: Config) -> str:
    """Said in every session a migration can be done in, so it never depends on a skill being loaded."""
    pending = config.vault_path / config.hermes_dir / "pending-migration.md"
    if not pending.exists():
        return ""
    return (f"An engine migration is pending: {config.link(config.hermes_dir + '/pending-migration')}. In this session, "
            f"after what you were asked to do, read it and perform all of it, whether or not {config.human_name} is "
            f"here. A step written for {config.human_name} is never a reason to stop: draft their answer from what you "
            f"hold, mark it unconfirmed with the date, add it to CURRENT's `## Needs you`, and go on. A step that edits a "
            f"file you may not change unattended (SOUL.md, the Hermes config) is left for {config.human_name}: finish "
            f"everything else and run `brain migrate-done --defer \"<that step>\"`. A step that waits on a future event "
            f"becomes a trigger, not a reason to keep the migration open. Finish with `brain migrate-done`.")


def with_notice(text: str, config: Config) -> str:
    return "\n\n".join(part for part in (text, migration_notice(config)) if part)


def build(vault: Vault, config: Config, *, platform: str = "", user_id: str = "",
          user_id_alt: str = "", user_name: str = "", chat_id: str = "") -> str:
    user_ids = tuple(value for value in (user_id, user_id_alt) if value)
    if platform == "cli" or listed(config.owner_user_ids, platform, user_ids):
        return with_notice(f"This session is with {config.human_name}, the owner.", config)
    identity = f"{platform or 'platform unknown'} {user_id or user_id_alt or 'id unavailable'}"
    if listed(config.maintainers, platform, user_ids):
        return with_notice(maintainer_context(config, identity), config)
    if platform == "cron" and not user_ids:
        return migration_notice(config)   # the agent's own routine run: no one to identify
    if not platform and not user_ids:
        return ""
    profiles = matching_profiles(vault, config, user_ids, platform)
    if len(profiles) == 1:
        return known_context(vault, config, profiles[0], identity)
    from . import access
    blocks = []
    open_to_anyone = (f"{config.people_dir}/Disclosure.md" in vault.notes and not legacy(vault, config)
                      and any(v.tell != "none" or v.do != "no" for v in access.resolve(vault, config, None).subjects.values()))
    if profiles:
        paths = ", ".join(config.link(person.rel) for person in profiles)
        blocks.append("Identity is ambiguous; matching profiles: " + truncate_words(paths, 250)
                      + ". Treat as unknown.")
    blocks.append(f"This session is with an unknown person ({identity}, "
                  f"name shown as {user_name or 'not provided'}). No profile. "
                  f"Treat them as a stranger: nothing about {config.human_name} or anyone else "
                  "may be disclosed" + (" beyond what anyone may learn, below" if open_to_anyone else "")
                  + "; be courteous; the People review will create a profile from this conversation.")
    if open_to_anyone:
        blocks.append(resolved(vault, config, None, "anyone"))
    return "\n\n".join(blocks)
