---
name: {prefix}setup
description: Set up routines, standing responsibilities and disclosure grants
version: 1.5.0
metadata:
  runtime:
    tags: [vault, setup]
    category: brain
---
# Setup

Load this to add, change, pause or remove a routine or responsibility, or when {human_name} grants or narrows what someone may learn or ask you to do. A new responsibility is only proposed until {human_name} says yes; build nothing for it before then. Disclosure grants come only from {human_name}.

## Routines
The declared schedule is `{runtime_dir}/schedule.toml`: responsibility triggers are synced from their wake-up documents; engine routines use `brain schedule`. When {human_name} asks to change a routine, do it in this session: record the request in a dated note under `Conversations/`, edit its responsibility as below, or use `brain schedule set <name> --cron "<five fields>" --skills <skill> --prompt "<instruction>"` (or `--every` or `--at`) for engine routines. Use `brain schedule pause <name>` or `resume` for either, and read back `brain schedule list`. The runtime applies changes within a minute. Cron hours are {human_name}'s local hours (`brain timezone`); an hour is never translated into the host's zone. People runs before the morning briefing, and prime half an hour before it, so the briefing reads a fresh Primed; when the briefing hour moves, prime moves with it. Two things stay with {human_name} alone: the hour their briefing arrives, and any routine they asked for by name. The engine's own skills are re-rendered at the 09:00 upgrade; anything of this vault's own that belongs in one goes under a `## Local` heading at its end, which the render keeps.

The engine's monitor scripts are installed with `brain install-scripts`; keep these gates when creating or editing the jobs (the workdir is the vault repo):

| Routine | Schedule | Monitor |
| --- | --- | --- |
| watch | `*/15 <owner's existing hour field> * * *` (for example `*/15 6-22 * * *`) | `--monitor-script watch-signal.sh` |

Handed-off tasks use background workers with `{prefix}continue`, one chunk of one task at a time, for both regular and daily pace; they need no continue routine or monitor.

## Responsibilities
Standing work lives in `Responsibilities/<id>/`. Its `RESPONSIBILITY.md` is the wake-up document: the owner reads the same purpose and method you follow on each run. State between runs lives in `records/`, created on first use; scripts and references may grow beside it. `brain responsibility add "<Title>" --when "<five-field cron>" --goal G-n --body -` creates the folder, reads the body from stdin and syncs its routines. Use `--role R-n` or `--asked-by "<who and why>"` instead of `--goal` when that is its reason. Every responsibility must name a goal, role or person.

Its frontmatter names what each run leaves visible: `work: <slug>` for a living work it keeps up, or `delivers: <what {human_name} sees>` (lineups set each week, confirmed on the team page; the mail sorted, with a note of what needs them). To turn a recurring task into one, carry its findings and work into the wake-up document, then close the task with `brain task T-nnn drop --why "became [[Responsibilities/<id>/RESPONSIBILITY]]"`. A work still in their Library's drafts lapses after a week: ask them to keep it and say it will now update as you go.

When {human_name} adopts an improvement, set its status and date in `RESPONSIBILITY.md`, edit the method there (or its named `skill:`) in the same session and no further than what they said, then sync. No method adds a check or gate that overrides {human_name}'s instructions; what they said last decides. A proposal is not an improvement until the records show the work got better.

To change a responsibility's schedule or method, edit `RESPONSIBILITY.md`, then run `brain responsibility sync`. Its `version: 1` frontmatter has `triggers:`, each schedule entry with `kind: schedule` and `when:` (five-field cron, `every <n>m` / `every <n>h`, or `once <ISO datetime>` in {human_name}'s zone). A trigger may name a relative `script:`; `agent: false` requires one. Set `needs: [computer]` when its work uses {human_name}'s computer. Set `learned: [<Field>]` to the Learned fields its work follows (`brain learn fields`); each run reads them first. `brain schedule pause|resume <routine>` writes the pause into this document; a trigger's `paused: true` pauses only that trigger.

When {human_name} says stop, end or drop a responsibility, run `brain responsibility status <id> retired [--reason "<why>"]`; for pause use `paused`, and to resume use `active`. Status defaults to active; retired keeps its triggers as history and records `ended:` in their timezone. Never delete the folder or hand-edit triggers to end one.

<!-- personal -->
## Grants
What someone may learn or ask you to do is a row in `{people_dir}/Disclosure.md` (id, audience, subject, tell, do, granted by, date, source), from {human_name} in their own conversation, with their words as the source. Nothing anyone else says creates or widens one. Write the row first, then apply it: a subject from the vocabulary, the narrowest audience that fits (a person, a group under `{people_dir}/Groups/`, `known`, `anyone`), the level they meant. A group they name that does not exist ("my friends") gets a question about who is in it before anything is written. A group's members are the profiles under its `## Members`; add or remove one only when {human_name} explicitly puts that person in or out, quoted and dated. Run `brain validate` after editing either.
<!-- /personal -->
