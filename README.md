# agent-brain

A long-term memory for a personal AI agent, kept as Markdown in git. Clone it, point [Hermes Agent](https://github.com/nousresearch/hermes-agent) at it, and the agent sets itself up from the instructions inside.

It follows the [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) pattern (your sources, an agent-maintained wiki, a schema file, an index, a log) and adds the part that pattern lacks: a memory model that decides what the agent loads, what fades, and what it never forgets.

## What you get

- **Working memory backed by long-term memory.** A small hot set is in every session. Everything else is one retrieval away, and the agent is told what exists so it knows to look.
- **Decay that measures use, not loading.** Every note has an activation score computed nightly from when the agent actually relied on it. Notes you talk about stay hot; notes you don't fade to an index line, then to search-only. Nothing is deleted.
- **Attribution that watches, not asks.** Hermes records every tool call, so the night sees which notes the agent opened before each reply and judges which it relied on. No citation discipline required of the agent.
- **Only surprises get written.** A fact the vault already states earns a use, not a copy. A correction from you supersedes in place. A conflicting third-party claim is kept beside the original and raised in the morning.
- **Commitments that never decay.** "When X, do Y" lives in a pinned Triggers note and fires when the world matches it, however old.
- **A night that does the housekeeping.** Export, attribute, consolidate, dream, audit. Each stage is one git commit, so `git log` tells you whether it ran and `git revert` undoes it.
- **A day of routines you can keep or delete.** Morning briefing, hourly watch, midday and evening register checks, a daily people review, and a proactive explore pass that picks one thread from your recent conversations and pushes it forward.
- **Registries for people and for your other agents.** One sourced profile each, decaying like any note.

## Layout

```
.hermes.md              generated session-start context (bootstrap instructions until the first refresh)
AGENTS.md               the same instructions for Claude Code, Codex and other agents
sources/                your own notes; read-only to every agent
memory/                 the agent's wiki
  START.md              the schema: routing, retrieval, read and write rules
  CURRENT.md            present context and dated pointers
  Hot Set.md            generated: what loads automatically
  Warm Index.md         generated: one line per warm note
  <Topic>.md            one hub per project or area of life, created as they appear
  agent/                SOUL, Tasks, Triggers, Memory Spec, Channels, Retrieval Checks, logs, dreams, reflections
  human/Profile.md      the agent's profile of you
  people/               profiles of people in your life
  agents/               profiles of your other agents
  Conversations/        dated records, plus the nightly session export and daily digests
  hermes/               deployment note, two scripts, one skill per routine
  .brain/               the Python script: activation, tiers, tasks, triggers, views, context, digest, retrieval, attribution
```

## Quickstart

1. Clone to a stable path and install the two Python dependencies:
   ```sh
   git clone <this repo> ~/brain && cd ~/brain
   python3 -m pip install pyyaml pytest
   (cd memory/.brain && python3 -m pytest -q)
   ```
2. Start Hermes from the repo root: `hermes`. The bootstrap `.hermes.md` tells the agent it is looking at a fresh brain and walks it through setup: it asks you a few questions, fills in its identity and your profile, installs its skills and scripts, and creates the cron jobs. Say yes when it asks to run commands, and answer the questions in your own words; they become its first memories.
3. The next morning, check `git log --oneline --since=yesterday`. You should see `archive`, `attribution`, `sleep1`, `rem` and `sleep2` commits. Read `memory/Conversations/Digests/<date>.md` once by eye, and read the briefing.

If you would rather do the setup by hand, or you use a different agent runtime, `memory/hermes/README.md` has every step and `memory/agent/Memory Spec.md` has the model. Nothing in the vault depends on Hermes except the two scripts and the skill format.

## Make it yours

- Edit `memory/agent/SOUL.md` for voice. The refresh script copies it into Hermes within the hour.
- Delete routines you do not want: remove the skill folder and its cron line. Add one by copying a skill and giving it a cron line.
- Put your existing notes in `sources/`, or symlink your Obsidian vault there. The agent reads them and never edits them.
- Open the vault in Obsidian. The wikilinks and graph work as-is.

## The model in one paragraph

Every note carries a tier (hot, warm, cold), a list of dates it was relied on, and an activation score. Activation is ACT-R base-level activation with exponent 1, calibrated so one use stays hot about a week and warm about a month, and daily use for a month stays hot most of a year. Hot notes load every session, warm notes are listed by one line in an index, cold notes are search-only. Retrieval spreads one hop over wikilinks. Writes go through a surprise gate. Open commitments and pinned notes never decay. The script does the arithmetic and refuses to commit if the vault is inconsistent; the agent does the judgment and its judgment is audited the next night. See `memory/agent/Memory Spec.md`.

## Status

Built in October 2026 for one person's assistant and generalised. The session-export parsers were written against the documented Hermes export formats, not a sample of every version; if digests come out empty, the field-name assumptions are listed in `memory/.brain/README.md`. Issues and pull requests welcome.

MIT.
