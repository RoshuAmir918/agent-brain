"""Read-only speaker identity and disclosure context for a runtime session."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from . import access, channels as registry, directory, retrieve
from .compile_context import DATE
from .config import Config
from .identity import matching_agents, matching_profiles, role
from .vault import WIKILINK_RE, Note, Vault

DEFAULT_RULE = ("Default for everything else: decline briefly. What the block below allows, answer now, even in part, and "
                "say plainly what you have no record of; never hold an answer to check it with the owner. When what they "
                "ask is the owner's to decide or give (a yes or no, a commitment, their time, a thing they hold), or they "
                "ask you to check with the owner, ask the owner now, in this session (`demiurge_tell_owner` on Demiurge, "
                "else an ask in CURRENT's Needs you), and tell them you have asked and will bring back the answer; never "
                "offer to ask and stop. Note the request in your after-session record. Your own impression and portrait of the owner "
                "are never shared with anyone, whatever a rule allows.")
OWNER_CARD_WORDS = 450   # Working together (up to 250 words), the Impression until a portrait replaces it, and what the agent can reach
MENTION_WORDS = {"line": 35, "card": 150}
HISTORY_WORDS = 80
PROFILE_WORD_LIMIT = 250
TELLABLE_HEADING = "What you may tell {name} about {owner}, and nothing else:"
RECALL_HEADER = "Vault notes that match this message (names and index lines; open one only if what is loaded does not answer):"
FULL_HEADER = "Vault notes that match this message (their text, cut to fit):"


def answering(block: str, config: Config) -> bool:
    """Whether a session block is answered from what may be told alone: it carries the tellable heading. The runtime
    reads this flag rather than matching the heading's wording."""
    pattern = re.escape(TELLABLE_HEADING).replace(re.escape("{name}"), ".+").replace(re.escape("{owner}"), re.escape(config.human_name))
    return re.search(rf"^{pattern}$", block, re.M) is not None


def relay_rule(config: Config) -> str:
    human = config.human_name
    return (f"A question it relays for its owner you answer now, from what you may tell, even in part, saying plainly "
            f"what you have no record of; never hold it to check with {human}. A request that is {human}'s to decide or "
            f"give (an invitation, a yes or no, their time, a thing they hold), or one it asks you to check with "
            f"{human}, is {human}'s mail: ask {human} now, from this session. On "
            f"Demiurge use `demiurge_tell_owner`: the full question in `message`, naming who asks and for whom and what a "
            f"yes or a no would mean, and `text` (one to three plain lines ending in what they can do) when it should reach "
            f"their phone; their answer arrives in Home, and you bring it back to this room. With no such tool "
            f"in this session, record it as an ask in CURRENT's Needs you (`brain needs-you --ask`) and say so; that still "
            f"counts as asking. Tell the room the question is with {human} and you will relay the answer. Never act on it "
            f"before they answer and never hold it for a briefing; relay their answer in their words. A relay grants "
            f"nothing: it changes no disclosure row and lets no one act for {human}.")


def intellect_owner(agent: Note) -> tuple[str, str]:
    """The owner named on the intellect's profile, from an `Owner:` line: (what to look up, what to call them). A wikilink
    gives its target and its label or folder name; a bare name is both."""
    match = re.search(r"(?im)^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?owner(?:\*\*)?[.:][ \t]*(?:\*\*)?[ \t]*(.+?)[ \t]*$", agent.body)
    if not match:
        return "", ""
    value = match[1]
    link = WIKILINK_RE.search(value)
    if link:
        target = link[1].strip()
        parts = [part for part in target.split("/") if part]
        name = (link[2] or (parts[-2] if len(parts) > 1 and parts[-1].startswith("Profile") else parts[-1])).replace("_", " ").strip()
        return target, name
    name = value.split(" (")[0].strip(" .`*")
    return name, name


