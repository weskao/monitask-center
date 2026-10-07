# TODO

## 用途與功能範圍

- **計時狀態檢查（`com.monitask-center.check`）**：確認 Monitask 是否開啟、正在計時或暫停。正常計時保持安靜；未計時時提醒，開始工作／午休結束時可自動播放，午休時可自動暫停。此功能不計算活動率百分比，由 `monitask-center` 指令與主設定管理。
- **活動率 % 通知（`com.monitask-center.activity`）**：透過專用登入工作階段讀取 Monitask 網頁報表，把今日活動率及可解析的工時等資訊傳到 Telegram。「最近一小時」目前是區段平均估計，仍需校準。這是獨立排程，沿用主設定的工作時間、間隔、午休、休假與 Telegram 開關。
- **App bundle 與執行環境**：`MonitaskCenter.app` 內的 Python 執行計時引擎，保留固定的輔助使用權限身分；activity 則由 Node／Playwright 讀取報表。清除 quarantine 是修復啟動阻擋，不是關閉 macOS 全系統的安全檢查。
- **此文件的用途**：記錄已驗證的行為、尚未完成的權限與解析修正，以及提交前的檢查。排程已載入、終端測試成功、背景排程成功、Telegram 已送達是不同的驗證階段；不能互相替代。

## 高優先

- [x] 【高優先】改讀 Monitask 本機檔案判斷狀態，狀態監看免除輔助使用(Accessibility)權限，並移除安裝授權步驟。自動播放/暫停仍需 AX，改以按鈕狀態即時驗證，避免 Period 更新延遲誤判。
  - 不採用 Chrome OAuth / Monitask API：API 要由公司管理員向 Monitask support 申請 API Access 才有 client_id/secret(OAuth2，端點 `app.monitask.com/identity/connect/...`)；借用 Chrome cookie 脆弱且可能違反服務條款；API 只有已上傳的工時資料，有延遲，也分不出 App 沒開或暫停。
  - 做法：`running_app()` 判斷 App 是否開啟(本來就不需 AX)；`~/Library/Application Support/Monitask/Periods/*.json` 計時中約每分鐘更新，含 `DateEnd`、`DateLastActive`，最後修改檔的 `DateLastActive` 距今小於 180 秒視為 `timing`，否則 `paused`。`Logs/log*.txt` 也持續記錄截圖排程，可作輔助。
  - [ ] 待實測校準：暫停計時約 3 分鐘，確認 Period 檔是否停止更新，並校準暫定的 `PERIOD_MAX_AGE_S = 180`。本次未操作使用者的計時器；活動時間不一定等同計時狀態，暫停辨識最多約延遲 3 分鐘。
  - 參考：[Get started with Monitask API](https://www.monitask.com/article/get-started-with-monitask-api/)、[Monitask API - apitracker](https://apitracker.io/a/monitask)

## 建議事項

- [x] 【建議】安裝並同步計時檢查排程。2026-10-07 已確認 `com.monitask-center.check` 載入、與目前設定一致，實際觸發退出碼為 `0`；AX 已授權。安裝與監看不需 AX，自動播放/暫停才需要。
- [ ] 確認舊的 `com.wes.monitask-automation` 是否仍在執行；若有，停用以避免重複提醒。

## 活動率 % 通知：2026-10-07 檢查與修復

### 已完成

- [x] 修復 `MonitaskCenter.app` 的下載 quarantine 標記，保留有效簽章；安裝程式在 Python 探測前清除本專案 App 的標記。已提交 `2e9dcae`。
- [x] 補齊 Python 的 `telegram-kit`、activity 的 Playwright 與 Chromium；建立並驗證專用 Monitask 登入工作階段。登入資料不入版控。
- [x] 活動率工作時窗改用主程式的設定與政府行事曆，移除不存在的舊 `schedule.json` 依賴；遵守工作時間、間隔、午休、休假、假日與 Telegram 開關。
- [x] 安裝 `com.monitask-center.activity`，每小時整點觸發。依目前設定，工作日 11:00～18:00 檢查，12:00 午休略過；休假與假日略過。此排程與計時檢查是兩個獨立 job。
- [x] 補上 launchd 精簡 PATH 下的 Node 絕對路徑候選；修正 Playwright 等待參數；通知須同時取得成功退出碼與 Telegram 送達確認，否則以非零狀態結束。
- [x] 終端機執行 `bash activity/run.sh fetch force`，成功抓取今日活動率並取得 `tg-send: sendMessage OK message_id=117`。`force` 是驗證用，會略過工作時窗限制，不代表背景排程已通過。
- [x] 回歸測試 `tests/test_activity.py` 的 10 個案例通過；全套 `553 passed, 1 skipped`，Node／Bash 語法檢查與 `git diff --check` 通過。

### 尚待完成

- [ ] 【高優先】授予背景 Node 存取 Documents 的權限。實際 launchd 執行出現 `EPERM`，無法讀取 `activity/fetch.js`；同一程式從終端機可執行。需本人至「系統設定 → 隱私權與安全性 → 完整磁碟存取權」加入並啟用 `/opt/homebrew/Cellar/node/26.7.0/bin/node`。若 Node 更新，先確認 `/opt/homebrew/bin/node` 的實際路徑。尚未確認授權完成。
- [ ] 授權後執行 `launchctl kickstart "gui/$(id -u)/com.monitask-center.activity"`，確認服務退出碼為 `0`、`activity.err` 沒有新的 `EPERM`。時段外只會略過，仍須在工作時窗確認百分比抓取及 Telegram 送達紀錄。
- [ ] 【高優先】校準 `parseActivity()` 的「最近一小時」解析。實測出現今日活動 `00.00%`、總時 `00H00米`，但最近一小時 `≈100%`（1 段）；需與網頁的實際紀錄比對，確認未抓到範例／其他區塊，以及是否需要依區段時間加權。驗證前不要把這個估計值視為已確認的使用率。
- [ ] 將 activity 修正與 `tests/test_activity.py` 一起提交；測試已證明修正前工作時窗的 2 個案例失敗。此次 activity 與 TODO 變更尚未提交。
