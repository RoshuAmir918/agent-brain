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
# NAME=value or NAME: value where the name says it is a secret; the name stays so the transcript still reads.
ASSIGNMENT = re.compile(r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD))(\s*[=:]\s*[\"']?)([^\s\"'`]{12,})")


def redact(text: str) -> tuple[str, int]:
    count = 0
    for kind, pattern in PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{kind}]", text)
        count += n

    def keep_name(match: re.Match) -> str:
        nonlocal count
        if match[3].startswith("[REDACTED") or match[3].startswith(("$", "op://", "<")):
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
