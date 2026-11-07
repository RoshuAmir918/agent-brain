"""Assemble the generated session startup context from its owning notes."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from . import channel_files, fm, goals, index, works
from .config import SECTIONS, Config
from .tasks import is_open, load_tasks, parse_register
from .vault import Vault

PRIMED_WORDS = 300   # Primed loads in every session; the prime routine writes the most important first
LIBRARY_WORKS = 40   # bound the listing, never the runtime's refusal, conflict or error notices
BEFORE = ("## Before answering\n\n"
          "What you need is usually already in front of you: this file (CURRENT's dated items and Needs you, the open "
          "tasks' Verified, Next and Blocker lines, Primed, the upcoming dates, Goals, the Portrait) and, under the owner's message, the "
          "vault notes that match it and the card of anyone it names. Answer from that in one reply. Open a note only when "
          "the loaded text does not hold the answer, and a person's history only when the question is about them and the "
          "card is not enough. Do not run retrieval the recall block already ran, and do not read a note quoted here in "
          "full. If something loaded bears on the request (a trip, a deadline, a pending decision, an offer waiting on the "
          "owner, a goal), say the connection in a sentence. A task the owner parked has no question for them. A reminder "
          "the owner asks for is a dated Triggers row, never a Needs you line. An answer any assistant could give without "
          "this vault is a miss; so is a lookup for what was already here.")
PORTRAIT = ("Who you think {human} is, ranked within each section: `said` they told you, `seen` you watched them do it "
            "twice, `guess` you inferred it and have not tested it. Act on a guess only to prepare or offer, never outward. "
            "This is yours about {human} and is never told to anyone else, whatever a rule allows.")
COMPANY_PORTRAIT = ("What you understand of {human}, ranked within each section: where it is heading, what it holds to, how "
                    "it likes work done, how it decides, where effort goes and what it has decided against. `said` its "
                    "leadership stated it or the roadmap, goals or initiatives say it; `seen` a pattern across its decisions "
                    "and reviews; `guess` your inference. Let it aim your work: each After line's `for:` is where you can help "
                    "the company get there, so look for that work; follow the Taste lines before anyone has to correct "
                    "you, and say when a task serves none of the After lines. Cite `said` and `seen` lines to "
                    "anyone. A guess about direction is never stated to the team: it goes to the operator as a confirm.")
UPCOMING_DAYS = 7
UPCOMING_ITEMS = 12
AHEAD = re.compile(r"\b(due|deadline|until|flight|flying|trip|visit|leav|arriv|call|renew|expire|starts?|ends?|meet)", re.I)   # today's lines that look forward, not today's evidence
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december")
DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
                  r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.? (\d{1,2})(?:st|nd|rd|th)?\b(?:,? (\d{4}))?", re.I)


def open_register(vault: Vault, body: str) -> str:
    registered, _ = load_tasks(vault.config)
    open_ids = {task.tid for task in registered if task.state and is_open(task)}
    spans, _ = parse_register(body)
    sections, seen = [], set()
    for task_id, start, end in spans:
        if task_id in open_ids and task_id not in seen:
            sections.append(body[start:end].rstrip())
        seen.add(task_id)
    return "\n\n".join(sections)


def build(vault: Vault, only: tuple[str, ...] | None = None, *, app_changes: bool = True) -> str:
    """The compiled context. `only` narrows it to the sections one run asks for (a routine's declaration), always within
    the sections the vault itself allows."""
    config = vault.config
    allowed = tuple(name for name in config.context.sections if only is None or name in only)
    named = dict(zip(("start", "current", "portrait", "primed", "goals", "triggers", "tasks"), config.context_sources))
    optional = (config.human_portrait, config.agent("Primed.md"), config.human_goals)   # written by the agent, the night and the owner: a vault may not have them yet
    sources = [rel for name, rel in named.items() if name in allowed
               and (rel not in optional or (vault.root / rel).is_file())]
    header = ("Generated nightly by `brain`.\n"
              "Edits to this file are overwritten.\n"
              "Edit the source notes instead: " + ", ".join(config.link(rel) for rel in sources) + ".")
    sections = [header]
    for name in SECTIONS:
        if name not in allowed:
            continue
        if name == "goals" and config.edition == "enterprise":
            continue
        if name == "before":
            sections.append(BEFORE)
        elif name == "upcoming":
            sections.append(upcoming(vault))
        elif name == "directory":
            sections.append(directory(vault))
        elif name == "library":
            if block := library():
                sections.append(block)
        elif named[name] in sources:
            rel = named[name]
            if rel in vault.malformed:
                continue   # its header is unreadable; validate names it
            _, body = fm.read(vault.root / rel)
            if rel == config.agent("Tasks.md"):
                body = open_register(vault, body)
            elif rel == config.agent("Primed.md"):
                body = capped(body, PRIMED_WORDS, config.link(rel))
            elif rel == config.agent("Triggers.md"):
                body = active_triggers(body, config.link(rel))
            elif rel == config.human_portrait:
                rule = COMPANY_PORTRAIT if config.edition == "enterprise" else PORTRAIT
                body = rule.format(human=config.human_name) + "\n\n" + body.strip()
            elif rel == config.human_goals:
                body = goals.context(config, app_changes=app_changes)
            sections.append(f"## {config.link(rel)}\n\n{body.strip()}")
        elif name == "goals":
            sections.append(f"## {config.link(config.human_goals)}\n\n{goals.context(config, app_changes=app_changes)}")
    sections.append("Conversations and everything else are retrieved on demand with `brain retrieve`.")
    return "\n\n".join(sections) + "\n"


def library() -> str:
    body = works.library()
    if not body:
        return ""
    kept, count = [], 0
    for line in body.splitlines():
        if works.ENTRY.match(line):
            count += 1
            if count > LIBRARY_WORKS:
                continue
        kept.append(line)
    if count > LIBRARY_WORKS:
        kept += ["", f"(cut at {LIBRARY_WORKS} works; the rest is in $AGENT_HOME/works/LIBRARY.md)"]
    return "## Library\n\n" + "\n".join(kept).strip()


def active_triggers(body: str, where: str) -> str:
    """Only the triggers still waiting to fire; the fired and retired ones are records, read on demand."""
    match = re.search(r"^## (?:Fired|Retired)\s*$", body, re.M)
    if match is None:
        return body
    return body[:match.start()].rstrip() + f"\n\n(Fired and retired triggers are in {where}.)"


def capped(body: str, limit: int, where: str) -> str:
    """The first `limit` words, cut at a line, with a pointer to the rest."""
    lines, kept, words = body.strip().splitlines(), [], 0
    for line in lines:
        n = len(line.split())
        if words + n > limit and kept:
            return "\n".join(kept).rstrip() + f"\n\n(cut at {limit} words; the rest is in {where})"
        kept.append(line)
        words += n
    return "\n".join(kept)


def upcoming(vault: Vault, days: int = UPCOMING_DAYS) -> str:
    """Sentences anywhere in the hubs, profiles, histories and registers that name a date in the coming days, so a
    deadline kept in a hub or a friend's visit noted in their history reaches the agent before the day passes."""
    config, today = vault.config, vault.config.today()
    found: dict[tuple[date, str], str] = {}
    for rel, note in sorted(vault.notes.items()):
        history = rel.endswith("/History.md")   # trips and visits land in a person's history first
        kind = vault.kind_for(rel)
        if kind in ("episode", "reflection", "dream", "primed") or rel.startswith(config.log_paths) \
                or (kind == "log" and not history) or f"/{config.agent('Journal/')}" in f"/{rel}":
            continue
        for line in note.body.splitlines():
            sentences = re.split(r"(?<=[.!?])\s+", line.strip(" -*>#|"))
            for i, sentence in enumerate(sentences):
                shown = f"{sentences[i - 1]} {sentence}" if i and len(sentence) < 80 else sentence   # a short sentence leans on the one before for its subject
                for m in DATE.finditer(sentence):   # only this sentence's own dates; the one before had its turn
                    when = mentioned(m, today)
                    if when and (today < when <= today + timedelta(days=days) or when == today and AHEAD.search(sentence)):
                        text = re.sub(r"\[\[(?:[^]|]*\|)?([^]]*)\]\]", r"\1", shown).replace("**", "").strip()[:240]
                        found.setdefault((when, text), rel[:-3])
    ahead = sorted(item for item in found.items() if item[0][0] > today)
    items = sorted([item for item in found.items() if item[0][0] == today][:4] + ahead)[:UPCOMING_ITEMS]
    lines = [f"## Upcoming: the next {days} days", "",
             "Dates named anywhere in the vault's notes. Raise one when it bears on the request or on the owner's plans; "
             "a due date the vault cannot show as done is worth one sentence.", ""]
    lines += [f"- {when:%a %b %-d} · {text} · [[{rel}]]" for (when, text), rel in items] or ["- nothing dated"]
    blocks = [block for block in (channel_files.calendar(vault, days), channel_files.canvas(vault, days)) if block]
    return "\n\n".join(["\n".join(lines), *blocks])


def mentioned(m: re.Match, today: date) -> date | None:
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        month = [name[:3] for name in MONTHS].index(m.group(4).lower()[:3]) + 1
        when = date(int(m.group(6) or today.year), month, int(m.group(5)))
        return when if m.group(6) or when >= today - timedelta(days=180) else date(today.year + 1, month, int(m.group(5)))
    except ValueError:
        return None


def directory(vault: Vault) -> str:
    """Most active notes with index lines, then names of everything the vault knows."""
    hubs, people, agents, reflections = [], [], [], []
    for rel, note in sorted(vault.notes.items()):
        kind = vault.kind_for(rel)
        if note.data.get("kind") in ("orientation", "log"):
            continue   # templates and registries' rules are not topics
        if kind == "profile" and rel.startswith(vault.config.agents_dir + "/"):
            agents.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "profile":
            people.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "reflection":
            reflections.append(note.path.stem)
        elif kind == "hub":
            hubs.append(rel[:-3])
    registered, archived = load_tasks(vault.config)
    tasks = [f"{t.tid} {t.title}" for t in registered + archived if t.state and is_open(t)]
    _, trig = fm.read(vault.root / vault.config.agent("Triggers.md"))
    triggers = sorted(set(re.findall(r"\bTR-\d{3}\b", trig)))
    selected, _, _ = index.hot_selection(vault, vault.config.context.hot_words)
    lines = ["## Directory: what the vault knows", ""]
    if selected:
        lines += ["Most active, with what each holds:", ""]
        lines += [f"- [[{vault.config.link(n.rel[:-3])}|{n.stem}]] — {index.index_line(n)}" for n in selected] + [""]
    lines += ["Names only. The notes matching each message are already recalled under it; run `brain retrieve` only when a request "
              "touches one of these and nothing recalled or loaded answers it.", ""]
    lines.append("- Hubs and topics: " + ", ".join(hubs))
    lines.append("- People with profiles: " + (", ".join(people) or "none"))
    lines.append("- Agents registered: " + (", ".join(agents) or "none"))
    if reflections:
        lines.append("- Reflections: " + ", ".join(reflections))
    lines.append("- Open tasks: " + "; ".join(tasks))
    if triggers:
        lines.append("- Triggers: " + ", ".join(triggers))
    return "\n".join(lines)


def summary(text: str, limit: int) -> str:
    words = len(text.split())
    warning = f"; WARNING: over {limit} words" if words > limit else ""
    return f"context: {words} words{warning}"


def write(vault: Vault, out: Path | None = None) -> str:
    text = build(vault, app_changes=False)
    fm.write_text(out if out is not None else vault.config.repo / ".context.md", text)
    return summary(text, vault.config.context.hot_words)
