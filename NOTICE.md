# Third-party notices

This repository contains only original source code (MIT, see LICENSE) and one binary built from it
(`vendor/bepinex/BallxPitBridge.dll`, source in `tools/bepinex/BallxPitBridge`).

## Not included — obtained on the user's PC at setup time

| What | Where it comes from | License / owner |
|---|---|---|
| Game text, icons, portraits | Extracted locally from the user's own BALL x PIT install (`tools/setup_data.py`) | © the game's developer and publisher. Not redistributed. |
| BepInEx 6.0.0-be.788 (Unity IL2CPP, win-x64) | Downloaded from https://builds.bepinex.dev (official), SHA-256 `F4CC496BD098A0DF4164B81E3737297707F13A47C2478DBA2F60EEFAB784817A` | LGPL-2.1 — https://github.com/BepInEx/BepInEx |

## Python dependencies (installed from PyPI by `setup.ps1`)

| Package | License |
|---|---|
| PyQt6 | GPL v3 (or commercial). Any bundled binary of this app is therefore distributed under GPL v3 terms; the source here stays MIT. |
| Pillow | MIT-CMU (HPND) |
| numpy | BSD-3-Clause |
| mss | MIT |
| pynput | LGPL-3.0 |
| winrt-* (Windows Runtime projections) | MIT |
| UnityPy (setup only) | MIT |

## Facts referenced (no text or data copied)

- BALL x PIT Wiki (wiki.gg) — https://ballxpit.wiki.gg
- Steam Community guides linked in README.md
- StonedModder/BallxPitxApp research docs — https://github.com/StonedModder/BallxPitxApp

BALL x PIT is a trademark of its respective owners. This project is unofficial and not affiliated with them.
