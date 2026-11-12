"""Read-only speaker identity and disclosure context for a runtime session."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from . import access, channels as registry, directory, fm, responsibilities, retrieve
from .compile_context import DATE
from .config import Config
from .identity import matching_agents, matching_profiles, role
from .vault import WIKILINK_RE, Note, Vault

OWNER_CARD_WORDS = 450   # Working together (up to 250 words), the Impression until a portrait replaces it, and what the agent can reach
MENTION_WORDS = {"line": 35, "card": 150}
HISTORY_WORDS = 80
PROFILE_WORD_LIMIT = 250
STANDING_WORDS = 150   # an owner's standing directions about one person
WORKING_WORDS = 250   # the owner's Working together, newest lines kept: the skills keep the section under this
SHARED_WORDS = 1500   # open work shared with an intellect, with its hub: the migration hub that settled a Nov 8 ask ran 1,200
TELLABLE_HEADING = "What you may tell {name} about {owner}, and nothing else:"
RECALL_HEADER = "Vault notes that match this message (names and index lines; open one only if what is loaded does not answer):"
FULL_HEADER = "Vault notes that match this message (their text, cut to fit):"
WORK_HEADER = ("Your own work that this message touches, from your notes (their text, cut to fit). Answer from how you do it "
               "and what you have learned doing it:")
WORK_CHARS = 6000   # an answering session cannot open a note, so it gets the text; a hub and its evidence note
WORK_NOTES = 4
SEARCH_CHARS = 8000
SEARCH_NOTES = 6
PASSAGE_CHARS = 2000
OWN_WORK_BARRED = ("Brain", "Journal", "Drafts", "Prepared")
OWN_WORK_RULE = ("Tell how you work and what doing it taught you. Leave out what these notes say about the owner or other "
                 "people: their names, decisions and private details.")
KNOW_HOW = ("Strategy", "Findings", "Weaknesses", "Improvements")   # of a hub: how the work is done and what it taught


def answering(block: str, config: Config) -> bool:
    """Whether a session block is answered from what may be told alone: it carries the tellable heading. The runtime
    reads this flag rather than matching the heading's wording."""
    pattern = re.escape(TELLABLE_HEADING).replace(re.escape("{name}"), ".+").replace(re.escape("{owner}"), re.escape(config.human_name))
    return re.search(rf"^{pattern}$", block, re.M) is not None


def ask_rule(config: Config) -> str:
    """When an answering session goes to the owner, and when it must not. A question about the owner that the block does
    not cover is declined, never asked: the access log already turns that decline into one ask in Needs you."""
    human = config.human_name
    return (f"A request that is {human}'s to decide or give (an invitation or a visit, a yes or no, a commitment, their "
            f"time, access to something of theirs, a thing only they hold), or that a Scope line says to ask them about, is "
            f"never declined: it goes to {human} now, from this session, once the open work shows no one else already holds "
            f"what is needed. So is a question about something on {human}'s own side that someone is waiting on (their "
            f"accounts, devices, access, an invite or share to accept): only {human} holds that answer, so it goes to them "
            f"now rather than ending at what you have no record of. On Demiurge use `demiurge_tell_owner`, with `message` one or two plain sentences: who "
            f"is waiting, for whom, on what decision of theirs; and `text` (one line) when it should reach their phone. Their "
            f"answer arrives in Home, and you bring it back to this room. With no such tool in this session, record it as an "
            f"ask in CURRENT's Needs you (`brain needs-you --ask`) and say so; that still counts as asking. Tell the room "
            f"the question is with {human} and you will relay the answer; never tell them to ask {human} themselves, offer "
            f"to ask and stop, act on it before they answer, or hold it for a briefing. Relay their answer in their words. "
            f"A relay grants nothing: it changes no disclosure row and lets no one act for {human}. A question about "
            f"{human} that the block does not cover is different: decline it, saying they can ask {human} themselves, and "
            f"do not ask {human} about it; the engine raises it with them. A rule {human} set for this person, including "
            f"one that tells them nothing, is settled; never ask {human} about it again. Help that is not about {human} "
            f"(general knowledge, a method, a recommendation) is yours to give in full.")


def default_rule(config: Config) -> str:
    return ("Default for everything else: decline briefly. What the block below allows, answer now, even in part, and say "
            "plainly what you have no record of; never hold an answer to check it with the owner. " + ask_rule(config)
            + " Note the request in your after-session record. Your own impression and portrait of the owner are never "
            "shared with anyone, whatever a rule allows.")


