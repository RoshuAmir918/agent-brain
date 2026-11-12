"""How to do a kind of work well: one note per field under `<agent>/Learned/`, in the role lesson grammar, each line
`- <how to act> (<source>, <date>) [*(candidate)* | *(rule)*] ^id`. A struck line moves under `## Struck` with who
struck it and when, and a claim close to it is refused until newer evidence says otherwise."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import fm, items
from .config import Config
from .lock import vault_lock
from .roles import LESSON, NAME, SOURCES, STATUS, STRUCK, _words, line_id

FOLDER = "Learned"
LOG = "Learned Log"   # monthly rows of what was learned, repeated, struck and promoted: how recurrence is counted
LOG_HEADER = ("---\nkind: log\ntier: cold\n---\n# Learned log\n\nOne row per change to a Learned field: date, event, "
              "field, line id, claim, source. A repeat is a correction landing on a line already held.\n\n")
LOG_ROW = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2}) · (?P<event>[a-z]+(?::[a-z0-9]+)?) · (?P<field>[^·]+?) · \^(?P<id>[0-9a-f]{4}) · "
                     r"(?P<claim>.+?) · (?P<source>.*?)\s*$")
OVERLAP = 0.8   # roles.refusal's share of words in common; it cannot tell a claim from its reversal, so it only flags
STRUCK_BY = re.compile(r"; struck by (?P<by>.+?) (?P<at>\d{4}-\d{2}-\d{2})\b")


@dataclass
class Line:
    id: str
    text: str   # the line as written, without its bullet or id
    status: str   # rule, active or candidate
    struck_at: str = ""

    @property
    def claim(self) -> str:
        return re.sub(r"\s*\([^()]*(?:\([^()]*\)[^()]*)*\)$", "", STATUS.sub("", self.text).rstrip())


@dataclass
class Note:
    field: str
    path: Path
    applies: list[str]
    hubs: list[str]
    lines: list[Line] = field(default_factory=list)
    struck: list[Line] = field(default_factory=list)


def folder(config: Config) -> Path:
    return config.vault_path / config.agent(FOLDER)


def log(config: Config, day: str, event: str, field: str, ident: str, claim: str, source: str) -> None:
    """One row in the month's log; callers hold the vault lock."""
    path = config.vault_path / config.agent(LOG) / f"{day[:7]}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else LOG_HEADER
    clean = [" ".join(part.replace("·", "-").split()) for part in (field, claim, source)]
    path.write_text(text.rstrip("\n") + f"\n- {day} · {event} · {clean[0]} · ^{ident} · {clean[1][:200]} · {clean[2][:160]}\n",
                    encoding="utf-8")


def logged(config: Config, since: str) -> list[dict]:
    found = []
    base = config.vault_path / config.agent(LOG)
    for path in sorted(base.glob("*.md")) if base.is_dir() else []:
        if path.stem < since[:7]:
            continue
        found += [match.groupdict() for line in path.read_text(encoding="utf-8").splitlines()
                  if (match := LOG_ROW.match(line)) and match["at"] >= since]
    return found


def account(config: Config, today: str, days: int = 7) -> str:
    """The week in learning: the owner's corrections, how many landed on a line already held (the lesson was learned
    but did not reach the work), and the lines repeated most."""
    from datetime import date, timedelta
    since = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
    rows = logged(config, since)
    corrections = [row for row in rows if row["event"] in ("added:rule", "added:active", "repeat")]
    repeats = [row for row in rows if row["event"] == "repeat"]
    near = [row for row in rows if row["event"].startswith("near:")]
    counts: dict[tuple[str, str], int] = {}
    for row in repeats:
        counts[(row["field"], row["id"])] = counts.get((row["field"], row["id"]), 0) + 1
    out = [f"Learned, {since} to {today}: {len(corrections)} correction{'s' * (len(corrections) != 1)} from "
           f"{config.human_name}, {len(repeats)} repeating a line already held, {len(near)} close to one (said again "
           f"in other words, or a change of mind); "
           + f"{sum(row['event'] == 'added:candidate' for row in rows)} lessons of your own, "
           + f"{sum(row['event'] == 'promoted' for row in rows)} promoted, {sum(row['event'] == 'struck' for row in rows)} struck."]
    for (field, ident), count in sorted(counts.items(), key=lambda item: -item[1])[:5]:
        claim = next(row["claim"] for row in repeats if row["id"] == ident and row["field"] == field)
        out.append(f"- repeated {count}x: {field} ^{ident}: {claim}")
    return "\n".join(out)


