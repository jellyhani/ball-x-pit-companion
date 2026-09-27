# BALL x PIT Companion

A Windows overlay that helps with ball choices, fusion recipes, harvest aiming, and base layouts.
It reads the game through BepInEx. You still make the choices and move buildings yourself.

**This is a beta.** Advice can be wrong, and predicted paths can differ from the game.
It is an unofficial project, with no affiliation to the game's developer or publisher.

## Choose your language

Installation and usage guides are available in 16 languages. Some translations still need review.

| | | | |
|---|---|---|---|
| [English](docs/guides/en.md) | [한국어](docs/guides/ko.md) | [日本語](docs/guides/ja.md) | [简体中文](docs/guides/schinese.md) |
| [繁體中文](docs/guides/tchinese.md) | [Deutsch](docs/guides/german.md) | [Français](docs/guides/french.md) | [Italiano](docs/guides/italian.md) |
| [Español (España)](docs/guides/spanish.md) | [Español (Latinoamérica)](docs/guides/latam.md) | [Português (Brasil)](docs/guides/brazilian.md) | [Polski](docs/guides/polish.md) |
| [Русский](docs/guides/russian.md) | [Українська](docs/guides/ukrainian.md) | [Türkçe](docs/guides/turkish.md) | [ไทย](docs/guides/thai.md) |

## Install

You need **Windows 10/11 and your own Steam copy of BALL x PIT**. Close the game normally when the bridge needs installation or an update.

### Packaged version

Download the application ZIP from [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases).
The separate `dependency-sources.zip` contains library source code and is not needed to run the app.
If there is no published release yet, use the source instructions below.

Extract the entire ZIP and run `BallxPitCompanion.exe`. Keep its `_internal` folder beside it. The first run extracts game data locally. The bridge installer waits for the game to close. Builds are unsigned; Windows may show a SmartScreen warning.

### From source

Download or clone this repository, open PowerShell in its folder, install [uv](https://docs.astral.sh/uv/), and run:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Then run `run_overlay.bat`. Setup installs dependencies, extracts labels and icons from your game,
and installs the bridge. No game files are included in the download.

## First use

1. Start the companion and the game. Check the connection in the companion settings.
2. Open a level-up or fusion selection screen to see advice. You make the selection yourself.
3. Turn **encyclopedia mode** on in the display settings when discovering combinations is your priority; it is off by default.
4. In the base, compare the current and suggested layouts and follow the listed move order manually.
5. In harvest mode, hold Shift to extend the predicted path. Check the result in game before relying on it.

If the overlay is missing, check its visibility and bridge status in settings. A bridge update
requires a normal game restart after installation.

## What to expect

- Ball advice includes reasons, alternatives, and evolution/fusion plans. Community rankings are opinions, not game rules.
- Base suggestions try to keep the entrance and resources accessible while limiting how much you need to move. They are not a proven best layout.
- Harvest predictions account for walls, roads, worker upgrades, and several building effects. Frame timing and some effects still differ from the game; a fresh live comparison is still needed for the recent physics changes.
- The main tested setup is Windows 11, 1920×1080, Korean, with game version 1.301. Game updates may break the bridge.

The [calculation notes](docs/reference/GAME_DATA_CONTRACT.ko.md) list the remaining gaps.
[Mechanics and guide sources](docs/reference/README.ko.md#자료-출처) are documented separately.

## Local data and permissions

The companion does not send game input or change saves. Installing BepInEx and the bridge does
add files to the game folder. Settings, extracted data, and gameplay logs stay under
`%LOCALAPPDATA%\BallxPitCompanion`; the app does not upload those records.

See [Privacy](PRIVACY.md) for screen capture, hotkeys, stored files, and installation downloads.
The app is provided as-is; [Disclaimer](DISCLAIMER.md) and [Third-party notices](NOTICE.md)
explain the rights and limits. Game assets remain the property of their rights holders.

## Report a problem

Open an [issue](https://github.com/jellyhani/ball-x-pit-companion/issues) with your versions,
language, resolution, and steps to reproduce the problem. A cropped screenshot helps.
Remove private information from logs and images; do not attach saves or extracted game assets.

## Development

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
.venv\Scripts\python.exe tools\regress.py
```

Tests that need locally extracted game data are skipped when it is absent. CI covers the
remaining tests; live game behavior needs separate checks. See [Contributing](CONTRIBUTING.md)
for development and testing instructions.

For code navigation, see the [architecture and reading guide](docs/ARCHITECTURE.md).
Maintainers should follow the [release procedure](docs/RELEASING.md), including publishing the
[corresponding dependency sources](docs/DEPENDENCY_SOURCES.md) beside each binary release.

Original software: [MIT](LICENSE) · [Project notice](DISCLAIMER.md) · [Privacy](PRIVACY.md) · [Third-party notices](NOTICE.md).
