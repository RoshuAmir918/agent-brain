#!/bin/bash
# The runtime runs monitors with the job's vault repo as cwd; VAULT_REPO is an explicit override.
set -eu
cd "${VAULT_REPO:-$PWD}"
exec brain explore-signal
