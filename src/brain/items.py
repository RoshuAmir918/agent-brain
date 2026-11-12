"""One grammar for the open things an intellect holds (loops, asks, ideas, consults): a top-level bullet, the indented
`- key: value` lines under it, and a stored `- id:` line. Each kind reads its own bullet; the grouping, the id, removal
and picking are shared, so a reworded bullet keeps its id and an indented line never outlives its bullet."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

from . import fm
from .config import Config
from .lock import vault_lock

DETAIL = re.compile(r"^[ \t]+-[ \t]+([a-z][a-z-]*):[ \t]*(.*?)[ \t]*$")
ID = re.compile(r"[0-9a-f]{12}")

T = TypeVar("T")


def derived(*parts: str) -> str:
    """The id an item had before ids were stored, from what it was keyed on; still the id a new item is given, so
    Answered tombstones, reception rows and the app's pending rows keep matching."""
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


@dataclass
class Block:
    line: str
    details: list[tuple[str, str]] = field(default_factory=list)
    text: str = ""   # the bullet and every indented line under it, as written

    def get(self, key: str) -> str | None:
        return next((value for name, value in self.details if name == key), None)

    @property
    def id(self) -> str | None:
        stored = self.get("id")
        return stored if stored and ID.fullmatch(stored) else None


def blocks(body: str, span: tuple[int, int] | None) -> list[Block]:
    """The section's bullets, each with the indented lines below it. Any other line (prose, a blank) ends a block."""
    out: list[Block] = []
    current = None
    for line in body[span[0]:span[1]].splitlines(keepends=True) if span else []:
        bare = line.rstrip("\r\n")
        if bare.startswith("- "):
            current = Block(bare, text=line)
            out.append(current)
        elif current is not None and bare[:1] in (" ", "\t") and bare.strip():
            current.text += line
            if detail := DETAIL.match(bare):
                current.details.append((detail[1], detail[2]))
        else:
            current = None
    return out


def parse(body: str, title: str, read: Callable[[Block], T | None]) -> list[T]:
    """The items of a `## title` section; `read` turns a block into an item, or None for a line that is not one. A stored
    id replaces the derived one."""
    out = []
    for block in blocks(body, fm.section(body, title)):
        item = read(block)
        if item is not None:
            if block.id:
                item.id = block.id
            out.append(item)
    return out


def without(body: str, title: str, read: Callable[[Block], T | None], drop: Callable[[T], bool]) -> tuple[str, list[T]]:
    """The body without the section's items `drop(item)` selects, each removed with every line under it."""
    span = fm.section(body, title)
    if span is None:
        return body, []
    section, removed, at = body[span[0]:span[1]], [], 0
    kept = []
    for block in blocks(body, span):
        item = read(block)
        if item is None:
            continue
        if block.id:
            item.id = block.id
        if drop(item):
            start = section.index(block.text, at)
            kept.append(section[at:start])
            at = start + len(block.text)
            removed.append(item)
    kept.append(section[at:])
    return body[:span[0]] + "".join(kept) + body[span[1]:], removed


def set_details(body: str, title: str, read: Callable[[Block], T | None], which: str, values: dict[str, str]) -> str:
    """The body with the item `which` given these `key: value` lines: each replaces a line of that key under its
    bullet, or is added at the end of the block."""
    span = fm.section(body, title)
    for block in blocks(body, span):
        item = read(block)
        if item is None or (block.id or item.id) != which:
            continue
        lines = block.text.splitlines(keepends=True)
        for key, value in values.items():
            line = f"  - {key}: {value}\n"
            at = next((n for n, text in enumerate(lines) if (d := DETAIL.match(text.rstrip("\r\n"))) and d[1] == key), None)
            if at is None:
                lines[-1] = lines[-1] if lines[-1].endswith("\n") else lines[-1] + "\n"
                lines.append(line)
            else:
                lines[at] = line
        start = body.index(block.text, span[0])
        return body[:start] + "".join(lines) + body[start + len(block.text):]
    raise ValueError(f"no item {which} under {title}")


