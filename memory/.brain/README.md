# .brain — the nightly memory script

One idempotent pass, run by Sleep 1, that does the arithmetic half of remembering. Git records snapshots: a local commit can remain unpublished, and a clean working tree proves only no local tracked changes. Distinguish prepared work, unpublished commits, published main bytes and independently verified occurrence coverage. Reconcile processed files/rows and existing candidates before resuming publication; neither a clean tree nor a missing local commit proves a night complete or unprocessed. There are no receipts, markers, manifests or transaction plans. Design and the judgment half live in [[memory/agent/Memory Spec|Memory Spec]]; day-to-day operation in [[memory/agent/Operator Guide|Operator Guide]].

## Parallel operation boundary

Run only one nightly process per vault. The script assumes it is the only writer during `--write`.

## Commands

The vault root passed as `--vault` is the repository's `memory/` directory. Within it, agent-owned notes live in `agent/`, people profiles in `people/<First_Last>/Profile.md`, and the agent-side profile of the owner in `human/Profile.md`. The repository-root `sources/` directory holds read-only human source notes; sibling `Archive/` links are also supported. `hermes/README.md` is an orientation note, and `skills` directories are skipped as notes. Generated wikilinks use the `memory/` prefix.

From `memory/.brain`:

```sh
python3 -m brain nightly   --vault ../ --date 2026-10-10            # report only, on a temp copy
python3 -m brain nightly   --vault ../ --date 2026-10-10 --write    # apply
python3 -m brain nightly   --vault ../ --date 2026-10-10 --write --commit --push
python3 -m brain nightly   --vault ../ --date 2026-10-10 --sessions-dir ../Conversations/Sessions --write
python3 -m brain validate  --vault ../
python3 -m brain attribute --vault ../ --file <record.md> --ts <iso> [--reason <text>] [--write]
python3 -m brain attribute --vault ../ --sessions-dir <dir> [--write]
python3 -m brain digest    --vault ../ --date YYYY-MM-DD [--sessions-dir ../Conversations/Sessions] [--write] [--max-words 25000]
python3 -m brain compile-context --vault ../ [--out <path>] [--write]
python3 -m brain retrieve  --vault ../ "harbor CI" [--limit 12] [--json]
python3 -m brain simulate  [--h 7 --w 28]
```

`--date` is the Pacific date of the occurrence; `digest` requires it, while `nightly` allows omitting it for an ad hoc run. `attribute` prints the explicit uses a record cites, plus observed on-demand reads from JSONL sessions, and, with `--write`, appends them to the month's Use Log. Use Log rows are `timestamp | target | reason | explicit/inferred/observed/live [| confirmed/unconfirmed]`. `simulate` prints the decay table from the spec.

`compile-context` prints the generated context to stdout without writing unless `--write` is given. Its word count goes to stderr, with a non-fatal warning over 6000 words. The default output is `.hermes.md` at `git rev-parse --show-toplevel`, falling back to the parent of the memory directory; `--out` overrides it only when writing. Context includes frontmatter-free START, CURRENT and Triggers, only explicitly open task sections exactly as displayed in the register (not expanded warm archives), and only Hot Set's Automatic load section.

`retrieve` is read-only and makes no model calls. Query tokens are lowercase punctuation-separated terms of at least three characters, minus common English stopwords; task IDs, trigger IDs and ISO dates stay intact. Distinct term hits score 3 in title/path, 2 in the index line and 1 in second-level headings, multiplied by hot/warm/cold factors 1.5/1.0/0.6, with no pin bonus. Notes linked from the top five positive metadata matches receive one +1 bonus, without another hop. Remaining zero-score notes get exact body-token matches worth 0.5 per distinct term, capped at 1.5. Explicit task sections and trigger rows score 5 for an ID match. Logs, dreams, primed notes, Conversations and the generated views are excluded from note search. Ties sort by path and reason; `--json` emits objects with path, tier, score and reason (an empty list when nothing matches), while text mode prints `no candidates`.

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

