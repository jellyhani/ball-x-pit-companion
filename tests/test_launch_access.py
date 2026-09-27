"""입구 복구는 생산을 보존하고, 점수 문턱과 관통 여부에 가려지지 않는다."""
import copy
import unittest
from unittest.mock import patch
from src.engine import harvest_sim as hs, layout_opt as lo, sim_jobs
from src.engine.launch_access import allowed_angles, entrance_blockers, repair_entrance
from tests.test_resource_access import unchanged_plan


def front_base():
    b=dict(geo=dict(left=-.5625,right=8.4375,bottom=-.5625,top=8.4375,
                    space_w=1.125,chunk_w=8,chunk_h=8,chunks=[[0,0]],entrance_chunk=[0,0],
                    launcher=[1.1,-.844],colliders=[]),buildings=[])
    for i,x in enumerate((0.,1.125,2.25),1):
        b['buildings'].append(dict(id=i,type='kGrandTree',x=x,y=0.,tw=1,th=1,rot=0,range=0.,stat='kNum',cap=7,res=7,can_harvest=True))
        b['geo']['colliders'].append(dict(id=i,shape='box',pts=[[x-.5625,-.5625],[x+.5625,-.5625],[x+.5625,.5625],[x-.5625,.5625]]))
    return b


class LaunchAccessTest(unittest.TestCase):
    def test_first_contact_below_game_threshold_blocks_even_empty_resource(self):
        b=front_base();angles=list(range(20,161,5))
        self.assertEqual(allowed_angles(b['geo'],angles),[])
        for row in b['buildings']:row.update(res=0,can_harvest=False)
        self.assertEqual(allowed_angles(b['geo'],angles),[])

    def test_front_row_repair_opens_all_tested_angles_and_preserves_active_production(self):
        b=front_base()
        b['buildings'].append(dict(id=9,type='kIdleLumberyard',x=1.125,y=3.375,tw=1,th=1,rot=0,
                                  stat='kNum',range=3.375,worker=0,in_range={'kGrandTree':3}))
        original=copy.deepcopy(b)
        moves,after=repair_entrance(b)
        self.assertEqual(len(moves),3)
        self.assertEqual(entrance_blockers(after),set())
        angles=list(range(20,161,5))
        self.assertEqual(allowed_angles(after['geo'],angles),angles)
        self.assertTrue(lo.preserves_production(b,after))
        self.assertEqual(b,original)

    def test_entrance_repair_bypasses_range_effect_threshold(self):
        b=front_base()
        with patch.object(lo,'optimize',return_value=unchanged_plan(b)):
            plan,_=sim_jobs.job_layout(b,[],9,[],seconds=.01)
        self.assertTrue(plan.movement_complete)
        self.assertEqual(len(plan.swaps),3)
        self.assertFalse(entrance_blockers(plan.evaluated_base))
        self.assertTrue(all('입구' in move.reason for move in plan.swaps))

    def test_unfinished_blocker_is_reported_and_does_not_offer_new_purchases(self):
        b=front_base()
        for row in b['buildings']:row['state']='kScaffold'
        with patch.object(lo,'optimize',return_value=unchanged_plan(b)):
            plan,_=sim_jobs.job_layout(b,[],9,[],seconds=.01)
        self.assertEqual(plan.swaps,[])
        self.assertEqual(len(entrance_blockers(plan.evaluated_base)),3)
        self.assertTrue(plan.construction_pending)

    def test_sweep_never_recommends_a_forbidden_launch(self):
        result=sim_jobs.job_sweep(front_base()['geo'],{},[{'type':'kDefault','speed':5,'upgrades':{'kPierceBuildings':1}}],9,2,lo=20,hi=160)
        self.assertEqual(result['top'],[])
