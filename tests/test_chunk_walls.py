"""미개방 구역의 벽은 관통 효과와 무관하며, 건물 채집/건설로 세지 않는다."""
import copy
import math
import unittest

from src.engine import harvest_sim as hs, native, sim_jobs
from src.engine.aim_preview import visible_path


def geo():
    return dict(left=0., right=20., bottom=0., top=20., launcher=[15., -1.],
                chunks=[[0, 0], [0, 1], [1, 0]], chunk_world_w=10., chunk_world_h=10., colliders=[])


class ChunkWallsTest(unittest.TestCase):
    def test_extended_path_bounces_at_inner_wall_instead_of_bounding_rectangle(self):
        team=[{'type':'kDefault','speed':5.,'upgrades':{'kPierceBuildings':1}}]
        result=sim_jobs.job_now(geo(),{},team,90,20)
        path=visible_path(result['path'],extended=True)
        self.assertGreater(len(path),4)
        self.assertAlmostEqual(path[1][1],10.0)  # 게임은 점 Raycast로 벽 표면에 닿는다.
        self.assertTrue(all(y<=10.0+1e-8 for x,y in path))
        self.assertEqual(result['total'],[0,0,0,0])
        self.assertEqual(result['build_hits'],0)

    def test_purchased_seam_is_open_and_side_of_missing_chunk_is_solid(self):
        g=geo();g['launcher']=[5,5]
        w=hs.world_from_geo(g,.03)
        self.assertAlmostEqual(hs.simulate(w,90,4).points[1][1],19.97)
        g['launcher']=[5,15]
        self.assertAlmostEqual(hs.simulate(hs.world_from_geo(g,.03),0,4).points[1][0],9.97)
        g['chunks'].append([1,1])
        self.assertAlmostEqual(hs.simulate(hs.world_from_geo(g,.03),0,4).points[1][0],19.97)

    def test_chunk_origin_tracks_nonzero_minimum_and_non_square_size(self):
        g=dict(left=8.,right=26.,bottom=4.,top=17.5,launcher=[21.,3.],
               chunks=[[1,2],[1,3],[2,2]],chunk_world_w=9.,chunk_world_h=6.75)
        wall=next(s for s in hs.world_from_geo(g).shapes if s.kind=='wall')
        self.assertEqual(wall.bb,(17.,10.75,26.,17.5))

    def test_wall_is_not_a_building_hit_even_for_explicit_pierce(self):
        w=hs.world_from_geo(geo(),.03)
        result=hs.simulate(w,90,4,pierce=[s.bid for s in w.shapes])
        self.assertAlmostEqual(result.points[1][1],9.97)
        self.assertEqual(result.hits,[])

    def test_simultaneous_resource_pickup_does_not_cancel_wall_bounce(self):
        w=hs.world_from_geo(geo(),.03)
        # 작업자0은 .2초에 밀을 줍고 작업자1은 같은 시각 내부 벽에 닿는다.
        w.shapes.append(hs.Shape(1,'box',pts=[(4.925,4),(5.325,4),(5.325,6),(4.925,6)]))
        buildings={1:{'type':'kWheatField','res':1,'can_harvest':True}}
        workers=[hs.Worker(3.8,5,1,0,5,0,{}),hs.Worker(15,8.97,0,1,5,0,{'kPierceBuildings':1})]
        _,out=hs.simulate_team_py(w,buildings,workers,.5)
        self.assertLess(out[1].dy,0)

    @unittest.skipUnless(native.lib(),'네이티브 모듈 없음')
    def test_native_and_python_match_with_walls_resources_and_piercing(self):
        g=geo();g['launcher']=[5,1]
        w=hs.world_from_geo(g,.03)
        w.shapes.append(hs.Shape(1,'box',pts=[(4,4),(6,4),(6,6),(4,6)]))
        buildings={1:{'type':'kWheatField','res':8,'can_harvest':True}}
        for angle in (0,25,45,60,90,130,175):
            dx,dy=math.cos(math.radians(angle)),math.sin(math.radians(angle))
            workers=[hs.Worker(5,1,dx,dy,5,i*.3,{'kPierceBuildings':1}) for i in range(3)]
            a,b={},{}
            ta,wa=hs.simulate_team(w,buildings,copy.deepcopy(workers),12,counts=a)
            tb,wb=hs.simulate_team_py(w,buildings,copy.deepcopy(workers),12,counts=b)
            self.assertEqual(ta,tb)
            self.assertEqual(a,b)
            self.assertTrue(all(i>=0 for i in a))
            for x,y in zip(wa,wb):
                self.assertEqual(len(x.path),len(y.path))
                for p,q in zip(x.path,y.path):
                    for v,u in zip(p,q):self.assertAlmostEqual(v,u,places=8)
