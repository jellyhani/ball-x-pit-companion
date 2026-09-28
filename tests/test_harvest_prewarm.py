"""기지 예비 계산은 실제 조준 입력과 일치할 때만 재사용한다."""

import copy
from types import SimpleNamespace
import unittest

from src.app_controller import AppController
from src.engine.harvest import need_resource
from src.tracking.meta_state import MetaState
from tests.test_runtime_audit_fixes import aiming
from tests import test_sweep_refresh as refresh_tests


class HarvestPrewarmTest(unittest.TestCase):
    def context(self):
        context, base = aiming()
        context.settings = SimpleNamespace(hud_auto_show=True)
        context.user_hidden = False
        context.meta = MetaState(resources=(0, 20, 10, 5), chars_raw=[
            {"type": "kDefault", "state": "kIdle", "harvest": {"kHarvestSpeed": 1},
             "harvest_bonus": {"kHarvestSpeed": 0}},
        ])
        context._shortfalls = lambda: {}
        context._game_active = lambda: False
        context._base_snap = None
        context._base_state = "kNormal"
        context.sim.busy = lambda _channel: False
        base["state"] = "kNormal"
        base["geo"].update(harvest_len=8., launch_team=[])
        return context, base

    def aimed(self, base):
        result = copy.deepcopy(base)
        result.update(state="kAimWorkers", harvest_secs_left=9.)
        result["geo"]["launch_team"] = [{"type": "kDefault", "launch_index": 0,
            "upgrades": {"kHarvestSpeed": 1}, "harvest_bonus": {"kHarvestSpeed": 0}}]
        return result

    def test_background_normal_base_result_is_reused_on_aim_entry(self):
        context, base = self.context()
        AppController._precompute_harvest(context, base, "kNormal")
        key = context._sim_req["sweep"]
        AppController._on_sim_done(context, "sweep", key, refresh_tests.SweepRefreshTest().result())
        lines = AppController._harvest_sim(context, self.aimed(base), need_resource(context.meta, {})[0], [])
        self.assertEqual(context._sim_req["sweep"], key)
        self.assertEqual(len([call for call in context.sim.submit.call_args_list if call.args[0] == "sweep"]), 1)
        self.assertTrue(any(text.startswith("1위 ") for text, _ in lines))

    def test_actual_participant_bonus_change_invalidates_precomputed_result(self):
        context, base = self.context()
        AppController._precompute_harvest(context, base, "kNormal")
        key = context._sim_req["sweep"]
        AppController._on_sim_done(context, "sweep", key, refresh_tests.SweepRefreshTest().result())
        aimed = self.aimed(base)
        aimed["geo"]["launch_team"][0]["harvest_bonus"]["kHarvestSpeed"] = 5
        AppController._harvest_sim(context, aimed, need_resource(context.meta, {})[0], [])
        self.assertNotEqual(context._sim_req["sweep"], key)
        self.assertFalse(context.base_overlay.set_paths.call_args.args[1])

    def test_disabled_unavailable_or_rearranging_does_not_start_background_work(self):
        for reason in ("disabled", "unknown_launcher", "rearrange", "already_harvested"):
            context, base = self.context()
            if reason == "disabled":
                context.settings.hud_auto_show = False
            elif reason == "unknown_launcher":
                base["geo"].update(launcher_source="unavailable", launcher=None)
            elif reason == "rearrange":
                base["state"] = "kRearrangeBuildings"
            else:
                base["harvested_today"] = True
            AppController._precompute_harvest(context, base, base["state"])
            context.sim.submit.assert_not_called()
