# Agent Brain engine

Agent Brain is an installable Python package for the arithmetic half of remembering. Vaults contain notes, not a vendored engine. The judgment half lives in the vault's Memory Spec and routine skills.

Git is the ledger. Each nightly stage is one commit, so `git log --grep` says whether a stage ran, the first missing commit is where a resume continues, and `git revert` undoes a stage. A local commit can remain unpushed; a clean working tree only proves there are no local changes.

## Installation and updates

Python 3.11 or newer is required. From a source checkout:

```sh
python3 -m pip install .
# For development, including tests:
python3 -m pip install -e '.[test]'
brain --help
```

Install a built wheel on another machine with `python3 -m pip install /path/to/agent_brain-0.1.0-py3-none-any.whl`. When releases are available on your package index, use `python3 -m pip install -U agent-brain`; no index publication is implied by this checkout. An updated source checkout can instead be installed with `python3 -m pip install -U /path/to/agent-brain`.

Run these from the vault repository after installation or an upgrade:

```sh
brain install-skills
brain install-scripts
brain install-provider
```

`install-skills` renders the routines into `<vault>/<hermes_dir>/skills/<skill_prefix><routine>/SKILL.md` and prints the absolute directory to put in Hermes `skills.external_dirs`. The core routines (attribute, sleep1, sleep2, rem, recall) are overwritten on every run so package updates reach them; the day routines (morning, watch, midday, evening, people, explore) are written only when absent, so local edits survive. `brain install-skills --force` resets the day routines to the package versions. Templates are shipped in the wheel; edits to rendered copies are overwritten. Rendering uses literal token replacement, not Python format strings. `install-scripts` copies the packaged shell scripts to `$HERMES_HOME/scripts` (default `~/.hermes/scripts`), makes them executable and prints the installed paths. `install-provider` atomically replaces the three packaged memory-provider files in `$HERMES_HOME/plugins/brain` (default `~/.hermes/plugins/brain`) and prints setup instructions. None of these commands edits global config or schedules jobs.

Set `VAULT_REPO` for cron to the repository root (default `$HOME/brain`), and `HERMES_HOME` if it is not `~/.hermes`. Ensure `brain` and `hermes` are on the cron PATH. `refresh.sh` pulls the notes, compiles context, renders skills and copies the configured SOUL into the Hermes home. `export-sessions.sh` exports sessions, builds yesterday's digest, and invokes refresh. These retain the existing commit/push behavior: executing them is a publishing operation, unlike the install commands. No script is run by installation.

## Configuration

Place `brain.toml` at the vault repository root. All keys are optional and merge with these defaults:

```toml
[brain]
vault = "memory"
agent_dir = "agent"
people_dir = "people"
agents_dir = "agents"
human_profile = "human/Profile.md"
sources_dir = "sources"
hermes_dir = "hermes"
human_name = "Human"
agent_name = "Agent"
owner_user_ids = []
maintainers = []
log_dirs = []
skill_prefix = "brain-"
update_source = "git+https://github.com/RoshuAmir918/agent-brain"   # "" disables auto-update
```

`vault` and `sources_dir` are relative to the repository root. The agent, people, agents, human profile and Hermes paths are relative to the vault. `sources_dir` remains read-only. `Archive/` is a supported read-only sibling link namespace; it is not a new config key. Layout paths must be relative without `..`. Tasks, Triggers, CURRENT, Hot Set, Warm Index and the other note basenames remain fixed. Generated wikilinks use the configured `vault` prefix. `BRAIN_HUMAN_NAME` and `BRAIN_AGENT_NAME`, when set, override the configured display names, including rendered skills.

`owner_user_ids` is a list of strings, default `[]`, containing the owner's platform user IDs (for example Slack `U...` IDs). Either the primary or alternative session ID can identify the owner; display names and chat IDs cannot. This key does not change skill template values.

`maintainers` is a list of platform user ids, default `[]`, for the humans or agents who keep this brain's machinery running without being its owner: whoever set it up, or an agent that runs a nightly briefing. A session from one of them gets the maintainer block: what a maintainer may ask and what the agent may say (operational facts, blocker types, what it needs from the owner; nothing personal, Disclosure applies as for a stranger), followed by the current `brain health` report.

