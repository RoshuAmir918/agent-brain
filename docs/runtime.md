# The runtime contract

What agent-brain needs from whatever runs the agent, and what it gives back. The Demiurge harness (`demiurge/apps/harness`) is the runtime. Nothing in the engine may assume more about its runtime than this page says. When the contract changes, this page changes first, in the same release, and the change is named in `CHANGELOG`-style prose at the end.

## 1. Who owns what

| | Owned and written by | Read by |
| --- | --- | --- |
| The vault (notes, skills, SOUL, the schedule) | the engine and the agent | the runtime, read-only, except the files the agent writes through its tools |
| `<vault folder>/<runtime_dir>/schedule.toml` (beside `engine-version`), the declared routines | the engine (`brain schedule`), the agent through that command | the runtime |
| `<vault repo>/.context.md`, the compiled session context | the engine (`brain compile-context --write`) | the runtime |
| `$AGENT_HOME/records/`, routine and engine state (except `works/state.json` below) | the engine and the agent | the engine |
| `$AGENT_HOME/harness/`, run state: jobs, runs, sessions | the runtime | the engine, read-only |
| `$AGENT_HOME/works/LIBRARY.md`, available works and notices | the runtime | the engine, read-only |
| `$AGENT_HOME/records/works/state.json`, work sync state | the runtime | the engine, read-only |

`<runtime_dir>` is `brain.toml`'s `runtime_dir`, default `Runtime`. This folder holds the schedule, skills and engine state; `.context.md` lives at the vault repository root.

## 2. Environment

