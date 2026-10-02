#!/bin/bash
# launchd entry point: run monitask.py with the python copy inside MonitaskCenter.app.
# It must go through this shell wrapper (not launchd exec'ing the bundle binary directly):
# otherwise LaunchServices starts it as an app and the script never runs; the AX identity
# still belongs to the bundle, so the Accessibility grant applies.
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE/src:$HERE/pylibs"
# Load Telegram settings (TG_BOT_TOKEN / TG_CHAT_ID) for tg-send; if absent notify falls back to macOS notifications
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a
exec "$HERE/MonitaskCenter.app/Contents/MacOS/python-runner" "$HERE/monitask.py" "$@"
