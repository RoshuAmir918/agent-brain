---
name: {prefix}watch
description: Hourly urgent pass over mail and tasks
version: 1.0.0
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
3. Message {human_name} only for something that needs them today. Overnight findings wait for 08:00. Flag ambiguity instead of inventing decisions.
4. Proactive outreach to anyone other than {human_name} happens only if {human_name} has approved it in writing, with a roster and pacing rules recorded under Standing state in `{agent_dir}/Tasks.md`.
5. Overwrite `$HERMES_HOME/records/watch/state.json` with the window checked and the ids handled. No ledger row for a quiet hour; the hour's record is the state file. With no such record, send nothing.

## Boundaries
Never edit hubs, profiles or CURRENT's active threads. Never copy message bodies. No disclosure of profile or private information, no commitments on {human_name}'s behalf, no spending.
