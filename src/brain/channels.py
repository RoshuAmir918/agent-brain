"""The channel registry: every account and channel an agent can reach, its own and its owner's, one row each, in
`<agent_dir>/Channels.md`. The engine parses and writes the rows; routines read reach from them instead of guessing;
the owner sees them in their app. A row names how a channel is opened (an `op://` item, a browser profile, a platform),
never a secret value."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import fm
from .config import Config
from .vault import Vault

COLUMNS = ("channel", "whose", "identity", "reach", "read", "write", "granted", "status")
WHOSE = ("own", "owner", "shared")
STATUS = ("live", "pending", "revoked")
WORD_BUDGET = 400
HEADER = "| " + " | ".join(COLUMNS) + " |\n| " + " | ".join("---" for _ in COLUMNS) + " |"
# A 1Password reference: `op://<vault>/<item>[/<field>]`, and item names carry spaces, so in prose one is read only
# inside backticks or quotes (the way the skills write them); a reach cell is the reference whole.
OP_QUOTED = re.compile(r"[`\"'](op://[^`\"'\n]+)[`\"']")
OP_CELL = re.compile(r"^op://[^\n|]+$")


def item_of(ref: str) -> str:
    """The reference without a trailing field: `op://Echo/Gmail app password/password` names the same item."""
    parts = ref.strip().rstrip("/").split("/")
    return "/".join(parts[:4]) if len(parts) > 4 and " " not in parts[-1] else ref.strip()
CONNECT_LINE = re.compile(r"^(?P<created>\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\s+(?P<id>\S+)\s+(?P<profile>\S+)\s+(?P<provider>\S+)\s+(?P<status>\S+)(?:\s+(?P<account>.+?))?\s*$")


@dataclass
class Channel:
    channel: str
    whose: str
    identity: str
    reach: str
    read: str
    write: str
    granted: str
    status: str

    def render(self) -> str:
        return "| " + " | ".join(getattr(self, name).replace("|", "/").strip() for name in COLUMNS) + " |"

    @property
    def op_refs(self) -> list[str]:
        cell = self.reach.strip()
        return [item_of(cell)] if OP_CELL.match(cell) else [item_of(ref) for ref in OP_QUOTED.findall(cell)]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table_span(body: str) -> tuple[int, int] | None:
    """The registry table: from its header row through its last row."""
    match = re.search(r"^\|[ \t]*channel[ \t]*\|[^\n]*\n\|[ \t]*-{3,}[^\n]*\n", body, re.M | re.I)
    if not match:
        return None
    end = match.end()
    for line in body[end:].splitlines(keepends=True):
        if not line.startswith("|"):
            break
        end += len(line)
    return match.start(), end


def parse(body: str) -> list[Channel]:
    span = table_span(body)
    if not span:
        return []
    lines = body[span[0]:span[1]].splitlines()
    names = [cell.lower() for cell in _cells(lines[0])]
    rows = []
    for line in lines[2:]:
        cells = _cells(line)
        if not any(cells):
            continue
        values = dict(zip(names, cells))
        rows.append(Channel(*(values.get(name, "") for name in COLUMNS)))
    return rows


def load(vault: Vault) -> list[Channel]:
    note = vault.notes.get(vault.config.agent("Channels.md"))
    return parse(note.body) if note else []


def live(rows: list[Channel], whose: str | None = None) -> list[Channel]:
    return [row for row in rows if row.status == "live" and (whose is None or row.whose == whose)]


def save(config: Config, rows: list[Channel]) -> None:
    """Rewrite the registry table in place, leaving the rest of the note as it is."""
    path = config.vault_path / config.agent("Channels.md")
    data, body = fm.read(path) if path.exists() else ({}, "# Channels\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = HEADER + "\n" + "\n".join(row.render() for row in rows) + "\n"
    span = table_span(body)
    body = body[:span[0]] + table + body[span[1]:] if span else body.rstrip("\n") + "\n\n" + table
    fm.write(path, data, body)


def from_connect(lines: list[str], human_name: str, profile: str | None = None) -> list[Channel]:
    """Rows for the completed grants in `connect list` output: a secret or token the owner handed over through a link,
    registered as the owner's channel reached by the `op://` item the grant wrote, until the agent fills in what it
    can read and write there."""
    rows = []
    for line in lines:
        match = CONNECT_LINE.match(line)
        if not match or match["status"] != "done" or (profile and match["profile"] != profile):
            continue
        account = (match["account"] or "").strip()
        if not account:
            continue
        name = account.rsplit("/", 1)[-1] if account.startswith("op://") else account
        rows.append(Channel(channel=name, whose="owner", identity="", reach=account, read="", write="",
                            granted=f"{human_name}, {match['created']}, connect grant {match['id']}", status="live"))
    return rows


def sync(config: Config, lines: list[str], profile: str | None = None) -> list[Channel]:
    """Add the connect grants the registry does not know by their item; existing rows are never changed. The caller
    holds the vault lock, and the registry is read fresh from disk under it."""
    path = config.vault_path / config.agent("Channels.md")
    rows = parse(fm.read(path)[1]) if path.exists() else []
    known = {row.reach for row in rows} | {ref for row in rows for ref in row.op_refs}
    added = []
    for row in from_connect(lines, config.human_name, profile):
        if item_of(row.reach) not in known:
            known.add(item_of(row.reach))
            added.append(row)
    if added:
        save(config, rows + added)
    return added


def problems(vault: Vault) -> list[str]:
    """What `brain validate` reports: a malformed registry, unknown vocabulary, a registry over budget, and an `op://`
    reference anywhere in the agent's notes that no row registers."""
    config = vault.config
    rel = config.agent("Channels.md")
    note = vault.notes.get(rel)
    if note is None:
        return []
    out = []
    rows = parse(note.body)
    if table_span(note.body) is None:
        out.append(f"{rel}: no registry table (a header row starting with `| channel |`)")
    for row in rows:
        if row.whose not in WHOSE:
            out.append(f"{rel}: {row.channel or '(unnamed)'}: whose must be one of {', '.join(WHOSE)}, not {row.whose!r}")
        if row.status not in STATUS:
            out.append(f"{rel}: {row.channel or '(unnamed)'}: status must be one of {', '.join(STATUS)}, not {row.status!r}")
        if "op://" in row.identity + row.read + row.write + row.granted:
            out.append(f"{rel}: {row.channel}: an op:// reference belongs in reach only")
    words = len(note.body.split())
    if words > WORD_BUDGET:
        out.append(f"{rel}: {words} words; the registry is rows, dated material goes to {config.agent('Channels/History.md')}")
    registered = {ref for row in rows for ref in row.op_refs}
    for other, entry in sorted(vault.notes.items()):
        if other == rel or other.startswith(config.sources_dir + "/") or vault.kind_for(other) in ("log", "episode"):
            continue
        for ref in sorted({item_of(ref) for ref in OP_QUOTED.findall(entry.body)}):
            if ref not in registered:
                out.append(f"{other}: credential reference {ref} is not a registered channel's reach")
    return out
