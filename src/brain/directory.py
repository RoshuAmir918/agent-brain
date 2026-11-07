"""Demiurge's identity directory, cached outside the vault; identity never grants access."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath

from . import install, runtime
from .config import Config
from .identity import profile_ids, profiles
from .vault import Vault


def path() -> Path:
    return install.records_path() / "directory.json"


def sync(config: Config, payload: dict, today: date) -> list[str]:
    directory = {
        "syncedAt": today.isoformat(),
        "people": [{"id": row["id"], "handle": row.get("handle", ""), "name": row.get("name", ""),
                    "owner": bool(row.get("yourOwner", False))}
                   for row in payload.get("people", []) if row.get("id")],
        "intellects": [{"id": row["id"], "handle": row.get("handle", ""), "name": row.get("name", ""),
                        "ownerId": row.get("ownerId", ""), "ownerHandle": row.get("ownerHandle", ""),
                        "ownerName": row.get("whose", "")}
                       for row in payload.get("intellects", []) if row.get("id")],
    }
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(json.dumps(directory, ensure_ascii=False, indent=2) + "\n")
            stream.close()
            staged.replace(target)
        finally:
            staged.unlink(missing_ok=True)
    count = len(directory["intellects"])
    return [f"synced {len(directory['people'])} people, {count} intellect{'s' if count != 1 else ''}"] \
        + reconcile(Vault(config.vault_path), directory, today)


def load(config: Config) -> dict | None:
    if runtime.configured() is None:
        return None
    try:
        directory = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return directory if isinstance(directory, dict) else None


def lookup(config: Config, ident: str) -> dict | None:
    directory = load(config)
    if directory is None:
        return None
    label, separator, value = ident.partition(":")
    if separator and re.fullmatch(r"[A-Za-z][\w-]*", label):
        if label.casefold() != "demiurge":
            return None
        ident = value
    entries = [dict(row, kind=kind) for field, kind in (("people", "person"), ("intellects", "intellect"))
               for row in directory.get(field, []) if row.get("id")]
    by_id = [row for row in entries if row["id"].casefold() == ident.casefold()]
    matches = by_id or [row for row in entries if row.get("handle")
                        and row["handle"].casefold() == ident.removeprefix("@").casefold()]
    return matches[0] if len(matches) == 1 else None


def describe(entry: dict) -> str:
    role = "a person here" if entry["kind"] == "person" else f"{entry['ownerName']}'s intellect"
    return f"{entry['name']} (@{entry['handle']}), {role}"


def reconcile(vault: Vault, directory: dict, today: date) -> list[str]:
    """Only explicit handles identify an id-less profile, and only when the match is unambiguous."""
    report = []
    for field, kind in (("people", "person"), ("intellects", "intellect")):
        listed = {row["handle"].casefold() for row in directory.get(field, []) if row.get("id") and row.get("handle")}
        for note in profiles(vault, kind):
            ids = [ident.split(":", 1)[1] for ident in profile_ids(note.body) if ident.startswith("demiurge:")]
            if any(ident.casefold() not in listed for ident in ids):
                continue   # bound to a permanent id, or to something this directory cannot vouch for: left alone
            handles = {PurePosixPath(note.rel).parent.name.casefold()}
            # A Demiurge "id" line holding a handle binds to a name that can change: it identifies the profile, and
            # the permanent id is added beside it.
            handles.update(ident.casefold() for ident in ids)
            # Only handles that name this profile itself: an @handle anywhere in the body may be someone it mentions.
            own = re.search(r"(?ims)^##[ \t]+Identifiers[ \t]*$(.*?)(?=^##[ \t]|\Z)", note.body)
            handles.update(match[1].casefold() for match in re.finditer(
                r"(?<![\w@-])@([\w-]+)(?![\w-])(?![@.]\w)", own[1] if own else ""))
            handles.update(match[1].casefold() for match in re.finditer(
                r"(?im)^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?handle(?:\*\*)?[ \t]*:[ \t]*"
                r"(?:\*\*)?[ \t]*`?@?([\w-]+)`?[ \t]*\r?$", note.body))
            matches = [row for row in directory.get(field, []) if row.get("id") and row.get("handle")
                       and row["handle"].casefold() in handles]
            if len(matches) != 1:
                continue
            entry = matches[0]
            line = f"- **Demiurge:** `{entry['id']}` (@{entry['handle']}), from the Demiurge directory {today.isoformat()}\n"
            heading = re.search(r"(?im)^##[ \t]+Identifiers[ \t]*(?:\n|$)", note.body) \
                or re.search(r"(?m)^#[ \t]+[^\n]*(?:\n|$)", note.body)
            if heading:
                offset = heading.end()
                prefix = note.body[:offset]
                note.body = prefix + ("" if prefix.endswith("\n") else "\n") + line + note.body[offset:]
            else:
                note.body = line + "\n" + note.body
            note.save()
            report.append(f"added demiurge:{entry['id']} (@{entry['handle']}) to {note.rel}")
    return report