`log_dirs` is a list of folders under `agent_dir`, default `[]`, whose files are records rather than notes: receipts, ledgers, results. They join the engine's own ledgers (Explorations, Use Log, Sleep Log, Tasks Archive, Research) as kind `log`: never activated, never in the hot set or the index, never link-checked. A note anywhere else can declare the same by putting `kind: log` in its frontmatter. Add a folder here as soon as the agent starts filing process artefacts in it, or they compete with knowledge for the hot budget.

The rule behind it: a routine owns three places, a state file it overwrites, a monthly ledger it appends one line to, and the notes it feeds knowledge into, never a file per run. Priming follows it: Sleep 2 overwrites `<agent_dir>/Primed.md` nightly; the older dated `Primed/` folder is still recognised and expired.

Discovery walks upward from an explicit `--vault`, or from cwd when omitted, choosing the nearest `brain.toml`. Without `--vault`, the vault is `<config repo>/<vault>`. With it, the given path is the vault root, while the discovered config supplies its layout. Without any config, defaults apply; the repository root falls back to Git's top level, or the explicit vault's parent / cwd when outside Git. No identity or setup interview is required by the engine.

## Hermes memory provider

The packaged `brain` provider supplies context without requiring the agent to choose a retrieval tool. At initialization it runs `brain session-context` once and caches the resulting system-prompt block for that session. CLI sessions and either ID in `owner_user_ids` receive only an owner identification. A third party is matched by whole-token IDs in `<people_dir>/*/Profile.md` bodies, not frontmatter. One match supplies the profile (at most 250 words, with an ellipsis when truncated and a full-path pointer), applicable person/group/anyone disclosure rows, and the default denial rule. Missing Disclosure.md is explicit. Oversized rule sets are not partially injected: the block requires opening Disclosure.md before any disclosure. Unknown and ambiguous identities receive the stranger warning; ambiguous matches are listed. Without platform or IDs, no block is injected. The session block stays around 400 words.

Before each API call, `prefetch` retrieves for the current user message when it contains at least two non-stopword tokens. It calls `brain retrieve --json --limit 6` with at most 300 query characters, returning note paths, tiers and reasons in a block capped at 1500 characters. It returns no block for no candidates or failures. Every subprocess has a five-second timeout. The provider adds no tools, stores no turns, and performs no network availability checks.

Injected text is **never attribution credit**, including automatically injected profiles and disclosure rules. Only notes the agent subsequently opens on demand are eligible for observed attribution under the usual exclusions; loading pointers does not count as use. Disclosure text is guidance for the agent, not a retrieval access-control filter.

From the vault repository, with `brain` on the gateway's PATH:

```sh
brain install-provider
hermes config set memory.provider brain
```

Ask the owner for their platform IDs and add `owner_user_ids = ["U_OWNER_ID"]` to the existing `[brain]` table, then restart the gateway. Verify with a non-owner Slack session or ask the agent what its provider block says. `brain paths provider` prints the installation directory. Hermes can use a different Python interpreter: the installed plugin imports no `brain` modules and uses only the standard library.

The plugin finds the repository from the session's `cwd`, then `VAULT_REPO`, then `$HOME/brain`, using the first location with a `brain.toml` (including parent directories). Each CLI subprocess runs with that repository as cwd so its configured vault layout applies. Set `HERMES_HOME` when installing into a non-default Hermes home. Installing or updating copies files only; enabling the provider and restarting remain explicit steps.

## Health

`brain health` prints an operational report built only from commits, engine state files and counts: the last run of each night stage with ok, stale or never (a stage is ok when its commit covers yesterday or later), installed against recorded engine version and any pending migration, yesterday's digest coverage line, validation problems, note counts by tier, open tasks, and the working tree against origin. `--json` gives the same as data; `--write` saves the committed copy to `<hermes_dir>/health.md` (without the working-tree line, so hourly refreshes only commit real change). The refresh script writes it every hour. An agent asked "how are you" by a maintainer answers from this report; nothing in it is a note's contents.

## Parallel operation boundary

Run only one nightly process per vault. The engine assumes it is the only writer during `--write`.

## Commands

`brain shard-sessions <export.jsonl> <sessions dir>` splits a full Hermes JSONL export into `jsonl/<session id>.jsonl`, rewriting only shards whose bytes changed, so a night's commit carries only the sessions that moved. The export script uses it instead of committing one large `all.jsonl`; the digest and attribution read the shards (and a legacy `all.jsonl` if one is still present).

