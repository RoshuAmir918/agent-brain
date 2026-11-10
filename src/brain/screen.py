"""The screen and the study queue (docs/roles.md section 7): every exchange a role's sources hold is labelled by a
classifier the runtime runs, so the nightly study reads the few items that teach rather than the many that don't.

The engine asks and records; it never calls a model. `requests` writes the questions for what is not yet labelled, in
the shape a classifier answers (each item read with its whole exchange and its speaker's role); `record` keeps the
answers. Labels and what has been studied live under `$AGENT_HOME/roles/<role>/`, outside the vault: they cost a
classifier to make, and a lost file is made again by screening again. Every step is idempotent, so an interrupted night
resumes where it stopped."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from . import fm, owners, roles, routing, runtime
from .config import Config
from .lock import evidence_lock
from .sources import Context, Exchange, registry
from .sources.reviews import AGENT_POSTED, BOT

FACT = [{"value": "yes", "description": "states a fact about the organisation a newcomer must know: a contract, a "
                                       "commitment, a customer's reliance, a vendor or legal constraint, who owns what"},
        {"value": "no", "description": "states no such fact"}]
ITEMS = 60   # items asked about in one request; a longer exchange is asked in parts, each with the whole exchange
TEXT = 1500   # characters of one item a request carries
SINCE_DAYS = 30   # how far back a first screen reads


@dataclass
class Label:
    id: str   # `<exchange id>/<item key>`
    exchange: str
    n: int
    at: str
    by: str
    speaker: str
    kind: str
    worth: int
    fact: bool
    classifier: str
    screened: str
    text: str
    where: str = ""


def folder(config: Config, role: str) -> Path:
    home = runtime.configured()
    if home is None:
        raise ValueError("AGENT_HOME is not set: a role's screen lives in its workspace")
    return home / "roles" / role


def _rows(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue   # a line a crash cut short
    return out


def labels(config: Config, role: str) -> dict[str, Label]:
    return {row["id"]: Label(**row) for row in _rows(folder(config, role) / "labels.jsonl")}


def studied(config: Config, role: str) -> set[str]:
    return {row["id"] for row in _rows(folder(config, role) / "studied.jsonl")}


def _append(path: Path, rows: list[dict]) -> None:
    """Caller holds the folder's lock. A tail a crash cut short is ended first, so the first new row isn't joined to it."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    torn = path.is_file() and path.stat().st_size and not path.read_bytes().endswith(b"\n")
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n" if torn else "")
        stream.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _key(said) -> str:
    return said.key or str(said.n)


def exchanges(config: Config, role: roles.Role, since: date | None) -> list[tuple[str, Exchange]]:
    """Every exchange the role's declared sources hold, with the platform its speakers' handles are on."""
    found, seen = [], set()
    entries = registry()
    for source in dict.fromkeys(item for ids in role.sources.values() for item in ids):
        entry = entries.get(source)
        if entry is None or entry.exchanges is None:
            continue   # a source a role learns from some other way, or not yet
        for exchange in entry.exchanges(Context(config.today(), since, None, config)):
            if exchange.id not in seen:
                seen.add(exchange.id)
                found.append((entry.source.needs, exchange))
    return found


def speakers(config: Config):
    """Who a handle is to the organisation, for the screen to weigh what they said: a role, never a name's threshold."""
    aliases = routing.identities(config)
    canonical = lambda person: aliases.get(person, person)
    rows = [(row.area, canonical(row.owner), canonical(row.backup) if row.backup else "") for row in owners.load(config)]

    def speaker(platform: str, by: str, text: str, exchange: Exchange) -> str:
        if AGENT_POSTED.search(text):
            return "an agent, posting under a person's account"
        if BOT.search(by) or by.casefold() in ("claude", "codex", "copilot"):
            return "a bot"
        if by == exchange.author:
            return "the author of this work"
        person = canonical(f"{platform}:{by}")
        owned = [area for area, owner, _ in rows if owner == person]
        backs = [area for area, _, backup in rows if backup and backup == person]
        if owned:
            return "owner of " + ", ".join(owned)
        if backs:
            return "backup owner of " + ", ".join(backs)
        return "a teammate"
    return speaker


def questions(role: roles.Role, n: int, key: str) -> list[dict]:
    """Named by the item's key, so an answer lands on the item it was asked about even if the exchange grew since."""
    screen = role.screen
    return [
        {"type": "choice", "name": f"kind_{key}", "choices": [{"value": k["kind"], "description": k["covers"]}
                                                           for k in screen["kinds"]],
         "instructions": f"Item [{n}]: what kind of item is it, for a newcomer learning how this team works?"},
        {"type": "score", "name": f"worth_{key}", "levels": [{"label": str(level), "description": text}
                                                         for level, text in enumerate(screen["worth"])],
         "instructions": f"Item [{n}]: how much does it teach about how THIS team works, beyond this piece of work?"},
        {"type": "choice", "name": f"fact_{key}", "choices": FACT,
         "instructions": f"Item [{n}]: does it state a fact about the organisation?"},
    ]


