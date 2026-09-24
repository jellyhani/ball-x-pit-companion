"""추가 보조 기능 검사: 회귀 묶음, 뽑기 보정, 펫 카드, 패시브 평가, 융합 비교, 원정 기록, 지역 계획."""
import copy
import os
import shutil
import sys
import tempfile
import unittest

from src.domain import CardLabel, ChoicePool, FuserCombo, FuserEvo, FuserOptions, InventorySlot
from src.engine.deck_plan import build_plan
from src.engine.expedition import personal_line
from src.engine.fusion import FusionAdvisor
from src.engine.planning import blueprint_targets, level_name, plan_levels
from src.engine.recommender import Recommender
from src.tracking.bridge_adapter import convert
from src.tracking.draw_stats import DrawStats
from src.tracking.meta_state import parse_meta
from src.tracking.run_history import RunRecord, expedition_history
from src.tracking.run_state import RunState
from tests.helpers import card, game_data, session, slots
from tests.test_assist import META
from tests.test_bridge import LEVELUP

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))


class RegressionTest(unittest.TestCase):
    def test_saved_real_snapshots_unchanged(self):
        import regress
        got = regress.replay(regress.load(regress.FIXTURE))
        import json
        with open(regress.EXPECTED, encoding="utf-8") as f:
            expected = json.load(f)
        self.assertGreaterEqual(len(got), 20)
        self.assertEqual(regress.compare(expected, got), [])


class DrawStatsTest(unittest.TestCase):
    def test_weights_need_enough_observations(self):
        tmp = tempfile.mkdtemp()
        try:
            ds = DrawStats(os.path.join(tmp, "draws.jsonl"))
            pool = ChoicePool(new_balls=("ball:stone", "ball:wind", "ball:warp"),
                              ball_upgrades=("ball:burn",), new_passives=("passive:magnet", "passive:armor"),
                              num_choices=3)
            # 새 패시브가 후보의 1/3 인데 한 번도 안 나온다 → 가중치가 낮아져야 한다
            for i in range(15):
                s = session([card(0, "ball:stone"), card(1, "ball:burn", CardLabel.UPGRADE, 2), card(2, "ball:wind")],
                            sid=i, pool=pool)
                ds._last_key = None
                ds.record(s)
            w = ds.weights()
            self.assertIsNotNone(w)
            self.assertLess(w["new_passive"], 0.5)
            self.assertGreater(w["ball_up"], 1.0)
            self.assertIn("관측 15회", ds.summary())
            again = DrawStats(os.path.join(tmp, "draws.jsonl"))
            self.assertEqual(again.count, 15)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_calibrated_odds_label(self):
        d = game_data()
        rec = Recommender(d)
        run = RunState()
        run.start_run()
        run.apply_inventory(slots(("ball:burn", 1)), d)
        plan = build_plan(run, d, None)
        pool = ChoicePool(new_balls=("ball:stone", "ball:earthquake", "ball:warp", "ball:petrify"),
                          new_passives=("passive:magnet", "passive:armor"), num_choices=3)
        s = session([card(0, "ball:lightningbug")], pool=pool)
        p0, total, k = rec.reroll_odds(s, plan, run)
        rec.draw_weights = {"new_ball": 2.0, "ball_up": 1.0, "new_passive": 0.2, "passive_up": 1.0}
        p1, _, _ = rec.reroll_odds(s, plan, run)
        self.assertGreater(k, 0)
        self.assertGreater(p1, p0)     # 목표(새 볼)가 더 잘 나오는 경향이면 확률이 오른다


class PetTest(unittest.TestCase):
    def test_pet_card_named_from_game(self):
        from src.gamedata import load_game_data
        d = load_game_data()      # 공유 데이터에 펫 항목을 남기지 않도록 새로 읽는다
        snap = copy.deepcopy(LEVELUP)
        snap["levelup"]["choices"][1] = {"idx": 1, "kind": "kPetUpgrade", "is_new": True, "equip_idx": -1,
                                         "type": "kRatBabyBall", "name_loc": "쥐: 베이비볼", "desc_loc": "설명",
                                         "rect": [335, 575, 260, 260]}
        st = convert(snap, d)
        pet = [c for c in st.observation.cards if c.item_id and c.item_id.startswith("pet:")]
        self.assertEqual(len(pet), 1)
        self.assertEqual(d.name(pet[0].item_id), "쥐: 베이비볼")
        run = RunState()
        run.start_run()
        s = session(list(st.observation.cards))
        r = Recommender(d).recommend(s, run)
        ev = next(e for e in r.evals if e.card.item_id == pet[0].item_id)
        self.assertTrue(ev.evaluated)
        self.assertFalse(ev.strong)       # 근거 없는 펫이 억지로 1위가 되지 않는다


