---
name: {prefix}study
description: Nightly study for a role, refreshing its sources and learning from what the screen kept
version: 1.1.0
metadata:
  runtime:
    tags: [roles, night]
    category: brain
    editions: [enterprise]
    context: [start, current]
---
# Study

A role gets better at its job by studying how the team corrects work, rules on it and states what is true about the organisation. Each night it reads what the screen kept since the last study and writes what it learned into the role's notes, every line sourced. The role is named in the prompt; its template and notes are in `Roles/<role>/`.

## Procedure
1. **Pull and read.** `brain pull`, then read `Roles/<role>/Role.md` and every note it lists. `brain roles show <role>` tells you which lines a person wrote (theirs outrank yours: never reword or retire them) and which are struck.
2. **Refresh the sources.** For each source the template declares that you read yourself, fetch what arrived since the newest file, as the seed skill describes. For `reviews`: in each `$AGENT_HOME/channels/<owner>-<repo>/reviews/` folder, `.refreshed` holds the date the last refresh finished. List the pull requests merged since that date (thirty days ago if it is missing), write each one whose file is missing, in the seed skill's format, and only then write today's date to `.refreshed`, so a refresh that stops is done again from the same date. The screen that runs after you labels them for tomorrow night.
3. **Corrections on your own work first: they are the strongest evidence.** For each piece of work in `brain roles work <role> list` that is open or closed in the last thirty days, read what reviewers said and did (for a pull request: its reviews and their state, its comments, the commits someone else pushed after you opened it, whether it merged or was closed, a later revert). Record each with `brain roles correction <role> add --work <id> --event <event> --by <who> --at <YYYY-MM-DD> --text "<what they said or changed>" --source-id <its id on the source>` (`--where <file>` when it is on one); the events are the ones the template's `corrections` lists, and a repeat is kept once. Then for each in `brain roles correction <role> next`, heaviest first: if a line in the notes already says it, `brain roles correction <role> file <id> --to <line id>`; if not, `file <id> --note <shape> --section <heading> --claim "<one claim a future task can act on>"`; add `--rule` only when the person stated it as a rule for the team (the engine checks they may). An event that teaches nothing beyond its own work (a typo, a one-off question) is `dismiss <id> --why "<why>"`. The engine marks a line active once a second piece of work backs it.
4. **Read the digest.** `brain roles study <role> next`. If it says nothing new, reply `[SILENT]`. Bold items are what the screen kept; the others are the exchange around them. A terse ruling from an owner, read with what it answered, is often the strongest line in the night.
5. **Write lessons.** For each thing worth keeping, find the note whose shape fits and its section. A line is one claim a future task can act on ("data changes go through audited backfills, never SQL in a migration, because …"), not a history of the item, ending with its sources in parentheses: `(review on #812, jo, 2026-09-14)`. Before adding a claim, run `brain roles may-learn <role> --claim "<line>" --evidence <newest source date>`; if it refuses, a person struck it and nothing newer says otherwise: leave it out. A claim seen once is `*(candidate)*`; it becomes active when it recurs on another piece of work or someone with authority states it as a rule, and then you add the new source to the existing line rather than writing a second. Newer evidence wins over older: where the team changed its mind, say so on the line. A company fact goes in the note whose shape holds rulings and facts.
6. **Keep it usable.** Merge duplicates into one line that keeps every source. Retire a line of yours that newer evidence contradicts, with the item that retired it in your commit message. Keep each note readable before a task, under about 400 lines.
7. **Commit.** `brain commit -m "study(<role>): <what changed in a few words>"`, then `brain roles rebuild <role>` (it gives new lines their ids and commits them), then `brain commit --push -m "study(<role>): push"`.
8. **Mark it studied.** `brain roles study <role> done`. Only now: a study that stops before this step reads the same items again next night and adds only what is missing.

Reply `[SILENT]` unless something needs a person: a ruling that contradicts a line a person wrote, or a source you could not refresh.
