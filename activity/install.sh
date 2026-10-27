#!/bin/bash
# install.sh — launchd job for activity-rate monitoring (wakes every 10 minutes; fetch reuses the main program's settings
# to pick the hourly report and the low activity checks, see launchd.ACTIVITY_TICKS).
#   ./install.sh install | sync | uninstall | reload | status
# The job runs a copy in ~/Library/Application Support/MonitaskCenter/activity: macOS privacy protection stops a
# launchd job reading a checkout under Documents/Desktop/Downloads, and the copy needs no Full Disk Access.
# sync = non-interactive install, run by `monitask-center`'s upgrade migration (launchd.migrate_activity).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LA="$HOME/Library/LaunchAgents"
LABEL="com.monitask-center.activity"
LEGACY_LABELS=(
  "com.dannylin.monitask-center.activity"
  "com.dannylin.monitask.activity"
)
UID_N="$(id -u)"
LOGDIR="$HOME/Library/Application Support/MonitaskCenter"
ACT="$LOGDIR/activity"
RUNNER="$ACT/run.sh"
CENTER_HOME="${MONITASK_CENTER_HOME:-$HOME/.monitask-center}"
PLIST="$LA/$LABEL.plist"

# Printed for the person running it, and kept with a timestamp in the job's own log (activity.out) for later.
log() {
  echo "  $*"
  { mkdir -p "$LOGDIR" && printf '[%s] install.sh: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOGDIR/activity.out"; } \
    2>/dev/null || true
}

ensure_node() {
  [ "$(uname -s)" = Darwin ] || { echo "活動率排程安裝僅支援 macOS。" >&2; exit 1; }
  local brew candidate
  brew="$(command -v brew || true)"
  for candidate in "$brew" /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      brew="$candidate"
      break
    fi
    brew=""
  done
  [ -n "$brew" ] || { echo "找不到 Homebrew，請先安裝 Homebrew。" >&2; exit 1; }

  if ! "$brew" list --versions node >/dev/null 2>&1; then
    echo "安裝 Node.js（Homebrew node）..."
    "$brew" install node
  fi
  NODE_PATH="$("$brew" --prefix node)/bin/node"
  [ -x "$NODE_PATH" ] || { echo "Homebrew 安裝後仍找不到 Node.js：$NODE_PATH" >&2; exit 1; }
  "$NODE_PATH" --version
  echo "Node.js: $(realpath "$NODE_PATH")"
}

# Keys the job reads from the checkout's .env, merged into the main program's .env (owner-only) without
# overwriting what is there. Only key names are logged. The bot token is copied only when the Keychain has none.
merge_env() {
  local src="$HERE/../.env" dst="$CENTER_HOME/.env" line key added=""
  [ -f "$src" ] || return 0
  mkdir -p "$CENTER_HOME"
  [ -f "$dst" ] || (umask 077 && : > "$dst")
  chmod 600 "$dst"
  while IFS= read -r line || [ -n "$line" ]; do
    key="${line%%=*}"
    case "$key" in
      TG_CHAT_ID|MONITASK_ACTIVITY_*) ;;
      TG_BOT_TOKEN)
        /usr/bin/security find-generic-password -s monitask-center -a telegram_bot_token >/dev/null 2>&1 && continue ;;
      *) continue ;;
    esac
    grep -q "^$key=" "$dst" && continue
    printf '%s\n' "$line" >> "$dst"
    added="$added $key"
  done < "$src"
  [ -z "$added" ] || log "已將 .env 設定合併到 ${dst}：$added"
}

