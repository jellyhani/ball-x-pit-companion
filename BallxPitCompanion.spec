# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for the BALL x PIT helper (onedir: Qt/LGPL libraries stay separate, replaceable files).
#   .venv\Scripts\pyinstaller.exe BallxPitCompanion.spec --noconfirm
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))
from tools.build_metadata import write_metadata
from tools.package_notices import collect_notices
metadata_dir = write_metadata()

# 빌드 도구의 PATH에 들어온 Poppler/libheif DLL이 Qt의 Windows 시스템 DLL 대신 묶이지 않게 한다.
# 이 프로세스의 검색 경로만 제한하며 사용자 환경 변수나 시스템 설치는 변경하지 않는다.
if sys.platform == "win32":
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    os.environ["PATH"] = os.pathsep.join(str(p) for p in (
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        system / "System32", system,
    ) if p.is_dir())

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("data/rules.json", "data"),                                   # own rules (game data is extracted at first run)
    ("data/community.json", "data"),
    ("data/i18n", "data/i18n"),                                     # 앱 화면 문구 번역 (src/i18n.py)
    ("src/engine/bxp_native.dll", "src/engine"),                    # native compute module (harvest sim, no CRT deps)                               # community tier lists / builds (opinions, sourced)
    ("vendor/bepinex/BallxPitBridge.dll", "vendor/bepinex"),       # our bridge plugin (BepInEx is downloaded)
    ("LICENSE", "."),
    ("NOTICE.md", "."),
    ("DISCLAIMER.md", "."),
    ("PRIVACY.md", "."),
    ("README.md", "."),
    ("docs", "docs"),
    (str(metadata_dir / "build-info.json"), "."),
]
datas += collect_notices()
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
# Qt는 Windows가 제공하는 ICU API를 사용한다. 다른 도구의 동명 ICU는 필요한 ucnv_* 심벌이 다르다.
a.binaries = [x for x in a.binaries if Path(x[0]).name.lower() != "icuuc.dll"
              and not Path(x[0]).name.lower().startswith(("api-ms-win-", "ext-ms-win-"))]
a.datas = [x for x in a.datas if not any(d in x[0] or d in x[1] for d in _DROP)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BallxPitCompanion", console=False,
          version=str(metadata_dir / "windows-version.txt"), icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="BallxPitCompanion")
