# TODO

## 高優先

- [ ] 【高優先】改讀 Monitask 本機檔案判斷狀態，免除輔助使用(Accessibility)權限。只需改 `monitask.py` 的 `read_state()`，安裝流程的 Accessibility 步驟也可移除。
  - 不採用 Chrome OAuth / Monitask API：API 要由公司管理員向 Monitask support 申請 API Access 才有 client_id/secret(OAuth2，端點 `app.monitask.com/identity/connect/...`)；借用 Chrome cookie 脆弱且可能違反服務條款；API 只有已上傳的工時資料，有延遲，也分不出 App 沒開或暫停。
  - 做法：`running_app()` 判斷 App 是否開啟(本來就不需 AX)；`~/Library/Application Support/Monitask/Periods/*.json` 計時中約每分鐘更新，含 `DateEnd`、`DateLastActive`，最新檔的 `DateLastActive` 距今小於 N 分鐘視為 `timing`，否則 `paused`。`Logs/log*.txt` 也持續記錄截圖排程，可作輔助。
  - 待驗證：暫停計時約 3 分鐘，確認 Period 檔是否停止更新，並據此定出 N。
  - 參考：[Get started with Monitask API](https://www.monitask.com/article/get-started-with-monitask-api/)、[Monitask API - apitracker](https://apitracker.io/a/monitask)

## 建議事項

- [ ] 【建議】開始排程:在終端機執行 `monitask-center install`(需互動輸入,不能在非互動環境執行)。過程中需要在「系統設定 → 隱私權與安全性 → 輔助使用」授權 MonitaskCenter。裝好後建議停用舊的 `com.wes.monitask-automation`,避免收到重複提醒。
