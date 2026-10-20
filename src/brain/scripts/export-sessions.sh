#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
brain update || echo "export-sessions: brain update failed; continuing with the installed version" >&2
brain migrate-check || echo "export-sessions: migrate-check failed" >&2
VAULT="$(brain paths vault)"
OUT="$VAULT/Conversations/Sessions"
DATE="$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)"
mkdir -p "$OUT"
git pull --rebase --autostash -q origin main
# The jsonl export returns every session; the markdown export needs a filter, so export markdown per session id.
hermes sessions export "$OUT/all.jsonl" --format jsonl

# --force refreshes a session that was exported before and has grown since.
ids="$(brain session-ids "$OUT/all.jsonl")"
expected="$(printf '%s\n' "$ids" | grep -c .)"
failed=0
while IFS= read -r id; do
  [ -n "$id" ] || continue
  hermes sessions export "$OUT" --format md --session-id "$id" --force || { echo "export-sessions: markdown export failed for session $id" >&2; failed=1; }
done <<< "$ids"
written="$(find "$OUT" -name '*.md' | wc -l | tr -d ' ')"
if [ "$failed" -ne 0 ] || [ "$written" -lt "$expected" ]; then
  echo "export-sessions: expected $expected sessions, found $written markdown files" >&2
  exit 1
fi
brain digest --date "$DATE" --write
git add "$OUT" "$VAULT/Conversations/Digests"
if ! git diff --cached --quiet; then
  git commit -q -m "archive($DATE): hermes session export"
  git push -q origin main
fi
"$(dirname "$0")/refresh.sh"
