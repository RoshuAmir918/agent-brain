---
name: {prefix}sleep2
description: Nightly retrieval repairs and weekly reflections
version: 3.5.0
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
3. **Portrait, weekly** (first run after Sunday), personal and organisation alike, by `{prefix}portrait`. `brain portrait coverage` and `brain reception summary`. In each facet (in `{human_portrait}` itself while there are none): re-rank each section by what this week's evidence and reception showed, set each facet's `rank` (1 for the area most central to {human_name} now), retire to History what those rules say goes, and keep to their caps. One Sleep Log line: which of the top three After lines got no explore run and no offer this week, and the week's hit rate.
4. **Portrait from evidence, nightly, for an organisation.** (A person's runs in the People review.) The evidence pass by `{prefix}portrait`, for at most three facets a night: what the sources found since each facet's last pass (corrections from reviews, projects and effort from plans) becomes lines in the facet it is about, Engineering and Direction first among the starting ones.
5. **Schedule check.** `brain schedule list` against `{runtime_dir}/schedule.toml`: a missing, paused or shifted job is one Sleep Log line. Change nothing.
6. **Sleep Log.** One entry, under 100 words: credited notes, cold credits, misses, fixes made, reflections written, facets judged, drift found. `brain commit -m "sleep2(<date>)" --push`.

## Boundaries
Writes only `index_line`, hub wikilinks, Reflections, the portrait's facets and the Sleep Log. Never hub or profile content, Tasks, Triggers, Primed or `{sources_dir}/`.
