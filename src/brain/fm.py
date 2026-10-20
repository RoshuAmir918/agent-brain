"""Frontmatter read/write for vault notes. Flat YAML only."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


def split(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[m.end():]


def join(data: dict, body: str) -> str:
    if not data:
        return body
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")
    return f"---\n{dumped}\n---\n{body}"


def read(path: Path) -> tuple[dict, str]:
    return split(path.read_text(encoding="utf-8"))


def write(path: Path, data: dict, body: str) -> None:
    path.write_text(join(data, body), encoding="utf-8")


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
