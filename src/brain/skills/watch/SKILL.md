---
name: {prefix}watch
description: Hourly pass for what needs the owner today and for loops the agent left open
version: 2.3.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Watch

The hourly pass over everything that could be waiting: {human_name}'s channels for what needs them today, and {agent_name}'s open-loops list for things it left open, a message never answered, a promise never kept, a person still waiting on an answer. Not a memory path: facts go to the owning task, bodies are never copied.

## Procedure
1. **Window.** Read `$HERMES_HOME/records/watch/state.json` for the end of the last window; the window is from there to now (the last hour on a first run). For any message you will answer, run `brain session-context --platform <platform> --user-id <sender handle or address> --user-id-alt <permanent id, if any>` first and follow the block.
2. **{human_name}'s channels.** Their live channels in the registry (`{agent_dir}/Channels.md`), opened the way each row says; check later replies before treating anything as open. A channel kept as files (`$HERMES_HOME/channels/<account>/mail/` and `calendar/`, written by `demiurge sync-channels`) is read as files: only the mail and calendar files changed since this window began, as you would read a log, never a search over everything. When its newest file is more than an hour old the sync is not running: use the row's command instead (`demiurge mail search 'newer_than:1d'`, `demiurge calendar list`) and write one Sleep Log line. Acting on it, a draft or a calendar change, goes through the command the row names, never by editing a file. Reconcile only important, actionable facts into the owning task: status, deadline, waiting-on, next step. A date-conditioned commitment becomes a commitment trigger. A durable, non-urgent fact gets a dated line on the task with a `morning:` tag.
3. **{agent_name}'s own loops.** Your own conversations and rooms in the window: Home, the rooms you are in, direct conversations, the `own` rows of the registry. Use `brain loops` for held work; close what you can in this pass:
   - *Unanswered.* A message addressed to you, by name, mention or a question plainly yours, with no reply from you. Answer it now under the usual rules, or say in one line why not.
   - *Promised and not done.* `brain loops --due` lists promises, answers or relays owed, and waits due for attention. If the next step is yours, do it now: ask {human_name} (`demiurge_tell_owner`, with `text` when someone is waiting), send, check or relay, then tell whoever was waiting and `brain loops --close <id> --outcome done --because "<what finished it>"`. A `waiting` loop past 24 hours gets one nudge to whoever it waits on, if appropriate, or stays; keep nudged ids in the state file. A loop that no longer applies is closed with `--outcome dropped --because "<why>"`.
   - *Handed to you and not handed off.* A row recording {human_name} asking for work to be done ("look into…", "have something by morning"), not a question and not your own plan, with no handed-off task behind it: write or find the task with their words, `brain handoff T-nnn --report <platform>`, and leave the rest to continue.
   - *Answered elsewhere.* Each open item in `brain needs-you --json` (at most five asks and a few confirms): did anything since it was raised answer it (its date in `--json`; look first in the conversation it came from, then everything after, not just this window), {human_name} saying so in any conversation, a channel showing it done, a task changing state? Then `brain needs-you --resolve <id> --because "<what showed it, in a sentence>"`. An answer that settles part of an ask resolves it and adds the open remainder as its own ask.
   - *Waiting on an answer you owe.* A person or intellect who asked you something that needed {human_name}, and {human_name} has answered: relay it in their words, now. {human_name} has not answered after a day: tell the person once that it is still with {human_name}, and no more than once.
   A loop you cannot close (the tool is gone, the channel is `revoked`) is one line to the person saying so, never silence.
4. **{human_name}.** Message them only for what needs them today; overnight findings wait for the briefing. Never a message to say you are still watching. Never raise this profile's own connections or tools with them; a gap in what you can see is a Sleep Log matter.
5. **Record.** Overwrite the state file with the end of the window and the ids handled. Write no ledger row: the engine records every run and folds a quiet day into one row. Your account names the loops closed (`answered Kevin in #lab; asked Roshan about the share for Kevin`); a quiet pass is `nothing`.

## Boundaries
Never edit hubs, profiles or CURRENT's active threads. Never copy message bodies. No disclosure beyond what the session block resolved, no commitments on {human_name}'s behalf, no spending. Proactive outreach to anyone who has not written to you happens only under a roster and pacing rules {human_name} approved in writing under Standing state in `{agent_dir}/Tasks.md`; closing a loop someone opened with you is not outreach. Handed-off tasks belong to the continue routine, not to this pass.
