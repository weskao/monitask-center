# monitask-center

跑在 **launchd 背景**的 Monitask 工時自動化(不是 Claude skill,不需開 Claude)。位置 `~/monitask-center/`。

**核心原則:離座一律讓計時停,不偽造工時——無 F13 / 合成輸入 / 補活動。**

## 每天自動發生什麼

依 `schedule.json` 判斷當天班別(`early` 早班 / `late` 晚班 / `off` 休)，**非該班別日各相位自動略過**。

早班日(early)：

| 時間 | 動作 | 通知 |
|------|------|------|
| 10:00 | 每 5 分輪詢，第一次把計時開起來即止(11:50 前)；已在計時→心跳跳過 | 發 |
| 12:00 | 午休暫停(已暫停則跳過) | 發 |
| 13:00 | 午休結束繼續計時(已在計時則跳過) | 發 |
| 20:00 | 先發 TG 提醒，畫面彈「關閉/繼續」對話框；選繼續→每 30 分再問；**15 分沒點→自動關閉** | 發 |

晚班日(late)：

| 時間 | 動作 | 通知 |
|------|------|------|
| 17:00 | 每 5 分輪詢，第一次把計時開起來即止(19:50 前) | 發 |
| 隔日 01:00 | 停止計時(僅「昨日為晚班」才動) | 發 |

全天常駐：watch 偵測離座彈窗 → 自動點「暫停/不計入」(learn 模式，見末段)。

輪詢的誠實原則：只負責把**第一次**啟動點成，成功即退出；之後你離座 idle 暫停時不會再補點。通知走 Telegram **群組 `monitask通知`**(bot + chat_id 存於 `.env`)，失敗退回 macOS 通知中心。

**班表每月要重生**：`schedule.json` 目前只含當月(由「2026年排班表-後端」Sheet 的個人 row 換算)。換月前需重新產生。

## 你平常要做的事:幾乎沒有

- **20:00 對話框跳出**:點「繼續監控」或「關閉監控」;放著 15 分會自動關閉。
- **離座彈窗**:learn 模式尚未自動點,暫時自己點(見末段)。
- 重開機:launchd 自動載入,免管。

## 首次設定

在專案目錄執行互動式設定程式：

```bash
./setup.sh
```

它會逐步檢查檔案、建立或更新 `.env`、引導開啟 macOS Accessibility 權限、載入 7 個 launchd job，最後顯示狀態與今日班表。有效的 Telegram 設定會保留，bot token 不會顯示在畫面上。

也可以用 Makefile：

```bash
make setup
make status
```

執行 `make help` 查看所有可用指令。

## 常用指令

```bash
~/monitask-center/run.sh status        # 現在計時中沒 / app 執行中沒 / AX 正常沒
~/monitask-center/run.sh plan          # 今天/昨天班別 + 各相位會做什麼(不動 Monitask)
~/monitask-center/install.sh status    # 7 個排程 job 是否都載入
~/monitask-center/run.sh dump-windows  # 印出 Monitask 視窗/按鈕結構(抓彈窗文字用)
tail -f ~/Library/Application\ Support/MonitaskCenter/monitask.log   # 看歷程
```

手動觸發(繞過時間；仍會依 schedule.json 判斷當天班別):

```bash
launchctl kickstart -k gui/$(id -u)/com.dannylin.monitask-center.morning     # 早班：立刻輪詢開始
launchctl kickstart -k gui/$(id -u)/com.dannylin.monitask-center.lunch       # 早班：立刻午休暫停
launchctl kickstart -k gui/$(id -u)/com.dannylin.monitask-center.afternoon   # 早班：立刻繼續
launchctl kickstart -k gui/$(id -u)/com.dannylin.monitask-center.night-start # 晚班：立刻輪詢開始
launchctl kickstart -k gui/$(id -u)/com.dannylin.monitask-center.night-stop  # 晚班：立刻停止(需昨日=晚班)
```

管理:

```bash
~/monitask-center/install.sh install     # 首次安裝/載入
~/monitask-center/install.sh reload      # 改過程式碼或設定後重載(watch 會重啟吃新碼)
~/monitask-center/install.sh uninstall   # 全部停用並移除排程
```

## 檔案結構

| 路徑 | 角色 |
|------|------|
| `monitask.py` | 主程式(pyobjc/AX)。子命令:`ensure-playing`/`ensure-paused`/`evening`/`watch`/`status`/`dump-windows` |
| `run.sh` | launchd 進入點:載入 `.env` + `PYTHONPATH=pylibs`,再跑 monitask.py |
| `MonitaskCenter.app` | 內含 python 副本;**AX 授權對象**(TCC 身分 `com.dannylin.monitask-center`),勿刪 |
| `.env` | `TG_BOT_TOKEN` + `TG_CHAT_ID`(權限 600,`.gitignore` 排除) |
| `pylibs/` | pyobjc(由 wheel 解壓,非 pip 安裝) |
| `pyfix/sitecustomize.py` | 補 `platform.mac_ver()`(僅 pip 下載 wheel 時用到) |
| `install.sh` | 產生/載入/卸載 launchd job |
| `setup.sh` | 首次互動式設定與驗證 |
| `Makefile` | 常用設定、管理與驗證指令 |
| log | `~/Library/Application Support/MonitaskCenter/monitask.log` |

## 通知一覽

| 事件 | TG DM |
|------|------|
| 開始/繼續成功、午休暫停、20:00 關閉、離座彈窗點擊 | ✅ |
| 排程檢查時已在目標狀態(心跳) | ✅ ☑️「無需動作」 |
| 失敗 / 讀不到狀態 / 取不到視窗 | ✅ ⚠️❌ |

管線:`run.sh` 載 `.env` → monitask 呼叫 `~/.claude/scripts/tg-send.sh`(cwd 固定在本目錄,已 `git init`,避開 tg-send 的 git 依賴)→ 你的 DM。

## 維護注意

- **別移除已授權的 AX 項目**(`MonitaskCenter.app`,及先前加的 `python3.13`)——否則排程動作全失效。
- **AX 是 launchd 能動 UI 的前提**:`MonitaskCenter.app` 必須在「系統設定 → 隱私權與安全性 → 輔助使用」開啟。launchd 一律經 `run.sh` 以 `/bin/bash` 呼叫 bundle 內 python(直接 exec bundle 執行檔會被當 app 啟動而失效)。
- **brew 升級 python@3.13 小版號**會讓 bundle 內 python 副本連結的 framework 絕對路徑失效,需重建:
  ```bash
  cp /opt/homebrew/opt/python@3.13/bin/python3.13 ~/monitask-center/MonitaskCenter.app/Contents/MacOS/python-runner
  codesign --force -s - ~/monitask-center/MonitaskCenter.app   # 重簽改 cdhash → 需重新授 AX
  ```

## 唯一未收尾:離座彈窗自動點暫停(learn 模式)

彈窗只有真的離座才會跳,尚未取得其按鈕文字,故 watch 保守處理:

- 第一次偵測到彈窗 → 記錄結構到 `~/Library/Application Support/MonitaskCenter/popup-structure.txt` + 發通知,**不亂點**;
- 只有比對到安全暫停鍵(`暫停/停止/不計入/捨棄/pause/stop/discard/no`)才點,**禁點**繼續類(`繼續/保留/計入/continue/keep/resume/yes`)。

拿到真實按鈕文字後,加進 `monitask.py` 的 `PAUSE_KEYWORDS` 即鎖定。查目前結構:`~/monitask-center/run.sh dump-windows`。

## 不外送任何畫面

只發文字通知(附主機名),不截圖、不外傳畫面內容。
