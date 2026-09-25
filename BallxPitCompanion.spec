# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for the BALL x PIT helper (onedir: Qt/LGPL libraries stay separate, replaceable files).
#   .venv\Scripts\pyinstaller.exe BallxPitCompanion.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("data/rules.json", "data"),                                   # own rules (game data is extracted at first run)
    ("data/community.json", "data"),
    ("src/engine/bxp_native.dll", "src/engine"),                    # native compute module (harvest sim, no CRT deps)                               # community tier lists / builds (opinions, sourced)
    ("vendor/bepinex/BallxPitBridge.dll", "vendor/bepinex"),       # our bridge plugin (BepInEx is downloaded)
    ("LICENSE", "."),
    ("NOTICE.md", "."),
]
binaries = []
hiddenimports = collect_submodules("winrt") + [
    "tools.setup_data", "tools.extract_game_text", "tools.extract_icons", "src.engine.sim_jobs",
]
for pkg in ("UnityPy", "texture2ddecoder", "etcpak", "astc_encoder", "fmod_toolkit", "tpk_ar", "archspec"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt6", "PyQt5", "pytest"],
    noarchive=False,
    # LGPL 파이썬 패키지는 압축 아카이브가 아니라 .py 파일 그대로 넣어 사용자가 바꿔 끼울 수 있게
    module_collection_mode={"pynput": "py"},
)
# 쓰지 않는 큰 파일 빼기: 소프트웨어 OpenGL(20MB), Qt 번역, Pillow AVIF 모듈
_DROP = ("opengl32sw.dll", "_avif.", "translations")
a.binaries = [x for x in a.binaries if not any(d in x[0] or d in x[1] for d in _DROP)]
a.datas = [x for x in a.datas if not any(d in x[0] or d in x[1] for d in _DROP)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BallxPitCompanion", console=False,
          version=None, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="BallxPitCompanion")
