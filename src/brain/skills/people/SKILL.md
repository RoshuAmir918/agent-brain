---
name: {prefix}people
description: Daily source-backed People profile review
version: 1.0.0
metadata:
  hermes:
    tags: [memory, people]
    category: brain
---
# People review

Maintain source-backed profiles and {agent_name}'s own revisable impressions of the people in {human_name}'s life.

## Procedure
1. Read yesterday's digest in `Conversations/Digests/`. Every user turn labelled with something other than {human_name}'s name is another person speaking: a platform handle or id (a Demiurge handle, say), or a display name. A handle can be renamed; the permanent id on the profile's `Demiurge:` line is what identifies them. List the distinct speakers, with what each said and asked and how they responded to the agent.
2. Resolve each speaker to a profile under `{people_dir}/` by the platform id recorded on the profile. A speaker with no matching profile is an unknown person: create `{people_dir}/<display name or id>/Profile.md` from the template with the id as its only confirmed identity, the date, and what they said, marked "identity unconfirmed by {human_name}", and put one line for the morning: "a new person messaged me; who is this?" Never merge an unknown id into an existing profile on a name alone.
3. For each person spoken with, write the day's dated entry to `{people_dir}/<Name>/History.md` (newest first: date, channel, what they said and asked, how it went, filed as their claims) and update `Profile.md` in place: what they told the agent, filed as their claim with the date; what they asked for and whether it was answered or declined under Disclosure; and the impression. Impressions are the agent's own and say so: how they talk, what they care about, what they expect from the agent, with confidence and counterevidence. Revise an earlier impression when yesterday contradicted it; do not just append. A Profile stays under 300 words and holds only what is stable: identifiers, relationship, impression, what they care about, standing facts, disclosure notes. Dated material belongs in History. A profile that has grown past the budget is split on this pass: move its dated entries to History, keep each fact as one sentence, add the History link at the end of the Profile.
4. Also read the agent's own mailbox for evidence about people. Open specific threads only to verify an identity or a reply; no broad poll, no history backfill. Read each affected profile in full before editing.
5. Keep `{people_dir}/README.md` current, including the Disclosure note: a person who asked for something the rules did not allow is a line for the morning, so {human_name} can grant or refuse.
6. Overwrite `$HERMES_HOME/records/people/state.json` with the reviewed window and the ids handled, so a rerun does not count a speaker twice. Commit `people(<date>)` and push.

## Boundaries
External services read-only; send nothing. No fixed character labels, no sensitive-trait or mental-health inference, no secrets, no raw message dumps. Preserve {human_name}'s edits. Never load `Dreams/`.
