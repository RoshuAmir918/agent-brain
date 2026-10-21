# agent-brain

The memory engine for a personal AI agent: a Python package that maintains a Markdown vault as long-term memory. Decay by use, observed attribution, surprise-gated writes, commitment triggers, a compiled session-start context, a daily digest of conversations, retrieval, and a night of routines, with the routines shipped as Hermes Agent skills.

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
| `brain nightly --write --commit --push` | the deterministic half of the night: fold uses, recompute activation and tiers, place tasks, fire triggers, rebuild the views and `.hermes.md`, validate, commit |
| `brain attribute --sessions-dir <dir>` | observed and explicit use rows from a Hermes session export |
| `brain digest --date <day> --write` | one file of that day's turns between owner and agent, with what the agent read before each reply |
| `brain retrieve "<query>"` | deterministic candidates: index-line scoring, one-hop spread over links, cold grep |
| `brain compile-context --write` | the session-start context file |
| `brain install-skills` / `install-scripts` | render the routines into the vault; put the cron scripts in the Hermes scripts dir |
| `brain update` / `migrate-check` / `migrate-done` | nightly self-upgrade and the agent's part of a migration |
| `brain validate`, `simulate`, `paths`, `session-ids` | checks, the decay table, resolved paths, session ids from an export |

`docs/engine.md` has every command, the config keys, what the nightly pass does in order, and the assumptions the session-export parsers make. The memory model itself is documented in the template's `memory/agent/Memory Spec.md`.

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
