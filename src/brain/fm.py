"""Frontmatter read/write for vault notes. Flat YAML only."""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


class Malformed(ValueError):
    """A header that is present but not a YAML mapping. Never read as body: a writer would bury it, and its tier."""


def split(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as error:
        raise Malformed(str(error).splitlines()[0]) from error
    if not isinstance(data, dict):
        raise Malformed(f"frontmatter is a {type(data).__name__}, not a mapping")
    return data, text[m.end():]


def join(data: dict, body: str) -> str:
    if not data:
        return body
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    return f"---\n{dumped}\n---\n{body}"


def read(path: Path) -> tuple[dict, str]:
    try:
        return split(path.read_text(encoding="utf-8"))
    except Malformed as error:
        raise Malformed(f"{path}: {error}") from error


def write(path: Path, data: dict, body: str) -> None:
    write_text(path, join(data, body))


def write_text(path: Path, text: str | bytes, mode: int | None = None) -> None:
    """Write through a uniquely named temporary file and an atomic rename: a reader never sees the file half-written,
    and two writers can no longer leave a truncated file padded with NUL bytes (an Amicus register on 2026-11-05)."""
    staged = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        staged.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        if mode is not None:
            staged.chmod(mode)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def section(body: str, title: str) -> tuple[int, int] | None:
    """The span of a `## title` section's body, up to the next `## ` heading."""
    heading = re.search(r"^## " + re.escape(title) + r"[ \t]*\r?$", body, re.M)
    if not heading:
        return None
    following = re.compile(r"^## ", re.M).search(body, heading.end())
    return heading.end(), following.start() if following else len(body)


def without(body: str, span: tuple[int, int], drop, *, bullet, detail: re.Pattern) -> tuple[str, list]:
    """The section's lines without the bullets `drop(item)` selects and their indented detail lines. `bullet` parses a
    line into an item or None; `detail` matches an indented line belonging to the bullet above it."""
    kept, removed, dropping = [], [], False
    for line in body[span[0]:span[1]].splitlines(keepends=True):
        if detail.match(line.rstrip("\r\n")):
            if not dropping:
                kept.append(line)
            continue
        item = bullet(line)
        dropping = bool(item and drop(item))
        if dropping:
            removed.append(item)
        else:
            kept.append(line)
    return body[:span[0]] + "".join(kept) + body[span[1]:], removed


def plain(text: str) -> str:
    """Case- and whitespace-insensitive form, for matching an item by its words."""
    return " ".join(text.casefold().split())


def cut(text: str, words: int) -> str:
    parts = text.split()
    return text if len(parts) <= words else " ".join(parts[:words]).rstrip(",;:") + "…"


def field(body: str, *names: str) -> str | None:
    """The value of the first `**Name:**` line for any of `names`, case-insensitive, bold marks removed."""
    match = re.search(r"^[ \t]*(?:-[ \t]+)?\*\*(?:" + "|".join(re.escape(name) for name in names) +
                      r"):\*\*[ \t]*([^\r\n]*)", body, re.M | re.I)
    return match[1].replace("**", "").strip() if match else None


def hub_goal(body: str) -> str | None:
    """Only the hub's own Goal bullet, before any subsection or proposal."""
    preamble = re.split(r"^#{2,6} ", body, maxsplit=1, flags=re.M)[0]
    match = re.search(r"^- \*\*Goal:\*\*[ \t]*([^\r\n]*)", preamble, re.M | re.I)
    return match[1].strip() if match else None


def first_line(body: str) -> str:
    """First non-heading, non-empty prose line, used for index lines."""
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("<!--") or s.startswith("[[") and s.endswith("]]"):
            continue
        return s
    for ln in body.splitlines():
        if ln.strip().startswith("#"):
            return ln.strip("# ").strip()
    return ""
