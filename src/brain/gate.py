"""The decision gate (docs/roles.md section 7): at plan time each decision is sorted by who decides it, whether it can be
undone and whether its answer can be found, then a fixed rule says what the role does. Whether to ask is decided here,
not in the moment: in replay, whether a plain intellect asked was a coin flip on identical input.

Who decides comes from the people's own records: `Owners.md`'s areas, its Restricted list (always ask a named person)
and each area's earned autonomy (consults.py). A role's notes say what the craft decides; they never say who decides."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from . import consults, fm, owners, roles, screen
from .config import Config

VERDICTS = ("decide", "flag", "ask")


@dataclass
class Verdict:
    decision: str
    type: str
    verdict: str   # decide: the craft or findable, cite it; flag: someone's and reversible, do it and say so; ask: early
    area: str = ""
    person: str = ""
    name: str = ""
    reason: str = ""
    recommendation: str = ""
    default: str = ""


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> set[str]:
    return {_stem(word) for word in re.findall(r"[a-z0-9]+", text.casefold())}


def area_for(areas: set[str], restricted: set[str], decision: dict) -> str:
    """The area a decision falls in: the one it names, else one whose name's words all appear in it. A restricted area
    wins over any other, then the most specific: `schema changes` matches "change the forms table schema". An area named
    that isn't on record is refused, so a slip in naming can't switch off a restriction."""
    named = " ".join(str(decision.get("area") or "").casefold().split())
    if named:
        if named not in areas:
            raise ValueError(f"no area {named!r} in Owners.md; areas are {', '.join(sorted(areas)) or 'none'} "
                             "(leave area out to match by the decision's words)")
        return named
    text = _words(f"{decision.get('decision', '')} {decision.get('type', '')}")
    matches = [area for area in areas if _words(area) and _words(area) <= text]
    return max(matches, key=lambda area: (area in restricted, len(_words(area))), default="")


def sort(config: Config, role: str, decisions: list[dict]) -> list[Verdict]:
    """Each decision's verdict. The craft or findable: decide and cite. Someone's and reversible: decide and flag, unless
    someone lowered the area's room to act to `ask`. Someone's and hard to undo, or in a restricted area: ask early, with a
    recommendation and a default so a busy person can answer with a word."""
    definition = roles.load(config, role)
    types = {entry["type"] for entry in definition.decisions} | {"craft"}
    rows, restricted = owners.load(config), owners.restricted(config)
    # An area someone lowered to `ask` holds the role to asking even what it could undo; every area starts at `ask`
    # before any consult, which alone doesn't mean anyone wants to be asked.
    lowered = {entry["area"] for entry in consults.autonomy(config)
               if entry["rung"] == "ask" and any(event["kind"] == "lower" for event in entry["events"])} \
        if config.edition == "enterprise" else set()
    out = []
    for raw in decisions:
        text = " ".join(str(raw.get("decision", "")).split())
        kind = str(raw.get("type", "craft"))
        if not text:
            raise ValueError("each decision needs its words: what is being decided")
        if kind not in types:
            raise ValueError(f"{text!r}: type {kind!r}; the template's decision types are {', '.join(sorted(types))}")
        area = area_for({row.area for row in rows} | restricted, restricted, raw)
        row = next((row for row in rows if row.area == area), None)
        verdict = Verdict(text, kind, "decide", area=area, recommendation=str(raw.get("recommendation", "")),
                          default=str(raw.get("default", "")))
        if row:
            verdict.person, verdict.name = row.owner, row.owner_name
        reversible, findable = bool(raw.get("reversible", True)), bool(raw.get("findable", False))
        if kind == "craft":   # the role's own call however its area is owned: naming, structure, tests
            verdict.area = verdict.person = verdict.name = ""
            verdict.reason = "the craft decides it; cite the note or code"
        elif area in restricted:   # with or without an owner on record: restricted work never goes ahead unasked
            verdict.verdict = "ask"
            verdict.reason = (f"{area} is restricted: always ask {row.owner_name}" + (f" ({row.source})" if row.source else "")
                              if row else f"{area} is restricted and Owners.md names nobody for it: ask the organisation's owner")
        elif row is None and kind != "craft":
            verdict.verdict, verdict.reason = "flag", "a decision of a kind people own, with no owner on record: do it, say so, and say who you would have asked"
        elif row is None or findable:
            verdict.reason = "the craft decides it; cite the note or code" if row is None else "findable: cite where it is answered"
        elif not reversible:
            verdict.verdict, verdict.reason = "ask", f"{row.owner_name}'s call in {row.area}, and hard to undo"
        elif row.area in lowered:
            verdict.verdict, verdict.reason = "ask", f"{row.owner_name}'s call in {row.area}, and the room to act there was lowered to ask"
        else:
            verdict.verdict, verdict.reason = "flag", f"{row.owner_name}'s call in {row.area}, reversible: do it and say so on the work"
        if verdict.verdict == "ask" and not (verdict.recommendation and verdict.default):
            raise ValueError(f"{text!r} is an ask: give a recommendation and a default, so it can be answered with a word")
        out.append(verdict)
    return out


def save(config: Config, role: str, work: str, verdicts: list[Verdict]) -> None:
    """The plan's decisions as sorted, kept with the work, so what it knew and chose can be read back."""
    if not work:
        return
    path = screen.folder(config, role) / "plans" / (re.sub(r"[^A-Za-z0-9._-]+", "-", work).strip("-") + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps({"work": work, "decisions": [asdict(v) for v in verdicts]}, indent=2) + "\n")
