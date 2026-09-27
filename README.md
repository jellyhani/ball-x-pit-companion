# BALL x PIT Companion

An unofficial **Windows overlay for BALL x PIT**: level-up advice, evolution and fusion planning, encyclopedia unlock goals, harvest trajectories, and base layout suggestions.

The companion reads live game state through a BepInEx bridge. It does not play the game, send game input, or modify saves. Game text and icons are extracted from your own installation and are not distributed in this repository.

**Early development:** recommendations and trajectories can be wrong. Layout suggestions are not proven optimal, and build ratings are not predictions of future DPS. The main tested environment is Windows 11, 1920×1080, Korean, Steam game version 1.301. Other languages and resolutions need more real-world testing.

## Choose your language

These 16 guides cover installation, everyday use, limitations, and bug reports. They are user guides, not full translations of every developer document. Translations have not all received native-speaker review.

| | | | |
|---|---|---|---|
| [English](docs/guides/en.md) | [한국어](docs/guides/ko.md) | [日本語](docs/guides/ja.md) | [简体中文](docs/guides/schinese.md) |
| [繁體中文](docs/guides/tchinese.md) | [Deutsch](docs/guides/german.md) | [Français](docs/guides/french.md) | [Italiano](docs/guides/italian.md) |
| [Español (España)](docs/guides/spanish.md) | [Español (Latinoamérica)](docs/guides/latam.md) | [Português (Brasil)](docs/guides/brazilian.md) | [Polski](docs/guides/polish.md) |
| [Русский](docs/guides/russian.md) | [Українська](docs/guides/ukrainian.md) | [Türkçe](docs/guides/turkish.md) | [ไทย](docs/guides/thai.md) |

## What it helps with

| Feature | What you get |
|---|---|
| Level-up and fusion advice | Ranked choices, reasons, alternatives, current recipes, and plans for later growth |
| Encyclopedia mode | Optional priority for undiscovered balls and unrecorded fusion combinations |
| Harvest aiming | Predicted paths and resource yields using game geometry and worker upgrades |
| Base planning | Move sequences that check entrance space, existing production, construction access, and resource access |
| Building and worker advice | Suggestions based on available buildings, costs, resources, and the chosen guide policy |

Workers assigned to farms, lumberyards, or quarries still participate in harvest launches. The current automatic producers use building-level production intervals; legacy farm/lumberyard/quarry speed upgrades do not improve those intervals. Assignment advice fills vacant jobs and uses a character bonus only for the old building types to which the game actually applies it.

Community tiers are opinions. Game values, community advice, and model estimates are different inputs. See the [detailed technical notes and sources (Korean)](docs/reference/README.ko.md#자료-출처).

The [game-data contract audit (Korean)](docs/reference/GAME_DATA_CONTRACT.ko.md) maps game getters to calculations and lists effects that still need live validation.

## Install

You need **Windows 10/11 and your own Steam copy of BALL x PIT**. Close the game normally when the bridge needs installation or an update.

### Packaged version

Check [Releases](https://github.com/jellyhani/ball-x-pit-companion/releases) for a published `BallxPitCompanion-*.zip`. **If no release is listed, use the source instructions below.** A local build is not a published download.

Extract the entire ZIP and run `BallxPitCompanion.exe`. Keep its `_internal` folder beside it. The first run extracts game data locally. The bridge installer waits for the game to close. Builds are unsigned; Windows may show a SmartScreen warning.

### From source

Download or clone this repository, open PowerShell in its folder, install [uv](https://docs.astral.sh/uv/), and run:

```powershell
winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Then run `run_overlay.bat`. Setup creates a Python environment, installs dependencies, extracts your game data, and prepares the bridge. It does not include a copy of the game.

## First use

1. Start the companion and the game. Check the connection in the companion settings.
2. Open a level-up or fusion selection screen to see advice. You make the selection yourself.
3. Turn **encyclopedia mode** on in the display settings when discovering combinations is your priority; it is off by default.
4. In the base, compare the current and suggested layouts and follow the listed move order manually.
5. Treat harvest paths and yields as predictions. Resource access across several tested angles does not mean one shot collects every tile.

If the overlay is missing, check its visibility, game window state, and bridge status. If the bridge needs updating, let the game exit normally and then reopen it. Please report persistent failures instead of assuming a recommendation is correct.

## Privacy and limits

- Logs and extracted data stay under `%LOCALAPPDATA%\BallxPitCompanion`. The companion does not upload gameplay records.
- Setup downloads dependencies and BepInEx. The read-only bridge uses a local named pipe and no Harmony patches.
- Hotkeys and recent left-click coordinates are observed to identify choices; the app does not record typed text.
- Game updates may break the bridge. Physics approximations, heuristic scores, and unverified effects can affect advice.
- Bridge 1.18.0 supplies live static walls, collider normals, pickup/raycast roles, roads, worker order and building effects. Older inputs reconstruct unopened chunk walls. Unsupported geometry holds the calculation instead of assuming an empty space.
- The harvest model includes resource depletion, regeneration and automatic production, but frame ordering, future completion changes and random effects remain approximate or explicitly unmodeled. Automatic producer income is separate from worker harvest totals. Cached angle recommendations can use a task timer up to ten seconds old; they are not exact forecasts.
- Current building range membership is checked against the game's own target-by-target verdicts. A disagreement holds layout suggestions and writes a local diagnostic; passing unit tests alone does not prove the bridge matches a live game scene.
- The game's direct launch verdict takes priority with bridge 1.17.0 or newer. With an older bridge, complete current aiming geometry can use the game-rule model, clearly labeled as a prediction. Blocked launches and incomplete inputs still hide the paths.
- Layout planning reserves the entrance's front row and central passage, moves blockers independently of the score-improvement threshold, and checks candidate launch angles against the game's first-contact rule. Verify the proposed placement in game after moving.
- Unofficial fan project; not affiliated with the developer or publisher. Compatibility with every game policy or leaderboard rule has not been established.

## Report a problem

Use [Issues](https://github.com/jellyhani/ball-x-pit-companion/issues) when the repository is available to you. Include the game/companion version, language, resolution, steps, expected result, and actual result. A cropped screenshot is useful. Review logs for personal paths and other private information before sharing; do not upload saves or extracted game assets.

## Development

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -t .
.venv\Scripts\python.exe tools\regress.py
```

Some tests require locally extracted game data and are skipped without it. A green CI run does not prove live game behavior. See [contribution guidelines (Korean)](CONTRIBUTING.md), [technical notes (Korean)](docs/reference/README.ko.md), and the [maintenance loop](IMPROVEMENT_LOOP.md).

Source: [MIT](LICENSE). Third-party components: [NOTICE.md](NOTICE.md).
