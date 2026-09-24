"""Windows 실제 동작 확인 (개발용): 실제 HUD 창을 띄워 캡처 제외·클릭 통과·포커스 유지를 잰다.

화면 내용은 저장하지 않는다. HUD가 있을 때와 없을 때 같은 영역의 픽셀 차이만 계산한다.
    .venv\\Scripts\\python.exe tools\\verify_windows_behavior.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    import mss
    from PIL import Image, ImageChops, ImageStat
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from src.services import game_window as gw
    from src.ui.hud import RecommendationHud
    from src.ui import tokens as tk
    from tests.test_ui import sample_recommendation
    from tests.helpers import game_data

    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def grab(rect):
        x, y, w, h = rect
        with mss.MSS() as s:
            shot = s.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    hud = RecommendationHud(game_data(), 1.0)
    hud.show_recommendation(sample_recommendation())
    hud.adjustSize()
    screen = app.primaryScreen()
    dpr = screen.devicePixelRatio()
    geo = screen.availableGeometry()
    hud.move(QPoint(geo.right() - hud.width() - 40, geo.top() + 120))
    fg_before = user32.GetForegroundWindow()

    # 1) HUD 없이 캡처
    rect_l = hud.geometry()
    rect = (int(rect_l.x()), int(rect_l.y()), int(rect_l.width() * dpr), int(rect_l.height() * dpr))
    before = grab(rect)
    hud.show()
    pump(0.6)
    hwnd = int(hud.winId())
    with_hud = grab(rect)
    fg_after = user32.GetForegroundWindow()
    capture_diff = ImageStat.Stat(ImageChops.difference(before, with_hud).convert("L")).mean[0]

    # 비교용: 캡처 제외를 끈 상태
    user32.SetWindowDisplayAffinity(hwnd, 0)
    pump(0.4)
    visible = grab(rect)
    visible_diff = ImageStat.Stat(ImageChops.difference(before, visible).convert("L")).mean[0]
    gw.exclude_from_capture(hwnd)

    # 2) 클릭 통과: HUD 가운데 좌표의 최상위 창이 HUD 인지
    cx = rect[0] + rect[2] // 2
    cy = rect[1] + rect[3] // 2
    point = ctypes.c_longlong((cy << 32) | (cx & 0xFFFFFFFF))
    top = user32.WindowFromPoint(point)
    top_root = user32.GetAncestor(top, 2) if top else None
    click_through = top_root != hwnd

    # 3) 위치 조정 모드에서는 클릭을 받는지
    hud.set_edit_mode(True)
    pump(0.3)
    top2 = user32.WindowFromPoint(point)
    edit_receives = (user32.GetAncestor(top2, 2) if top2 else None) == hwnd
    hud.set_edit_mode(False)

    print(f"화면 배율 {dpr:.2f}, HUD {rect_l.width()}×{rect_l.height()} (논리), 글꼴 {tk.family()}")
    print(f"캡처 제외 API 적용 확인: {hud.capture_excluded}")
    print(f"캡처 차이(제외 켬): {capture_diff:.2f}  / 비교(제외 끔): {visible_diff:.2f}")
    print(f"클릭 통과: {click_through} (HUD 확장 스타일 투명 {hud.click_through})")
    print(f"위치 조정 모드에서 클릭 받음: {edit_receives}")
    print(f"포커스 유지: {fg_before == fg_after} (전 {fg_before}, 후 {fg_after})")
    ok = hud.capture_excluded and capture_diff < 1.0 and visible_diff > 5 and click_through and edit_receives \
        and fg_before == fg_after
    hud.close()
    print("결과:", "통과" if ok else "확인 필요")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
