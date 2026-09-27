"""채집 사건의 시간 순서·충돌 의미를 독립된 작은 기지로 확인한다."""
import copy
import unittest
from unittest.mock import patch

from src.engine import harvest_sim as hs, native
from src.engine.harvest import layout_key, unfinished_buildings
from src.engine.spa import spa_advice


def box(bid, x, y=1):
    return hs.Shape(bid, "box", pts=[(x, y - .5), (x + 1, y - .5),
                                     (x + 1, y + .5), (x, y + .5)])


def world(shapes):
    return hs.World(0, 100, 0, 100, shapes, (1, 1), radius=0)


def tile(bid, kind, resource, amount=3, **extra):
    held = [0, 0, 0, 0]
    held[resource] = amount
    return dict(id=bid, type=kind, res=amount, held=held, can_harvest=True, **extra)


class HarvestEventTest(unittest.TestCase):
    def implementations(self):
        yield hs.simulate_team_py
        if native.lib() is not None:
            yield hs.simulate_team

    def run_case(self, shapes, buildings, workers, duration):
        results = []
        for fn in self.implementations():
            counts = {}
            result = fn(world(copy.deepcopy(shapes)), buildings, copy.deepcopy(workers), duration, counts=counts)
            results.append((fn.__name__, *result, counts))
        if len(results) > 1:
            self.assertEqual(results[0][1], results[1][1])
            self.assertEqual(results[0][3], results[1][3])
            for a, b in zip(results[0][2], results[1][2]):
                self.assertEqual(a.gain, b.gain)
                self.assertEqual(len(a.path), len(b.path))
                for p, q in zip(a.path, b.path):
                    for x, y in zip(p, q):
                        self.assertAlmostEqual(x, y, places=10)
        return results

    def test_cannot_harvest_after_deadline(self):
        for duration, expected in ((.1, 0), (.3, 1)):
            for name, total, workers, _ in self.run_case(
                    [box(1, 2)], {1: tile(1, "kDenseWheat", 1, 4)},
                    [hs.Worker(1, 1, 1, 0, 5, 0)], duration):
                self.assertEqual(total[1], expected, name)
                self.assertAlmostEqual(workers[0].t, duration)

    def test_first_contact_wins_even_if_worker_spawned_later(self):
        workers = [hs.Worker(1, 1, 1, 0, 5, 0), hs.Worker(2.5, 1, 1, 0, 5, .1, {"kFasterStone": 2})]
        for name, total, result, counts in self.run_case(
                [box(1, 3)], {1: tile(1, "kBoulder", 3)}, workers, .6):
            self.assertEqual(total[3], 3, name)
            self.assertEqual([w.gain[3] for w in result], [0, 3], name)
            self.assertEqual([w.dx for w in result], [1, -1], name)
            self.assertAlmostEqual(result[1].path[1][2], .2)
            self.assertEqual(counts, {1: 1})

    def test_simultaneous_independent_hits_are_both_processed(self):
        workers = [hs.Worker(1, 1, 1, 0, 5, 0), hs.Worker(1, 3, 1, 0, 5, 0)]
        buildings = {1: tile(1, "kBoulder", 3), 2: tile(2, "kForest", 2)}
        for name, total, result, counts in self.run_case([box(1, 3), box(2, 3, 3)], buildings, workers, .6):
            self.assertEqual(total, [0, 0, 1, 1], name)
            self.assertEqual(counts, {1: 1, 2: 1}, name)
            self.assertEqual([w.dx for w in result], [-1, -1], name)

    def test_rocky_hill_is_a_house_and_empty_boulder_is_passable(self):
        for kind, expected in (("kRockyHill", {1: 1}), ("kBoulder", {})):
            building = {1: dict(id=1, type=kind, res=0, can_harvest=False, state="kNormal")}
            for name, _, _, counts in self.run_case([box(1, 3)], building, [hs.Worker(1, 1, 1, 0, 5, 0)], .6):
                self.assertEqual(counts, expected, (name, kind))

    def test_construction_state_does_not_erase_game_reported_harvestable_stock(self):
        for kind in ("kDenseWheat", "kWheatField", "kForest", "kBoulder"):
            for state in ("kScaffold", "kUpgrading"):
                building = {1: dict(id=1, type=kind, res=4, can_harvest=True, state=state)}
                for name, total, _, counts in self.run_case(
                        [box(1, 3)], building, [hs.Worker(1, 1, 1, 0, 5, 0)], .6):
                    resource=1 if kind in hs.WHEAT_TYPES else 2 if kind=='kForest' else 3
                    expected=[0,0,0,0];expected[resource]=1
                    self.assertEqual(total,expected,(name,kind,state))
                    self.assertEqual(counts,{} if resource==1 else {1:1},(name,kind,state))

    def test_missing_collision_for_one_worker_does_not_end_other_workers(self):
        workers = [hs.Worker(-10, 1, -1, 0, 5, 0), hs.Worker(1, 1, 1, 0, 5, 0)]
        for name, total, _, counts in self.run_case([box(1, 3)], {1: tile(1, "kBoulder", 3)}, workers, .6):
            self.assertEqual(total[3], 1, name)
            self.assertEqual(counts, {1: 1}, name)

    def test_worker_launched_after_harvest_has_no_yield(self):
        for name, total, _, counts in self.run_case(
                [box(1, 3)], {1: tile(1, "kBoulder", 3)}, [hs.Worker(1, 1, 1, 0, 5, 1)], .6):
            self.assertEqual(total, [0, 0, 0, 0], name)
            self.assertEqual(counts, {}, name)

    def test_raw_resource_bonus_is_preserved_without_assuming_its_mapping(self):
        for kind, resource, upgrade in (("kDenseWheat", 1, "kFasterWheat"), ("kForest", 2, "kFasterWood")):
            worker = hs.Worker(1, 1, 1, 0, 5, 0, {upgrade: 1}, harvest_bonus={upgrade: 2})
            for name, total, _, _ in self.run_case([box(1, 3)], {1: tile(1, kind, resource, 8)}, [worker], .8):
                self.assertEqual(total[resource], 2, (name, kind))
            limits = hs.model_limitations([{"upgrades": {upgrade: 1}, "harvest_bonus": {upgrade: 2}}],
                                         {1: tile(1, kind, resource, 8)})
            self.assertIn("continuous_pickup_approximation", limits)
            self.assertNotIn("unsupported_harvest_upgrade:" + upgrade, limits)

    def test_game_speed_percent_is_applied_to_actual_base_speed(self):
        w = world([box(1, 8)])
        w.worker_speed, w.worker_speed_mult = 10, 2
        team = hs.team_from_chars([{"type": "kTest", "harvest": {"kHarvestSpeed": 1},
                                    "harvest_bonus": {"kHarvestSpeed": 50}}])
        _, result = hs.run_angle(w, {1: {"id": 1, "type": "kHome"}}, team, 0, 1)
        self.assertAlmostEqual(result[0].path[1][2], 7 / 15)

    def test_build_points_and_collision_counts_are_separate(self):
        building = {1: dict(id=1, type="kIdleFarm", state="kScaffold", can_harvest=False)}
        for fn in self.implementations():
            counts, points = {}, {}
            worker = hs.Worker(1, 1, 1, 0, 5, 0, {"kMoreBuildPts": 1}, harvest_bonus={"kMoreBuildPts": 4})
            fn(world([box(1, 3)]), building, [worker], .6, counts=counts, build_points=points)
            self.assertEqual(counts, {1: 1}, fn.__name__)
            self.assertEqual(points, {1: 5}, fn.__name__)


