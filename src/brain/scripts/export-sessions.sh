#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
VAULT="$(brain paths vault)"
OUT="$VAULT/Conversations/Sessions"
DATE="$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)"
mkdir -p "$OUT"
git pull --rebase --autostash -q origin main
hermes sessions export "$OUT" --format md
hermes sessions export "$OUT/all.jsonl" --format jsonl
brain digest --date "$DATE" --write
git add "$OUT" "$VAULT/Conversations/Digests"
if ! git diff --cached --quiet; then
  git commit -q -m "archive($DATE): hermes session export"
  git push -q origin main
fi
"$(dirname "$0")/refresh.sh"
