"""합성 벽으로 둘러싼 자원: 관통 채집 계수, 접근 복구, 입구·생산·실행 가능한 이동 보존."""
import copy
import unittest
from unittest.mock import patch

from src.engine import harvest_sim as hs, native, layout_opt as lo, sim_jobs
from src.engine.layout import grid_from_geo, buildings_from_base, occupied, shape_masks
from src.engine.resource_access import ResourceAccess, repair_resources
from tests.test_layout_safety import base_of, building


TEAM=[{'type':'kDefault','speed':5,'upgrades':{'kPierceWood':1,'kFasterWood':6}}]


def enclosed():
    rows=[building(1,'kGrandTree',6.5,6.5,cap=7,res=7,can_harvest=True,state='kNormal')]
    # 대각선 틈도 막는다. 작업자 몸체보다 넓은 수집 범위가 모서리 너머 자원에 닿을 수 있다.
    ring=((5.5,6.5),(6.5,5.5),(7.5,6.5),(6.5,7.5),(5.5,5.5),(5.5,7.5),(7.5,5.5),(7.5,7.5))
    rows += [building(i,'kHome',x,y) for i,(x,y) in enumerate(ring,2)]
    b=base_of(rows,8,8,True)
    # base_of의 0.98칸 모양에는 실제 0.02칸 틈이 있다. 밀폐 장면은 경계를 맞붙인다.
    for col in b['geo']['colliders']:
        col['pts']=[[round(x),round(y)] for x,y in col['pts']]
    b['geo'].update(right=8.,top=8.,launcher=[1.5,.5])
    return b


def unchanged_plan(base):
    grid=grid_from_geo(base['geo']);p,o=lo.pieces_from_base(base,grid,lo.housing_types())
    centers={i:grid.center(*at,p[i].w,p[i].h) for i,at in o.items()}
    return lo.FullPlan(o,dict(o),centers,1.,1.,{}, {}, notes=['2% 이하 유지'])


