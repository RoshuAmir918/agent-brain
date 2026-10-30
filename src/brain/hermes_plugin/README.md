# Brain memory provider

This standalone Hermes plugin shells out to `brain` on PATH; Hermes does not
need the brain package in its own Python environment. It uses only the Python
standard library. Commands time out after five seconds; failures inject nothing.

From your vault repository:

```sh
brain install-provider
hermes config set memory.provider brain
```

Add the owner's platform IDs to `[brain].owner_user_ids` in `brain.toml`, then
restart the gateway. The plugin is installed in `$HERMES_HOME/plugins/brain`
(default `~/.hermes/plugins/brain`). Installation does not change Hermes config.

The vault is discovered from the session's `cwd`, then `VAULT_REPO`, then
`$HOME/brain`, choosing the first location containing a `brain.toml` (including
its parents). Every CLI call runs from that repository so its configured vault
layout applies.

At session initialization, `brain session-context` supplies a cached identity
and disclosure block. CLI sessions and configured owner IDs receive only the
owner identification. Known third parties receive their profile and applicable
disclosure rows; unknown or ambiguous identities receive a stranger warning.

Before each API call, messages with at least two non-stopword tokens trigger
`brain retrieve --json --limit 6`. Queries are capped at 300 characters and the
recalled block at 1500 characters. These are pointers, not full notes, unless the
vault's `brain.toml` `[context]` table or the `BRAIN_PREFETCH*` variables say
otherwise (see the engine docs). Neither
injected block earns attribution credit; only subsequent on-demand note opens
can be observed uses. This provider adds no tools and writes no conversation
memory. Disclosure instructions guide the agent; they are not a retrieval
access-control filter.
