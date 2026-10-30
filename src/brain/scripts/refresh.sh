#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
cd "$VAULT_REPO"
git pull --rebase --autostash -q origin main
brain compile-context --write
brain install-skills
brain health --write >/dev/null
SOUL="$(brain paths soul)"
mkdir -p "$HERMES_HOME"
if ! cmp -s "$SOUL" "$HERMES_HOME/SOUL.md"; then
  cp "$SOUL" "$HERMES_HOME/SOUL.md"
  echo "SOUL.md refreshed from the vault"
fi
git add .hermes.md "$(dirname "$(brain paths skills)")"
if ! git diff --cached --quiet; then
  git commit -q -m "context(${BRAIN_TODAY:-$(date +%F)}): refresh context and skills"
  git push -q origin main
fi