class PassiveTest(unittest.TestCase):
    def test_passive_damage_and_record(self):
        d = game_data()
        rec = Recommender(d)
        meta = copy.deepcopy(META)
        meta["passive_stats"] = {"kMagnet": {"obtained": 6, "completed": 5}, "kArmor": {"obtained": 5, "completed": 0},
                                 "kThorns": {"obtained": 4, "completed": 2}, "kSneakers": {"obtained": 4, "completed": 2},
                                 "kFeather": {"obtained": 3, "completed": 1}}
        rec.meta = parse_meta(meta, d)
        run = RunState()
        run.start_run()
        run.apply_inventory(slots(("ball:burn", 2), ("passive:thorns", 1)), d)
        run.damage = {"ball:burn": 10000, "passive:thorns": 3000}
        s = session([card(0, "passive:thorns", CardLabel.UPGRADE, 2), card(1, "passive:magnet"), card(2, "passive:armor")])
        r = rec.recommend(s, run)
        ids = [[x.rule_id for x in e.reasons + e.warnings] for e in r.evals]
        self.assertIn("passive_dmg", ids[0])
        self.assertIn("my_record_clear", ids[1])
        self.assertIn("my_record_clear_low", ids[2])


class FusionDataTest(unittest.TestCase):
    def test_combo_gets_run_share_reason(self):
        d = game_data()
        fa = FusionAdvisor(d)
        run = RunState()
        run.start_run()
        inv = (InventorySlot(0, (0, 0, 0, 0), True, "ball:bleed", 3, at_max=True),
               InventorySlot(1, (0, 0, 0, 0), True, "ball:heavy", 3, at_max=True),
               InventorySlot(2, (0, 0, 0, 0), True, "ball:burn", 3, at_max=True),
               InventorySlot(3, (0, 0, 0, 0), True, "ball:freeze", 3, at_max=True))
        run.apply_inventory(inv, d)
        run.damage = {"ball:burn": 50000, "ball:freeze": 40000, "ball:bleed": 2000, "ball:heavy": 1000}
        fz = FuserOptions(evos=(FuserEvo("ball:hemorrhage", 0),),
                          combos=(FuserCombo("ball:burn", "ball:freeze", 2, 3, 30.0, False),))
        r = fa.recommend(fz, inv, run)
        combo = r.combos[0]
        self.assertIn("fz_run_share", [x.rule_id for x in combo.reasons])
        self.assertTrue(r.best is not None)
        self.assertTrue(any("비교" in n or "진화" in n for n in r.notes))


class ExpeditionHistoryTest(unittest.TestCase):
    def test_personal_line(self):
        recs = [RunRecord(started_at=i, result="보스 격퇴", endless_turns=t,
                          expedition={"verdict": "continue", "health": h, "evolved": 2, "maxed": 2})
                for i, (h, t) in enumerate([(0.8, 100), (0.7, 60), (0.75, 90), (0.2, 5), (0.9, 150)])]
        recs.append(RunRecord(started_at=9, result="보스 격퇴", expedition={"verdict": "return", "health": 0.3}))
        hist = expedition_history(recs)
        self.assertEqual(len(hist), 5)          # 복귀한 런은 빠진다
        line = personal_line(hist, 0.8, 2)
        self.assertIn("4번 중", line)
        self.assertIn("100턴", line)
        self.assertIsNone(personal_line(hist[:4], 0.8, 2))   # 표본 부족


class PlanningTest(unittest.TestCase):
    def test_levels(self):
        d = game_data()
        meta = copy.deepcopy(META)
        meta["chars"] = [{"type": "kRecaller", "lvl": 3}, {"type": "kEmptyNester", "lvl": 8},
                         {"type": "kItchyFinger", "lvl": 1}]
        meta["levels"] = [
            {"type": "kSnowy", "name": "얼어붙은{[x]}설원", "unlocked": True, "done": True, "chars_done": ["kRecaller"],
             "blueprints_left": ["kBank"]},
            {"type": "kDesert", "name": "경계의{[x]}사막", "unlocked": True, "done": False, "chars_done": [],
             "blueprints_left": ["kExorcist", "kCasino", "kMarket"]},
            {"type": "kHell", "name": "지옥", "unlocked": False, "chars_done": [], "blueprints_left": ["kLab"]}]
        m = parse_meta(meta, d)
        recs = [RunRecord(started_at=1, char="char:itchyfinger", result="보스 격퇴")]
        plans = plan_levels(m, d, recs)
        self.assertEqual([p.level for p in plans], ["kSnowy", "kDesert"])     # 잠긴 지역 제외
        self.assertEqual(plans[0].name, "얼어붙은 설원")
        self.assertNotIn("char:recaller", plans[0].chars_left)
        self.assertEqual(plans[1].chars_left[0], "char:itchyfinger")          # 내 격퇴 기록이 있는 캐릭터 먼저
        self.assertEqual(plans[1].chars_left[1], "char:emptynester")          # 그다음 캐릭터 레벨
        self.assertEqual(blueprint_targets(plans)[0].level, "kDesert")
        self.assertEqual(level_name({"type": "kX"}), "kX")


if __name__ == "__main__":
    unittest.main()
