# BALL x PIT Companion — 简体中文

[🌐 Languages](../../README.md#choose-your-language)

这是 BALL x PIT 的非官方 Windows 辅助工具。它读取游戏状态，为升级选择、融合、图鉴解锁、采集和基地布局提供建议。游戏操作由玩家自己完成。

## 安装

需要 Windows 10/11 和你自己安装的 Steam 版 BALL x PIT。如果 [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) 中提供了 ZIP，请完整解压并运行 `BallxPitCompanion.exe`，保留旁边的 `_internal` 文件夹。如果尚未发布版本，请使用下方的源码安装方式。程序未签名，可能触发 SmartScreen 提示。

下载仓库，在仓库文件夹中打开 PowerShell，安装 uv 并运行初始化：

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

随后运行 `run_overlay.bat`。初始化会下载依赖和 BepInEx，并从你自己的游戏中提取文本与图标。安装或更新连接插件时，请正常退出游戏；游戏运行期间安装程序会等待。

## 使用方法

- 在设置中确认连接状态，然后打开升级或融合界面查看建议。
- 若要优先解锁内容，请在显示设置中开启图鉴模式；默认关闭。
- 对比当前布局与建议布局，按列出的顺序手动移动。
- 采集轨迹和产量只是预测。多个角度都能到达资源，不代表一次发射就能全部采集。

## 隐私与限制

连接插件只读取数据，不使用 Harmony 补丁，不修改存档，也不发送游戏操作。日志和提取的数据保存在 `%LOCALAPPDATA%\BallxPitCompanion`，不会上传游玩记录。当前为早期版本，主要验证环境为 Windows 11、1920×1080、韩语、游戏 1.301。不保证最优布局或精确的未来 DPS。并非所有翻译都经过母语使用者审校。这不是官方产品。

## 排查与反馈

若叠加层不显示，请检查显示设置、游戏窗口和连接状态。在 [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) 中提供版本、语言、分辨率、复现步骤、预期结果和实际结果。分享截图或日志前请移除个人信息，不要上传存档或提取的游戏素材。

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