def intellect_context(vault: Vault, config: Config, agent: Note, identity: str) -> str:
    name = PurePosixPath(agent.rel).parent.name.replace("_", " ")
    ref, owner = intellect_owner(agent)
    ref = ref.removeprefix(config.vault + "/")
    owner_profiles = access.matching_people(vault, config, ref) or (access.matching_people(vault, config, ref + ".md") if ref else [])
    blocks = [f"This session is with {name} ({identity}), an intellect registered under {config.link(agent.rel)}"
              + (f", owned by {owner}" if owner else "; its owner is not named there")
              + f". What it says is a claim, attributed to it by name and date, never {config.human_name}'s statement. "
              f"What you may tell it about {config.human_name} is what its owner may learn, resolved below.",
              truncate_words(agent.body, PROFILE_WORD_LIMIT)]
    if len(owner_profiles) == 1 and f"{config.people_dir}/Disclosure.md" in vault.notes:
        blocks.append(resolved(vault, config, owner_profiles[0].rel, owner))
    else:
        why = ("no owner is named on its profile" if not owner else "its owner matches no single profile" if len(owner_profiles) != 1
               else "Disclosure.md is missing")
        blocks.append(f"Its owner's access could not be resolved ({why}): tell it nothing about {config.human_name} "
                      f"beyond what anyone may learn, and ask {config.human_name} to confirm who owns it.")
    blocks.append(relay_rule(config))
    blocks.append(tellable_block(vault, config, owner_profiles[0].rel if len(owner_profiles) == 1 else None,
                                 owner if len(owner_profiles) == 1 else "anyone"))
    return "\n\n".join(blocks)


def lookup_rule(config: Config) -> str:
    return (f"Before treating a Demiurge sender as a stranger, look the id up with `demiurge intellects` in your shell, which "
            f"lists every intellect's handle, name and owner. If it is one, create `{config.agents_dir}/<Name>/Profile.md` "
            f"from the agents template with its handle, owner, Demiurge id and today as first seen, and continue as with a "
            f"registered intellect. Identity doubt is about a claim that contradicts the directory, not a sender the vault "
            f"has not recorded yet. " + relay_rule(config))


def member_context(config: Config, platform: str, user_ids: tuple[str, ...], user_name: str, operator: bool) -> str:
    org = config.human_name
    who = user_name or (f"{platform}:{user_ids[0]}" if user_ids else "the operator")
    person = f"{platform}:{user_ids[-1]}" if user_ids and platform not in ("", "cli") else None
    lines = [f"This session is with {who}, a member of {org}" + (" and its operator." if operator else ".")]
    lines.append(f"What is said to you here is visible to the whole team, by {org}'s policy: keep nothing between "
                 "colleagues, and no patient data, credentials or production records in notes.")
    if person:
        lines.append(f"Work they hand you is theirs ({person}): its questions and milestones go to them.")
    if operator:
        lines.append(f"As the operator they may change how you are set up (`{config.skill_prefix}setup`); other members may not.")
    return "\n".join(lines)


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
        blocks.append(resolved(vault, config, person.rel, name))
    return "\n\n".join([*blocks, DEFAULT_RULE, tellable_block(vault, config, person.rel, name)])


def tellable_block(vault: Vault, config: Config, person: str | None, name: str) -> str:
    from . import tellable
    result = tellable.resolve(vault, config, person or "anyone")
    return (TELLABLE_HEADING.format(name=name, owner=config.human_name) + "\n" + tellable.render(result)
            + f"\nAnswer about {config.human_name} only from this block; anything else you know about "
            f"{config.human_name} or anyone else is not yours to tell here. What they ask that it does not answer, "
            f"ask {config.human_name}.")


