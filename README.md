# 熱血少年｜免費雲端拍攝管理 V5 (部署準備)

> **重要：目前 Firebase 連接尚未完成專案初始化。公開網站在設定完成前只能看無資料的畫面／手動開展示模式，不能正式保存工作。**

## 架構

- 前端：免費靜態網站，可用 Render Static Site / Firebase Hosting；Git push 自動更新，無需下載 ZIP。
- 後端：Firebase Authentication（Google 登入）＋ Cloud Firestore（Spark 免費資料庫）。
- 支援：賽事卡片、日期排序、即將拍攝、已拍待交、已完成、待核對、雙攝影師各自標記、交件倒數、手機版、月曆、JSON 備份／匯入。
- 每月 NT$0 原則：**僅在免費配額內使用，不升級 Blaze，也不啟用付費主機。** 超出配額可能暫停服務。

## 上線必要條件（尚未執行，不能當作已上線）

1. 在 [Firebase Console](https://console.firebase.google.com/) 建立 **Spark** 專案（不要連結計費）。
2. 新增 Web App，取得公開 Firebase Web 設定值（`apiKey`,`authDomain`,`projectId`,`appId`）。這些不是 Admin SDK 金鑰，不要提供服務帳號私鑰。
3. Authentication → Sign-in method → 開啟「Google」；Authorized domains 加入最終網站主機網域。
4. Cloud Firestore → 建立資料庫（請勿使用「測試模式」公開規則）→ Rules，完整貼上此專案 `firestore.rules` 並發佈。
5. 將 `web/firebase-config.js` 的 `null` 改為 Firebase Web App 設定物件，重新部署。
6. 第一位攝影師 Google 登入，建立團隊，分享團隊代碼。第二位登入，輸入代碼送出申請。第一位核准後，第二位再輸入代碼即可加入。
7. 正式資料從舊版 V4.2 的「匯出 JSON」一次匯入；先確認沒有重複後再匯入，並保留原始備份。匯入不會刪除舊資料。

## 安全與限制

- 禁止將私人 Google 日曆、密碼、SQLite 資料庫、服務帳號私鑰與家長資料放入公開程式庫。
- Firebase Web App 設定非管理金鑰，但**安全性取決於 Firestore Rules**。規則必須先部署，不可使用預設測試開放模式。
- 目前支援 Firebase Google **登入**，不代表 Google Calendar 已自動連結；日曆同步功能尚未實作，請勿誤認為已即時同步。
- 限制範圍為輕量文字工作紀錄，**不是照片原始檔備份系統**。
- 雙攝影師同場合併一張卡；個別完成狀態分開。編輯工作基本資訊時，會共用交件日（個別調整交期尚未完成）。
- Firestore 免費配額依 Google Firebase 官方公告為準，建議定期自行下載 JSON 備份。
- 網頁可公開載入，但沒有登入和成員資格無法讀取私有工作；「展示模式」只使用模擬內容、不會寫入資料庫。