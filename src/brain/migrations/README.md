# Migrations

One Markdown file per package version that needs the agent to do something after the upgrade: `<major>.<minor>.<patch>.md`. Most versions need nothing and ship no file.

After `brain update`, the 09:00 upgrade job runs `brain migrate-check`. It compares the installed version with the version recorded in `<vault>/<runtime_dir>/engine-version`, collects every migration file for versions in between, and writes them in order to `<vault>/<runtime_dir>/pending-migration.md`. When that file is new or changed and a schedule is declared, it adds a one-shot `brain-migration-<version>` routine two minutes out to `schedule.toml`, so the runtime starts the migration instead of waiting for the agent's next session. `brain migrate-done` removes these routines. The recall skill tells the agent to perform a pending migration in its next session, all of it: a step written for the owner is drafted from what the agent holds, marked unconfirmed with the date, and raised in the next briefing. `unattended: true` only says no such flag is needed. The agent finishes with `brain migrate-done`, which records the version and removes the file. With no migration files in range, `migrate-check` records the version itself; a vault with no recorded version is new, so it records the installed version and queues nothing.

Once every live vault has recorded a version, the files at or below it can be deleted.

A migration file is a prompt to the agent, not a script. Say what changed, what to check, what to edit, and how to verify. Keep it under 300 words. Start with a one-line frontmatter `unattended: true` when nothing in it needs the owner's confirmation; otherwise write the owner's steps so they can be drafted and flagged.

A step may edit `SOUL.md` or the runtime config: the migration is the engine's instruction, written by the people who ship it, so the agent does it unattended. No step waits for a person.

A step that waits on a future event ("when the owner says Slack is switched off, remove the Slack ids") is not a step of the migration. Write it as a `when` loop the migration opens (`brain loops --add … --kind when --if "<the event>"`), so `brain migrate-done` never waits on it.

The inferred-goals migration (0.82.0) is unattended: refresh skills with `brain install-skills` and run one evidenced inference pass immediately, including vaults whose goal files are empty. Read existing and refused goals, merge the same aim, keep at most five active inferred goals and add at most two per 24 hours, counting readings already made today in the register and archive. Never ask a capture question or wait for confirmation; silence keeps a reading. Preparation is held and outward permissions stay as given.
