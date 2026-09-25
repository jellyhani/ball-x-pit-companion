"""생산 건물 비율 조언(advise_resource_ratio)·골드마인 조준 팁(gold_bounce_tip)."""
import unittest

from src.engine.harvest import advise_resource_ratio, gold_bounce_tip


class ResourceRatioTest(unittest.TestCase):
    def test_no_note_when_too_few_buildings(self):
        self.assertIsNone(advise_resource_ratio(["kIdleFarm"], 1))

    def test_no_note_when_balanced(self):
        types = ["kIdleFarm", "kIdleLumberyard", "kIdleStoneMine"]
        self.assertIsNone(advise_resource_ratio(types, 3))

    def test_no_note_when_need_unknown(self):
        types = ["kIdleFarm", "kIdleFarm", "kIdleLumberyard"]
        self.assertIsNone(advise_resource_ratio(types, None))
        self.assertIsNone(advise_resource_ratio(types, 0))    # 0 = 골드, 이 조언 대상 아님

    def test_note_when_needed_resource_building_lags(self):
        # 돌이 부족한데 채석장은 1개뿐, 농장은 4개
        types = ["kIdleFarm"] * 4 + ["kIdleStoneMine"]
        note = advise_resource_ratio(types, 3)
        self.assertIsNotNone(note)
        self.assertIn("채석장", note)
        self.assertIn("돌", note)

    def test_note_when_needed_resource_has_zero_even_early_game(self):
        """실제 기록 확인(2026-09-25 라이브 스냅샷): 농장 1·야적장 0·채석장 1인 실제 기지에서
        나무가 부족한데도 예전 임계값(최대 2개 이상)때문에 조언이 안 떴음 — 고친 뒤 재확인."""
        types = ["kIdleFarm", "kIdleStoneMine"]
        note = advise_resource_ratio(types, 2)
        self.assertIsNotNone(note)
        self.assertIn("야적장", note)


class GoldBounceTipTest(unittest.TestCase):
    def test_no_tip_without_unfinished_buildings(self):
        base = {"buildings": [{"type": "kGoldMine"}]}
        self.assertIsNone(gold_bounce_tip(base, []))

    def test_no_tip_without_gold_mine(self):
        base = {"buildings": [{"type": "kIdleFarm"}]}
        self.assertIsNone(gold_bounce_tip(base, [object()]))

    def test_tip_when_both_present(self):
        base = {"buildings": [{"type": "kGoldMine"}, {"type": "kIdleFarm"}]}
        self.assertIsNotNone(gold_bounce_tip(base, [object()]))


if __name__ == "__main__":
    unittest.main()
