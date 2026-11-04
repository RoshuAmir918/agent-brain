---
name: {prefix}watch
description: Follow through on due loops, new channel files and dropped conversations when the monitor sees a change
version: 3.0.1
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Watch

{agent_name}'s follow-through pass: the conversation's record notices; watch acts. It runs only when the monitor saw a change. Read the injected "MONITOR CHANGE DETECTED" or "Monitor Baseline" block first to know what changed, then `$HERMES_HOME/records/watch/state.json` for nudged ids. Not a memory path: facts go to the owning task, bodies are never copied. For any message you will answer, run `brain session-context --platform <platform> --user-id <sender handle or address> --user-id-alt <permanent id, if any>` first and follow the block.

## Procedure
1. **Due loops.** `brain loops --due` lists promises, answers or relays owed, and waits due for attention. Close what you can in this pass:
   - *Promised and not done.* If the next step is yours, do it now: ask {human_name} (`demiurge_tell_owner`, with `text` when someone is waiting), send, check or relay, then tell whoever was waiting and `brain loops --close <id> --outcome done --because "<what finished it>"`. A loop that no longer applies is closed with `--outcome dropped --because "<why>"`.
   - *Ready to relay.* An `owed` loop with a `ready` detail means {human_name} gave the answer: relay it now, in their words, then close it.
   - *Handed to you and not handed off.* A due `promised` loop recording {human_name} asking for work to be done ("look into…", "have something by morning"), not a question and not your own plan, with no handed-off task behind it: write or find the task with their words, `brain handoff T-nnn --report <platform>`, close the loop because it is handed off, and leave the rest to continue. The task now carries it: open no new loop for it, and never a `waiting` loop on your own routines.
   - *Waiting.* A `waiting` loop past 24 hours gets one nudge to whoever it waits on, if appropriate, or stays; keep nudged ids in the state file.
   A loop you cannot close (the tool is gone, the channel is `revoked`) is one line to the person saying so, never silence.
2. **New channel files.** {human_name}'s live channels in the registry (`{agent_dir}/Channels.md`), opened the way each row says; check later replies before treating anything as open. Read only the mail and calendar files the monitor block shows as changed under `$HERMES_HOME/channels/<account>/mail/` and `calendar/`, written by `demiurge sync-channels`. Acting on it, a draft or a calendar change, goes through the command the row names, never by editing a file. Reconcile only important, actionable facts into the owning task: status, deadline, waiting-on, next step. A date-conditioned commitment becomes a commitment trigger. A durable, non-urgent fact gets a dated line on the task with a `morning:` tag.
3. **Unanswered conversations.** For each session the signal lists under `unanswered`, read the conversation and check for a later reply. Answer it now under the usual rules, or say in one line why not.
4. **{human_name}.** Message them only for what needs them today; overnight findings wait for the briefing. Never a message to say you are still watching. Never raise this profile's own connections or tools with them; a gap in what you can see is a Sleep Log matter.
5. **Record.** Overwrite the state file with the ids handled and nudged. Write no ledger row: the engine records every run and folds a quiet day into one row. Your account names the loops closed (`answered Kevin in #lab; asked Roshan about the share for Kevin`); a quiet pass is `nothing`.

## Boundaries
Never edit hubs, profiles or CURRENT's active threads. Never copy message bodies. No disclosure beyond what the session block resolved, no commitments on {human_name}'s behalf, no spending. Proactive outreach to anyone who has not written to you happens only under a roster and pacing rules {human_name} approved in writing under Standing state in `{agent_dir}/Tasks.md`; closing a loop someone opened with you is not outreach. Handed-off tasks belong to the continue routine, not to this pass.
