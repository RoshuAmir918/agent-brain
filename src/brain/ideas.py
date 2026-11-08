"""Proposals for new ways to help, shown one at a time at the owner's pace."""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import fm, health, tasks
from . import needs as needs_mod
from .config import Config
from .lock import vault_lock
from .needs import Item
from .vault import Vault

NEW_DAYS = 21
COOLDOWN_NEW = 1
COOLDOWN_ESTABLISHED = 7
SNOOZE_DAYS = 14
ADD_SPACING_NEW = 1
ADD_SPACING_ESTABLISHED = 7

TEMPLATE = """---
kind: orientation
tier: warm
pinned: false
---
# Ideas

Proposals for new ways to help, for the owner to consider one at a time.

## Open

"""
DETAIL = re.compile(r"^[ \t]+-[ \t]+(why|needs|goal):[ \t]*(.+?)[ \t]*$")
ANSWERS = ("yes", "not-now", "no", "stop-asking")


def _bullet(line: str) -> Item | None:
    match = re.fullmatch(r'- (\d{4}-\d{2}-\d{2}) · (.+?) · (\[\[[^\[\]\n]+\]\]|"[^"\n]+")', line.strip())
    if not match or len(line.split(" · ")) != 3 or not match[2].strip():
        return None
    try:
        date.fromisoformat(match[1])
    except ValueError:
        return None
    source = match[3]
    if source.startswith("[["):
        source = source[2:-2].split("|", 1)[0].strip()
    return Item("idea", match[2].strip(), "ideas", match[1], source) if source else None


def parse_section(body: str, *, cut: bool = True) -> list[Item]:
    """Read Open bullets and their optional, indented why and needs lines; hygiene reads the uncut why."""
    span = fm.section(body, "Open")
    out = []
    if span is None:
        return out
    for line in body[span[0]:span[1]].splitlines():
        detail = DETAIL.match(line)
        if detail and out:
            field = "detail" if detail[1] == "why" else detail[1]
            setattr(out[-1], field, ((getattr(out[-1], field) or "") + " " + detail[2]).strip())
        elif (item := _bullet(line)) is not None:
            out.append(item)
    if cut:
        for item in out:
            item.detail = fm.cut(item.detail, needs_mod.WHY_WORDS)
    return out