**Session kinds.** Every exported session is classified as a conversation, a routine (a cron run: source or title begins `cron`), or an evaluation (title or source matches `Evaluation fixture`, `retrieval check` or `RC-nn`, or the first user message begins `Evaluation fixture:`). The digest includes only conversations unless `--include-routines` is passed; attribution ignores evaluation sessions entirely and takes only explicit citations, never observed opens, from routines. Paths under `<agent_dir>/Retrieval Checks/` never earn observed credit. The digest's `coverage:` line reports how many sessions of each kind were included or excluded and how many turns the word cap omitted.

`brain migrate-check` and `brain migrate-done` carry the agent's part of an upgrade: the package ships a Markdown prompt per version that needs one (see `src/brain/migrations/README.md`), `migrate-check` writes the applicable ones to `<hermes_dir>/pending-migration.md` after each nightly update, the recall skill makes the agent perform them, and `migrate-done` records the version. `<hermes_dir>/engine-version` is the recorded version, committed with the vault.

`brain update` upgrades the package from `update_source` using the interpreter that owns `brain`, then reinstalls the cron scripts and memory provider atomically per file. The export script runs it first every night, so a vault stays current without anyone touching the machine; a failed update is reported and the archive continues on the installed version. `brain paths python` prints that interpreter.

`brain session-ids <all.jsonl>` prints each session id once; the export script uses it to run one `hermes sessions export --session-id --force` per session, because the directory form of the Markdown export needs a filter and otherwise exits 0 without writing, and without `--force` a session exported before is skipped with a success status. The script fails if any per-session export fails or fewer Markdown files exist than sessions.

From the vault repository or any directory below it:

```sh
brain nightly --date 2026-10-10
brain nightly --date 2026-10-10 --write
brain nightly --date 2026-10-10 --write --commit --push
brain nightly --date 2026-10-10 --sessions-dir memory/Conversations/Sessions --write
brain validate
brain attribute --file <record.md> --ts <iso> [--reason <text>] [--write]
brain attribute --sessions-dir <dir> [--write]
brain digest --date YYYY-MM-DD [--sessions-dir <dir>] [--write] [--max-words 25000]
brain compile-context [--out <path>] [--write]
brain retrieve "harbor CI" [--limit 12] [--json]
brain session-context [--platform slack] [--user-id <id>] [--user-id-alt <id>] [--user-name <name>] [--chat-id <id>]
brain simulate [--h 7 --w 28]
brain install-skills
brain install-scripts
brain install-provider
brain paths <soul|skills|scripts|provider|vault|repo>
```

Every subcommand accepts optional `--vault <path>`. `python3 -m brain` is also supported. `brain paths` prints an absolute path: SOUL, rendered skills, installed scripts or provider directory, the active vault or repository root. `--sessions-dir`, `--file` and `--out` paths are relative to cwd, not the vault; adjust example session paths to your config.

`--date` is the Pacific date of the occurrence; `digest` requires it, while `nightly` allows omitting it for an ad hoc run. `attribute` prints explicit uses and JSONL observed on-demand reads, and `--write` appends them to monthly Use Logs. Rows are `timestamp | target | reason | explicit/inferred/observed/live [| confirmed/unconfirmed]`. `simulate` prints the spec's decay table.

`compile-context` prints generated context without writing unless `--write` is given. Its word count goes to stderr, with a non-fatal warning over 6000 words. Output defaults to the configured repository's `.hermes.md`; `--out` overrides this only when writing. Context includes frontmatter-free START, CURRENT and Triggers, only explicitly open task sections exactly as represented in the register, Hot Set's Automatic load section, and a names-only directory. Full archived warm-task bodies and overflow notes remain on demand.

`retrieve` is read-only and deterministic, scoring title/path, index-line and heading tokens with tier weighting, then spreading one hop through local links. Task and trigger IDs receive direct matches. Logs, dreams, primed notes and Conversations are not retrieval candidates. JSON output is a list, empty when nothing matches; text mode prints `no candidates`.

