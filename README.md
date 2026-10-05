# monitask-center

跑在 **launchd 背景**的 Monitask 工時檢查器(會在固定時機自動播放/暫停)(不是 Claude skill,不需開 Claude)。由 `uv tool` 安裝的 `monitask-center` 指令管理，設定用互動選單 `monitask-center --config`。

## 核心原則

**平常只監看，只在兩個固定時機按播放/暫停。**

- 只會按 Monitask 的「播放」或「暫停」，且只有 `check.decide()` 判定需要時才按：沒有 Dock 點擊、不自動回應彈窗、不關閉 App。App 沒開或視窗被隱藏時，才會 `open -a Monitask` 並等待控制項出現(最長 30 秒)，按下後輪詢確認狀態真的改變。
- 離座不補點、不偽造工時，也沒有 F13 / 合成輸入 / 補活動。
- 狀態讀取集中在 `monitask.py` 的 `read_state()`：先確認 App 是否執行，再讀 `~/Library/Application Support/Monitask/Periods/` 中最後修改的 JSON；`DateLastActive` 距今小於 180 秒視為 `timing`，否則為 `paused`。不需 Accessibility(AX)權限；自動播放/暫停仍透過 `set_playing()` 使用 AX，並以按鈕狀態即時確認結果。
- 180 秒是暫定門檻(`PERIOD_MAX_AGE_S`)；檔案約每分鐘更新，暫停後最多約 3 分鐘才會辨識為 `paused`。尚待實際暫停觀察校準；`DateLastActive` 反映活動時間，不能保證等同計時器即時狀態。

## 每個工作日會發生什麼

launchd 在設定的整點喚醒 `check`（預設 10:00 ~ 18:00，每 1 小時，18:00 那一次也會檢查）。每次檢查依序判斷：

1. **今天是不是工作日**：台灣政府行事曆(含補班日) + 你自己設定的個人休假。個人休假優先於行事曆；放假/休假日不檢查、不提醒。行事曆讀不到時 **fail-open**：視為工作日，並每天最多通知一次。
2. **午休時段**：預設 12:00 ~ 13:00(`[12, 13)`)。午休內的檢查若 Monitask 還在計時(`timing` / `hidden`)就**自動按暫停**並通知；已暫停或沒開則不通知、不動作。午休只在執行時判斷，不會改動 launchd 排程。
3. **讀取 Monitask 狀態並決定**：

| 狀態 | 行為 |
|------|------|
| `timing`(計時中；`running` 視同) | 安靜，只寫日誌 |
| `paused`(已暫停) / `not-running`(App 沒開) | **開始時間與午休結束那一次**(預設 10:00、13:00)：必要時開啟 Monitask 並**自動按播放**，成功或失敗都通知；其餘整點只發提醒通知 |
| `unknown`(Period 目錄/檔案缺少或無法讀取、格式錯誤或時間戳記不合法) | 發一則警告通知 |

自動播放失敗不會留下標記，下一個整點會退回提醒通知。沒有「下班前停止」之類的動作。每次結果會記錄到 `~/.monitask-center/last-check.json`。

## 首次設定

