---
name: {prefix}sleep2
description: Nightly audit, reflections and morning priming
version: 2.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# Sleep 2: dream

The thinking half. Runs on Sleep 1's output, never on raw conversation.

## Procedure
1. **Retrieval audit.** From yesterday's Use Log rows (explicit, judged and observed), note the tier each credited note was in. A cold citation on a current-work query is a defect on the second recurrence: set the note's `index_line` or add a wikilink from the hub the query touched. For each `MISS` row, search fresh; if a note existed, report the miss and apply the same fix. Three numbers to the Sleep Log: credited notes, cold credits, misses.
2. **Consolidation audit.** Re-judge three of Sleep 1's gate decisions at random with fresh context. A disagreement is one Sleep Log line with both judgments; never overrule Sleep 1.
3. **Retrieval checks.** Run every check in its own session whose title begins `Evaluation fixture: <id>` and whose first message begins `Evaluation fixture:`; the engine excludes such sessions, and anything under `{agent_dir}/Retrieval Checks/`, from the digest and from every attribution path, so a test answer can never earn memory credit. Follow `{agent_dir}/Retrieval Checks.md`: three of the twelve questions nightly, all twelve on the first run after Sunday. Grade content and appropriate uncertainty, not wording.
4. **Reflections, weekly.** For each hub with three or more new dated entries since its last reflection, write at most one to `{agent_dir}/Reflections/YYYY-MM-DD <subject>.md` with `kind: reflection`, `sources:` and `confidence: tentative`. At most three a week. A dream enters memory only through a reflection that cites it.
5. **Priming: today for {human_name}.** If `{hermes_dir}/pending-migration.md` exists, make its first line the first Primed item: "engine migration pending; see the file." Then overwrite `{agent_dir}/Primed.md` (frontmatter `kind: primed`, `tier: hot`; yesterday's content is gone, the Sleep Log line is its record). It loads in every session today, so under 300 words, in this order, each part dropped when empty and nothing invented:
   - *Where and with whom:* where {human_name} is today and who they are with, from CURRENT, the digest and People histories, with the date it was said. "At home, nothing noted" is a valid line.
   - *Today:* what they said they would do today, and what is due today or tomorrow that the Upcoming scan cannot see (a commitment in prose with no date, a recurring thing). Upcoming already carries the dated items; do not copy them.
   - *People this week:* at most three people with something happening, one line each with its source.
   - *Waiting for them:* anything under `{agent_dir}/Prepared/` made in the last seven days that they have not raised, one line each, once; after that it waits silently.
   - *Likely to come up:* the three notes most likely to be needed today, each with the notes to load and the state in a sentence.
   - *Nudges:* a warm or cold open task, not set aside, reached by one-hop spread from the notes hot this week, not superseded by a recorded decision, and not nudged since its last reactivation. Phrase it: "related to what you're on, untouched since <date>: still want this?" A goal in `{human_goals}` with no dated evidence for sixty days gets the same treatment, once: "you said <goal> on <date>; nothing since. Still a goal?" After thirty more days of silence, move it under Set aside with today's date; it is the one edit to that note the night may make.
   - *Gestures:* one line, `gestures: open` or `gestures: paused until <date>`, carried from the last service pass (step 6); `open` when there has been none.
6. **Service, weekly** (first run after Sunday). From this month's explore ledger rows (`{agent_dir}/Explore/YYYY-MM.md`, the engine's record of each run; an older vault's `Explorations/` rows count too) whose account names `people` or `occasion` and `held`, `messaged` or `delivered`, and the Journal rows that followed each: write three sentences to the Sleep Log, what {agent_name} did for {human_name} unasked this week, how they took it (replied, used it, said nothing), and what it would do differently. If the last two `delivered` rows both met silence, set `gestures: paused until <today + 30 days>` in Primed and keep it there each night until that date; otherwise `open`. One sentence of this goes to the briefing under Noticed only when there is something to say; never the counts. A week with no such rows is one Sleep Log line and no change.
7. **Routine reconciliation.** Run `hermes cron list` from the terminal and compare it with the cron table in `{hermes_dir}/README.md`: every listed job present, enabled, and at its listed time. A missing, paused or shifted job is one Sleep Log line, and one briefing line only when it is the briefing's own job or tonight's. Change nothing here; schedules change only in conversation with {human_name}.
8. **Proposals.** Only when the audit shows a pattern across three or more nights. Write it to the Sleep Log, and as an `ask` in CURRENT's `## Needs you` when it needs a decision; never edit skills, Triggers' rules or the Memory Spec.
9. **Sleep Log.** One entry, under 150 words: the HEAD SHA, the three audit numbers, fixes made, reflections written, checks passed and failed, drift found. Say what was done, not what was not. Commit `sleep2(<date>): <audit numbers, fixes, reflections, primed>` and push.

## Boundaries
Writes only `index_line`, hub wikilinks, Reflections, Primed, Sleep Log, an `ask` in CURRENT's `## Needs you`, and a dormant goal's move to Set aside. Never hub or profile content, Tasks, Triggers, `{sources_dir}/`. Testers for the retrieval checks never see credential or code notes.
