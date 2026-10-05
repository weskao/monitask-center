# TODO

## 高優先

- [x] 【高優先】改讀 Monitask 本機檔案判斷狀態，狀態監看免除輔助使用(Accessibility)權限，並移除安裝授權步驟。自動播放/暫停仍需 AX，改以按鈕狀態即時驗證，避免 Period 更新延遲誤判。
  - 不採用 Chrome OAuth / Monitask API：API 要由公司管理員向 Monitask support 申請 API Access 才有 client_id/secret(OAuth2，端點 `app.monitask.com/identity/connect/...`)；借用 Chrome cookie 脆弱且可能違反服務條款；API 只有已上傳的工時資料，有延遲，也分不出 App 沒開或暫停。
  - 做法：`running_app()` 判斷 App 是否開啟(本來就不需 AX)；`~/Library/Application Support/Monitask/Periods/*.json` 計時中約每分鐘更新，含 `DateEnd`、`DateLastActive`，最後修改檔的 `DateLastActive` 距今小於 180 秒視為 `timing`，否則 `paused`。`Logs/log*.txt` 也持續記錄截圖排程，可作輔助。
  - [ ] 待實測校準：暫停計時約 3 分鐘，確認 Period 檔是否停止更新，並校準暫定的 `PERIOD_MAX_AGE_S = 180`。本次未操作使用者的計時器；活動時間不一定等同計時狀態，暫停辨識最多約延遲 3 分鐘。
  - 參考：[Get started with Monitask API](https://www.monitask.com/article/get-started-with-monitask-api/)、[Monitask API - apitracker](https://apitracker.io/a/monitask)

## 建議事項

- [ ] 【建議】開始排程:在終端機執行 `monitask-center install`(需互動輸入,不能在非互動環境執行)。安裝與監看不需 AX；自動播放/暫停才需要在「系統設定 → 隱私權與安全性 → 輔助使用」授權 MonitaskCenter。裝好後建議停用舊的 `com.wes.monitask-automation`,避免收到重複提醒。