def path_for(config: Config, name: str) -> Path:
    if not NAME.fullmatch(name) or name in (STRUCK, FOLDER):
        raise ValueError(f"a field is a short name of letters, digits, spaces and dashes, not {name!r}")
    return folder(config) / f"{name}.md"


def read(path: Path) -> Note:
    data, body = fm.read(path)
    note = Note(str(data.get("field") or path.stem), path, _strings(data.get("applies")), _strings(data.get("hubs")))
    section = ""
    for raw in body.splitlines():
        if raw.startswith("## "):
            section = raw[3:].strip()
            continue
        match = LESSON.match(raw)
        if not match:
            continue
        status = STATUS.search(match["text"])
        line = Line(match["id"] or "", match["text"], status[1] if status else "active")
        if section == STRUCK:
            struck = STRUCK_BY.search(line.text)
            line.struck_at = struck["at"] if struck else ""
            note.struck.append(line)
        else:
            note.lines.append(line)
    return note


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def notes(config: Config) -> list[Note]:
    out = []
    for path in sorted(folder(config).glob("*.md")) if folder(config).is_dir() else []:
        try:
            out.append(read(path))
        except fm.Malformed:
            continue   # validate names it
    return out


def find(config: Config, name: str) -> Note | None:
    """A field by name, any case, so the record's "coding" lands in Coding."""
    return next((note for note in notes(config) if note.field.casefold() == name.strip().casefold()), None)


def plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


NEGATIONS = {"no", "not", "never", "don", "dont", "t", "avoid", "without", "stop", "nor"}


def close(a: str, b: str) -> bool:
    """Most words in common: the same lesson, a refinement of it, or its reversal ("never use bullets"), each worth
    showing beside the line it may replace."""
    ours, theirs = _words(a), _words(b)
    return bool(ours and theirs) and len(ours & theirs) / len(ours | theirs) >= OVERLAP


def same_lesson(a: str, b: str) -> bool:
    """Close and with the same polarity, so a strike can refuse it: a negation on one side only is a reversal, which
    agrees with striking the original rather than repeating it."""
    return close(a, b) and bool(_words(a) & NEGATIONS) == bool(_words(b) & NEGATIONS)


def add(config: Config, name: str, claim: str, source: str, day: str, status: str = "active",
        applies: list[str] | None = None) -> tuple[str, Line, list[Line]]:
    """Learn one line in a field, making the field when it is new. Returns what happened, the line, and the field's
    lines close to it, for the writer to strike any the new one replaces. What happened is `added`, `held` (a line
    already says it in the same words, returned instead) or `refused` (your own candidate, close to a line someone struck,
    with no evidence newer than the strike). The owner's words are never refused: a reversal looks like what it reverses."""
    claim, source = " ".join(claim.split()), " ".join(source.replace("(", "").replace(")", "").split())
    if not claim or not source:
        raise ValueError("a line needs a claim and its source")
    if status not in ("rule", "active", "candidate"):
        raise ValueError("status is rule, active or candidate")
    with vault_lock(config):
        note = find(config, name)
        path = note.path if note else path_for(config, name[:1].upper() + name[1:].strip())
        if note is None:
            if path.exists():
                raise ValueError(f"{path.name} has malformed frontmatter; fix it before learning more there")
            path.parent.mkdir(parents=True, exist_ok=True)
            fm.write(path, {"kind": "learned", "field": path.stem, "tier": "warm",
                            "applies": list(applies or [path.stem.casefold()]), "hubs": []},
                     f"# {path.stem}\n\n## {STRUCK}\n")
            note = read(path)
        if status == "candidate" and (struck := next((line for line in note.struck
                                                      if same_lesson(line.claim, claim) and day <= line.struck_at), None)):
            return "refused", struck, []
        if held := next((line for line in note.lines if plain(line.claim) == plain(claim)), None):
            if status != "candidate":   # the owner saying it again: it was learned and did not reach the work
                log(config, day, "repeat", note.field, held.id, claim, source)
            return "held", held, []
        near = [line for line in note.lines if close(line.claim, claim)]
        data, body = fm.read(path)
        if applies:
            data["applies"] = list(dict.fromkeys([*_strings(data.get("applies")), *applies]))
        ident = line_id(claim, {line.id for line in note.lines + note.struck})
        marker = {"rule": " *(rule)*", "candidate": " *(candidate)*"}.get(status, "")
        text = f"{claim} ({source}, {day}){marker}"
        span = fm.section(body, STRUCK)
        heading = body.rfind(f"## {STRUCK}", 0, span[0]) if span else len(body)
        before = body[:heading].rstrip("\n")
        before += "\n\n" if before.rsplit("\n", 1)[-1].startswith("#") else "\n"
        body = f"{before}- {text} ^{ident}\n\n{body[heading:]}" if span else f"{before}- {text} ^{ident}\n"
        fm.write(path, data, body)
        log(config, day, f"added:{status}", note.field, ident, claim, source)
        for other in near if status != "candidate" else []:
            log(config, day, f"near:{ident}", note.field, other.id, other.claim, source)
        return "added", Line(ident, text, status), near


