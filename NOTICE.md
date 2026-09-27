# Third-party notices

The original application code is MIT-licensed (see LICENSE). Two binaries are built from that code:
`vendor/bepinex/BallxPitBridge.dll` (source in `tools/bepinex/BallxPitBridge`) and
`src/engine/bxp_native.dll` (source in `native`). Third-party license texts are kept separately in `vendor/licenses`.

Packaged builds also contain Python and third-party dependencies. Their license texts and a versioned
inventory are included under `_internal/third_party/`. The inventory covers runtime and build dependencies;
listing a build tool does not mean that its entire code is part of the executable.

Each binary release must also provide its matching `dependency-sources.zip` asset. It contains
the exact unmodified Qt Base, Qt SVG, Qt for Python/Shiboken, and pynput source archives.
See [corresponding source access and maintainer instructions](docs/DEPENDENCY_SOURCES.md).
The source manifest records official URLs and SHA-256 digests; library versions and bundled Qt
modules are checked during packaging. License texts alone do not replace source obligations.

Qt/PySide, Shiboken, and pynput are used without local source modifications. The onedir package keeps
shared libraries as separate files, and pynput's Python modules remain replaceable source files.
You may replace compatible library files or rebuild the application with modified versions using the
provided build recipe. The application imposes no additional restriction on debugging such modifications.

Upstream source locations: [Qt](https://code.qt.io/cgit/qt/qtbase.git/),
[PySide and Shiboken](https://code.qt.io/cgit/pyside/pyside-setup.git/),
[pynput](https://github.com/moses-palmer/pynput), and [PyWinRT](https://github.com/pywinrt/pywinrt).
Use the versions recorded in `dependency-notices.json` when obtaining corresponding sources.

## Not included — obtained on the user's PC at setup time

| What | Where it comes from | License / owner |
|---|---|---|
| Game text, icons, portraits | Extracted locally from the user's own BALL x PIT install (`tools/setup_data.py`) | © the game's developer and publisher. Not redistributed. |
| BepInEx 6.0.0-be.788 (Unity IL2CPP, win-x64) | Downloaded from https://builds.bepinex.dev (official), SHA-256 `F4CC496BD098A0DF4164B81E3737297707F13A47C2478DBA2F60EEFAB784817A` | LGPL-2.1 — https://github.com/BepInEx/BepInEx |

## Python dependencies (installed from PyPI by `setup.ps1`)

| Package | License |
|---|---|
| PySide6-Essentials / shiboken6 (Qt for Python) | LGPL-3.0 (Qt libraries, dynamically linked — users may replace them). |
| Pillow | MIT-CMU (HPND) |
| numpy | BSD-3-Clause |
| mss | MIT |
| pynput | LGPL-3.0 |
| winrt-* (Windows Runtime projections) | MIT |
| UnityPy (data extraction, also bundled in the packaged app) | MIT |

## Research references and community assessments

Game mechanics and community assessments inform the project's own explanations and heuristic
rules. Community rankings are opinions, not official game values or endorsements. Source
references are retained in `data/community.json`, `src/engine/construction_policy.py`, and the
[technical notes](docs/reference/README.ko.md#자료-출처). We do not claim ownership of the referenced
articles, their original expression, or third-party datasets. The MIT grant for this project's
original software does not relicense third-party material. Attribution alone does not grant
permission to copy a source's protected content.

- BALL x PIT Wiki (wiki.gg) — https://ballxpit.wiki.gg
- Steam Community guides linked in README.md
- StonedModder/BallxPitxApp research docs — https://github.com/StonedModder/BallxPitxApp

BALL x PIT is a trademark of its respective owners. This project is unofficial and not affiliated with them.

See [DISCLAIMER.md](DISCLAIMER.md) for the project's warranty/limitation notice and rights-concern
reporting route, and [PRIVACY.md](PRIVACY.md) for local records and installation downloads.