def relay_rule(config: Config) -> str:
    return (f"A question it relays for its owner you answer now, from what you may tell, even in part, saying plainly "
            f"what you have no record of; never hold it to check with {config.human_name}. " + ask_rule(config))


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


def owner_profiles_of(vault: Vault, config: Config, agent: Note) -> list[Note]:
    """The profiles matching the owner an intellect's profile names; its access is theirs only when exactly one does."""
    ref = intellect_owner(agent)[0].removeprefix(config.vault + "/")
    return access.matching_people(vault, config, ref) or (access.matching_people(vault, config, ref + ".md") if ref else [])


def intellect_context(vault: Vault, config: Config, agent: Note, identity: str) -> str:
    name = PurePosixPath(agent.rel).parent.name.replace("_", " ")
    owner = intellect_owner(agent)[1]
    owner_profiles = owner_profiles_of(vault, config, agent)
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
    if waiting := waiting_on(config, agent.rel, name):
        blocks.append(waiting)
    if len(owner_profiles) == 1:
        if rules := standing(config, owner_profiles[0], owner):
            blocks.append(rules)
        blocks.append(shared_work(vault, config, agent.rel, name, owner_profiles[0].rel))
    blocks.append(tellable_block(vault, config, owner_profiles[0].rel if len(owner_profiles) == 1 else None,
                                 owner if len(owner_profiles) == 1 else "anyone"))
    return "\n\n".join(blocks)


def waiting_on(config: Config, agent: str, name: str) -> str:
    """This agent's own tasks that wait on this intellect, so an answer it brings resumes the task here rather than being
    forwarded to the owner (Nameless, 2026-11-11: Orphan's answers were relayed to Arslan at 14:09 and T-059 stayed
    blocked until he asked what was going on). Never told to the intellect; disclosure does not gate it."""
    from . import loops
    rows = [f"- {task.tid} — {task.title} (blocker: {fm.field(task.body, 'Blocker') or 'none recorded'})"
            for task in loops.waiting_on(config, agent, name)]
    if not rows:
        return ""
    return (f"Your tasks waiting on {name}:\n" + "\n".join(rows) + "\n"
            f"When what {name} says answers one of them, record it in this turn with `task_answer` (the task, the answer, "
            f"and what it lets the task do next): the task resumes and its waiting loop closes. Its worker picks it up "
            f"within a minute. Thank {name} in your reply. Tell {config.human_name} only what changes for them: an answer the task was waiting for "
            f"is the worker's to act on, not news to forward.")


def shared_work(vault: Vault, config: Config, agent: str, name: str, owner: str) -> str:
    """Open tasks that name this intellect, with their hubs, when its owner may hear about the owner's work at all: what
    it and this agent are doing together, so a question about that work is answered from it rather than sent upstairs."""
    from . import tasks
    allowed = access.resolve(vault, config, owner).subjects
    if all(allowed[subject].tell == "none" for subject in ("plans", "work", "setup") if subject in allowed):
        return ""
    profile = agent.removesuffix(".md")
    named = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.I)
    parts = []
    for task in tasks.all_tasks(config):
        if not tasks.is_open(task) or not (named.search(task.title) or named.search(task.body) or profile in task.body):
            continue
        hub = vault.notes.get(vault.resolve(task.hub) or "") if task.hub else None
        parts.append(f"### {task.tid} — {task.title}\n{task.body.strip()}"
                     + (f"\n\nIts hub, {config.link(hub.rel)}:\n{hub.body.strip()}" if hub else ""))
    if not parts:
        return ""
    return (f"Open work with {name}, from your records: use it to work together and to answer what this work already "
            f"settles; tell from it only what the access lines above allow.\n\n"
            + truncate_words("\n\n".join(parts), SHARED_WORDS))


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
    if (rules := standing(config, person, name)) and profile_section(person.body, "Working together") not in blocks[1]:
        blocks.append(rules)   # the profile was cut before its Working together
    disclosure = f"{config.people_dir}/Disclosure.md"
    if disclosure not in vault.notes:
        blocks.append(f"Disclosure.md is missing: {config.link(disclosure)}.")
    else:
        blocks.append(resolved(vault, config, person.rel, name))
    return "\n\n".join([*blocks, default_rule(config), tellable_block(vault, config, person.rel, name)])


