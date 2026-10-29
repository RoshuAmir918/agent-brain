# Migrations

One Markdown file per package version that needs the agent to do something after the upgrade: `<major>.<minor>.<patch>.md`. Most versions need nothing and ship no file.

After `brain update`, the export job runs `brain migrate-check`. It compares the installed version with the version recorded in `<vault>/<hermes_dir>/engine-version`, collects every migration file for versions in between, and writes them in order to `<vault>/<hermes_dir>/pending-migration.md`. The recall skill tells the agent to perform a pending migration in its next session, all of it: a step written for the owner is drafted from what the agent holds, marked unconfirmed with the date, and raised in the next briefing. `unattended: true` only says no such flag is needed. The agent finishes with `brain migrate-done`, which records the version and removes the file. With no migration files in range, `migrate-check` records the version itself.

A migration file is a prompt to the agent, not a script. Say what changed, what to check, what to edit, and how to verify. Keep it under 300 words. Start with a one-line frontmatter `unattended: true` when nothing in it needs the owner's confirmation; otherwise write the owner's steps so they can be drafted and flagged.

A step that edits a file Hermes protects (`SOUL.md`, the Hermes config) needs an approval answered live, which an unattended run cannot give. Write such a step for the owner: the agent drafts the change, adds an `ask` to CURRENT's `## Needs you`, and makes the edit in the session where the owner approves it. It never blocks `brain migrate-done`; a run that cannot complete a step must still finish every other step and mark the migration done.
