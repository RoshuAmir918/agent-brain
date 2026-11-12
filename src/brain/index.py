"""Index lines and activation-ordered selection for the context directory."""
from __future__ import annotations

from . import fm
from .vault import WIKILINK_RE, Vault

MAX_INDEX_WORDS = 25


def plain(text: str) -> str:
    """Wikilinks become their display text; pipes are dropped so the line is table-safe."""
    text = WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1).rsplit("/", 1)[-1]).strip(), text)
    return text.replace("|", "/").replace("**", "")


def index_line(note) -> str:
    line = (note.data.get("index_line") or "").strip() or fm.first_line(note.body)
    line = plain(line)
    words = line.split()
    if len(words) > MAX_INDEX_WORDS:
        line = " ".join(words[:MAX_INDEX_WORDS]) + "…"
    return line


def hot_selection(vault: Vault, budget_words: int):
    hot = [n for n in vault.notes.values() if n.data.get("tier") == "hot"
           and vault.kind_for(n.rel) not in ("orientation", "log", "primed", "episode", "dream", "draft")
           and not (vault.kind_for(n.rel) == "responsibility" and n.data.get("status") == "retired")]
    hot.sort(key=lambda n: (not n.data.get("pinned", False), -float(n.data.get("activation", -99)), n.rel))
    selected, overflow, total = [], [], 0
    for n in hot:
        words = len(n.body.split())
        if total + words > budget_words and not n.data.get("pinned", False):
            overflow.append(n)
        else:
            selected.append(n)
            total += words
    return selected, overflow, total
