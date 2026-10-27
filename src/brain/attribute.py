"""The use log: one row per time a note or task was actually relied on."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import compile_context, session_tools
from . import session_tools
from .vault import Vault, WIKILINK_RE, TASK_ID_RE, TRIGGER_ID_RE

ROW_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}")
MISS_RE = re.compile(r"\b(no record (?:exists|found|of)|found no record|did not search|not searched|nothing in (?:the )?vault)\b", re.I)
HEADER = ("# Use log — {month}\n\n"
          "One row per actual reliance: `timestamp | target | reason | explicit/inferred/observed/live [| confirmed/unconfirmed]`. "
          "Only rows starting with a date count. A wrong row is edited or removed in place; git keeps the history. "
          "Never read for retrieval.\n\n")
ROLES = "assistant|user|human|system|developer|tool|function"
ROLE_RE = re.compile(r"^\s*(?:#{1,6}\s+|\*\*|(?:-\s+)?role\s*:\s*['\"]?|"
                     r"(?=(?:" + ROLES + r")\s*:))(" + ROLES + r")\b", re.I)
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
                          r"(?:[Zz]|[+-]\d{2}:?\d{2})?\b")
DAY_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


def path_pattern(vault: Vault) -> re.Pattern:
    config = vault.config
    prefixes = (config.agent_dir, config.people_dir, config.agents_dir,
                str(Path(config.human_profile).parent), config.sources_dir, config.hermes_dir)
    directories = "|".join(re.escape(prefix) for prefix in sorted(set(prefixes), key=len, reverse=True))
    return re.compile(r"\b(?:" + re.escape(config.vault) + r"/)?((?:(?:" + directories + r")/)?"
                      r"(?:[A-Z][\w ()'-]*/)*[A-Z][\w ()'-]*\.md)\b")


@dataclass
class Use:
    ts: str
    target: str      # vault-relative path, T-nnn, TR-nnn, or MISS
    reason: str
    source: str      # explicit | inferred | observed | live
    status: str = "" # confirmed | unconfirmed for live rows

    def line(self) -> str:
        parts = [self.ts, self.target, self.reason, self.source]
        if self.status:
            parts.append(self.status)
        return " | ".join(parts)

    @classmethod
    def parse(cls, line: str) -> "Use | None":
        if not ROW_RE.match(line):
            return None
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            return None
        return cls(parts[0], parts[1], parts[2], parts[3], parts[4] if len(parts) > 4 else "")


def explicit_uses(vault: Vault, text: str, ts: str, reason: str = "", exclude_orientation: bool = True) -> list[Use]:
    """Everything a reply or routine output explicitly points at."""
    seen, out = set(), []

    def add(target: str):
        if target in seen:
            return
        if exclude_orientation and target in vault.notes and vault.kind_for(target) == "orientation":
            return
        seen.add(target)
        out.append(Use(ts, target, reason, "explicit"))

    for m in WIKILINK_RE.finditer(text):
        r = vault.resolve(m.group(1))
        if r:
            add(r)
    for m in path_pattern(vault).finditer(text):
        r = vault.resolve(m.group(1))
        if r:
            add(r)
    for m in TASK_ID_RE.finditer(text):
        add(f"T-{m.group(1)}")
    for m in TRIGGER_ID_RE.finditer(text):
        add(f"TR-{m.group(1)}")
    if MISS_RE.search(text):
        out.append(Use(ts, "MISS", MISS_RE.search(text).group(0), "explicit"))
    return out


def read_log(path: Path) -> list[Use]:
    return [u for u in map(Use.parse, path.read_text(encoding="utf-8").splitlines()) if u]


def append_log(path: Path, uses: list[Use]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(HEADER.format(month=path.stem), encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        for u in uses:
            f.write(u.line() + "\n")


def fallback_date(path: Path) -> str:
    for name in (path.stem, *(parent.name for parent in path.parents)):
        for match in DAY_RE.finditer(name):
            try:
                return date.fromisoformat(match[0]).isoformat()
            except ValueError:
                continue
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def last_timestamp(line: str) -> str | None:
    for match in reversed(list(TIMESTAMP_RE.finditer(line))):
        try:
            datetime.fromisoformat(match[0].upper().replace("Z", "+00:00"))
        except ValueError:
            continue
        return match[0]
    return None


def session_rows(vault: Vault, path: Path) -> list[Use]:
    from . import digest   # digest imports this module; keep the dependency one-directional at import time

    if digest.markdown_kind(path) == "evaluation":
        return []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    has_roles = any(ROLE_RE.match(line) for line in lines)
    role, timestamp = "", fallback_date(path)
    reason = f"cited in session {path.stem}"
    uses = []
    for line in lines:
        marker = ROLE_RE.match(line)
        if marker:
            role = marker[1].lower()
        if line.lstrip().lower().startswith("sources:") and (not has_roles or role == "assistant"):
            rows = explicit_uses(vault, line, timestamp, reason)
            for use in rows:
                use.reason = reason
            uses.extend(rows)
        timestamp = last_timestamp(line) or timestamp
    return uses


def automatic_notes(vault: Vault) -> set[str]:
    hot = vault.notes.get("Hot Set.md")
    section = compile_context.automatic_load(hot.body) if hot else ""
    return vault.config.orientation | {"Hot Set.md", "Warm Index.md"} | set(vault.links_in(section))


def json_session_rows(vault: Vault, path: Path) -> list[Use]:
    from . import digest

    automatic, uses = automatic_notes(vault), []
    for number, record in digest.json_records(path):
        containers = digest.session_maps(record)
        kind = session_tools.session_kind(containers, digest.message_list(containers))
        if kind == "evaluation":
            continue   # retrieval checks earn nothing, before any extraction or dedupe
        session_id = digest.text_field(containers, ("id", "session_id"), f"{path.name}:{number}")
        reason = f"cited in session {session_id}"
        zone = vault.config.zone
        previous = next((stamp for fields in containers if (stamp := digest.timestamp_in(fields, zone=zone))), None)
        for message in digest.message_list(containers):
            previous = digest.timestamp_in(message, zone=zone) or previous
            timestamp = previous.isoformat() if previous else fallback_date(path)
            reads, _ = session_tools.activity(message, vault.config) if kind == "conversation" else ([], [])   # a routine opens notes mechanically; that is not use
            for read in reads:
                target = read.removeprefix(vault.config.vault + "/")
                if target in vault.notes and target not in automatic and not target.startswith(vault.config.observed_excluded):
                    uses.append(Use(timestamp, target, f"opened on demand in session {session_id}", "observed"))
            if (str(message.get("role", "")).strip().lower() != "assistant"
                    or str(message.get("compacted", False)).lower() in ("true", "1")):
                continue
            for line in digest.content_text(message.get("content", message.get("text", ""))).splitlines():
                if line.lstrip().lower().startswith("sources:"):
                    rows = explicit_uses(vault, line, timestamp, reason)
                    for use in rows:
                        use.reason = reason
                    uses.extend(rows)
                timestamp = last_timestamp(line) or timestamp
    return uses


def session_key(use: Use) -> tuple[str, str, str | None]:
    return use.ts[:10], use.target, None if use.source == "observed" else use.reason


def sessions(vault: Vault, directory: Path, write: bool = False) -> list[Use]:
    """New explicit and observed session uses, deduplicated against logs and this batch."""
    if not directory.is_dir():
        raise ValueError(f"sessions directory does not exist: {directory}")
    known, monthly, uses = {}, {}, []
    from . import digest
    jsonl = set(digest.jsonl_sources(directory))
    paths = sorted(jsonl) if jsonl else sorted(directory.rglob("*.md"))
    for path in paths:
        rows = json_session_rows(vault, path) if path in jsonl else session_rows(vault, path)
        for use in rows:
            month = use.ts[:7]
            log = vault.root / vault.config.agent("Use Log") / f"{month}.md"
            if month not in known:
                existing = read_log(log) if log.exists() else []
                known[month] = {(row.ts[:10], row.target, row.reason) for row in existing}
                known[month].update(session_key(row) for row in existing if row.source == "observed")
            key = session_key(use)
            if key not in known[month]:
                known[month].add(key)
                known[month].add((use.ts[:10], use.target, use.reason))
                uses.append(use)
                monthly.setdefault(log, []).append(use)
    if write:
        for log, rows in monthly.items():
            append_log(log, rows)
    return uses
