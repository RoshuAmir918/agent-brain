# Knowledge

*Draft, 2026-11-07. How an organisation's intellect knows who to ask, what matters and what was done before, without new machinery for each new case. Read with `docs/engine.md` ("Consults") and `docs/image.md` section 4, the evidence contract this shares with the portrait; Demiurge's `docs/judgment.md` and `docs/vision.md` set the rules this serves.*

## 1. The principle

The intellect should adapt to a case nobody planned for the way a good new hire does: by remembering what happened, noticing what repeats, and thinking at the moment of choice. Not by someone adding a rule, a table or a new kind of record each time a new situation appears.

Consults grew the other way. Each feature added its own line format, its own parser and its own constants: decisions, overrules, delegations, mutes and autonomy lines in area notes; evidence kinds and their weights in code; a CLI flag per source. Every nuance asked for another construct. The next ones were already queued: entities so that "this table" can beat "migrations", choice kinds so that schema changes count for more, packs so that customer work can do what code work does. That road never ends.

The engine already has a better shape for this, because the personal side is built on it: notes with activation and decay, night passes that consolidate the day into memory, Taste lines learned from corrections, retrieval that loads what is relevant. This document moves the enterprise knowledge onto that shape.

**Code holds only what must never bend. Memory holds everything the intellect learns. Judgment happens at the choice, and its reasons are kept so they can be corrected.**

## 2. What stays in code

A short list, each because getting it wrong once is unacceptable or because it is the substrate everything else stands on:

1. **Attribution.** A decision is a person's words, never the intellect's inference (judgment rule 1). Who said what, when, and where, is recorded exactly.
2. **The restricted floor.** Production, money and what the organisation lists stay at Ask, on a person's word, whatever memory suggests (rule 6).
3. **Restraint.** Budgets, working hours, absences and stop-asking: what a person asked of the intellect about being asked.
4. **No profiles of people.** Knowledge is filed by what it is about, never kept as a dossier on a person (rule 8). A view of a person is computed when asked for, and not stored.
5. **One format for what happened.** Everything the intellect observes is written once, as evidence rows in the shared format (section 3).
6. **The loop.** Every consult, act and piece of delivered work ends in an outcome that is written back as evidence.

Everything else in today's consult code is a heuristic that memory and judgment should be able to outgrow: candidate weights, decay constants, the evidence kinds, the climb threshold, word-overlap relevance. They stay as defaults until what replaces them is measured to do better (section 8).

## 3. One memory of what happened

The intellect's knowledge of the organisation is a stream of evidence rows, in the engine's one evidence contract (`docs/image.md` section 4), the same rows the portrait's sources write: a date, a verb, the fact in one plain sentence, whose it is, hints about what it concerns (a portrait facet, or here a knowledge area), and a pointer back to the original. Verbs are an open set; each source declares the ones it emits. Consult rows carry the outcome as their verb (`asked`, `confirmed`, `changed`, `redirected`, `overruled`, `acted`, `reversed` and so on) and add optional trailing fields such as `id=<consult id>`. Everything is read back with `brain evidence [--source] [--hint] [--since] [--json]`, so recall, routing and the image filter the same rows the same way.

What is written, from where:

| Source | Example facts |
| --- | --- |
| Consults | asked Kim whether to backfill `analytics_events`; Kim confirmed; Noah redirected to Kim; Ana overruled Kim's decision of Oct 7 |
| Acts | acted on its own in data: rotated the export logs; Kim reversed it |
| Work and corrections | merged #412, a migration adding a column to `forms`, after 9 review comments; Kim rewrote the customer reply's delivery date |
| Code history | Kim created `analytics_events` in #391; Noah approved 14 migrations this quarter |
| Mail and chat | "check with Noah" on vendor renewals, in the ops inbox |
| People's own words | Kim: I'm out from the 20th to the 27th; Noah: I decide release timing |

Two consequences matter more than the format:

- **No registry of things.** A table, an account, a vendor or a process is a name in a fact and a link, not an object someone has to declare. "`analytics_events`" is known because facts mention it. Nothing new has to be modelled before the intellect can know about it.
- **No fixed vocabulary of kinds.** "Migration", "pricing promise" and "vendor change" are words in facts and verbs sources chose, not a schema. A case nobody anticipated is already representable on the day it first happens.

Areas stay, but loosely: a hint for filing and for reading, not a schema. A fact can concern several areas, and an area is whatever name people and history give it.

## 4. Recall at the moment of choice

Before a consequential choice, the intellect asks its memory what happened around it, using the names in the choice itself: the table, the file, the account, the kind of change, the area. Recall returns the most relevant facts, newest and most specific first, with their outcomes, and the lessons (section 5) that cover them.

Then it reasons, in the open:

