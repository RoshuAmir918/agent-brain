"""Vault layout, note discovery, kinds, and wikilink resolution."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import fm

WIKILINK_RE = re.compile(r"\[\[([^\]|#\\]+)(?:#[^\]|\\]*)?(?:\\?\|[^\]]*)?\]\]")
TASK_ID_RE = re.compile(r"\bT-(\d{3})\b")
TRIGGER_ID_RE = re.compile(r"\bTR-(\d{3})\b")
DAILY = re.compile(r"Conversations/\d{4}-\d{2}-\d{2}\.md\Z")  # immutable daily conversation archives

ORIENTATION = {
    "START.md", "CURRENT.md", "Hot Set.md", "Warm Index.md",
    "agent/SOUL.md", "agent/Triggers.md", "agent/Tasks.md", "agent/Channels.md",
    "agent/Memory Spec.md", "agent/Retrieval Checks.md", "hermes/README.md",
}
SKIP_DIRS = {".brain", ".git", ".obsidian", "skills"}  # hermes/skills are SKILL.md files, not notes


def is_dream_path(rel: str) -> bool:
    """Fiction is catalogued for link resolution but its body is never read by the mechanics."""
    return PurePosixPath(rel).parts[:2] == ("agent", "Dreams")


def navigation_text(text: str) -> str:
    """Remove fenced examples and inline code, retaining real navigation.

    Fence lengths matter: frozen exports wrap source text in longer fences
    which may themselves contain shorter fences. Sources footers outside code
    remain visible to the link checker.
    """
    lines = []
    fence_char, fence_size = "", 0
    for line in text.splitlines(keepends=True):
        if fence_char:
            if re.match(r"^ {0,3}" + re.escape(fence_char) +
                        r"{" + str(fence_size) + r",}[ \t]*(?:\r?\n)?$", line):
                fence_char, fence_size = "", 0
            lines.append("\n" if line.endswith("\n") else "")
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence_char, fence_size = opening[1][0], len(opening[1])
            lines.append("\n" if line.endswith("\n") else "")
        else:
            lines.append(line)
    visible = "".join(lines)
    # Inline spans cannot cross paragraph boundaries.
    return "".join(_without_code_spans(block) for block in
                   re.split(r"(\n[ \t]*\n)", visible))


def _without_code_spans(visible: str) -> str:
    # A code span closes only on a backtick run of the same length.
    out, pos = [], 0
    while opening := re.search(r"(?<![\\`])`+", visible[pos:]):
        start, end = pos + opening.start(), pos + opening.end()
        run = visible[start:end]
        closing = re.search(r"(?<!`)" + re.escape(run) + r"(?!`)", visible[end:])
        out.append(visible[pos:start])
        if closing:
            out.append(" ")
            pos = end + closing.end()
        else:
            out.append(run)
            pos = end
    out.append(visible[pos:])
    return "".join(out)


@dataclass
class Note:
    rel: str            # path relative to the memory root, forward slashes
    path: Path
    data: dict = field(default_factory=dict)
    body: str = ""

    @property
    def stem(self) -> str:
        return Path(self.rel).stem

    def save(self) -> None:
        fm.write(self.path, self.data, self.body)


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._paths: set[str] = set()
        self.notes: dict[str, Note] = {}
        self._by_stem: dict[str, list[str]] = {}
        self.load()

    def load(self) -> None:
        self.notes.clear(); self._by_stem.clear(); self._paths = set()
        for p in sorted(self.root.rglob("*.md")):
            if any(part in SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            rel = p.relative_to(self.root).as_posix()
            self._paths.add(rel)
            self._by_stem.setdefault(p.stem, []).append(rel)
            if is_dream_path(rel):
                continue
            data, body = fm.read(p)
            self.notes[rel] = Note(rel, p, data, body)

    def kind_for(self, rel: str) -> str:
        if rel in ORIENTATION:
            return "orientation"
        if rel.startswith(("Conversations/Sessions/", "Conversations/Digests/")):
            return "log"   # Hermes session export: cold, never indexed
        if rel.startswith("Conversations/"):
            return "episode"
        if rel.startswith(("people/", "agents/")) and rel.endswith("/Profile.md"):
            return "profile"
        if rel.startswith(("agent/Research/", "agent/Explorations/", "agent/Use Log/", "agent/Sleep Log/", "agent/Tasks Archive/")):
            return "log"
        if rel.startswith("agent/Dreams/"):
            return "dream" if not rel.endswith("README.md") else "log"
        if rel.startswith("agent/Reflections/"):
            return "reflection"
        if rel.startswith("agent/Primed/"):
            return "primed"
        return "hub"

    def resolve(self, target: str) -> str | None:
        """Resolve a wikilink target to a vault-relative path, or None."""
        t = target.strip()
        if t.startswith("memory/"):
            t = t[len("memory/"):]
        for cand in (t, t + ".md"):
            if cand in self._paths or (self.root / cand).is_file():   # skill files are skipped as notes but are valid link targets
                return cand
        parent = self.root.parent
        if t.startswith("sources/") or t.startswith("Archive/") or t.startswith("memory/"):
            for cand in (t, t + ".md"):
                if (parent / cand).exists():
                    return "../" + cand if cand.startswith(("sources/", "Archive/")) else cand[len("memory/"):]
        # Dots are part of a title unless the target explicitly ends in .md.
        name = Path(t).name
        stem = name[:-3] if name.endswith(".md") else name
        hits = self._by_stem.get(stem, [])
        if len(hits) == 1:
            return hits[0]
        if hits:
            return min(hits, key=lambda r: (r.count("/"), r))
        for q in (self.root.parent / "sources").rglob("*.md") if (self.root.parent / "sources").exists() else []:
            if q.name == stem + ".md":
                return "../" + q.relative_to(self.root.parent).as_posix()
        return None

    def links_in(self, text: str) -> list[str]:
        out = []
        for m in WIKILINK_RE.finditer(navigation_text(text)):
            r = self.resolve(m.group(1))
            if r and r not in out:
                out.append(r)
        return out

    def unresolved_links(self, text: str) -> list[str]:
        return sorted({m.group(1) for m in WIKILINK_RE.finditer(navigation_text(text)) if self.resolve(m.group(1)) is None})
