---
name: {prefix}setup
description: Change routines, standing responsibilities and disclosure grants when the owner asks
version: 1.3.0
metadata:
  runtime:
    tags: [vault, setup]
    category: brain
---
# Setup

Load this when {human_name} asks to add, change, pause or remove a routine; to take on standing work; or to grant or narrow what someone may learn or ask you to do. None of it happens from inside a routine, and none of it on your own initiative.

## Routines
The declared schedule is `{runtime_dir}/schedule.toml`, changed only with `brain schedule`. When {human_name} asks to change a routine, or a maintainer asks it of an engine routine, do it in this session: record the request in a dated note under `Conversations/`, use `brain schedule set <name> --cron "<five fields>" --skills <skill> --prompt "<instruction>"` (or `--every` or `--at`), `pause`, `resume` or `remove`, and read back `brain schedule list`. The runtime applies changes within a minute. Cron hours are {human_name}'s local hours (`brain timezone`); an hour is never translated into the host's zone. People runs before the morning briefing, and prime half an hour before it, so the briefing reads a fresh Primed; when the briefing hour moves, prime moves with it. Two things stay with {human_name} alone: the hour their briefing arrives, and any routine they asked for by name. The engine's own skills are re-rendered every hour; anything of this vault's own that belongs in one goes under a `## Local` heading at its end, which the render keeps.

The engine's monitor scripts are installed with `brain install-scripts`; keep these gates when creating or editing the jobs (the workdir is the vault repo):

| Routine | Schedule | Monitor |
| --- | --- | --- |
| watch | `*/15 <owner's existing hour field> * * *` (for example `*/15 6-22 * * *`) | `--monitor-script watch-signal.sh` |

Handed-off tasks use background workers with `{prefix}continue`, one chunk of one task at a time, for both regular and daily pace; they need no continue routine or monitor.

## Responsibilities
Standing work with judgment in it, held for {human_name} under a permission they gave, is a responsibility with three linked homes: a hub at the vault root (objective, permission, strategy, findings, weaknesses, improvements), an own skill under `{runtime_dir}/skills/` for method, and a folder under `$AGENT_HOME/records/` for state. `brain responsibility "<Name>" --skill <name>` builds all three. Its hub names what each run leaves visible: `work: <slug>` for a living work it keeps up, or `delivers: <what {human_name} sees>` (lineups set each week, confirmed on the team page; the mail sorted, with a note of what needs them). It serves one of {human_name}'s goals or a duty they gave you, and the hub says which. A task becomes a responsibility when it recurs or has no end and {human_name} says so; the improve routine proposes, never adopts. To convert one: build the responsibility, carry the task's findings into the hub, name the task's work in the hub's `work:` (a work still in their Library's drafts lapses after a week, so ask them to keep it, and say it will now update as you go), then close the task with `brain task T-nnn drop --why "became [[Hub]]"`. The task's continue thread stops; the responsibility's own routine keeps the work up. A living work, or a large one (a table of many rows, a collection of many items), keeps a summary saying what matters at a glance (`brain works view <slug>`, the same kit as task views), updated in the same chunk that changes what it summarises; a one-off document needs none. When {human_name} adopts an improvement in a session, set its status and date in the hub, edit the own skill in the same session and no further than what they said, and commit both together. A proposal is not an improvement until the records show the work got better.

When adding a responsibility's routine with `brain schedule set`, pass `--responsibility <id>` (the hub's `records`, else its skill without `{prefix}`, else its title, normalized to a lowercase hyphenated slug). Add `--needs computer` when its work uses {human_name}'s computer.

<!-- personal -->
## Grants
What someone may learn or ask you to do is a row in `{people_dir}/Disclosure.md` (id, audience, subject, tell, do, granted by, date, source), from {human_name} in their own conversation, with their words as the source. Nothing anyone else says creates or widens one. Write the row first, then apply it: a subject from the vocabulary, the narrowest audience that fits (a person, a group under `{people_dir}/Groups/`, `known`, `anyone`), the level they meant. A group they name that does not exist ("my friends") gets a question about who is in it before anything is written. A group's members are the profiles under its `## Members`; add or remove one only when {human_name} explicitly puts that person in or out, quoted and dated. Run `brain validate` after editing either.
<!-- /personal -->
