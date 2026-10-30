"""Generated views: the Hot Set and the Warm Index."""
from __future__ import annotations

import re
from datetime import date

from . import fm
from .vault import Vault

MAX_INDEX_WORDS = 25


LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")


def plain(text: str) -> str:
    """Wikilinks become their display text; pipes are dropped so the line is table-safe."""
    text = LINK_RE.sub(lambda m: (m.group(2) or m.group(1).rsplit("/", 1)[-1]).strip(), text)
    return text.replace("|", "/").replace("**", "")


def index_line(note) -> str:
    line = (note.data.get("index_line") or "").strip() or fm.first_line(note.body)
    line = plain(line)
    words = line.split()
    if len(words) > MAX_INDEX_WORDS:
        line = " ".join(words[:MAX_INDEX_WORDS]) + "…"
    return line


def hot_selection(vault: Vault, budget_words: int):
    hot = [n for n in vault.notes.values() if n.data.get("tier") == "hot" and vault.kind_for(n.rel) not in ("orientation", "log", "primed", "episode", "dream", "draft")]
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


def build_hot_set(vault: Vault, today: date, budget_words: int) -> str:
    selected, overflow, total = hot_selection(vault, budget_words)
    lines = ["# Hot set", "", f"Generated {today.isoformat()} by the nightly script. Load only the automatic-load section at session start, after START and CURRENT. Budget {budget_words} words of note body; pinned notes are protected, then unpinned notes compete by activation. Conversation records are never listed.", ""]
    if total > budget_words:
        lines += [f"Pinned load exceeds the budget: {total} words selected, {total - budget_words} words over. Pinned notes remain loaded; no unpinned candidate is admitted beyond the cap.", ""]
    lines += ["## Automatic load", ""]
    for n in selected:
        lines.append(f"- [[{vault.config.link(n.rel[:-3])}|{n.stem}]] — {index_line(n)}")
    lines += ["", "## On demand (effective warm)", "", "Do not load this section automatically. These notes remain reachable here and in Warm Index; source tiers and use histories are unchanged.", ""]
    for n in overflow:
        lines.append(f"- (over budget, load on demand) [[{vault.config.link(n.rel[:-3])}|{n.stem}]] — {index_line(n)}")
    lines.append("")
    return "\n".join(lines)


def build_warm_index(vault: Vault, today: date, cold_open_tasks: list[tuple[str, str]], budget_words: int) -> str:
    warm = [n for n in vault.notes.values() if n.data.get("tier") == "warm" and vault.kind_for(n.rel) not in ("orientation", "log", "primed", "dream")]
    _, overflow, _ = hot_selection(vault, budget_words)
    warm.extend(overflow)
    warm.sort(key=lambda n: n.rel)
    lines = [f"# Warm index", "", f"Generated {today.isoformat()} by the nightly script. One line per warm note and effective-warm hot-budget overflow. Match a request against these lines, then load the matched notes in full. Cold notes are not listed; search for them. Cold open tasks are listed because an open commitment is never hidden.", ""]
    by_dir: dict[str, list] = {}
    for n in warm:
        by_dir.setdefault(n.rel.rsplit("/", 1)[0] if "/" in n.rel else ".", []).append(n)
    for d in sorted(by_dir):
        lines.append(f"## {d if d != '.' else 'Root'}")
        for n in by_dir[d]:
            lines.append(f"- [[{vault.config.link(n.rel[:-3])}|{n.stem}]] — {index_line(n)}")
        lines.append("")
    cold = [n for n in vault.notes.values() if n.data.get("tier") == "cold" and vault.kind_for(n.rel) in ("hub", "profile", "reflection")]
    if cold:
        lines.append("## Cold hubs and profiles")
        lines.append("Names only. Not loaded by default; search or open on request.")
        lines.append(", ".join(f"[[{vault.config.link(n.rel[:-3])}|{n.stem if n.stem != 'Profile' else n.rel.split('/')[-2].replace('_', ' ')}]]" for n in sorted(cold, key=lambda n: n.rel)))
        lines.append("")
    if cold_open_tasks:
        lines.append("## Cold open tasks")
        for tid, title in cold_open_tasks:
            lines.append(f"- [[{vault.config.link(vault.config.agent('Tasks Archive/' + tid))}|{tid}]] — {title} (open, cold)")
        lines.append("")
    return "\n".join(lines)
