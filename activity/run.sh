#!/bin/bash
# Entry point for activity-rate monitoring. Usage:
#   ./run.sh login        first run: open a visible browser and log in to Monitask by hand (saves the profile)
#   ./run.sh fetch        fetch once headlessly (skipped automatically outside the work window) — used by launchd
#   ./run.sh fetch force  fetch once headlessly, skipping the window gate (for testing)
#   ./run.sh now          send the current activity rate right now (same as fetch force)
#   ./run.sh record       save today's rate for `monitask-center --config` only (no Telegram, any hour)
HERE="$(cd "$(dirname "$0")" && pwd)"

# Prefer Homebrew Node so the Full Disk Access grant maps to the program the schedule actually runs.
NODE="${MONITASK_ACTIVITY_NODE:-/opt/homebrew/opt/node/bin/node}"
[ -x "$NODE" ] || NODE="/usr/local/opt/node/bin/node"
[ -x "$NODE" ] || NODE="/opt/homebrew/bin/node"
[ -x "$NODE" ] || NODE="/usr/local/bin/node"
[ -x "$NODE" ] || NODE="$HOME/.nvm/versions/node/v22.23.1/bin/node"
[ -x "$NODE" ] || NODE="$(command -v node)"
[ -x "$NODE" ] || { echo "找不到 node"; exit 1; }

# Load the Telegram and activity settings from the main program's .env (install.sh merges the checkout's .env
# into it): the launchd job may not read a checkout under Documents.
ENV_FILE="${MONITASK_CENTER_HOME:-$HOME/.monitask-center}/.env"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
# A Keychain token (saved by install / --config) wins over .env, the same order monitask_center.telegram uses.
KEYCHAIN_TOKEN="$(/usr/bin/security find-generic-password -s monitask-center -a telegram_bot_token -w 2>/dev/null)"
[ -n "$KEYCHAIN_TOKEN" ] && export TG_BOT_TOKEN="$KEYCHAIN_TOKEN"
unset KEYCHAIN_TOKEN

case "${1:-}" in
  login) exec "$NODE" "$HERE/login.js" ;;
  fetch) shift; exec "$NODE" "$HERE/fetch.js" "$@" ;;
  now) exec "$NODE" "$HERE/fetch.js" force ;;
  record) exec "$NODE" "$HERE/fetch.js" record ;;
  *) echo "usage: $0 {login | fetch [force] | now | record}" >&2; exit 2 ;;
esac
