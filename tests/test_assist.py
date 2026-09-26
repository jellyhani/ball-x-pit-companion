"""게임 보조 기능 검사 (합성 입력): 연동 1.2 필드, 실측 피해·내 기록 규칙, 새로고침 확률,
원정 계속 판단, 기지 조언, 런 기록, 연동 모드 설치."""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from src.domain import CardLabel, ChoicePool, RunProgress
from src.engine.base_advisor import suggest
from src.engine.deck_plan import build_plan
from src.engine.expedition import advise
from src.engine.recommender import Recommender
from src.services import mod_installer as mi
from src.tracking.bridge_adapter import convert
from src.tracking.meta_state import parse_meta
from src.tracking.run_history import RunRecorder, describe, item_summary
from src.tracking.run_state import RunState
from tests.helpers import card, game_data, session, slots
from tests.test_bridge import LEVELUP

SNAP_12 = copy.deepcopy(LEVELUP)
SNAP_12["plugin"] = "1.2.0"
SNAP_12["battle"].update({"endless": True, "endless_start_turn": 190, "revives": 0, "revives_max": 1,
                          "completed_level": True})
SNAP_12["battle"]["balls"][0].update({"dmg": 9000, "kills": 40})
SNAP_12["battle"]["balls"][1].update({"dmg": 1000, "kills": 5})
SNAP_12["levelup"]["pool"] = {"new_balls": ["kLaserHorz", "kStone", "kWind"], "ball_upgrades": ["kFreeze", "kHeavy"],
                              "new_passives": ["kEtherealCloak"], "passive_upgrades": [], "prev": [],
                              "num_choices": 3, "banishing": False}
SNAP_12["game_over"] = {"completed": True, "endless_btn": True}

META = {"resources": [500, 30, 40, 10], "day": 3, "battles": 12, "boss_waves": 4,
        "bonuses": {"banishes": 2, "free_rerolls": 0, "revives": 1, "choices": 3, "ball_slots": 4,
                    "passive_slots": 4, "endless": True},
        "buildings": [{"type": "kFarm", "lvl": 1, "state": "kNormal", "can_upgrade": True,
                       "upgrade_cost": [100, 0, 20, 0]}],
        "blueprints": [{"type": "kExorcist", "slug": "exorcist", "cat": "kWarfare", "cost": [300, 0, 60, 20]},
                       {"type": "kBank", "slug": "bank", "cat": "kEconomy", "cost": [200, 0, 10, 0]}],
        "ball_stats": {"kBurn": {"obtained": 10, "completed": 5, "damage": 900000, "launches": 1},
                       "kStone": {"obtained": 6, "completed": 1, "damage": 60000},
                       "kWind": {"obtained": 5, "completed": 2, "damage": 200000},
                       "kFreeze": {"obtained": 4, "completed": 2, "damage": 300000},
                       "kHeavy": {"obtained": 7, "completed": 3, "damage": 150000},
                       "kBleed": {"obtained": 3, "completed": 1, "damage": 100000}},
        "passive_stats": {}, "chars": [{"type": "kRecaller", "lvl": 3, "battles": 4}],
        "levels": [{"type": "kSnowy", "done": True, "attempts": 5, "best_endless": 120}]}


