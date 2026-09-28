"""기록으로 단계 간 요청을 연결하고, 기록 자체가 기능이나 저장 공간을 해치지 않는지 검사한다."""

from concurrent.futures import Future
import io
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.services import diagnostics
from src.services.sim_worker import JobMeasurement, SimWorker, _measure_job


class DiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.logger = logging.Logger("diagnostics-test", logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.output))
        self.patcher = patch.object(diagnostics, "logger", self.logger)
        self.patcher.start()
        self.cache = patch.dict(diagnostics._recent, {}, clear=True)
        self.cache.start()

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.cache.stop()
        self.patcher.stop()

    def records(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_unchanged_frames_are_counted_and_changes_are_immediate(self):
        with patch.object(diagnostics.time, "monotonic", return_value=100.) as clock:
            diagnostics.emit("state", stream="game", state="playing", sequence=1)
            for sequence in range(2, 100):
                diagnostics.emit("state", stream="game", state="playing", sequence=sequence)
            diagnostics.emit("state", stream="game", state="choice", sequence=100)
            clock.return_value = 131.
            diagnostics.emit("state", stream="game", state="choice", sequence=101)
        records = self.records()
        self.assertEqual([row["sequence"] for row in records], [1, 100, 101])
        self.assertEqual(records[1]["suppressed"], 98)

    def test_raw_payload_is_not_serialized_and_sampling_cache_is_bounded(self):
        for index in range(300):
            diagnostics.emit("metadata", stream=index, raw={"private": "secret"}, items=["private"],
                             description="x" * 500, count=index)
        row = self.records()[-1]
        self.assertNotIn("raw", row)
        self.assertNotIn("items", row)
        self.assertEqual(len(row["description"]), 240)
        self.assertLessEqual(len(diagnostics._recent), 256)

    def test_log_write_failure_does_not_interrupt_processing(self):
        with patch.object(self.logger, "info", side_effect=OSError("disk full")):
            diagnostics.emit("failed_write", count=1)

    def test_rotation_keeps_bounded_files_and_configure_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            diagnostics.configure(directory)
            diagnostics.configure(directory)
            handlers = [handler for handler in self.logger.handlers
                        if isinstance(handler, diagnostics.RotatingFileHandler)]
            self.assertEqual(len(handlers), 1)
            self.assertEqual((handlers[0].maxBytes, handlers[0].backupCount), (5_000_000, 4))
            handlers[0].maxBytes = 400
            for index in range(50):
                diagnostics.emit("rotation", value=index)
            self.assertLessEqual(len(list(Path(directory).glob("diagnostics.jsonl*"))), 5)
            handlers[0].close()
            self.logger.removeHandler(handlers[0])

    def test_latest_request_replacement_and_results_have_matching_tokens(self):
        executor = Mock()
        first, last = Future(), Future()
        executor.submit.side_effect = [first, last]
        worker = SimWorker(use_process=False)
        results = []
        worker.done.connect(lambda channel, key, result: results.append((key, result)))
        with patch.object(worker, "_executor", return_value=executor):
            worker.submit("now", "private-first", lambda: None)
            worker.submit("now", "private-middle", lambda: None)
            worker.submit("now", "private-last", lambda: None)
            first.set_result(JobMeasurement({"angle": 1}, .01, 123))
            last.set_result(JobMeasurement({"angle": 3}, .02, 123))
        worker.shutdown()
        records = self.records()
        started = [row["request"] for row in records if row["event"] == "compute.started"]
        completed = [row["request"] for row in records if row["event"] == "compute.completed"]
        self.assertEqual(started, completed)
        self.assertEqual(started, [diagnostics.request_token(key) for key in ("private-first", "private-last")])
        self.assertEqual(results, [("private-first", {"angle": 1}), ("private-last", {"angle": 3})])
        self.assertNotIn("private-", self.output.getvalue())
        self.assertEqual([row["compute_ms"] for row in records if row["event"] == "compute.completed"], [10., 20.])

    def test_timed_worker_preserves_results_and_exceptions(self):
        measurement = _measure_job(sum, ([1, 2, 3],))
        self.assertEqual(measurement.result, 6)
        self.assertGreaterEqual(measurement.compute_seconds, 0)
        with self.assertRaises(ZeroDivisionError):
            _measure_job(lambda: 1 / 0, ())

    def test_rejected_controller_result_is_traceable_without_applying_it(self):
        from src.app_controller import AppController
        controller = SimpleNamespace(_sim_req={"now": "new"})
        AppController._on_sim_done(controller, "now", "old", {"angle": 1})
        row = self.records()[-1]
        self.assertEqual(row["event"], "compute.discarded")
        self.assertEqual(row["reason"], "superseded")
        self.assertEqual(row["request"], diagnostics.request_token("old"))

    def test_bridge_rejection_does_not_record_bad_input(self):
        from src.services.bridge_client import BridgeClient
        with self.assertLogs("src.services.bridge_client", "WARNING"):
            BridgeClient()._handle(b"private malformed payload")
        self.assertEqual(self.records()[-1]["reason"], "invalid_json")
        self.assertNotIn("private", self.output.getvalue())

    def test_summary_reads_rotated_logs_and_ignores_partial_last_line(self):
        from tools.summarize_diagnostics import summarize
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = dict(event="compute.completed", channel="sweep", elapsed_ms=40., compute_ms=30., time="first")
            second = dict(event="compute.discarded", reason="superseded", time="last")
            (folder / "diagnostics.jsonl.1").write_text(json.dumps(first) + "\n", encoding="utf-8")
            (folder / "diagnostics.jsonl").write_text(json.dumps(second) + '\n{"event":', encoding="utf-8")
            result = summarize(folder)
        self.assertEqual(result["channels"]["sweep"]["compute_average_ms"], 30.)
        self.assertEqual(result["channels"]["sweep"]["average_ms"], 40.)
        self.assertEqual(result["reasons"]["compute.discarded:superseded"], 1)
        self.assertEqual(result["malformed_lines"], 1)
        self.assertEqual((result["first"], result["last"]), ("first", "last"))
