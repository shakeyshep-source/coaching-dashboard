#!/usr/bin/env bash
# Run this ONCE on the laptop to package the Garmin token store for
# GitHub Actions:
#
#   bash export_garmin_tokens.sh
#
# Copy the entire output block, then in GitHub:
#   repo -> Settings -> Secrets and variables -> Actions
#   -> New repository secret -> name: GARMIN_TOKENS_B64 -> paste -> save.
#
# The underlying Garmin token lasts about a year. When the daily pull
# starts failing with auth errors, log in once on the laptop (any
# garmin_pull.py run refreshes the store) and re-run this script to
# update the secret.

set -euo pipefail

TOKEN_DIR="${1:-$HOME/.garmin_tokens}"

if [ ! -d "$TOKEN_DIR" ]; then
  echo "ERROR: $TOKEN_DIR not found." >&2
  echo "Run garmin_pull.py once on this machine first so the token store exists." >&2
  exit 1
fi

echo "--- copy everything between the lines into the GARMIN_TOKENS_B64 secret ---"
tar -czf - -C "$(dirname "$TOKEN_DIR")" "$(basename "$TOKEN_DIR")" | base64 | tr -d '\n'
echo
echo "--- end ---"
