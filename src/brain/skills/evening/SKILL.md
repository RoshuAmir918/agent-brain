---
name: {prefix}evening
description: Evening register update before the night
version: 1.2.0
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
   - Ask only when it has gone quiet and something is at stake: `brain needs-you --ask "<one question>" --source <Tasks note>#<id>`. At most two a week, and none when Needs you is full.
   - Leave it when it waits on someone else or on a date still ahead.
   Write each decision into the task as `- **Reviewed:** <date> — <set aside | dropped | asked>: <why, in one clause>`. The run's account gives the counts ("set aside 3, dropped 1, asked 0") and names them.
   Then the asks that have waited: `brain needs-you --stale` lists every ask unanswered for two weeks. Decide each yourself. When it is reversible, take the answer {human_name} would most likely give, act on it, and replace the ask with a `confirm` saying what you did, so they can still overturn it. When it is not, or it was never theirs (machinery, someone else's decision), remove it and write one dated line on its source note saying why. Never re-ask one. The account names how many were decided and retired.
4. Message {human_name} only for important developments, deadlines or decisions. No no-news message. A channel that is not registered in `{agent_dir}/Channels.md` does not exist for this pass: never report a mailbox, workspace or connection {human_name} has not set up as missing, failing or unread, and never raise this profile's own connections or tools with them; a gap in what you can see is a Sleep Log matter, not theirs.
5. Commit `evening(<date>)` if anything changed.

## Boundaries
Only Tasks state fields, and asks through `brain needs-you --ask`. No hubs, profiles, CURRENT threads or memory structure; Sleep 1 owns those tonight. Fully read-only externally; no outreach.
