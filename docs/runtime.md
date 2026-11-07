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
| `responsibility` | optional responsibility id, matching `^[a-z0-9][a-z0-9-]{0,62}$`; explicitly assigns the routine to that hub; unknown ids are validation warnings |
| `needs` | default `[]`; `["computer"]` means the work needs the owner's computer (the harness's hands target); only `computer` is supported |

Set these with `brain schedule set <name> --responsibility <id> --needs computer`; clear them with `--responsibility "" --needs ""`, like clearing `--skills`. `brain schedule list --json` includes both fields (`null` and `[]` when absent). `set` refuses unknown needs; `brain validate` warns about them in a hand-edited file without failing validation. Responsibility ids and fallback links are described in [the engine guide](engine.md#responsibilities).

Reconciliation, by the runtime, at start and whenever the file's modification time or size changes (checked each scheduler tick):

- A routine in the file and not in the runtime's store is created. One in the store and not in the file is removed. One in both takes the file's definition and keeps its run state (last run, next run, the monitor's last hash, failure streak).
- An invalid file changes nothing: the runtime keeps the last valid set, logs `schedule: invalid <path>: <reason>; keeping jobs`, and the engine's `brain validate` names the problem. A valid but empty file removes every job.
- A one-shot (`at`) routine that already ran stays done even if its time is edited; declare a new name to run it again.
- The runtime records each job's state as in section 4, keyed by the routine's name.

Monitors: the runtime runs the monitor with the routine's environment and working directory, hashes its exact stdout, and skips the run when the hash equals the last one that led to a run. A monitor that exits non-zero is a failed run, never a change, and leaves the stored hash alone. When it does run, the monitor's stdout is given to the run as context.

Handed-off tasks run through background workers, outside the routine schedule. The runtime reads `brain continue --due --json` and starts one chunk of one task with "Run continue for T-nnn.". The default due list combines regular tasks within budget and daily tasks from 12:00 in the owner's timezone with `chunksToday == 0`, preserving each row's `pace` and ordering both paces least recently worked first. State, Delivers, Resume (including a later time or a computer rest) and review-cap gates still apply. A counted chunk removes a daily task from that list for the rest of the local day; regular work continues while due. The runtime keeps one worker per task and counts each chunk with `brain continue --chunk T-nnn`. `--daily` retains the legacy daily-only selection without the noon or one-chunk gate, in text and JSON. Text `--due` and `continue-signal` use the combined selection too. The default schedule has no `continue` or `continue-daily` routine; their legacy signal commands remain available, but validate and health require no continue heartbeat.

## 4. What the runtime records, and the engine reads

`$AGENT_HOME/records/continue/tasks/<id>.json`: the engine's per-task checkpoint, `{task, at, text}`, atomically replaced by `brain continue --checkpoint T-nnn --text "..."`. Both text and JSON due lists expose its text. The legacy `records/continue/state.json` is left untouched. `records/continue/progress.json` remains keyed by resolved vault path and task id; each record adds `chunks: {date, count}` for the owner's local day. Missing counts mean zero.

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

`brain tellable <person|known|anyone> [--json]` resolves the owner's tellable note for an audience; JSON is `{person, groups, subjects: {subject: {level, lines}}}`. Outside sessions (including intellects, resolved for their owners) carry those lines under `What you may tell <name> about <owner>, and nothing else:`, the format in `brain.session_context.TELLABLE_HEADING`. The runtime keys on that heading: such a session gets only its session context, no compiled context, prefetched notes or vault tools (`read_file`, `search_files`, `terminal`, `write_file`, `patch`). Tools that reach the owner remain available. The resolved lines are capped at 600 words, dropping oldest evidence first, and say `Nothing yet.` when empty. Owner, maintainer, enterprise member and routine sessions have no such block; enterprise intellect sessions do. Legacy and missing disclosure notes still produce the block, resolved as `anyone`.

Before each owner message, in parallel: `brain people-mentioned --form <f> -- <message>` and `brain retrieve --json --limit <n> [--form <f>] -- <message>`.

After a conversation goes quiet: `brain record-spec --json` (the record call's system prompt, instructions, JSON schema and transcript cap), `brain needs-you --json` and `brain loops --json` for the record's context, then the model call, then `brain journal append` with the record on stdin, and `brain ledger append --json` after a routine.

From the owner's app: `brain needs-you --dismiss <id> no|stop-asking`, `brain task <id> drop|done`, `brain ideas --respond <id> yes|not-now|no|stop-asking`, and `brain state --json` for the push.

The runtime gives the agent these tools besides its six workspace tools: `demiurge_tell_owner`, `demiurge_message`, `demiurge_today`.

### A routine's context

A routine's skill may declare, in its frontmatter's `metadata.runtime`, the compiled-context sections its run needs: `context: [start, current, tasks, library]`, from `before`, `start`, `current`, `primed`, `goals`, `triggers`, `upcoming`, `tasks`, `library`, `directory`. The runtime then loads `brain compile-context --sections <those>` for that run instead of the whole `.context.md`; an empty list means no compiled context. A skill without the declaration gets the whole context. The engine owns these declarations, since it owns the routines; a vault's own skills may declare them too. Requested sections remain within the vault's `[context].sections` allowlist; an explicit list must include `library` to load it.

## 6. Changes

- 0.63.0: the tellable note. `brain tellable <person|known|anyone> [--json]` returns only the owner's sourced facts at the resolved gist/detail level. Outside session contexts carry the fixed `TELLABLE_HEADING` block; the runtime uses it to withhold compiled context, prefetch and vault tools, keeping tools that reach the owner.
- 0.49.1: every open task keeps a view. A task whose result has no shape shows its own plan (steps or a checklist from its Delivers, Next and blockers, and links to its work); the recall skill asks for one at creation, and a migration fills in the open tasks that have none.
- 0.49.0: background workers take daily tasks too. Default text and JSON continue due lists and `continue-signal` include daily tasks from noon local until their first counted chunk that day, with existing gates and ordering. `--daily` stays compatible. Default schedules drop `continue` and `continue-daily`; an unattended migration removes both routines and refreshes skills. Validate and health no longer check their monitor heartbeats.

- 0.48.1: a migration has each intellect write a view for every open task whose result has a shape, from what the task and its notes already hold, since views were only chosen at handoff and no existing task had one.
- 0.48.0: schedule routines add `responsibility` and `needs`; `brain state --json` adds schedule-based `responsibilities` and each existing routine row's responsibility id. State schema remains 1.
- 0.36.4: the watch skill starts each pass with `brain channels mail --since <checked_at>` and resolves Needs you asks and loops that new mail settles (`brain needs-you --resolve`, `brain loops --close`, with the message as evidence); its state file records `checked_at`; it lists its commands so a pass spends no calls on `--help`. Installed skills update on `brain update`.
- 0.48.0: members. In an enterprise vault, any speaker the runtime names (not a maintainer, another intellect or a routine) gets a member block: "This session is with <name>, a member of <organisation>." with the team-visibility policy and that their work is theirs; the operator (`owner_user_ids` or cli) gets "a member of <organisation> and its operator." and may change the setup. Members count as owners in the journal and are not access-logged. Runtimes that detect owner sessions by their first line should also accept ", a member of ".
- 0.47.1: task views. Table rows and places take `waiting` (sent, awaiting a reply) and `blocked`; places take `value`, `state` and `link`, so located candidates are one piece. The continue skill writes a view for a task that has none when its result has a shape, so tasks handed off before views existed get one.
- 0.47.0: per-task owners. With `edition = "enterprise"`, `brain handoff` records the person a task is for on its handoff line (`· for <platform>:<id>`), from `--for` or the runtime's `BRAIN_SPEAKER`, kept through later handoffs and report changes. Needs you items carry `owner` (the task's), `brain needs-you --for PERSON` filters by it, `brain state --json` tasks carry `owner`, and `brain continue --engaged T --by PERSON` (default `BRAIN_SPEAKER`) counts only the owner's engagement. Personal vaults never record an owner.
- 0.46.1: task views. A number's trend may say `since` when it starts, and the kit and the recall skill say each thing appears in one piece, with counts that match what they count.
- 0.46.0: editions. `edition = "personal" | "enterprise"` in `[brain]`. Enterprise installs no morning, people or prime skill (removing earlier renders), drops shared skills' `<!-- personal -->` parts (disclosure grants, goals, Working together, people candidates) for an organisation's version, leaves goals and primed out of its context, and does not expect the people and prime stages in health or an owner profile in validation. `brain schedule init` adds the edition's default routines without touching existing ones. Personal vaults are unchanged.
- 0.45.0: task views v2. The kit adds `gallery` (https images with label, value, state, link), `steps` (ordered stages, the current one marked) and `schedule` (times and what happens); a number may carry a `trend` sparkline series, and any piece a `from` line saying where its data came from. `brain state --json` tasks add `said`, the owner's words from a quoted Source.
- 0.43.0: task views. `brain task T-nnn view` keeps a view of a task as data in the app's kit (numbers, progress, table, checklist, places, links), checked against the kit and carried by `brain state --json` as each task's `view`. The recall skill says when a task gets one (a result with a shape) and the continue skill to update its data when it changes, not each chunk.
- 0.41.0: task brevity. `brain task set` refuses a field write that would leave a task's current fields over 150 words and larger than before ("say it shorter"); writes that shrink an entry, and `--reviewed`/`--state`/`--clear`/`--title`, are always taken. `brain task new` refuses a task that starts over the limit. The continue skill asks for a sentence or two per field and `--evidence` with the chunk's note after every chunk, so the night can keep the entry hot and move older evidence to the archive.
- 0.39.0: task integrity. The night processes the task register under the vault lock on a fresh read (a worker's write made during the night is kept; the night's frontmatter saves keep every note's body as it is on disk), and an entry it cannot bound without judgment is left as is and reported instead of stopping the night; older Reviewed lines may move to the archive. A closed task leaves the register at night: its entry to `Tasks Archive/T-nnn.md`, one line to that month's file. An open handed-off task stays in the register however cold its uses. A `###` subheading inside an entry is part of it. `brain task T-nnn reopen --why`, `set --title`, `set --work <slug>` (attach a work without restarting the handoff), `set --evidence` (the Latest evidence line), `set --clear handoff` (take a task back from its worker). `waiting_for_owner` needs an Ask. A task leaving `waiting_for_owner`, or closing, takes the Needs you asks that stood for it with it (recorded as answered). Inside a turn, `done` and `drop` need `--why`; flags a close would ignore are refused. `stale-tasks` counts a continue chunk as a touch. `brain state --json` tasks add `ask`, `why`, `verified`, `evidence`, `resume`, `report`, `lastChunk`, `chunksToday`, `budget`, `closed` and `outcome`, and `blockers` reads the Blocker line.
- 0.38.1: follow-ons are held to a high bar: offered only when the goal is still open or a step would uniquely help the owner that they would not have thought to ask for; never an invented need or a routine next step.
- 0.38.0: follow-ons: `brain completed [--days N] [--json]` lists recently completed tasks; continue's Done step and the improve routine ask whether what a finished task served is still open, or an extension would help, and offer it as one idea (an offer, never a new task).
- 0.37.1: a new task's id follows the highest task that exists (register and archive headings), not the highest id any note mentions: a note naming "T-999" had left `brain task new` and accepted ideas with no id.
- 0.37.0: a task's daily chunk budget follows its owner's engagement: whole on a day they engaged (a handoff, a task changed in a conversation rather than by a worker, `brain continue --engaged T-nnn` from the runtime when they write in its thread), halved for each day without, down to one; `$AGENT_HOME/records/continue/engaged.json` holds the times, and `--due --json` gives each task's `budget`. `brain needs-you --ask`, `brain task set --ask` and a recorded `needsYou` refuse an ask that does not name its subject ("approve the proposed action").
- 0.36.6: a failed pull or push says what failed (no credentials, no network) instead of calling everything a conflict; only a stopped rebase is reported as one.
- 0.36.5: `brain commit -m MESSAGE [--push] [PATH ...]` and `brain pull` take a per-repository lock, and `brain nightly --commit --push` uses it too, so parallel runs never run git in one vault at once; the routines commit and pull only through them.
- 0.36.3: notes are written through a temporary file and an atomic rename (a racing writer had padded a register with NUL bytes); `brain validate` reports a note holding NUL bytes; `brain task set --morning TEXT` adds a note for the next briefing and `--clear morning` removes them; `brain task new` places a task before the register's standing-state section.
- 0.36.2: `brain task T-nnn set --resume "YYYY-MM-DD[ HH:MM]"` rests a task until that local time (`--due` and the continue monitor leave it out until then); `set --report <where>` changes only a handoff's report target; recall records an owner's instruction in a task's own thread on the task.
- 0.36.1: the continue skill takes `Run continue for T-nnn.` as a worker's prompt (one chunk of one task, a one-line result, milestones only); with `DEMIURGE_WORKERS=1` in a turn's environment, `brain handoff` tells the turn a worker will start and not to begin the work itself.
- 0.36.0: all task-writing commands hold the vault lock; `brain task T-nnn set` edits open task fields and `brain task new` creates entries with the shared idea allocator. Per-task continue checkpoints live in `records/continue/tasks/<id>.json`. `brain continue --due --json` exposes ordered scheduling state, checkpoints and chunk counts, returning `[]` with exit 0 when none. Regular tasks wait after 16 chunks per local day, configurable with `[continue] daily_chunks` or `BRAIN_DAILY_CHUNKS`; daily pace is unaffected. Existing continue routines and legacy state files remain compatible; no migration is required.

- 0.29.0: this page; `schedule.toml` and `brain schedule`; `brain record-spec`; `AGENT_HOME`.

- 0.30.0: `Runtime/` and `runtime_dir`; `.context.md`; native harness `brain export-sessions` and JSON shards; removed the old runtime plugin, cron and session-store adapters.

- 0.31.0: only `AGENT_HOME` and `runtime_dir` are supported; removed the folder migration command and compatibility aliases. Maintainer commands work without a runtime; updates skip script reinstalls when `AGENT_HOME` is unset.
- 0.32.0: a routine's skill declares its context sections; `brain compile-context --sections`.
- 0.33.0: the runtime writes `works/LIBRARY.md` and `records/works/state.json`; the engine reads the `library` context section and counts a task's saved work as progress.
- 0.35.0: `brain continue --due` lists the handoff worked least recently first (a task never worked before all of them), from the progress record, so one long task no longer takes every regular pass; recall never says work is underway after its reply ends and names the next pass instead.
- 0.34.1: closing, completing or changing a warm task updates its register line as well as its archive, so a closed warm task no longer reads as open.
- 0.34.0: conversation closures carry `loop`, `outcome` and `why` (legacy strings still apply); loop additions validate known counterparts and refuse the owner; vault drafts and reflections no longer count as continue progress.
