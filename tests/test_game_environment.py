"""실제 정적 충돌체와 도로 경계를 계산 입력으로 보존한다."""
import copy
import unittest
from src.engine import harvest_sim as hs, native
from src.engine.aim_preview import worker_preview


class GameEnvironmentTest(unittest.TestCase):
    def implementations(self):
        return [hs.simulate_team_py]+([hs.simulate_team] if native.lib() else [])

    def geo(self,**env):
        return dict(left=0,right=10,bottom=0,top=10,launcher=[1,5],
                    environment=dict(walls=[],roads=[],road_speed_mult=1,**env))

    def test_actual_wall_outside_legacy_bounds_is_authoritative(self):
        g=self.geo();g['environment']['walls']=[{'shape':'box','paths':[[[12,0],[13,0],[13,10],[12,10]]]}]
        w=hs.world_from_geo(g)
        self.assertFalse(w.use_bounds)
        for fn in self.implementations():
            counts={}
            total,workers=fn(w,{},[hs.Worker(1,5,1,0,5,0,{'kPierceBuildings':1})],3,counts=counts)
            self.assertAlmostEqual(workers[0].path[1][0],12.)
            self.assertEqual(counts,{})
            self.assertEqual(total,[0,0,0,0])

    def test_open_edge_does_not_gain_closing_segment(self):
        g=self.geo();g['environment']['walls']=[{'shape':'edge','paths':[[[3,3],[4,4],[5,3]]]}]
        w=hs.world_from_geo(g)
        for fn in self.implementations():
            _,ws=fn(w,{},[hs.Worker(4,1,0,1,5,0)],.7)
            self.assertAlmostEqual(ws[0].path[1][1],4.)

    def test_road_changes_travel_time_without_bounce_or_intrinsic_acceleration(self):
        g=self.geo();g['environment'].update(roads=[[3,4,5,6]],road_speed_mult=2.)
        w=hs.world_from_geo(g)
        for fn in self.implementations():
            _,ws=fn(w,{},[hs.Worker(1,5,1,0,5,0)],1.)
            self.assertAlmostEqual(ws[0].x,7.,places=3)
            self.assertEqual(ws[0].speed,5.)
            self.assertEqual(ws[0].dx,1.)
            self.assertEqual(len(ws[0].path),4)
            self.assertEqual(len(worker_preview(ws)),2)

    def test_missing_environment_keeps_explicit_legacy_fallback(self):
        g=self.geo();g.pop('environment')
        self.assertTrue(hs.world_from_geo(g).use_bounds)

    def test_unknown_real_wall_is_not_silently_dropped(self):
        g=self.geo();g['environment']['walls']=[{'shape':'poly','paths':[],'unsupported':True}]
        self.assertIsNone(hs.world_from_geo(g))