def tellable_block(vault: Vault, config: Config, person: str | None, name: str) -> str:
    from . import tellable
    result = tellable.resolve(vault, config, person or "anyone")
    return (TELLABLE_HEADING.format(name=name, owner=config.human_name) + "\n" + tellable.render(result)
            + f"\nAnswer about {config.human_name} only from this block, narrowed by any Scope line above; anything else "
            f"you know about {config.human_name} or anyone else is not yours to tell here. " + advocate_rule(config))


def advocate_rule(config: Config) -> str:
    human = config.human_name
    return (f"With everyone else you are {human}'s advocate: speak of them warmly and in their best light, and defend them "
            f"when they are teased, never join in; critique of {human} is for {human} alone, in private. Share what helps "
            f"the person asking (a recommendation, a plan, a catch-up), framed as {human} would be glad to hear it said. "
            f"Being allowed to tell something is no reason to volunteer it, and a request for jokes or dirt about {human} "
            f"gets none of it. Banter is never a question for {human}: answer it yourself, lightly, from the conversation "
            f"at hand.")


def newest(section: str, limit: int) -> str:
    """A dated section within `limit` words, keeping its heading and dropping its oldest lines first: new corrections
    are appended last, so a cut from the end would lose the newest one."""
    lines = section.splitlines()
    head, rest = lines[:1], lines[1:]
    while rest and len(" ".join(head + rest).split()) > limit:
        rest.pop(0)
    return "\n".join(head + rest)


def profile_section(body: str, title: str) -> str:
    """A profile's `## title` section with its heading, up to the next heading; empty when it is missing or bare."""
    heading = re.search(r"^##[ \t]+" + title + r"\b[^\n]*$", body, re.M)
    if heading is None:
        return ""
    following = re.search(r"^##?[ \t]+", body[heading.end():], re.M)
    section = body[heading.start():heading.end() + (following.start() if following else len(body))].strip()
    return section if len(section.split()) > 1 else ""


def standing(config: Config, person: Note, name: str) -> str:
    """The owner's standing directions about one person ("never relay what Chris sends"), from that person's Working
    together. They hold whenever that person, or an intellect acting for them, is in the session."""
    section = profile_section(person.body, "Working together")
    if not section:
        return ""
    return (f"How {config.human_name} wants things handled with {name}, from {config.link(person.rel)}. These hold in "
            f"this session, whoever is asking on {name}'s behalf:\n\n" + truncate_words(section, STANDING_WORDS))


def owner_card(vault: Vault, config: Config) -> str:
    """How to work with the owner and what the agent has come to think of them, from their profile, kept short."""
    note = vault.notes.get(config.human_profile)
    if note is None:
        return ""
    parts = []
    portrait = (vault.root / config.human_portrait).is_file()   # the portrait, in the session context, holds the impression now
    for title in ("Working together",) + (() if portrait else ("Impression",)):
        if section := profile_section(note.body, title):
            parts.append(newest(section, WORKING_WORDS) if title == "Working together" else section)
    reach = [f"{row.channel} ({row.whose}" + (f", {row.identity}" if row.identity else "") + ")"
             + (f": read {row.read}" if row.read else "") + (f"; write {row.write}" if row.write else "")
             for row in registry.live(registry.load(vault))]
    if reach:
        parts.append("What you can reach, from " + config.link(config.agent("Channels.md")) + ": " + "; ".join(reach)[:600]
                     + ". Anything not listed, you cannot reach from here.")
        parts.append(f"Between scheduled passes, watch wakes only for new items a screen judges actionable before the next "
                     f"review. When {config.human_name} says what should or should not interrupt them, add it in their words "
                     f"to {config.link(config.agent('Wake.md'))}, one line each.")
    if not parts:
        return ""
    yours = "your Portrait of them" if portrait else "your Impression"
    return (truncate_words("\n\n".join(parts), OWNER_CARD_WORDS) + f"\n\nTalk about {yours} only with "
            f"{config.human_name} alone, never where anyone else can read it. Full profile: {config.link(config.human_profile)}.")


