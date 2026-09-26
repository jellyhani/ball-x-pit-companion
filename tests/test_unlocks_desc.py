"""해금 안 된 재료가 든 진화 거르기(tracking/unlocks.py, GameData.available)와 게임 설명 수치 채우기(engine/desc_fill.py)."""
import os
import tempfile
import unittest

from src.engine.desc_fill import fill, match_key
from src.gamedata import load_game_data
from src.tracking import unlocks
from src.tracking.run_state import Owned, RunState

# 기본 볼 20개 중 매혹만 빠진 실제 상황 (2026-09-26 게임 후보 목록)
BASE = ["ball:" + s for s in ("bleed", "burn", "freeze", "lightning", "poison", "dark", "ghost", "light", "laserhorz",
                              "laservert", "stone", "heavy", "wind", "vampire", "broodmother", "eggsac", "cell", "flesh",
                              "earthquake")]


class UnlockFilterTest(unittest.TestCase):
    def setUp(self):
        self.d = load_game_data()          # 새 인스턴스 — 해금 목록을 바꿔도 다른 테스트에 새지 않게

    def test_unknown_means_no_filter(self):
        charm = [r for r in self.d.recipes if "ball:charm" in r.ingredients]
        self.assertTrue(charm)
        self.assertTrue(all(self.d.recipe_reachable(r) for r in charm))

    def test_locked_base_ball_blocks_its_evolutions(self):
        self.d.note_available(BASE)
        berserk = next(r for r in self.d.recipes if r.result == "ball:berserk" and "ball:charm" in r.ingredients)
        self.assertFalse(self.d.recipe_reachable(berserk))
        self.assertEqual(self.d.locked_ingredients(berserk), ["ball:charm"])
        magma = next(r for r in self.d.recipes if r.result == "ball:magma")
        self.assertTrue(self.d.recipe_reachable(magma))

    def test_roadmap_and_targets_skip_locked(self):
        from src.engine.roadmap import browsable_targets, build_roadmap
        self.d.note_available(BASE)
        run = RunState()
        run.start_run()
        run.owned["ball:burn"] = Owned("ball:burn", "ball", 1, "game")
        entries = build_roadmap(run, self.d)
        self.assertTrue(entries)
        self.assertFalse([e for e in entries if "ball:charm" in e.recipe.ingredients])   # 광란(매혹 + 화상) 없음
        targets = browsable_targets(self.d)
        names = [r.result for r in targets]
        self.assertEqual(len(names), len(set(names)))          # 결과가 같은 레시피는 하나만 (냉동 광선 두 개 → 하나)
        self.assertNotIn("ball:lovestruck", names)             # 매혹 + 빛 — 매혹이 잠김

    def test_recommender_ignores_locked_evolution(self):
        from src.engine.recommender import Recommender
        from tests.helpers import card, session
        run = RunState()
        run.start_run()
        run.owned["ball:burn"] = Owned("ball:burn", "ball", 1, "game")
        before = Recommender(self.d).recommend(session([card(0, "ball:charm")]), run).evals[0]
        self.d.note_available(BASE)
        after = Recommender(self.d).recommend(session([card(0, "ball:bleed")]), run).evals[0]
        self.assertTrue(any(r.rule_id.startswith("evo") for r in before.reasons))    # 해금 모를 땐 광란 경로
        self.assertFalse(any("광란" in r.text for r in after.reasons))

    def test_store_roundtrip_and_disabled(self):
        path = os.path.join(tempfile.mkdtemp(), "u.json")
        self.assertIsNone(unlocks.load(path))
        unlocks.save(path, {"ball:burn", "ball:bleed"})
        self.assertEqual(unlocks.load(path), {"ball:burn", "ball:bleed"})
        self.assertIsNone(unlocks.load("-"))
        unlocks.save("-", {"x"})                           # 테스트 모드: 아무것도 안 함


class DescFillTest(unittest.TestCase):
    def test_match_names(self):
        self.assertEqual(match_key("reduce_speed_pct", ["kReduceSpeedPct", "kMaxDamagePct"]), "kReduceSpeedPct")
        self.assertEqual(match_key("damage_pct", ["kBonusDamagePct", "kMaxDamagePct"]), "kBonusDamagePct")
        self.assertEqual(match_key("min_spawn_cycle", ["kMinBlockSpawnCycle", "kMaxBlockSpawnCycle"]), "kMinBlockSpawnCycle")
        self.assertEqual(match_key("lifesteal_heal", ["kLifeStealChance", "kLifeStealHeal"]), "kLifeStealHeal")
        self.assertEqual(match_key("aoe_dmg_pct", ["kAOEDmgPct"]), "kAOEDmgPct")
        self.assertEqual(match_key("max_venom_stacks", ["kVenomStacks", "kMaxVenomDamage"]), "kVenomStacks")
        self.assertIsNone(match_key("nothing_here", ["kBurnLength"]))

    def test_levels_ranges_and_units(self):
        steel = [{"kReduceSpeedPct": 50, "kBonusDamagePct": 10, "kMaxDamagePct": 300},
                 {"kReduceSpeedPct": 50, "kBonusDamagePct": 15, "kMaxDamagePct": 300},
                 {"kReduceSpeedPct": 50, "kBonusDamagePct": 15, "kMaxDamagePct": 400}]
        self.assertEqual(fill("속도 {[reduce_speed_pct]} 감소, {[damage_pct]}씩 (최대 {[max_damage_pct]})", steel),
                         "속도 50% 감소, 10/15/15%씩 (최대 300/300/400%)")
        burn = [{"kMinBurnDamage": 4, "kMaxBurnDamage": 8}, {"kMinBurnDamage": 7, "kMaxBurnDamage": 11}]
        self.assertEqual(fill("초당 {[min_burn_damage]}-{[max_burn_damage]}", burn, 2), "초당 4-8/7-11")
        crit = [{"kCritChance": 600}, {"kCritChance": 800}, {"kCritChance": 1000}]
        self.assertEqual(fill("치명타 {[crit_chance]}", crit), "치명타 60/80/100%")      # 0.1% 단위 (위키 60/80/100%)
        self.assertEqual(fill("{[unknown_thing]}", crit), "?")
        self.assertIsNone(fill("", crit))
        self.assertIsNone(fill("{[crit_chance]}", None))


if __name__ == "__main__":
    unittest.main()
