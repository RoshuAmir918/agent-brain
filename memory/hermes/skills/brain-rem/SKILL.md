---
name: brain-rem
description: One nightly dream from distant notes
version: 1.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# REM

Build an edge between notes that never met. It may be strange or useless; it must never leak.

## Procedure
1. Skip if `git log --since=yesterday --grep='rem('` already has today's commit, or if Sleep 1 is still running.
2. Draw two or three random notes, weighted toward warm and cold: hubs, profiles, reflections. At most one may be a person, and only if `people/README.md` lists a roster the owner has approved for dreams; with no roster, draw no people. Never Triggers, Tasks, corrections, Conversations, Sessions, skills or `sources/`.
3. Write `agent/Dreams/YYYY-MM-DD.md`, under 400 words, frontmatter `kind: dream`, `fiction: true`, `tier: cold`, `uses: []`, `ingredients:` listing the draw. Scenes, not arguments. No insight, moral or usefulness. A person is a character only: no invented quotes, no sensitive traits.
4. Append one line to `agent/Dreams/README.md`: `date | ingredients | gist`.
5. Commit `rem(<date>)` and push. Write nothing else.

## Boundaries
Only the owner, conversation with the owner, and Sleep 2's recurrence check may read `Dreams/`. No routine and nothing that composes a message may load a dream. A dream authorizes nothing and becomes a task, trigger or profile line by no path.
