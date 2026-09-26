"""합성 기지로 이동 순서·후속 길 열기·범위 보고의 같은 상태 계약을 확인한다."""
import copy
import pickle
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from src.engine import layout_opt as lo, sim_jobs
from src.engine.layout import Grid, Move, build_purchase_cost, buildings_from_base, grid_from_geo, moved_base, plan_access
from src.engine.layout_city import guide_report
from src.engine.layout_guide import covered_members, hub_groups, preserves_guide, repair


def building(i, kind, x, y, **fields):
    return dict(id=i, type=kind, x=x, y=y, tw=1, th=1, rot=0, range=0.0, stat="kNum",
                **fields)


def base_of(buildings, width=8, height=8, entrance=False):
    base = {"geo": {"space_w": 1.0, "chunk_w": width, "chunk_h": height, "chunks": [[0, 0]],
                    "left": 0.0, "bottom": 0.0, "colliders": []}, "buildings": buildings}
    if entrance:
        base["geo"]["entrance_chunk"] = [0, 0]
    for b in buildings:
        x, y = b["x"], b["y"]
        base["geo"]["colliders"].append({"id": b["id"], "shape": "box",
            "pts": [[x - .49, y - .49], [x + .49, y - .49], [x + .49, y + .49], [x - .49, y + .49]]})
    return base


def boxed_swap():
    return base_of([building(1, "kA", .5, .5), building(2, "kB", 3.5, .5),
                    building(3, "kHome", .5, 1.5), building(4, "kHome", 3.5, 1.5)], 4, 2, True)


class MovementSafetyTest(unittest.TestCase):
    def test_unavailable_parking_never_returns_a_partial_or_entrance_move(self):
        base = boxed_swap()
        grid = grid_from_geo(base["geo"])
        pieces, before = lo.pieces_from_base(base, grid, lo.housing_types())
        target = dict(before)
        target[1], target[2] = before[2], before[1]
        result = lo.move_sequence(grid, pieces, before, target, avoid=lo.entrance_cells(base["geo"], grid))
        self.assertFalse(result.complete)
        self.assertEqual(result, [])
        self.assertEqual(set(result.unresolved), {1, 2})
        final = {i: grid.center(*at, 1, 1) for i, at in target.items()}
        remaining = lo.remaining_moves(base, final)
        self.assertFalse(remaining.complete)
        self.assertEqual(remaining, [])
        restored = pickle.loads(pickle.dumps(remaining))
        self.assertFalse(restored.complete)
        self.assertEqual(restored.unresolved, remaining.unresolved)

    def test_safe_temporary_parking_still_finishes_swap(self):
        base = boxed_swap()
        base["geo"]["chunk_h"] = 3
        grid = grid_from_geo(base["geo"])
        pieces, before = lo.pieces_from_base(base, grid, lo.housing_types())
        target = dict(before)
        target[1], target[2] = before[2], before[1]
        final = {i: grid.center(*at, 1, 1) for i, at in target.items()}
        result = lo.remaining_moves(base, final)
        self.assertTrue(result.complete)
        self.assertTrue(result)
        state = dict(before)
        entrance = lo.entrance_cells(base["geo"], grid)
        for m in result:
            cells = grid.cells(*m.to, 1, 1)
            self.assertFalse(cells & entrance)
            state[m.a] = next(iter(cells))
            self.assertEqual(len(set(state.values())), len(state))
        self.assertEqual(state, target)

    def test_unreachable_goal_is_reported_as_held_current_state(self):
        base = boxed_swap()
        original = copy.deepcopy(base)
        grid = grid_from_geo(base["geo"])
        pieces, before = lo.pieces_from_base(base, grid, lo.housing_types())
        target = dict(before)
        target[1], target[2] = before[2], before[1]
        full = lo.FullPlan(before, target, {i: grid.center(*at, 1, 1) for i, at in target.items()},
                           10.0, 20.0, {}, {}, moved=2)
        plan = sim_jobs._plan_from(base, full, grid, {}, [], lambda _: {}, lambda _: [0] * 4,
                                  [], {}, 0.0, {}, 16.0, 1, (0, 0, 0), [0] * 4)
        self.assertFalse(plan.movement_complete)
        self.assertTrue(plan.construction_pending)
        self.assertEqual(plan.swaps, [])
        self.assertEqual(plan.score_before, plan.score_after)
        self.assertEqual(plan.evaluated_base, original)
        self.assertTrue(all(plan.final[b["id"]] == (b["x"], b["y"]) for b in base["buildings"]))
        self.assertEqual(plan.builds, [])
        self.assertEqual(base, original)

    def test_repair_expired_budget_does_not_mutate_input(self):
        base = boxed_swap()
        grid = grid_from_geo(base["geo"])
        pieces, before = lo.pieces_from_base(base, grid, lo.housing_types())
        original = dict(before)
        self.assertIsNone(repair(grid, pieces, before, [], 0.0, deadline=time.perf_counter() - 1))
        self.assertEqual(before, original)


