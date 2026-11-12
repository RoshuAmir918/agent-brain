# agent-brain

The memory engine for a personal AI agent: a Python package that maintains a Markdown vault as long-term memory. Decay by use, observed attribution, surprise-gated writes, commitment triggers, a compiled session-start context, a daily digest of conversations, retrieval, and a night of routines, with the routines shipped as runtime skills.

To get a vault, start from the [agent-brain-template](https://github.com/demiurge-lab/agent-brain-template) repository. This repo is the engine that vault installs and updates itself from.

## Install

```sh
python3 -m pip install git+https://github.com/demiurge-lab/agent-brain
brain --help
```

Python 3.11 or newer. The only dependency is PyYAML.

## How a vault uses it

A vault is a git repo with a `brain.toml` naming its folders and the two display names (see `docs/brain.toml.example`). From anywhere in that repo:

| Command | Does |
| --- | --- |
| `brain nightly --write --commit --push` | the deterministic half of the night: fold uses, recompute activation and tiers, place tasks, fire triggers, rebuild the views and `.context.md`, validate, commit |
| `brain attribute --sessions-dir <dir>` | observed and explicit use rows from harness session archives |
| `brain export-sessions [--date <day>]` | archive harness session transcripts and native JSON shards |
| `brain schedule list` / `set` / `pause` / `resume` / `remove` | manage the declared routines in `Runtime/schedule.toml` |
| `brain responsibility add` / `adopt` / `sync` / `status <id> <active\|paused\|retired> [--reason "<why>"]` | manage standing work; its document controls whether it runs |
| `brain digest --date <day> --write` | one file of that day's turns between owner and agent, with what the agent read before each reply |
| `brain retrieve "<query>"` | deterministic candidates: index-line scoring, one-hop spread over links, cold grep |
| `brain goals new` / `list` / `show` / `due` / `G-n edit` / `evidence` / `reached` / `superseded-by` / `asked-missed` / `set-aside` / `reopen` / `keep` / `not-a-goal` / `to-task` | the person's stated or evidenced goals, progress and the work toward them |
| `brain compile-context --write` | the session-start context file |
| `brain install-skills` / `install-scripts` | render the routines into the vault; put the cron scripts in `$AGENT_HOME/scripts` |
| `brain update` / `migrate-check` / `migrate-done` | 09:00 self-upgrade and the agent's part of a migration |
| `brain validate`, `paths` | checks, resolved paths |

`docs/engine.md` has every command, the config keys, what the nightly pass does in order, and session archive formats. The [runtime contract](docs/runtime.md) defines `AGENT_HOME`, `Runtime/schedule.toml`, `.context.md` and harness sessions. `brain export-sessions` archives harness conversations and routine runs. The memory model itself is documented in the template's `memory/agent/Memory Spec.md`.

When the owner says stop, end, drop or pause a responsibility, use `brain responsibility status` (`retired` ends, `paused` pauses, `active` resumes). Never delete its folder or hand-edit triggers to end it.

Goals are inferred from repeated, dated evidence as well as stated by the person. `brain goals new "<title>" --words "<their quoted words>" --said YYYY-MM-DD --inferred --evidence "<date> · <what> · [[note]]"` records a reading; repeat `--evidence` for its sources. The nightly People pass alone infers goals and merges existing aims, read `brain goals refused --json` and keep at most five active inferred goals, with at most two new ones per night. Silence keeps them; thirty days without grounded evidence flags them stale for People to judge. Both origins fund preparation immediately, held, under existing outward permissions. Keep adopts a reading, Not a goal archives and blocks its return, and To task creates asked work and prints its task id. Nobody is asked whether something is a goal. See [the engine's goals reference](docs/engine.md#goals).

## Layout

```
src/brain/            the package: one module per concern, stdlib plus PyYAML
src/brain/skills/     the routine templates, rendered into a vault by install-skills
src/brain/scripts/    the cron and signal scripts, installed by install-scripts
src/brain/migrations/ per-version prompts for the agent when an upgrade needs one
docs/                 engine reference and the example config
tests/                pytest; a whole night against fixture vaults, in both layouts
```

Every routine, and the Memory Spec, is the engine's: each refresh re-renders it into the vault and overwrites any edit to its body. A vault's own additions go in a `## Local` section at the end of a rendered skill, which is carried over verbatim; that is the only customization point. To change a routine itself, fork this repo and point the vault's `update_source` at the fork.

## Development

```sh
git clone https://github.com/demiurge-lab/agent-brain && cd agent-brain
python3 -m pip install -e '.[test]'
python3 -m pytest -q
```

Never run `brain nightly --write` against a vault you have not committed. Bump the version in `pyproject.toml` on every change that reaches vaults; the nightly updater compares versions to decide whether a migration prompt applies.

MIT.
