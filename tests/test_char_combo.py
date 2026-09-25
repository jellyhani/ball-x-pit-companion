"""캐릭터 조합 추천·융합 진화 티어·새 건물이 생긴 뒤 남은 옮기기."""
import unittest

from tests import HAS_GAME_DATA

if HAS_GAME_DATA:
    from src.engine.char_combo import char_id, suggest_pairs
    from src.gamedata import load_game_data


class Rec:
    def __init__(self, char, extra, result):
        self.char, self.extra_chars, self.result = char, extra, result


@unittest.skipUnless(HAS_GAME_DATA, "게임 자료 없음")
class CharComboTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = load_game_data()

    def test_game_type_to_id(self):
        self.assertEqual(char_id("kItchyFinger"), "char:itchyfinger")
        self.assertEqual(char_id("kDefault"), "char:default")

    def test_pairs_only_from_unlocked(self):
        chars = [{"type": "kItchyFinger", "lvl": 3}, {"type": "kRecaller", "lvl": 3}, {"type": "kShade", "lvl": 1}]
        pairs = suggest_pairs(self.d, chars)
        self.assertEqual({pairs[0].a, pairs[0].b}, {"char:itchyfinger", "char:recaller"})
        for p in pairs:
            self.assertTrue({p.a, p.b} <= {"char:itchyfinger", "char:recaller", "char:shade"})

    def test_my_record_counts(self):
        chars = [{"type": "kShade", "lvl": 1}, {"type": "kCogitator", "lvl": 1}]
        self.assertEqual(suggest_pairs(self.d, chars), [])            # 추천도 기록도 없으면 안 띄움
        hist = [Rec("char:shade", ["char:cogitator"], "보스 격퇴")] * 2
        pairs = suggest_pairs(self.d, chars, hist)
        self.assertEqual(len(pairs), 1)
        self.assertIn("내 기록", pairs[0].reasons[0])

    def test_fixed_only_pairs_with_that_char(self):
        """알선소에서 캐릭터 하나를 이미 고른 채 둘째를 고르는 중 — 그 캐릭터가 낀 조합만."""
        chars = [{"type": "kItchyFinger", "lvl": 3}, {"type": "kRecaller", "lvl": 3}, {"type": "kShade", "lvl": 1}]
        pairs = suggest_pairs(self.d, chars, fixed="char:itchyfinger")
        self.assertTrue(pairs)
        for p in pairs:
            self.assertIn("char:itchyfinger", (p.a, p.b))
        self.assertEqual(suggest_pairs(self.d, chars, fixed="char:shade"), [])   # 섀이드 낀 근거 있는 조합 없음

    def test_exact_pair_shown_even_without_evidence(self):
        """둘 다 이미 골랐으면 커뮤니티 추천·내 기록이 없어도 궁합을 보여준다 (전략 궁합 정도만 있어도 됨)."""
        chars = [{"type": "kItchyFinger", "lvl": 3}, {"type": "kShade", "lvl": 1}]
        pairs = suggest_pairs(self.d, chars, exact=("char:itchyfinger", "char:shade"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual({pairs[0].a, pairs[0].b}, {"char:itchyfinger", "char:shade"})
        self.assertTrue(all("커뮤니티" not in r and "내 기록" not in r for r in pairs[0].reasons))

    def test_exact_pair_shown_with_no_reasons_at_all(self):
        """전략 궁합도 안 맞는 조합은 근거 문구 없이(빈 목록) 낮은 점수로만 표시된다."""
        chars = [{"type": "kRecaller", "lvl": 3}, {"type": "kShade", "lvl": 1}]
        pairs = suggest_pairs(self.d, chars, exact=("char:recaller", "char:shade"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].reasons, [])

    def test_fusion_prefers_higher_tier_evolution(self):
        from src.domain import FuserEvo, FuserOptions
        from src.engine.fusion import FusionAdvisor
        from src.tracking.run_state import RunState
        run = RunState()
        run.start_run()
        fz = FuserOptions(options=(), evos=(FuserEvo("ball:radiationbeam", 0), FuserEvo("ball:swamp", 1)), combos=(),
                          free_upgrades=None)
        rec = FusionAdvisor(self.d).recommend(fz, (), run)
        self.assertEqual(rec.best.result_id, "ball:swamp")          # 늪 S티어 > 방사성 광선 A티어

    def test_free_upgrade_beats_combo_when_many_balls_unleveled(self):
        """분열: 최대 레벨이 안 된 볼이 여럿이면 평범한 융합보다 먼저 권한다 (dood.gg 메타 가이드)."""
        from src.domain import FuserCombo, FuserOptions, InventorySlot
        from src.engine.fusion import FusionAdvisor
        from src.tracking.run_state import RunState
        run = RunState()
        run.start_run()
        combo = FuserCombo("ball:radiationbeam", "ball:swamp", 0, 1)   # ai_score·bad 없음 — 평범한 융합
        fz = FuserOptions(options=("kCombo", "kFreeUpgrades"), evos=(), combos=(combo,), free_upgrades=True)
        maxlv = self.d.max_level("ball")
        balls = tuple(InventorySlot(i, (0, 0, 0, 0), True, "ball:radiationbeam", 1) for i in range(4))
        rec = FusionAdvisor(self.d).recommend(fz, balls, run)
        self.assertEqual(rec.best.kind, "free")
        self.assertLess(maxlv, 4)                                      # 전제: 레벨 1은 최대 레벨이 아님

    def test_free_upgrade_loses_to_combo_when_balls_maxed(self):
        from src.domain import FuserCombo, FuserOptions, InventorySlot
        from src.engine.fusion import FusionAdvisor
        from src.tracking.run_state import RunState
        run = RunState()
        run.start_run()
        combo = FuserCombo("ball:radiationbeam", "ball:swamp", 0, 1)
        maxlv = self.d.max_level("ball")
        fz = FuserOptions(options=("kCombo", "kFreeUpgrades"), evos=(), combos=(combo,), free_upgrades=True)
        balls = tuple(InventorySlot(i, (0, 0, 0, 0), True, "ball:radiationbeam", maxlv) for i in range(4))
        rec = FusionAdvisor(self.d).recommend(fz, balls, run)
        self.assertEqual(rec.best.kind, "combo")


class RemainingMovesTest(unittest.TestCase):
    def test_new_building_not_in_plan(self):
        """계획 뒤 새로 지은 건물(계획에 없음)이 있어도 멈추지 않는다 (실제 로그: KeyError 68)."""
        import json
        import os
        from src.engine import layout_opt as lo
        fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "harvest_trace_48deg.json"),
                            encoding="utf-8"))
        base = {"buildings": fx["buildings_before"], "geo": fx["geo"]}
        final = {b["id"]: (b["x"], b["y"]) for b in fx["buildings_before"][1:]}   # 첫 건물은 계획에 없음
        self.assertEqual(lo.remaining_moves(base, final), [])


if __name__ == "__main__":
    unittest.main()
