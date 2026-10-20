---
name: brain-explore
description: Check own channels, then pursue one useful thread
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day, research]
    category: brain
---
# Explore

The proactive pass. Twice-hourly polling is gone; this runs every two hours while Human is awake and does two things: handle Agent's own inbound, then pick one thread from what Human and Agent have been talking about and push it forward.

## Procedure
1. **Inbound.** Read Agent's own mailbox and any chat threads Agent is part of, since the last run. Reply where a reply is Agent's to give (per SOUL: ask Human for context rather than guess when a message is really for them). Record anything durable on the owning task or note.
2. **Choose one thread.** Candidates, in order: an open question or "would be useful to know" in yesterday's sessions; an open task whose next step Agent can advance without approval; a dated item in CURRENT coming up within a week; a hub section Human touched this week with a gap in it. Skip anything in `agent/Explorations/YYYY-MM.md` from the last three days unless new evidence arrived. Pick the one where two hours of effort most plausibly produces something Human would act on.
3. **Do the work.** Research the question with real sources, check the status (a PR, an order, a deadline, a reply that was expected), draft the thing they said they wanted, find the paper or the person or the price. Depth over breadth: one thread, done properly.
4. **File it.** Through the surprise gate: a fact goes in the owning hub, profile or task with its source and date; a draft goes beside the task it serves; a finding worth their attention gets a `morning:` tag on the owning task so the briefing carries it.
5. **Message Human only if** it is time-sensitive, or clearly worth interrupting for (a decision they can make now, a problem found, a result they asked about). Otherwise it waits for the morning. At most two messages a day from this routine.
6. **Ledger.** Append one line to `agent/Explorations/YYYY-MM.md`: `date time | thread | what was done | outcome (filed / messaged / nothing found)`. "Nothing found" is a valid outcome; say so and stop.

## Boundaries
No sends to anyone but Human, except replies to mail already addressed to Agent. No spending, sign-ups, new accounts or credentials. No edits to `sources/`, SOUL, skills, Triggers' rules or the Memory Spec. Never invent a finding to have something to report; a quiet run is fine. Do not re-ask a question Human has not answered.
