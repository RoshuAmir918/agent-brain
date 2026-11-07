#!/bin/bash
# Nightly session export. Deterministic, no model. Each outcome is separate: export, digest, local commit, publication, refresh.
# Conversations go to the vault; routine transcripts and every JSON shard, go to this
# profile's records under $AGENT_HOME, so each agent on a shared machine keeps its own and the vault holds only memory.
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
brain update || echo "export-sessions: brain update failed; continuing with the installed version" >&2
brain migrate-check || echo "export-sessions: migrate-check failed" >&2
VAULT="$(brain paths vault)"
OUT="$VAULT/Conversations/Sessions"
RECORDS="$(brain paths records)/sessions"
ZONE="$(brain timezone)"   # the owner's zone from brain.toml: "yesterday" is theirs, whatever the host clock says
if [ -n "${BRAIN_TODAY:-}" ]; then   # a pinned today, as the engine reads it
  DATE="$(date -j -v-1d -f %F "$BRAIN_TODAY" +%F 2>/dev/null || date -d "$BRAIN_TODAY -1 day" +%F)"
else
  DATE="$(TZ="$ZONE" date -v-1d +%F 2>/dev/null || TZ="$ZONE" date -d yesterday +%F)"
fi
mkdir -p "$OUT" "$RECORDS"
brain pull >/dev/null

brain export-sessions --date "$DATE"

brain digest --date "$DATE" --write
# Transcripts and digests are pushed: scrub pasted credentials (key shapes, secret-named assignments) first.
brain redact "$OUT" "$VAULT/Conversations/Digests" >/dev/null || { echo "export-sessions: redaction failed; nothing committed" >&2; exit 1; }
# Exports, digests, and engine state such as engine-version and a pending migration. The push publishes whatever is
# pending, including commits an earlier failed run left behind, and retries a large push before giving up.
if ! brain commit --push -m "archive($DATE): session export" -- "$OUT" "$VAULT/Conversations/Digests" "$(dirname "$(brain paths skills)")" >/dev/null; then
  echo "export-sessions: archive not committed or not published; will retry next run" >&2
  exit 1
fi
"$(dirname "$0")/refresh.sh"
