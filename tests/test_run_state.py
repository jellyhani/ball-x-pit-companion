"""보유 상태 (합성 입력)."""
import unittest

from src.domain import CardLabel, PickOutcome
from src.tracking.run_state import RunState
from tests.helpers import card, game_data, slots


class RunStateTest(unittest.TestCase):
    def setUp(self):
        self.d = game_data()
        self.run = RunState()
        self.run.start_run()

    def test_inventory_is_authoritative(self):
        self.run.set_owned("ball:bleed", "ball", 3)   # 잘못 기록된 항목
        self.run.apply_inventory(slots(("ball:burn", 1), ("passive:magnet", 2)), self.d)
        self.assertEqual({k: v.level for k, v in self.run.owned.items()}, {"ball:burn": 1, "passive:magnet": 2})
        self.assertEqual(self.run.owned["passive:magnet"].kind, "passive")   # 패시브가 볼 칸으로 가지 않는다
        self.assertTrue(self.run.owned_complete)

    def test_unreadable_slot_keeps_previous_record(self):
        self.run.set_owned("ball:bleed", "ball", 2)
        self.run.apply_inventory(slots(("ball:burn", 1), unreadable=1), self.d)
        self.assertIn("ball:bleed", self.run.owned)
        self.assertFalse(self.run.owned_complete)
        self.assertIn("보유 칸 1개를 읽지 못함", self.run.limitations(self.d))

    def test_unknown_level_is_none_not_zero(self):
        self.run.apply_inventory(slots(("ball:burn", None)), self.d)
        self.assertIsNone(self.run.owned["ball:burn"].level)

    def test_duplicate_outcome_applied_once(self):
        out = PickOutcome(7, "picked", card(0, "ball:burn", CardLabel.UPGRADE, 2), "클릭")
        self.assertTrue(self.run.apply_outcome(out, self.d))
        self.assertFalse(self.run.apply_outcome(out, self.d))
        self.assertEqual(self.run.owned["ball:burn"].level, 2)

    def test_second_or_third_pick_is_what_gets_recorded(self):
        out = PickOutcome(1, "picked", card(2, "ball:earthquake"), "오른쪽 카드 클릭")
        self.run.apply_outcome(out, self.d)
        self.assertEqual(self.run.owned["ball:earthquake"].level, 1)

    def test_unknown_outcome_flags_state_until_next_inventory(self):
        self.run.apply_outcome(PickOutcome(1, "unknown"), self.d)
        self.assertIn("직전 선택 결과 미확인", self.run.limitations(self.d))
        self.run.apply_inventory(slots(("ball:burn", 2)), self.d)
        self.assertNotIn("직전 선택 결과 미확인", self.run.limitations(self.d))

    def test_new_run_clears_previous_run(self):
        self.run.apply_inventory(slots(("ball:burn", 3)), self.d)
        self.run.apply_character("char:itchyfinger")
        self.run.start_run()
        self.assertEqual(self.run.owned, {})
        self.assertEqual(self.run.characters, [])
        self.assertFalse(self.run.inventory_seen)

    def test_mid_run_join_is_marked(self):
        r = RunState()
        r.join_mid_run()
        self.assertTrue(r.joined_mid_run)
        self.assertIn("보유 칸을 아직 읽지 못함", r.limitations(self.d))

    def test_manual_character_not_overwritten_by_portrait(self):
        self.run.set_character("char:emptynester")
        self.run.apply_character("char:itchyfinger")
        self.assertEqual(self.run.character_ids, ["char:emptynester"])

    def test_upgrade_card_implies_ownership_when_inventory_unread(self):
        self.run.reconcile_cards((card(0, "ball:burn", CardLabel.UPGRADE, 2),), self.d)
        self.assertEqual(self.run.owned["ball:burn"].level, 1)


if __name__ == "__main__":
    unittest.main()
