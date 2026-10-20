#!/bin/bash
# Deterministic refresh of what Hermes reads at session start. No model.
# Run hourly from a script-only cron job, and at the end of the nightly session export.
# Pulls the vault, regenerates .hermes.md from the source notes, syncs SOUL into the
# Hermes home, and commits the context file only when it changed.
set -eu
VAULT="${VAULT:-$HOME/orph}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
cd "$VAULT"
git pull --rebase --autostash -q origin main
( cd memory/.brain && python3 -m brain compile-context --vault ../ --write )
if ! cmp -s memory/agent/SOUL.md "$HERMES_HOME/SOUL.md"; then
  cp memory/agent/SOUL.md "$HERMES_HOME/SOUL.md"
  echo "SOUL.md refreshed from the vault"
fi
git add .hermes.md
if ! git diff --cached --quiet; then
  git commit -q -m "context($(date +%F)): refresh .hermes.md"
  git push -q origin main
fi
