"""The owner's own sourced facts, resolved to exactly what an audience may hear."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from . import access
from .config import Config
from .vault import Vault

KINDS = ("said", "sent", "transactional", "calendar")
FORBIDDEN = ("people", "anything", "setup")
WORDS = 600
LINE = re.compile(r"^- (?P<text>.+?) · (?P<kind>[^ ·]+) · (?P<evidence>[^ ·]+) · (?P<source>.+?)[ \t]*$")
GIST = re.compile(r"^  - gist:[ \t]*(?P<text>.+?)[ \t]*$")


@dataclass
class Line:
    section: str
    text: str
    kind: str
    evidence: str
    source: str
    gist: str = ""


def parse(body: str, vocabulary: dict[str, str] | None = None) -> tuple[list[Line], list[str]]:
    """The valid lines and their gist forms, and what is malformed about the note."""
    vocabulary = access.SUBJECTS if vocabulary is None else vocabulary
    lines: list[Line] = []
    problems: list[str] = []
    section, above = "", None
    for raw in body.splitlines():
        if raw.startswith("## "):
            section, above = raw[3:].strip(), None
            if section in FORBIDDEN:
                problems.append(f"forbidden section {section!r}")
            elif section not in vocabulary:
                problems.append(f"unknown section {section!r}")
            continue
        if not raw.strip():
            continue
        if gist := GIST.match(raw):
            if above is None:
                problems.append(f"{section or 'Tellable'}: gist: line with no line above it")
            else:
                above.gist = (above.gist + " " + gist["text"]).strip()
            continue
        above = None
        if not section:
            if raw.lstrip().startswith("- "):
                problems.append(f"Tellable: malformed line outside a subject section {raw.strip()[:80]!r}")
            continue
        if section not in vocabulary or section in FORBIDDEN:
            continue
        match = LINE.match(raw)
        if (not match or len(raw.split(" · ")) != 4 or re.search(r"\s\^[\w-]+$", raw)
                or not match["text"].strip() or not match["source"].strip()):
            problems.append(f"{section}: malformed line {raw.strip()[:80]!r}; "
                            "write `- text · kind · YYYY-MM-DD · source`")
            continue
        where = f"{section} · {match['text']}"
        valid = True
        if match["kind"] not in KINDS:
            problems.append(f"{where}: kind {match['kind']!r}; one of {', '.join(KINDS)}")
            valid = False
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", match["evidence"]):
                raise ValueError
            date.fromisoformat(match["evidence"])
        except ValueError:
            problems.append(f"{where}: evidence date {match['evidence']!r} is not YYYY-MM-DD")
            valid = False
        if valid:
            above = Line(section, match["text"], match["kind"], match["evidence"], match["source"])
            lines.append(above)
    return lines, problems


def hygiene(body: str, where: str, vocabulary: dict[str, str] | None = None) -> list[str]:
    return [f"{where}: {problem}" for problem in parse(body, vocabulary)[1]]


def resolve(vault: Vault, config: Config, who: str = "anyone") -> dict:
    """Apply tell levels, then drop the oldest evidence until the rendered lines fit the word budget."""
    if who in ("known", "anyone"):
        effective = access.resolve(vault, config, audience=access.Audience(who))
    else:
        matches = access.matching_people(vault, config, who)
        if len(matches) > 1:
            raise ValueError("ambiguous person; matching profiles: "
                             + ", ".join(config.link(note.rel) for note in matches))
        effective = access.resolve(vault, config, matches[0].rel if matches else None)
    note = vault.notes.get(config.human_tellable)
    lines = parse(note.body, access.subjects(vault, config))[0] if note else []
    selected = []
    for line in sorted(lines, key=lambda line: line.evidence, reverse=True):
        level = effective.subjects[line.section].tell
        text = line.text if level == "details" else line.gist if level == "gist" else ""
        if text:
            selected.append((line, level, text))
    words = sum(len(f"- {line.section} ({level}): {text}".split()) for line, level, text in selected)
    while words > WORDS:
        line, level, text = selected.pop()
        words -= len(f"- {line.section} ({level}): {text}".split())
    subjects = {}
    for subject, value in effective.subjects.items():
        texts = [text for line, _, text in selected if line.section == subject]
        if texts:
            subjects[subject] = {"level": value.tell, "lines": texts}
    return {"person": effective.person, "groups": effective.groups, "subjects": subjects}


def render(result: dict) -> str:
    return "\n".join(f"- {subject} ({value['level']}): {text}"
                     for subject, value in result["subjects"].items() for text in value["lines"]) or "Nothing."
