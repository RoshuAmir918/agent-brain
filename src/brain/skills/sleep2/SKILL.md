---
name: {prefix}sleep2
description: Nightly retrieval repairs and weekly reflections
version: 3.2.0
metadata:
  runtime:
    tags: [memory, night]
    category: brain
    context: [start, current, directory]
---
# Sleep 2

Repairs what made memory hard to find yesterday, and once a week writes reflections. Runs on Sleep 1's output, never on raw conversation. Today's brief is the prime routine's, later in the morning.

## Procedure
1. **Retrieval repairs.** From yesterday's Use Log rows, note the tier each credited note was in. A cold citation on a current-work query is a defect on its second recurrence: set the note's `index_line` or add a wikilink from the hub the query touched. For each `MISS` row, search fresh; if a note existed, apply the same fix.
2. **Reflections, weekly** (first run after Sunday). For each hub with three or more new dated entries since its last reflection, write at most one to `{agent_dir}/Reflections/YYYY-MM-DD <subject>.md` with `kind: reflection`, `sources:` and `confidence: tentative`. At most three a week. A dream enters memory only through a reflection that cites it.
3. **Portrait, weekly** (first run after Sunday), personal and organisation alike. `brain portrait coverage` and `brain reception summary`. In `{human_portrait}`: re-rank each section by what this week's evidence and reception showed; move to `History.md` beside it, dated with its id, every `guess` whose last evidence is more than thirty days old (`brain validate` names them) and every After line whose goal was reached or set aside; keep each section within eight lines and the note within 700 words. One Sleep Log line: which of the top three After lines got no explore run and no offer this week, and the week's hit rate.
4. **Schedule check.** `brain schedule list` against `{runtime_dir}/schedule.toml`: a missing, paused or shifted job is one Sleep Log line. Change nothing.
5. **Sleep Log.** One entry, under 100 words: credited notes, cold credits, misses, fixes made, reflections written, drift found. `brain commit -m "sleep2(<date>)" --push`.

## Boundaries
Writes only `index_line`, hub wikilinks, Reflections and the Sleep Log. Never hub or profile content, Tasks, Triggers, Primed or `{sources_dir}/`.
