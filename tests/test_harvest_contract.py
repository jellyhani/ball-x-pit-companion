"""발사 입력을 이후 상태와 격리하고 불충분한 기록을 정확한 재현으로 오인하지 않는다."""
import unittest
from src.tracking.harvest_contract import capture,observation
from src.engine.harvest_sim import team_from_base
from src.engine.sim_signature import physical_base


class HarvestContractTest(unittest.TestCase):
    def test_capture_keeps_original_team_resources_and_geometry(self):
        team=[{'type':'kDefault','upgrades':{'kWheatTime':1},'harvest_bonus':{'kWheatTime':1}}]
        base={'state':'kAimWorkers','geo':{'launch_team':team,'environment':{'roads':[],'walls':[]},
               'game_time':10,'physics_time':10,'ball_time_dist':.3,'game_speed':1,
               'world_tick_progress':.2,'world_tick_interval':1},'buildings':[{'id':1,'res':7}]}
        record=capture(base,team,[1,2,3,4],game_version='1.301')
        base['buildings'][0]['res']=0;team[0]['upgrades']['kWheatTime']=9
        self.assertEqual(record['base']['buildings'][0]['res'],7)
        self.assertEqual(record['team'][0]['upgrades']['kWheatTime'],1)
        self.assertTrue(record['complete_for_replay'])

    def test_old_trace_is_identified_as_incomplete(self):
        result=capture({'geo':{}},[],[])
        self.assertFalse(result['complete_for_replay'])
        self.assertIn('launch_team',result['missing'])

    def test_actual_launch_order_beats_meta_and_empty_actual_team_stays_empty(self):
        raw=[{'type':'kDefault','state':'kIdle'}]
        base={'state':'kAimWorkers','geo':{'launch_team':[]}}
        self.assertEqual(team_from_base(base,raw),[])
        base['geo']['launch_team']=[{'type':'kRecaller','upgrades':{},'harvest_bonus':{}}]
        self.assertEqual(team_from_base(base,raw)[0]['type'],'kRecaller')

    def test_clock_sampling_does_not_invalidate_physical_cache(self):
        a={'geo':{'game_time':1,'physics_time':1,'environment_age':.1,'ball_time_dist':.3}}
        b={'geo':dict(a['geo'],game_time=2,physics_time=2,environment_age=.2)}
        self.assertEqual(physical_base(a),physical_base(b))
        b['geo']['ball_time_dist']=.4
        self.assertNotEqual(physical_base(a),physical_base(b))