def requests(config: Config, name: str, since: date | None = None, limit: int = 20) -> list[dict]:
    """Questions for up to `limit` exchanges with items not yet labelled. Each item is shown with the whole exchange,
    numbered, each line saying who spoke and their role; only the unlabelled items are asked about."""
    role = roles.load(config, name)
    if not role.screen:
        raise ValueError(f"role {name!r} has no screen in its Role.md; add one (see the shipped template)")
    since = since or config.today() - timedelta(days=SINCE_DAYS)
    done, speaker, out = labels(config, name), speakers(config), []
    # An exchange asked about once stays asked about until every item is labelled, however old it grows.
    asked = {row["exchange"] for row in _rows(folder(config, name) / "asked.jsonl")} | {label.exchange for label in done.values()}
    fresh = []
    for platform, exchange in exchanges(config, role, None):
        if exchange.id not in asked and (exchange.at or max(said.at for said in exchange.items)) < since.isoformat():
            continue
        todo = [said for said in exchange.items if f"{exchange.id}/{_key(said)}" not in done]
        if not todo:
            continue
        lines = [f"[{said.n}] {said.at} {said.by} ({speaker(platform, said.by, said.text, exchange)})"
                 + (f" on {said.where}" if said.where else "") + f":\n{said.text[:TEXT]}" for said in exchange.items]
        head = (f"{exchange.title} (by {exchange.author}, {exchange.outcome}){' ' + exchange.link if exchange.link else ''}"
                "\n\nEverything said on it, numbered, oldest first:\n\n")
        for start in range(0, len(todo), ITEMS):
            part = todo[start:start + ITEMS]
            out.append({"role": name, "exchange": exchange.id, "items": [_key(said) for said in part],
                        "input": head + "\n\n".join(lines),
                        "questions": [q for said in part for q in questions(role, said.n, _key(said))]})
            if exchange.id not in asked:
                fresh.append({"exchange": exchange.id})
                asked.add(exchange.id)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    with evidence_lock(folder(config, name)):
        _append(folder(config, name) / "asked.jsonl", fresh)
    return out


def _answer(answers: dict, name: str, field: str):
    answer = answers.get(name) or {}
    return answer.get(field)


def record(config: Config, name: str, answered: list[dict], today: date | None = None) -> int:
    """Keep a classifier's answers. An item already labelled, or one the answers leave out or garble, is skipped, so a
    repeat changes nothing and a gap is asked again next time."""
    role = roles.load(config, name)
    by_id = {exchange.id: (platform, exchange) for platform, exchange in exchanges(config, role, None)}
    speaker, kinds = speakers(config), {k["kind"] for k in role.screen.get("kinds", [])}
    # What is labelled is read and appended under one lock, so two records can't both take an item.
    with evidence_lock(folder(config, name)):
        rows = _labelled(config, name, role, answered, by_id, speaker, kinds, today)
        _append(folder(config, name) / "labels.jsonl", rows)
    return len(rows)


def _labelled(config, name, role, answered, by_id, speaker, kinds, today) -> list[dict]:
    done, rows = labels(config, name), []
    for entry in answered:
        found = by_id.get(entry.get("exchange", ""))
        if not found:
            continue
        platform, exchange = found
        answers = {a.get("name"): a for a in entry.get("answers", []) if isinstance(a, dict)}
        for said in exchange.items:
            key = _key(said)
            ident = f"{exchange.id}/{key}"
            kind, worth, fact = (_answer(answers, f"kind_{key}", "choice"), _answer(answers, f"worth_{key}", "score"),
                                 _answer(answers, f"fact_{key}", "choice"))
            if ident in done or kind not in kinds or fact not in ("yes", "no"):
                continue
            try:
                worth = float(worth)
            except (TypeError, ValueError):
                continue
            if not 0 <= worth <= len(role.screen["worth"]) - 1:
                continue   # off the scale: a garbled answer, asked again
            worth = int(round(worth))
            label = Label(ident, exchange.id, said.n, said.at, said.by, speaker(platform, said.by, said.text, exchange),
                          kind, worth, fact == "yes",
                          str(entry.get("classifier", "")), (today or config.today()).isoformat(), said.text[:TEXT],
                          said.where)
            done[ident] = label
            rows.append(asdict(label))
    return rows


MACHINES = ("a bot", "an agent, posting under a person's account")


def kept(role: roles.Role, label: Label, following: list[Label]) -> bool:
    """Whether the study reads it: a kind the template keeps at its worth or above, or a company fact. A bot's or an
    agent's finding counts only when a person later spoke on what it was about (the same file, or anywhere for a finding
    on the whole work), since the answer is what teaches."""
    keep = role.screen["keep"]
    if not (label.fact or label.kind in keep["kinds"] and label.worth >= keep["worth"]):
        return False
    if label.speaker in MACHINES:
        return answer(label, following) is not None
    return True


