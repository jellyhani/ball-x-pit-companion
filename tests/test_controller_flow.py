"""컨트롤러 흐름 검사: 게임 연동 스냅샷을 차례로 넣어 새로고침·삭제·선택을 게임 상태로 구분하는지 본다.

사용자 폴더에 쓰지 않도록 기록·관측·스냅샷 저장 위치를 임시 폴더로 바꾼다.
"""
import copy
import os
import shutil
import sys
import tempfile
import time
import unittest

from PySide6.QtWidgets import QApplication

from tests.test_bridge import LEVELUP

app = QApplication.instance() or QApplication(sys.argv)


class ControllerFlowTest(unittest.TestCase):
    def setUp(self):
        from src.app_controller import AppController
        from src.tracking.draw_stats import DrawStats
        from src.tracking.run_history import RunRecorder
        from src.tracking.snapshot_log import SnapshotLog
        self.tmp = tempfile.mkdtemp()
        self.c = AppController(app)
        self.c.dump_dir = self.tmp                     # 사용자 폴더의 live_state.json 을 덮어쓰지 않게
        self.c.snapshots = SnapshotLog(os.path.join(self.tmp, "snap"))
        self.c.draws = DrawStats(os.path.join(self.tmp, "draws.jsonl"))
        self.c.recorder = RunRecorder(os.path.join(self.tmp, "runs.jsonl"))
        self.c.bridge.connected = True
        self.c.bridge.last_at = time.monotonic()

    def tearDown(self):
        self.c.hud.close()
        self.c.highlight.close()
        self.c.control.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def feed(self, snap):
        self.c.bridge.last_at = time.monotonic()
        self.c._on_bridge_snapshot(copy.deepcopy(snap), time.monotonic())

    def test_reroll_is_not_an_unknown_pick(self):
        s1 = copy.deepcopy(LEVELUP)
        s1["battle"]["rerolls"] = 0
        self.feed(s1)
        self.assertIsNotNone(self.c.tracker.session)
        s2 = copy.deepcopy(s1)
        s2["battle"]["rerolls"] = 1          # 골드는 그대로 (실제 로그에서 '확인 못 함'이 된 경우)
        s2["levelup"]["choices"][1]["type"] = "kStone"          # 새로고침으로 카드가 바뀜
        s2["levelup"]["choices"][2]["type"] = "kWind"
        self.feed(s2)
        self.assertIn("새로고침", self.c.run.history)
        self.assertFalse(any("확인 못 함" in h for h in self.c.run.history))

    def test_banish_detected(self):
        s1 = copy.deepcopy(LEVELUP)
        self.feed(s1)
        s2 = copy.deepcopy(s1)
        s2["battle"]["banishes"] = 1
        s2["battle"]["banished"] = ["kLaserHorz"]
        s2["levelup"]["choices"][1]["type"] = "kStone"
        self.feed(s2)
        self.assertIn("삭제 사용", self.c.run.history)

    def test_pick_confirmed_from_inventory(self):
        s1 = copy.deepcopy(LEVELUP)
        self.feed(s1)
        after = copy.deepcopy(s1)
        after["game_state"] = "kPlaying"
        after["levelup"] = None
        after["battle"]["balls"][0]["lvl"] = 1               # 냉동 강화 (레벨 1 → 2)
        self.feed(after)
        self.feed(after)
        self.assertTrue(any("선택: 냉동" in h for h in self.c.run.history), self.c.run.history)


class BaseAimFlowTest(unittest.TestCase):
    """기지 채집 조준: 계산은 별도 프로세스, 결과가 오면 안내를 다시 그린다. 미완성 건물이 먼저."""

    def test_unfinished_first_and_path_opening(self):
        import json
        from src.app_controller import AppController
        from src.services import game_window as gw
        fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json"),
                            encoding="utf-8"))
        c = AppController(app)
        c.dump_dir = tempfile.mkdtemp()
        c.harvest_log.path = os.path.join(c.dump_dir, "harvest.jsonl")
        c.bridge.connected = True
        c.bridge.last_at = time.monotonic()

        class Win:
            rect = (0, 0, 1920, 1080)
            size = (1920, 1080)
            foreground, minimized, origin = True, False, (0, 0)
        c.window = Win()
        c._game_active = lambda: True
        blds = fx["buildings_before"]
        states = {11: "kUpgrading", 12: "kUpgrading"}
        meta = {"resources": [500, 30, 40, 10], "buildings": [
            {"type": b["type"], "lvl": 0, "state": states.get(b["id"], "kNormal")} for b in blds],
            "chars": [{"type": w["char"], "harvest": w["upgrades"]} for w in fx["workers"]]}
        c._on_bridge_snapshot({"meta": meta}, time.monotonic())
        base = {"state": "kAimWorkers", "harvest_secs_left": fx["duration"], "buildings": blds, "geo": fx["geo"],
                "player": [960, 900, 0.67, 0.74]}
        try:
            c.bridge.last_at = time.monotonic()
            c._on_bridge_snapshot({"base": base, "game_state": "kBase"}, time.monotonic())
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not (
                    c.layout_plan is not None and c._sim_res.get("sweep") and c._sim_res.get("now")):
                app.processEvents()
                time.sleep(0.02)
            lines = [t for t, _ in c.base_overlay._lines]
            self.assertTrue(lines[0].startswith("미완성 먼저:"), lines)
            self.assertTrue(any("어떤 각도로도 닿지 않음" in t and ("빈 자리로 옮기기" in t or "최적 배치대로 옮기면 닿음" in t)
                                for t in lines), lines)
            self.assertTrue(any(t.startswith("1위 ") or t.startswith("각도 차이 거의 없음") for t in lines), lines)
            self.assertTrue(c.base_overlay._path_now and c.base_overlay._path_best)
            self.assertTrue(all(v > 0 for v in c.layout_plan.reach_after.values()), c.layout_plan.reach_after)
        finally:
            c.shutdown()


if __name__ == "__main__":
    unittest.main()
