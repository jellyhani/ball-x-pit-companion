"""계산 전용 프로세스를 두고, 채널마다 '가장 최근 요청만' 처리한다 (조준이 바뀌는 동안 요청이 쌓이지 않게).

- 빠른 줄(now: 지금 조준의 궤적, 15ms 안팎)과 무거운 줄(sweep·layout: 0.7초~수 초)을 다른 프로세스로 나눠,
  무거운 계산 중에도 조준 궤적이 바로 따라온다.
- 결과는 Qt 시그널로 화면 스레드에 넘어온다. 프로세스를 못 만들면(드문 환경) 스레드로 대신 돌린다.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

FAST_CHANNELS = ("now",)
# 채널 → 계산 프로세스 줄: 지금 조준(빠름) / 각도 탐색 / 배치 최적화(수 초) — 서로 기다리지 않게
LANES = {"now": "fast", "sweep": "heavy", "layout": "layout"}


class SimWorker(QObject):
    done = Signal(str, object, object)     # (채널, 요청 키, 결과 — 실패하면 None)

    def __init__(self, use_process: bool = True):
        super().__init__()
        self._use_process = use_process
        self._ex: Dict[str, Executor] = {}
        self._lock = threading.Lock()
        self._busy: Dict[str, bool] = {}
        self._pending: Dict[str, Tuple[Any, Callable, tuple]] = {}
        self._closed = False

    def _executor(self, channel: str) -> Executor:
        lane = LANES.get(channel, "heavy")
        ex = self._ex.get(lane)
        if ex is None:
            if self._use_process:
                try:
                    ex = ProcessPoolExecutor(max_workers=1)
                except Exception:
                    log.exception("계산 프로세스를 만들지 못해 스레드로 대신합니다")
            if ex is None:
                ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"Sim-{lane}")
            self._ex[lane] = ex
        return ex

    def submit(self, channel: str, key: Any, fn: Callable, *args):
        """channel 에서 이미 계산 중이면 이 요청을 '다음 차례'로 덮어쓴다 (가장 최근 것만 남김)."""
        if self._closed:
            return
        with self._lock:
            if self._closed:
                return
            if self._busy.get(channel):
                self._pending[channel] = (key, fn, args)
                return
            self._busy[channel] = True
        self._start(channel, key, fn, args)

    def _start(self, channel: str, key: Any, fn: Callable, args: tuple):
        if self._closed:
            return
        try:
            fut = self._executor(channel).submit(fn, *args)
        except Exception:
            log.exception("계산 요청 실패 (%s)", channel)
            self._complete(channel, key, None)
            return
        fut.add_done_callback(lambda f, c=channel, k=key: self._finished(c, k, f, fn, args))

    def _finished(self, channel: str, key: Any, fut: Future, fn: Optional[Callable] = None, args: tuple = ()):
        if self._closed:
            return
        if fut.cancelled():
            self._complete(channel, key, None)
            return
        try:
            result = fut.result()
        except BrokenProcessPool:
            # 계산 프로세스가 죽었거나 시작하지 못함 → 이 줄은 스레드로 바꿔 같은 요청을 다시 한다
            lane = LANES.get(channel, "heavy")
            log.warning("계산 프로세스를 쓸 수 없어 스레드로 바꿉니다 (%s)", lane)
            old = self._ex.get(lane)
            if old is not None:
                old.shutdown(wait=False, cancel_futures=True)
            self._ex[lane] = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"Sim-{lane}")
            if fn is not None:
                self._start(channel, key, fn, args)
                return
            result = None
        except Exception:
            log.exception("계산 실패 (%s)", channel)
            result = None
        self._complete(channel, key, result)

    def _complete(self, channel: str, key: Any, result):
        """제출 실패도 완료로 알린다. 화면의 '계산 중' 상태와 다음 요청을 함께 해제한다."""
        if self._closed:
            return
        self.done.emit(channel, key, result)     # 다른 스레드에서 보내도 Qt 가 화면 스레드로 넘긴다
        with self._lock:
            nxt = self._pending.pop(channel, None)
            if nxt is None:
                self._busy[channel] = False
        if nxt is not None:
            self._start(channel, *nxt)

    def busy(self, channel: str) -> bool:
        return bool(self._busy.get(channel))

    def shutdown(self):
        self._closed = True
        for ex in self._ex.values():
            ex.shutdown(wait=False, cancel_futures=True)
        self._ex.clear()