class ResourceAccessTest(unittest.TestCase):
    def test_production_stock_changes_do_not_invalidate_full_layout_but_do_update_aim(self):
        from src.engine.sim_signature import physical_base
        b=enclosed()
        before=copy.deepcopy(b)
        before['buildings'][0]['held']=[0,0,7,0]
        after=copy.deepcopy(before)
        after['buildings'][0].update(res=0,held=[0,0,0,0],can_harvest=False)
        self.assertEqual(physical_base(before,full=True),physical_base(after,full=True))
        self.assertNotEqual(physical_base(before),physical_base(after))
        after['buildings'][0]['cap']=14
        self.assertNotEqual(physical_base(before,full=True),physical_base(after,full=True))

    def test_pass_through_harvest_is_recorded_without_a_bounce(self):
        shape=hs.Shape(1,'box',pts=[(3,.5),(4,.5),(4,1.5),(3,1.5)])
        world=hs.World(0,10,0,10,[shape],(1,1),radius=.03)
        b={1:{'type':'kGrandTree','cap':7,'res':7,'can_harvest':True}}
        funcs=[hs.simulate_team_py]+([hs.simulate_team] if native.lib() else [])
        for fn in funcs:
            counts,got={},{}
            total,workers=fn(world,b,[hs.Worker(1,1,1,0,5,0,{'kPierceWood':1})],.8,counts=counts,collected=got)
            # MoveBalls는 WorkerHitBuilding을 관통 분기보다 먼저 호출한다. 접촉과 반사를 구분한다.
            self.assertEqual(counts,{1:1})
            self.assertEqual(workers[0].dx,1.)
            self.assertEqual(got,{1:1})
            self.assertEqual(total[2],1)
        self.assertEqual(b[1]['res'],7)

    def test_unreachable_tree_is_moved_without_blocking_entrance_or_overlapping(self):
        b=enclosed();before=copy.deepcopy(b);checker=ResourceAccess(b,TEAM,4,[30,45,60,90,120,150])
        self.assertEqual(checker.check(b).blocked,{1})
        moves,after=repair_resources(b,checker)
        self.assertEqual(len(moves),1)
        self.assertEqual(checker.check(after).blocked,set())
        grid=grid_from_geo(after['geo']);pcs,origin=lo.pieces_from_base(after,grid,lo.housing_types())
        cells=[c for i in pcs for c in lo.Layout(grid,pcs,origin).cells(i)]
        self.assertEqual(len(cells),len(set(cells)))
        self.assertFalse(grid.cells(*moves[0].to,1,1)&lo.entrance_cells(b['geo'],grid))
        self.assertEqual(b,before)

    def test_only_working_calibrated_producer_counts_as_automatic_access(self):
        for worker,expected in ((-1,{1}),(0,set())):
            b=enclosed();p=b['buildings'][1]
            p.update(type='kIdleLumberyard',range=2.,worker=worker,in_range={'kGrandTree':1})
            checker=ResourceAccess(b,TEAM,4,[45,90])
            report=checker.check(b)
            self.assertEqual(report.collected,{})
            self.assertEqual(report.blocked,expected)

    def test_unknown_collision_is_not_reported_as_impossible_access(self):
        b=enclosed();b['geo']['colliders']=[c for c in b['geo']['colliders'] if c['id']!=1]
        self.assertIsNone(ResourceAccess(b,TEAM,4,[45]).check(b))

    def test_idle_producer_follows_relocated_resource_without_losing_access(self):
        b=enclosed()
        b['buildings'][1].update(type='kIdleLumberyard',range=1.1,worker=-1,
                                  in_range={'kGrandTree':1})
        checker=ResourceAccess(b,TEAM,4,[30,45,60,90,120,150])
        moves,after=repair_resources(b,checker,
                                    accept=lambda candidate: candidate['buildings'][0]['x'] < 3,
                                    max_candidates=128)
        self.assertEqual([m.a for m in moves],[1,2])
        self.assertEqual(checker.check(after).blocked,set())
        rows={r['id']:r for r in after['buildings']}
        self.assertTrue(lo.in_range(rows[1]['x']-rows[2]['x'],rows[1]['y']-rows[2]['y'],1.1))
        # 근로자를 배정한 생산 건물은 이미 이 자원을 채집하므로 이동할 이유가 없다.
        b['buildings'][1]['worker']=0
        working=ResourceAccess(b,TEAM,4,[30,45,60,90,120,150])
        self.assertEqual(repair_resources(b,working)[0],[])

    def test_today_empty_tile_is_checked_with_refilled_resources(self):
        b=base_of([building(1,'kGrandTree',1.5,3.5,cap=7,res=0,can_harvest=False)],8,8)
        b['geo'].update(right=8.,top=8.,launcher=[1.5,.5])
        report=ResourceAccess(b,TEAM,4,[90]).check(b)
        self.assertEqual(report.blocked,set())
        self.assertGreater(report.collected[1],0)

    def test_external_construction_guard_can_reject_all_resource_moves(self):
        b=enclosed();checker=ResourceAccess(b,TEAM,4,[45,90])
        moves,after=repair_resources(b,checker,accept=lambda _:False)
        self.assertEqual(moves,[])
        self.assertEqual(after,b)

    def test_resource_repair_is_not_lost_to_two_percent_effect_threshold(self):
        b=enclosed()
        with patch.object(lo,'optimize',return_value=unchanged_plan(b)):
            # 여기서는 복구 채택 기준을 검사한다. 정확히 접한 모서리를 지나는 45도 광선은 제외한다.
            plan,_=sim_jobs.job_layout(b,TEAM,4,[],seconds=.01,aim_limits=(30,40))
        self.assertTrue(plan.resource_access_checked)
        self.assertEqual(plan.resource_unreachable_before,(1,))
        self.assertEqual(plan.resource_unreachable_after,())
        self.assertTrue(plan.movement_complete)
        self.assertEqual([m.a for m in plan.swaps],[1])
        self.assertNotIn('2% 이하 유지',plan.notes)
        self.assertEqual(plan.score_after,plan.score_before)
        self.assertTrue(plan.construction_pending)

    def test_full_board_reports_unresolved_resources_and_defers_purchases(self):
        rows=[building(1,'kGrandTree',1.5,1.5,cap=7,res=7,can_harvest=True)]
        rows += [building(2+y*3+x,'kHome',x+.5,y+.5) for x in range(3) for y in range(3) if (x,y)!=(1,1)]
        b=base_of(rows,3,3);b['geo'].update(right=3.,top=3.,launcher=[1.5,-1.])
        for col in b['geo']['colliders']:
            col['pts']=[[round(x),round(y)] for x,y in col['pts']]
        with patch.object(lo,'optimize',return_value=unchanged_plan(b)):
            plan,_=sim_jobs.job_layout(b,TEAM,4,[],seconds=.01,aim_limits=(20,160))
        self.assertEqual(plan.swaps,[])
        self.assertEqual(plan.resource_unreachable_after,(1,))
        self.assertTrue(plan.construction_pending)
        self.assertEqual(plan.builds,[])
