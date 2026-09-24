"""채집 궤적·채집량 계산을 실제 게임 기록 1회(48도, 작업자 8명)와 비교한다.

기록: tests/fixtures/harvest_trace_48deg.json — 채집 전 건물 자원, 작업자별 실제 위치(0.2초 간격), 실제 증가량.
"""
import json
import math
import os
import unittest

from src.engine import harvest_sim as hs
from src.engine.layout import buildings_from_base, effect_score, plan_swaps, swap_geo, swap_positions

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


def load():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def pos_at(path, t):
    for i in range(1, len(path)):
        if path[i][2] >= t:
            a, b = path[i - 1], path[i]
            f = (t - a[2]) / (b[2] - a[2]) if b[2] > a[2] else 0
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
    return path[-1][0], path[-1][1]


class HarvestSimTest(unittest.TestCase):
    def setUp(self):
        self.fx = load()
        self.world = hs.world_from_geo(self.fx["geo"], 0.03)
        self.blds = {b["id"]: b for b in self.fx["buildings_before"]}

    def run_team(self):
        ws = [hs.Worker(w["x"], w["y"], w["dx"], w["dy"], w["speed"], w["t"], w["upgrades"]) for w in self.fx["workers"]]
        return hs.simulate_team(self.world, self.blds, ws, self.fx["duration"])

    def test_paths_follow_real_workers_for_6_seconds(self):
        _, ws = self.run_team()
        for w, real in zip(ws, self.fx["workers"]):
            for t, x, y in real["real"]:
                if t - real["t"] > 6.5:
                    break
                sx, sy = pos_at(w.path, t)
                self.assertLess(math.dist((sx, sy), (x, y)), 0.6, f"{real['char']} {t:.1f}s")

    def test_yield_close_to_real(self):
        total, _ = self.run_team()
        actual = self.fx["actual_gain"]
        self.assertEqual(total[1], actual[1])          # 밀 정확
        self.assertEqual(total[2], actual[2])          # 나무 정확
        self.assertLessEqual(abs(total[3] - actual[3]), 5)   # 돌: 12초 뒤 경로가 조금 어긋나 최대 5 차이

    def test_homography_reproduces_corners(self):
        h = hs.homography(self.fx["geo"]["proj"])
        for wx, wy, sx, sy in self.fx["geo"]["proj"]:
            x, y = hs.to_screen(h, wx, wy)
            self.assertLess(abs(x - sx) + abs(y - sy), 2)

    def test_launch_below_bottom_wall_goes_up(self):
        p = hs.simulate(self.world, 90, 3.0, 5.0)
        self.assertGreater(p.points[1][1], self.world.launcher[1])


class LayoutTest(unittest.TestCase):
    def test_swap_moves_colliders_with_buildings(self):
        fx = load()
        base = {"buildings": fx["buildings_before"], "geo": fx["geo"]}
        blds = buildings_from_base(base)
        a, b = next((i, j) for i in blds for j in blds if i < j and blds[i].footprint == blds[j].footprint
                    and blds[i].type != blds[j].type)
        g2 = swap_geo(fx["geo"], blds, a, b)
        moved = {c["id"]: c for c in g2["colliders"]}
        orig = {c["id"]: c for c in fx["geo"]["colliders"]}
        for bid, other in ((a, b), (b, a)):
            d = (blds[other].x - blds[bid].x, blds[other].y - blds[bid].y)
            c0, c1 = orig[bid], moved[bid]
            p0 = c0.get("c") or c0["pts"][0]
            p1 = c1.get("c") or c1["pts"][0]
            self.assertAlmostEqual(p1[0] - p0[0], d[0], places=3)
            self.assertAlmostEqual(p1[1] - p0[1], d[1], places=3)
        s2 = swap_positions(blds, a, b)
        self.assertEqual((s2[a].x, s2[a].y), (blds[b].x, blds[b].y))

    def test_plan_never_lowers_score(self):
        fx = load()
        plan = plan_swaps({"buildings": fx["buildings_before"], "geo": fx["geo"]}, 6)
        self.assertGreaterEqual(plan.score_after, plan.score_before)
        before, _ = effect_score(buildings_from_base({"buildings": fx["buildings_before"]}))
        self.assertAlmostEqual(before, plan.score_before)


class GridTest(unittest.TestCase):
    def test_every_real_building_sits_on_the_grid(self):
        from src.engine.layout import building_cells, grid_from_geo, occupied, shape_masks
        fx = load()
        blds = buildings_from_base({"buildings": fx["buildings_before"]})
        g = grid_from_geo(fx["geo"])
        masks = shape_masks(fx["geo"], blds, g)
        cells = [building_cells(b, g, masks.get(i)) for i, b in blds.items()]
        for c in cells:
            self.assertTrue(c <= g.tiles)
        # 실제 충돌 모양으로 보면 건물끼리 겹치는 타일이 없다 (사각형으로 보면 ㄱ자 건물 때문에 10칸 겹침)
        self.assertEqual(sum(len(c) for c in cells), len(occupied(blds, g, masks=masks)))

    def test_new_spot_is_free_and_covers_tiles(self):
        from src.engine.layout import grid_from_geo, occupied
        fx = load()
        base = {"buildings": fx["buildings_before"], "geo": fx["geo"]}
        plan = plan_swaps(base, 0, None, [{"type": "kIdleLumberyard"}])
        self.assertEqual(len(plan.new_spots), 1)
        ns = plan.new_spots[0]
        from src.engine.layout import shape_masks
        g = grid_from_geo(fx["geo"])
        blds = buildings_from_base(base)
        cells = g.cells(ns.center[0], ns.center[1], *ns.size)
        self.assertTrue(cells <= g.tiles)
        self.assertFalse(cells & occupied(blds, g, masks=shape_masks(fx["geo"], blds, g)))
        self.assertGreater(ns.covered, 0)


if __name__ == "__main__":
    unittest.main()
