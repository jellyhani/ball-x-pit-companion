# BALL x PIT Companion — 繁體中文

[🌐 Languages](../../README.md#choose-your-language)

這是 BALL x PIT 的非官方 Windows 輔助工具。它讀取遊戲狀態，為升級選擇、融合、圖鑑解鎖、採集和基地配置提供建議。遊戲操作由玩家自行完成。

## 安裝

需要 Windows 10/11 與你自己安裝的 Steam 版 BALL x PIT。如果 [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) 提供 ZIP，請完整解壓縮並執行 `BallxPitCompanion.exe`，保留旁邊的 `_internal` 資料夾。如果尚未發布版本，請使用下方的原始碼安裝方式。程式未簽章，可能出現 SmartScreen 提示。

下載儲存庫，在其資料夾中開啟 PowerShell，安裝 uv 並執行初始設定：

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

接著執行 `run_overlay.bat`。初始設定會下載相依套件與 BepInEx，並從你自己的遊戲中擷取文字及圖示。安裝或更新連線外掛時，請正常結束遊戲；遊戲執行期間安裝程式會等待。

## 使用方式

- 在設定中確認連線狀態，然後開啟升級或融合畫面查看建議。
- 若要優先解鎖內容，請在顯示設定中開啟圖鑑模式；預設關閉。
- 比較目前與建議的配置，依照列出的順序手動移動。
- 採集軌跡與產量只是預測。多個角度能到達資源，不代表一次發射就能全部採集。

## 隱私與限制

連線外掛唯讀，不使用 Harmony 修補，不修改存檔，也不傳送遊戲操作。紀錄與擷取資料保存在 `%LOCALAPPDATA%\BallxPitCompanion`，不會上傳遊玩紀錄。目前為早期版本，主要驗證環境為 Windows 11、1920×1080、韓語、遊戲 1.301。不保證最佳配置或精確的未來 DPS。並非所有翻譯都經過母語使用者校閱。這不是官方產品。

## 疑難排解與回報

若浮層未顯示，請檢查顯示設定、遊戲視窗及連線狀態。在 [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) 提供版本、語言、解析度、重現步驟、預期及實際結果。分享截圖或紀錄前請移除個人資訊，不要上傳存檔或擷取的遊戲素材。

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