需要 macOS、Homebrew、[uv](https://docs.astral.sh/uv/)，並已安裝、登入 Monitask 桌面程式。在專案目錄執行：

```bash
uv tool install --editable .     # 安裝 monitask-center 指令
monitask-center install          # 一次完成其餘設定
```

`monitask-center install` 會依序：確認檔案 → 準備執行環境 → (選用)設定 Telegram → 載入 launchd job → 最終驗證。每項檢查通過以 ✅ 標示；最終驗證(排程檔已安裝且 job 已載入)未通過以 ❌ 標示並以非零狀態結束，全部通過才顯示「✅ 安裝完成」。

- **執行環境全自動**：缺少 `python@3.13` 時用 Homebrew 安裝，把 PyObjC 裝到 `pylibs/`；`MonitaskCenter.app` 內的 python runner 損壞或版本不符時自動重建並重新簽章。再次執行只修復壞掉的部分，所以已授予的 Accessibility 權限會保留。
- **安裝與狀態監看不需 Accessibility**。若要自動播放/暫停，仍需到「系統設定 → 隱私權與安全性 → 輔助使用」授權 `MonitaskCenter.app`；未授權時動作會回報失敗並通知，監看與一般提醒照常運作。`dump-windows` 診斷也需要 AX。
- **Telegram 是選用功能**：token 存於作業系統認證存放區(macOS Keychain / Secret Service / Windows Credential Manager)，以 `monitask-center --config` 的「Telegram Bot Token」列編輯，畫面上只顯示遮罩；chat id 是識別碼而非密鑰，直接存於 `config.json`。設定完成後會立即發送一則測試通知；安裝程式不檢驗是否送達，請自行到 Telegram 確認。本機沒有認證存放區時，`install` 才退回寫入專案目錄 `.env`(權限 600，已被 `.gitignore` 排除，範例見 `.env.example`)；既有的 `.env` token 會在下次 `install` 自動搬進認證存放區並從檔案移除。Telegram 關閉或傳送失敗時退回 macOS 通知。

## 設定:`monitask-center --config`

互動選單(方向鍵 / 數字操作；管線或非 TTY 時自動退為編號式選單；遵守 `NO_COLOR`)。每次修改立即驗證並存檔。`r` 重設該列，`R` 重設全部(保留休假日)。

游標列標記：支援 emoji 的 UTF-8 終端機顯示 👉，其餘顯示 `> `；可用環境變數 `MONITASK_CENTER_POINTER=emoji|ascii` 強制指定。

| 列 | 說明 |
|----|------|
| 開始時間 | 第一次檢查的整點(預設 10) |
| 結束時間 | 最後一次檢查的整點(預設 18) |
| 間隔(小時) | 每幾小時檢查一次(預設 1，不可超過結束 - 開始) |
| 午休略過 | 開/關(預設開)；開啟時午休內不提醒，並在午休自動暫停、午休結束自動播放 |
| 午休開始 / 午休結束 | 午休時段 `[開始, 結束)`(預設 12 / 13) |
| 語言 | 在 `locales/` 內現有的語言間切換 |
| Telegram | 開：Telegram，失敗退回 macOS 通知；關：只用 macOS 通知 |
| 休假日 | 子選單：新增單日(`2026-10-05`)或區間(`2026-10-05~2026-10-09`)、移除、列出 |
| 排程狀態 | 唯讀：launchd 是否安裝、載入、與設定一致 |
| 狀態區 | 唯讀：今天是否工作日、下次檢查、Monitask 狀態、上次檢查 |

選單頂端有 `monitask-center` 字樣 logo,與 tagline 一起水平置中(終端寬度 ≥ 70 欄且高度 ≥ 25 列顯示大字，≥ 46 欄顯示小字，更窄或高度不足時自動隱藏，只留單行標題)；調整視窗大小會自動重繪。

修改開始 / 結束 / 間隔後，選單會自動**重新產生 launchd plist 並只重新載入有差異的 job**(逐位元組比對已安裝檔)。若尚未安裝排程，只會提示訊息並保留設定，不會偷偷安裝。午休相關設定不影響排程。

## 其他指令

| 指令 | 說明 |
|------|------|
| `monitask-center install` | 首次安裝 / 修復(見上) |
| `monitask-center uninstall` | 移除排程與舊版殘留 job |
| `monitask-center status` | 唯讀狀態：工作日、檢查時段、下次檢查、午休、休假日、排程、選用的自動操作權限、Monitask、上次檢查 |
| `monitask-center plan` | 今天會不會檢查、哪些整點、下次時間(不接觸 Monitask) |
| `monitask-center check` | 立即執行一次檢查(仍遵守時段 / 午休 / 工作日規則，必要時會通知) |
| `monitask-center dump-windows` | 印出 Monitask 視窗與按鈕結構(診斷用) |
| `monitask-center logs` | 追蹤日誌；`logs --path` 只印出路徑 |
| `monitask-center config` | 等同 `monitask-center --config` |

Makefile 只是薄包裝：

```bash
make install     # 只安裝 CLI：uv tool install --editable . --force
make config      # monitask-center --config
make status      # monitask-center status
make plan        # monitask-center plan
make logs        # monitask-center logs
make uninstall   # monitask-center uninstall
make test        # uv run --group dev pytest -q
make help        # 列出全部
```

## launchd 排程

只有**一個** job：`com.monitask-center.check`，用 `StartCalendarInterval` 在每個設定的整點(分鐘 0)各一筆，經由 `run.sh` 執行。

安裝與移除時會一併清掉舊版 job：`morning`、`lunch`、`afternoon`、`evening`、`night-start`、`night-stop`、`watch`(標籤 `com.monitask-center.<名稱>`)，以及舊 bundle id 前綴 `com.dannylin.monitask-center.*` 與 `com.dannylin.monitask.*` 的對應 job。

手動觸發一次(繞過時間，但仍遵守時段 / 午休 / 工作日規則)：

```bash
launchctl kickstart -k gui/$(id -u)/com.monitask-center.check
```

## 檔案結構

| 路徑 | 角色 |
|------|------|
| `TODO.md` | 待辦與建議事項 |
| `monitask.py` | 狀態引擎(本機 Period 檔案；按鈕操作使用 AX)，含 `set_playing()` 按播放/暫停。子命令：`check` / `status` / `dump-windows`；`read_state()` 回傳 `timing` / `paused` / `unknown` / `not-running` |
| `run.sh` | launchd 進入點：設定 `PYTHONPATH`(`src`、`pylibs`)、載入 `.env`，再用 bundle 內 python 跑 `monitask.py` |
| `MonitaskCenter.app` | 內含 python 副本；**AX 授權對象**(TCC 身分 `com.dannylin.monitask-center`)，勿刪 |
| `pyfix/sitecustomize.py` | 補 `platform.mac_ver()`(僅 pip 下載 wheel 時用到) |
| `pylibs/` | 安裝程式透過 pip 裝入的 PyObjC wheel(不入版控) |
| `src/monitask_center/` | `uv tool` 指令套件(僅標準函式庫)：`cli.py` 指令入口、`config.py` 設定、`check.py` 判斷與上次檢查紀錄、`workday.py` 工作日、`launchd.py` 排程、`install.py` 安裝、`status.py` 狀態、`notify.py` 通知與日誌、`i18n.py` 多語系 |
| `src/monitask_center/tui/` | `--config` 選單：`menu.py`、`present.py`、`keyreader.py`、`logo.py`(兩種尺寸的標題 logo) |
| `src/monitask_center/locales/<lang>/*.json` | 各語言文字(目前 `zh-TW`) |
| `tests/` | pytest 測試(含 i18n 守門) |
| `activity/` | 活動率監控，**獨立的選用功能**，與本工具的 launchd job 無關 |
| `.env` | 僅在無認證存放區時的後備：`TG_BOT_TOKEN` + `TG_CHAT_ID`(不入版控) |
| `~/.monitask-center/config.json` | 使用者設定(單一檔案，不放在專案目錄) |
| `~/.monitask-center/last-check.json` | 上次檢查結果 |
| `~/.monitask-center/holidays/` | 政府行事曆快取(每年一檔) |
| `~/Library/Application Support/MonitaskCenter/monitask.log` | 日誌 |

## 多語系(i18n)

程式碼中不放任何中文；所有使用者可見文字都走 `monitask_center.i18n.t("key", ...)`，文字在 `src/monitask_center/locales/<lang>/*.json`(扁平的 key → 文字；同一語言目錄下所有 `*.json` 會合併，跨檔重複 key 視為錯誤)。

新增語言：

1. 複製 `src/monitask_center/locales/zh-TW/` 為 `src/monitask_center/locales/<lang>/`。
2. 只翻譯值，key 與 `{placeholder}` 名稱必須完全相同；`tui.language.self` 填該語言自己的名稱。
3. 設定選單的「語言」列會自動出現新語言，用左右鍵切換。

查找順序：**目前語言 → `zh-TW` → key 本身**，所以漏翻的字會退回 `zh-TW`，不會空白或當掉。`tests/test_i18n_catalog.py` 會檢查程式碼沒有中文、每個 `t("key")` 都存在且佔位符一致、沒有孤兒 key、各語言目錄可乾淨合併。

## 通知一覽

| 事件 | 通知 |
|------|------|
| Monitask 已暫停 / 沒有執行(工作日、非午休) | 提醒 |
| 無法讀取 Monitask 狀態(Period 檔案或時間戳記異常) | 警告 |
| 政府行事曆無法取得(fail-open) | 警告，每天最多一次 |
| 計時中 / 視窗隱藏 / 午休 / 放假 / 休假 / 時段外 | 不通知，只寫日誌 |

通知先走 Telegram(`telegram-kit` 直連 Bot API；token 取自認證存放區，其次為 `.env` 的 `TG_BOT_TOKEN`；chat id 取自 `config.json`，其次為 `TG_CHAT_ID`；訊息附主機名)，Telegram 關閉或傳送失敗時退回 macOS 通知中心。只發文字，不截圖、不外傳畫面內容。

## 維護注意

- **需要自動播放/暫停時保留 AX 授權** `MonitaskCenter.app`；移除授權不影響狀態讀取與提醒。launchd 一律經 `run.sh` 以 `/bin/bash` 呼叫 bundle 內 python(直接 exec bundle 執行檔會被當 app 啟動而失效)。
- **brew 升級 `python@3.13`** 可能讓 bundle 內 python 副本連結的 framework 路徑失效：重跑 `monitask-center install` 會重建 runner 並重新簽章；重簽後可能需要重新授予 Accessibility。
- 修改程式碼不需重新安裝(`--editable`)；若改了 launchd 內容(`launchd.py`)，到 `--config` 改任一排程設定或重跑 `monitask-center install` 即可套用。
