#!/bin/bash
# install.sh — 產生並載入 launchd LaunchAgents（誠實版 Monitask 排程）
# 用法：
#   ./install.sh install     產生 plist 並載入
#   ./install.sh uninstall   卸載並刪除 plist
#   ./install.sh reload       重新載入（改過 plist 後用）
#   ./install.sh status       列出目前載入的 job

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# launchd 經由 run.sh 呼叫 bundle 內 python 副本；AX 身分 = MonitaskCenter.app（穩定），
# 對該 app 授一次 AX 即可。不可讓 launchd 直接 exec bundle 執行檔（會被當 app 啟動）。
RUNNER="$HERE/run.sh"
LA="$HOME/Library/LaunchAgents"
PREFIX="com.dannylin.monitask-center"
LEGACY_PREFIX="com.dannylin.monitask"
UID_N="$(id -u)"

# label:排程(時:分, 或 watch):子命令
# 相位命令自行依 schedule.json 判斷今天班別，非該班別日自動略過。
JOBS=(
  "morning:10:00:morning"           # 早班：10:00 起每5分輪詢開始（11:50 前止）
  "lunch:12:00:lunch"               # 早班：午休暫停
  "afternoon:13:00:afternoon"       # 早班：午休結束繼續
  "evening:20:00:evening"           # 早班：下班詢問（15分未點自動停）
  "night-start:17:00:night-start"   # 晚班：17:00 起每5分輪詢開始（19:50 前止）
  "night-stop:01:00:night-stop"     # 晚班：隔日 01:00 停止（昨日為晚班才動）
  "watch:watch:watch"
)

plist_path() { echo "$LA/$PREFIX.$1.plist"; }

cleanup_legacy() {
  for job in "${JOBS[@]}"; do
    IFS=':' read -r name _ <<< "$job"
    local legacy="$LA/$LEGACY_PREFIX.$name.plist"
    launchctl bootout "gui/$UID_N" "$legacy" 2>/dev/null || true
    rm -f "$legacy"
  done
}

gen_plist() {
  local name="$1" sched="$2" cmd="$3"
  local path; path="$(plist_path "$name")"
  local schedule_block
  if [ "$sched" = "watch" ]; then
    schedule_block="<key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>"
  else
    local hh="${sched%%:*}" mm="${sched##*:}"
    schedule_block="<key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$((10#$hh))</integer>
    <key>Minute</key><integer>$((10#$mm))</integer>
  </dict>"
  fi
  cat > "$path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PREFIX.$name</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$RUNNER</string>
    <string>$cmd</string>
  </array>
  $schedule_block
  <key>StandardOutPath</key><string>$HOME/Library/Application Support/MonitaskCenter/launchd.$name.out</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Application Support/MonitaskCenter/launchd.$name.err</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLIST
  echo "  wrote $path"
}

do_install() {
  [ -f "$RUNNER" ] || { echo "找不到 run.sh：$RUNNER"; exit 1; }
  [ -x "$HERE/MonitaskCenter.app/Contents/MacOS/python-runner" ] || { echo "找不到 MonitaskCenter.app"; exit 1; }
  cleanup_legacy
  mkdir -p "$LA" "$HOME/Library/Application Support/MonitaskCenter"
  for job in "${JOBS[@]}"; do
    IFS=':' read -r name a b cmd <<< "$job"
    if [ "$name" = "watch" ]; then gen_plist "watch" "watch" "watch"; else gen_plist "$name" "$a:$b" "$cmd"; fi
  done
  for job in "${JOBS[@]}"; do
    IFS=':' read -r name _ _ _ <<< "$job"
    local p; p="$(plist_path "$name")"
    launchctl bootout "gui/$UID_N" "$p" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$p" && echo "  loaded $PREFIX.$name"
  done
  echo "完成。用 './install.sh status' 檢查。"
}

do_uninstall() {
  for job in "${JOBS[@]}"; do
    IFS=':' read -r name _ _ _ <<< "$job"
    local p; p="$(plist_path "$name")"
    launchctl bootout "gui/$UID_N" "$p" 2>/dev/null || true
    rm -f "$p" && echo "  removed $PREFIX.$name"
  done
  cleanup_legacy
}

do_status() {
  launchctl list | grep "$PREFIX" || echo "（沒有載入中的 job）"
}

case "${1:-}" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
  reload)    do_uninstall; do_install ;;
  status)    do_status ;;
  *) echo "usage: $0 {install|uninstall|reload|status}" >&2; exit 2 ;;
esac