class AccessSafetyTest(unittest.TestCase):
    def test_opening_second_target_cannot_close_first_target(self):
        base = base_of([building(1, "kSchoolhouse", .5, .5, state="kScaffold"),
                        building(2, "kGunsmith", 4.5, .5, state="kScaffold"),
                        building(3, "kBank", 4.5, 1.5, state="kNormal")], 6, 4)
        original = copy.deepcopy(base)

        def reach(geo):
            shape = next(c for c in geo["colliders"] if c["id"] == 3)
            original_place = abs(sum(p[1] for p in shape["pts"]) / 4 - 1.5) < 1e-6
            return {1: 5 if original_place else 0, 2: 0 if original_place else 5}

        moves, final, _ = plan_access(base, [1, 2], reach,
            accept_fn=lambda candidate: lo.preserves_production(base, candidate) and preserves_guide(base, candidate))
        self.assertEqual(moves, [])
        self.assertEqual(reach(final["geo"]), {1: 5, 2: 0})
        self.assertEqual(base, original)


class EffectEvidenceTest(unittest.TestCase):
    def test_purchase_cost_distinguishes_tile_quantity_from_building_coverage(self):
        costs = {"kDenseWheat": (100, 1, 0, 0), "kIdleFarm": (100, 0, 0, 0)}
        self.assertEqual(build_purchase_cost(("kDenseWheat", (0, 0), (1, 1), 1.0, 8, 0), costs),
                         (800, 8, 0, 0))
        self.assertEqual(build_purchase_cost(("kIdleFarm", (0, 0), (2, 2), 1.0, 32, 0), costs),
                         (100, 0, 0, 0))
        self.assertIsNone(build_purchase_cost(("kUnknown", (0, 0), (1, 1), 1.0, 1, 0), costs))

    def test_canonicalize_keeps_capacity_range_and_construction_differences(self):
        first = lo.Piece(1, "kDenseWheat", 1, 1, frozenset({(0, 0)}), True, 0.0, 1.0, 14.0)
        before, target = {1: (0, 0), 2: (3, 0)}, {1: (3, 0), 2: (0, 0)}
        for second in (replace(first, id=2, cap=1.0), replace(first, id=2, range=2.0),
                       replace(first, id=2, unfinished=True)):
            with self.subTest(second=second):
                self.assertEqual(lo.canonicalize({1: first, 2: second}, before, target), target)

    def test_active_overlapping_producers_are_not_all_demolition_candidates(self):
        farms = []
        for i, (x, y) in enumerate(((2.5, 2.5), (4.5, 2.5), (2.5, 4.5)), 1):
            row = building(i, "kIdleFarm", x, y, worker=i, lvl=0, state="kNormal",
                           in_range={"kDenseWheat": 1})
            row["range"] = 4.0
            farms.append(row)
        base = base_of(farms + [building(4, "kDenseWheat", 4.5, 4.5, cap=14)])
        self.assertEqual(lo.suggest_demolish(base), [])
        missing = base_of([building(1, "kIdleFarm", 2.5, 2.5, worker=-1, state="kNormal")])
        self.assertEqual(lo.suggest_demolish(missing), [])
        missing["buildings"][0]["in_range"] = {"kDenseWheat": 0}
        self.assertEqual([r[0] for r in lo.suggest_demolish(missing)], [1])

    def test_circle_guide_report_uses_same_coverage_as_constraints(self):
        grid = Grid(0, 0, 1, {(x, y) for x in range(5) for y in range(5)})
        pieces = {1: lo.Piece(1, "kVeteranHut", 1, 1, frozenset({(0, 0)}), True, 1.2, 1.0),
                  2: lo.Piece(2, "kCozyHome", 1, 1, frozenset({(0, 0)}), True, 0.0, 1.0)}
        origins = {1: (1, 1), 2: (2, 2)}
        scorer = lo.Scorer(pieces, set(), {"veteranhut", "cozyhome"})
        with mock.patch.object(lo, "RANGE_SHAPE", "circle"):
            self.assertEqual(covered_members(grid, pieces, origins, hub_groups(pieces, scorer), 0.0), {1: set()})
            self.assertIn("0/1", guide_report(grid, pieces, origins, scorer)[0])


