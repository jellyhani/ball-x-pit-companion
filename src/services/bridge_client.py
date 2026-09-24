"""게임 안 BepInEx 플러그인(BallxPitBridge)이 보내는 상태를 받는다.

플러그인이 게임 메모리의 실제 값(선택지, 보유 볼·패시브와 레벨, 골드, 캐릭터, 카드 화면 위치)을
0.2초마다 확인해 바뀔 때 JSON 한 줄로 보낸다. 화면 인식보다 정확하고 빠르다.
연결이 없거나 끊기면(게임 업데이트로 플러그인이 동작하지 않는 경우 등) 앱은 화면 인식으로 돌아간다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
PIPE_NAME = r"\\.\pipe\ballxpit-bridge-" + os.environ.get("USERNAME", "")
STALE_AFTER_S = 5.0          # 플러그인은 최소 2초마다 신호를 보낸다
MAX_LINE = 1_000_000


class BridgeClient(QObject):
    snapshot = pyqtSignal(object, float)     # dict, monotonic 수신 시각
    status_changed = pyqtSignal(str)

    def __init__(self, pipe_name: str = PIPE_NAME, parent=None):
        super().__init__(parent)
        self.pipe_name = pipe_name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.last_at = 0.0
        self.game_version = ""
        self.messages = 0

    @property
    def live(self) -> bool:
        return self.connected and time.monotonic() - self.last_at < STALE_AFTER_S

    def start(self):
        self._thread = threading.Thread(target=self._run, name="bridge", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _set_connected(self, on: bool, why: str = ""):
        if on != self.connected:
            self.connected = on
            text = "게임 연동 연결됨" if on else f"게임 연동 없음{(' — ' + why) if why else ''}"
            log.info(text)
            self.status_changed.emit(text)

    def _run(self):
        while not self._stop.is_set():
            try:
                f = open(self.pipe_name, "rb", buffering=0)
            except OSError:
                self._set_connected(False)
                self._stop.wait(1.5)   # 게임이 꺼져 있거나 플러그인이 없음
                continue
            self._set_connected(True)
            buf = b""
            try:
                while not self._stop.is_set():
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_LINE and b"\n" not in buf:
                        log.warning("브리지 메시지가 너무 김 — 버림")
                        buf = b""
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle(line)
            except OSError:
                pass
            finally:
                try:
                    f.close()
                except OSError:
                    pass
                self._set_connected(False, "연결 끊김")

    def _handle(self, line: bytes):
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            log.warning("브리지 메시지를 읽지 못함 (%d바이트)", len(line))
            return
        if not isinstance(msg, dict) or msg.get("v") != PROTOCOL_VERSION:
            log.warning("브리지 프로토콜 버전이 다름: %s", msg.get("v") if isinstance(msg, dict) else "?")
            return
        self.last_at = time.monotonic()
        self.game_version = msg.get("game_version", "")
        self.messages += 1
        self.snapshot.emit(msg, self.last_at)
