"""게임 창 찾기와 게임 창만 캡처하기 (Windows 전용).

- 창 제목이 아니라 프로세스 실행 파일 이름(Balls.exe)으로 게임 창을 찾는다.
- 좌표는 모두 물리 픽셀이다. Qt 가 프로세스를 모니터별 DPI 인식으로 설정하므로 Win32 좌표와 일치한다.
- 캡처는 게임 클라이언트 영역만 가져온다. 게임이 앞에 있으면 화면 영역(mss), 뒤에 있으면 PrintWindow.
  오버레이 창은 SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)로 캡처에서 빠진다.
"""
from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image, ImageStat

GAME_EXE = "balls.exe"

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_RENDERFULLCONTENT = 0x2
DIB_RGB_COLORS = 0

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.IsWindow.argtypes = [wintypes.HWND]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    pid: int
    origin: Tuple[int, int]      # 클라이언트 영역 왼쪽 위 (화면 물리 좌표)
    size: Tuple[int, int]        # 클라이언트 영역 크기 (물리 픽셀)
    dpi: int
    minimized: bool
    foreground: bool

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.origin[0], self.origin[1], self.size[0], self.size[1])


def _process_path(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_game_window(exe_name: str = GAME_EXE) -> Optional[GameWindow]:
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if os.path.basename(_process_path(pid.value)).lower() == exe_name:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            found.append((rc.right * rc.bottom, hwnd, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return None
    _, hwnd, pid = max(found)
    return describe_window(hwnd, pid)


def describe_window(hwnd: int, pid: int = 0) -> Optional[GameWindow]:
    if not user32.IsWindow(hwnd):
        return None
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    try:
        dpi = int(user32.GetDpiForWindow(hwnd)) or 96
    except OSError:
        dpi = 96
    fg = user32.GetForegroundWindow()
    fg_pid = wintypes.DWORD()
    if fg:
        user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
    return GameWindow(
        hwnd=hwnd, pid=pid, origin=(pt.x, pt.y), size=(rc.right - rc.left, rc.bottom - rc.top),
        dpi=dpi, minimized=bool(user32.IsIconic(hwnd)),
        foreground=(fg == hwnd or (pid != 0 and fg_pid.value == pid)),
    )


def _is_blank(img: Image.Image) -> bool:
    small = img.resize((32, 18))
    return max(ImageStat.Stat(small.convert("L")).extrema[0]) < 8


def capture_print_window(win: GameWindow) -> Optional[Image.Image]:
    w, h = win.size
    if w < 64 or h < 64:
        return None
    hdc_win = user32.GetDC(win.hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbm = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc_mem, hbm)
    try:
        if not user32.PrintWindow(win.hwnd, hdc_mem, PW_RENDERFULLCONTENT):
            return None
        bmi = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, bmi, DIB_RGB_COLORS):
            return None
        img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        return None if _is_blank(img) else img.copy()
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(win.hwnd, hdc_win)


def capture_screen_region(win: GameWindow) -> Optional[Image.Image]:
    import mss
    x, y, w, h = win.rect
    with mss.MSS() as sct:
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return None if _is_blank(img) else img


def capture_game(win: GameWindow) -> Tuple[Optional[Image.Image], str]:
    """(이미지, 사용한 방식). 실패하면 (None, 이유).

    게임이 앞에 있으면 화면에서 게임 영역만 잘라 온다(빠르고, DirectX 게임에서도 안정적).
    오버레이 창은 캡처 제외가 적용되어 섞이지 않는다. 게임이 뒤에 있을 때만 PrintWindow 를 쓴다
    (가려진 창도 그릴 수 있지만 DirectX 게임에서는 느리거나 멈출 수 있다).
    """
    if win.minimized:
        return None, "게임 창이 최소화됨"
    order = ("screen", "print") if win.foreground else ("print", "screen")
    reasons = []
    for method in order:
        try:
            img = capture_screen_region(win) if method == "screen" else capture_print_window(win)
        except Exception as e:  # mss·GDI 오류는 형식이 다양하다
            reasons.append(f"{method}: {e}")
            continue
        if img is not None:
            return img, "화면 영역" if method == "screen" else "PrintWindow"
        reasons.append(f"{method}: 검은 화면")
    return None, "캡처 실패 (" + ", ".join(reasons) + ") — 독점 전체 화면이면 '전체 창 모드'로 바꿔 주세요"


WDA_EXCLUDEFROMCAPTURE = 0x11
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def set_capture_exclusion(hwnd: int, on: bool) -> bool:
    """on=True 면 캡처에서 제외, False 면 스크린샷·녹화에 보이게 한다. 실제 적용된 상태(제외 여부)를 돌려준다."""
    user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if on else 0)
    v = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(v))
    return v.value == WDA_EXCLUDEFROMCAPTURE


def exclude_from_capture(hwnd: int) -> bool:
    """오버레이 창을 화면 캡처에서 제외한다. 실제로 적용됐는지 다시 읽어서 확인한다."""
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        return False
    v = wintypes.DWORD()
    return bool(user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(v))) and v.value == WDA_EXCLUDEFROMCAPTURE


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x80
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def set_click_through(hwnd: int, enabled: bool) -> int:
    """마우스 입력이 오버레이를 지나 게임에 닿게 한다. 적용 후 확장 스타일 값을 돌려준다."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED
    style = style | WS_EX_TRANSPARENT if enabled else style & ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    return user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
