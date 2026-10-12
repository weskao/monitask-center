#!/bin/bash
# Entry point for activity-rate monitoring. Usage:
#   ./run.sh login        first run: open a visible browser and log in to Monitask by hand (saves the profile)
#   ./run.sh fetch        fetch once headlessly (skipped automatically outside the work window) — used by launchd
#   ./run.sh fetch force  fetch once headlessly, skipping the window gate (for testing)
HERE="$(cd "$(dirname "$0")" && pwd)"

# Prefer Homebrew Node so the Full Disk Access grant maps to the program the schedule actually runs.
NODE="${MONITASK_ACTIVITY_NODE:-/opt/homebrew/opt/node/bin/node}"
[ -x "$NODE" ] || NODE="/usr/local/opt/node/bin/node"
[ -x "$NODE" ] || NODE="/opt/homebrew/bin/node"
[ -x "$NODE" ] || NODE="/usr/local/bin/node"
[ -x "$NODE" ] || NODE="$HOME/.nvm/versions/node/v22.23.1/bin/node"
[ -x "$NODE" ] || NODE="$(command -v node)"
[ -x "$NODE" ] || { echo "找不到 node"; exit 1; }

# Load the Telegram settings (reuses the clock-in .env: bot token + group chat_id)
[ -f "$HERE/../.env" ] && set -a && . "$HERE/../.env" && set +a

case "${1:-}" in
  login) exec "$NODE" "$HERE/login.js" ;;
  fetch) shift; exec "$NODE" "$HERE/fetch.js" "$@" ;;
  *) echo "usage: $0 {login | fetch [force]}" >&2; exit 2 ;;
esac
