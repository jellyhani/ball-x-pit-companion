# BALL x PIT Companion — English

[🌐 Languages](../../README.md#choose-your-language)

An unofficial Windows companion for BALL x PIT. It reads game state and shows advice for level-up choices, fusion, encyclopedia unlocks, harvesting, and base placement. You control the game yourself.

## Installation

You need Windows 10/11 and your own Steam installation of BALL x PIT. If a ZIP is available in [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases), extract it completely and run `BallxPitCompanion.exe`; keep `_internal` beside it. If no release is listed, install from source below. Unsigned builds can trigger SmartScreen.

For source installation, download this repository and open PowerShell in its folder. Install uv and run setup:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Start `run_overlay.bat`. Setup downloads dependencies and BepInEx and extracts text and icons from your own game. Close the game normally for bridge installation or updates; the installer waits while the game is running.

## Using the companion

- Check the game connection in settings, then open a level-up or fusion screen for advice.
- Enable encyclopedia mode in display settings to prioritize discoveries; it is off by default.
- Compare current and suggested base layouts and follow the listed moves manually.
- Harvest paths and yields are estimates. Access across several angles does not mean one shot collects every tile.

## Privacy and limits

The bridge is read-only, uses no Harmony patches, and does not change saves or send game input. Logs and extracted data stay in `%LOCALAPPDATA%\BallxPitCompanion`; gameplay records are not uploaded. This early version has been tested mainly on Windows 11, 1920×1080, Korean, game 1.301. Recommendations are not guaranteed optimal and do not predict exact future DPS. Language guides have not all received native-speaker review. This is not an official product.

## Troubleshooting and reports

If the overlay is missing, check visibility, the game window, and bridge status. Include versions, language, resolution, steps, expected result, and actual result in [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues). Remove private information from screenshots/logs; do not upload saves or extracted game assets.

[MIT](../../LICENSE) · [NOTICE](../../NOTICE.md)
