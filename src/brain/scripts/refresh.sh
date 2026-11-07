#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
: "${AGENT_HOME:?AGENT_HOME is not set}"
cd "$VAULT_REPO"
git pull --rebase --autostash -q origin main
brain people sync --run >/dev/null 2>&1 || true
LAPSED="$(brain consult lapse 2>/dev/null || true)"
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
if [ -n "$LAPSED" ]; then
  AGENT_DIR="$(brain paths agent)"
  for path in "$AGENT_DIR/Consults.md" "$AGENT_DIR/Areas" "$AGENT_DIR/Answered" "$AGENT_DIR/Tasks.md" "$AGENT_DIR/Tasks Archive"; do
    if [ -e "$path" ]; then
      git add "$path"
    fi
  done
fi
if ! git diff --cached --quiet; then
  git commit -q -m "context(${BRAIN_TODAY:-$(date +%F)}): refresh context and skills"
  git push -q origin main
fi