def _responses(config: Config) -> list[tuple[date, str, str, str]]:
    """(day, answer, text, id) from Answered, retaining the existing dismissal format for People review."""
    out = []
    for path in sorted((config.vault_path / config.agent("Answered")).glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.removeprefix("- ").split(" · ")
            if len(parts) < 4:
                continue
            if parts[1] in ("dismissed no", "dismissed stop-asking") and parts[2] == "idea":
                answer, text = parts[1].removeprefix("dismissed "), parts[3]
            elif parts[1] == "idea" and parts[3] in ANSWERS:
                answer, text = parts[3], parts[2]
            else:
                continue
            try:
                day = date.fromisoformat(parts[0])
            except ValueError:
                continue
            ident = next((part[3:] for part in parts[4:] if re.fullmatch(r"id:[0-9a-f]{12}", part)), "")
            out.append((day, answer, text, ident))
    return out


def _open(vault_or_config: Vault | Config, today: date) -> list[Item]:
    """All unclosed ideas, including those still snoozed; old proposals remain available in --all."""
    vault = vault_or_config if isinstance(vault_or_config, Vault) else Vault(vault_or_config.vault_path)
    config = vault_or_config if isinstance(vault_or_config, Config) else vault.config
    note = vault.notes.get(config.agent("Ideas.md"))
    out = parse_section(note.body) if note else []
    for rel, note in vault.notes.items():
        if vault.kind_for(rel) == "hub":
            for item in needs_mod.improvements(note.body, rel, today):
                item.kind = "idea"   # the origin/source/label keep existing proposal ids stable
                out.append(item)
    gone = needs_mod.dismissed_ids(config)
    rejected = {fm.plain(text) for _, answer, text, _ in _responses(config) if answer in ("no", "stop-asking")}
    return sorted((item for item in out if item.id not in gone and fm.plain(item.text) not in rejected),
                  key=lambda item: item.since or "", reverse=True)


def _snoozed(config: Config, today: date) -> set[str]:
    latest = {}
    for day, answer, _, ident in sorted(_responses(config), key=lambda response: response[0]):
        if ident and day <= today:
            latest[ident] = (day, answer)
    return {ident for ident, (day, answer) in latest.items()
            if answer == "not-now" and today < day + timedelta(days=SNOOZE_DAYS)}


def items(vault_or_config: Vault | Config, today: date) -> list[Item]:
    config = vault_or_config if isinstance(vault_or_config, Config) else vault_or_config.config
    snoozed = _snoozed(config, today)
    return [item for item in _open(vault_or_config, today) if item.id not in snoozed]


def new_owner(config: Config, today: date) -> bool:
    """The vault's first NEW_DAYS days, by its first commit: the time to show what the agent can be. By age alone, so an
    owner who simply wants little is not offered ideas at the new-owner pace forever."""
    roots = (health.git(config.repo, "log", "--max-parents=0", "--format=%cs") or "").split()
    if not roots:
        return True   # without a first commit, the vault has not established its age
    return (today - date.fromisoformat(min(roots))).days < NEW_DAYS


def cooldown_until(config: Config, today: date) -> date | None:
    days = [day for day, _, _, _ in _responses(config) if day <= today]
    if not days:
        return None
    until = max(days) + timedelta(days=COOLDOWN_NEW if new_owner(config, today) else COOLDOWN_ESTABLISHED)
    return until if today < until else None


def shown(config: Config, today: date) -> Item | None:
    if cooldown_until(config, today):
        return None
    return next(iter(items(config, today)), None)


def add_refusal(config: Config, today: date, text: str) -> str | None:
    text = " ".join(text.replace("·", "-").split())
    needle = fm.plain(text)
    if not needle:
        return "idea text must not be empty"
    if any(fm.plain(item.text) == needle for item in _open(config, today)):
        return "the same idea is already open (including snoozed ideas)"
    if any(fm.plain(previous) == needle for _, answer, previous, _ in _responses(config)
           if answer in ("no", "stop-asking")):
        return "the owner already dismissed this idea with no or stop-asking"
    path = config.vault_path / config.agent("Ideas.md")
    dates = [date.fromisoformat(item.since) for item in parse_section(fm.read(path)[1])] if path.exists() else []
    spacing = ADD_SPACING_NEW if new_owner(config, today) else ADD_SPACING_ESTABLISHED
    if dates and today < (until := max(dates) + timedelta(days=spacing)):
        return f"ideas can be added every {spacing} day(s); next idea can be added {until.isoformat()}"
    return None


def add(config: Config, today: date, text: str, source: str, why: str = "", needs: str = "", goal: str | None = None) -> None:
    with vault_lock(config):
        from .goals import guard
        guard(config, goal, source)
        if refusal := add_refusal(config, today, text):
            raise ValueError(refusal)
        source = source.strip()
        if not source or any(char in source for char in "[]\r\n"):
            raise ValueError("source must name a note, without wikilink brackets or newlines")
        path = config.vault_path / config.agent("Ideas.md")
        data, body = fm.read(path) if path.exists() else fm.split(TEMPLATE)
        text = " ".join(text.replace("·", "-").split())
        reference = source if re.fullmatch(r'"[^"\n]+"', source) else f"[[{source}]]"
        bullet = f"- {today.isoformat()} · {text} · {reference}"
        for label, value in (("why", why), ("needs", needs), ("goal", goal or "")):
            if value.strip():
                bullet += f"\n  - {label}: {' '.join(value.split())}"
        span = fm.section(body, "Open")
        if span is None:
            body = body.rstrip("\n") + "\n\n## Open\n\n" + bullet + "\n"
        else:
            body = body[:span[1]].rstrip("\n") + "\n\n" + bullet + "\n\n" + body[span[1]:]
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write(path, data, body)
        from . import reception
        reception.record(config, "idea", text, source)


def respond(config: Config, which: str, today: date, answer: str) -> Item:
    if answer not in ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(ANSWERS)}")
    with vault_lock(config):
        candidates = _open(config, today)
        chosen = [item for item in candidates if item.id == which]
        if not chosen:
            needle = fm.plain(which)
            chosen = [item for item in candidates if needle and needle in fm.plain(item.text)]
        if len(chosen) != 1:
            raise ValueError(("no open idea matches " if not chosen else f"{len(chosen)} open ideas match ")
                             + repr(which) + "; use an id from `brain ideas --all`")
        item = chosen[0]
        tid = None
        if answer == "yes":
            tid = tasks.next_id(config)
            body = f"- **Status:** {item.detail}\n" if item.detail else ""
            if item.needs:
                body += f"- **Needs:** {item.needs}\n"
            reference = item.source if item.source.startswith('"') else f"[[{item.source}]]"
            body += f"- **Source:** {reference}\n"
            if item.goal:
                body += f"- **Goal:** {item.goal}\n"
            task = tasks.Task(tid, item.text, tasks.with_handoff(body, today, "Home"), {"state": "ready"})
            task.add_use(today.isoformat())
            tasks.append_task(config, task)
        if answer != "not-now":
            if item.origin == "ideas":
                path = config.vault_path / config.agent("Ideas.md")
                data, body = fm.read(path)
                span = fm.section(body, "Open")
                body, _ = fm.without(body, span, lambda bullet: bullet.id == item.id,
                                     bullet=_bullet, detail=DETAIL)
                fm.write(path, data, body)
            else:
                rel, _, anchor = item.source.partition("#")
                path = config.vault_path / rel
                body = path.read_text(encoding="utf-8")
                heading = re.search(r"^### " + re.escape(anchor) + r"[ \t]*\r?$", body, re.M)
                following = re.compile(r"^#{2,3} ", re.M).search(body, heading.end())
                end = following.start() if following else len(body)
                entry = body[heading.end():end]
                status = f"`{'accepted' if answer == 'yes' else 'declined'}`, {today.isoformat()}"
                changed = needs_mod.STATUS_FIELD.sub(lambda m: m[0].replace(m[1], status), entry, count=1)
                if changed == entry:
                    changed = entry.rstrip("\n") + f"\n- **Status:** {status}\n\n"
                fm.write_text(path, body[:heading.end()] + changed + body[end:])
        text = item.text.replace("·", "-")
        if answer in ("no", "stop-asking"):
            line = f"- {today.isoformat()} · dismissed {answer} · idea · {text} · id:{item.id}"
        else:
            line = f"- {today.isoformat()} · idea · {text} · {answer}" + (f" · {tid}" if tid else "") + f" · id:{item.id}"
        needs_mod.answered(config, today, line + (f" · goal:{item.goal}" if item.goal else "") + f" · [[{item.source}]]")
        from . import reception
        reception.answered(config, "idea", item.text, "used" if answer == "yes" else f"declined:{answer}")
        return item