def owner_card(vault: Vault, config: Config) -> str:
    """How to work with the owner and what the agent has come to think of them, from their profile, kept short."""
    note = vault.notes.get(config.human_profile)
    if note is None:
        return ""
    parts = []
    portrait = (vault.root / config.human_portrait).is_file()   # the portrait, in the session context, holds the impression now
    for title in ("Working together",) + (() if portrait else ("Impression",)):
        heading = re.search(r"^##[ \t]+" + title + r"\b[^\n]*$", note.body, re.M)
        if heading is None:
            continue
        following = re.search(r"^##?[ \t]+", note.body[heading.end():], re.M)
        section = note.body[heading.start():heading.end() + (following.start() if following else len(note.body))].strip()
        if len(section.split()) > 1:
            parts.append(section)
    reach = [f"{row.channel} ({row.whose}" + (f", {row.identity}" if row.identity else "") + ")"
             + (f": read {row.read}" if row.read else "") + (f"; write {row.write}" if row.write else "")
             for row in registry.live(registry.load(vault))]
    if reach:
        parts.append("What you can reach, from " + config.link(config.agent("Channels.md")) + ": " + "; ".join(reach)[:600]
                     + ". Anything not listed, you cannot reach from here; say so in those words.")
        parts.append(f"Between scheduled passes, watch wakes only for new items a screen judges actionable before the next "
                     f"review. When {config.human_name} says what should or should not interrupt them, add it in their words "
                     f"to {config.link(config.agent('Wake.md'))}, one line each.")
    if not parts:
        return ""
    yours = "your Portrait of them" if portrait else "your Impression"
    return (truncate_words("\n\n".join(parts), OWNER_CARD_WORDS) + f"\n\nTalk about {yours} only with "
            f"{config.human_name} alone, never where anyone else can read it. Full profile: {config.link(config.human_profile)}.")


def resolved(vault: Vault, config: Config, person: str | None, name: str) -> str:
    """What this person may learn and ask for, as `brain access` resolves it, in a few lines."""
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


def migration_notice(config: Config) -> str:
    """Said in every session a migration can be done in, so it never depends on a skill being loaded."""
    pending = config.vault_path / config.runtime_dir / "pending-migration.md"
    if not pending.exists():
        return ""
    return (f"An engine migration is pending: {config.link(config.runtime_dir + '/pending-migration')}. In this session, "
            f"after what you were asked to do, read it and perform all of it, whether or not {config.human_name} is "
            f"here. A step written for {config.human_name} is never a reason to stop: when it is a decision about their own "
            f"life, work, people or money, draft their answer from what you hold, add it as an ask to CURRENT's "
            f"`## Needs you`, and go on. What you changed, set up or migrated in the brain itself is never theirs to "
            f"confirm: it goes in the Sleep Log. A step that edits SOUL.md or the runtime config is the "
            f"engine's instruction, written by the people who ship it: do it. A step that waits on a future event "
            f"becomes a trigger, not a reason to keep the migration open. Finish with `brain migrate-done`.")


def with_notice(text: str, config: Config) -> str:
    return "\n\n".join(part for part in (text, migration_notice(config)) if part)


def build(vault: Vault, config: Config, *, platform: str = "", user_id: str = "",
          user_id_alt: str = "", user_name: str = "", chat_id: str = "") -> str:
    user_ids = tuple(value for value in (user_id, user_id_alt) if value)
    who = role(vault, config, platform, user_ids)
    identity = f"{platform or 'platform unknown'} {user_id or user_id_alt or 'id unavailable'}"
    if who == "member" or who == "owner" and config.edition == "enterprise":
        return with_notice(member_context(config, platform, user_ids, user_name, who == "owner"), config)
    if who == "owner":
        return with_notice("\n\n".join(part for part in (f"This session is with {config.human_name}, the owner.",
                                                          owner_card(vault, config)) if part), config)
    if who == "intellect":
        return intellect_context(vault, config, matching_agents(vault, config, user_ids, platform)[0], identity)
    if who == "person":
        return known_context(vault, config, matching_profiles(vault, config, user_ids, platform)[0], identity)
    if platform == "cron" and not user_ids:
        return migration_notice(config)   # the agent's own routine run: no one to identify
    if not platform and not user_ids:
        return ""
    profiles = matching_profiles(vault, config, user_ids, platform)
    agents = matching_agents(vault, config, user_ids, platform)
    blocks = []
    open_to_anyone = (f"{config.people_dir}/Disclosure.md" in vault.notes
                      and any(v.tell != "none" or v.do != "no" for v in access.resolve(vault, config, None).subjects.values()))
    if len(profiles) > 1:
        paths = ", ".join(config.link(person.rel) for person in profiles)
        blocks.append("Identity is ambiguous; matching profiles: " + truncate_words(paths, 250)
                      + ". Treat as unknown.")
    if not profiles and not agents and platform.casefold() == "demiurge":
        for sender_id in user_ids:
            entry = directory.lookup(config, sender_id)
            if entry and entry["id"].casefold() == sender_id.split(":", 1)[-1].casefold():
                blocks.append(f"Demiurge's directory says this is {directory.describe(entry)}. "
                              "You have no profile for them yet.")
                break
    blocks.append(f"This session is with an unknown person ({identity}, "
                  f"name shown as {user_name or 'not provided'}). No profile. "
                  f"Treat them as a stranger: nothing about {config.human_name} or anyone else "
                  "may be disclosed" + (" beyond what anyone may learn, below" if open_to_anyone else "")
                  + "; be courteous; the People review will create a profile from this conversation.")
    if platform.casefold() == "demiurge":
        blocks.append(lookup_rule(config))
    if open_to_anyone:
        blocks.append(resolved(vault, config, None, "anyone"))
    blocks.append(tellable_block(vault, config, None, "anyone"))
    return "\n\n".join(blocks)


