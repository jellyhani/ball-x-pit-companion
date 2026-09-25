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


if __name__ == "__main__":
    unittest.main()
