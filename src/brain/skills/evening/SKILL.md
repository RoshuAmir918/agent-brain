---
name: {prefix}evening
description: Evening register update before the night
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Evening review

Leave Tasks accurate for the night.

## Procedure
1. Read today's sessions, open tasks, Triggers, and targeted rechecks of registered threads. Check later replies.
2. For anything that moved today, update state, next action, blocker and latest evidence link. Note unfinished threads on their tasks so Sleep 2 can prime them.
3. Review what has gone quiet: `brain stale-tasks`. Decide each one yourself; {human_name} should never have to sweep a list of things they once mentioned.
   - `cancelled` when the evidence says it no longer applies: superseded by a later decision, done another way, an event or opportunity that has passed, or {human_name} said to drop it. An overdue obligation (a bill, a promise, a filing) has not stopped applying; it is at stake.
   - `set_aside` when it has only gone quiet and nothing is at stake in waiting: no money, no promise to someone else, no live deadline, nothing {human_name} called important. Set aside is reversible; when it comes up again, set its state back.
   - Ask only when it has gone quiet and something is at stake: `brain needs-you --ask "<one question>" --source <Tasks note>#<id>`. At most two a week.
   - Leave it when it waits on someone else or on a date still ahead.
   Write each decision into the task as `- **Reviewed:** <date> — <set aside | dropped | asked>: <why, in one clause>`. The run's account gives the counts ("set aside 3, dropped 1, asked 0") and names them.
4. Message {human_name} only for important developments, deadlines or decisions. No no-news message.
5. Commit `evening(<date>)` if anything changed.

## Boundaries
Only Tasks state fields, and asks through `brain needs-you --ask`. No hubs, profiles, CURRENT threads or memory structure; Sleep 1 owns those tonight. Fully read-only externally; no outreach.
