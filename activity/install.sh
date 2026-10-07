#!/bin/bash
# install.sh — 活動率監控的 launchd job（每整點觸發，fetch 沿用主程式的設定）。
#   ./install.sh install | uninstall | reload | status
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$HERE/run.sh"
LA="$HOME/Library/LaunchAgents"
LABEL="com.monitask-center.activity"
LEGACY_LABELS=(
  "com.dannylin.monitask-center.activity"
  "com.dannylin.monitask.activity"
)
UID_N="$(id -u)"
LOGDIR="$HOME/Library/Application Support/MonitaskCenter"
PLIST="$LA/$LABEL.plist"

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
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOGDIR/activity.out</string>
  <key>StandardErrorPath</key><string>$LOGDIR/activity.err</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
  echo "  wrote $PLIST"
}

do_install() {
  [ -f "$RUNNER" ] || { echo "找不到 run.sh：$RUNNER"; exit 1; }
  [ -d "$HERE/profile" ] || echo "  ⚠️ 尚未登入（profile/ 不存在）。先跑 ./run.sh login。"
  cleanup_legacy
  gen_plist
  launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_N" "$PLIST" && echo "  loaded $LABEL"
  echo "完成。"
}

do_uninstall() {
  launchctl bootout "gui/$UID_N" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST" && echo "  removed $LABEL"
  cleanup_legacy
}

case "${1:-}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  reload)    do_uninstall; do_install ;;
  status)    launchctl list | grep "$LABEL" || echo "（未載入）" ;;
  *) echo "usage: $0 {install|uninstall|reload|status}" >&2; exit 2 ;;
esac
