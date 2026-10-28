# 熱血少年｜Codex 專案交接（2026-10-28）

## 這個資料夾包含什麼

- `01_最新V5_免費雲端網頁版/`：**請以這套為主線開發**，純 HTML/CSS/JavaScript，Firebase Authentication + Firestore 待接通；含安全規則、Firebase Hosting 設定及原始碼。
- `02_舊版V4_2_本機程式參考/`：先前 Python + SQLite 本機版，供比對功能、舊 JSON 匯出資料格式、測試及資料遷移用。**不要直接把 SQLite 版部署在免費無持久磁碟主機。**
- `03_參考截圖/`：新舊版電腦與手機畫面，用來持續改善 UI。
- `給Codex的完整需求.txt`：可直接貼給 Codex 的任務指令。

## 目前真正已完成的事

- 免費前端已經在 Render 靜態網站上線： https://rexue-photo-workspace-free.onrender.com
- 來源 GitHub 分支（公開、僅非敏感程式碼）：
  https://github.com/cs619et-hash/rexue-photo-assistant-test/tree/rexue-workspace-firebase-free
- 靜態網站採用 Git commit 自動部署；頁面更新後可直接重新整理。
- 介面支援日期排序、已拍待交、接下來拍、已交件、待核對、雙攝影師分開操作、交件倒數、月曆、JSON 匯入匯出與展示資料。

## 尚未完成 / 不要誤稱已完成

- Firebase 插件已在 ChatGPT 端安裝，但**Firebase 專案尚未建立**；未啟用 Firebase Google 登入 / Firestore；`web/firebase-config.js` 目前為 null。
- `firestore.rules` 尚未部署並進行真實授權測試；**在此之前網站只是展示介面，不能儲存正式共同工作。**
- 植先與植丞尚未完成兩個真實帳號、不同裝置的同步測試。
- Google Calendar 即時同步尚未完成；V4.2 `calendar_snapshot.json` 是 2026-10-28 取得的**靜態快照**，不是即時同步。
- 尚未遷移舊 SQLite 正式工作資料；此交接包中 V4.2 的 `data/` 原本就沒有資料庫，**不能把它誤當真實工作備份**。

## 不可違背的使用者要求

1. **維持 NT$0，Firebase Spark，不升級 Blaze，不啟用需付費服務，不綁信用卡。** 超出免費額度要明確提醒。
2. **絕不可刪除、覆寫、重置任何原有工作紀錄**。匯入前先做 JSON 備份、驗證筆數、確認攝影師對應，處理重複事件要採安全合併/跳過。
3. Google 日曆只匯入 `2026-09-01`（含）以後的行程。
4. 使用者只想顯示年月日，不要顯示幾點幾分；同一天允許多場。
5. 首頁只以已拍待交、接下來拍、已完成交件三大入口為主；過期尚未核對另有提醒；不顯示照片篩選中、修圖中等進度細項。
6. 同場植先、植丞應共用一張賽事卡、各自有獨立的拍攝確認與交件狀態。日期不是完成依據，需手動按「拍攝完成」，交件也要明確確認。
7. 交件期限預估與正式期限必須區分，預估不能自動標示「逾期」。
8. 深藍黑＋攝影黃風格，第一屏就能看到最優先的工作，電腦、手機都要直覺可用。
9. 以固定網址部署，網站修改完成後只要重新整理，不再要求使用者下載 ZIP。
10. Firebase 安全規則只能讓團隊授權成員存取私人工作資料，**先部署 rules，再導入真實紀錄**。

## 安全注意

`02_舊版V4_2_本機程式參考/calendar_snapshot.json` 包含真實賽事名稱，**僅供私人遷移參考，嚴禁放進公開 GitHub**。任何 OAuth client secret、服務帳號私鑰、登入密碼、家長個資或 SQLite 正式資料庫也不得上傳公開儲存庫。GitHub 分支內的非敏感原始碼不含日曆快照。

## 本機確認

在 `01_最新V5_免費雲端網頁版` 執行：

```
python -m unittest discover -s tests
node --check web/app.js
python -m http.server 8000 -d web
```

瀏覽 http://localhost:8000 並點「展示模式」。上線需補全 Firebase 專案、Firestore 規則與 Google Auth，不可只用這個靜態示範測試宣稱完成。