def answer(label: Label, following: list[Label]) -> Label | None:
    """The first person to speak after a machine's finding on what it was about: the same file, or anywhere for a
    finding on the whole work. Without threads this is the best reading of who answered it."""
    return next((other for other in following
                 if other.speaker not in MACHINES and (not label.where or other.where == label.where)), None)


def digest(config: Config, name: str, limit: int = 40) -> tuple[str, list[str]]:
    """What the study reads next: up to `limit` exchanges with kept items not yet studied, oldest first, each kept item
    in bold with the item before and after it. Returns the text and the ids it shows, which `done` marks studied."""
    role = roles.load(config, name)
    every, seen = labels(config, name), studied(config, name)
    by_exchange: dict[str, list[Label]] = {}
    for label in every.values():
        by_exchange.setdefault(label.exchange, []).append(label)
    # Only a whole exchange is studied, in the order it reads now: a ruling waits until what it answered is labelled,
    # and a comment edited since it was labelled is left out with its old words.
    current = {exchange.id: [every[f"{exchange.id}/{_key(said)}"] for said in exchange.items]
               for _, exchange in exchanges(config, role, None)
               if all(f"{exchange.id}/{_key(said)}" in every for said in exchange.items)}
    blocks, shown = [], []
    for exchange_id, items in sorted(current.items(), key=lambda kv: min(label.at for label in kv[1])):
        take = [i for i, label in enumerate(items) if label.id not in seen and kept(role, label, items[i + 1:])]
        if not take:
            continue
        context = {j for i in take for j in (i - 1, i, i + 1) if 0 <= j < len(items)}
        for i in take:   # a machine's finding is shown with the person who answered it
            if items[i].speaker in MACHINES and (reply := answer(items[i], items[i + 1:])):
                context.add(items.index(reply))
        lines = [f"### {exchange_id}"]
        for j in sorted(context):
            label, mark = items[j], "**" if j in take else ""
            text = " ".join(label.text.split())
            lines.append(f"- {mark}{label.at} {label.by} ({label.speaker}){mark} [{label.kind}, {label.worth}"
                         f"{', fact' if label.fact else ''}]: {fm.cut(text, 160)}")
        blocks.append("\n".join(lines))
        shown.extend(items[i].id for i in take)
        if len(blocks) >= limit:
            break
    return "\n\n".join(blocks), shown


def shown_path(config: Config, name: str) -> Path:
    return folder(config, name) / "shown.json"


def next_digest(config: Config, name: str, limit: int = 40) -> str:
    """The digest, remembering what it showed, so `done` marks exactly that and an interrupted study sees it again."""
    text, ids = digest(config, name, limit)
    path = shown_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps(ids) + "\n")
    return text


def done(config: Config, name: str, today: date | None = None) -> int:
    path = shown_path(config, name)
    try:
        ids = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    already = studied(config, name)
    rows = [{"id": ident, "studied": (today or config.today()).isoformat()} for ident in ids if ident not in already]
    _append(folder(config, name) / "studied.jsonl", rows)
    path.unlink(missing_ok=True)
    return len(rows)


def calibrate(config: Config, name: str, sample: list[dict]) -> dict:
    """How far the screen agrees with labels a strong model or a person made: per item, the kind, and whether it would
    be kept. A sample row is `{"id", "kind", "kept"}`; items the screen hasn't labelled are left out."""
    role = roles.load(config, name)
    every = labels(config, name)
    by_exchange: dict[str, list[Label]] = {}
    for label in every.values():
        by_exchange.setdefault(label.exchange, []).append(label)
    for items in by_exchange.values():
        items.sort(key=lambda label: label.n)
    compared = kind_agree = keep_agree = 0
    for row in sample:
        label = every.get(row.get("id", ""))
        if label is None:
            continue
        items = by_exchange[label.exchange]
        compared += 1
        kind_agree += row.get("kind") == label.kind
        keep_agree += bool(row.get("kept")) == kept(role, label, items[items.index(label) + 1:])
    return {"compared": compared, "kind": round(kind_agree / compared, 3) if compared else None,
            "kept": round(keep_agree / compared, 3) if compared else None}


def routines(name: str, prefix: str) -> dict[str, dict]:
    """A role's nightly pair: the study refreshes its sources and studies what the last screen kept; then the screen
    labels what was fetched, for the next night. The study reads a night behind its fetch, so neither waits on the other."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return {f"role-{slug}-study": {"cron": "30 1 * * *", "skills": [f"{prefix}study"],
                                   "prompt": f"Run the nightly study for the {name} role."},
            f"role-{slug}-screen": {"after": f"role-{slug}-study", "prompt": "", "agent": False,
                                    "classify": ["roles", "screen", name]}}
