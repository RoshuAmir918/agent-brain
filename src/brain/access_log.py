"""Engine-written monthly records of non-owner access requests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import access, fm, session_context
from .config import Config
from .lock import vault_lock
from .vault import Vault, WIKILINK_RE

OUTCOMES = ("told gist", "told details", "declined", "asked owner", "did")


@dataclass
class AccessEntry:
    when: str
    person: str
    subject: str
    outcome: str
    rule: str | None
    asked: str
    source: str = ""


def path_for(config: Config, month: str) -> Path:
    return config.vault_path / config.people_dir / "Access" / f"{month}.md"


def render(entry: AccessEntry) -> str:
    return "- " + " · ".join((entry.when, entry.person, entry.subject, entry.outcome,
                              entry.rule or "default", entry.asked))


def parse(text: str) -> list[AccessEntry]:
    entries = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        parts = line[2:].split(" · ")
        if len(parts) != 6:
            continue
        when, person, subject, outcome, rule, asked = parts
        try:
            if datetime.strptime(when, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M") != when:
                continue
        except ValueError:
            continue
        if (outcome not in OUTCOMES or not subject or not rule or not asked or len(asked) > 120
                or not (WIKILINK_RE.fullmatch(person) or person.startswith("stranger (") and person.endswith(")"))):
            continue
        entries.append(AccessEntry(when, person, subject, outcome, None if rule == "default" else rule, asked))
    return entries


def _clean(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def append(config: Config, entries: list | None, platform: str, at: datetime, *,
           user_id: str = "", user_id_alt: str = "") -> Path | None:
    """Keep valid entries only, resolving identity from the session rather than the model."""
    user_ids = tuple(value for value in (user_id, user_id_alt) if value)
    if session_context.is_owner(config, platform, user_ids) or not isinstance(entries, list) or not entries:
        return None
    vault = Vault(config.vault_path)
    vocabulary = access.subjects(vault, config)
    rule_ids = {rule.id for rule in access.rules(vault, config)}
    profiles = session_context.matching_profiles(vault, config, user_ids, platform)
    person = (f"[[{config.link(profiles[0].rel)}]]" if len(profiles) == 1 else
              f"stranger ({_clean(platform) or 'platform unknown'} {_clean(user_id or user_id_alt) or 'id unavailable'})")
    local = at.replace(tzinfo=config.zone) if at.tzinfo is None else at.astimezone(config.zone)
    lines = []
    for entry in entries[:5]:
        if not isinstance(entry, dict):
            continue
        subject, outcome, asked, rule = (entry.get(key) for key in ("subject", "outcome", "asked", "rule"))
        if (not isinstance(subject, str) or subject not in vocabulary or outcome not in OUTCOMES
                or not isinstance(asked, str) or not asked.strip() or len(asked) > 120
                or "rule" not in entry or rule is not None and not isinstance(rule, str)):
            continue
        lines.append(render(AccessEntry(local.strftime("%Y-%m-%d %H:%M"), person, subject, outcome,
                                        rule if rule in rule_ids else None, _clean(asked))))
    if not lines:
        return None
    path = path_for(config, local.strftime("%Y-%m"))
    with vault_lock(config):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            fm.write(path, {"kind": "log", "tier": "cold"}, "\n")
        with path.open("rb") as stream:
            stream.seek(0, 2)
            separator = ""
            if stream.tell():
                stream.seek(-1, 2)
                separator = "" if stream.read(1) == b"\n" else "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(separator + "\n".join(lines) + "\n")
    return path


def recent(config: Config, today: date, days: int) -> list[AccessEntry]:
    """Entries in the last `days` calendar days, including today, oldest first."""
    if days <= 0:
        return []
    floor = today - timedelta(days=days - 1)
    entries = []
    for path in sorted(path_for(config, "").parent.glob("????-??.md")):
        if not floor.strftime("%Y-%m") <= path.stem <= today.strftime("%Y-%m"):
            continue
        for entry in parse(path.read_text(encoding="utf-8")):
            if floor.isoformat() <= entry.when[:10] <= today.isoformat():
                entry.source = path.relative_to(config.vault_path).as_posix()
                entries.append(entry)
    return sorted(entries, key=lambda entry: entry.when)
