# BALL x PIT Companion — 日本語

[🌐 Languages](../../README.md#choose-your-language)

BALL x PIT向けの非公式Windows補助ツールです。ゲーム状態を読み取り、レベルアップ、融合、図鑑の解放、採集、拠点配置を案内します。ゲームの操作はプレイヤーが行います。

## インストール

Windows 10/11と、ご自身のSteam版BALL x PITが必要です。[Releases](https://github.com/jellyhani/ball-x-pit-companion/releases)にZIPが公開されていれば、すべて展開して`BallxPitCompanion.exe`を起動します。`_internal`フォルダーも必要です。リリースがなければ、以下のソースからの手順を使ってください。署名がないためSmartScreenの警告が出る場合があります。

リポジトリをダウンロードし、そのフォルダーでPowerShellを開いて、uvの導入と初期設定を実行します。

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

その後、`run_overlay.bat`を起動します。初期設定では依存パッケージとBepInExを取得し、ご自身のゲームからテキストとアイコンを抽出します。連携機能の導入・更新時はゲームを通常の方法で終了してください。実行中はインストールを待機します。

## 使い方

- 設定で接続状態を確認し、レベルアップや融合の画面を開きます。
- 未発見の組み合わせを優先するには、表示設定で図鑑モードを有効にします。初期状態は無効です。
- 現在と提案された配置を比較し、表示された順番で手動で移動します。
- 軌道と採集量は予測です。複数の角度で到達できても、1回の発射ですべて採集できるとは限りません。

## プライバシーと制限

連携は読み取り専用で、Harmonyパッチ、セーブ変更、ゲーム入力を行いません。ログと抽出データは`%LOCALAPPDATA%\BallxPitCompanion`に保存され、プレイ記録はアップロードされません。主な検証環境はWindows 11、1920×1080、韓国語、ゲーム1.301です。最適な配置や正確な将来DPSは保証しません。すべての翻訳が母語話者による校閲済みではありません。公式製品ではありません。

## 問題の確認と報告

表示されない場合は、表示設定、ゲームウィンドウ、接続状態を確認してください。[Issues](https://github.com/jellyhani/ball-x-pit-companion/issues)にはバージョン、言語、解像度、再現手順、期待した結果と実際の結果を記載してください。画像やログの個人情報を削除し、セーブや抽出したゲーム素材は投稿しないでください。

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
