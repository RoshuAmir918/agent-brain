---
name: brain-sleep2
description: Nightly audit, reflections and morning priming
version: 1.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# Sleep 2: dream

The thinking half. Runs on Sleep 1's output, never on raw conversation. Record the HEAD SHA you audited in the Sleep Log.

## Procedure
1. **Retrieval audit.** From yesterday's Use Log rows (explicit, judged and observed), note the tier each credited note was in. A cold citation on a current-work query is a defect on the second recurrence: set the note's `index_line` or add a wikilink from the hub the query touched. For each `MISS` row, search fresh; if a note existed, report the miss and apply the same fix. Three numbers to the Sleep Log: credited notes, cold credits, misses.
2. **Consolidation audit.** Re-judge three of Sleep 1's gate decisions at random with fresh context. A disagreement is one morning line with both judgments; never overrule Sleep 1.
3. **Retrieval checks.** Follow `agent/Retrieval Checks.md`: three of the twelve questions nightly, all twelve on the first run after Sunday. Grade content and appropriate uncertainty, not wording.
4. **Reflections, weekly.** For each hub with three or more new dated entries since its last reflection, write at most one to `agent/Reflections/YYYY-MM-DD <subject>.md` with `kind: reflection`, `sources:` and `confidence: tentative`. At most three a week. A dream enters memory only through a reflection that cites it.
5. **Priming.** If `hermes/pending-migration.md` exists, make its first line the first Primed item: "engine migration pending; see the file." Then write `agent/Primed/YYYY-MM-DD.md`: the five things most likely to come up today, each with the notes to load and the state in a sentence, plus nudges. A nudge is a warm or cold open task reached by one-hop spread from the notes hot this week, not superseded by a recorded decision, and not nudged since its last reactivation. Phrase it: "related to what you're on, untouched since <date>: still want this?"
6. **Routine reconciliation.** Run `hermes cron list` from the terminal and compare it with the cron table in `hermes/README.md`: every listed job present, enabled, and at its listed time. A missing, paused or shifted job is one morning line. Change nothing here; schedules change only in conversation with Human.
7. **Proposals.** Only when the audit shows a pattern across three or more nights. Write it for the morning; never edit skills, Triggers' rules or the Memory Spec.
8. Commit `sleep2(<date>): <audit numbers, fixes, reflections, primed>` and push.

## Boundaries
Writes only `index_line`, hub wikilinks, Reflections, Primed, Sleep Log. Never hub or profile content, Tasks, Triggers, `sources/`. Testers for the retrieval checks never see credential or code notes.
