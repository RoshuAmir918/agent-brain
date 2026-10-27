#!/bin/bash
# Nightly session export. Deterministic, no model. Each outcome is separate: export, digest, local commit, publication, refresh.
# Conversations go to the vault; the agent's own routine and evaluation sessions, and every JSONL shard, go to this
# profile's records under $HERMES_HOME, so each agent on a shared machine keeps its own and the vault holds only memory.
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
brain update || echo "export-sessions: brain update failed; continuing with the installed version" >&2
brain migrate-check || echo "export-sessions: migrate-check failed" >&2
VAULT="$(brain paths vault)"
OUT="$VAULT/Conversations/Sessions"
RECORDS="$(brain paths records)/sessions"
ZONE="${BRAIN_TZ:-$(brain timezone)}"   # the owner's zone from brain.toml: "yesterday" is theirs, whatever the host clock says
DATE="$(TZ="$ZONE" date -v-1d +%F 2>/dev/null || TZ="$ZONE" date -d yesterday +%F)"
mkdir -p "$OUT" "$RECORDS"
git pull --rebase --autostash -q origin main

# The jsonl export returns every session; the markdown export needs a filter, so export markdown per session id,
# with --force so a session that grew since last night is refreshed.
# The full export goes to a scratch file and is split into one shard per session under the records folder; only
# shards that changed are rewritten.
EXPORT="$(mktemp "${TMPDIR:-/tmp}/sessions.XXXXXX.jsonl")"
hermes sessions export "$EXPORT" --format jsonl
brain shard-sessions "$EXPORT" "$RECORDS"
kinds="$(brain session-ids "$EXPORT" --with-kind)"
rm -f "$EXPORT"
failed=0
while IFS="$(printf '\t')" read -r id kind; do
  [ -n "$id" ] || continue
  case "$kind" in conversation) target="$OUT" ;; *) target="$RECORDS" ;; esac
  hermes sessions export "$target" --format md --session-id "$id" --force || { echo "export-sessions: markdown export failed for session $id" >&2; failed=1; }
  if ! find "$target" -maxdepth 1 -name "*${id}*.md" | grep -q .; then
    echo "export-sessions: no markdown file for session $id" >&2; failed=1
  fi
done <<< "$kinds"
if [ "$failed" -ne 0 ]; then
  echo "export-sessions: export incomplete; nothing committed" >&2
  exit 1
fi

brain digest --date "$DATE" --write
git add "$OUT" "$VAULT/Conversations/Digests" "$(dirname "$(brain paths skills)")"   # exports, digests, and engine state such as engine-version and a pending migration
if ! git diff --cached --quiet; then
  git commit -q -m "archive($DATE): hermes session export"
fi

# Publish whatever is pending, including commits an earlier failed run left behind. Large pushes have hit HTTP 400;
# retry once with a bigger post buffer before giving up.
publish() {
  git push -q origin HEAD:main && return 0
  sleep 5
  git -c http.postBuffer=52428800 push -q origin HEAD:main && return 0
  sleep 15
  git -c http.version=HTTP/1.1 -c http.postBuffer=104857600 push -q origin HEAD:main
}
git fetch -q origin main
if [ -n "$(git rev-list origin/main..HEAD)" ]; then
  if ! publish; then
    echo "export-sessions: archive committed locally but not published; will retry next run" >&2
    exit 1
  fi
fi
"$(dirname "$0")/refresh.sh"
