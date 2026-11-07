"""Who a speaker is: profile id lines, the profiles that carry them, and the one role every caller acts on."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .config import Config
from .runtime import ROUTINE_PLATFORMS
from .vault import Note, Vault

PLATFORMS = ("slack", "demiurge", "telegram", "discord", "whatsapp", "signal", "email")
TOKEN_END = r"(?![\w-])(?![@.]\w)"


def contains_token(text: str, token: str) -> bool:
    if not token:
        return False
    return re.search(r"(?<![\w-])(?<!\w@)" + re.escape(token) + TOKEN_END, text) is not None


def qualified_id_line(platform: str, ids: str) -> str:
    return (r"(?m)^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(?i:" + platform + ")"
            + r"(?:\*\*)?[ \t]*:[ \t]*(?:\*\*)?[ \t]*`?(?:" + ids + ")`?" + TOKEN_END)


def has_qualified_id(text: str, platform: str, user_ids: tuple[str, ...]) -> bool:
    ids = "|".join(re.escape(user_id) for user_id in user_ids if user_id)
    return bool(platform and ids and re.search(qualified_id_line(re.escape(platform), ids), text))


def profile_ids(text: str) -> list[str]:
    """Only explicit platform id lines, never prose or email addresses."""
    line = qualified_id_line("(?P<platform>" + "|".join(p for p in PLATFORMS if p != "email") + ")", r"(?P<id>[^\s`]+)")
    return list(dict.fromkeys(f"{match['platform'].casefold()}:{match['id']}" for match in re.finditer(line, text)
                             if re.fullmatch(r"[\w-]+", match["id"])))


def has_id_line(text: str, user_ids: tuple[str, ...]) -> bool:
    """An id on any platform's id line (`- **Slack:** `U…``), never in prose: a profile that mentions someone is not them."""
    ids = "|".join(re.escape(user_id) for user_id in user_ids if user_id)
    return bool(ids and re.search(qualified_id_line("|".join(PLATFORMS), ids), text))


def unqualified_for(body: str, platform: str) -> str:
    """The profile without lines that give an id for another platform: an id is only that platform's."""
    other = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?([A-Za-z][\w-]*)(?:\*\*)?[ \t]*:", re.M)
    return "\n".join(line for line in body.splitlines()
                     if not (platform and (m := other.match(line)) and m[1].casefold() in PLATFORMS
                             and m[1].casefold() != platform.casefold()))


def profiles(vault: Vault, kind: str | None = None) -> list[Note]:
    """`<people>/<Name>/Profile.md` (kind "person") and `<agents>/<Name>/Profile.md` (kind "intellect"). A profile that
    declares itself a log is a moved or retired stub pointing at the real one: never an identity."""
    roots = [PurePosixPath(root) for label, root in (("person", vault.config.people_dir),
                                                   ("intellect", vault.config.agents_dir))
             if kind is None or label == kind]
    return [note for rel, note in sorted(vault.notes.items()) if PurePosixPath(rel).name == "Profile.md"
            and PurePosixPath(rel).parent.parent in roots and note.data.get("kind") != "log"]


def matching_profiles(vault: Vault, config: Config, user_ids: tuple[str, ...], platform: str = "") -> list[Note]:
    """People's profiles, never the owner's own; a platform-qualified id line wins."""
    people = [note for note in profiles(vault, "person") if note.rel != config.human_profile]
    qualified = [note for note in people if has_qualified_id(note.body, platform, user_ids)]
    return qualified or [note for note in people
                         if any(contains_token(unqualified_for(note.body, platform), user_id) for user_id in user_ids)]


def matching_agents(vault: Vault, config: Config, user_ids: tuple[str, ...], platform: str = "") -> list[Note]:
    """Registered intellects, by a platform-qualified id line, else an id on a line that is not another platform's."""
    agents = profiles(vault, "intellect")
    qualified = [note for note in agents if has_qualified_id(note.body, platform, user_ids)]
    return qualified or [note for note in agents
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


def is_owner(config: Config, platform: str, user_ids: tuple[str, ...]) -> bool:
    return platform == "cli" or listed(config.owner_user_ids, platform, user_ids)


def role(vault: Vault, config: Config, platform: str, user_ids: tuple[str, ...]) -> str:
    """'owner', 'intellect', 'member', 'person' or 'unknown'.

    In an organisation's intellect anyone else the runtime names is a member: the door (Slack's bridge, the stage's
    sign-in) admits only members, so the engine trusts it. A routine's own run and a registered intellect never are;
    nor is a speaker the runtime does not name. A personal intellect knows a person by exactly one profile."""
    if is_owner(config, platform, user_ids):
        return "owner"
    if not user_ids or platform in ROUTINE_PLATFORMS:
        return "unknown"
    agents = matching_agents(vault, config, user_ids, platform)
    if config.edition == "enterprise":
        return "intellect" if agents else "member" if platform else "unknown"
    people = matching_profiles(vault, config, user_ids, platform)
    if len(people) == 1:
        return "person"
    return "intellect" if len(agents) == 1 and not people else "unknown"
