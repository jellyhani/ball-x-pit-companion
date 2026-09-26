"""게임 1.301 본문에서 확인한 채집량·시간·범위·작업자 참가 조건."""
import copy
import unittest
from src.engine import harvest_sim as hs, native


class HarvestPassivesTest(unittest.TestCase):
    def implementations(self):
        return [hs.simulate_team_py] + ([hs.simulate_team] if native.lib() is not None else [])

    def test_contact_amount_is_one_plus_resource_upgrade_level(self):
        shape=hs.Shape(1,'box',pts=[(3,4),(4,4),(4,6),(3,6)])
        world=hs.World(0,10,0,10,[shape],(1,5),radius=.03)
        b={1:{'type':'kBoulder','res':10,'can_harvest':True}}
        for fn in self.implementations():
            for level in (0,1,3):
                worker=hs.Worker(1,5,1,0,5,0,{'kFasterStone':level})
                total,_=fn(world,b,[worker],.5)
                self.assertEqual(total[3],1+level)
        self.assertEqual(b[1]['res'],10)

    def test_long_sickle_reaches_nearby_wheat_without_changing_bounce_radius(self):
        shape=hs.Shape(1,'box',pts=[(3,5.8),(4,5.8),(4,6.3),(3,6.3)])
        world=hs.World(0,10,0,10,[shape],(1,5),radius=.03)
        b={1:{'type':'kDenseWheat','res':5,'can_harvest':True}}
        for fn in self.implementations():
            for level,expected in ((0,0),(2,1)):
                total,workers=fn(world,b,[hs.Worker(1,5,1,0,5,0,{'kWheatRange':level})],1)
                self.assertEqual(total[1],expected)
                self.assertEqual(len(workers[0].path),2)

    def test_clock_bonus_stops_after_twenty_successful_resource_contacts(self):
        shape=hs.Shape(1,'box',pts=[(2,.5),(3,.5),(3,1.5),(2,1.5)])
        world=hs.World(0,4,0,4,[shape],(1,1),radius=.03)
        b={1:{'type':'kBoulder','res':500,'can_harvest':True}}
        for fn in self.implementations():
            worker=hs.Worker(1,1,1,0,5,0,{'kStoneTime':5},harvest_bonus={'kStoneTime':5})
            total,workers=fn(world,b,[worker],1)
            self.assertGreater(total[3],20)
            self.assertAlmostEqual(workers[0].t,21.)

    def test_empty_resource_does_not_extend_clock(self):
        shape=hs.Shape(1,'box',pts=[(2,.5),(3,.5),(3,1.5),(2,1.5)])
        world=hs.World(0,4,0,4,[shape],(1,1),radius=.03)
        for fn in self.implementations():
            worker=hs.Worker(1,1,1,0,5,0,{'kStoneTime':5})
            _,workers=fn(world,{1:{'type':'kBoulder','res':0,'can_harvest':False}},[worker],1)
            self.assertEqual(workers[0].t,1)

    def test_game_worker_conditions_and_initial_clock(self):
        chars=[{'type':'kDefault','state':'kWorking','work':'kIdleStoneMine','harvest':{'kExtraHarvestLength':1}},
               {'type':'kRecaller','state':'kWorking','work':'kGoldMine'},
               {'type':'kItchyFinger','state':'kWorking','work':'kIdleLauncher'},
               {'type':'kInfluencer','state':'kIdle'},
               {'type':'kShade','state':'kInBattle'},
               {'type':'kCogitator','state':'kIdle','harvest':{'kExtraHarvestLength':2}}]
        team=hs.team_from_chars(chars,['kCogitator','kDefault'])
        self.assertEqual([m['type'] for m in team],['kCogitator','kDefault'])
        self.assertEqual(hs.initial_harvest_duration({'geo':{'harvest_len':9}},team),17)
        self.assertEqual(hs.initial_harvest_duration({'state':'kAimWorkers','harvest_secs_left':17,
                                                    'geo':{'harvest_len':9}},team),17)