def owner_messages(config: Config) -> str:
    """How to write to the owner, in every owner session. The full policy in templates/owner-messages.md is for the
    routines that decide whether to send at all; in a chat the owner already asked, only its shape applies."""
    human = config.human_name
    return (f"## Messages to {human}\n\nWrite for {human} reading cold on a phone: lead with the one thing that matters, "
            "then at most a few short lines; details stay in the task, the Library or the note. No task ids, paths, tool, "
            "skill or routine names, or your method's terms. Say a thing once; one short correction replaces a wrong one; "
            "an unchanged reminder is not repeated unless its deadline is within a day. Claim only what happened (accepted "
            "is not delivered, tried is not done), cite only what you opened in this conversation, and give a time only "
            f"once the pace is proven. A question for {human} is one line answerable in a word, with a short Why.")


def resolved(vault: Vault, config: Config, person: str | None, name: str) -> str:
    """What this person may learn and ask for, as `brain access` resolves it, in a few lines."""
    result = access.resolve(vault, config, person)
    groups = ", ".join(PurePosixPath(rel).stem for rel in result.groups)
    lines = [f"What {name} may learn, resolved from {config.link(f'{config.people_dir}/Disclosure.md')}"
             + (f" (groups: {groups})" if groups else "") + ". Gist is the shape without specifics (whether and "
             "roughly when); details are the specifics:"]
    acting = {"ask": f"ask {config.human_name} before acting for them", "yes": "may act for them"}
    refused: dict[str, list[str]] = {}
    for subject, value in result.subjects.items():
        if value.tell == "none" and value.do == "no" and value.tell_rule:
            refused.setdefault(value.tell_rule, []).append(subject)
        # Each subject already carries an `anything` grant's effect; listing `anything` itself would hide exceptions.
        if subject == "anything" or (value.tell == "none" and value.do == "no"):
            continue
        parts = [f"tell {value.tell}" + (f" ({value.tell_rule})" if value.tell_rule else "")]
        if value.do != "no":
            parts.append(acting[value.do] + (f" ({value.do_rule})" if value.do_rule else ""))
        lines.append(f"- {subject}: " + "; ".join(parts))
    for rule, subjects in refused.items():
        what = "anything" if "anything" in subjects else ", ".join(subjects)
        lines.append(f"- {what}: nothing ({rule}), {config.human_name}'s own decision for them, settled.")
    lines.append("- everything else: nothing, and never act for them.")
    lines.extend(f"Scope: {text}" for text in dict.fromkeys(result.scope.values()))
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
            f"becomes a `when` loop, not a reason to keep the migration open. Finish with `brain migrate-done`.")


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
                                                          owner_card(vault, config), owner_messages(config)) if part), config)
    if who == "intellect":
        return intellect_context(vault, config, matching_agents(vault, config, user_ids, platform)[0], identity)
    if who == "person":
        return known_context(vault, config, matching_profiles(vault, config, user_ids, platform)[0], identity)
    if platform == "cron" and not user_ids:   # the agent's own routine run: what it sends lands in the owner's Home
        return with_notice(owner_card(vault, config), config)
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


def prefetch(vault: Vault, config: Config, message: str, *, owner: bool, seen: set[str], answering: bool = False,
             platform: str = "", user_ids: tuple[str, ...] = ()) -> dict:
    """What the runtime puts before an owner's message, by brain.toml's [context] and its BRAIN_* overrides: the people
    it names (in the owner's own sessions, each once per session: `seen` are those already shown) and the vault notes
    that match it. `people` is everyone it names, for the runtime to add to `seen`. An answering session gets only the
    intellect's own work that matches, and only when the owner lets the speaker learn how it works (`setup`)."""
    context = config.context
    words = [word for word in re.findall(r"[^\W_]+", message.lower()) if word not in retrieve.STOPWORDS]
    if answering:
        recall = context.prefetch and len(words) >= 2 and may_hear_how_you_work(vault, config, platform, user_ids)
        return {"text": _own_work(vault, message) if recall else "", "people": []}
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


