---
name: {prefix}sleep2
description: Nightly retrieval repairs and weekly reflections
version: 3.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# Sleep 2

Repairs what made memory hard to find yesterday, and once a week writes reflections. Runs on Sleep 1's output, never on raw conversation. Today's brief is the prime routine's, later in the morning.

## Procedure
1. **Retrieval repairs.** From yesterday's Use Log rows, note the tier each credited note was in. A cold citation on a current-work query is a defect on its second recurrence: set the note's `index_line` or add a wikilink from the hub the query touched. For each `MISS` row, search fresh; if a note existed, apply the same fix.
2. **Reflections, weekly** (first run after Sunday). For each hub with three or more new dated entries since its last reflection, write at most one to `{agent_dir}/Reflections/YYYY-MM-DD <subject>.md` with `kind: reflection`, `sources:` and `confidence: tentative`. At most three a week. A dream enters memory only through a reflection that cites it.
3. **Schedule check.** `hermes cron list` against the cron table in `{hermes_dir}/README.md`: a missing, paused or shifted job is one Sleep Log line. Change nothing.
4. **Sleep Log.** One entry, under 100 words: credited notes, cold credits, misses, fixes made, reflections written, drift found. Commit `sleep2(<date>)` and push.

## Boundaries
Writes only `index_line`, hub wikilinks, Reflections and the Sleep Log. Never hub or profile content, Tasks, Triggers, Primed or `{sources_dir}/`.
