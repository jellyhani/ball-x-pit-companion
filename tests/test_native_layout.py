"""배치 최적화 네이티브 계산: 점수가 파이썬과 같은지, 담금질이 파이썬보다 나쁘지 않은지 (실제 기지 1개)."""
import json
import os
import random
import unittest

from src.engine import layout_opt as lo
from src.engine import native_layout as nl
from src.engine.layout import grid_from_geo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


@unittest.skipUnless(nl._lib() is not None, "네이티브 모듈 없음 (native\\build.ps1)")
class NativeLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8") as f:
            fx = json.load(f)
        cls.geo = fx["geo"]
        cls.base = {"buildings": fx["buildings_before"], "geo": fx["geo"]}
        cls.grid = grid_from_geo(fx["geo"])
        cls.pieces, cls.o0 = lo.pieces_from_base(cls.base, cls.grid, lo.housing_types())

    def scorer(self, **kw):
        return lo.Scorer(self.pieces, lo._stat_types(self.base), lo.housing_types(), {1: 1.5, 2: 1.0, 3: 1.0}, 0.3, **kw)

    def test_score_same_as_python(self):
        lane = lo.lane_values(self.geo, self.grid)
        for kw in ({}, {"lane": lane}, {"lane": lane, "preset_spots": lo.gold_u_spots(self.geo, self.grid)}):
            sc = self.scorer(**kw)
            lay = lo.Layout(self.grid, self.pieces, self.o0)
            rng = random.Random(5)
            mov = [i for i, p in self.pieces.items() if p.movable]
            for _ in range(150):
                i = rng.choice(mov)
                lay.swap_regions(lay.origin[i], rng.choice(sorted(self.grid.tiles)), self.pieces[i].w, self.pieces[i].h)
                self.assertAlmostEqual(sc.score(lay)[0], nl.score(lay, sc), places=9)

    def test_anneal_result_is_valid_and_scored_right(self):
        sc = self.scorer(lane=lo.lane_values(self.geo, self.grid))
        lay = lo.Layout(self.grid, self.pieces, self.o0)
        org, best, iters = nl.anneal(lay, sc, 0.3, 11, 0.8, 0.005, self.o0, lo.EXPLORE_COST)
        self.assertGreater(iters, 10000)
        out = lo.Layout(self.grid, self.pieces, org)
        cells = [c for i in out.origin for c in out.cells(i)]
        self.assertEqual(len(cells), len(set(cells)))                    # 겹침 없음
        self.assertTrue(set(cells) <= self.grid.tiles)                   # 산 땅 안
        self.assertTrue(all(org[i] == self.o0[i] for i, p in self.pieces.items() if not p.movable))
        self.assertAlmostEqual(best, lo._objective(out, sc, self.o0, lo.EXPLORE_COST), places=9)

    def test_not_worse_than_python(self):
        sc = self.scorer(lane=lo.lane_values(self.geo, self.grid))
        org, best, _ = nl.anneal(lo.Layout(self.grid, self.pieces, self.o0), sc, 0.5, 3, 0.8, 0.005, self.o0,
                                 lo.EXPLORE_COST)
        keep = nl._lib
        try:
            nl._lib = lambda: None                                          # 파이썬 담금질
            _, py = lo.anneal(lo.Layout(self.grid, self.pieces, self.o0), sc, 1.0, random.Random(3), origin0=self.o0)
        finally:
            nl._lib = keep
        self.assertGreaterEqual(best, py - 1e-9)

    def test_polish_never_worse(self):
        sc = self.scorer()
        lay = lo.Layout(self.grid, self.pieces, self.o0)
        before = lo._objective(lay, sc, self.o0)
        after = lo.polish(lay, sc, self.o0, 1.0)
        self.assertGreaterEqual(after, before - 1e-9)
        self.assertAlmostEqual(after, lo._objective(lay, sc, self.o0), places=9)   # 배치도 그 점수대로 옮겨짐


if __name__ == "__main__":
    unittest.main()
