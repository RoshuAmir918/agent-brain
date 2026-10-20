#!/bin/bash
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
brain update || echo "export-sessions: brain update failed; continuing with the installed version" >&2
VAULT="$(brain paths vault)"
OUT="$VAULT/Conversations/Sessions"
DATE="$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)"
mkdir -p "$OUT"
git pull --rebase --autostash -q origin main
# The jsonl export returns every session; the markdown export needs a filter, so export markdown per session id.
hermes sessions export "$OUT/all.jsonl" --format jsonl

brain session-ids "$OUT/all.jsonl" | while IFS= read -r id; do
  hermes sessions export "$OUT" --format md --session-id "$id"
done
if [ "$(find "$OUT" -name '*.md' | wc -l)" -eq 0 ]; then
  echo "export-sessions: no markdown sessions were written; check hermes sessions export --session-id" >&2
  exit 1
fi
brain digest --date "$DATE" --write
git add "$OUT" "$VAULT/Conversations/Digests"
if ! git diff --cached --quiet; then
  git commit -q -m "archive($DATE): hermes session export"
  git push -q origin main
fi
"$(dirname "$0")/refresh.sh"
