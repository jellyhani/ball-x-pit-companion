"""각도 탐색을 기존 계산 풀에서 나눠 처리한다. 작업자 안에 자식 풀을 만들지 않는다."""

from concurrent.futures import Future, InvalidStateError
import os
import threading
import time

from ..engine import harvest_sim as simulator, sim_jobs


def worker_count():
    available = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    # 게임·현재 조준·배치 작업에 여유를 남긴다. 12 논리 코어인 개발 PC에서는 3개.
    return max(1, min(3, (available - 2) // 2))


def measured_batch(arguments, angles):
    started = time.perf_counter()
    ranked = sim_jobs.job_sweep_batch(*arguments[:6], angles)
    return ranked, time.perf_counter() - started, os.getpid()


class SweepWork:
    """거친 탐색 → 상위 후보 주변 정밀 탐색. 완료 순서가 달라도 원래 각도 순서로 합친다."""

    def __init__(self, executor, arguments):
        self.executor = executor
        arguments = tuple(arguments) + (None, 12., 168.)[max(0, len(arguments) - 5):]
        self.arguments = arguments
        self.future = Future()
        self.lock = threading.RLock()
        self.children = []
        self.coarse = []
        self.started = time.perf_counter()
        self.worker_seconds = 0.
        self.worker_ids = set()
        self.refine = sim_jobs.sweep_needs_refinement(arguments[1])
        self.future.add_done_callback(self._cancel_children)
        self._launch(sim_jobs._angle_grid(arguments[6:8], 6 if self.refine else 1), "coarse")

    def _launch(self, angles, stage):
        with self.lock:
            if self.future.done():
                return
            batches = [angles[index:index + 3] for index in range(0, len(angles), 3)]
            if not batches and stage == "coarse":
                batches = [[]]  # 각도가 없어도 작업자에서 geometry 유효성을 검사한다.
            self.stage, self.parts, self.remaining = stage, {}, len(batches)
            if not batches:
                self._stage_complete([])
                return
            for index, batch in enumerate(batches):
                if self.future.done():
                    break
                try:
                    child = self.executor.submit(measured_batch, self.arguments, batch)
                except Exception as error:
                    self._fail(error)
                    return
                self.children.append(child)
                child.add_done_callback(lambda completed, index=index: self._received(index, completed))

    def _received(self, index, completed):
        with self.lock:
            if self.future.done():
                return
            try:
                ranked, seconds, process_id = completed.result()
                self.worker_seconds += seconds
                self.worker_ids.add(process_id)
                if ranked is None:
                    self._finish({"error": "geometry_unavailable", "model_limitations": ["geometry_unavailable"]})
                    return
                self.parts[index] = ranked
                self.remaining -= 1
                if not self.remaining:
                    ordered = [row for index in sorted(self.parts) for row in self.parts[index]]
                    self._stage_complete(ordered)
            except Exception as error:
                self._fail(error)

    def _stage_complete(self, ranked):
        ranked.sort(key=lambda row: -simulator.angle_score(row, self.arguments[4]))
        if self.stage == "coarse" and self.refine:
            self.coarse = ranked
            fine = sim_jobs.fine_sweep_angles(ranked, *self.arguments[6:8])
            self._launch(fine, "fine")
        else:
            _, buildings, team, _, need, targets, *_ = self.arguments
            self._finish(sim_jobs.finish_sweep(self.coarse + ranked, buildings, team, need, targets))

    def _finish(self, result):
        from .sim_worker import JobMeasurement
        try:
            self.future.set_result(JobMeasurement(result, time.perf_counter() - self.started, None,
                                                 len(self.worker_ids), self.worker_seconds))
        except InvalidStateError:
            pass  # 종료와 마지막 작업 완료가 동시에 일어난 경우.

    def _fail(self, error):
        try:
            self.future.set_exception(error)
        except InvalidStateError:
            pass
        for child in self.children:
            child.cancel()

    def _cancel_children(self, completed):
        if completed.cancelled():
            with self.lock:
                for child in self.children:
                    child.cancel()


def submit(executor, arguments):
    return SweepWork(executor, arguments).future
