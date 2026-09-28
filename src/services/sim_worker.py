"""계산 전용 프로세스를 두고, 채널마다 '가장 최근 요청만' 처리한다 (조준이 바뀌는 동안 요청이 쌓이지 않게).

- 지금 조준의 궤적(now)과 무거운 각도·배치 탐색(sweep·layout)을 다른 프로세스로 나눠,
  무거운 계산 중에도 조준 궤적이 바로 따라온다.
- 결과는 Qt 시그널로 화면 스레드에 넘어온다. 프로세스를 못 만들면(드문 환경) 스레드로 대신 돌린다.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, Signal
from . import diagnostics

log = logging.getLogger(__name__)

FAST_CHANNELS = ("now",)
# 채널 → 계산 프로세스 줄: 지금 조준(빠름) / 각도 탐색 / 배치 최적화(수 초) — 서로 기다리지 않게
LANES = {"now": "fast", "sweep": "heavy", "layout": "layout"}


@dataclass
class JobMeasurement:
    result: Any
    compute_seconds: float
    process_id: int


def _measure_job(function: Callable, args: tuple) -> JobMeasurement:
    """작업자는 측정값만 돌려준다. 여러 프로세스가 같은 회전 로그 파일을 쓰지 않게 한다."""
    started = time.perf_counter()
    result = function(*args)
    return JobMeasurement(result, time.perf_counter() - started, os.getpid())


class SimWorker(QObject):
    done = Signal(str, object, object)  # (채널, 요청 키, 결과 — 실패하면 None)

    def __init__(self, use_process: bool = True):
        super().__init__()
        self._use_process = use_process
        self._ex: Dict[str, Executor] = {}
        self._lock = threading.Lock()
        self._busy: Dict[str, bool] = {}
        self._pending: Dict[str, Tuple[Any, Callable, tuple, float]] = {}
        self._closed = False
        self._started_at: Dict[str, float] = {}
        self._reported_channels = set()

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
        submitted_at = time.perf_counter()
        request = diagnostics.request_token(key)
        with self._lock:
            if self._closed:
                return
            if self._busy.get(channel):
                previous = self._pending.get(channel)
                diagnostics.emit("compute.queued", channel=channel, request=request,
                                 replaces=diagnostics.request_token(previous[0]) if previous else None)
                self._pending[channel] = (key, fn, args, submitted_at)
                return
            self._busy[channel] = True
        self._start(channel, key, fn, args, submitted_at)

    def _start(self, channel: str, key: Any, fn: Callable, args: tuple, submitted_at=None):
        if self._closed:
            return
        self._started_at[channel] = time.perf_counter()
        waited = self._started_at[channel] - submitted_at if submitted_at is not None else 0.
        try:
            executor = self._executor(channel)
            diagnostics.emit("compute.started", channel=channel, request=diagnostics.request_token(key),
                             job=getattr(fn, "__name__", type(fn).__name__),
                             executor=type(executor).__name__, wait_ms=round(waited * 1000, 2))
            fut = executor.submit(_measure_job, fn, args)
        except Exception:
            log.exception("계산 요청 실패 (%s)", channel)
            diagnostics.emit("compute.failed", channel=channel, request=diagnostics.request_token(key),
                             reason="submission")
            self._complete(channel, key, None)
            return
        fut.add_done_callback(lambda f, c=channel, k=key: self._finished(c, k, f, fn, args))

    def _finished(self, channel: str, key: Any, fut: Future, fn: Optional[Callable] = None, args: tuple = ()):
        if self._closed:
            return
        if fut.cancelled():
            diagnostics.emit("compute.cancelled", channel=channel, request=diagnostics.request_token(key))
            self._complete(channel, key, None)
            return
        try:
            result = fut.result()
        except BrokenProcessPool:
            # 계산 프로세스가 죽었거나 시작하지 못함 → 이 줄은 스레드로 바꿔 같은 요청을 다시 한다
            lane = LANES.get(channel, "heavy")
            log.warning("계산 프로세스를 쓸 수 없어 스레드로 바꿉니다 (%s)", lane)
            diagnostics.emit("compute.fallback", channel=channel, request=diagnostics.request_token(key),
                             reason="broken_process_pool", executor="ThreadPoolExecutor")
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
            diagnostics.emit("compute.failed", channel=channel, request=diagnostics.request_token(key),
                             reason="execution")
            result = None
        self._complete(channel, key, result)

    def _complete(self, channel: str, key: Any, result):
        """제출 실패도 완료로 알린다. 화면의 '계산 중' 상태와 다음 요청을 함께 해제한다."""
        if self._closed:
            return
        started = self._started_at.pop(channel, None)
        measurement = result if isinstance(result, JobMeasurement) else None
        if measurement is not None:
            result = measurement.result
        if started is not None:
            elapsed = time.perf_counter() - started
            diagnostics.emit("compute.completed", channel=channel, request=diagnostics.request_token(key),
                             elapsed_ms=round(elapsed * 1000, 2),
                             compute_ms=round(measurement.compute_seconds * 1000, 2) if measurement else None,
                             worker_pid=measurement.process_id if measurement else None,
                             has_result=result is not None,
                             error=result.get("error") if isinstance(result, dict) else None)
            if channel not in self._reported_channels or elapsed >= 1.0:
                log.info("계산 완료: 채널=%s 소요=%.3f초 성공=%s", channel, elapsed, result is not None)
                self._reported_channels.add(channel)
        self.done.emit(channel, key, result)  # 다른 스레드에서 보내도 Qt 가 화면 스레드로 넘긴다
        with self._lock:
            next_value = self._pending.pop(channel, None)
            if next_value is None:
                self._busy[channel] = False
        if next_value is not None:
            self._start(channel, *next_value)

    def busy(self, channel: str) -> bool:
        return bool(self._busy.get(channel))

    def shutdown(self):
        diagnostics.emit("compute.shutdown", running=sum(bool(value) for value in self._busy.values()),
                         pending=len(self._pending))
        self._closed = True
        for ex in self._ex.values():
            ex.shutdown(wait=False, cancel_futures=True)
        self._ex.clear()
