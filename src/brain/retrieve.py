"""Deterministic, read-only retrieval over note metadata and local links."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import index, triggers
from .tasks import parse_register
from .vault import Vault

TOKEN_RE = re.compile(r"\b(?:tr-\d{3}|t-\d{3}|\d{4}-\d{2}-\d{2}|[^\W_]+)\b", re.I)
STOPWORDS = set("a an and are as at be been but by can could did do does for from had has have how "
                "i if in into is it its me my not of on or our that the their them there these they "
                "this those to was we were what when where which who why will with would you your".split())
FACTORS = {"hot": 1.5, "warm": 1.0, "cold": 0.6}
EXCLUDED = {"log", "dream", "primed"}


@dataclass
class Candidate:
    path: str
    tier: str
    score: float = 0.0
    reason: str = ""

    def add(self, score: float, reason: str) -> None:
        self.score += score
        self.reason = "; ".join(filter(None, (self.reason, reason)))


def tokens(text: str) -> set[str]:
    return {term for term in TOKEN_RE.findall(text.lower().replace("_", " "))
            if len(term) >= 3 and term not in STOPWORDS}


def eligible(vault: Vault, rel: str) -> bool:
    return (rel not in ("Hot Set.md", "Warm Index.md") and not rel.startswith("Conversations/")
            and vault.kind_for(rel) not in EXCLUDED
            and vault.notes[rel].data.get("kind") not in EXCLUDED)


def metadata_score(vault: Vault, note, terms: set[str]) -> Candidate:
    tier = note.data.get("tier", "warm")
    candidate = Candidate(vault.config.link(note.rel), tier)
    title = " ".join(re.findall(r"^# (.+)$", note.body, re.M))
    fields = (("title/path", str(Path(note.rel).with_suffix("")) + " " + title, 3),
              ("index line", index.index_line(note), 2),
              ("heading", " ".join(re.findall(r"^## (.+)$", note.body, re.M)), 1))
    for name, text, weight in fields:
        matches = terms & tokens(text)
        if matches:
            candidate.add(weight * len(matches) * FACTORS.get(tier, 1.0),
                          f"{name}: {', '.join(sorted(matches))}")
    return candidate


def spread(vault: Vault, candidates: dict[str, Candidate]) -> None:
    top = sorted((rel for rel in candidates if candidates[rel].score > 0),
                 key=lambda rel: (-candidates[rel].score, rel))[:5]
    linked = {}
    for rel in top:
        for target in vault.links_in(vault.notes[rel].body):
            if target in candidates:
                linked.setdefault(target, []).append(rel)
    for target, sources in linked.items():
        candidates[target].add(1.0, "linked from " + ", ".join(vault.config.link(rel) for rel in sources))


def id_matches(vault: Vault, terms: set[str]) -> list[Candidate]:
    matches = []
    register = vault.notes.get(vault.config.agent("Tasks.md"))
    if register:
        _, tasks = parse_register(register.body)
        seen = set()
        for task in tasks:
            if task.tid.lower() in terms and task.tid not in seen:
                matches.append(Candidate(f"{vault.config.link(register.rel)}#{task.tid} — {task.title}",
                                         task.state.get("tier", register.data.get("tier", "warm")),
                                         5.0, f"ID match: {task.tid} — {task.title}"))
            seen.add(task.tid)
    trigger_note = vault.notes.get(vault.config.agent("Triggers.md"))
    if trigger_note:
        for trigger in triggers.parse(trigger_note.body):
            if trigger.id.lower() in terms:
                matches.append(Candidate(vault.config.link(trigger_note.rel),
                                         trigger_note.data.get("tier", "warm"), 5.0,
                                         f"ID match: {trigger.row()}"))
    return matches


def text(vault: Vault, candidate: Candidate, form: str) -> str:
    """A candidate's index line (snippet) or body (full); a task match gives its own entry."""
    rel, _, anchor = candidate.path.removeprefix(vault.config.vault + "/").partition("#")
    note = vault.notes[rel]
    task = next((task for task in parse_register(note.body)[1] if anchor.startswith(task.tid + " ")), None) if anchor else None
    if task is not None:
        return task.title if form == "snippet" else task.body.strip()
    return index.index_line(note) if form == "snippet" else note.body.strip()


def search(vault: Vault, query: str, limit: int = 12) -> list[Candidate]:
    terms = tokens(query)
    if not terms:
        return []
    candidates = {rel: metadata_score(vault, note, terms) for rel, note in vault.notes.items() if eligible(vault, rel)}
    spread(vault, candidates)
    for rel, candidate in candidates.items():
        if candidate.score == 0:
            matches = terms & tokens(vault.notes[rel].body)
            if matches:
                candidate.add(min(1.5, 0.5 * len(matches)), "body match: " + ", ".join(sorted(matches)))
    scored = [candidate for candidate in candidates.values() if candidate.score > 0]
    scored.extend(id_matches(vault, terms))
    return sorted(scored, key=lambda candidate: (-candidate.score, candidate.path, candidate.reason))[:max(0, limit)]