def strike(config: Config, name: str, which: str, by: str, why: str, day: str) -> Line:
    """Move a line under Struck with who struck it, when and why. A rule is the owner's words, so only they strike one."""
    why = " ".join(why.replace("(", "").replace(")", "").replace('"', "'").split())
    if not why:
        raise ValueError("say why it is struck")
    with vault_lock(config):
        note = find(config, name)
        if note is None:
            raise ValueError(f"no field {name}; see `brain learn fields`")
        line = items.pick(note.lines, which, "line", f"brain learn show {note.field}")
        if line.status == "rule" and by != config.human_name:
            raise ValueError(f"{line.id} is {config.human_name}'s rule; only they strike it")
        text = STATUS.sub("", line.text).rstrip()
        text = (text[:-1] + f"; struck by {by} {day}: \"{why}\")") if text.endswith(")") else (
            f"{text} (struck by {by} {day}: \"{why}\")")
        data, body = fm.read(note.path)
        body = re.sub(rf"^- {re.escape(line.text)} \^{line.id}[ \t]*\n?", "", body, count=1, flags=re.M)
        if fm.section(body, STRUCK) is None:
            body = body.rstrip("\n") + f"\n\n## {STRUCK}\n"
        end = fm.section(body, STRUCK)[1]
        kept = body[:end].rstrip("\n")
        body = f"{kept}\n- {text} ^{line.id}\n" + (f"\n{body[end:]}" if body[end:] else "")
        fm.write(note.path, data, body)
        log(config, day, "struck", note.field, line.id, line.claim, f"{by}: {why}")
        return Line(line.id, text, line.status, day)


def promote(config: Config, name: str, which: str, evidence: str) -> Line:
    """Make your own candidate an active line once a second, independent piece of evidence supports it; the evidence
    joins its sources."""
    evidence = " ".join(evidence.replace("(", "").replace(")", "").split())
    if not evidence:
        raise ValueError("name the second evidence")
    with vault_lock(config):
        note = find(config, name)
        if note is None:
            raise ValueError(f"no field {name}; see `brain learn fields`")
        line = items.pick([line for line in note.lines if line.status == "candidate"], which, "candidate",
                          f"brain learn show \"{note.field}\"")
        text = STATUS.sub("", line.text).rstrip()
        text = text[:-1] + f"; {evidence})" if text.endswith(")") else f"{text} ({evidence})"
        data, body = fm.read(note.path)
        body = re.sub(rf"^- {re.escape(line.text)} \^{line.id}[ \t]*$", lambda _: f"- {text} ^{line.id}", body, count=1, flags=re.M)
        fm.write(note.path, data, body)
        log(config, config.today().isoformat(), "promoted", note.field, line.id, line.claim, evidence)
        return Line(line.id, text, "active")


