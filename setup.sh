#!/bin/bash
# Interactive first-time setup for Monitask Center.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/.env"
INSTALLER="$HERE/install.sh"
RUNNER="$HERE/run.sh"
PYTHON_RUNNER="$HERE/MonitaskCenter.app/Contents/MacOS/python-runner"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This setup requires macOS." >&2
  exit 1
fi

say() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

confirm() {
  local answer
  while true; do
    read -r -p "$1 [Y/n] " answer
    case "${answer:-y}" in
      y|Y|yes|YES) return 0 ;;
      n|N|no|NO) return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}

require_file() {
  [ -f "$1" ] || fail "Missing required file: $1"
}

require_executable() {
  [ -x "$1" ] || fail "Missing executable permission or file: $1"
}

read_env_value() {
  local key="$1"
  if [ -f "$ENV_FILE" ]; then
    sed -n "s/^${key}=//p" "$ENV_FILE" | head -n 1
  fi
}

configure_env() {
  local current_token current_chat token chat temp_file
  current_token="$(read_env_value TG_BOT_TOKEN)"
  current_chat="$(read_env_value TG_CHAT_ID)"

  if [[ "$current_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
    token="$current_token"
    echo "Telegram bot token: already configured (kept hidden)."
  else
    echo "Telegram notifications need a bot token."
    read -r -s -p "TG_BOT_TOKEN: " token
    echo
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] ||
      fail "The bot token format is invalid. It should look like 123456:ABC..."
  fi

  if [[ "$current_chat" =~ ^-?[0-9]+$ ]]; then
    chat="$current_chat"
    echo "Telegram chat ID: already configured (kept)."
  else
    read -r -p "TG_CHAT_ID: " chat
    [[ "$chat" =~ ^-?[0-9]+$ ]] ||
      fail "The chat ID must be a numeric Telegram chat ID."
  fi

  umask 077
  temp_file="$(mktemp "$HERE/.env.setup.XXXXXX")"
  printf 'TG_BOT_TOKEN=%s\nTG_CHAT_ID=%s\n' "$token" "$chat" > "$temp_file"
  mv "$temp_file" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Saved Telegram configuration to .env with owner-only permissions."
}

check_accessibility() {
  local status_output
  status_output="$("$RUNNER" status 2>&1 || true)"
  printf '%s\n' "$status_output"

  if grep -q 'accessibility_trusted=True' <<<"$status_output"; then
    echo "Accessibility permission is already enabled."
    return 0
  fi

  echo
  echo "Accessibility permission is required for Monitask UI control."
  echo "Enable the item named MonitaskCenter.app, then return here."
  if confirm "Open macOS Accessibility settings now?"; then
    open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility' || true
  fi
  read -r -p "Press Enter after enabling Accessibility permission..." _

  status_output="$("$RUNNER" status 2>&1 || true)"
  printf '%s\n' "$status_output"
  if ! grep -q 'accessibility_trusted=True' <<<"$status_output"; then
    echo "WARNING: Accessibility is still not enabled."
    echo "The launchd jobs can be installed, but UI automation will not work yet."
  fi
}

say "Checking Monitask Center files"
require_file "$INSTALLER"
require_file "$RUNNER"
require_executable "$INSTALLER"
require_executable "$RUNNER"
require_executable "$PYTHON_RUNNER"
require_file "$HERE/schedule.json"
echo "Required files are present."

say "Configuring Telegram notifications"
configure_env

say "Checking Accessibility permission"
check_accessibility

say "Installing background launchd jobs"
"$INSTALLER" install

say "Final verification"
"$INSTALLER" status
echo
"$RUNNER" plan
echo
echo "Setup complete."
echo "The system now runs in the background. Useful checks:"
echo "  $RUNNER status"
echo "  $INSTALLER status"
echo "  tail -f \"$HOME/Library/Application Support/MonitaskCenter/monitask.log\""