class FinalStateJobTest(unittest.TestCase):
    def test_fractional_sweep_limits_never_round_outward(self):
        observed = []

        def rank(world, buildings, team, duration, need, targets=None, angles=()):
            values = list(angles)
            observed.extend(values)
            return [SimpleNamespace(angle=a, total=[0, 1, 0, 0], build_hits=0, per_building={}) for a in values]

        for native_lib in (None, object()):
            with self.subTest(native=bool(native_lib)), \
                    mock.patch.object(sim_jobs.native, "lib", return_value=native_lib), \
                    mock.patch.object(sim_jobs.hs, "world_from_geo", return_value=object()), \
                    mock.patch.object(sim_jobs.hs, "rank_angles", side_effect=rank), \
                    mock.patch.object(sim_jobs.hs, "run_angle", return_value=([0, 1, 0, 0], [])):
                result = sim_jobs.job_sweep({}, {}, [], 10, 1, lo=36.2, hi=36.8)
                self.assertTrue(result["top"])
        self.assertTrue(observed)
        self.assertTrue(all(36.2 <= a <= 36.8 for a in observed))

    def test_post_access_geometry_drives_effect_and_all_angle_sweeps(self):
        farm = building(1, "kIdleFarm", 1.5, 1.5, worker=0, lvl=0, state="kNormal",
                        in_range={"kDenseWheat": 0})
        farm["range"] = 1.2
        base = base_of([farm, building(2, "kDenseWheat", 5.5, 1.5, cap=14),
                        building(9, "kGunsmith", 6.5, 6.5, state="kScaffold")])
        grid = grid_from_geo(base["geo"])
        pcs, origins = lo.pieces_from_base(base, grid, lo.housing_types())
        full = lo.FullPlan(origins, origins, {i: grid.center(*at, 1, 1) for i, at in origins.items()},
                           0.0, 0.0, {}, {})
        final = moved_base(base, buildings_from_base(base), 1, (4.5, 1.5))
        final_center = (4.5, 1.5)
        seen = []

        def rank(world, buildings, team, duration, need, targets=None, angles=()):
            angles = list(angles)
            self.assertTrue(angles)
            self.assertTrue(all(36.2 <= a <= 36.8 for a in angles))
            shape = next(c for c in world["colliders"] if c["id"] == 1)
            center = tuple(sum(p[k] for p in shape["pts"]) / 4 for k in (0, 1))
            seen.append((targets, center, (buildings[1]["x"], buildings[1]["y"])))
            return [SimpleNamespace(angle=angles[0], total=[0, 3, 0, 0], per_building={9: 5})]

        def best(world, buildings, team, duration, need, angles=()):
            values = list(angles)
            self.assertTrue(all(36.2 <= a <= 36.8 for a in values))
            self.assertEqual(buildings[2]["res"], 14)
            return [(values[0], [0, 3, 0, 0])]

        with mock.patch.object(lo, "optimize", return_value=full) as optimize, \
                mock.patch("src.engine.layout.plan_access", return_value=([Move(1, final_center, 5, "경로", target=9)], final, set())), \
                mock.patch.object(sim_jobs.hs, "world_from_geo", side_effect=lambda geo, _: geo), \
                mock.patch.object(sim_jobs.hs, "rank_angles", side_effect=rank), \
                mock.patch.object(sim_jobs.hs, "best_angles", side_effect=best):
            plan, sweeps = sim_jobs.job_layout(base, [{"type": "kTest"}], 10, [], {9: 10}, aim_limits=(36.2, 36.8))
        self.assertIsNone(optimize.call_args.args[1])         # 미검증 채집량을 재배치 점수로 쓰지 않는다.
        self.assertIn("continuous_pickup_approximation", plan.model_limitations)
        self.assertEqual(plan.harvest_before, [0, 3, 0, 0])   # 표시용 참고 계산은 현재 가득 찬 타일에서 별도 수행.
        self.assertEqual(plan.evaluated_base, final)
        self.assertEqual(plan.final[1], final_center)
        self.assertEqual(plan.detail_after["kIdleFarm"], 1)
        self.assertGreater(plan.score_after, plan.score_before)
        self.assertNotIn(1, [row[0] for row in plan.demolish])  # 생산 자리로 옮기는 농장을 동시에 철거하라고 하지 않는다.
        final_sweeps = [entry for entry in seen if entry[0] is None]
        self.assertEqual(len(final_sweeps), 3)
        self.assertTrue(all(abs(entry[1][0] - final_center[0]) < 1e-6 and entry[2] == final_center for entry in final_sweeps))
        self.assertEqual(set(sweeps), {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
