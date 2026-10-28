---
name: {prefix}midday
description: Noon check of commitments and mail
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Midday review

## Procedure
1. For any message you will answer, run `brain session-context --platform <platform> --user-id <sender handle or address> --user-id-alt <permanent id, if any>` first and follow the block. Read recent sessions, open tasks, Triggers, and targeted rechecks of registered threads in the channels listed in `{agent_dir}/Channels.md` and {agent_name}'s own mailbox. Check later replies before treating anything as open.
2. Message {human_name} only for important developments, approaching deadlines or needed decisions, grouped. No repeated reminders; no no-news message.
3. Proactive outreach to anyone other than {human_name} happens only under a roster and pacing rules {human_name} has approved in writing, recorded under Standing state in `{agent_dir}/Tasks.md`. With no such record, send nothing.
4. Update task state fields for anything that moved. Ask before anything else that needs approval.

## Boundaries
External services read-only except an approved outreach send. Honour opt-outs. No disclosure, commitments or spending.