**Digest (before the pass).** The nightly export script runs `python3 -m brain digest --vault ../ --date YYYY-MM-DD --write` after exporting sessions. Sleep 1 reads `Conversations/Digests/YYYY-MM-DD.md` for consolidation before invoking `nightly`.

1. **Rollover.** Create this month's Use Log, Sleep Log and Tasks Archive files if missing.
2. **Frontmatter defaults.** Every mechanical note gets `kind`, `tier`, `pinned`, `uses`, `uses_inferred`; a new hub, profile, reflection, episode or primed note gets today as its first use. Logs, dreams, daily archives and the two generated views are left alone.
3. **Load tasks.** One `Task` per ID. A generated `- **Warm:**` line in the register is replaced by its archive body; a full register entry is canonical over the archive; a duplicate ID keeps its first entry. Archived open tasks the register does not list are loaded too.
4. **Attribute sessions, then fold uses.** When `--sessions-dir` is given, append new assistant Sources citations and JSONL observed reads to the relevant monthly Use Logs first. Every dated row in `agent/Use Log/*.md` is then credited to its target: explicit rows into `uses`, inferred and observed rows into `uses_inferred` (half weight), task citations into the register comment. An explicit note use supersedes an observed use on the same date, including previously folded observed credit; observed rows never credit tasks, which rely on citations or triggers. Rows are skipped when future-dated, `MISS`, a trigger, `live | unconfirmed`, or aimed at a log, dream or daily archive. Credits are date sets, so folding the same row twice changes nothing.
5. **Activation and tiers.** ACT-R base-level activation from the use dates; hot, warm or cold per the spec. Pinned stays hot, unconsolidated conversation records cannot go cold, an expired Primed note goes cold.
6. **Triggers.** Fire date commitments (a fired commitment adds a use to its task), mention once and then retire intentions whose source went cold.
7. **Write.** Save changed notes. Place each open task: hot as a full entry bounded to 150 words with older evidence archived, warm as one line with the full body in `agent/Tasks Archive/T-nnn.md`, cold as archive only and listed in Warm Index. Rebuild CURRENT's active-threads block. Regenerate Hot Set and Warm Index.
8. **Compile context.** After regenerating the views, rebuild the repository-root `.hermes.md` from the source notes and report its word count (warning, not failure, above 6000 words).
9. **Validate.** Required frontmatter, resolvable wikilinks on changed notes, pinned notes hot, superseded headers present, no open task archived without an index line. On failure: print, append a failed entry to the Sleep Log, exit 1, no commit.
10. **Record and publish.** Append the report to the Sleep Log. With `--commit`, stage `memory` minus `.brain` plus repository-root `.hermes.md` and commit as `sleep1(<date>): <summary>`. With `--push`, `git pull --rebase origin main` then push; a conflict aborts the rebase and exits non-zero with nothing pushed.

Without `--write` the same pass runs on a temporary copy of the agent tree and only prints the report.

## Idempotence and interruption

Every step is a function of the files as they are. Running the same date twice produces the same files, apart from a second Sleep Log entry. A run killed halfway leaves some notes saved and others not; the next run converges, because nothing depends on a cursor, a marker or a prior receipt. If a run must be undone, `git revert` the commit.

## What it does not do

Read Gmail or any connector, capture conversations, judge salience or surprise, infer reliance, write facts, or decide anything about the owner. Those are the agent's judgment steps in the routine files. The script is the same every night; the judgment is logged and audited by Sleep 2.

## State

- Note frontmatter: `tier`, `activation`, `uses`, `uses_inferred`, `pinned`, `consolidated`, optional `index_line`.
- Register comments: `<!-- brain: status=open; uses=...; inferred=...; tier=...; activation=... -->` under each `### T-nnn` heading.
- `.brain/state.json`: only `channels`, the Gmail acknowledged-coverage checkpoints described in [[memory/agent/Channels|Channels]]. The agent maintains it; the script never reads or writes it.

## Tests

```sh
cd memory/.brain && python3 -m pytest -q
```

`tests/test_night.py` runs a whole night against a fixture vault with a bare local remote, reruns it to prove idempotence, kills it halfway and reruns, and drives one task hot → warm → hot → cold → hot asserting a single identity throughout.
