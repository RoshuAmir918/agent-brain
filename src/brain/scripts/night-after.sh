#!/bin/bash
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
brain nightly --date "$DATE" --write --commit --push --stage after
