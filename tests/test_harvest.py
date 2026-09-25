"""생산 건물 비율 조언(advise_resource_ratio)·골드마인 조준 팁(gold_bounce_tip)."""
import unittest

from src.engine.harvest import advise_resource_ratio, gold_bounce_tip


class ResourceRatioTest(unittest.TestCase):
    """목표 비율 밀 1.5 : 나무 1.25 : 돌 1 (Steam 가이드 Drake·apo), 건물 분당 생산량은 Drake 측정값."""

    def test_no_note_when_too_few_buildings(self):
        self.assertIsNone(advise_resource_ratio(["kIdleFarm"], 1))

    def test_no_note_at_guide_ratio(self):
        # Drake 가이드 정답 개수: 채석장 11 · 야적장 4 · 농장 3 (+ 거처 3) → 분당 78 · 62.5 · 55.5
        types = ["kIdleStoneMine"] * 11 + ["kIdleLumberyard"] * 4 + ["kIdleFarm"] * 3 +             ["kSingleFamilyHome", "kCozyHome", "kHovel"]
        self.assertIsNone(advise_resource_ratio(types, 3))

    def test_equal_counts_still_lack_stone(self):
        """개수가 같아도 채석장은 분당 4.5 돌뿐이라 돌이 크게 모자란다 (농장 24 밀)."""
        note = advise_resource_ratio(["kIdleFarm", "kIdleLumberyard", "kIdleStoneMine"], None)
        self.assertIn("채석장", note)

    def test_note_when_needed_resource_building_lags(self):
        # 돌이 부족한데 채석장은 1개뿐, 농장은 4개 (나무도 0이지만 지금 부족한 돌을 먼저)
        types = ["kIdleFarm"] * 4 + ["kIdleStoneMine"]
        note = advise_resource_ratio(types, 3)
        self.assertIsNotNone(note)
        self.assertIn("채석장", note)
        self.assertIn("돌", note)

    def test_note_when_needed_resource_has_zero_even_early_game(self):
        """실제 기록(2026-09-25 라이브 스냅샷): 농장 1·야적장 0·채석장 1 기지에서 나무가 부족 → 야적장."""
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
        out = advise_workers(None, chars, ["kIdleFarm", "kIdleStoneMine"], _Names())
        self.assertEqual(sorted((a.char_id, a.action) for a in out),
                         [("char:mid", "assign"), ("char:weak", "assign")])
        self.assertNotIn("char:strong", [a.char_id for a in out])

    def test_gold_mines_are_not_filled_and_workers_come_out(self):
        """금광은 안 씀 (사용자 결정: 무한 모드로 골드 충분) — 빈 금광은 채우지 않고, 금광 일꾼은 빼라고 한다."""
        from src.engine.harvest import advise_workers
        chars = [{"type": "kMiner", "state": "kWorking", "work": "kGoldMine"},
                 {"type": "kWeak", "state": "kIdle"}]
        out = advise_workers(None, chars, ["kGoldMine", "kGoldMine"], _Names())
        self.assertEqual([(a.building, a.char_id, a.action) for a in out], [("kGoldMine", "char:miner", "remove")])

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
