"""스파 재채집 손익, 금광 U자 프리셋."""
import json
import os
import unittest

from src.engine import layout_opt as lo
from src.engine.layout import grid_from_geo
from src.engine.spa import spa_advice

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


class SpaTest(unittest.TestCase):
    rows = [{"gain": [1600, 5, 10, 3], "layout": "A"}, {"gain": [1400, 3, 8, 1], "layout": "A"}]

    def test_profit_when_gold_exceeds_cost(self):
        a = spa_advice({"built": True, "cost": 500}, self.rows, "A")
        self.assertEqual(a.verdict, "profit")
        self.assertIn("순이익 1,000", a.text)

    def test_loss_and_resources(self):
        self.assertEqual(spa_advice({"built": True, "cost": 2500}, self.rows, "A").verdict, "loss")
        self.assertEqual(spa_advice({"built": True, "cost": 2500}, self.rows, "A", {"나무": 20}).verdict, "resources")

    def test_not_built_or_no_record(self):
        self.assertIsNone(spa_advice({"built": False, "cost": 50}, self.rows))
        self.assertEqual(spa_advice({"built": True, "cost": 50}, []).verdict, "unknown")


class GoldUTest(unittest.TestCase):
    def setUp(self):
        with open(FIX, encoding="utf-8") as f:
            self.fx = json.load(f)

    def test_spots_form_u_in_front_of_launcher(self):
        geo = self.fx["geo"]
        grid = grid_from_geo(geo)
        spots = lo.gold_u_spots(geo, grid)
        self.assertEqual(len(spots), 7)
        lc = int((geo["launcher"][0] - grid.ox) // grid.size)
        self.assertEqual(sorted({c for c, _ in spots}), [lc - 3, lc - 1, lc + 1])   # 양옆 기둥 + 위를 막는 1개
        cells = [(c + dx, r + dy) for c, r in spots for dx in range(2) for dy in range(2)]
        self.assertEqual(len(cells), len(set(cells)))                              # 겹치지 않음

    def test_preset_keeps_u_spots_clear_for_future_mines(self):
        base = {"buildings": self.fx["buildings_before"], "geo": self.fx["geo"]}
        grid = grid_from_geo(base["geo"])
        plan = lo.optimize(base, None, None, seconds=3, restarts=1, seed=2, preset="gold_u")
        pieces, _ = lo.pieces_from_base(base, grid, lo.housing_types())
        lay = lo.Layout(grid, pieces, plan.origin_after)
        spots = lo.gold_u_spots(base["geo"], grid)
        blocked = sum(1 for c, r in spots for dx in range(2) for dy in range(2) if (c + dx, r + dy) in lay.occ)
        lay0 = lo.Layout(grid, pieces, plan.origin_before)
        blocked0 = sum(1 for c, r in spots for dx in range(2) for dy in range(2) if (c + dx, r + dy) in lay0.occ)
        self.assertLess(blocked, blocked0)                                         # 금광 자리를 비워 둠


if __name__ == "__main__":
    unittest.main()
