"""Assemble the generated Hermes startup context from its owning notes."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import fm
from .tasks import load_tasks, parse_register
from .vault import Vault

SOURCES = ("START.md", "CURRENT.md", "agent/Triggers.md", "agent/Tasks.md", "Hot Set.md")
WORD_LIMIT = 6000


def repo_root(root: Path) -> Path:
    try:
        result = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL)
        return Path(result.strip())
    except (OSError, subprocess.CalledProcessError):
        return root.parent


def open_register(root: Path, body: str) -> str:
    registered, _ = load_tasks(root)
    open_ids = {task.tid for task in registered if task.state.get("status") == "open"}
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


def build(root: Path) -> str:
    header = ("Generated nightly by `.brain`.\n"
              "Edits to this file are overwritten.\n"
              "Edit the source notes instead: " + ", ".join(f"memory/{rel}" for rel in SOURCES) + ".")
    sections = [header]
    for rel in SOURCES:
        _, body = fm.read(root / rel)
        if rel == "agent/Tasks.md":
            body = open_register(root, body)
        elif rel == "Hot Set.md":
            body = automatic_load(body)
        sections.append(f"## memory/{rel}\n\n{body.strip()}")
    sections.append(directory(root))
    sections.append("Warm Index, Conversations and everything else are retrieved on demand with `brain retrieve`.")
    return "\n\n".join(sections) + "\n"


def directory(root: Path) -> str:
    """Names only: what the vault knows, so the agent can decide to look."""
    vault = Vault(root)
    hubs, people, agents, reflections = [], [], [], []
    for rel, note in sorted(vault.notes.items()):
        kind = vault.kind_for(rel)
        if note.data.get("kind") in ("orientation", "log"):
            continue   # templates and registries' rules are not topics
        if kind == "profile" and rel.startswith("agents/"):
            agents.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "profile":
            people.append(rel.split("/")[-2].replace("_", " "))
        elif kind == "reflection":
            reflections.append(note.path.stem)
        elif kind == "hub":
            hubs.append(rel[:-3])
    registered, archived = load_tasks(root)
    tasks = [f"{t.tid} {t.title}" for t in registered + archived if t.state.get("status") == "open"]
    _, trig = fm.read(root / "agent" / "Triggers.md")
    triggers = sorted(set(re.findall(r"\bTR-\d{3}\b", trig)))
    lines = ["## Directory: what the vault knows", "",
             "Names only. If a request touches one of these, run `brain retrieve` before answering.", ""]
    lines.append("- Hubs and topics: " + ", ".join(hubs))
    lines.append("- People with profiles: " + (", ".join(people) or "none"))
    lines.append("- Agents registered: " + (", ".join(agents) or "none"))
    if reflections:
        lines.append("- Reflections: " + ", ".join(reflections))
    lines.append("- Open tasks: " + "; ".join(tasks))
    if triggers:
        lines.append("- Triggers: " + ", ".join(triggers))
    return "\n".join(lines)


def summary(text: str) -> str:
    words = len(text.split())
    warning = f"; WARNING: over {WORD_LIMIT} words" if words > WORD_LIMIT else ""
    return f"context: {words} words{warning}"


def write(root: Path, out: Path | None = None) -> str:
    text = build(root)
    (out if out is not None else repo_root(root) / ".hermes.md").write_text(text, encoding="utf-8")
    return summary(text)