# Copy the job (code, node_modules, the python package it reads the settings with) out of the checkout and
# move the login session along once; the session is never copied, so only one copy of it exists.
sync_runtime() {
  [ -d "$HERE/../src/monitask_center" ] || { echo "找不到 $HERE/../src/monitask_center" >&2; exit 1; }
  mkdir -p "$ACT/src"
  /usr/bin/rsync -a --delete --exclude profile --exclude src --exclude install.sh --exclude .DS_Store \
    "$HERE/" "$ACT/"
  chmod 700 "$ACT"  # after rsync, which gives it the checkout folder's mode; it holds the login session
  /usr/bin/rsync -a --delete --exclude __pycache__ "$HERE/../src/monitask_center/" "$ACT/src/monitask_center/"
  log "已同步活動率程式：$HERE → $ACT"
  if [ -d "$HERE/profile" ]; then
    if [ -e "$ACT/profile" ]; then
      log "⚠️ 兩處都有登入 session，沿用 $ACT/profile；確認後可自行刪除 $HERE/profile"
    else
      mv "$HERE/profile" "$ACT/profile"
      chmod 700 "$ACT/profile"
      log "已搬移登入 session：$HERE/profile → $ACT/profile"
    fi
  fi
  merge_env
}

cleanup_legacy() {
  local legacy_label legacy_plist
  for legacy_label in "${LEGACY_LABELS[@]}"; do
    legacy_plist="$LA/$legacy_label.plist"
    launchctl bootout "gui/$UID_N" "$legacy_plist" 2>/dev/null || true
    rm -f "$legacy_plist"
  done
}

gen_plist() {
  mkdir -p "$LA" "$LOGDIR"
  local ticks="" minute
  for minute in 0 10 20 30 40 50; do
    ticks="$ticks    <dict><key>Minute</key><integer>$minute</integer></dict>
"
  done
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUNNER</string>
    <string>fetch</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MONITASK_ACTIVITY_NODE</key><string>$NODE_PATH</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
$ticks  </array>
  <key>StandardOutPath</key><string>$LOGDIR/activity.out</string>
  <key>StandardErrorPath</key><string>$LOGDIR/activity.err</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
  echo "  wrote $PLIST"
}

load_job() {
  launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
  if launchctl bootstrap "gui/$UID_N" "$PLIST"; then
    log "已載入 ${LABEL}（執行 ${RUNNER}）"
  else
    log "❌ 載入 $LABEL 失敗"
    exit 1
  fi
}

do_install() {
  [ -f "$HERE/run.sh" ] || { echo "找不到 run.sh：$HERE/run.sh"; exit 1; }
  ensure_node
  sync_runtime
  echo "  活動率排程從 $ACT 執行，不需要「完整磁碟存取權」；先前若為 Node 加過此權限，可到系統設定移除。"
  echo "  活動率沿用登入 session，自動取得本人報表的成員 ID。"
  echo "  個別設定：$CENTER_HOME/.env（範例：$HERE/../.env.example）"
  echo "  MONITASK_ACTIVITY_TIMEZONE：Windows 時區名稱；MONITASK_ACTIVITY_MEMBER_ID：選填指定成員。"
  echo "  首次使用／登入過期：bash \"$RUNNER\" login"
  echo "  程式更新（git pull、npm install）後重跑：bash \"$HERE/install.sh\" install"
  [ -d "$ACT/profile" ] || echo "  ⚠️ 尚未登入（$ACT/profile 不存在）。先跑 bash \"$RUNNER\" login。"
  cleanup_legacy
  gen_plist
  load_job
  echo "完成。"
}

do_sync() {
  NODE_PATH="${MONITASK_ACTIVITY_NODE:-}"
  [ -x "$NODE_PATH" ] || ensure_node
  sync_runtime
  cleanup_legacy
  gen_plist
  load_job
}

do_uninstall() {
  launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST" && log "已移除 $LABEL"
  cleanup_legacy
  [ -d "$ACT" ] && echo "  程式與登入 session 仍保留在 ${ACT}，不再使用可自行刪除。"
  return 0
}

case "${1:-}" in
  install)   do_install ;;
  sync)      do_sync ;;
  uninstall) do_uninstall ;;
  reload)    do_uninstall; do_install ;;
  status)    launchctl list | grep "$LABEL" || echo "（未載入）" ;;
  *) echo "usage: $0 {install|sync|uninstall|reload|status}" >&2; exit 2 ;;
esac
