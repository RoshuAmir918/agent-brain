"""The runtime-independent specification for recording conversations and routine runs."""
from . import access_log, loops, matters, needs, tasks
from .config import Config
from .ledger import NIGHT_STAGES, OUTCOMES

TRANSCRIPT_CAP = 40000
RECORD_SCHEMA = {
    "type": "object",
    "required": ["headline", "outcome"],
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string", "maxLength": 90},
        "outcome": {"enum": list(OUTCOMES)},
        "touched": {"type": ["array", "null"], "items": {"type": "string"}},
        "needsYou": {"type": ["string", "null"], "maxLength": 160},
        "next": {"type": ["string", "null"], "maxLength": 120},
        "loops": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["kind", "text", "with", "due", "if"], "additionalProperties": False,
            "properties": {"kind": {"enum": ["promised", "owed", "waiting", "when"]},
                           "text": {"type": "string", "maxLength": 160},
                           "with": {"type": ["string", "null"]}, "due": {"type": ["string", "null"]},
                           "if": {"type": ["string", "null"], "maxLength": 160}}}},
        "closed": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["loop", "outcome", "why"], "additionalProperties": False,
            "properties": {"loop": {"type": "string"}, "outcome": {"enum": ["done", "dropped"]},
                           "why": {"type": "string", "maxLength": 120}}}},
        "resolved": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "ready": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "workingTogether": {"type": ["array", "null"], "maxItems": 3, "items": {"type": "string", "maxLength": 200}},
        "noted": {"type": ["array", "null"], "maxItems": 5, "items": {"type": "string", "maxLength": 240}},
        "about": {"type": ["array", "null"], "maxItems": 2, "items": {
            "type": "object", "required": ["name", "now", "soon"], "additionalProperties": False,
            "properties": {"name": {"type": "string", "maxLength": 60},
                           "now": {"type": ["string", "null"], "maxLength": 140},
                           "soon": {"type": ["string", "null"], "maxLength": 140}}}},
        "access": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["subject", "asked", "outcome", "rule"], "additionalProperties": False,
            "properties": {"subject": {"type": "string"}, "asked": {"type": "string", "maxLength": 120},
                           "outcome": {"enum": list(access_log.OUTCOMES)},
                           "rule": {"type": ["string", "null"]}}}},
    },
}
ROUTINE_SCHEMA = {**RECORD_SCHEMA, "properties": {key: RECORD_SCHEMA["properties"][key] for key in ("headline", "outcome", "next")}}
SYSTEM = ("You are the agent whose session this was, keeping your own records. Answer only with the JSON object "
          "the schema describes.")
