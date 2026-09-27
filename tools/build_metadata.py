"""배포 버전 리소스와 빌드 식별자를 만든다. 개인 경로는 산출물에 넣지 않는다."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION, WINDOWS_VERSION


def write_metadata():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    changed = subprocess.check_output(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "src",
            "data",
            "native",
            "tools",
            "main.py",
            "BallxPitCompanion.spec",
            "requirements.txt",
            "requirements-setup.txt",
            "requirements-build.txt",
            "vendor",
            "LICENSE",
            "NOTICE.md",
            "DISCLAIMER.md",
            "PRIVACY.md",
            "README.md",
            "docs",
            "build_exe.ps1",
        ],
        cwd=ROOT,
        text=True,
    )
    folder = ROOT / "build"
    folder.mkdir(exist_ok=True)
    (folder / "build-info.json").write_text(
        json.dumps({"version": APP_VERSION, "revision": revision, "dirty": bool(changed)}, indent=2) + "\n",
        encoding="utf-8",
    )
    value = f"""VSVersionInfo(ffi=FixedFileInfo(filevers={WINDOWS_VERSION!r},prodvers={WINDOWS_VERSION!r},
mask=0x3f,flags=0x2,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[
StringFileInfo([StringTable('040904B0',[
StringStruct('FileDescription','BALL x PIT Companion'),
StringStruct('FileVersion',{APP_VERSION!r}),StringStruct('ProductVersion',{APP_VERSION!r}),
StringStruct('ProductName','BALL x PIT Companion'),StringStruct('OriginalFilename','BallxPitCompanion.exe')])]),
VarFileInfo([VarStruct('Translation',[1033,1200])])])
"""
    (folder / "windows-version.txt").write_text(value, encoding="utf-8")
    return folder


if __name__ == "__main__":
    write_metadata()