> This changes `analytics_events`. Kim created it a month ago (#391) and wrote to it last week (#410). Schema changes in data were corrected in three of the last four PRs, so this is not one to do quietly. Noah reviews most migrations. What the migration should mean depends on Kim's intent: ask Kim; Noah next if she redirects, or for the mechanics.

That paragraph is the whole of what entities, choice kinds, stakes and precedent were going to be: **specificity** (the freshest and most specific fact about this table outweighs the general fact about migrations), **stakes** (a learned lesson that this kind of change here goes wrong), **precedent** (what was done before, and how it was corrected), and **routing**. None of it needs a dedicated mechanism; it needs recall that surfaces the right facts and a model that weighs them.

The reasons are not thrown away. They go into the consult (`--why`), the act, or the choice's note, and they become evidence themselves. When a redirect or overrule shows the reasoning was wrong, the facts it rested on and the conclusion it drew are both on record, and the next consolidation learns from the gap.

## 5. Consolidation: lessons

Recall alone repeats raw history. A brain also generalises. Each night, after the existing passes, a consolidation pass reads the day's evidence beside what was already learned and writes **lessons**: short statements of what tends to be true, with the facts behind them, a confidence, and the date they were last reinforced.

- "Schema changes in data get corrected or reversed more often than anything else there; ask before making one." (from outcomes and corrections)
- "Questions about `analytics_events` go to Kim; she made it and answers within hours." (from consult outcomes and code history)
- "Customer replies that name a delivery date are edited by Ana before they go." (from corrections)
- "Release timing is Noah's; he said so, and his decisions there stand." (from people's words and outcomes)

Lessons are memory notes like any other: they load when relevant, decay when nothing reinforces them, and are revised when evidence contradicts them. They are the intellect's understanding, not a person's words: a lesson is never cited as a decision, and one about direction or about a person's reliability is not written. Where a lesson would change behaviour sharply (it would stop asking somewhere, or start acting on its own), it goes to the operator as a confirm first, as guesses do in the portrait.

Stakes, who knows what, precedent and preferences all arrive this way. A new kind of case gets a lesson once its facts repeat, without anyone naming it in advance.

## 6. Autonomy, routing and the record, rebuilt on this

What is already built keeps its commands and its tests; what changes is where its knowledge comes from.

- **The record answers first** (stage 2) becomes recall over decisions and lessons together: a standing decision or a confident lesson that covers the question is cited instead of asking.
- **Routing** becomes recall plus judgment. `brain consult candidates` stays as one view of the evidence, with its weights as fallback defaults, but the choice and its reasons are the model's, written in `--why`.
- **Earned autonomy** (stage 3) is derived per lesson-sized scope rather than only per area: room to act on refactors in data, not on schema changes in data, follows from outcomes on each without either being declared. The restricted floor and people's ceilings and lowers stay exactly as they are.
- **The record lines** (decisions, overrules, delegations, mutes, autonomy lines) remain people's words with their sources. Over time they are written as evidence rows too, so there is one reader, not five parsers.
- **Seeders** become sources in the shared format: the GitHub and mail seeders declare their verbs and write evidence rows; new sources (the tracker, shared channels, meeting notes) are a source each, with no change to routing.

## 7. People, without profiles

"What does Kim know?" is a question memory can answer well, and that is exactly why nothing about Kim is stored. A view of a person is computed when someone needs it: the areas and things the facts tie her to, her decisions that stand, the lessons that name her, her stated hours. It is as rich as the record and never stale, and it disappears when the question is answered. What a person says about themselves (hours, absences, what they own, how they like to be asked) is kept as their own words, in the owner map.

## 8. Building it, and knowing it works

The order keeps everything working at every step:

1. **Write consult events as evidence.** Consult outcomes, acts, redirects, overrules and seeded history are also written as evidence rows in the shared format. Nothing reads them yet.
2. **Recall.** A recall command over evidence and memory for the names in a choice, newest and most specific first, with outcomes. The skill asks for it before a consequential choice and writes its reasons.
3. **Lessons.** The consolidation pass, its decay and contradiction handling, and the operator confirm for behaviour-changing lessons.
4. **Cut over.** Stage 2 and 3 and routing read recall and lessons first, with today's mechanics as the fallback.
5. **Retire what lost.** Weights, constants and parsers that measurably add nothing are removed.

Whether it works is measured, not assumed. `agent-brain-evals` gets routing and stakes cases built like the example above: a choice where the general expert is the wrong person to ask, a change whose risk only history shows, a new kind of case seen three times and then a fourth. Each step ships only if it does at least as well as what it replaces, and the measures in `docs/vision.md` section 7 (correction burden, redirect rate, record coverage, autonomy without reversals) are the ones that decide.

## 9. Open

- **Recall quality is the crux.** Everything above rests on surfacing the right twenty facts out of thousands. Retrieval today is word-based; names of tables and accounts help, but whether embeddings or the activation model are needed is for the evals to say.
- **Lessons can overgeneralise.** One loud correction should not become a rule. How much evidence a lesson needs, and how fast contradiction retires one, need numbers from real use.
- **Cost.** A nightly consolidation over an organisation's evidence is a model call on a growing input; it needs the same budget discipline as the other night passes.