def merge(config: Config, source: str, target: str, day: str) -> tuple[Note, int]:
    """Fold one field into another, broader one: every line moves with its id and status, struck lines too, unless the
    target already holds it in the same words; the key words join, the source note goes, and a responsibility that
    named the source names the target. Returns the target and how many lines moved."""
    from . import responsibilities
    from .vault import Vault
    with vault_lock(config):
        old, new = find(config, source), find(config, target)
        if old is None or new is None:
            raise ValueError(f"no field {source if old is None else target}; see `brain learn fields`")
        if old.path == new.path:
            raise ValueError("a field cannot be merged into itself")
        taken = {line.id for line in new.lines + new.struck}
        held = {plain(line.claim) for line in new.lines + new.struck}
        moved: dict[str, list[str]] = {"live": [], "struck": []}
        for kind, lines in (("live", old.lines), ("struck", old.struck)):
            for line in lines:
                if plain(line.claim) in held:
                    continue
                ident = line.id if line.id and line.id not in taken else line_id(line.claim, taken)
                taken.add(ident)
                held.add(plain(line.claim))
                moved[kind].append(f"- {line.text} ^{ident}")
                log(config, day, "moved", new.field, ident, line.claim, f"from {old.field}")
        data, body = fm.read(new.path)
        data["applies"] = list(dict.fromkeys([*_strings(data.get("applies")), old.field.casefold(), *old.applies]))
        data["hubs"] = list(dict.fromkeys([*_strings(data.get("hubs")), *old.hubs]))
        if fm.section(body, STRUCK) is None:
            body = body.rstrip("\n") + f"\n\n## {STRUCK}\n"
        heading = body.rfind(f"## {STRUCK}")
        before = body[:heading].rstrip("\n")
        before += "\n\n" if before.rsplit("\n", 1)[-1].startswith("#") else "\n"
        struck = body[heading:].rstrip("\n")
        body = (before + "".join(line + "\n" for line in moved["live"]) + "\n" + struck + "\n"
                + "".join(line + "\n" for line in moved["struck"]))
        fm.write(new.path, data, body)
        old.path.unlink()
        renamed = False
        for path in (config.vault_path / "Responsibilities").glob("*/RESPONSIBILITY.md"):
            meta, text = fm.read(path)
            names = meta.get("learned")
            if isinstance(names, list) and any(isinstance(n, str) and n.casefold() == old.field.casefold() for n in names):
                meta["learned"] = list(dict.fromkeys(new.field if isinstance(n, str) and n.casefold() == old.field.casefold()
                                                     else n for n in names))
                fm.write(path, meta, text)
                renamed = True
        if renamed:
            responsibilities.sync(Vault(config.vault_path), locked=True)
        return read(new.path), len(moved["live"]) + len(moved["struck"])

def index(config: Config) -> list[str]:
    """One line per field: its name, how many lines it holds, and its first rule."""
    out = []
    for note in notes(config):
        rule = next((line.claim for line in note.lines if line.status == "rule"), "")
        out.append(f"- {note.field} · {len(note.lines)} line{'s' * (len(note.lines) != 1)}"
                   + (f" · {fm.cut(rule, 16)}" if rule else ""))
    return out


def problems(config: Config) -> list[str]:
    out = []
    for note in notes(config):
        rel = note.path.relative_to(config.vault_path).as_posix()
        out += [f"{rel}: ^{line.id or '?'} has no source; end it with (where it was learned, date)"
                for line in note.lines if not SOURCES.search(line.text)]
    return out


def _mentions(text: str, term: str) -> bool:
    return bool(term.strip()) and re.search(r"(?<![\w-])" + re.escape(term.strip()) + r"(?![\w-])", text, re.I) is not None


def matching(config: Config, text: str, *, strong: bool) -> list[Note]:
    """The fields some work is about, best first: those it names (the field or one of its hubs), then those whose
    `applies:` words it uses. A strong match, for a conversation, needs a name or two of those words."""
    scored = []
    for note in notes(config):
        named = _mentions(text, note.field) or any(_mentions(text, hub.removesuffix(".md")) for hub in note.hubs)
        terms = sum(_mentions(text, term) for term in note.applies)
        if named or terms >= (2 if strong else 1):
            scored.append((named, terms, note))
    return [note for _, _, note in sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)]


def block(note: Note, words: int, *, candidates: bool) -> str:
    """A field's lessons within `words`: rules first, then the newest others; your own candidates only when asked for,
    marked as yours."""
    rules = [line for line in note.lines if line.status == "rule"]
    others = [line for line in reversed(note.lines) if line.status == "active" or candidates and line.status == "candidate"]
    out = [f"How to do {note.field} work, from what you have learned (`brain learn show \"{note.field}\"`):"]
    for line in rules + others:
        text = f"- {line.claim}" + (" (your conclusion, unconfirmed)" if line.status == "candidate" else "")
        if len(" ".join(out + [text]).split()) <= words:
            out.append(text)
    return "\n".join(out) if len(out) > 1 else ""
