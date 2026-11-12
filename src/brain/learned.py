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
OVERLAP = 0.8   # roles.refusal's: at this share of words in common, two claims say the same thing
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


def same(a: str, b: str) -> bool:
    ours, theirs = _words(a), _words(b)
    return bool(ours and theirs) and len(ours & theirs) / len(ours | theirs) >= OVERLAP


def add(config: Config, name: str, claim: str, source: str, day: str, status: str = "active",
        applies: list[str] | None = None) -> tuple[str, Line]:
    """Learn one line in a field, making the field when it is new. Returns what happened and the line: `added`, `held`
    (a line already says it, returned instead) or `refused` (the owner struck one like it, and the evidence is no newer
    than the strike)."""
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
        if struck := next((line for line in note.struck if same(line.claim, claim) and day <= line.struck_at), None):
            return "refused", struck
        if held := next((line for line in note.lines if same(line.claim, claim)), None):
            return "held", held
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
        return "added", Line(ident, text, status)


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
        return Line(line.id, text, line.status, day)


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
