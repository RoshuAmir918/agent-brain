#!/bin/bash
# The runtime runs pre-run scripts with the job's vault repo as cwd; VAULT_REPO is an explicit override.
set -eu
cd "${VAULT_REPO:-$PWD}"
exec brain evening-signal
