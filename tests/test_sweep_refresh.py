"""실시간 타이머가 계속 변해도 탐색 결과가 표시되고, 배치·재고 변화는 즉시 무효화되는지 검사한다."""

import copy
import unittest
from unittest.mock import patch

from src.app_controller import AppController
from src.engine.sim_signature import aim_signature, sweep_signature
from tests.test_runtime_audit_fixes import aiming


class SweepRefreshTest(unittest.TestCase):
    def context(self):
        context, base = aiming()
        base["buildings"][0].update(task_seconds=0, task_target_seconds=60)
        context._base_snap = None
        context._sim_retry_at = {}
        context.sim.busy = lambda _channel: False
        return context, base

    def result(self):
        return {"top": [{"angle": 90., "total": [0, 5, 3, 1], "score": 5., "build_hits": 0,
                         "path": [(0., 0.), (2., 3.)]}], "reach": {}, "model_limitations": []}

    def requests(self, context):
        return [call for call in context.sim.submit.call_args_list if call.args[0] == "sweep"]

    def test_timer_changes_during_long_sweep_do_not_discard_finished_result(self):
        context, base = self.context()
        with patch("src.app_controller.time.monotonic", return_value=100.) as clock:
            AppController._harvest_sim(context, base, 1, [])
            key = context._sim_req["sweep"]
            context.sim.busy = lambda _channel: True
            for elapsed in (1, 3, 5, 7):
                clock.return_value = 100. + elapsed
                base["buildings"][0]["task_seconds"] += 10
                AppController._harvest_sim(context, base, 1, [])
            self.assertEqual(len(self.requests(context)), 1)
            self.assertEqual(context._sim_req["sweep"], key)
            clock.return_value = 108.8
            context.sim.busy = lambda _channel: False
            AppController._on_sim_done(context, "sweep", key, self.result())
            lines = AppController._harvest_sim(context, base, 1, [])
            self.assertTrue(any(text.startswith("1위 ") for text, _ in lines))
            self.assertFalse(any(text == "추천 각도 계산 중…" for text, _ in lines))
            self.assertTrue(context.base_overlay.set_paths.call_args.args[1])

    def test_periodic_refresh_uses_new_clock_and_keeps_compatible_recommendation(self):
        context, base = self.context()
        with patch("src.app_controller.time.monotonic", return_value=100.) as clock:
            AppController._harvest_sim(context, base, 1, [])
            key = context._sim_req["sweep"]
            clock.return_value = 109.
            AppController._on_sim_done(context, "sweep", key, self.result())
            clock.return_value = 115.
            base["buildings"][0]["task_seconds"] = 37.25
            AppController._harvest_sim(context, base, 1, [])
            self.assertEqual(len(self.requests(context)), 2)
            self.assertEqual(self.requests(context)[-1].args[4][1]["task_seconds"], 37.25)
            context.sim.busy = lambda _channel: True
            for tick in range(10):
                clock.return_value += .2
                lines = AppController._harvest_sim(context, base, 1, [])
            self.assertEqual(len(self.requests(context)), 2)
            self.assertTrue(any(text.startswith("1위 ") for text, _ in lines))
            self.assertTrue(any("직전 계산 결과" in text for text, _ in lines))

    def test_stock_or_geometry_change_clears_old_recommendation_and_rejects_old_completion(self):
        for change in (lambda base: base["buildings"][0].update(res=5),
                       lambda base: base["geo"].update(launcher=[1, 0]),
                       lambda base: base["buildings"][0].update(state="kUpgrading")):
            context, base = self.context()
            AppController._harvest_sim(context, base, 1, [])
            old_key = context._sim_req["sweep"]
            AppController._on_sim_done(context, "sweep", old_key, self.result())
            context.sim.busy = lambda _channel: True
            change(base)
            lines = AppController._harvest_sim(context, base, 1, [])
            self.assertNotEqual(context._sim_req["sweep"], old_key)
            self.assertFalse(context.base_overlay.set_paths.call_args.args[1])
            self.assertFalse(any(text.startswith("1위 ") for text, _ in lines))
            AppController._on_sim_done(context, "sweep", old_key, {"top": []})
            self.assertEqual(context._sim_res["sweep"][1], self.result())

    def test_sweep_excludes_only_clock_progress_and_fast_aim_keeps_it(self):
        _, base = self.context()
        args = ([{"speed": 5}], 38., 2, {1: 8}, (20., 160.))
        before = sweep_signature(base, *args)
        fast_before = aim_signature(base, *args)
        base["buildings"][0]["task_seconds"] = 20
        self.assertEqual(sweep_signature(base, *args), before)
        self.assertNotEqual(aim_signature(base, *args), fast_before)
        for changes in ({"res": 1}, {"task_active": True}, {"task_target_seconds": 10},
                        {"pickup_enabled": True}, {"raycast_enabled": False}, {"x": 3}):
            changed = copy.deepcopy(base)
            changed["buildings"][0].update(changes)
            self.assertNotEqual(sweep_signature(changed, *args), before)
        self.assertNotEqual(sweep_signature(base, [{"speed": 6}], *args[1:]), before)
