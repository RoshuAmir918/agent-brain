# Migrations

One Markdown file per package version that needs the agent to do something after the upgrade: `<major>.<minor>.<patch>.md`. Most versions need nothing and ship no file.

After `brain update`, the export job runs `brain migrate-check`. It compares the installed version with the version recorded in `<vault>/<hermes_dir>/engine-version`, collects every migration file for versions in between, and writes them in order to `<vault>/<hermes_dir>/pending-migration.md`. The recall skill tells the agent to perform a pending migration in its next session, with the owner present unless the file says `unattended: true`, and to finish with `brain migrate-done`, which records the version and removes the file. With no migration files in range, `migrate-check` records the version itself.

A migration file is a prompt to the agent, not a script. Say what changed, what to check, what to edit, and how to verify. Keep it under 300 words. Start with a one-line frontmatter `unattended: true` only when nothing in it needs a human decision.