def pick(candidates: Iterable[T], which: str, noun: str, listing: str, *,
         narrow: Callable[[list[T]], list[T]] | None = None) -> T:
    """One open item by id, else by its exact words, else by words that appear in exactly one."""
    candidates = list(candidates)
    chosen = [item for item in candidates if item.id == which]
    needle = fm.plain(which)
    if not chosen and needle:
        chosen = ([item for item in candidates if fm.plain(item.text) == needle]
                  or [item for item in candidates if needle in fm.plain(item.text)])
    if len(chosen) > 1 and narrow and len(narrowed := narrow(chosen)) == 1:
        chosen = narrowed
    if len(chosen) != 1:
        raise ValueError((f"no open {noun} matches " if not chosen else f"{len(chosen)} open {noun}s match ")
                         + repr(which) + f"; use an id from `{listing}`")
    return chosen[0]


def stamp_body(body: str, title: str, read: Callable[[Block], T | None]) -> tuple[str, int]:
    """Write each item's id under its bullet where none is stored yet: the id it already has, so nothing that points
    at it changes, but from now on rewording the bullet keeps it."""
    span = fm.section(body, title)
    if span is None:
        return body, 0
    section, out, at, count = body[span[0]:span[1]], [], 0, 0
    for block in blocks(body, span):
        item = read(block)
        if item is None or block.id:
            continue
        end = section.index(block.text, at) + len(block.text)
        newline = "" if block.text.endswith("\n") else "\n"
        out.append(section[at:end] + newline + f"  - id: {item.id}\n")
        at = end
        count += 1
    out.append(section[at:])
    return body[:span[0]] + "".join(out) + body[span[1]:], count


def stamp(config: Config) -> int:
    """Store the id of every loop, ask, confirm and idea written without one (by hand, or before ids were stored)."""
    from . import ideas, loops, needs
    targets = ((loops.path_for(config), ("Open", "Closed"), loops.read),
               (config.vault_path / "CURRENT.md", ("Needs you",), needs.read),
               (config.vault_path / config.agent("Ideas.md"), ("Open",), ideas.read))
    total = 0
    with vault_lock(config):
        for path, titles, read in targets:
            if not path.is_file():
                continue
            try:
                data, body = fm.read(path)
            except fm.Malformed:
                continue   # validate names it
            count = 0
            for title in titles:
                body, stamped = stamp_body(body, title, read)
                count += stamped
            if count:
                fm.write(path, data, body)
                total += count
    return total


ANSWERS = {"loop": ("drop",), "idea": ("yes", "not-now", "no", "stop-asking"),
           "need": ("no", "done", "stop-asking"), "consult": ("confirmed", "no", "stop-asking")}


def answer(config: Config, which: str, value: str, words: str = "", by: str = ""):
    """The owner's answer to one open item, from their app or a colleague's from Slack; what it does depends only on
    what the item is. A loop is dropped; an idea is answered (yes opens its task, `words` the owner's own); a consult is
    confirmed in `words`, by `by`; any other Needs you item is dismissed with the answer as its reason."""
    from . import consults, ideas, loops, needs
    today = config.today()
    if any(loop.id == which for loop in loops.open_loops(config)):
        kind = "loop"
    elif any(idea.id == which for idea in ideas._open(config, today)):
        kind = "idea"
    elif need := next((item for item in needs.items(config, today) if item.id == which), None):
        kind = "consult" if need.origin == "consult" else "need"
    else:
        raise ValueError(f"no open item has id {which}")
    if value not in ANSWERS[kind]:
        raise ValueError(f"a {kind}'s answer is one of {', '.join(ANSWERS[kind])}")
    if kind == "loop":
        return loops.close(config, which, config.now(), "dropped", words or f"{config.human_name} dropped it in their app")
    if kind == "idea":
        return ideas.respond(config, which, today, value, words)
    if value == "confirmed":
        return consults.answer(config, which, "confirmed", words, by)
    return needs.dismiss(config, which, today, value, by=by)