CONVERSATION = (
    "The conversation above has gone quiet. Record what you are holding for your owner.\n"
    "- headline: what happened, past tense, one line under 90 characters.\n"
    "- outcome: done (finished), progress (moved forward), waiting (on someone), blocked, failed, or nothing "
    "(nothing worth a line: small talk, a test, a question answered in passing).\n"
    "- touched: exact titles of vault notes you worked on, if any.\n"
    "- needsYou: a decision only your owner can make, phrased as a question, or null. Not a status update.\n"
    "- next: your next step, or null.\n"
    "- loops: each new thing you promised someone, an answer or relay you now owe someone, or something you now wait "
    "on from someone. One plain line each, under 160 characters. `with` is the person or intellect by name, someone "
    "who asked you or whom you asked; your owner only for a promise to them (what you wait on from your owner is needsYou, not a loop) and never "
    "a role, a team or a system. `due` is any date or deadline that was named, always, including an expiry or a "
    "\"proceeds unless declined by\" date (YYYY-MM-DD or YYYY-MM-DD HH:MM); it is what wakes you in time. A `when` "
    "loop is something to do once a date comes or something happens (\"when Sazed replies, tell me\", \"remind me "
    "Friday\"): `text` is what to do, `if` the condition when there is one, `due` the date when there is one, at "
    "least one of them; `if` is null for every other kind. One loop "
    "per thing: two waits on the same person about the same matter are one loop. Not work a task carries: the open "
    "tasks listed below already hold their follow-up, including a handoff's report. Not work at all: something you "
    "will build, configure, research or prepare is a task, and a promise is only a reply, a relay or a check you owe. "
    "Not a standing direction about how to work, which is a Working together line. Not something done or answered "
    "in this conversation.\n"
    "- closed: open loops listed below that this conversation finished or that no longer apply, each with `loop` "
    "(its id or exact text), `outcome` (done only when this conversation shows it happened; dropped when it was "
    "cancelled, made moot, or the person changed their mind) and `why` (under 120 characters, what happened, in plain "
    "words).\n"
    "- resolved: asks listed below that this conversation answered or made moot, and confirms your owner confirmed or "
    "corrected (a correction is also recorded where it belongs); use their ids or exact texts.\n"
    "- ready: owed loops listed below whose answer your owner gave here, so you can pass it on; use their ids or exact texts.\n"
    "- access: list each time someone other than the owner asked for something a vault subject covers, and what you "
    "did; leave empty for the owner and small talk. Use the vault's subject, asked in a few words without sensitive "
    "specifics (at most 120 characters), the outcome, and a rule id from Disclosure.md or null.\n"
    "- workingTogether: only when your owner told you how they want you to talk to them or work with them from now on: "
    "style, format, length, tone, what to leave alone, how to approach their work or their projects. Each one "
    "instruction, in their sense, under 200 characters (\"No em dashes; a few sentences unless I ask for detail\", "
    "\"Build features as views into the brain, not new frameworks\"). Not a request for this one reply. Empty for "
    "anyone but your owner.\n"
    "- noted: new facts or plans the person stated that matter beyond today: a trip, a date, a decision, a birthday, "
    "something about a person in their life. Each one plain line with the date when they gave one (\"Flying to Madison "
    "at 5 am on October 29 to visit Faisal; Hope is there too\"). Not what is already in your context, not what you did.\n"
    "- about: what in your owner's life this conversation was about, at most two: a trip, a move, a visit, an "
    "application, an event, a purchase, something with a person in their life. Each `name` is the exact title of a "
    "matter listed below, or a short new name for one (\"Lisbon trip\"); `now` is where it stands after this "
    "conversation in one line (\"flights booked; still need a place in Alfama\"), or null when the conversation did "
    "not change that; `soon` is the next date in it that anyone named, as `YYYY-MM-DD · what happens` (\"2026-12-03 · "
    "Theo lands\"), always when one was named, even if it is also a noted line; null only when no date came up. Not a "
    "responsibility, a task or your own machinery; empty for small talk, a test or a question "
    "answered in passing.\n"
    "Record what you did, never what you did not do.")
ROUTINE = (
    "The routine run above has ended. Record it for the routine's ledger.\n"
    "- headline: what the run found or did, under 90 characters; for a quiet run, name what it weighed "
    "and why that lost (\"weighed Contracts outline, worker owns it; renal deck, made 11-06\"), not that it checked.\n"
    "- outcome: done, progress, waiting, blocked, failed (the run could not do its job), or nothing "
    "(it ran and there was nothing to act on).\n"
    "- next: the next step it leaves, or null.")

# A record of nothing is written only when it captures something: any field but these.
UNCAPTURED = ("headline", "outcome", "touched", "next")


def held(config: Config) -> str:
    """What the conversation record is shown it already holds for the owner, appended to CONVERSATION: open asks and
    confirms, open loops and open tasks, each with its id."""
    asks = [f"- {item.id} · {item.kind} · {item.text[:160]}" for item in needs.items(config, config.today())
            if item.kind in ("ask", "confirm")][:20]
    open_loops = [f"- {item.id} · {item.kind} · {item.text}" for item in loops.open_loops(config)[:20]]
    from .vault import Vault
    shown = [f"- {title}" for title, _ in matters.listed(Vault(config.vault_path), config.today())]
    open_tasks = [f"- {task.tid} · {task.title}{' (handed off)' if task.handoff else ''}"
                  for task in tasks.all_tasks(config) if tasks.is_open(task)][:30]
    sections = (("Already waiting on your owner; leave needsYou null if the conversation only raised one of these again, "
                 "in any wording; list answered ones in resolved:", asks),
                ("Open loops; close these when finished, do not add them again:", open_loops),
                ("Open tasks; each carries its own follow-up, so add no loop for their work or their reports:", open_tasks),
                ("Matters in your owner's life; name these exactly in about:", shown))
    return "".join(f"\n{heading}\n" + "\n".join(lines) for heading, lines in sections if lines)
