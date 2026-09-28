"""공사 우선 배치는 가까움·입구 비움·실제 도달·기존 효과를 함께 만족해야 한다."""

import copy
import time
import unittest
from unittest.mock import patch

from src.engine import construction_front as front, layout_opt as lo, layout_guide as guide
from src.engine.layout import Grid, grid_from_geo
from tests.test_guide_contract import base_of, bld


class ConstructionFrontTest(unittest.TestCase):
    def optimize(self, base, **kwargs):
        with patch("src.engine.native_layout._lib", return_value=None), \
                patch.object(lo, "anneal", side_effect=lambda lay, *a, **kw: (dict(lay.origin), 0)), \
                patch.object(lo, "polish", return_value=0):
            return lo.optimize(base, preset="guide", seconds=1., restarts=0, **kwargs)

    def base(self, kind="kClinic"):
        base = base_of(bld(1, kind, 9.5, 5.5, state="kUpgrading"))
        base["geo"].update(launcher=[8., -.5], entrance_chunk=[0, 0])
        return base

    def test_already_in_broad_front_zone_still_moves_closer_with_flat_yield(self):
        base = self.base()
        original = copy.deepcopy(base)
        grid = grid_from_geo(base["geo"])
        self.assertGreater(lo.lane_values(base["geo"], grid).get((9, 5), 0), 0)
        plan = self.optimize(base, harvest_eval=lambda _: [0, 10, 10, 10], reach_ok=lambda _: True)
        after = lo.final_base(base, plan)
        self.assertGreater(front.base_priority(after), front.base_priority(base))
        self.assertEqual(plan.moved, 1)
        self.assertTrue(guide.preserves_guide(base, after))
        self.assertEqual(base, original)

    def test_all_unfinished_types_and_old_target_use_construction_priority(self):
        for kind in ("kForest", "kSingleFamilyHome", "kClinic"):
            for state in ("kScaffold", "kUpgrading"):
                with self.subTest(kind=kind, state=state):
                    base = self.base(kind)
                    base["buildings"][0]["state"] = state
                    plan = self.optimize(base, prefer={1: (10.5, 7.5)})
                    after = lo.final_base(base, plan)
                    self.assertGreater(front.base_priority(after), front.base_priority(base))
                    grid = grid_from_geo(after["geo"])
                    pieces, origins = lo.pieces_from_base(after, grid, lo.housing_types())
                    self.assertFalse(set(lo.Layout(grid, pieces, origins).occ) & lo.entrance_cells(after["geo"], grid))

    def test_demolition_target_and_fixed_launcher_are_not_pulled_forward(self):
        for kind in ("kGoldMine", "kHome"):
            base = self.base(kind)
            self.assertEqual(self.optimize(base).moved, 0)

    def test_unknown_launcher_does_not_invent_a_front_position(self):
        base = self.base()
        base["geo"].pop("launcher")
        self.assertEqual(front.base_priority(base), ())
        self.assertEqual(self.optimize(base).moved, 0)

    def test_swap_can_clear_near_space_without_displacing_other_construction(self):
        grid = Grid(0, 0, 1, {(0, 0), (0, 1), (0, 2), (0, 8), (1, 8)})
        pieces = {1: lo.Piece(1, "kHome", 1, 1, frozenset({(0, 0)}), True, 0, unfinished=True),
                  2: lo.Piece(2, "kClinic", 1, 1, frozenset({(0, 0)}), True, 0),
                  3: lo.Piece(3, "kHome", 1, 1, frozenset({(0, 0)}), False, 0)}
        original = {1: (0, 8), 2: (0, 1), 3: (0, 2)}
        candidates = front.candidates(grid, pieces, original, (.5, .5), {(0, 0)},
                                      lambda *_: True, time.perf_counter() + 1)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0][0][1], (0, 1))
        self.assertEqual(candidates[0][0][2], (0, 8))
        self.assertEqual(candidates[0][0][3], (0, 2))
        pieces[2].unfinished = True
        candidates = front.candidates(grid, pieces, original, (.5, .5), {(0, 0)},
                                      lambda *_: True, time.perf_counter() + 1)
        self.assertTrue(all(origins[2] == original[2] for origins, _ in candidates))

    def test_wall_reach_is_checked_and_unreachable_current_position_does_not_win(self):
        from src.engine import harvest_sim as hs
        base = self.base()
        base["geo"]["launcher"] = [8., .5]
        base["buildings"][0].update(x=8.5, y=9.5)

        def reachable(candidate):
            row = candidate["buildings"][0]
            for dx, dy in ((0, 1), (.6, .8), (-.6, .8)):
                world = hs.World(0, 16, 0, 12, [
                    hs.Shape(1, "box", pts=[(row["x"]-.5, row["y"]-.5), (row["x"]+.5, row["y"]-.5),
                                            (row["x"]+.5, row["y"]+.5), (row["x"]-.5, row["y"]+.5)]),
                    hs.Shape(-1, "wall", pts=[(0, 5), (16, 5)]),
                ], (8, .5))
                counts = {}
                hs.simulate_team(world, {1: row}, [hs.Worker(8, .5, dx, dy, 2, 0)], 6., counts=counts)
                if counts.get(1, 0) > 0:
                    return True
            return False

        self.assertFalse(reachable(base))
        plan = self.optimize(base, reach_ok=reachable)
        self.assertTrue(reachable(lo.final_base(base, plan)))

    def test_existing_hub_coverage_is_preserved_when_pulling_construction_forward(self):
        base = self.base()
        base["buildings"].append(bld(2, "kBrickHouse", 9.5, 7.5, range=2.5))
        plan = self.optimize(base)
        self.assertTrue(guide.preserves_guide(base, lo.final_base(base, plan)))

    def test_sparse_shape_can_rotate_into_near_space_without_filling_its_empty_corner(self):
        footprint = frozenset({(0, 0), (0, 1), (0, 2), (1, 0)})
        old_cells = {(dx, 6 + dy) for dx, dy in footprint}
        near_cells = {(1, 1), (1, 2), (2, 2), (3, 2)}
        grid = Grid(0, 0, 1, old_cells | near_cells)
        pieces = {1: lo.Piece(1, "kClinic", 2, 3, footprint, True, 0, unfinished=True)}
        candidates = front.candidates(grid, pieces, {1: (0, 6)}, (2, 0), set(),
                                      lambda *_: True, time.perf_counter() + 1)
        self.assertTrue(candidates)
        origins, turns = candidates[0]
        rotated = lo.rotated(pieces[1], turns[1])
        self.assertEqual((rotated.w, rotated.h), (3, 2))
        self.assertEqual(set(lo.Layout(grid, {1: rotated}, origins).occ), near_cells)
