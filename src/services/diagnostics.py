"""로컬 진단 사건 기록. 원본 게임 JSON 대신 단계·개수·시간·판정 사유를 남긴다."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import threading
import time

logger = logging.getLogger("bxp.diagnostics")
logger.propagate = False
logger.addHandler(logging.NullHandler())
_lock = threading.Lock()
_recent = OrderedDict()


def configure(folder: str) -> None:
    """현재 파일과 이전 4개, 총 약 25MB로 제한한다. UI 프로세스 한 곳에서만 쓴다."""
    destination = Path(folder) / "diagnostics.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(destination, maxBytes=5_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    with _lock:
        _recent.clear()


def request_token(key) -> str:
    """계산 키의 원본은 쓰지 않는다. 요청과 완료·폐기를 같은 짧은 지문으로 연결한다."""
    return hashlib.blake2s(repr(key).encode("utf-8"), digest_size=8).hexdigest()


def emit(event: str, *, stream=None, state=None, interval: float = 30., **fields) -> None:
    """같은 stream/state는 간격마다 요약하고, 상태 변화는 즉시 쓴다.

    호출부가 고른 작은 메타데이터만 허용한다. 원본 dict·객체·배열을 실수로 넘겨도 기록하지 않는다.
    로그 실패가 게임 상태 처리나 계산 결과 전달을 막아서는 안 된다.
    """
    try:
        now = time.monotonic()
        skipped = 0
        if stream is not None:
            slot = (event, stream)
            with _lock:
                previous = _recent.get(slot)
                if previous is not None and previous[0] == state and now - previous[1] < interval:
                    _recent[slot] = (state, previous[1], previous[2] + 1)
                    _recent.move_to_end(slot)
                    return
                skipped = previous[2] if previous is not None else 0
                _recent[slot] = (state, now, 0)
                _recent.move_to_end(slot)
                while len(_recent) > 256:
                    _recent.popitem(last=False)
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "monotonic": round(now, 3),
            "pid": os.getpid(), "thread": threading.current_thread().name,
            "event": event, "suppressed": skipped,
        }
        for name, value in fields.items():
            if value is None or type(value) in (bool, int, float):
                payload[name] = value
            elif isinstance(value, str):
                payload[name] = value[:240]
        logger.info(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, TypeError):
        pass
