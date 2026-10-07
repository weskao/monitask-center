#!/bin/bash
# 活動率監控進入點。用法：
#   ./run.sh login        首次：開有畫面瀏覽器，你本人手動登入 Monitask（存 profile）
#   ./run.sh fetch        無頭抓一次（工作時窗外會自動略過）— launchd 用這個
#   ./run.sh fetch force  無頭抓一次並略過時窗閘門（測試用）
HERE="$(cd "$(dirname "$0")" && pwd)"

# node：優先用 nvm 的 v22，退回 PATH。
NODE="$HOME/.nvm/versions/node/v22.23.1/bin/node"
[ -x "$NODE" ] || NODE="/opt/homebrew/bin/node"
[ -x "$NODE" ] || NODE="/usr/local/bin/node"
[ -x "$NODE" ] || NODE="$(command -v node)"
[ -x "$NODE" ] || { echo "找不到 node"; exit 1; }

# 載入 Telegram 設定（沿用打卡那份 .env：bot token + 群組 chat_id）
[ -f "$HERE/../.env" ] && set -a && . "$HERE/../.env" && set +a

case "${1:-}" in
  login) exec "$NODE" "$HERE/login.js" ;;
  fetch) shift; exec "$NODE" "$HERE/fetch.js" "$@" ;;
  *) echo "usage: $0 {login | fetch [force]}" >&2; exit 2 ;;
esac
