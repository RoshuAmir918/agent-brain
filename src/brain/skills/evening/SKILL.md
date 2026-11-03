---
name: {prefix}evening
description: Decide what has gone quiet, so nobody has to sweep a list
version: 2.2.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
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
   Write each decision on the task as `- **Reviewed:** <date> — <set aside | dropped | asked>: <why, in one clause>`.
2. **Quiet asks.** `brain needs-you --stale` lists asks unanswered for two weeks. Decide each: when it is reversible, take the answer {human_name} would most likely give, act on it, and replace the ask with a `confirm` saying what you did; when it is not, or it was never theirs, remove it and write one dated line on its source note saying why. Never re-ask one.
3. **Handled in the world.** For each open ask whose outcome can be checked (a share or invitation accepted, a payment made, an email sent, a booking confirmed, a form filed), check it once, with the command or file that would show it: `tailscale status`, the channel files, `demiurge mail search`, the task's own evidence. Done: `brain needs-you --resolve <id> --because "<the evidence>"`, and when {human_name} did it themselves, one `morning:` line on the source task saying "looks like you handled <it>". Not checkable, or still not done: leave it.
4. Commit `evening(<date>)` if anything changed. The run's account gives the counts and names them ("set aside 3, dropped 1; asks decided 2, retired 1, resolved by evidence 1").

## Boundaries
Only task state fields and Needs you. No hubs, profiles, CURRENT threads or memory structure. No messages: what is decided here shows in the briefing and the app.
