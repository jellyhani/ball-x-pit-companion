"""각도 병렬화의 순위·수치·경로 일치와 취소/실패 수렴을 확인한다."""

from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
import unittest

from src.engine import harvest_sim as hs, sim_jobs
from src.services import parallel_sweep
from tests.test_sim_jobs import load, team_of


class ControlledExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, arguments, angles):
        future = Future()
        self.calls.append((list(angles), future))
        return future


class ParallelSweepTest(unittest.TestCase):
    def test_real_processes_match_serial_including_paths(self):
        fixture = load()
        buildings = {row["id"]: dict(row) for row in fixture["buildings_before"]}
        next(row for row in buildings.values() if hs.resource_tile(row))["task_target_seconds"] = 30
        args = (fixture["geo"], buildings, team_of(fixture), 8., 1, {11: 5}, 30., 60.)
        serial = sim_jobs.job_sweep(*args)
        with ProcessPoolExecutor(max_workers=2) as executor:
            result = parallel_sweep.submit(executor, args).result(timeout=30)
        self.assertEqual(result.result, serial)
        self.assertGreaterEqual(result.worker_count, 1)
        self.assertLessEqual(result.worker_count, 2)

    def test_reverse_completion_keeps_tie_order(self):
        executor = ControlledExecutor()
        buildings = {1: {"type": "kForest", "task_target_seconds": 30}}
        args = ({}, buildings, [], 1., 1, {}, 30., 48.)
        future = parallel_sweep.submit(executor, args)

        def rows(angles):
            return [hs.AngleResult(angle, [0, 1, 0, 0], preview=[(0, 0), (angle, 1)]) for angle in angles]

        coarse = [angle for angles, _ in executor.calls for angle in angles]
        for angles, child in list(reversed(executor.calls)):
            child.set_result((rows(angles), .01, 1))
        fine = [angle for angles, child in executor.calls if not child.done() for angle in angles]
        for angles, child in list(reversed(executor.calls)):
            if not child.done():
                child.set_result((rows(angles), .01, 2))
        self.assertTrue(fine)
        expected = sim_jobs.finish_sweep(rows(coarse) + rows(fine), buildings, [], 1, {})
        self.assertEqual(future.result().result, expected)

    def test_empty_angle_range_still_validates_missing_geometry(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = parallel_sweep.submit(executor, ({}, {}, [], 1., 1, {}, 60., 30.)).result(timeout=5)
        self.assertEqual(result.result, sim_jobs.job_sweep({}, {}, [], 1., 1, {}, 60., 30.))

    def test_cancellation_does_not_start_refinement(self):
        executor = ControlledExecutor()
        future = parallel_sweep.submit(executor, ({}, {}, [], 1., 1, {}, 30., 48.))
        initial = len(executor.calls)
        self.assertTrue(future.cancel())
        self.assertTrue(all(child.cancelled() for _, child in executor.calls))
        self.assertEqual(len(executor.calls), initial)

    def test_worker_error_is_reported_and_pending_batches_are_cancelled(self):
        executor = ControlledExecutor()
        future = parallel_sweep.submit(executor, ({}, {}, [], 1., 1, {}, 30., 48.))
        executor.calls[0][1].set_exception(ValueError("합성 계산 오류"))
        with self.assertRaisesRegex(ValueError, "합성 계산 오류"):
            future.result()
        self.assertTrue(executor.calls[1][1].cancelled())

    def test_preview_is_the_same_as_rerunning_selected_angle(self):
        fixture = load()
        buildings = {row["id"]: row for row in fixture["buildings_before"]}
        team = team_of(fixture)
        result = sim_jobs.job_sweep(fixture["geo"], buildings, team, 8., 1, {11: 5}, 30., 60.)
        for row in result["top"]:
            again = sim_jobs.job_now(fixture["geo"], buildings, team, row["angle"], 8., {11: 5})
            self.assertEqual(row["path"], again["path"])
            self.assertEqual(row["total"], again["total"])