class BridgeFieldsTest(unittest.TestCase):
    def test_v12_fields(self):
        st = convert(SNAP_12, game_data())
        self.assertEqual(st.plugin, "1.2.0")
        self.assertEqual(st.damage, {"ball:freeze": 9000, "ball:heavy": 1000})
        self.assertTrue(st.game_over.completed and st.game_over.endless_button)
        p = st.observation.progress
        self.assertEqual((p.endless_start_turn, p.revives_left), (190, 1))
        pool = st.observation.pool
        self.assertEqual(pool.new_balls, ("ball:laserhorz", "ball:stone", "ball:wind"))
        self.assertEqual(pool.ball_upgrades, ("ball:freeze", "ball:heavy"))

    def test_old_plugin_has_no_new_fields(self):
        st = convert(LEVELUP, game_data())
        self.assertIsNone(st.observation.pool)
        self.assertIsNone(st.game_over)
        self.assertEqual(st.damage, {})


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        self.d = game_data()
        self.rec = Recommender(self.d)
        self.run = RunState()
        self.run.start_run()
        self.run.apply_inventory(slots(("ball:freeze", 2), ("ball:heavy", 1), ("ball:lightningbug", 1)), self.d)

    def ids(self, ev):
        return [r.rule_id for r in ev.reasons + ev.warnings]

    def test_run_damage_share(self):
        self.run.damage = {"ball:freeze": 8000, "ball:heavy": 1500, "ball:lightningbug": 300}
        s = session([card(0, "ball:freeze", CardLabel.UPGRADE, 3), card(1, "ball:lightningbug", CardLabel.UPGRADE, 2)])
        r = self.rec.recommend(s, self.run)
        self.assertIn("run_carry", self.ids(r.evals[0]))
        self.assertIn("1위", next(x.text for x in r.evals[0].reasons if x.rule_id == "run_carry"))
        self.assertIn("run_weak", self.ids(r.evals[1]))

    def test_little_damage_makes_no_claim(self):
        self.run.damage = {"ball:freeze": 800, "ball:heavy": 100}     # 표본이 너무 적음
        s = session([card(0, "ball:freeze", CardLabel.UPGRADE, 3)])
        self.assertNotIn("run_carry", self.ids(self.rec.recommend(s, self.run).evals[0]))

    def test_my_record(self):
        self.rec.meta = parse_meta(META, self.d)
        s = session([card(0, "ball:burn"), card(1, "ball:stone")])
        r = self.rec.recommend(s, self.run)
        self.assertIn("my_record_top", self.ids(r.evals[0]))     # 화상: 런당 피해 1위
        self.assertIn("my_record_low", self.ids(r.evals[1]))     # 돌: 최하위

    def test_reroll_odds(self):
        plan = build_plan(self.run, self.d, None)
        pool = ChoicePool(new_balls=("ball:stone", "ball:wind", "ball:warp"), ball_upgrades=("ball:freeze",),
                          num_choices=3)
        s = session([card(0, "ball:warp")], pool=pool)
        p, total, k = self.rec.reroll_odds(s, plan, self.run)
        self.assertEqual(total, 3)     # 지금 선택지(warp)는 새로고침하면 빠진다
        good = sum(1 for i in ("ball:stone", "ball:wind") if i in plan.wanted) + (1 if "ball:freeze" in plan.core else 0)
        self.assertEqual(k, good)
        self.assertEqual(p, 1.0 if k else 0.0)   # 후보 3장 = 뽑는 수 3장

    def test_reroll_without_targets_is_kept(self):
        plan = build_plan(self.run, self.d, None)
        dead = [i for i, it in self.d.items.items() if it.kind == "ball" and i not in plan.wanted
                and i not in self.run.owned and not self.d.recipes_for(i)
                and not any(o in self.run.owned for r in self.d.recipes_using(i) for o in r.ingredients)][:4]
        pool = ChoicePool(new_balls=tuple(dead), num_choices=3)
        cards = [card(0, dead[0])]            # 연결 없는 새 볼 한 장 → 약한 선택지
        r = self.rec.recommend(session(cards, pool=pool, gold=500, reroll_cost=10), self.run)
        self.assertEqual(r.reroll_odds[2], 0)
        self.assertEqual(r.reroll_status, "keep")
        self.assertIn("후보에 없음", r.reroll_text)


