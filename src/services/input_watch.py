"""전역 단축키와 마우스 클릭 기록 (pynput).

- 키를 누르고 있으면 Windows가 같은 키를 반복해서 보낸다. 떼기 전까지는 한 번만 처리한다.
- 마우스 클릭은 선택 결과를 판단하는 보조 증거로만 쓴다. 좌표와 시각 외에는 저장하지 않는다.
- pynput 콜백은 별도 스레드에서 오므로 Qt 시그널로 GUI 스레드에 넘긴다.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional, Set

from PySide6.QtCore import QObject, Signal

from ..i18n import tr

log = logging.getLogger(__name__)


class KeyDebouncer:
    """눌림 상태를 기억해 반복 입력을 무시한다. 테스트하기 쉽게 pynput 과 분리했다."""

    def __init__(self, handlers: Dict[str, Callable[[], None]]):
        self._handlers = handlers
        self._down: Set[str] = set()

    def press(self, name: str):
        if name in self._down:
            return
        self._down.add(name)
        handler = self._handlers.get(name)
        if handler:
            handler()

    def release(self, name: str):
        self._down.discard(name)


class InputWatcher(QObject):
    toggle_detail = Signal()   # F7 HUD 간단히 / 자세히
    resync = Signal()          # F8
    toggle_hud = Signal()      # F9
    toggle_window = Signal()   # F10
    clicked = Signal(int, int, float)   # 화면 물리 좌표, monotonic
    status_changed = Signal(str)
    extend_aim = Signal(bool)  # Shift를 누르는 동안 경로 펼치기 (입력 전달은 건드리지 않음)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kb = None
        self._mouse = None
        self._debouncer = KeyDebouncer({
            "f7": self.toggle_detail.emit, "f8": self.resync.emit, "f9": self.toggle_hud.emit, "f10": self.toggle_window.emit,
        })
        self.keyboard_ok = False
        self.mouse_ok = False
        self._shift_down: Set[str] = set()

    def _key_event(self, name: str, pressed: bool):
        if name in ("shift", "shift_l", "shift_r"):
            before = bool(self._shift_down)
            if pressed:
                self._shift_down.add(name)
            else:
                self._shift_down.discard(name)
            if before != bool(self._shift_down):
                self.extend_aim.emit(bool(self._shift_down))
        if pressed:
            self._debouncer.press(name)
        else:
            self._debouncer.release(name)

    @staticmethod
    def _key_name(key) -> Optional[str]:
        name = getattr(key, "name", None)
        return name.lower() if name else None

    def start(self):
        try:
            from pynput import keyboard, mouse
        except ImportError as e:
            self.status_changed.emit(tr("단축키 사용 불가: {e}", e=e))
            return
        try:
            self._kb = keyboard.Listener(
                on_press=lambda k: self._key_event(self._key_name(k) or "", True),
                on_release=lambda k: self._key_event(self._key_name(k) or "", False),
            )
            self._kb.daemon = True
            self._kb.start()
            self.keyboard_ok = True
        except Exception as e:
            log.exception("키보드 후킹 실패")
            self.status_changed.emit(tr("단축키 사용 불가: {e}", e=e))
        try:
            def on_click(x, y, button, pressed):
                if not pressed and getattr(button, "name", "") == "left":
                    self.clicked.emit(int(x), int(y), time.monotonic())
            self._mouse = mouse.Listener(on_click=on_click)
            self._mouse.daemon = True
            self._mouse.start()
            self.mouse_ok = True
        except Exception as e:
            log.exception("마우스 후킹 실패")
            self.status_changed.emit(tr("클릭 기록 사용 불가: {e}", e=e))

    def stop(self):
        for listener in (self._kb, self._mouse):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._kb = self._mouse = None
        if self._shift_down:
            self._shift_down.clear()
            self.extend_aim.emit(False)
