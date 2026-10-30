---
name: {prefix}watch
description: Hourly urgent pass over mail and tasks
version: 1.1.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Watch guard

An hourly pass for deadlines, things needing {human_name} today, and register accuracy. Not a memory path.

## Procedure
1. For any message you will answer, run `brain session-context --platform <platform> --user-id <sender handle or address> --user-id-alt <permanent id, if any>` first and follow the block. Read the last hour of the channels in `{agent_dir}/Channels.md` and {agent_name}'s own mailbox; check later replies first.
2. Reconcile only important, actionable facts into the owning task: status, deadline, waiting-on, next step. A date-conditioned commitment becomes a commitment trigger. Durable but non-urgent facts get a dated line on the task with a `morning:` tag; Sleep 1 consolidates them.
3. Message {human_name} only for something that needs them today. Overnight findings wait for 08:00. Flag ambiguity instead of inventing decisions. A channel that is not registered in `{agent_dir}/Channels.md` does not exist for this pass: never report a mailbox, workspace or connection {human_name} has not set up as missing, failing or unread, and never raise this profile's own connections or tools with them; a gap in what you can see is a Sleep Log matter, not theirs.
4. Proactive outreach to anyone other than {human_name} happens only if {human_name} has approved it in writing, with a roster and pacing rules recorded under Standing state in `{agent_dir}/Tasks.md`.
5. Overwrite `$HERMES_HOME/records/watch/state.json` with the window checked and the ids handled. Write no ledger row: the engine records every run after it ends and folds a day's quiet runs into one row. With no state file record, send nothing.

## Boundaries
Never edit hubs, profiles or CURRENT's active threads. Never copy message bodies. No disclosure of profile or private information, no commitments on {human_name}'s behalf, no spending.
