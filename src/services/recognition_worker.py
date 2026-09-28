"""캡처·OCR·화면 해석을 GUI 밖의 전용 스레드에서 실행한다.

- 한 번에 작업 하나만 처리한다. 바쁠 때 들어온 요청은 쌓지 않고 버린다(컨트롤러가 다음 틱에 다시 요청).
- 모든 결과에는 요청 번호와 세대 번호가 붙는다. 새로고침·런 전환으로 세대가 바뀌면 늦게 도착한
  이전 결과는 컨트롤러가 버린다.
- 화면이 계속 움직이는 전투 중에는 OCR 간격을 늘리고, 화면이 멈추면(선택창이 뜨면 게임이 멈춘다) 바로 읽는다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from PIL import Image, ImageChops, ImageStat
from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..domain import FrameInfo, ScreenKind, ScreenObservation
from ..recognition.screen_parser import parse_layout
from . import game_window as gw
from ..i18n import tr

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    job_id: int
    generation: int
    observation: ScreenObservation
    window: Optional[gw.GameWindow] = None
    ocr_ran: bool = False
    skipped: str = ""
    timings_ms: Dict[str, float] = field(default_factory=dict)
    image: Optional[Image.Image] = None  # 진단용 저장 요청 때만 채운다


class _Worker(QObject):
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self._reader = None
        self._level_up = None
        self._reader_error = ""
        self._prev_thumb: Optional[Image.Image] = None
        self._ocr_thumb: Optional[Image.Image] = None
        self._last_ocr_at = 0.0
        self._last_obs: Optional[ScreenObservation] = None
        self._frame_seq = 0
        self.moving_interval = 1.2  # 움직이는 화면에서 OCR 최소 간격(초)
        self.static_threshold = 3.0  # 축소 흑백 이미지 평균 차이

    def _ensure_reader(self) -> bool:
        if self._reader is not None:
            return True
        if self._reader_error:
            return False
        try:
            from ..recognition.level_up_reader import LevelUpReader
            from ..recognition.ocr import OcrReader

            self._reader = OcrReader()
            number_reader = OcrReader(target_height=100000)  # 작은 숫자 조각은 줄이지 않는다
            from ..gamedata import load_game_data
            from ..recognition.text_match import NameIndex

            names = NameIndex((index.id, index.name_ko) for index in load_game_data().items.values())
            self._level_up = LevelUpReader(number_reader.read, names=names)
            return True
        except Exception as error:
            self._reader_error = str(error)
            log.exception("OCR 초기화 실패")
            return False

    @Slot(int, int, bool, bool)
    def scan(self, job_id: int, generation: int, forced: bool, keep_image: bool):
        t0 = time.perf_counter()
        timings: Dict[str, float] = {}

        def done(observation: ScreenObservation, **kw):
            timings["total"] = (time.perf_counter() - t0) * 1000
            self.finished.emit(ScanResult(job_id, generation, observation, timings_ms=timings, **kw))

        try:
            win = gw.find_game_window()
            timings["find"] = (time.perf_counter() - t0) * 1000
            if win is None:
                return done(ScreenObservation(ScreenKind.GAME_NOT_FOUND))
            if not forced and (win.minimized or not win.foreground):
                return done(
                    ScreenObservation(ScreenKind.OTHER), window=win, skipped=tr("게임이 앞에 있지 않음")
                )
            t1 = time.perf_counter()
            img, backend = gw.capture_game(win)
            timings["capture"] = (time.perf_counter() - t1) * 1000
            if img is None:
                return done(ScreenObservation(ScreenKind.CAPTURE_FAILED, error=backend), window=win)
            now = time.monotonic()
            self._frame_seq += 1
            frame = FrameInfo(self._frame_seq, now, win.origin, img.size, backend)

            thumb = img.resize((64, 36), Image.Resampling.BILINEAR).convert("L")
            moving = self._diff(thumb, self._prev_thumb) > self.static_threshold
            same_as_last_ocr = self._diff(thumb, self._ocr_thumb) < 1.0
            self._prev_thumb = thumb
            if not forced:
                if same_as_last_ocr and self._last_obs is not None:
                    return done(self._reuse(self._last_obs, frame), window=win, skipped="이전 화면과 같음")
                if moving and now - self._last_ocr_at < self.moving_interval:
                    return done(
                        ScreenObservation(ScreenKind.OTHER, frame=frame), window=win, skipped="화면 움직임"
                    )

            if not self._ensure_reader():
                return done(
                    ScreenObservation(
                        ScreenKind.CAPTURE_FAILED,
                        frame=frame,
                        error=tr("OCR 사용 불가: {v0}", v0=self._reader_error),
                    ),
                    window=win,
                )
            t2 = time.perf_counter()
            lines = self._reader.read(img)
            timings["ocr"] = (time.perf_counter() - t2) * 1000
            t3 = time.perf_counter()
            parsed = parse_layout(lines, frame)
            timings["parse"] = (time.perf_counter() - t3) * 1000
            if parsed.kind == ScreenKind.LEVEL_UP:
                t4 = time.perf_counter()
                observation = self._level_up.read(img, frame, parsed.layout)
                timings["icons"] = (time.perf_counter() - t4) * 1000
            else:
                observation = ScreenObservation(parsed.kind, frame=frame, error=parsed.note)
            self._ocr_thumb = thumb
            self._last_ocr_at = now
            self._last_obs = observation
            return done(observation, window=win, ocr_ran=True, image=img if keep_image else None)
        except Exception as error:
            log.exception("인식 작업 실패")
            return done(ScreenObservation(ScreenKind.CAPTURE_FAILED, error=tr("인식 오류: {e}", e=error)))

    @staticmethod
    def _diff(a: Optional[Image.Image], b: Optional[Image.Image]) -> float:
        if a is None or b is None:
            return 255.0
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]

    @staticmethod
    def _reuse(observation: ScreenObservation, frame: FrameInfo) -> ScreenObservation:
        from dataclasses import replace

        return replace(observation, frame=frame)

    @Slot()
    def shutdown(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None


class RecognitionService(QObject):
    """GUI 스레드에서 쓰는 창구. 요청은 시그널로 작업 스레드에 전달된다."""

    result = Signal(object)
    _request = Signal(int, int, bool, bool)
    _shutdown = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job_seq = 0
        self.busy_since: Optional[float] = None
        self.inflight_job: Optional[int] = None
        self.restarts = 0
        self._abandoned = []  # 멈춘 스레드는 강제로 없앨 수 없어 참조만 남긴다
        self._spawn()

    def _spawn(self):
        self._thread = QThread()
        self._thread.setObjectName("recognition")
        self._worker = _Worker()
        self._worker.moveToThread(self._thread)
        self._request.connect(self._worker.scan)
        self._shutdown.connect(self._worker.shutdown)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def restart_if_stuck(self, timeout_s: float = 8.0) -> bool:
        """작업이 timeout_s 넘게 끝나지 않으면 그 스레드를 버리고 새 작업 스레드를 만든다.

        캡처나 OCR 호출이 멈춰도 감시 전체가 멈추지 않게 한다. 늦게 끝난 결과는 요청 번호가 달라 무시된다.
        """
        if self.busy_for() < timeout_s:
            return False
        log.warning(
            "인식 작업 %s이(가) %.1f초 동안 응답 없음 → 작업 스레드 교체", self.inflight_job, self.busy_for()
        )
        for sig, slot in ((self._request, self._worker.scan), (self._shutdown, self._worker.shutdown)):
            try:
                sig.disconnect(slot)
            except TypeError:
                pass
        try:
            self._worker.finished.disconnect(self._on_finished)
        except TypeError:
            pass
        self._thread.quit()
        self._abandoned.append((self._thread, self._worker))
        self.inflight_job = None
        self.busy_since = None
        self.restarts += 1
        self._spawn()
        return True

    def request(self, generation: int, forced: bool = False, keep_image: bool = False) -> Optional[int]:
        if self.inflight_job is not None:
            return None
        self._job_seq += 1
        self.inflight_job = self._job_seq
        self.busy_since = time.monotonic()
        self._request.emit(self._job_seq, generation, forced, keep_image)
        return self._job_seq

    def _on_finished(self, res: ScanResult):
        if res.job_id != self.inflight_job:
            return  # 스레드 교체 전에 큐에 들어온 이전 결과도 버린다.
        self.inflight_job = None
        self.busy_since = None
        self.result.emit(res)

    def busy_for(self) -> float:
        return 0.0 if self.busy_since is None else time.monotonic() - self.busy_since

    def stop(self):
        self._shutdown.emit()
        self._thread.quit()
        if not self._thread.wait(3000):
            log.warning("인식 스레드가 3초 안에 끝나지 않았습니다")
