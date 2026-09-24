"""채집 계산 작업(별도 프로세스)과 미완성 건물 우선 추천."""
import json
import os
import pickle
import time
import unittest

from src.engine import harvest_sim as hs
from src.engine import sim_jobs
from src.engine.harvest import unfinished_buildings
from src.tracking.meta_state import BuildingState, MetaState

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json")


def load():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def team_of(fx):
    return [{"type": w["char"], "upgrades": w["upgrades"], "speed": w["speed"]} for w in fx["workers"]]


class UnfinishedTest(unittest.TestCase):
    def test_states_from_meta_when_base_has_none(self):
        base = {"buildings": [{"id": 1, "type": "kForest", "upgrade_pct": 0},
                              {"id": 2, "type": "kRockyHill", "upgrade_pct": 0.95, "sx": 10, "sy": 20},
                              {"id": 3, "type": "kSchoolhouse", "upgrade_pct": 0}]}
        meta = MetaState(buildings=[BuildingState("kForest", 1, "kNormal"), BuildingState("kRockyHill", 0, "kScaffold"),
                                    BuildingState("kSchoolhouse", 0, "kUpgrading")])
        u = unfinished_buildings(base, meta)
        self.assertEqual([x.id for x in u], [2, 3])        # 완성에 가까운 것부터
        self.assertEqual(u[0].hits_left, 1)
        self.assertFalse(u[0].exact)

    def test_exact_hits_from_plugin(self):
        base = {"buildings": [{"id": 5, "type": "kClinic", "state": "kUpgrading", "upg_pts": 3, "upg_tgt": 8}]}
        u = unfinished_buildings(base, None)
        self.assertEqual((u[0].hits_left, u[0].exact), (5, True))

    def test_meta_misaligned_is_ignored(self):
        base = {"buildings": [{"id": 1, "type": "kForest"}]}
        meta = MetaState(buildings=[BuildingState("kBoulder", 0, "kScaffold")])
        self.assertEqual(unfinished_buildings(base, meta), [])


class RankTest(unittest.TestCase):
    def test_build_targets_come_first(self):
        fx = load()
        world = hs.world_from_geo(fx["geo"], 0.03)
        blds = {b["id"]: b for b in fx["buildings_before"]}
        team = team_of(fx)
        angles = range(20, 161, 20)
        plain = hs.rank_angles(world, blds, team, fx["duration"], 1, None, angles)
        # 총포상(6)은 여러 각도에서 맞는다 — 그걸 미완성으로 치면 맞히는 각도가 1위
        hit = hs.rank_angles(world, blds, team, fx["duration"], 1, {6: 3}, angles)
        best_hits = max(r.build_hits for r in hit)
        self.assertGreater(best_hits, 0)
        self.assertEqual(hit[0].build_hits, best_hits)
        self.assertEqual(plain[0].build_hits, 0)            # 목표가 없으면 세지 않는다
        self.assertTrue(all(v <= 3 for r in hit for v in r.per_building.values()))   # 필요한 만큼까지만

    def test_enclosed_buildings_get_a_path(self):
        """실제 기지: 강화 공사 중인 학교(11)·영사관(12)이 사방이 막혀 0% — 어떤 각도로도 닿지 않는다.
        옆 건물 하나를 빈 자리로 옮기면 닿게 되는 옮기기를 찾아야 한다."""
        fx = load()
        blds = {b["id"]: b for b in fx["buildings_before"]}
        team = team_of(fx)
        sw = sim_jobs.job_sweep(fx["geo"], blds, team, fx["duration"], 1, {11: 10, 12: 10})
        self.assertEqual(sw["reach"], {11: 0, 12: 0})
        plan, _ = sim_jobs.job_layout({"buildings": fx["buildings_before"], "geo": fx["geo"]}, team,
                                      fx["duration"], [], {11: 10, 12: 10}, seconds=2.0)
        # 최적 배치(+길 열기)를 적용하면 두 건물 모두 어떤 각도로 닿는다
        from src.engine.layout import move_geo, buildings_from_base
        geo = fx["geo"]
        cur = buildings_from_base({"buildings": fx["buildings_before"]})
        for i, (x, y) in plan.final.items():
            if (cur[i].x, cur[i].y) != (x, y):
                geo = move_geo(geo, cur, i, (x, y))
        sw2 = sim_jobs.job_sweep(geo, blds, team, fx["duration"], 1, {11: 10, 12: 10})
        self.assertTrue(all(v > 0 for v in sw2["reach"].values()), sw2["reach"])
        self.assertTrue(plan.swaps)

    def test_jobs_are_picklable_and_match_direct(self):
        fx = load()
        blds = {b["id"]: b for b in fx["buildings_before"]}
        team = team_of(fx)
        r = sim_jobs.job_now(fx["geo"], blds, team, 48, fx["duration"], {11: 5})
        total, _ = hs.run_angle(hs.world_from_geo(fx["geo"], 0.03), blds, team, 48, fx["duration"])
        self.assertEqual(r["total"], total)
        self.assertTrue(r["path"])
        pickle.loads(pickle.dumps(r))
        plan = sim_jobs.job_layout({"buildings": fx["buildings_before"], "geo": fx["geo"]}, [], 16.0, [], seconds=0.5)
        pickle.loads(pickle.dumps(plan))


class SimWorkerTest(unittest.TestCase):
    def test_process_worker_returns_latest_only(self):
        from PyQt6.QtCore import QCoreApplication
        from src.services.sim_worker import SimWorker
        app = QCoreApplication.instance() or QCoreApplication([])
        fx = load()
        blds = {b["id"]: b for b in fx["buildings_before"]}
        team = team_of(fx)
        w = SimWorker()
        got = []
        w.done.connect(lambda ch, key, res: got.append((ch, key, res)))
        try:
            for ang in (30, 40, 50, 60):          # 빠르게 여러 번: 첫 요청과 마지막 요청만 계산
                w.submit("now", ang, sim_jobs.job_now, fx["geo"], blds, team, ang, fx["duration"], None)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not (got and got[-1][1] == 60):
                app.processEvents()
                time.sleep(0.01)
        finally:
            w.shutdown()
        keys = [k for _, k, _ in got]
        self.assertEqual(keys, [30, 60])
        self.assertEqual(got[-1][2]["angle"], 60)


if __name__ == "__main__":
    unittest.main()