class VerdictTest(unittest.TestCase):
    def test_card_verdicts(self):
        from src.engine.recommender import card_verdict
        d = game_data()
        rec = Recommender(d)
        run = RunState()
        run.start_run()
        run.apply_inventory(slots(("ball:bleed", 2), ("ball:heavy", 3)), d)
        s = session([card(0, "ball:stone"), card(1, "ball:bleed", CardLabel.UPGRADE, 3), card(2, None)])
        r = rec.recommend(s, run)
        v = [card_verdict(r, e) for e in r.evals]
        self.assertEqual(v[1], "best")
        self.assertEqual(v[2], "unknown")
        self.assertIn(v[0], ("alt", "skip"))
        # 판단 보류면 초록 '비슷함'은 섞지 않고, 그나마 나은 하나만 '무난'(pick) 으로 짚는다
        run2 = RunState()
        run2.start_run()
        run2.apply_inventory(slots(("ball:burn", 1)), d)
        s2 = session([card(i, x) for i, x in enumerate(("ball:lightningbug", "ball:warp", "ball:petrify"))])
        r2 = rec.recommend(s2, run2)
        self.assertEqual(r2.status, "hold")
        kinds = [card_verdict(r2, e) for e in r2.evals]
        self.assertTrue(all(k in ("neutral", "banish", "pick") for k in kinds), kinds)
        self.assertLessEqual(kinds.count("pick"), 1)


class ExpeditionTest(unittest.TestCase):
    def setUp(self):
        self.d = game_data()
        self.run = RunState()
        self.run.start_run()

    def test_verdicts(self):
        from src.domain import InventorySlot
        inv = (InventorySlot(0, (0, 0, 0, 0), True, "ball:sandstorm", 3, at_max=True),
               InventorySlot(1, (0, 0, 0, 0), True, "ball:vampirelord", 3, at_max=True),
               InventorySlot(2, (0, 0, 0, 0), True, "ball:freeze", 3, at_max=True))
        self.run.apply_inventory(inv, self.d)
        good = advise(self.run, self.d, RunProgress(health=90, max_health=100, revives_left=1))
        self.assertEqual(good.verdict, "continue")
        bad = advise(self.run, self.d, RunProgress(health=20, max_health=100))
        self.assertNotEqual(bad.verdict, "continue")
        self.run.apply_inventory(slots(("ball:freeze", 1)), self.d)
        weak = advise(self.run, self.d, RunProgress(health=20, max_health=100))
        self.assertEqual(weak.verdict, "return")
        self.assertTrue(any("확인하지 못했" in c for c in weak.cautions))


class BaseTest(unittest.TestCase):
    def test_retired_gold_mine_never_requests_construction_resources(self):
        """철거 예정 금광은 건설·강화·완성 후보에서 빼고 다른 건물 비용은 보존한다."""
        d = game_data()
        raw = {"resources": [1, 2, 3, 4],
               "buildings": [
                   {"type": "kGoldMine", "state": "kScaffold"},
                   {"type": "kGoldMine", "state": "kNormal", "can_upgrade": True,
                    "upgrade_cost": [900, 800, 700, 600]},
                   {"type": "kIdleFarm", "state": "kScaffold"},
                   {"type": "kBank", "state": "kNormal", "can_upgrade": True,
                    "upgrade_cost": [10, 20, 30, 40]}],
               "blueprints": [
                   {"type": "kGoldMine", "cost": [999, 888, 777, 666]},
                   {"type": "kBank", "cost": [100, 200, 300, 400]}]}
        m = parse_meta(raw, d)
        out = suggest(m, d)
        self.assertEqual({(s.kind, s.type) for s in out},
                         {("finish", "kIdleFarm"), ("build", "kBank"), ("upgrade", "kBank")})
        self.assertEqual(next(s.cost for s in out if s.kind == "build"), (100, 200, 300, 400))
        self.assertEqual(m.resources, (1, 2, 3, 4))
        self.assertEqual(sum(b.type == "kGoldMine" for b in m.buildings), 2)

    def test_meta_and_suggestions(self):
        d = game_data()
        m = parse_meta(META, d)
        self.assertEqual(m.resources, (500, 30, 40, 10))
        self.assertEqual(m.records["ball:burn"].damage_per_run, 90000)
        self.assertEqual(m.damage_rank("ball:burn"), (1, 6))
        sg = suggest(m, d)
        self.assertEqual(sg[0].type, "kExorcist")          # 런에 직접 영향 (삭제) → 맨 앞
        self.assertFalse(sg[0].affordable)                 # 나무 60 필요, 40 보유
        self.assertIn("나무 20", sg[0].missing_text)
        self.assertEqual(d.building_name("kExorcist"), "엑소시스트")


