"""게임 조작 없이 최신 입력·실패 복구·사용자 자료 격리를 확인하는 통합 회귀."""
import copy
import json
import math
import os
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, mock_open, patch

import numpy as np
from PySide6.QtCore import QPoint

from src.app_controller import AppController
from src.domain import ScreenKind, ScreenObservation
from src.engine.layout import LayoutPlan, NewSpot
from src.services import game_window as gw
from src.services.recognition_worker import RecognitionService, ScanResult
from src.services.settings import APP_DIR, Settings
from src.services.sim_worker import SimWorker


def aiming():
    ctx = NS(meta=NS(chars_raw=[{"type": "kDefault", "state": "kIdle", "harvest": {}}]),
             _homography=lambda _: np.eye(3), _harvest_dur=9,
             aim_range=NS(limits=(25, 155), learned=(True, True)), _sim_req={}, _sim_res={},
             sim=NS(submit=Mock()), base_overlay=NS(set_paths=Mock()), _yield_text=AppController._yield_text)
    base = {"launch_allowed": True, "geo": {"colliders": [{"id":1,"shape":"box","pts":[[1.5,1.5],[2.5,1.5],[2.5,2.5],[1.5,2.5]]}],
            "left":0,"right":10,"bottom":0,"top":10,"launcher": [0, 0], "worker_speed": 5},
            "buildings": [{"id": 1, "type": "kHome", "x": 2, "y": 2, "rot": 0, "res": 0}],
            "player": [0, 0, 1, 1]}
    return ctx, base