The session-export parser assumptions below are retained verbatim from the vendored engine documentation. Their literal default-layout examples (`memory/`, `agent/`, `../Conversations/Sessions`, and `vault.ORIENTATION`) now refer to the corresponding configured paths and orientation set. Name defaults now come from TOML before the environment overrides, and resolve per config load rather than module import. The installed commands run from the repository root, not `memory/.brain`.

### Session export assumptions

Sleep 1 passes `--sessions-dir ../Conversations/Sessions` after the nightly export. Attribution prefers `all.jsonl` (one object per session), even if empty, and otherwise recursively scans every `*.md` as UTF-8 (accepting a BOM and replacing undecodable bytes). It does not verify manifest hashes or interpret lineage; Markdown fallback adds no observed attribution. The exact export layouts remain unverified.

The Markdown parser is line-based, not a Markdown AST. Case-insensitive role markers include headings such as `## assistant` or `### Assistant`, bold names such as `**assistant**`, `assistant:`, and metadata such as `role: assistant` (also quoted or list-prefixed). The same forms recognize user/human, system, developer, tool and function roles; only the nearest preceding assistant marker permits a line whose first non-whitespace text is `Sources:`. If there are no recognized role markers anywhere in the file, all such Sources lines qualify. Sources text uses the existing explicit-use parser, including its orientation-note exclusion; surrounding prose is not inferred as reliance, and fenced examples are not specially interpreted.

Each row takes the nearest preceding valid ISO-8601 date-time in the file (minutes required, optional seconds/fraction/zone), otherwise the first valid YYYY-MM-DD in the filename, then the nearest dated parent directory, then the file's local mtime date. A date-time inside the Sources line itself is not preceding that line. The reason is `cited in session <file stem>`; rows are deduplicated within the export batch and against the relevant monthly Use Log by `(date, target, reason)`, regardless of timestamp time or source type. Re-exporting the same session stem is safe; renaming stems changes attribution identity. Review a report-only attribution against a real export before relying on a new export layout.

JSONL attribution reuses the digest's message/container, content and timestamp aliases below; timestamps are Pacific ISO date-times, inherited from the nearest preceding timestamped message, then session metadata, then the export filename/parent/mtime date. Assistant `Sources:` lines retain explicit parsing and `(date, target, reason)` deduplication, with reason `cited in session <id>`; IDs prefer `id`, then `session_id`, then the export filename and line number. Observed rows use `opened on demand in session <id>` and deduplicate by `(date, target, source=observed)` across all sessions and existing monthly logs, independent of session ID.

Observed attribution excludes `vault.ORIENTATION`, every linked note in Hot Set's current `## Automatic load` section, Hot Set and Warm Index themselves, and all paths under Conversations, agent/Dreams, agent/Explorations, agent/Use Log, agent/Sleep Log and agent/Tasks Archive. Loading is not use: automatic context and bookkeeping reads must not reinforce themselves just by being loaded.

Tool-call field names are assumptions, not a verified Hermes schema: assistant records are searched recursively through dict/list wrappers (including `tool_calls`, `function_call` and content tool blocks) for `arguments`, `args`, `input` or `parameters`, with names from `tool_name`, `name` or `tool`. Dict/list arguments and JSON-encoded strings are traversed recursively; any `memory/...md` path substring in an argument string counts as an opened note regardless of tool name (`read_file`, `search_files`, `execute_code`, `session_search`, MCP tools, etc.). Paths without spaces match `memory/[^"'\s]+\.md`; space-bearing names such as `memory/Harbor Context.md` are also accepted, ending at the first `.md` before a quote, newline or another `memory/` prefix. Only existing vault notes receive observed rows, and compacted calls and tool/function result messages never count; no success or semantic-reliance claim is inferred from a call.

For a named `terminal` call containing `brain retrieve`, shell quoting is parsed with `shlex` and the positional query is recorded instead of any reads from that call; `--vault`, `--limit`, their `=value` forms and `--json` are skipped. This is not a shell interpreter: malformed quoting yields an empty query, and dynamic shell expansion or unrecognized export argument fields are not reconstructed.

### Digest

`digest` is a separate command, not another step inside `nightly`. The default sessions directory is `<vault>/Conversations/Sessions`. It prefers `all.jsonl` from `hermes sessions export <file> --format jsonl`, even if empty; only when absent does it recursively read Markdown exports. Without `--write`, only the complete digest is printed to stdout. With `--write`, only the digest file is saved, and its path and retained conversation word count are printed. It never attributes uses, changes notes, or publishes anything.