class HarvestAdviceUnitsTest(unittest.TestCase):
    def test_angle_rank_prefers_five_build_points_from_one_hit_over_three_hits(self):
        """물리의 강화 점수(위 검사)와 추천 순위 연결을 각각 검증한다."""
        buildings = {1: {"id": 1, "type": "kIdleFarm", "state": "kScaffold"}}

        def measured(_world, _buildings, _team, angle, _duration, counts, points):
            counts[1], points[1] = (1, 5) if angle == 30 else (3, 3)
            return [0, 0, 0, 0], []

        with patch.object(hs, "run_angle", side_effect=measured):
            ranked = hs.rank_angles(world([]), buildings, [], 1, 1, {1: 10}, [30, 60])
        self.assertEqual([r.angle for r in ranked], [30, 60])
        self.assertEqual([r.build_hits for r in ranked], [1, 3])
        self.assertEqual([r.build_points for r in ranked], [5, 3])
        self.assertEqual(ranked[0].per_building_points, {1: 5})
        self.assertGreater(hs.angle_score(ranked[0], 1), hs.angle_score(ranked[1], 1))

    def test_construction_priority_caps_points_at_remaining_work(self):
        def measured(_world, _buildings, _team, angle, _duration, counts, points):
            counts[1], points[1] = (1, 5) if angle == 30 else (3, 3)
            return [0, 0 if angle == 30 else 1, 0, 0], []

        with patch.object(hs, "run_angle", side_effect=measured):
            ranked = hs.rank_angles(world([]), {1: {"state": "kScaffold"}}, [], 1, 1, {1: 2}, [30, 60])
        self.assertEqual([r.angle for r in ranked], [60, 30])
        self.assertEqual([r.build_points for r in ranked], [2, 2])

    def test_legacy_targets_use_one_point_per_hit_without_exact_claim(self):
        def measured(_world, _buildings, _team, _angle, _duration, counts, _points):
            counts[1] = 3
            return [0, 0, 0, 0], []

        with patch.object(hs, "run_angle", side_effect=measured):
            result = hs.rank_angles(world([]), {1: {"id": 1}}, [], 1, 1, {1: 10}, [30])[0]
        self.assertEqual(result.build_points, 3)
        self.assertEqual(result.per_building, {1: 3})
        self.assertEqual(hs.angle_score(hs.AngleResult(30, [0, 0, 0, 0], 2, {1: 2}), 1), 200)

    def test_unverified_game_effects_are_reported_instead_of_silently_supported(self):
        b = {1: tile(1, "kBoulder", 3)}
        team = [{"upgrades": {"kHarvestSpeed": 1, "kMoreBuildPts": 1, "kWoodTime": 1}}]
        self.assertEqual(hs.model_limitations(team, b), ["continuous_pickup_approximation", "missing_build_point_bonus", "missing_harvest_speed_bonus"])
        team = [{"upgrades": {"kFasterStone": 1}, "harvest_bonus": {"kFasterStone": 2}}]
        self.assertEqual(hs.model_limitations(team, b),
                         ["continuous_pickup_approximation"])

    def test_game_construction_points_are_not_exact_bounce_counts(self):
        b = dict(id=1, type="kIdleFarm", state="kScaffold", upg_tgt=10, upg_pts=3)
        u = unfinished_buildings({"buildings": [b]}, None)[0]
        self.assertEqual(u.remaining_points, 7)
        self.assertTrue(u.points_exact)
        self.assertFalse(u.exact)
        u = unfinished_buildings({"buildings": [{k: v for k, v in b.items() if k != "upg_pts"}]}, None)[0]
        self.assertIsNone(u.remaining_points)
        self.assertFalse(u.points_exact)

    def test_rotated_building_has_different_layout_fingerprint(self):
        b = dict(type="kLShape", x=1, y=2, rot=0)
        self.assertNotEqual(layout_key({"buildings": [b]}), layout_key({"buildings": [dict(b, rot=1)]}))
        self.assertEqual(layout_key({"buildings": [b]}), layout_key({"buildings": [dict(b, rot=4)]}))

    def test_spa_only_addresses_resources_it_can_provide(self):
        rows = [{"gain": [0, 10, 0, 0], "layout": "A"}]
        self.assertEqual(spa_advice({"built": True, "cost": 50}, rows, "A", {"돌": 1}).verdict, "loss")
        self.assertEqual(spa_advice({"built": True, "cost": 50}, rows, "A", {"밀": 1}).verdict, "resources")
