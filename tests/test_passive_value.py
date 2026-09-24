"""패시브·볼의 레벨별 실제 수치 해석, 순위·확신도 표시 (게임 catalog 의 실제 lvl_props 값)."""
import unittest

from src.engine.passive_value import ball_effect, passive_effect

ARMOR = [{"kReduceDmgPct": 10}, {"kReduceDmgPct": 20}, {"kReduceDmgPct": 30}]
HOURGLASS = [{"kBonusDamagePct": 150, "kBonusDecayPct": 30, "kMinDamagePct": 50},
             {"kBonusDamagePct": 200, "kBonusDecayPct": 30, "kMinDamagePct": 60},
             {"kBonusDamagePct": 200, "kBonusDecayPct": 25, "kMinDamagePct": 60}]
TURRET = [{"kTurretCooldown": 20}, {"kTurretCooldown": 16}, {"kTurretCooldown": 12}]
BURN = [{"kBurnLength": 3, "kMinBurnDamage": 4, "kMaxBurnDamage": 8, "kMaxBurnStacks": 3},
        {"kBurnLength": 3, "kMinBurnDamage": 7, "kMaxBurnDamage": 11, "kMaxBurnStacks": 3},
        {"kBurnLength": 5, "kMinBurnDamage": 7, "kMaxBurnDamage": 11, "kMaxBurnStacks": 5}]


class PassiveEffectTest(unittest.TestCase):
    def test_new_and_upgrade_text(self):
        self.assertEqual(passive_effect(ARMOR, None, 1).text, "받는 피해 감소 10%")
        pe = passive_effect(ARMOR, 1, 2)
        self.assertEqual((pe.role, pe.text), ("defense", "받는 피해 감소 10% → 20%"))
        self.assertAlmostEqual(pe.gain, 1.0)

    def test_small_upgrade_is_detected(self):
        pe = passive_effect(HOURGLASS, 2, 3)          # 주 효과 200 → 200, 감소율 30 → 25 만
        self.assertLess(pe.gain, 0.3)
        self.assertIn("피해 감소율 30% → 25%", pe.text)

    def test_lower_is_better(self):
        pe = passive_effect(TURRET, 1, 2)
        self.assertGreater(pe.gain, 0)
        self.assertEqual(pe.role, "ally")

    def test_damage_range_pairs(self):
        amulet = [{"kMaxDamage": 15, "kMinDamage": 10}, {"kMaxDamage": 20, "kMinDamage": 15}]
        self.assertEqual(passive_effect(amulet, None, 1).text, "피해 10~15")
        self.assertEqual(passive_effect(amulet, 1, 2).text, "피해 10~15 → 15~20")

    def test_unknown_or_out_of_range(self):
        self.assertIsNone(passive_effect(None, None, 1))
        self.assertIsNone(passive_effect(ARMOR, 3, 4))

    def test_ball_effect(self):
        self.assertEqual(ball_effect(BURN, None, 1), "피해 4~8")
        self.assertEqual(ball_effect(BURN, 1, 2), "피해 4~8 → 7~11")
        self.assertEqual(ball_effect(BURN, 2, 3), "지속 3 → 5 · 최대 중첩 3 → 5")


class RankTest(unittest.TestCase):
    def test_every_known_card_gets_a_rank_and_badge(self):
        from tests.test_ui import sample_recommendation
        from src.engine.recommender import card_badge, card_rank
        rec = sample_recommendation()
        known = [e for e in rec.evals if e.evaluated]
        self.assertEqual(sorted(card_rank(rec, e) for e in known), list(range(1, len(known) + 1)))
        self.assertTrue(card_badge(rec, rec.best).startswith("1위"))
        self.assertIn(rec.confidence, ("확실", "추천", "근소"))


if __name__ == "__main__":
    unittest.main()
