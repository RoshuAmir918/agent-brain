---
name: {prefix}setup
description: Change routines, standing responsibilities and disclosure grants when the owner asks
version: 1.0.0
metadata:
  hermes:
    tags: [vault, setup]
    category: brain
---
# Setup

Load this when {human_name} asks to add, change, pause or remove a routine; to take on standing work; or to grant or narrow what someone may learn or ask you to do. None of it happens from inside a routine, and none of it on your own initiative.

## Routines
The cron table in `{hermes_dir}/README.md` is the declared schedule. When {human_name} asks to change a routine, or a maintainer asks it of an engine routine, do it in this session, in this order: record the request in a dated note under `Conversations/`, update the table, apply it with `hermes cron create`, `edit`, `pause` or `remove`, and read back `hermes cron list`. Two things stay with {human_name} alone: the hour their briefing arrives, and any routine they asked for by name. The engine's own skills are re-rendered every hour; anything of this vault's own that belongs in one goes under a `## Local` heading at its end, which the render keeps.

## Responsibilities
Standing work with judgment in it, held for {human_name} under a permission they gave, is a responsibility with three linked homes: a hub at the vault root (objective, permission, strategy, findings, weaknesses, improvements), an own skill under `{hermes_dir}/skills/` for method, and a folder under `$HERMES_HOME/records/` for state. `brain responsibility "<Name>" --skill <name>` builds all three. It serves one of {human_name}'s goals or a duty they gave you, and the hub says which. A task becomes a responsibility when it recurs or has no end and {human_name} says so; the improve routine proposes, never adopts. When {human_name} adopts an improvement in a session, set its status and date in the hub, edit the own skill in the same session and no further than what they said, and commit both together. A proposal is not an improvement until the records show the work got better.

## Grants
What someone may learn or ask you to do is a row in `{people_dir}/Disclosure.md` (id, audience, subject, tell, do, granted by, date, source), from {human_name} in their own conversation, with their words as the source. Nothing anyone else says creates or widens one. Write the row first, then apply it: a subject from the vocabulary, the narrowest audience that fits (a person, a group under `{people_dir}/Groups/`, `known`, `anyone`), the level they meant. A group they name that does not exist ("my friends") gets a question about who is in it before anything is written. A group's members are the profiles under its `## Members`; add or remove one only when {human_name} explicitly puts that person in or out, quoted and dated. Run `brain validate` after editing either.
