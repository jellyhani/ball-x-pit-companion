"""게임이 남기는 Unity Player.log 를 읽어 런 시작·종료와 새로고침을 알아낸다.

게임을 변경하지 않는 읽기 전용 연동이다. 확인된 로그 형식(버전 1.301):
  메시지 줄
  UnityEngine.DebugLogHandler:Internal_Log(...)
  UnityEngine.Logger:Log(...)
  UnityEngine.Debug:Log(Object)
  호출한 메서드
  ...
  (빈 줄)

사용하는 신호
  "kSelectingLevel -> kEnteringLvl"  (BaseMgr:SetState)   런 시작
  "kNormal -> kReturningFromLvl"     (BaseMgr:SetState)   기지로 복귀 = 런 종료
  "... bonus gold dropped ..."       (GameMgr:MarkLevelComplete) 층 클리어
  스택에 _EnterGameOver                                     패배
  "skippin kX since it was just in the pool" + 스택에 _AnimateReroll   강화 새로고침
  "Launched version '...'"                                  게임 버전
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List

from PySide6.QtCore import QObject, QTimer, Signal

from ..i18n import tr

log = logging.getLogger(__name__)

DEFAULT_LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "Kenny Sun", "BALL x PIT")

_STATE_RE = re.compile(r"^(k\w+) -> (k\w+)$")
_SKIP_RE = re.compile(r"^skippin (k\w+) since it was just in the pool")
_VERSION_RE = re.compile(r"^Launched version '([^']+)'")


@dataclass(frozen=True)
class LogEvent:
    kind: str  # version | base_state | run_started | run_ended | level_complete | game_over | reroll | pool_skip
    value: str = ""
    extra: str = ""


class PlayerLogParser:
    """텍스트 조각을 받아 이벤트 목록을 돌려준다. 줄이 중간에 끊겨도 다음 조각과 이어 붙인다."""

    def __init__(self):
        self._pending = ""
        self._reroll_block_open = False

    def feed(self, chunk: str) -> List[LogEvent]:
        text = self._pending + chunk.replace("\r\n", "\n")
        # 마지막 빈 줄 이후는 아직 완성되지 않은 블록일 수 있다.
        cut = text.rfind("\n\n")
        if cut < 0:
            self._pending = text
            return []
        self._pending = text[cut + 2 :]
        events: List[LogEvent] = []
        for block in text[:cut].split("\n\n"):
            events.extend(self._parse_block(block))
        return events

    def _parse_block(self, block: str) -> List[LogEvent]:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            return []
        try:
            step_index = next(
                index
                for index, ln in enumerate(lines)
                if ln.startswith("UnityEngine.DebugLogHandler:Internal_Log")
            )
        except StopIteration:
            return []
        message = lines[step_index - 1].strip() if step_index > 0 else ""
        stack = [ln for ln in lines[step_index + 1 :] if not ln.startswith("UnityEngine.")]
        caller = stack[0] if stack else ""
        stack_text = "\n".join(stack)
        result: List[LogEvent] = []

        m = _VERSION_RE.match(message)
        if m:
            result.append(LogEvent("version", m.group(1)))
        m = _STATE_RE.match(message)
        if m and caller.startswith("BaseMgr:SetState"):
            old, new = m.groups()
            result.append(LogEvent("base_state", new, old))
            if new == "kEnteringLvl":
                result.append(LogEvent("run_started"))
            elif new == "kReturningFromLvl":
                result.append(LogEvent("run_ended"))
        if caller.startswith("GameMgr:MarkLevelComplete"):
            result.append(LogEvent("level_complete"))
        if "_EnterGameOver" in stack_text:
            result.append(LogEvent("game_over"))
        m = _SKIP_RE.match(message)
        if m:
            if "_AnimateReroll" in stack_text:
                result.append(LogEvent("reroll", m.group(1)))
            else:
                result.append(LogEvent("pool_skip", m.group(1)))
        return result


def summarize_phase(events: List[LogEvent]) -> str:
    """기존 로그 전체에서 현재 위치를 판단한다: in_run | base | unknown."""
    phase = "unknown"
    for event in events:
        if event.kind == "run_started":
            phase = "in_run"
        elif event.kind == "run_ended":
            phase = "base"
        elif event.kind == "base_state" and phase == "unknown":
            phase = "base"
    return phase


class PlayerLogWatcher(QObject):
    """Player.log 를 주기적으로 읽는다. 게임이 다시 시작되어 파일이 줄어들면 처음부터 읽는다."""

    log_event = Signal(object, float)  # LogEvent, monotonic time
    synced = Signal(str, str)  # phase, game_version
    status_changed = Signal(str)

    def __init__(self, log_dir: str = DEFAULT_LOG_DIR, interval_ms: int = 250, parent=None):
        super().__init__(parent)
        self.path = os.path.join(log_dir, "Player.log")
        self._pos = 0
        self._parser = PlayerLogParser()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self.phase = "unknown"
        self.game_version = ""
        self.available = False

    def start(self):
        self._initial_sync()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _read_from(self, position: int) -> str:
        with open(self.path, "rb") as file_handle:
            file_handle.seek(position)
            data = file_handle.read()
        self._pos = position + len(data)
        return data.decode("utf-8", errors="replace")

    def _initial_sync(self):
        if not os.path.exists(self.path):
            self.available = False
            self.status_changed.emit(tr("게임 로그 없음 (게임을 한 번 실행하면 생성됩니다)"))
            self.synced.emit("unknown", "")
            return
        self.available = True
        self._parser = PlayerLogParser()
        events = self._parser.feed(self._read_from(0))
        self.phase = summarize_phase(events)
        self.game_version = next((e.value for e in reversed(events) if e.kind == "version"), "")
        self.status_changed.emit(tr("게임 로그 연결됨"))
        log.info("Player.log 동기화: phase=%s version=%s", self.phase, self.game_version)
        self.synced.emit(self.phase, self.game_version)

    def _poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            if self.available:
                self.available = False
                self.status_changed.emit(tr("게임 로그를 읽을 수 없음"))
            return
        if not self.available or size < self._pos:
            # 게임을 다시 실행하면 Player.log 가 새로 만들어진다.
            log.info("Player.log 새로 시작됨 (게임 재실행)")
            self._initial_sync()
            return
        if size == self._pos:
            return
        now = time.monotonic()
        for event in self._parser.feed(self._read_from(self._pos)):
            if event.kind == "version":
                self.game_version = event.value
            elif event.kind == "run_started":
                self.phase = "in_run"
            elif event.kind == "run_ended":
                self.phase = "base"
            self.log_event.emit(event, now)
