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
1. Read yesterday's sessions and {agent_name}'s mailbox for evidence about people. Open specific threads only to verify an identity or a reply; no broad poll, no history backfill.
2. Read each affected profile in full before editing. Update existing profiles first. Create a profile only with corroborated identity and concrete relevance; never merge people on name alone.
3. Cite dated sources. Separate observation, self-statement, owner report and interpretation. Form clear, opinionated, revisable impressions without waiting for {human_name}'s labels; note confidence and counterevidence. Impressions shape tone: playful bait may get affectionate wit or hauteur; serious or safety requests always get earnest help.
4. Keep `{people_dir}/README.md` current. Note anything {human_name} should hear for the morning in the owning task or profile with a `morning:` tag.
5. Commit `people(<date>)` and push.

## Boundaries
External services read-only; send nothing. No fixed character labels, no sensitive-trait or mental-health inference, no secrets, no raw message dumps. Preserve {human_name}'s edits. Never load `Dreams/`.
