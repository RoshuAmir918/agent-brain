# Agent instructions

This repository is a long-term memory for a personal AI agent. If you are an agent reading this, you are either the agent that owns it or a guest. Either way, read `memory/START.md` first and follow its rules; `sources/` is read-only; write only under `memory/`.

## If this brain is not set up yet

You will know because `memory/agent/SOUL.md` still has bracketed placeholders and `memory/agent/Tasks.md` has only T-001. Set it up now, with the owner present. Do these in order and say what you are doing as you go.

1. **Ask the owner, in a conversation, not a form:** what they want to call you; their name; their timezone and roughly when they wake and sleep; the channel they will mostly talk to you on; what they want you for, in their words; and two or three things about how they like to be spoken to. Keep their answers verbatim where they are specific.
2. **Write what you learned:** fill the bracketed parts of `memory/agent/SOUL.md` and `memory/human/Profile.md`; write one dated record `memory/Conversations/<today> First conversation.md` quoting their answers; set the main channel row in `memory/agent/Channels.md`.
3. **Set the environment** the scripts read: `VAULT` (this repo's path), `BRAIN_HUMAN_NAME`, `BRAIN_AGENT_NAME`, and `HERMES_HOME` if it is not `~/.hermes`.
4. **Install:** copy `memory/hermes/refresh.sh` and `memory/hermes/export-sessions.sh` into `~/.hermes/scripts/`; set `skills.external_dirs` to this repo's `memory/hermes/skills`; set `sessions.auto_prune` to false; set the platform home channel to the owner's DM. The exact config keys are in `memory/hermes/README.md`.
5. **Generate:** `cd memory/.brain && python3 -m brain nightly --vault ../ --write` once, then run `~/.hermes/scripts/refresh.sh`. That writes the real `.hermes.md` over this bootstrap and copies SOUL into the Hermes home.
6. **Schedule:** create the cron jobs from `memory/hermes/README.md`, adjusting hours to the owner's sleep. Create one, check `hermes cron list` shows the next run in the owner's timezone, then the rest.
7. **Commit** everything you wrote with a message like `setup: first conversation and identity`, and tell the owner what will happen tonight and what to check in the morning.

Ask before running anything that needs their credentials or sends a message anywhere. Do not invent facts about the owner to fill a section; leave it as "unknown" and ask when it matters.

## Once it is set up

Use the `brain-recall` skill for retrieval and small daytime writes. The night routines own structure. Never edit `sources/`, SOUL, the skills, Triggers' rules or the Memory Spec from a routine; those change only when the owner asks.
