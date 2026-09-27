"""게임 1.301 실행 코드의 분기·수식을 독립된 장면으로 검증한다.

MoveBalls 0x4596E0, _LaunchWorkers.MoveNext 0x467060,
WorkerHitBuilding 0x455930, GetHitByWorker 0x462E50.
파이썬과 C++끼리 일치하는 것만으로 통과시키지 않는다.
"""
import copy
import math
import unittest
from src.engine import harvest_sim as hs, native, sim_jobs


class GamePhysicsContractTest(unittest.TestCase):
    def test_rotated_box_does_not_treat_its_empty_aabb_corner_as_inside(self):
        shape=hs.Shape(1,'box',pts=[(3,1),(5,3),(3,5),(1,3)])
        self.assertEqual(shape.kind,'poly')
        self.assertFalse(hs._inside_shape(shape,1.25,1.25,0))
        for fn in self.implementations():
            _,worker,_=self.run_scene(fn,shape,{'type':'kBank'},hs.Worker(1.25,1.25,1,0,1,0),1.7)
            self.assertAlmostEqual(worker.dx,0.)
            self.assertAlmostEqual(worker.dy,-1.)

    def implementations(self):
        return [hs.simulate_team_py] + ([hs.simulate_team] if native.lib() else [])

    def run_scene(self, fn, shape, building, worker, duration):
        world=hs.World(0,20,0,20,[shape],(worker.x,worker.y),radius=0)
        points={}
        total, ws=fn(world,{1:building},[copy.deepcopy(worker)],duration,build_points=points)
        return total,ws[0],points

    def box(self):
        return hs.Shape(1,'box',pts=[(3,4.5),(4,4.5),(4,5.5),(3,5.5)])

    def test_circle_uses_contact_normal(self):
        for fn in self.implementations():
            with self.subTest(fn=fn.__name__):
                _,w,_=self.run_scene(fn,hs.Shape(1,'circle',c=(5,5),r=1),
                    {'type':'kBank','state':'kNormal'},hs.Worker(1,4.5,1,0,5,0),.8)
                self.assertAlmostEqual(w.dx,-.5)
                self.assertAlmostEqual(w.dy,-math.sqrt(3)/2)

    def test_oblique_face_uses_normal_not_largest_axis(self):
        self.assertEqual(hs.reflected(1.,0.,-1.,-1.),(0.,-1.))

    def test_ray_inside_collider_does_not_hit_owner_on_exit(self):
        # globalgamemanagers Physics2DSettings.m_QueriesStartInColliders=false.
        self.assertIsNone(hs._hit_shape(self.box(),3.5,5.,1.,0.,0.))

    def test_building_pierce_does_not_include_resource_tiles(self):
        for fn in self.implementations():
            for kind in ['kBoulder','kForest']:
                with self.subTest(fn=fn.__name__,kind=kind):
                    _,w,_=self.run_scene(fn,self.box(),{'type':kind,'state':'kNormal','res':20,'can_harvest':True},
                        hs.Worker(1,5,1,0,5,0,{'kPierceBuildings':1}),.6)
                    self.assertEqual(w.dx,-1)

    def test_resource_specific_pierce_still_works(self):
        for fn in self.implementations():
            _,w,_=self.run_scene(fn,self.box(),{'type':'kBoulder','state':'kNormal','res':20,'can_harvest':True},
                hs.Worker(1,5,1,0,5,0,{'kPierceBuildings':1,'kPierceStone':1}),.6)
            self.assertEqual(w.dx,1)

    def test_stone_pile_is_not_a_resource_tile(self):
        # BuildingUtl.GetResourceType(6B5D00): StonePile(51)는 kNum(4).
        from src.engine import layout_opt,layout
        from src.engine.construction_policy import RESOURCE_TILE_TYPES
        self.assertEqual(set(layout_opt.TILE_RES),set(RESOURCE_TILE_TYPES))
        self.assertEqual(set(layout.TILE_TYPES),set(RESOURCE_TILE_TYPES))
        self.assertNotIn('kStonePile',hs.RESOURCE_TILE_TYPES)
        for fn in self.implementations():
            _,w,_=self.run_scene(fn,self.box(),{'type':'kStonePile','state':'kNormal','res':0,'can_harvest':False},
                hs.Worker(1,5,1,0,5,0,{'kPierceStone':1}),.6)
            self.assertEqual(w.dx,-1)

    def test_explicit_game_resource_classification_overrides_legacy_name(self):
        b={'type':'kForest','is_resource':False,'resource_type':4}
        self.assertFalse(hs.resource_tile(b))
        self.assertIsNone(hs.resource_kind(b))
        self.assertEqual(hs.passable_ids({1:b},{'kPierceBuildings':1}),{1})

    def test_wheat_range_does_not_enlarge_wood_piercing_contact(self):
        for fn in self.implementations():
            total,_,_=self.run_scene(fn,self.box(),{'type':'kForest','res':4,'can_harvest':True},
                hs.Worker(1,4.3,1,0,5,0,{'kPierceWood':1,'kWheatRange':2}),.6)
            self.assertEqual(total,[0,0,0,0])

    def test_initial_pickup_overlap_is_collected_once_without_reentry(self):
        for fn in self.implementations():
            total,_,_=self.run_scene(fn,self.box(),{'type':'kWheatField','res':4,'can_harvest':True},
                hs.Worker(2.9,5,1,0,5,0),.05)
            self.assertEqual(total,[0,1,0,0])

    def test_pickup_box_corners_are_round_not_square(self):
        self.assertFalse(hs._inside_shape(self.box(),2.9,4.4,.125))
        h=hs._hit_shape(self.box(),2.,4.4,1.,0.,.125)
        self.assertAlmostEqual(h[0],.925)
        for fn in self.implementations():
            total,_,_=self.run_scene(fn,self.box(),{'type':'kWheatField','res':4,'can_harvest':True},
                hs.Worker(2.9,4.4,-1,0,5,0),.05)
            self.assertEqual(total,[0,0,0,0])

    def test_pierced_construction_receives_contact_points(self):
        for fn in self.implementations():
            _,w,points=self.run_scene(fn,self.box(),{'type':'kSchoolhouse','state':'kScaffold','res':0,'can_harvest':False},
                hs.Worker(1,5,1,0,5,0,{'kPierceBuildings':1},harvest_bonus={'kMoreBuildPts':2}),.6)
            self.assertEqual(w.dx,1)
            self.assertEqual(points,{1:3})

    def test_bounce_speed_capped_at_100(self):
        for fn in self.implementations():
            _,w,_=self.run_scene(fn,self.box(),{'type':'kBank','state':'kNormal'},hs.Worker(1,5,1,0,99.9,0),.021)
            self.assertEqual(w.speed,100.)

    def test_received_launch_interval_and_point_ray(self):
        geo={'left':0,'right':20,'bottom':0,'top':20,'launcher':[1,5],'ball_time_dist':.4}
        world=hs.world_from_geo(geo)
        self.assertEqual(world.radius,0.)
        _,ws=hs.run_angle(world,{},[{'speed':5,'upgrades':{}}]*2,0,.1)
        self.assertEqual(ws[1].path[0][2],.4)
        for invalid in [0,-1,float('nan'),float('inf')]:
            self.assertIsNone(hs.world_from_geo(dict(geo,ball_time_dist=invalid)))

    def test_job_contact_position_is_not_inflated(self):
        geo={'left':0,'right':20,'bottom':0,'top':20,'launcher':[1,5],
             'colliders':[{'id':1,'shape':'box','pts':self.box().pts}]}
        result=sim_jobs.job_now(geo,{1:{'type':'kBank','state':'kNormal'}},[{'speed':5,'upgrades':{}}],0,.6)
        self.assertAlmostEqual(result['path'][1][0],3.)
