---
name: {prefix}evening
description: Decide what has gone quiet, so nobody has to sweep a list
version: 2.6.0
metadata:
  runtime:
    tags: [routine, day]
    category: brain
    context: [start, current, tasks, upcoming]
---
# Evening

One job: decide what has gone quiet, so {human_name} never has to sweep a list of things they once mentioned and nothing waits on them that does not have to. Watch owns what is new today; this owns what is old.

## Procedure
1. **Quiet tasks.** `brain stale-tasks`. Decide each one yourself:
   - `cancelled` when the evidence says it no longer applies: superseded by a later decision, done another way, an event or opportunity that has passed, or {human_name} said to drop it. An overdue obligation (a bill, a promise, a filing) has not stopped applying.
   - `set_aside` when it has only gone quiet and nothing is at stake in waiting: no money, no promise to someone else, no live deadline, nothing {human_name} called important. Reversible; set it back when it comes up again.
   - an ask (recall's Needs you rules) only when something is at stake and only {human_name} can decide it.
   - a `yours` task, {human_name}'s own work, quiet for two weeks: never dropped or set aside by you. One ask, sourced to the task: "Still on your plate: <title>. Still working on it, done, or drop it?" Their answer decides it (still on: leave it `yours`; done: completed; drop: cancelled), and the Reviewed line restarts its two weeks.
   - left alone when it waits on someone else or on a date still ahead.
   Record each decision with `brain task T-nnn set --reviewed "<set aside | dropped | asked>: <why, in one clause>"` (with `--state set_aside` to set one aside, or `brain task T-nnn drop --why …` to drop it).
2. **Quiet asks, and too many.** `brain needs-you --stale` lists asks unanswered for three days (a task's ask waits with its task and is never listed), and `brain needs-you --overflow` the lowest-ranked asks past the five {human_name} sees. Decide each: when it is reversible, take the answer {human_name} would most likely give, act on it, and replace the ask with a `confirm` saying what you did; when it is not, or it was never theirs, remove it and write one dated line on its source note saying why. Never re-ask one.
3. **Handled in the world.** For each open ask in `brain needs-you --json`, check whether a channel or a task changing state shows it answered outside any conversation. For an outcome that can be checked (a share or invitation accepted, a payment made, an email sent, a booking confirmed, a form filed), check it once, through the registry row that would show it (`{agent_dir}/Channels.md`, opened the way the row says: a synced mail folder, a command, a connected account) or the task's own evidence. Done: `brain needs-you --resolve <id> --because "<the evidence>"`, and when {human_name} did it themselves, one `morning:` line on the source task saying "looks like you handled <it>". Not checkable, or still not done: leave it.
4. **What validate names.** `brain validate`. Fix each Needs you problem it names:
   - a bullet about the brain itself (a migration, a setup or engine change, a skill): resolve it, with a dated Sleep Log line saying what it recorded;
   - a task waiting on {human_name} with no linked Needs you ask, or a question over its cap: write its question and why as recall says. A proposed improvement keeps its Ask and Why lines.
   Any other problem it names is not this routine's: give the count in the account.
5. **Stuck loops.** `brain loops --stale` lists loops two days or more past due: watch has acted on or nudged each one and nothing moved. None stays open past tonight; decide each:
   - done, when the evidence shows it happened: `brain loops --close <id> --outcome done --because "<what shows it>"`;
   - a task, when it is work rather than a reply or a relay: find or write the task with the loop's words, hand it off if it is yours to do, then close the loop `--outcome dropped --because "carried by T-nnn"`;
   - Working together, when it is a standing instruction about how to work: add it to the Working together section of the owner profile with its date, then close the loop `--outcome dropped --because "a standing direction, kept in Working together"`; when it is about how to do a kind of work rather than how to deal with them, learn it in its field instead (`brain learn add --field <Field> --claim "<how to act>" --source "<their words, or where you learned it>" [--rule]`) and close the loop the same way, `--because "a lesson, kept in Learned"`;
   - dropped, when it no longer applies or a nudge went unanswered and nothing is at stake: `--outcome dropped --because "<why>"`. When something is at stake for {human_name} (money, a promise they made, a date), say so in one line in tomorrow's briefing instead of dropping it silently.
6. **What went wrong today.** Read today's rows in `{agent_dir}/Answered/`: an idea or offer {human_name} declined, a draft they called not this, a loop dropped because you mishandled it. For each, ask what you would do differently next time, as a rule for that kind of work. When there is one, learn it once, in its field: `brain learn add --field <Field> --claim "<how to act next time>" --source "reflection on <T-nnn, or what happened>" --candidate`. At most one line per outcome; most need none.
7. `brain commit -m "evening(<date>)" --push` if anything changed. The run's account gives the counts by kind.

## Boundaries
Only task state fields and Needs you. No hubs, profiles, CURRENT threads or memory structure. No messages: what is decided here shows in the briefing and the app.