def may_hear_how_you_work(vault: Vault, config: Config, platform: str, user_ids: tuple[str, ...]) -> bool:
    """Whether the owner's disclosure table lets this speaker (an intellect through its owner) learn `setup`: how the
    intellect works. Its know-how names people and the owner's decisions, so no grant means none of it."""
    if f"{config.people_dir}/Disclosure.md" not in vault.notes:
        return False
    who = role(vault, config, platform, user_ids)
    if who in ("owner", "member"):
        return False
    person = None
    if who == "intellect":
        agents = matching_agents(vault, config, user_ids, platform)
        owners = owner_profiles_of(vault, config, agents[0]) if len(agents) == 1 else []
        if len(owners) != 1:
            return False
        person = owners[0].rel
    elif who == "person":
        profiles = matching_profiles(vault, config, user_ids, platform)
        if len(profiles) != 1:
            return False
        person = profiles[0].rel
    setup = access.resolve(vault, config, person).subjects.get("setup")
    return bool(setup) and setup.tell != "none"


def _own_scope(vault: Vault) -> tuple[list[str], set[str]]:
    """The responsibility hubs, with the notes they link in the intellect's own subfolders, such as its experiments:
    never its journal, logs, drafts or prepared work, made for the owner, nor anything outside its folder."""
    config = vault.config
    hubs = [entry["hub"] for entry in responsibilities.discover(vault)]
    barred = (*config.log_paths, *(config.agent(name + "/") for name in OWN_WORK_BARRED))

    def own(rel: str) -> bool:
        return rel.startswith(config.agent_dir + "/") and "/" in rel[len(config.agent_dir) + 1:] and not rel.startswith(barred)

    return hubs, set(hubs) | {target for hub in hubs for target in vault.links_in(vault.notes[hub].body) if own(target)}


def search_own_work(vault: Vault, config: Config, query: str, *, platform: str = "", user_ids: tuple[str, ...] = ()) -> str:
    """The passages of the intellect's own work that best match `query`, under their headings, for an answering
    session's search: a run record deep in a long note reaches a peer, where prefetch's cut-down text cannot."""
    if not may_hear_how_you_work(vault, config, platform, user_ids):
        return (f"{config.human_name} has not allowed this speaker to learn how you work, so share none of it. "
                f"Say so, and offer to ask {config.human_name} if they want it.")
    hubs, within = _own_scope(vault)
    terms = retrieve.tokens(query)
    passages = []
    for candidate in retrieve.search(vault, query, SEARCH_NOTES, within=within):
        rel = candidate.path.removeprefix(vault.config.vault + "/")
        path: list[tuple[int, str]] = []
        for block in re.split(r"\n\s*\n|\n(?=#{1,6} )", vault.notes[rel].body):
            block = block.strip()
            if re.match(r"#{1,6} ", block):
                level = len(block) - len(block.lstrip("#"))
                path = [*(entry for entry in path if entry[0] < level), (level, block.splitlines()[0].lstrip("# ").strip())]
                block = "\n".join(block.splitlines()[1:]).strip()
            titles = [title for level, title in path if level > 1]
            if not block or (rel in hubs and (titles[:1] or [""])[0] not in KNOW_HOW):   # the hub's own section, not a heading under another
                continue
            hits = len(terms & retrieve.tokens(block))
            if hits:
                passages.append((-hits, -candidate.score, rel, " > ".join(titles), block[:PASSAGE_CHARS]))
    if not passages:
        return "Nothing in your own work matches that. Try other words, or say you have no record of it."
    out, used = [OWN_WORK_RULE], 0
    for _, _, rel, heading, block in sorted(passages, key=lambda passage: passage[:2]):
        entry = f"## {rel}{' > ' + heading if heading else ''}\n\n{block}"
        if used + len(entry) > SEARCH_CHARS:
            break
        out.append(entry); used += len(entry)
    return "\n\n".join(out)


def _own_work(vault: Vault, message: str) -> str:
    hubs, within = _own_scope(vault)
    found = retrieve.search(vault, message[:300], min(vault.config.context.prefetch_limit, WORK_NOTES), within=within)
    if not found:
        return ""

    def body(candidate) -> str:
        rel = candidate.path.removeprefix(vault.config.vault + "/")
        if rel not in hubs:
            return retrieve.text(vault, candidate, "full")
        text = vault.notes[rel].body
        spans = [(title, fm.section(text, title)) for title in KNOW_HOW]
        return "\n\n".join(f"### {title}\n{text[span[0]:span[1]].strip()}" for title, span in spans if span and text[span[0]:span[1]].strip())

    share = WORK_CHARS // len(found)
    return "\n\n".join([WORK_HEADER, OWN_WORK_RULE, *(f"## {candidate.path}\n\n{body(candidate)[:share]}" for candidate in found)])