Export field names remain unverified against a real Hermes export. The defensive JSONL assumptions are:

- Each nonblank line is one session object. Message arrays may be named `messages`, `history`, `turns` or `conversation`, at the root or under `session`, `metadata`, `data` or a `conversation` object; a wrapper's `metadata` is also checked. Nonobjects and malformed message entries are ignored; malformed JSON fails with the file and line number rather than silently losing turns. Files accept UTF-8 BOMs and replace undecodable bytes. No manifest or lineage deduplication is attempted; tool-call observations use the defensive parsing above.
- Message roles are case-insensitive `user` and `assistant` only. System, developer, tool and function messages are excluded. `compacted: true` (also `1` or their string equivalents) excludes a turn; `active: false` alone does not. String `content` is preserved verbatim, with `text` accepted when `content` is absent. Lists concatenate string blocks and `text`, `input_text` or `output_text` blocks without adding separators; missing block types with `text` are accepted, including a nested `text.value`. Tool and image blocks are ignored. Empty or unsupported content is omitted. Sources footers remain ordinary text, with whitespace untouched.
- Timestamps check `timestamp`, `created_at`, `created`, `ts`, `time`, `sent_at`, then `started_at`, `start_time`, `start`, `ended_at`, `end_time`, `end`, first on the message and then in `timings`. ISO-8601 strings and epoch seconds/milliseconds (including numeric strings; magnitude at least 100 billion means milliseconds) are accepted. Offset-free timestamps are assumed Pacific; aware timestamps use `zoneinfo`'s `America/Los_Angeles`, including DST. Invalid values are skipped. An undated message inherits the preceding usable timestamp, even from an excluded message. Leading undated messages are omitted if any message has a timestamp.
- Only sessions with no usable message timestamps can use a session-level range. Start aliases are `started_at`, `start_time`, `start`, `created_at`, `created`, `timestamp`, `ts`, `time`; end aliases are `ended_at`, `end_time`, `end`, `updated_at`, also checking session `timings`. Inclusion uses inclusive Pacific dates. A lone endpoint covers only its own date; missing or reversed ranges are omitted. Included turns receive the start time on its date, otherwise midnight, and a note explicitly marks these times approximate.
- Titles prefer `title`, `name`, `session_title`, then `id` or `session_id`; sources prefer string `source`, `platform` or `channel`. These are checked in the same wrappers, with headings normalized to one line. Missing metadata falls back to the export filename and JSONL line number (or Markdown relative path).

Markdown fallback reuses attribution's role and ISO timestamp recognition (including `human` as user), taking a preamble's first `#` heading as the title or otherwise the filename stem. Role-marker suffixes may carry inline content or timestamps; standalone or labeled timestamps before a turn's text are metadata. Fenced blocks and timestamps inside ordinary message bodies are preserved, not treated as metadata. Files with no recognized roles contribute nothing. When all message timestamps are missing, the filename/dated-parent/local-mtime date fallback is used with an approximate-time note. The parser is line-based; role-like lines outside code fences remain ambiguous, so review a real export before depending on its layout.

Turn labels use `BRAIN_HUMAN_NAME` (default `Human`) and `BRAIN_AGENT_NAME` (default `Agent`), resolved once when the script starts. These environment variables affect display labels only, not paths or export role parsing.

After each assistant turn's original text, `reads: <paths>` lists distinct tool-call note paths in encounter order since the preceding text-bearing assistant turn in that session; textless tool-call messages accumulate into the next such turn. Each retrieval adds `retrieve: "<query>"` on its own line, with JSON string escaping; tool results never add reads, and digest reads include automatic notes even though observed attribution excludes them.

Sessions are ordered by their first retained timestamp; turns within each session are chronological, with stable input-order ties. `--max-words` (default 25000, nonnegative) counts whitespace-separated words of retained message text only, excluding frontmatter, headings, turn labels, fallback notes, reads/retrieve annotations and the truncation notice. It selects a chronological prefix across all sessions, stopping before the first whole turn that would exceed the cap, then groups those turns into session sections. The header counts only represented sessions and retained conversation words; a final truncation line counts every remaining turn. The generated date is today's Pacific date: identical inputs on the same generation date produce identical bytes, and unchanged files are not rewritten; regeneration on a later day updates that header date.

