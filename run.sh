#!/bin/bash
# launchd 進入點：用 MonitaskCenter.app 內的 python 副本跑 monitask.py。
# 必須經由此 shell 呼叫 python-runner（而非讓 launchd 直接 exec bundle 執行檔，
# 否則會被 LaunchServices 當 app 啟動而不執行腳本）；AX 身分仍歸屬 bundle，授權生效。
HERE="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$HERE/pylibs"
# 載入 Telegram 設定（TG_BOT_TOKEN / TG_CHAT_ID）供 tg-send 使用；不存在則 notify 退回 macOS 通知
[ -f "$HERE/.env" ] && set -a && . "$HERE/.env" && set +a
exec "$HERE/MonitaskCenter.app/Contents/MacOS/python-runner" "$HERE/monitask.py" "$@"
