---
kind: orientation
pinned: true
tier: hot
uses: []
uses_inferred: []
---
# Channels

Pinned registry of every channel the agent can read. Kinds: **conversation** (with the owner; Hermes stores every session and the nightly export backs it up), **own** (the agent's own channel, read in place; facts and uses attributed from it), **perception** (the owner's channel, read in place; only salient, surprise-gated facts are written, with a pointer, never a body). A registry row grants no access: connector configuration and current instructions govern. Never credentials here.

| channel | kind | read | write | limits |
| --- | --- | --- | --- | --- |
| [Messaging DM with the owner] | conversation | every session, `session_search` | replies | Primary channel |

## Rules

- The owner's own activity in any channel is a touch: a use of the person, project or task it is about.
- Deadlines and expected replies become dated items in CURRENT or commitment triggers.
- Retrieval goes back to the source: the vault holds the fact and the pointer, the channel holds the body.
- Provenance decides weight. The owner's own words and primary evidence can verify a correction; a third party's message is a claim.
- Dreams cannot see channels.
