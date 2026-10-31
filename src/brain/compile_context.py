"""Assemble the generated Hermes startup context from its owning notes."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from . import fm
from .config import SECTIONS, Config
from .tasks import is_open, load_tasks, parse_register
from .vault import Vault

WORD_LIMIT = 6000
BEFORE = ("## Before answering\n\n"
          "What you need is usually already in front of you: this file (CURRENT's dated items and Needs you, the open "
          "tasks' Verified, Next and Blocker lines, Primed, the upcoming dates, Goals) and, under the owner's message, the "
          "vault notes that match it and the card of anyone it names. Answer from that in one reply. Open a note only when "
          "the loaded text does not hold the answer, and a person's history only when the question is about them and the "
          "card is not enough. Do not run retrieval the recall block already ran, and do not read a note quoted here in "
          "full. If something loaded bears on the request (a trip, a deadline, a pending decision, an offer waiting on the "
          "owner, a goal), say the connection in a sentence. A task the owner parked has no question for them. A reminder "
          "the owner asks for is a dated Triggers row, never a Needs you line. An answer any assistant could give without "
          "this vault is a miss; so is a lookup for what was already here.")
UPCOMING_DAYS = 7
UPCOMING_ITEMS = 12
AHEAD = re.compile(r"\b(due|deadline|until|flight|flying|trip|visit|leav|arriv|call|renew|expire|starts?|ends?|meet)", re.I)   # today's lines that look forward, not today's evidence
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december")
DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?\b(?:,? (\d{4}))?",
                  re.I)


def repo_root(config: Config) -> Path:
    return config.repo


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


def automatic_load(body: str) -> str:
    heading = re.search(r"^## Automatic load[ \t]*\r?$", body, re.M | re.I)
    if not heading:
        return ""
    following = re.search(r"^#{1,2} ", body[heading.end():], re.M)
    end = heading.end() + following.start() if following else len(body)
    return body[heading.start():end].rstrip()


def build(vault: Vault) -> str:
    config = vault.config
    named = dict(zip(("start", "current", "primed", "goals", "triggers", "tasks", "hot"), config.context_sources))
    optional = (config.agent("Primed.md"), config.human_goals)   # Primed is written by the night, Goals with the owner: a vault may not have them yet
    sources = [rel for name, rel in named.items() if name in config.context.sections
               and (rel not in optional or (vault.root / rel).is_file())]
    header = ("Generated nightly by `brain`.\n"
              "Edits to this file are overwritten.\n"
              "Edit the source notes instead: " + ", ".join(config.link(rel) for rel in sources) + ".")
    sections = [header]
    for name in SECTIONS:
        if name not in config.context.sections:
            continue
        if name == "before":
            sections.append(BEFORE)
        elif name == "upcoming":
            sections.append(upcoming(vault))
        elif name == "directory":
            sections.append(directory(vault))
        elif named[name] in sources:
            rel = named[name]
            _, body = fm.read(vault.root / rel)
            if rel == config.agent("Tasks.md"):
                body = open_register(vault, body)
            elif rel == "Hot Set.md":
                body = automatic_load(body)
            sections.append(f"## {config.link(rel)}\n\n{body.strip()}")
    sections.append("Warm Index, Conversations and everything else are retrieved on demand with `brain retrieve`.")
    return "\n\n".join(sections) + "\n"


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
        _, body = fm.read(note.path)
        for line in body.splitlines():
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
    return "\n".join(lines)


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
    """Names only: what the vault knows, so the agent can decide to look."""
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
    lines = ["## Directory: what the vault knows", "",
             "Names only. The notes matching each message are already recalled under it; run `brain retrieve` only when a request "
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


def summary(text: str, limit: int = WORD_LIMIT) -> str:
    words = len(text.split())
    warning = f"; WARNING: over {limit} words" if words > limit else ""
    return f"context: {words} words{warning}"


def write(vault: Vault, out: Path | None = None) -> str:
    text = build(vault)
    (out if out is not None else repo_root(vault.config) / ".hermes.md").write_text(text, encoding="utf-8")
    return summary(text, vault.config.context.hot_words)
