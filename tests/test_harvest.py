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



class _Names:
    """advise_workers 가 쓰는 이름 함수만 (게임 문구 없이도 돌게)."""
    def building_name(self, t):
        return t

    def name(self, cid):
        return cid


class WorkerAdviceTest(unittest.TestCase):
    """건물 일꾼은 채집 때 발사되지 않는다 (실제 궤적: 13명 중 동시에 10~11명) → 발사 강화가 적은 캐릭터를 건물에."""

    def test_empty_mine_gets_weakest_launcher(self):
        from src.engine.harvest import advise_workers
        chars = [{"type": "kStrong", "state": "kIdle", "harvest": {"kPierceStone": 1, "kFasterStone": 1}},
                 {"type": "kWeak", "state": "kIdle"},
                 {"type": "kMid", "state": "kIdle", "harvest": {"kFasterWheat": 1}}]
        out = advise_workers(None, chars, ["kGoldMine", "kGoldMine"], _Names())
        self.assertEqual([(a.building, a.char_id, a.action) for a in out],
                         [("kGoldMine", "char:weak", "assign"), ("kGoldMine", "char:mid", "assign")])

    def test_strong_harvester_in_building_is_swapped_out(self):
        from src.engine.harvest import advise_workers
        chars = [{"type": "kStrong", "state": "kWorking", "work": "kIdleStoneMine",
                  "harvest": {"kPierceStone": 1, "kFasterStone": 1}},
                 {"type": "kWeak", "state": "kIdle"}]
        out = advise_workers(None, chars, ["kIdleStoneMine"], _Names())
        self.assertEqual([(a.building, a.char_id, a.action, a.replace) for a in out],
                         [("kIdleStoneMine", "char:weak", "swap", "char:strong")])

    def test_building_upgrade_wins_the_slot(self):
        from src.engine.harvest import advise_workers
        chars = [{"type": "kWeak", "state": "kIdle"},
                 {"type": "kFarmer", "state": "kIdle", "harvest": {"kFarmSpeed": 1, "kFasterWheat": 1}}]
        out = advise_workers(None, chars, ["kIdleFarm"], _Names())
        self.assertEqual([(a.building, a.char_id) for a in out], [("kIdleFarm", "char:farmer")])

    def test_working_chars_are_not_launched(self):
        from src.engine import harvest_sim as hs
        team = hs.team_from_chars([{"type": "kA", "state": "kWorking", "work": "kGoldMine"},
                                   {"type": "kB", "state": "kIdle"}])
        self.assertEqual([m["type"] for m in team], ["kB"])


if __name__ == "__main__":
    unittest.main()
