"""Credential scrubbing for what the export commits: session transcripts and digests are pushed to a remote, so a key
pasted into a conversation must not ride along. Well-known key shapes only, and secret-named assignments; ordinary
prose is never touched. Idempotent: a redacted file comes out the same."""
from __future__ import annotations

import re
from pathlib import Path

PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\bxapp-[A-Za-z0-9-]{10,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
]
# NAME=value, name: value or "name": "value" where the name says it is a secret and the value looks like one (twenty or
# more key characters, letters and digits both); the name stays so the transcript still reads.
ASSIGNMENT = re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET_KEY|ACCESS_KEY|PRIVATE_KEY|TOKEN|SECRET|PASSWORD))"
                        r"([\"']?\s*[=:]\s*[\"']?)([A-Za-z0-9_\-+/.=]{20,})")
# Documentation placeholders keep their shape: `sk-xxxx`, `xoxb-your-token`, `<key>`. The whole value must be one; a
# real key that merely contains "your" or "example" is still a key.
PLACEHOLDER = re.compile(r"(?i)(?:[a-z]+[-_])*(?:x{4,}|<[^>]*>|.*\.\.\.|"
                         r"(?:your|example|placeholder|dummy|redacted|changeme)(?:[-_].*)?)")


def redact(text: str) -> tuple[str, int]:
    count = 0
    for kind, pattern in PATTERNS:
        def replace(match: re.Match, kind=kind) -> str:
            nonlocal count
            if kind != "private-key" and PLACEHOLDER.fullmatch(match[0]):
                return match[0]
            count += 1
            return f"[REDACTED:{kind}]"
        text = pattern.sub(replace, text)

    def keep_name(match: re.Match) -> str:
        nonlocal count
        if PLACEHOLDER.fullmatch(match[3]) or not (re.search(r"\d", match[3]) and re.search(r"[A-Za-z]", match[3])):
            return match[0]
        count += 1
        return f"{match[1]}{match[2]}[REDACTED:secret]"

    return ASSIGNMENT.sub(keep_name, text), count


def redact_paths(paths: list[Path]) -> dict[Path, int]:
    """Scrub every Markdown, JSON and text file under the given files or folders in place; returns the changed ones."""
    changed = {}
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
        for path in files:
            if path.suffix not in (".md", ".json", ".jsonl", ".txt"):
                continue
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            clean, n = redact(text)
            if n:
                path.write_text(clean, encoding="utf-8", errors="surrogateescape")
                changed[path] = n
    return changed
