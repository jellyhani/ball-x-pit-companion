"""긴 조준 경로: 실제 반사 기록, 표시 전환과 계산 캐시, 입력·잔상 경계 확인."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import mock_open, patch

from PySide6.QtWidgets import QApplication

from src.app_controller import AppController
from src.engine import harvest_sim as hs, sim_jobs
from src.engine.aim_preview import visible_path, worker_preview
from src.services.input_watch import InputWatcher
from src.services.settings import Settings
from src.ui.base_overlay import BaseOverlay
from tests.test_runtime_audit_fixes import aiming


class AimPreviewTest(unittest.TestCase):
    def test_wheat_passage_does_not_shorten_reflection_preview(self):
        geo = {"left": 0, "right": 10, "bottom": 0, "top": 10, "launcher": [1, 5],
               "colliders": [{"id": i, "shape": "box",
                              "pts": [[i, 4], [i + .4, 4], [i + .4, 6], [i, 6]]}
                             for i in range(2, 8)]}
        blds = {i: {"type": "kWheatField", "res": 1, "can_harvest": True} for i in range(2, 8)}
        team = [{"type": "kDefault", "upgrades": {}, "speed": 5}]
        result = sim_jobs.job_now(geo, blds, team, 0, 16)
        self.assertEqual(result["total"][1], 6)
        self.assertGreater(len(result["path"]), 4)
        self.assertAlmostEqual(result["path"][1][0], 10.0)
        self.assertAlmostEqual(result["path"][2][0], 0.0)
        # 짧게 표시해도 밀밭 여섯 개를 지나 첫 벽 반사 이후까지 보인다.
        self.assertEqual(visible_path(result["path"], "short"), result["path"][:3])

    def test_terminal_point_is_never_extrapolated(self):
        path = [(0, 0, 0), (2, 2, .5)]
        self.assertEqual(worker_preview([NS(path=path)]), [(0, 0), (2, 2)])
        self.assertEqual(visible_path([(0, 0), (2, 2)], extended=True), [(0, 0), (2, 2)])
        self.assertEqual(worker_preview([]), [])

    def test_display_switch_reuses_physics_results_and_restores_length(self):
        ctx, base = aiming()
        ctx.settings = Settings()
        AppController._harvest_sim(ctx, base, 1, [])
        path = [(i, i % 2) for i in range(9)]
        result = {"angle": 45, "path": path, "total": [0, 1, 0, 0], "score": 1}
        ctx._sim_res["now"] = (ctx._sim_req["now"], result)
        ctx._sim_res["sweep"] = (ctx._sim_req["sweep"], {"top": [result]})
        for length, held, expected in (("normal", False, (5, 3)), ("long", False, (9, 3)),
                                        ("short", True, (9, 9)), ("short", False, (3, 3))):
            ctx.settings.aim_path_length, ctx._aim_extended = length, held
            AppController._harvest_sim(ctx, base, 1, [])
            self.assertEqual(tuple(map(len, ctx.base_overlay.set_paths.call_args.args)), expected)
        self.assertEqual(ctx.sim.submit.call_count, 2)
        self.assertEqual(len(path), 9)

    def test_missing_geometry_clears_old_preview(self):
        ctx, base = aiming()
        base["geo"].pop("colliders")
        self.assertEqual(AppController._harvest_sim(ctx, base, 1, []), [])
        ctx.base_overlay.set_paths.assert_called_with([], [])

    def test_empty_building_list_is_valid_geometry_and_errors_do_not_spin_as_calculating(self):
        ctx,base=aiming();base['geo']['colliders']=[]
        AppController._harvest_sim(ctx,base,1,[])
        self.assertEqual(ctx.sim.submit.call_count,2)
        ctx._sim_res['sweep']=(ctx._sim_req['sweep'],{'error':'geometry_unavailable'})
        lines=AppController._harvest_sim(ctx,base,1,[])
        self.assertIn('물리 정보가 불완전',lines[0][0])
        ctx.base_overlay.set_paths.assert_called_with([],[])

    def test_shift_repeat_and_two_keys_release(self):
        watcher = InputWatcher()
        seen = []
        watcher.extend_aim.connect(seen.append)
        for key, pressed in (("shift", True), ("shift", True), ("shift_r", True),
                              ("shift", False), ("shift_r", False)):
            watcher._key_event(key, pressed)
        self.assertEqual(seen, [True, False])
        watcher._key_event("shift_r", True)
        watcher.stop()
        self.assertEqual(seen, [True, False, True, False])

    def test_invalid_saved_length_falls_back(self):
        with patch("builtins.open", mock_open(read_data=json.dumps({"aim_path_length": "huge"}))):
            self.assertEqual(Settings.load().aim_path_length, "normal")

    def test_overlay_dirty_rect_covers_markers_and_removes_old_tail(self):
        app = QApplication.instance() or QApplication([])
        overlay = BaseOverlay()
        overlay._scale = .5
        with patch.object(overlay, "update") as update:
            overlay.set_paths([(10, 20), (400, 100), (700, 300)], [])
            previous = overlay._content_rect()
            self.assertTrue(previous.contains(350 + 8, 150))
            overlay.set_paths([(10, 20), (40, 40)], [])
            self.assertTrue(update.call_args.args[0].contains(previous))
            overlay.set_paths([], [])
            self.assertTrue(overlay._content_rect().isNull())
        overlay.close()


if __name__ == "__main__":
    unittest.main()
