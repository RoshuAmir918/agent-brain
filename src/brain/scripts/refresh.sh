#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
: "${AGENT_HOME:?AGENT_HOME is not set}"
cd "$VAULT_REPO"
brain pull >/dev/null
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
PATHS=(.context.md "$(dirname "$(brain paths skills)")")
if [ -n "$LAPSED" ]; then
  AGENT_DIR="$(brain paths agent)"
  for path in "$AGENT_DIR/Consults.md" "$AGENT_DIR/Areas" "$AGENT_DIR/Answered" "$AGENT_DIR/Tasks.md" "$AGENT_DIR/Tasks Archive"; do
    if [ -e "$path" ]; then
      PATHS+=("$path")
    fi
  done
fi
# Pushes every run, so a commit an earlier failed push left behind is published now.
brain commit --push -m "context(${BRAIN_TODAY:-$(TZ="$(brain timezone)" date +%F)}): refresh context and skills" -- "${PATHS[@]}" >/dev/null
