---
name: {prefix}morning
description: The morning briefing
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Morning briefing

A concise, verified account for {human_name} at the agreed hour, sent every day even when there is no news. Never before that hour.

## Procedure
1. Read `{agent_dir}/Primed.md` first and follow its order. Then open tasks, Triggers, overnight commits, and any task entries tagged `morning:`.
2. Recheck already-registered threads in {human_name}'s channels before raising an item. No broad inbox poll. Do not repeat an unchanged reminder.
3. Compose, separating verified fact from reported status and results from proposals:
   - completions, blockers, decisions or questions owed, today's commitments and dated items;
   - Primed nudges, verbatim, once;
   - Sleep 1's questions and conflicts; notes reactivated from cold;
   - new reflections, labelled as inference;
   - audit patterns, never raw numbers;
   - Improve's recommendation and candidate responsibilities, each with its status, labelled as proposals;
   - whether `archive`, `attribution`, `sleep1`, `rem` and `sleep2` commits exist for last night; a missing one gets its own line.
4. After composing, remove every `morning:` line you carried from the task entries: a fact worth keeping goes into the owning note in one sentence, the rest is gone. The register is a state view, not a log; lines left behind push entries over budget and stop the night.
5. Send the briefing to {human_name}'s Home, the destination named in `{agent_dir}/Channels.md`.
6. Add a line to the Sleep Log: briefing sent, and what it contained in one phrase.

## Customise
This is the natural place for anything {human_name} wants delivered daily: a reading, a reminder, a digest of a feed. Add it as a numbered step with its own boundaries, and record {human_name}'s request in a dated note so the instruction has a source.

## Boundaries
No secrets, payments or account changes. No third-party contact unless {human_name} has authorized a specific recurring send, recorded in `{agent_dir}/Tasks.md` under Standing state. Acceptance is not delivery; say "accepted," not "delivered."
