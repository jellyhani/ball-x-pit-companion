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

from PySide6.QtCore import QObject, Signal

from ..i18n import tr
from . import diagnostics

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
PIPE_NAME = r"\\.\pipe\ballxpit-bridge-" + os.environ.get("USERNAME", "")
STALE_AFTER_S = 5.0  # 플러그인은 최소 2초마다 신호를 보낸다
MAX_LINE = 1_000_000


class BridgeClient(QObject):
    snapshot = Signal(object, float)  # dict, monotonic 수신 시각
    status_changed = Signal(str)

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
            text = tr("게임 연동 연결됨") if on else tr("게임 연동 없음{v0}", v0=(" — " + why) if why else "")
            log.info(text)
            diagnostics.emit("bridge.connection", connected=on, messages=self.messages)
            self.status_changed.emit(text)

    def _run(self):
        while not self._stop.is_set():
            try:
                file_handle = open(self.pipe_name, "rb", buffering=0)
            except OSError:
                self._set_connected(False)
                self._stop.wait(1.5)  # 게임이 꺼져 있거나 플러그인이 없음
                continue
            self._set_connected(True)
            buffer = b""
            try:
                while not self._stop.is_set():
                    chunk = file_handle.read(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    if len(buffer) > MAX_LINE and b"\n" not in buffer:
                        log.warning("브리지 메시지가 너무 김 — 버림")
                        diagnostics.emit("bridge.rejected", stream="length", reason="oversized", bytes=len(buffer))
                        buffer = b""
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        self._handle(line)
            except OSError:
                pass
            finally:
                try:
                    file_handle.close()
                except OSError:
                    pass
                self._set_connected(False, tr("연결 끊김"))

    def _handle(self, line: bytes):
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            log.warning("브리지 메시지를 읽지 못함 (%d바이트)", len(line))
            diagnostics.emit("bridge.rejected", stream="decode", reason="invalid_json", bytes=len(line))
            return
        if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
            log.warning(
                "브리지 프로토콜 버전이 다름: %s", message.get("v") if isinstance(message, dict) else "?"
            )
            diagnostics.emit("bridge.rejected", stream="version", reason="protocol_version", bytes=len(line))
            return
        previous_at = self.last_at
        self.last_at = time.monotonic()
        self.game_version = message.get("game_version", "")
        self.messages += 1
        message_kind = "catalog" if "catalog" in message else "meta" if "meta" in message else "state"
        diagnostics.emit("bridge.received", stream=message_kind, state=message.get("plugin"),
                         kind=message_kind, sequence=message.get("seq"), plugin=message.get("plugin"),
                         bytes=len(line), messages=self.messages,
                         gap_ms=round((self.last_at - previous_at) * 1000, 1) if previous_at else None,
                         plugin_read_ms=message.get("cost_ms"))
        self.snapshot.emit(message, self.last_at)
