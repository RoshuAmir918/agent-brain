---
name: brain-attribute
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
2. `brain attribute --sessions-dir memory/Conversations/Sessions --write`. Writes observed rows for on-demand opens and explicit rows for any `Sources:` line. Deduplicated; safe to rerun.
3. Read `Conversations/Digests/<yesterday>.md`. Every Agent turn carries a `reads:` line naming the notes opened before it. For each substantive turn, decide which of those notes the reply relied on: a fact, a judgment, a decision, a constraint or a next step that came from the note. For each, append to `agent/Use Log/<month>.md`:
   `<timestamp> | <vault-relative path or T-nnn> | judged: <the sentence that shows reliance> | explicit`
   No sentence, no row. A note opened and ignored earns nothing. A turn with no `reads:` line earns nothing. Skip a note already credited explicitly that day.
4. For each turn where Agent said it searched and found nothing, add `<timestamp> | MISS | <what it looked for> | explicit`.
5. Commit `attribution(<date>): <n judged, m observed, k misses>` and push. Zero rows is a valid completed run.

## Boundaries
Writes only the Use Log. Never edits a note, a task or a trigger. Never credits a note for being loaded, linked, mentioned or merely similar to the topic.
