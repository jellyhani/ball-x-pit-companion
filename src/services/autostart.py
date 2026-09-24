"""윈도우 로그인 시 자동 실행 (현재 사용자 Run 키). 설정에서 켤 때만 등록하고, 끄면 지운다."""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "BallxPitCompanion"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def command() -> str:
    exe = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exe}" "{os.path.join(ROOT, "main.py")}" --tray'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, VALUE)[0] == command()
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
        log.info("윈도우 시작 시 자동 실행: %s", "켬" if on else "끔")
        return True
    except OSError:
        log.exception("자동 실행 설정 실패")
        return False
