#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
: "${AGENT_HOME:?AGENT_HOME is not set}"
cd "$VAULT_REPO"
git pull --rebase --autostash -q origin main
brain people sync --run >/dev/null 2>&1 || true
brain compile-context --write
brain install-skills
brain health --write >/dev/null
SOUL="$(brain paths soul)"
mkdir -p "$AGENT_HOME"
if ! cmp -s "$SOUL" "$AGENT_HOME/SOUL.md"; then
  cp "$SOUL" "$AGENT_HOME/SOUL.md"
  echo "SOUL.md refreshed from the vault"
fi
git add .context.md "$(dirname "$(brain paths skills)")"
if ! git diff --cached --quiet; then
  git commit -q -m "context(${BRAIN_TODAY:-$(date +%F)}): refresh context and skills"
  git push -q origin main
fi
