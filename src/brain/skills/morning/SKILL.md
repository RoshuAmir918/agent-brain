---
name: {prefix}morning
description: The morning briefing
version: 2.0.0
metadata:
  hermes:
    tags: [routine, day]
    category: brain
---
# Morning briefing

What today is for {human_name}, at the agreed hour, every day, even when there is little to say. Never before that hour. It reads like a good assistant's word at the door, not a status report: two hundred words is the ceiling, and a hundred is a good morning. The machinery's health lives in the Sleep Log and `brain health`, where a maintainer reads it; none of it is for {human_name}.

## Procedure
1. Read `{agent_dir}/Primed.md` first; it is the brief Sleep 2 wrote for today. Then Upcoming in the session context, Triggers, open tasks, any task entries tagged `morning:`, and what `brain needs-you` lists.
2. Recheck already-registered threads in {human_name}'s channels before raising an item. No broad inbox poll. Do not repeat an unchanged reminder.
3. Compose in this order, dropping any part with nothing in it, separating verified fact from reported status and results from proposals:
   - **Today.** Where they are, who they are with, what they said they would do, and what is due or happening today or tomorrow, from Primed and Upcoming. One or two sentences, not a list. When the vault cannot confirm a due thing was done, say so once: "due today; I can't see that it's done."
   - **Done for you.** The one thing {agent_name} prepared, found or finished since yesterday, if there is one, and where it is. A thing held under `{agent_dir}/Prepared/` is mentioned here once, as waiting, and never again.
   - **Needs you.** Asks first, then confirms, one short line each, from `brain needs-you`. Primed's nudges verbatim, once. Sleep 1's open conflicts and questions.
   - **Coming up.** The dated items in the next week that need something from them, from Upcoming. Only those; the rest stays in context.
   - **Noticed**, labelled as inference: a new reflection, a pattern the audit found, Improve's recommendation with its status, the weekly service line when Sleep 2 wrote one. Never raw numbers.
4. What the briefing leaves out: whether last night's commits exist, which stages ran, routine drift, retrieval-audit counts, consolidation disagreements, source-coverage caveats, and anything about this profile's own connections or tools. They are in the Sleep Log; `brain health` reports the stages; a maintainer asks for them. When the night did not run, that is one sentence in total, however many stages are missing ("I didn't consolidate last night, so this is from the day before"), never a list. A responsibility's prepared piece (a letter, a comparison, a companion) is one line under Done for you with where it is, not its contents.
5. After composing, remove every `morning:` line you carried from the task entries (never a `## Needs you` item: it leaves when it is resolved): a fact worth keeping goes into the owning note in one sentence, the rest is gone. The register is a state view, not a log; lines left behind push entries over budget and stop the night.
6. Send the briefing to {human_name}'s Home, the destination named in `{agent_dir}/Channels.md`.
7. Add a line to the Sleep Log: briefing sent, and what it contained in one phrase.

## Customise
This is the natural place for anything {human_name} wants delivered daily: a reading, a reminder, a digest of a feed. Add it as a numbered step with its own boundaries, and record {human_name}'s request in a dated note so the instruction has a source.

## Boundaries
No secrets, payments or account changes. No third-party contact unless {human_name} has authorized a specific recurring send, recorded in `{agent_dir}/Tasks.md` under Standing state. Acceptance is not delivery; say "accepted," not "delivered."
