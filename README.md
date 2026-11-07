# agent-brain

The memory engine for a personal AI agent: a Python package that maintains a Markdown vault as long-term memory. Decay by use, observed attribution, surprise-gated writes, commitment triggers, a compiled session-start context, a daily digest of conversations, retrieval, and a night of routines, with the routines shipped as runtime skills.

To get a vault, start from the [agent-brain-template](https://github.com/RoshuAmir918/agent-brain-template) repository. This repo is the engine that vault installs and updates itself from.

## Install

```sh
python3 -m pip install git+https://github.com/RoshuAmir918/agent-brain
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
| `brain digest --date <day> --write` | one file of that day's turns between owner and agent, with what the agent read before each reply |
| `brain retrieve "<query>"` | deterministic candidates: index-line scoring, one-hop spread over links, cold grep |
| `brain goals new` / `list` / `show` / `G-n edit` / `evidence` / `reached` / `set-aside` / `reopen` | the person's goals, progress and the work toward them |
| `brain compile-context --write` | the session-start context file |
| `brain install-skills` / `install-scripts` | render the routines into the vault; put the cron scripts in `$AGENT_HOME/scripts` |
| `brain update` / `migrate-check` / `migrate-done` | nightly self-upgrade and the agent's part of a migration |
| `brain validate`, `simulate`, `paths` | checks, the decay table, resolved paths |

`docs/engine.md` has every command, the config keys, what the nightly pass does in order, and session archive formats. The [runtime contract](docs/runtime.md) defines `AGENT_HOME`, `Runtime/schedule.toml`, `.context.md` and harness sessions. `brain export-sessions` archives harness conversations and routine runs. The memory model itself is documented in the template's `memory/agent/Memory Spec.md`.

## Layout

```
src/brain/            the package: one module per concern, stdlib plus PyYAML
src/brain/skills/     the routine templates, rendered into a vault by install-skills
src/brain/scripts/    the two cron scripts, installed by install-scripts
src/brain/migrations/ per-version prompts for the agent when an upgrade needs one
docs/                 engine reference and the example config
tests/                pytest; a whole night against fixture vaults, in both layouts
```

Core routines (attribute, sleep1, sleep2, rem, recall) are re-rendered into every vault on each refresh; the day routines (morning, watch, midday, evening, people, explore) are written once and then belong to the vault. To change a core routine, fork this repo and point your vault's `update_source` at the fork.

## Development

```sh
git clone https://github.com/RoshuAmir918/agent-brain && cd agent-brain
python3 -m pip install -e '.[test]'
python3 -m pytest -q
```

Never run `brain nightly --write` against a vault you have not committed. Bump the version in `pyproject.toml` on every change that reaches vaults; the nightly updater compares versions to decide whether a migration prompt applies.

MIT.
