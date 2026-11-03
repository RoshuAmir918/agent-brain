---
name: {prefix}prime
description: Write today for the owner, after the night and the People review
version: 1.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# Prime

One job: overwrite `{agent_dir}/Primed.md` with what today is for {human_name}. It loads in every session today, so it is short, true and in order of importance. Runs after the People review, so the people in it are current.

## Procedure
1. Read CURRENT, the open tasks, Upcoming in the session context, yesterday's digest in `Conversations/Digests/`, and the People histories touched yesterday. If `{hermes_dir}/pending-migration.md` exists, its first line is the first line of Primed: "engine migration pending; see the file."
2. Write `{agent_dir}/Primed.md` (frontmatter `kind: primed`, `tier: hot`; yesterday's content is gone), at most 300 words, most important first, each part dropped when empty, nothing invented:
   - **Where and with whom.** Where {human_name} is today and who they are with, with the date it was said. "At home, nothing noted" is a valid line.
   - **Today.** What they said they would do today, and anything due today or tomorrow that Upcoming cannot see (a commitment in prose with no date, a recurring thing). Upcoming carries the dated items; never copy them.
   - **People this week.** At most three, one line each with its source.
   - **Waiting for them.** Anything under `{agent_dir}/Prepared/` made in the last seven days that they have not raised, one line each, once.
   - **Likely to come up.** The three notes most likely needed today, each with the note to load and its state in a sentence.
   - **Nudge.** At most one: a quiet open task reached from what is hot this week, not set aside, not `yours` (the evening asks about those), and not nudged since it last went quiet ("related to what you're on, untouched since <date>: still want this?"); or a goal in `{human_goals}` with no dated evidence for sixty days, once ("you said <goal> on <date>; nothing since. Still a goal?"). A goal silent for thirty more days after its nudge moves under Set aside with today's date; that is the one edit to Goals this routine makes.
3. Nothing about the machinery: no audits, counts, stages, versions, commits or routine drift. Those are the Sleep Log's.
4. `brain compile-context --write`, commit `prime(<date>)` with the files you changed, and push.

## Boundaries
Writes only Primed, the compiled context, and a dormant goal's move to Set aside. Never hubs, profiles, Tasks, Triggers or CURRENT.
