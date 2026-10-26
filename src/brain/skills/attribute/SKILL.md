---
name: {prefix}attribute
description: Nightly reliance attribution from the digest
version: 1.0.0
metadata:
  hermes:
    tags: [memory, night]
    category: brain
---
# Attribution

Decide which notes yesterday's replies relied on. This is what drives activation, so it runs on its own, before consolidation, and does nothing else.

## Procedure
1. `cd $VAULT_REPO && git pull --rebase --autostash`. If `git log --since=yesterday --grep='attribution('` already has today's commit, stop.
2. `brain attribute --write`. Reads last night's export from this profile's `$HERMES_HOME/records/sessions`. Writes observed rows for on-demand opens and explicit rows for any `Sources:` line. Deduplicated; safe to rerun.
3. Read `Conversations/Digests/<yesterday>.md`. Its `coverage:` line says how many sessions were included and how many routine and evaluation sessions the engine excluded; if turns were omitted by the word cap, regenerate with `brain digest --date <yesterday> --max-words 100000` and read all of it before judging. Routine sessions (the agent's own cron runs) and evaluation sessions never earn credit; judge only conversations. Every {agent_name} turn carries a `reads:` line naming the notes opened before it. For each substantive turn, decide which of those notes the reply relied on: a fact, a judgment, a decision, a constraint or a next step that came from the note. For each, append to `{agent_dir}/Use Log/<month>.md`:
   `<timestamp> | <vault-relative path or T-nnn> | judged: <the sentence that shows reliance> | explicit`
   No sentence, no row. A note opened and ignored earns nothing. A turn with no `reads:` line earns nothing. Skip a note already credited explicitly that day.
4. For each turn where {agent_name} said it searched and found nothing, add `<timestamp> | MISS | <what it looked for> | explicit`.
5. Commit `attribution(<date>): <n judged, m observed, k misses>` and push. Zero rows is a valid completed run.

## Boundaries
Writes only the Use Log. Never edits a note, a task or a trigger. Never credits a note for being loaded, linked, mentioned or merely similar to the topic.
