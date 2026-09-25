"""게임 연동 모드(BepInEx + BallxPitBridge 플러그인) 상태 확인과 자동 설치.

- 설치 파일은 프로젝트 안 vendor/bepinex 에 들어 있다 (인터넷에서 받지 않는다).
  BepInEx 압축 파일은 SHA-256 으로 확인한 뒤에만 푼다.
- 게임이 켜져 있으면 설치하지 않는다 (게임이 파일을 잡고 있다). 도우미가 게임 종료를 기다렸다가 설치한다.
- 사용자가 BepInEx 를 꺼 두었으면(doorstop enabled=false) 자동으로 켜지 않는다. 알리기만 한다.
- 게임 원본 파일은 바꾸지 않는다. 추가한 파일 목록은 tools/bepinex/installed_files.txt 에 남아
  `bridge.ps1 uninstall` 로 지울 수 있다.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import sys
import re
import shutil
import subprocess
import time
import zipfile
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDOR = os.path.join(ROOT, "vendor", "bepinex")
BEPINEX_NAME = "BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.788.zip"
BEPINEX_ZIP = os.path.join(VENDOR, BEPINEX_NAME)
# 공개판은 BepInEx 를 저장소에 넣지 않는다 — 설치할 때 공식 빌드 서버에서 받아 SHA-256 으로 확인한다.
BEPINEX_URL = "https://builds.bepinex.dev/projects/bepinex_be/788/BepInEx-Unity.IL2CPP-win-x64-6.0.0-be.788%2B5b766a3.zip"
BEPINEX_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BallxPitCompanion", "cache",
                             BEPINEX_NAME)
BEPINEX_SHA256 = "F4CC496BD098A0DF4164B81E3737297707F13A47C2478DBA2F60EEFAB784817A"
PLUGIN_DLL = os.path.join(VENDOR, "BallxPitBridge.dll")
PLUGIN_VERSION = "1.11.0"          # Plugin.cs 의 Plugin.Version 과 같아야 한다
# 추가한 파일 목록 (지우기용). exe 는 설치 폴더가 읽기 전용일 수 있고 tools 폴더도 없어 사용자 자료 폴더에 둔다.
MANIFEST = (os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BallxPitCompanion", "installed_files.txt")
            if getattr(sys, "frozen", False) else os.path.join(ROOT, "tools", "bepinex", "installed_files.txt"))
APP_ID = "2062430"
GAME_EXE = "Balls.exe"
STATE_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "BallxPitCompanion", "mod_state.json")


# ---- Steam 설치 위치 ----
def find_game_dir() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
    except OSError:
        return None
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        with open(vdf, encoding="utf-8", errors="replace") as f:
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    for lib in libs:
        manifest = os.path.join(lib, "steamapps", f"appmanifest_{APP_ID}.acf")
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                path = os.path.join(lib, "steamapps", "common", m.group(1))
                if os.path.exists(os.path.join(path, GAME_EXE)):
                    return os.path.normpath(path)
    return None


def read_build_id(game_dir: str) -> Optional[str]:
    manifest = os.path.join(os.path.dirname(os.path.dirname(game_dir)), f"appmanifest_{APP_ID}.acf")
    try:
        with open(manifest, encoding="utf-8", errors="replace") as f:
            m = re.search(r'"buildid"\s+"(\d+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return GAME_EXE.lower() in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# ---- 파일 버전 (Windows 버전 리소스) ----
def file_version(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    try:
        ver = ctypes.WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        p = ctypes.c_void_p()
        n = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n)):
            return None
        ffi = ctypes.cast(p, ctypes.POINTER(wintypes.DWORD * 13)).contents
        ms, ls = ffi[2], ffi[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    except OSError:
        return None


def _doorstop_enabled(game_dir: str) -> Optional[bool]:
    ini = os.path.join(game_dir, "doorstop_config.ini")
    try:
        with open(ini, encoding="utf-8", errors="replace") as f:
            m = re.search(r"^\s*enabled\s*=\s*(\w+)", f.read(), re.M)
        return None if m is None else m.group(1).lower() == "true"
    except OSError:
        return None


@dataclass
class ModStatus:
    game_dir: Optional[str]
    build_id: Optional[str] = None
    bepinex: bool = False
    enabled: Optional[bool] = None
    plugin_version: Optional[str] = None
    running: bool = False
    vendor_ok: bool = False

    @property
    def plugin_current(self) -> bool:
        return self.plugin_version == PLUGIN_VERSION

    @property
    def ok(self) -> bool:
        return bool(self.game_dir) and self.bepinex and self.enabled is not False and self.plugin_current

    @property
    def needs_install(self) -> bool:
        """자동으로 고칠 수 있는 문제(설치 안 됨·플러그인 옛 버전)."""
        return bool(self.game_dir) and (not self.bepinex or not self.plugin_current)

    @property
    def summary(self) -> str:
        if not self.game_dir:
            return "게임 설치 폴더를 찾지 못함 (Steam 라이브러리)"
        if not self.bepinex:
            return "게임 연동 모드(BepInEx) 없음"
        if self.enabled is False:
            return "BepInEx 가 꺼져 있음 (doorstop enabled=false)"
        if self.plugin_version is None:
            return "연동 플러그인 없음"
        if not self.plugin_current:
            return f"연동 플러그인 옛 버전 {self.plugin_version} (최신 {PLUGIN_VERSION})"
        return f"설치됨 · 플러그인 {self.plugin_version}"


def check(game_dir: Optional[str] = None) -> ModStatus:
    game_dir = game_dir or find_game_dir()
    st = ModStatus(game_dir, vendor_ok=os.path.exists(PLUGIN_DLL))     # BepInEx 는 없으면 설치 때 받는다
    if not game_dir:
        return st
    st.build_id = read_build_id(game_dir)
    st.bepinex = (os.path.exists(os.path.join(game_dir, "BepInEx", "core", "BepInEx.Core.dll"))
                  and os.path.exists(os.path.join(game_dir, "winhttp.dll")))
    st.enabled = _doorstop_enabled(game_dir) if st.bepinex else None
    st.plugin_version = file_version(os.path.join(game_dir, "BepInEx", "plugins", "BallxPitBridge.dll"))
    st.running = game_running()
    return st


# ---- 설치 ----
class InstallError(RuntimeError):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _record(files: List[str]):
    try:
        existing = []
        if os.path.exists(MANIFEST):
            with open(MANIFEST, encoding="utf-8") as f:
                existing = [x.strip() for x in f if x.strip()]
        merged = list(dict.fromkeys(existing + files))
        os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
        with open(MANIFEST, "w", encoding="utf-8") as f:
            f.write("\n".join(merged) + "\n")
    except OSError:
        log.exception("설치 파일 목록 기록 실패")


def _save_state(st: ModStatus):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"build_id": st.build_id, "plugin": st.plugin_version, "at": time.time()}, f)
    except OSError:
        pass


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def bepinex_zip(say: Callable[[str], None] = log.info) -> str:
    """BepInEx 압축 파일 경로. 저장소에 없으면 공식 빌드 서버에서 받아 캐시한다 (SHA-256 확인)."""
    for path in (BEPINEX_ZIP, BEPINEX_CACHE):
        if os.path.exists(path) and _sha256(path) == BEPINEX_SHA256:
            return path
    import urllib.request
    os.makedirs(os.path.dirname(BEPINEX_CACHE), exist_ok=True)
    tmp = BEPINEX_CACHE + ".part"
    say(f"BepInEx 내려받는 중 (공식 빌드 서버, 약 34MB): {BEPINEX_URL}")
    try:
        with urllib.request.urlopen(BEPINEX_URL, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        raise InstallError(f"BepInEx 를 내려받지 못했습니다: {e}")
    if _sha256(tmp) != BEPINEX_SHA256:
        os.remove(tmp)
        raise InstallError("내려받은 BepInEx 가 예상 파일과 다릅니다 (SHA-256 불일치) — 설치 중단")
    os.replace(tmp, BEPINEX_CACHE)
    return BEPINEX_CACHE


def install(st: Optional[ModStatus] = None, say: Callable[[str], None] = log.info) -> ModStatus:
    """없는 것만 설치한다: BepInEx(없을 때) + 플러그인(없거나 옛 버전일 때). 게임이 꺼져 있어야 한다."""
    st = st or check()
    if not st.game_dir:
        raise InstallError("게임 설치 폴더를 찾지 못했습니다")
    if game_running():
        raise InstallError("게임이 실행 중입니다 — 게임을 끈 뒤 설치합니다")
    if not st.vendor_ok:
        raise InstallError(f"설치 파일이 없습니다: {VENDOR}")
    added: List[str] = []
    if not st.bepinex:
        say("BepInEx 설치 파일 확인 중 (SHA-256)")
        zip_path = bepinex_zip(say)
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                name = info.filename.replace("/", os.sep)
                dst = os.path.normpath(os.path.join(st.game_dir, name))
                if not dst.startswith(os.path.normpath(st.game_dir) + os.sep):
                    raise InstallError(f"압축 파일 경로가 이상합니다: {info.filename}")
                if info.is_dir():
                    os.makedirs(dst, exist_ok=True)
                    continue
                if os.path.exists(dst):
                    continue          # 이미 있는 파일은 건드리지 않는다
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with z.open(info) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                added.append(name)
        say(f"BepInEx 설치 완료 (파일 {len(added)}개)")
    plugins = os.path.join(st.game_dir, "BepInEx", "plugins")
    os.makedirs(plugins, exist_ok=True)
    dst = os.path.join(plugins, "BallxPitBridge.dll")
    if file_version(dst) != PLUGIN_VERSION:
        shutil.copy2(PLUGIN_DLL, dst)
        added.append(os.path.join("BepInEx", "plugins", "BallxPitBridge.dll"))
        say(f"연동 플러그인 {PLUGIN_VERSION} 설치")
    _record(added)
    new = check(st.game_dir)
    _save_state(new)
    if not new.plugin_current:
        raise InstallError(f"설치 뒤 확인 실패: {new.summary}")
    return new


def wait_and_install(say: Callable[[str], None] = log.info, poll: float = 5.0,
                     stop: Callable[[], bool] = lambda: False) -> Optional[ModStatus]:
    """게임이 꺼질 때까지 기다렸다가 설치한다 (도우미 앱 백그라운드용)."""
    while not stop():
        if not game_running():
            time.sleep(2.0)   # 게임이 파일을 놓을 시간
            return install(say=say)
        time.sleep(poll)
    return None


def bepinex_log_problems(game_dir: str, max_bytes: int = 200_000) -> List[str]:
    """BepInEx 로그에서 우리 플러그인 오류만 뽑는다 (게임 업데이트로 깨졌는지 판단용)."""
    path = os.path.join(game_dir, "BepInEx", "LogOutput.log")
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if ("BALL x PIT Bridge" in line or "BallxPitBridge" in line) and ("Error" in line or "실패" in line
                                                                         or "Exception" in line):
            out.append(line.strip()[:300])
    return out[-5:]
