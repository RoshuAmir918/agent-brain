#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
brain update || echo "upgrade: brain update failed; continuing with the installed version" >&2
brain migrate-check || echo "upgrade: migrate-check failed" >&2
brain install-skills
brain roles template check >/dev/null || echo "upgrade: role template check failed" >&2
brain commit --push -m "upgrade(${BRAIN_TODAY:-$(TZ="$(brain timezone)" date +%F)}): refresh engine and skills" -- "$(dirname "$(brain paths skills)")" "$(brain paths agent)/Memory Spec.md" >/dev/null