class RuntimeAuditFixTest(unittest.TestCase):
    def test_worker_change_recomputes_with_same_head_count(self):
        ctx, base = aiming()
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 2)
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 2)
        ctx.meta.chars_raw[0]["harvest"] = {"kPierceWood": 1}
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 4)
        base["geo"]["worker_speed"] = 8
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 6)

    def test_rotated_shape_recomputes(self):
        ctx, base = aiming()
        AppController._harvest_sim(ctx, base, 1, [])
        base["buildings"][0]["rot"] = 1
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 4)

    def test_idle_production_progress_does_not_invalidate_physics(self):
        ctx, base = aiming()
        base["buildings"][0]["task"] = .1
        AppController._harvest_sim(ctx, base, 1, [])
        base["buildings"][0]["task"] = .2
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 2)

    def test_previous_angle_and_translated_path_are_not_shown_as_current(self):
        ctx, base = aiming()
        AppController._harvest_sim(ctx, base, 1, [])
        key = ctx._sim_req["now"]
        ctx._sim_res["now"] = (key, {"angle": 45, "path": [(0, 0), (1, 1)], "total": [0, 1, 0, 0]})
        base["player"] = [0, 0, 0, 1]
        lines = AppController._harvest_sim(ctx, base, 1, [])
        self.assertFalse(any("지금 조준 45" in text for text, _ in lines))
        self.assertEqual(ctx.base_overlay.set_paths.call_args.args[0], [])
        base["geo"]["launcher"] = [1, 0]
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.base_overlay.set_paths.call_args.args[0], [])

    def test_fractional_aim_is_not_rounded_before_simulation(self):
        ctx, base = aiming()
        for degrees in (44.6, 45.4):
            rad = math.radians(degrees)
            base["player"] = [0, 0, math.cos(rad), math.sin(rad)]
            AppController._harvest_sim(ctx, base, 1, [])
        now_calls = [call for call in ctx.sim.submit.call_args_list if call.args[0] == "now"]
        self.assertEqual(len(now_calls), 2)
        self.assertAlmostEqual(now_calls[-1].args[6], 45.4)

    def test_failed_sweep_retries_after_cooldown_without_recursion(self):
        ctx, base = aiming()
        ctx._sim_retry_at = {}
        AppController._harvest_sim(ctx, base, 1, [])
        key = ctx._sim_req["sweep"]
        AppController._on_sim_done(ctx, "sweep", key, None)
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 2)
        ctx._sim_retry_at["sweep"] = 0
        AppController._harvest_sim(ctx, base, 1, [])
        self.assertEqual(ctx.sim.submit.call_count, 3)

    def test_layout_result_for_previous_inputs_is_discarded(self):
        ctx = NS(_sim_req={"layout": "old"}, _sim_res={}, _layout_busy=True,
                 _layout_request=("old", {"buildings": []}), _base_snap={"buildings": []}, meta=object(),
                 _base_state="kNormal", _layout_fingerprint=lambda _: "new", _on_layout_done=Mock())
        AppController._on_sim_done(ctx, "layout", "old", (LayoutPlan(1, 2), {}))
        self.assertFalse(ctx._layout_busy)
        ctx._on_layout_done.assert_not_called()
        self.assertIsNone(ctx._layout_for)

    def test_unchanged_layout_result_is_applied_during_rearrangement(self):
        ctx = NS(_sim_req={"layout": "same"}, _sim_res={}, _layout_busy=True,
                 _layout_request=("same", {"buildings": []}), _base_snap={"buildings": []}, meta=object(),
                 _base_state="kRearrangeBuildings", _layout_fingerprint=lambda _: "same", _on_layout_done=Mock())
        AppController._on_sim_done(ctx, "layout", "same", (LayoutPlan(1, 2), {}))
        ctx._on_layout_done.assert_called_once()

    def test_moved_building_still_invalidates_result_during_rearrangement(self):
        ctx = NS(_sim_req={"layout": "old"}, _sim_res={}, _layout_busy=True,
                 _layout_request=("old", {"buildings": []}), _base_snap={"buildings": []}, meta=object(),
                 _base_state="kRearrangeBuildings", _layout_fingerprint=lambda _: "moved", _on_layout_done=Mock())
        AppController._on_sim_done(ctx, "layout", "old", (LayoutPlan(1, 2), {}))
        ctx._on_layout_done.assert_not_called()

    def test_submission_failure_signals_completion(self):
        worker = SimWorker(use_process=False)
        events = []
        worker.done.connect(lambda *event: events.append(event))
        executor = Mock()
        executor.submit.side_effect = RuntimeError("합성 제출 실패")
        with patch.object(worker, "_executor", return_value=executor), self.assertLogs("src.services.sim_worker", "ERROR"):
            worker.submit("layout", "request", lambda: None)
        self.assertEqual(events, [("layout", "request", None)])
        self.assertFalse(worker.busy("layout"))
        worker.shutdown()

    def test_obsolete_ocr_request_is_not_forwarded(self):
        ctx = NS(inflight_job=9, busy_since=1.0, result=NS(emit=Mock()))
        RecognitionService._on_finished(ctx, ScanResult(8, 0, ScreenObservation(ScreenKind.OTHER)))
        ctx.result.emit.assert_not_called()
        self.assertEqual(ctx.inflight_job, 9)

    def test_live_game_state_wins_over_late_ocr(self):
        ctx = NS(_log_scan_change=Mock(), pending_save=False, bridge=NS(live=True), _handle=Mock())
        AppController._on_scan(ctx, ScanResult(1, 0, ScreenObservation(ScreenKind.LEVEL_UP)))
        ctx._handle.assert_not_called()

    def test_new_upgrade_level_is_not_mistaken_for_closing_animation(self):
        from src.domain import CardLabel
        from tests.helpers import card, session
        previous = session([card(0, "ball:burn", CardLabel.UPGRADE, 2)])
        current = session([card(0, "ball:burn", CardLabel.UPGRADE, 3)])
        ctx = NS(_picked_sig=(previous.signature, time.monotonic()), bridge=NS(live=True))
        self.assertTrue(AppController._is_after_pick_echo(ctx, previous))
        self.assertFalse(AppController._is_after_pick_echo(ctx, current))

    def test_hud_drag_uses_the_position_it_was_actually_drawn_from(self):
        ctx = NS(_hud_auto_spot=QPoint(20, 115), settings=NS(save=Mock()))
        AppController._on_hud_moved(ctx, QPoint(40, 135))
        self.assertEqual((ctx.settings.hud_offset_x, ctx.settings.hud_offset_y), (20, 20))

    def test_hidden_game_never_falls_back_to_other_apps_screen_pixels(self):
        win = gw.GameWindow(1, 1, (0, 0), (800, 600), 96, False, False)
        with patch.object(gw, "capture_print_window", return_value=None), \
                patch.object(gw, "capture_screen_region") as screen:
            image, _ = gw.capture_game(win)
        self.assertIsNone(image)
        screen.assert_not_called()

    def test_invalid_setting_types_recover_without_startup_failure(self):
        for raw in ([], {"font_scale": "large"}, {"scan_interval_ms": None}):
            with self.subTest(raw=raw), patch("builtins.open", mock_open(read_data=json.dumps(raw))):
                loaded = Settings.load()
                self.assertEqual(loaded.font_scale, 1.0)
                self.assertEqual(loaded.scan_interval_ms, 700)

    def test_affordability_is_checked_again_before_display(self):
        row = ("kDenseWheat", (1, 1), (1, 1), 1, 2, 0)
        plan = LayoutPlan(1, 1, builds=[row], build_costs={"kDenseWheat": (100, 1, 0, 0)},
                          new_spots=[NewSpot("kDenseWheat", (1, 1), (1, 1), 2, "")])
        AppController._validate_purchase_budget(NS(meta=NS(resources=(150, 10, 10, 10))), plan)
        self.assertEqual((plan.builds, plan.new_spots), ([], []))

    def test_test_package_isolates_data_before_controller_construction(self):
        self.assertEqual(APP_DIR, os.environ["BXP_APP_DIR"])
        user_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "BallxPitCompanion")
        self.assertNotEqual(os.path.normcase(APP_DIR), os.path.normcase(user_dir))
