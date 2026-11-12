"""Vault layout, note discovery, kinds, and wikilink resolution."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import fm
from .config import Config

WIKILINK_RE = re.compile(r"\[\[([^\]|#\\]+)(?:#[^\]|\\]*)?(?:\\?\|([^\]]*))?\]\]")   # target, label
TASK_ID_RE = re.compile(r"\bT-(\d{3,})\b")
TRIGGER_ID_RE = re.compile(r"\bTR-(\d{3})\b")
DAILY = re.compile(r"Conversations/\d{4}-\d{2}-\d{2}\.md\Z")  # immutable daily conversation archives

SKIP_DIRS = {".brain", ".git", ".obsidian", "skills"}  # Runtime/skills are SKILL.md files, not notes


def is_dream_path(rel: str, config: Config) -> bool:
    """Fiction is catalogued for link resolution but its body is never read by the mechanics."""
    return PurePosixPath(rel).is_relative_to(config.agent("Dreams"))


def navigation_text(text: str) -> str:
    """Remove fenced examples and inline code, retaining real navigation.

    Fence lengths matter: frozen exports wrap source text in longer fences
    which may themselves contain shorter fences. Sources footers outside code
    remain visible to the link checker.
    """
    lines, fence = [], ""
    for line in text.splitlines(keepends=True):
        following = fence_after(line, fence)
        if fence or following:
            fence = following
            lines.append("\n" if line.endswith("\n") else "")
        else:
            lines.append(line)
    visible = "".join(lines)
    # Inline spans cannot cross paragraph boundaries.
    return "".join(_without_code_spans(block) for block in
                   re.split(r"(\n[ \t]*\n)", visible))


def fence_after(line: str, fence: str) -> str:
    """The open code fence after `line` (its backtick or tilde run), given the one open before it; "" when none."""
    if fence:
        closing = r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\r?\n?"
        return "" if re.fullmatch(closing, line) else fence
    opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
    if opening and not (opening[1][0] == "`" and "`" in opening[2]):
        return opening[1]
    return ""


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
        self.config = Config.load(self.root)
        self._paths: set[str] = set()
        self.notes: dict[str, Note] = {}
        self.malformed: dict[str, str] = {}
        self._by_stem: dict[str, list[str]] = {}
        self._sources: dict[str, str] | None = None   # file name to vault-relative path in sources_dir, on first need
        self.load()

    def load(self) -> None:
        self.notes.clear(); self.malformed.clear(); self._by_stem.clear(); self._paths = set(); self._sources = None
        for p in sorted(self.root.rglob("*.md")):
            if any(part in SKIP_DIRS for part in p.relative_to(self.root).parts):
                continue
            rel = p.relative_to(self.root).as_posix()
            self._paths.add(rel)
            self._by_stem.setdefault(p.stem, []).append(rel)
            if is_dream_path(rel, self.config):
                continue
            try:
                data, body = fm.read(p)
            except fm.Malformed as error:
                self.malformed[rel] = str(error)   # kept out of the notes: nothing reads or rewrites it until fixed
                continue
            self.notes[rel] = Note(rel, p, data, body)

    def kind_for(self, rel: str) -> str:
        parts = PurePosixPath(rel).parts
        if len(parts) == 3 and parts[0] == "Responsibilities" and parts[2] == "RESPONSIBILITY.md":
            return "responsibility"
        if len(parts) >= 4 and parts[0] == "Responsibilities" and parts[2] == "records":
            return "log"
        if rel in self.config.orientation:
            return "orientation"
        note = self.notes.get(rel)
        if note is not None and note.data.get("kind") in ("log", "draft", "reflection"):
            return note.data["kind"]   # a note may declare itself a record, a draft or a reflection wherever it lives
        if rel.startswith(("Conversations/Sessions/", "Conversations/Digests/")):
            return "log"   # Session export: cold, never indexed
        if rel.startswith(self.config.runtime_dir + "/") and rel != f"{self.config.runtime_dir}/README.md":
            return "log"   # engine state such as a pending migration, never a note
        if rel.startswith("Conversations/"):
            return "episode"
        if rel.startswith((self.config.people_dir + "/", self.config.agents_dir + "/")) and rel.endswith("/Profile.md"):
            return "profile"
        if rel.startswith((self.config.people_dir + "/", self.config.agents_dir + "/")) and rel.endswith("/History.md"):
            return "log"
        if rel.startswith(self.config.log_paths):
            return "log"
        if rel.startswith(self.config.agent("Dreams/")):
            return "dream" if not rel.endswith("README.md") else "log"
        if rel.startswith(self.config.agent("Reflections/")):
            return "reflection"
        if rel == self.config.agent("Primed.md") or rel.startswith(self.config.agent("Primed/")):
            return "primed"   # one file, overwritten nightly; the dated folder is the older layout
        if rel.startswith(self.config.agent("Channels/")):
            return "log"   # the registry's dated history: a record beside the rows
        if rel.startswith(self.config.agent("Prepared/")):
            return "draft"   # made or drafted for the owner and held until wanted; retrievable, never auto-loaded
        return "hub"

    def resolve(self, target: str) -> str | None:
        """Resolve a wikilink target to a vault-relative path, or None."""
        t = target.strip()
        t = t.removeprefix(self.config.vault + "/")
        for cand in (t, t + ".md"):
            if cand in self._paths or (self.root / cand).is_file():   # skill files are skipped as notes but are valid link targets
                return cand
        parent = self.config.repo
        if t.startswith((self.config.sources_dir + "/", self.config.archive_dir + "/")):
            for cand in (t, t + ".md"):
                if (parent / cand).is_file():
                    return Path(os.path.relpath(parent / cand, self.root)).as_posix()
        # Dots are part of a title unless the target explicitly ends in .md.
        name = Path(t).name
        stem = name[:-3] if name.endswith(".md") else name
        hits = self._by_stem.get(stem, [])
        if len(hits) == 1:
            return hits[0]
        if hits:
            return min(hits, key=lambda r: (r.count("/"), r))
        if self._sources is None:
            self._sources = {}
            for q in (parent / self.config.sources_dir).rglob("*.md"):
                self._sources.setdefault(q.name, Path(os.path.relpath(q, self.root)).as_posix())
        return self._sources.get(stem + ".md")

    def links_in(self, text: str) -> list[str]:
        out = []
        for m in WIKILINK_RE.finditer(navigation_text(text)):
            r = self.resolve(m.group(1))
            if r and r not in out:
                out.append(r)
        return out

    def unresolved_links(self, text: str) -> list[str]:
        return sorted({m.group(1) for m in WIKILINK_RE.finditer(navigation_text(text)) if self.resolve(m.group(1)) is None})