## What `nightly` does, in order

**Digest (before the pass).** The nightly export script runs `brain digest --date YYYY-MM-DD --write` after exporting sessions. Sleep 1 reads `Conversations/Digests/YYYY-MM-DD.md` for consolidation before invoking `nightly`.

1. **Rollover.** Create this month's Use Log, Sleep Log and Tasks Archive files if missing.
2. **Frontmatter defaults.** Every mechanical note gets `kind`, `tier`, `pinned`, `uses`, `uses_inferred`; a new hub, profile, reflection, episode or primed note gets today as its first use. Logs, dreams, daily archives and the two generated views are left alone.
3. **Load tasks.** One `Task` per ID. A generated `- **Warm:**` line in the register is replaced by its archive body; a full register entry is canonical over the archive; a duplicate ID keeps its first entry. Archived open tasks the register does not list are loaded too.
4. **Attribute sessions, then fold uses.** When `--sessions-dir` is given, append new assistant Sources citations and JSONL observed reads to the relevant monthly Use Logs first. Every dated row in `agent/Use Log/*.md` is then credited to its target: explicit rows into `uses`, inferred and observed rows into `uses_inferred` (half weight), task citations into the register comment. An explicit note use supersedes an observed use on the same date, including previously folded observed credit; observed rows never credit tasks, which rely on citations or triggers. Rows are skipped when future-dated, `MISS`, a trigger, `live | unconfirmed`, or aimed at a log, dream or daily archive. Credits are date sets, so folding the same row twice changes nothing.
5. **Activation and tiers.** ACT-R base-level activation from the use dates; hot, warm or cold per the spec. Pinned stays hot, unconsolidated conversation records cannot go cold, an expired Primed note goes cold.
6. **Triggers.** Fire date commitments (a fired commitment adds a use to its task), mention once and then retire intentions whose source went cold.
7. **Write.** Save changed notes. Place each open task: hot as a full entry bounded to 150 words with older evidence archived, warm as one line with the full body in `agent/Tasks Archive/T-nnn.md`, cold as archive only and listed in Warm Index. Rebuild CURRENT's active-threads block. Regenerate Hot Set and Warm Index.
8. **Compile context.** After regenerating the views, rebuild the repository-root `.hermes.md` from the source notes and report its word count (warning, not failure, above 6000 words).
9. **Validate.** Required frontmatter, resolvable wikilinks on changed notes, pinned notes hot, superseded headers present, no open task archived without an index line. On failure: print, append a failed entry to the Sleep Log, exit 1, no commit.
10. **Record and publish.** Append the report to the Sleep Log. With `--commit`, stage the configured vault minus legacy `.brain` state plus repository-root `.hermes.md` and commit as `sleep1(<date>): <summary>`. With `--push`, `git pull --rebase origin main` then push; a conflict aborts the rebase and exits non-zero with nothing pushed.

Without `--write` the same pass runs on a temporary copy of the agent tree and only prints the report.

## Idempotence and interruption

Every step is a function of the files as they are. Running the same date twice produces the same files, apart from a second Sleep Log entry. A run killed halfway leaves some notes saved and others not; the next run converges, because nothing depends on a cursor, a marker or a prior receipt. If a run must be undone, `git revert` the commit.

## What it does not do

Read Gmail or any connector, capture conversations, judge salience or surprise, infer reliance, write facts, or decide anything about the owner. Those are the agent's judgment steps in the routine files. The script is the same every night; the judgment is logged and audited by Sleep 2.

## State

- Note frontmatter: `tier`, `activation`, `uses`, `uses_inferred`, `pinned`, `consolidated`, optional `index_line`.
- Register comments: `<!-- brain: status=open; uses=...; inferred=...; tier=...; activation=... -->` under each `### T-nnn` heading.
- Legacy `.brain/state.json` (not managed or required by the package): only `channels`, the Gmail acknowledged-coverage checkpoints described in [[memory/agent/Channels|Channels]]. The agent maintains it; the script never reads or writes it.

## Tests

```sh
python3 -m pytest -q
```

`tests/test_night.py` runs a whole night against a fixture vault with a bare local remote, reruns it to prove idempotence, kills it halfway and reruns, and drives one task hot → warm → hot → cold → hot asserting a single identity throughout.
