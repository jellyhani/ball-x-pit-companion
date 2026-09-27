"""생산과 이동 사건의 순서가 같은 자원 재고를 공유하는지 검사한다."""
import copy
import unittest
from src.engine import harvest_sim as hs
from src.engine.world_tasks import TaskClock


class WorldTaskTest(unittest.TestCase):
    def resource(self,**kwargs):
        b=dict(type='kForest',state='kNormal',x=3,y=2,res=0,cap=2,can_harvest=False,
               raycast_enabled=False,pickup_enabled=False,task_seconds=4,task_target_seconds=5)
        b.update(kwargs);return b

    def test_regeneration_reactivates_ray_collision_before_worker_arrives(self):
        buildings={1:self.resource()};original=copy.deepcopy(buildings)
        world=hs.World(0,10,0,10,[hs.Shape(1,'box',pts=[(2.5,1.5),(3.5,1.5),(3.5,2.5),(2.5,2.5)])],
                       (1,2),task_clock=(.5,1.,1.))
        total,workers=hs.simulate_team(world,buildings,[hs.Worker(1,2,1,0,1,0)],2.)
        self.assertEqual(total,[0,0,1,0])
        self.assertEqual(workers[0].dx,-1.)
        self.assertEqual(buildings,original)

    def test_idle_producer_depletes_shared_stock_but_is_not_worker_income(self):
        b={1:self.resource(res=2,can_harvest=True,raycast_enabled=True,task_seconds=0),
           2:dict(type='kIdleLumberyard',state='kNormal',x=3,y=2,range=2,lvl=1,
                  task_active=True,is_idle_harvester=True,production_resource=2,task_seconds=4,task_target_seconds=5)}
        world=hs.World(0,10,0,10,[hs.Shape(1,'box',pts=[(2.5,1.5),(3.5,1.5),(3.5,2.5),(2.5,2.5)])],
                       (1,2),task_clock=(.5,1.,1.))
        total,workers=hs.simulate_team(world,b,[hs.Worker(1,2,1,0,1,0)],2.)
        self.assertEqual(total,[0,0,0,0]);self.assertEqual(world.automatic_gain,[0,0,2,0])
        self.assertEqual(workers[0].dx,1.)

    def test_full_resource_pauses_clock_and_snapshot_is_not_mutated(self):
        b={1:self.resource(res=2)};stocks={1:2};notes=set()
        clock=TaskClock((.25,1,2),b,stocks,hs.resource_kind,hs.resource_tile,notes)
        self.assertEqual(clock.next,.75)
        clock.tick();self.assertEqual(clock.elapsed[1],4)
        stocks[1]=0;clock.tick()
        self.assertEqual(stocks[1],1);self.assertTrue(b[1]['raycast_enabled'])

    def test_slow_game_rate_uses_unscaled_time_and_missing_clock_is_explicit(self):
        b={1:self.resource()};notes=set()
        clock=TaskClock((.5,1,.5),b,{1:0},hs.resource_kind,hs.resource_tile,notes)
        self.assertEqual(clock.next,.25)
        TaskClock(None,b,{1:0},hs.resource_kind,hs.resource_tile,notes)
        self.assertIn('missing_world_task_clock',notes)

    def test_cache_retains_results_within_timing_bucket(self):
        from src.engine.sim_signature import physical_base
        b={'buildings':[dict(self.resource(),id=1)],'geo':{'world_tick_progress':.1,'game_speed':1}}
        first=physical_base(b)
        b['geo']['world_tick_progress']=.8;b['buildings'][0]['task_seconds']=9
        self.assertEqual(first,physical_base(b))
        b['buildings'][0]['task_seconds']=10
        self.assertNotEqual(first,physical_base(b))

    def test_full_layout_restores_collision_roles_and_ignores_task_clock(self):
        from src.engine.sim_jobs import full_tiles
        from src.engine.sim_signature import physical_base
        row=dict(self.resource(),id=1)
        full=full_tiles({1:row})[1]
        self.assertTrue(full['raycast_enabled']);self.assertFalse(full['pickup_enabled'])
        base={'buildings':[row]};before=physical_base(base,full=True)
        row['task_seconds']=30
        self.assertEqual(before,physical_base(base,full=True))
