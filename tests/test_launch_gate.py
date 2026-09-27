"""발사 금지·판정 누락·허용 복귀 시 경로/대체선/늦은 결과가 섞이지 않는다."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from src.app_controller import AppController
from tests.test_runtime_audit_fixes import aiming


class LaunchGateTest(unittest.TestCase):
    def test_blocked_clears_both_paths_and_discards_late_worker_results(self):
        ctx,base=aiming()
        AppController._harvest_sim(ctx,base,1,[])
        old=dict(ctx._sim_req)
        ctx._sim_res={k:(v,{'path':[(1,1),(2,2)]}) for k,v in old.items()}
        base['launch_allowed']=False
        ctx.sim.submit.reset_mock()
        lines=AppController._harvest_sim(ctx,base,1,[])
        self.assertIn('발사 불가',lines[0][0])
        ctx.base_overlay.set_paths.assert_called_with([],[])
        self.assertEqual(ctx._sim_req,{})
        self.assertEqual(ctx._sim_res,{})
        ctx.sim.submit.assert_not_called()
        for channel,key in old.items():
            AppController._on_sim_done(ctx,channel,key,{'path':[(1,1),(2,2)]})
        self.assertEqual(ctx._sim_res,{})
        base['launch_allowed']=True
        AppController._harvest_sim(ctx,base,1,[])
        self.assertEqual(ctx.sim.submit.call_count,2)

    def test_old_bridge_missing_status_is_unknown_not_permission(self):
        for value in (None,1,'true'):
            ctx,base=aiming();base['launch_allowed']=value
            lines=AppController._harvest_sim(ctx,base,1,[])
            self.assertIn('미확인',lines[0][0])
            ctx.sim.submit.assert_not_called()

    def test_stale_preview_direction_does_not_authorize_new_aim(self):
        _,base=aiming();base['launch_aim']=[0,1]
        self.assertIn('미확인',AppController._launch_block_message(base))
        base['launch_aim']=[1,1]
        self.assertIsNone(AppController._launch_block_message(base))

    def test_blocked_render_clears_fallback_recommendation_line_and_marks(self):
        ctx,base=aiming();base['launch_allowed']=False
        ctx.window=NS(rect=(0,0,1280,720),size=(1280,720))
        ctx.settings=NS(hud_auto_show=True);ctx.user_hidden=False
        ctx._game_active=lambda:True
        ctx.base_overlay=Mock();ctx.base_overlay.isVisible.return_value=False
        AppController._render_base(ctx,base,'kAimWorkers')
        ctx.base_overlay.set_paths.assert_called_with([],[])
        ctx.base_overlay.set_build_marks.assert_called_with([])
        args=ctx.base_overlay.show_advice.call_args.args
        self.assertIsNone(args[2])
        self.assertIsNone(args[3])
        self.assertIn('발사 불가',args[4][0][0])
