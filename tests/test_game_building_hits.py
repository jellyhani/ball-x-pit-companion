"""BuildingInst.GetHitByWorker(0x462E50)의 효과와 처리 순서를 검증한다."""
import copy
import unittest
from src.engine import harvest_sim as hs


class BuildingHitContractTest(unittest.TestCase):
    def scene(self,kind,**fields):
        b=dict(type=kind,x=3.5,y=5.,state='kNormal',effect_value=100,hit_limit=1,housing_effect_active=True,
               range=2.,range_boxes=[[-.5,-.5,.5,.5]])
        b.update(fields)
        w=hs.World(0,20,0,20,[hs.Shape(1,'box',pts=[(3,4.5),(4,4.5),(4,5.5),(3,5.5)])],(1,5))
        return w,{1:b}

    def test_haunted_house_multiplies_speed_before_bounce_acceleration(self):
        world,buildings=self.scene('kHauntedHouse')
        _,ws=hs.simulate_team(world,buildings,[hs.Worker(1,5,1,0,5,0)],.6)
        self.assertAlmostEqual(ws[0].speed,10.2)
        self.assertEqual(ws[0].dx,-1.)

    def test_inactive_housing_does_not_invent_effect(self):
        world,buildings=self.scene('kHauntedHouse',housing_effect_active=False)
        _,ws=hs.simulate_team(world,buildings,[hs.Worker(1,5,1,0,5,0)],.6)
        self.assertAlmostEqual(ws[0].speed,5.2)

    def test_monastery_extends_shared_clock(self):
        world,buildings=self.scene('kMonastery')
        _,ws=hs.simulate_team(world,buildings,[hs.Worker(1,5,1,0,5,0)],.6)
        self.assertAlmostEqual(ws[0].t,1.6)

    def test_brick_house_advances_other_building_without_direct_hit(self):
        world,buildings=self.scene('kBrickHouse',effect_value=4)
        buildings[2]=dict(type='kSchoolhouse',state='kScaffold',x=5.,y=5.,upg_pts=0,upg_tgt=10,
                          range_boxes=[[-.5,-.5,.5,.5]])
        points={}
        hs.simulate_team(world,buildings,[hs.Worker(1,5,1,0,5,0)],.6,build_points=points)
        self.assertEqual(points,{2:4})

    def test_baby_workers_spawn_once_and_stop_collecting_after_owner_limit(self):
        world,buildings=self.scene('kBabyWorkerCross')
        workers=[hs.Worker(1,5,1,0,5,0)]
        hs.simulate_team(world,buildings,workers,4.)
        self.assertEqual(len(workers),5)
        self.assertTrue(all(w.owner_id==1 for w in workers[1:]))
        self.assertTrue(all(w.bounces<=1 for w in workers[1:]))
        self.assertTrue(all(not w.active for w in workers[1:]))

    def test_completed_construction_does_not_keep_receiving_points(self):
        world,buildings=self.scene('kSchoolhouse',state='kScaffold',upg_pts=0,upg_tgt=1)
        original=copy.deepcopy(buildings);points={}
        hs.simulate_team(world,buildings,[hs.Worker(1,5,1,0,5,0)],5.,build_points=points)
        self.assertEqual(points,{1:1})
        self.assertEqual(buildings,original)
        self.assertNotIn('completion_global_bonuses_not_projected',world.model_notes)

    def test_cobbler_completion_changes_future_launches_not_already_flying_workers(self):
        world,buildings=self.scene('kCobbler',state='kScaffold',upg_pts=0,upg_tgt=1,
                                   effect_value=20,completion_effect_value=20)
        workers=[hs.Worker(1,5,1,0,5,0),hs.Worker(1,2,1,0,6,.5,harvest_bonus={'kHarvestSpeed':20})]
        hs.simulate_team(world,buildings,workers,.6)
        self.assertAlmostEqual(workers[0].speed,5.2)
        self.assertAlmostEqual(workers[1].speed,7.2)

    def test_missing_effect_input_is_reported(self):
        self.assertIn('missing_building_effect:kHauntedHouse',
                      hs.model_limitations([],{1:{'type':'kHauntedHouse'}}))