Every process the runtime starts for an agent (its shell, a routine's script, a brain call) gets:

- `VAULT_REPO`: the vault's git repository, also the working directory.
- `AGENT_HOME`: the agent's workspace root, where `records/`, `harness/` and `works/` live. The engine reads only `AGENT_HOME`. Without it, runtime-dependent paths report `AGENT_HOME is not set`; `brain version`, vault paths and `brain validate` still work. `brain update` installs the package and prints `scripts not reinstalled: AGENT_HOME is not set`.
- A `PATH` holding `brain` and `demiurge`.

No credential is ever in this environment beyond what `agents/<id>.json`'s `env` names for that agent.

## 3. The schedule: declared by the vault

`<vault folder>/<runtime_dir>/schedule.toml` (beside `engine-version`) is the single source of truth for which routines an agent has and how each runs. The runtime reconciles its jobs to it; it never edits the file. The agent never edits the runtime's job store; it changes a routine with `brain schedule`, which edits this file and validates it.

```toml
# The routines this agent runs. Edit with `brain schedule`; the runtime applies changes within a minute.

[routines.watch]
cron = "*/15 6-22 * * *"        # five-field cron, in the owner's zone (`brain timezone`)
skills = ["tars-watch"]
prompt = "Run watch."
deliver = "demiurge"            # "local" (default), "demiurge" (the owner's Home), or "demiurge:<conversation id>"
monitor = "watch-signal.sh"     # optional; see below

[routines.demiurge-sync]
every = "15m"                   # instead of cron: "<n>m" or "<n>h"
script = "demiurge-sync.sh"
agent = false                   # a script only, no model run

[routines.career-questions]
at = "2026-11-05T06:30:00-05:00"   # instead of cron: once
skills = ["tars-recall"]
prompt = "Send Rohan the morning questions."
owner = true                    # asked for by the owner by name; only they change or remove it
```

Fields, per routine (the table name is the routine's name, `[a-z0-9-]+`, unique):

| Field | Meaning |
| --- | --- |
| `cron`, `every`, `at` | exactly one: when it runs |
| `skills` | skills loaded for the run, by name, from `brain paths skills` or the agent's own skills folder |
| `prompt` | the run's instruction; default `Run <name>.` |
| `deliver` | where the run's final response goes, default `local` (nowhere) |
| `monitor` | a script under `$AGENT_HOME/scripts/`; the run happens only when its stdout changed since the last run that happened |
| `script` | a script under `$AGENT_HOME/scripts/` run before the agent, its stdout given to the run; with `agent = false`, the whole job |
| `agent` | default `true` |
| `workdir` | default `$VAULT_REPO` |
| `enabled` | default `true`; `false` keeps the routine declared but paused |
| `owner` | default `false`; `true` marks a routine the owner asked for by name |

Reconciliation, by the runtime, at start and whenever the file's modification time or size changes (checked each scheduler tick):

- A routine in the file and not in the runtime's store is created. One in the store and not in the file is removed. One in both takes the file's definition and keeps its run state (last run, next run, the monitor's last hash, failure streak).
- An invalid file changes nothing: the runtime keeps the last valid set, logs `schedule: invalid <path>: <reason>; keeping jobs`, and the engine's `brain validate` names the problem. A valid but empty file removes every job.
- A one-shot (`at`) routine that already ran stays done even if its time is edited; declare a new name to run it again.
- The runtime records each job's state as in section 4, keyed by the routine's name.

Monitors: the runtime runs the monitor with the routine's environment and working directory, hashes its exact stdout, and skips the run when the hash equals the last one that led to a run. A monitor that exits non-zero is a failed run, never a change, and leaves the stored hash alone. When it does run, the monitor's stdout is given to the run as context.

## 4. What the runtime records, and the engine reads

`$AGENT_HOME/works/LIBRARY.md`: a `# Library` heading, then one line per work, `- Title | slug | form | status | editable|read-only | ISO updated`, followed by plain-sentence notices about refusals, conflicts or errors. Each work is a git repository with a working copy at `$AGENT_HOME/works/<slug>/`. The runtime refreshes the listing on every run. The engine's `library` context section strips the title, includes up to forty work lines and keeps every notice; it is silently absent without `AGENT_HOME`, the file, or any works or notices.

`$AGENT_HOME/records/works/state.json`: a map from work slug to `{lastPull?, lastPush?, lastCommit?: {hash, when}, refusal?, conflict?, error?}`, with ISO timestamps. `_library` is reserved, not a work. The runtime writes it; the engine reads successful pushes as progress for a task naming that work. A local commit without a push does not count, and missing or unreadable state is no progress.

`$AGENT_HOME/harness/jobs.json`: a JSON array of jobs (a reader also accepts `{"jobs": [...]}`), each job `{id, name, schedule, skills, prompt, script?, noAgent?, monitorScript?, monitorHash?, deliver, workdir?, enabled, nextRunAt?, lastRunAt?, lastStatus?: "ok" | "failed" | "skipped", lastError?, failureStreak?}`. The engine reads it through `brain.runtime.jobs()` and nowhere else.

`$AGENT_HOME/harness/runs.jsonl`: one line per run, `{id, jobId, status, startedAt, finishedAt?, outcome?: "delivered" | "silent" | "skipped" | "local", error?, sessionId?}`.

`$AGENT_HOME/harness/sessions/<YYYY-MM-DD>/<session id>.json`, one per conversation or routine run:

```text
{ id, day, speaker: {platform, userId?, userIdAlt?, userName?, chatId?}, startedAt, timezone, system, sessionBlock,
  owner: bool, peopleSeen: [...], entries: [{at, item, said?}], recorded: int, usage: {...} }
```

`item` is one of `{type: "message", role: "user" | "assistant", text}`, `{type: "tool_call", id, name, arguments}`, `{type: "tool_result", callId, output}`, `{type: "reasoning", ...}`. `speaker.platform` is `cron` for a routine run. `said` is what the person typed when `text` carries added context. The night's digest and attribution read these files directly.

## 5. What the runtime calls

At the start of a conversation, in parallel: `brain paths soul`, `brain paths skills`, `brain compile-context --write` (then reads `.context.md`), `brain timezone`, `brain session-context --platform --user-id [--user-id-alt] [--user-name] [--chat-id]`.

Before each owner message, in parallel: `brain people-mentioned --form <f> -- <message>` and `brain retrieve --json --limit <n> [--form <f>] -- <message>`.

After a conversation goes quiet: `brain record-spec --json` (the record call's system prompt, instructions, JSON schema and transcript cap), `brain needs-you --json` and `brain loops --json` for the record's context, then the model call, then `brain journal append` with the record on stdin, and `brain ledger append --json` after a routine.

From the owner's app: `brain needs-you --dismiss <id> no|stop-asking`, `brain task <id> drop|done`, `brain ideas --respond <id> yes|not-now|no|stop-asking`, and `brain state --json` for the push.

The runtime gives the agent these tools besides its six workspace tools: `demiurge_tell_owner`, `demiurge_message`, `demiurge_today`.

### A routine's context

A routine's skill may declare, in its frontmatter's `metadata.runtime`, the compiled-context sections its run needs: `context: [start, current, tasks, library]`, from `before`, `start`, `current`, `primed`, `goals`, `triggers`, `upcoming`, `tasks`, `library`, `directory`. The runtime then loads `brain compile-context --sections <those>` for that run instead of the whole `.context.md`; an empty list means no compiled context. A skill without the declaration gets the whole context. The engine owns these declarations, since it owns the routines; a vault's own skills may declare them too. Requested sections remain within the vault's `[context].sections` allowlist; an explicit list must include `library` to load it.

## 6. Changes

- 0.29.0: this page; `schedule.toml` and `brain schedule`; `brain record-spec`; `AGENT_HOME`.

- 0.30.0: `Runtime/` and `runtime_dir`; `.context.md`; native harness `brain export-sessions` and JSON shards; removed the old runtime plugin, cron and session-store adapters.

- 0.31.0: only `AGENT_HOME` and `runtime_dir` are supported; removed the folder migration command and compatibility aliases. Maintainer commands work without a runtime; updates skip script reinstalls when `AGENT_HOME` is unset.
- 0.32.0: a routine's skill declares its context sections; `brain compile-context --sections`.
- 0.33.0: the runtime writes `works/LIBRARY.md` and `records/works/state.json`; the engine reads the `library` context section and counts a task's saved work as progress.
- 0.34.1: closing, completing or changing a warm task updates its register line as well as its archive, so a closed warm task no longer reads as open.
- 0.34.0: conversation closures carry `loop`, `outcome` and `why` (legacy strings still apply); loop additions validate known counterparts and refuse the owner; vault drafts and reflections no longer count as continue progress.