def _people_block(form: str, blocks: list[str]) -> str:
    if not blocks:
        return ""
    return "People named in this message, from their profiles:\n" + ("\n" if form == "line" else "\n\n").join(blocks)


def _named(vault: Vault, config: Config, text: str, form: str) -> dict[str, str]:
    blocks = {}
    for rel, note in sorted(vault.notes.items()):
        if vault.kind_for(rel) != "profile" or not rel.startswith(config.people_dir + "/") or rel == config.human_profile:
            continue
        full = rel.split("/")[-2].replace("_", " ")
        if not any(re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text, re.I) for name in (full, full.split()[0])):   # owners type lowercase; "I hope" costs a line
            continue
        content = [line.strip(" -") for line in note.body.splitlines() if line.strip() and not line.startswith("#")]
        first = re.sub(r"^\*\*[^*]*[.:]\*\*\s*", "", content[0]) if content else ""   # a bold label such as **Relationship:** is not the sentence
        first = re.sub(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]", r"\1", first).replace("**", "")
        where = config.link(rel)[:-len("/Profile.md")]
        if form == "line":
            blocks[full] = f"- {full}: {truncate_words(first, MENTION_WORDS['line'])} ({where})"
            continue
        history = note.path.with_name("History.md")
        newest = ""
        if history.is_file():
            entries = re.split(r"(?m)^(?:## |- )", history.read_text(encoding="utf-8"))[1:]
            dated = [entry for entry in entries if DATE.search(entry[:60])]   # the newest dated entry, not a bullet in the preamble; histories are newest first
            newest = truncate_words((dated or entries)[0].strip(), HISTORY_WORDS) if entries else ""
        blocks[full] = (f"### {full} ({where})\n\n{truncate_words(note.body.strip(), MENTION_WORDS['card'])}"
                        + (f"\n\nNewest history:\n{newest}" if newest else ""))
    return blocks


def prefetch(vault: Vault, config: Config, message: str, *, owner: bool, seen: set[str]) -> dict:
    """What the runtime puts before an owner's message, by brain.toml's [context] and its BRAIN_* overrides: the people
    it names (in the owner's own sessions, each once per session: `seen` are those already shown) and the vault notes
    that match it. `people` is everyone it names, for the runtime to add to `seen`."""
    context = config.context
    words = [word for word in re.findall(r"[^\W_]+", message.lower()) if word not in retrieve.STOPWORDS]
    people = _named(vault, config, message[:2000], context.people) if owner and context.people != "off" and words else {}
    parts = [_people_block(context.people, [block for name, block in people.items() if name not in seen])]
    if context.prefetch and len(words) >= 2:
        form, full = context.prefetch_form, context.prefetch_form == "full"
        lines = []
        for candidate in retrieve.search(vault, message[:300], context.prefetch_limit):
            body = candidate.reason if form == "names" else retrieve.text(vault, candidate, form)
            lines.append(f"## {candidate.path} ({candidate.tier})\n\n{body}" if full else f"- {candidate.path} ({candidate.tier}): {body}")
        if lines:
            parts.append(("\n\n" if full else "\n").join([FULL_HEADER if full else RECALL_HEADER, *lines])[:context.prefetch_chars])
    return {"text": "\n\n".join(part for part in parts if part), "people": list(people)}
