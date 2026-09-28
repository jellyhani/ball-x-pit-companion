"""네이티브 계산 모듈이 파이썬 구현과 같은 결과를 내는지 (실제 기지 기록으로)."""
import copy
import json
import math
import os
import unittest

from src.engine import harvest_sim as hs
from src.engine import native

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


@unittest.skipUnless(native.lib() is not None, "네이티브 모듈 없음 (native\build.ps1)")
class NativeSameAsPythonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8") as f:
            cls.fx = json.load(f)
        cls.blds = {b["id"]: b for b in cls.fx["buildings_before"]}
        full = {i: (dict(b, res=int(b.get("cap") or 1), can_harvest=True) if b.get("type") in
                    ("kWheatField", "kForest", "kBoulder", "kDenseWheat", "kGrandTree", "kGraniteSlab") else b)
                for i, b in cls.blds.items()}
        cls.full = full
        cls.team = [{"type": w["char"], "upgrades": w["upgrades"], "speed": w["speed"]} for w in cls.fx["workers"]]

    def workers(self, angle):
        lx, ly = self.world.launcher
        dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        return [hs.Worker(lx, ly, dx, dy, m["speed"], i * hs.LAUNCH_GAP, dict(m["upgrades"]))
                for i, m in enumerate(self.team)]

    def compare(self, blds, angles, radius=0.03, dur=16.0):
        self.world = hs.world_from_geo(self.fx["geo"], radius)
        for a in angles:
            ca, cb = {}, {}
            ta, wa = hs.simulate_team(self.world, blds, self.workers(a), dur, counts=ca)
            tb, wb = hs.simulate_team_py(self.world, copy.deepcopy(blds), self.workers(a), dur, counts=cb)
            self.assertEqual(ta, tb, a)
            self.assertEqual(ca, cb, a)
            for x, y in zip(wa, wb):
                self.assertEqual(x.gain, y.gain, a)
                self.assertEqual(len(x.path), len(y.path), a)
                for p, q in zip(x.path, y.path):
                    for u, v in zip(p, q):
                        self.assertAlmostEqual(u, v, places=9)

    def test_same_as_python_many_angles(self):
        self.compare(self.blds, range(10, 171, 4))

    def test_same_with_full_tiles_and_other_radius(self):
        self.compare(self.full, range(15, 166, 10), radius=0.07, dur=20.0)

    def test_pierce_upgrades(self):
        self.team = [dict(m, upgrades={"kPierceStone": 1, "kPierceWood": 1}) for m in self.team[:3]] + \
                    [dict(m, upgrades={"kPierceBuildings": 1}) for m in self.team[3:5]]
        self.compare(self.full, range(20, 161, 20))

    def test_dynamic_resources_match_python_with_regeneration_and_piercing(self):
        """실제 형태의 기지에서도 재생 시각마다 충돌·픽업을 같은 순서로 처리한다."""
        buildings = copy.deepcopy(self.full)
        for building in buildings.values():
            if hs.resource_tile(building):
                building.update(task_target_seconds=5, task_seconds=4)
        self.assertTrue(hs.needs_dynamic_simulation(buildings))
        self.team = [dict(member, upgrades={"kPierceWood": 1, "kPierceBuildings": 1})
                     for member in self.team[:4]]
        self.compare(buildings, (25, 48, 80, 130), dur=12.)

    def test_dynamic_query_keeps_shared_shape_contacts_and_wall_priority(self):
        world = hs.World(0, 12, 0, 10, [
            hs.Shape(1, "circle", c=(3, 3), r=.5),
            hs.Shape(1, "circle", c=(3.5, 3), r=.5),
            hs.Shape(2, "circle", c=(6, 3), r=.5),
            hs.Shape(-1, "wall", pts=[(9, 0), (9, 10)]),
        ], (1, 3), task_clock=(.5, 1., 1.))
        buildings = {
            1: dict(type="kWheatField", state="kNormal", res=3, cap=3, can_harvest=True,
                    pickup_enabled=True, raycast_enabled=False, task_seconds=0, task_target_seconds=2),
            2: dict(type="kForest", state="kNormal", res=2, cap=2, can_harvest=True,
                    pickup_enabled=False, raycast_enabled=True, task_seconds=0, task_target_seconds=3),
        }
        for upgrades in ({}, {"kPierceBuildings": 1, "kPierceWood": 1}):
            results = []
            for simulator in (hs.simulate_team, hs.simulate_team_py):
                scene = copy.deepcopy(world)
                counts, collected = {}, {}
                total, workers = simulator(scene, buildings,
                    [hs.Worker(1, 3, 1, 0, 2, 0, upgrades)], 12., counts=counts, collected=collected)
                results.append((total, workers, counts, collected, scene.automatic_gain, scene.model_notes))
            self.assertEqual(results[0], results[1])

    def test_dynamic_simulation_uses_native_query_but_reference_does_not(self):
        from unittest.mock import patch

        world = hs.World(0, 10, 0, 10, [], (1, 1))
        buildings = {1: dict(type="kForest", task_target_seconds=5)}
        with patch.object(native.DynamicShapeQuery, "nearest", return_value=None) as query:
            hs.simulate_team(world, buildings, [hs.Worker(1, 1, 1, 0, 1, 0)], 1.)
            self.assertTrue(query.called)
            query.reset_mock()
            hs.simulate_team_py(world, buildings, [hs.Worker(1, 1, 1, 0, 1, 0)], 1.)
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
