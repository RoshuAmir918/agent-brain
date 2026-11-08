#!/bin/bash
# Nightly attribution. Deterministic, no model: Use Log rows for the notes last night's sessions opened on demand and
# for any Sources line, from this profile's session records. Rows are deduplicated, so a rerun adds nothing.
set -eu
VAULT_REPO="${VAULT_REPO:-$HOME/brain}"
cd "$VAULT_REPO"
ZONE="$(brain timezone)"
if [ -n "${BRAIN_TODAY:-}" ]; then
  DATE="$(date -j -v-1d -f %F "$BRAIN_TODAY" +%F 2>/dev/null || date -d "$BRAIN_TODAY -1 day" +%F)"
else
  DATE="$(TZ="$ZONE" date -v-1d +%F 2>/dev/null || TZ="$ZONE" date -d yesterday +%F)"
fi
brain pull >/dev/null
ROWS="$(brain attribute --write | wc -l | tr -d ' ')"
if [ "$ROWS" -gt 0 ]; then
  brain commit --push -m "attribution($DATE): $ROWS rows" -- "$(brain paths agent)/Use Log" >/dev/null
fi