class HistoryTest(unittest.TestCase):
    def test_record_roundtrip(self):
        d = game_data()
        tmp = tempfile.mkdtemp()
        try:
            rr = RunRecorder(os.path.join(tmp, "runs.jsonl"))
            run = RunState()
            run.start_run()
            rr.start()
            run.apply_character("char:recaller")
            run.apply_inventory(slots(("ball:sandstorm", 2), ("ball:freeze", 1)), d)
            run.picks = ["ball:freeze", "ball:sandstorm"]
            run.damage = {"ball:sandstorm": 7000, "ball:freeze": 3000}
            rr.update(run, RunProgress(turn=300, endless=True, endless_start_turn=190, level_name="kSnowy"),
                      completed=True, game_over=True)
            rec = rr.finish()
            self.assertEqual((rec.result, rec.endless_turns), ("보스 격퇴", 110))
            loaded = rr.load()
            self.assertEqual(len(loaded), 1)
            title, sub = describe(loaded[0], d)
            self.assertIn("보스 격퇴", title)
            self.assertIn("주력 모래폭풍 70%", sub)
            self.assertEqual(item_summary(loaded)["ball:freeze"], (1, 1))
            rr.start()
            self.assertIsNone(rr.finish())   # 선택 없이 끝난 런은 남기지 않음
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class InstallerTest(unittest.TestCase):
    def test_vendor_plugin_version_matches(self):
        self.assertEqual(mi.file_version(mi.PLUGIN_DLL), mi.PLUGIN_VERSION)

    def test_plugin_source_version_matches(self):
        import re
        src = open(os.path.join(mi.ROOT, "tools", "bepinex", "BallxPitBridge", "Plugin.cs"), encoding="utf-8").read()
        proj = open(os.path.join(mi.ROOT, "tools", "bepinex", "BallxPitBridge", "BallxPitBridge.csproj"),
                    encoding="utf-8").read()
        self.assertEqual(re.search(r'Version = "([\d.]+)"', src).group(1), mi.PLUGIN_VERSION)
        self.assertEqual(re.search(r"<Version>([\d.]+)</Version>", proj).group(1), mi.PLUGIN_VERSION)

    def test_install_into_fake_game_dir(self):
        from src.services import mod_installer as _mi
        if not any(os.path.exists(p) for p in (_mi.BEPINEX_ZIP, _mi.BEPINEX_CACHE)):
            self.skipTest("BepInEx 압축 파일 없음 (공개판은 설치 때 공식 서버에서 받음)")
        tmp = tempfile.mkdtemp()
        try:
            game = os.path.join(tmp, "BALLxPIT")
            os.makedirs(game)
            open(os.path.join(game, "Balls.exe"), "wb").close()
            with mock.patch.object(mi, "game_running", return_value=False), \
                    mock.patch.object(mi, "MANIFEST", os.path.join(tmp, "installed.txt")), \
                    mock.patch.object(mi, "STATE_PATH", os.path.join(tmp, "state.json")):
                st = mi.check(game)
                self.assertTrue(st.needs_install)
                self.assertEqual(st.summary, "게임 연동 모드(BepInEx) 없음")
                new = mi.install(st, say=lambda m: None)
                self.assertTrue(new.ok, new.summary)
                self.assertTrue(os.path.exists(os.path.join(game, "winhttp.dll")))
                with open(os.path.join(tmp, "installed.txt"), encoding="utf-8") as f:
                    self.assertGreater(len(f.read().splitlines()), 200)
                # 이미 최신이면 아무것도 하지 않는다
                self.assertFalse(mi.check(game).needs_install)
            with mock.patch.object(mi, "game_running", return_value=True):
                with self.assertRaises(mi.InstallError):
                    mi.install(mi.check(game))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
