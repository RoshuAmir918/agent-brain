---
name: {prefix}explore
description: Check own channels, then pursue one useful thread
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day, research]
    category: brain
---
# Explore

The proactive pass. Twice-hourly polling is gone; this runs every two hours while {human_name} is awake and does two things: handle {agent_name}'s own inbound, then pick one thread from what {human_name} and {agent_name} have been talking about and push it forward.

## Procedure
1. **Inbound.** Before replying to anyone, run `brain session-context --platform email --user-id <sender address>` (in chat, `--platform demiurge --user-id <handle> --user-id-alt <permanent id>`) and follow the block it prints: it names the person, their profile and the disclosure rows that apply, or says they are a stranger.  Read {agent_name}'s own mailbox and any chat threads {agent_name} is part of, since the last run. Reply where a reply is {agent_name}'s to give (per SOUL: ask {human_name} for context rather than guess when a message is really for them). Record anything durable on the owning task or note.
2. **Choose one thread.** Candidates, in order: an open question or "would be useful to know" in yesterday's sessions; an open task whose next step {agent_name} can advance without approval; a dated item in CURRENT coming up within a week; a hub section {human_name} touched this week with a gap in it. Skip anything in `{agent_dir}/Explorations/YYYY-MM.md` from the last three days unless new evidence arrived. Pick the one where two hours of effort most plausibly produces something {human_name} would act on.
3. **Do the work.** Research the question with real sources, check the status (a PR, an order, a deadline, a reply that was expected), draft the thing they said they wanted, find the paper or the person or the price. Depth over breadth: one thread, done properly.
4. **File it.** Through the surprise gate: a fact goes in the owning hub, profile or task with its source and date; a draft goes beside the task it serves; a finding worth their attention gets a `morning:` tag on the owning task so the briefing carries it.
5. **Message {human_name} only if** it is time-sensitive, or clearly worth interrupting for (a decision they can make now, a problem found, a result they asked about). Otherwise it waits for the morning. At most two messages a day from this routine.
6. **Ledger.** Overwrite `$HERMES_HOME/records/explore/state.json` with inbound checkpoints and the thread in progress, then append one line, at most 25 words, to `{agent_dir}/Explorations/YYYY-MM.md`: `date time | thread | what was done | outcome (filed / messaged / nothing found)`. A quiet pass is the row `date time | inbound | nothing new | nothing found` and nothing more. The row records what was done, never what was not done, checked or changed; the Boundaries below already say that.

## Boundaries
No sends to anyone but {human_name}, except replies to mail already addressed to {agent_name}. No spending, sign-ups, new accounts or credentials. No edits to `{sources_dir}/`, SOUL, skills, Triggers' rules or the Memory Spec. Never invent a finding to have something to report; a quiet run is fine. Do not re-ask a question {human_name} has not answered.
