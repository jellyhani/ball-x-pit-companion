"""추천 엔진 (합성 상태 입력). 기대값은 rules.json 과 게임 공식 설명에서 나온 규칙이다."""
import unittest

from src.domain import CardLabel
from src.engine.recommender import Recommender
from src.tracking.run_state import RunState
from tests.helpers import card, game_data, session, slots


class RecommenderTest(unittest.TestCase):
    def setUp(self):
        self.d = game_data()
        self.rec = Recommender(self.d)
        self.run = RunState()
        self.run.start_run()

    def state(self, *items, char=None):
        self.run.apply_inventory(slots(*items), self.d)
        if char:
            self.run.apply_character(char)
        return self.run

    def with_known_max(self, level=3):
        """게임 연동이 최대 레벨을 알려 준 상황 (공유 데이터를 바꾸지 않도록 새로 읽는다)."""
        from src.gamedata import load_game_data
        self.d = load_game_data()
        self.d.set_observed_max_level("ball", level)
        self.rec = Recommender(self.d)

    def test_upgrade_that_completes_evolution_wins(self):
        # 대출혈 = 출혈 + 무쇠 (위키 레시피). 최대 3 확정, 무쇠 3, 출혈 2 → 출혈 강화로 진화 조건 충족
        self.with_known_max(3)
        run = self.state(("ball:bleed", 2), ("ball:heavy", 3))
        s = session([card(0, "ball:stone"), card(1, "ball:bleed", CardLabel.UPGRADE, 3), card(2, "ball:wind")])
        r = self.rec.recommend(s, run)
        self.assertEqual(r.status, "recommend")
        self.assertEqual(r.best.card.position, "가운데")
        self.assertIn("evo_ready", [x.rule_id for x in r.best.reasons])
        self.assertEqual(r.best.action_text, "레벨 2 → 3")

    def test_evolution_is_judged_after_the_action(self):
        # 행동 전에는 출혈 1레벨. 강화 후에도 최대가 아니면 '조건 충족'이라고 하지 않는다
        self.with_known_max(3)
        run = self.state(("ball:bleed", 1), ("ball:heavy", 3))
        s = session([card(0, "ball:bleed", CardLabel.UPGRADE, 2)])
        ev = self.rec.recommend(s, run).evals[0]
        ids = [x.rule_id for x in ev.reasons]
        self.assertNotIn("evo_ready", ids)
        self.assertIn("evo_path", ids)

    def test_unknown_max_level_makes_no_numeric_claim(self):
        # 게임이 최대 레벨을 알려 주기 전(화면 인식 경로 등)에는 위키 값으로 숫자를 말하지 않는다
        run = self.state(("ball:bleed", 2), ("ball:heavy", 3))
        ev = self.rec.recommend(session([card(0, "ball:bleed", CardLabel.UPGRADE, 3)]), run).evals[0]
        ids = [x.rule_id for x in ev.reasons]
        self.assertNotIn("evo_ready", ids)
        self.assertNotIn("evo_path", ids)
        self.assertIn("evo_path_level_unknown", ids)
        self.assertNotIn("already_max", [w.rule_id for w in ev.warnings])

    def test_game_max_flag_is_used(self):
        from tests.helpers import slots as mk
        from src.domain import InventorySlot
        inv = tuple(InventorySlot(i, (0, 0, 0, 0), True, it, lv, at_max=mx)
                    for i, (it, lv, mx) in enumerate((("ball:bleed", 4, False), ("ball:heavy", 5, True))))
        self.run.apply_inventory(inv, self.d)
        ev = self.rec.recommend(session([card(0, "ball:bleed", CardLabel.UPGRADE, 5)]), self.run).evals[0]
        self.assertIn("evo_partner_max", [x.rule_id for x in ev.reasons])

    def test_leech_gets_no_healing_reason(self):
        run = self.state(("ball:burn", 1))
        ev = self.rec.recommend(session([card(0, "ball:leech")]), run).evals[0]
        self.assertNotIn("heal_gap", [x.rule_id for x in ev.reasons])

    def test_real_heal_source_valued_only_when_state_complete(self):
        run = self.state(("ball:burn", 1))
        ev = self.rec.recommend(session([card(0, "ball:vampire")]), run).evals[0]
        self.assertIn("heal_gap", [x.rule_id for x in ev.reasons])
        run.apply_inventory(slots(("ball:burn", 1), unreadable=1), self.d)   # 한 칸을 못 읽음 → '없음' 판단 불가
        ev = self.rec.recommend(session([card(0, "ball:vampire")]), run).evals[0]
        self.assertNotIn("heal_gap", [x.rule_id for x in ev.reasons])

    def test_character_changes_baby_ball_evaluation(self):
        cards = [card(0, "passive:babyrattle")]
        warrior = self.rec.recommend(session(cards), self.state(("ball:burn", 1), char="char:default")).evals[0]
        self.run.start_run()
        nester = self.rec.recommend(session(cards), self.state(("ball:burn", 1), char="char:emptynester")).evals[0]
        self.assertEqual(warrior.warnings, [])
        self.assertIn("char_reduces", [w.rule_id for w in nester.warnings])
        self.assertLess(nester.score, warrior.score)

    def test_unknown_card_is_not_ranked(self):
        run = self.state(("ball:bleed", 2), ("ball:heavy", 3))
        s = session([card(0, None), card(1, "ball:bleed", CardLabel.UPGRADE, 3), card(2, "ball:wind")])
        r = self.rec.recommend(s, run)
        self.assertFalse(r.evals[0].evaluated)
        self.assertTrue(any("읽지 못한 카드 1장" in x for x in r.limitations))
        self.assertEqual(r.reroll_status, "unknown")

    # 화상과 레시피·태그로 이어지지 않는 항목들 (레시피 데이터 기준)
    UNLINKED = ("ball:lightningbug", "ball:warp", "ball:petrify")

    def test_no_connection_means_hold_not_fake_first_place(self):
        run = self.state(("ball:burn", 1))
        s = session([card(i, x) for i, x in enumerate(self.UNLINKED)])
        r = self.rec.recommend(s, run)
        self.assertEqual(r.status, "hold")
        self.assertIsNone(r.best)

    def test_reroll_advice_uses_gold(self):
        run = self.state(("ball:burn", 1))
        cards = [card(i, x) for i, x in enumerate(self.UNLINKED)]
        self.assertEqual(self.rec.recommend(session(cards, gold=0, reroll_cost=5), run).reroll_status, "none")
        r = self.rec.recommend(session(cards, gold=None, reroll_cost=5), run)
        self.assertEqual(r.reroll_status, "consider")
        self.assertIn("보유 골드 미확인", r.reroll_text)
        r = self.rec.recommend(session(cards, gold=20, reroll_cost=5), run)
        self.assertIn("5골드, 보유 20골드", r.reroll_text)
        self.assertIn("무료 2회", self.rec.recommend(session(cards, free_rerolls=2), run).reroll_text)

    def test_strong_option_means_no_reroll(self):
        run = self.state(("ball:bleed", 2), ("ball:heavy", 3))
        s = session([card(0, "ball:bleed", CardLabel.UPGRADE, 3)], gold=50, reroll_cost=5)
        self.assertEqual(self.rec.recommend(s, run).reroll_status, "keep")

    def test_auto_select_character_gets_no_recommendation(self):
        run = self.state(("ball:burn", 1), char="char:cogitator")
        r = self.rec.recommend(session([card(0, "ball:stone")]), run)
        self.assertEqual(r.status, "auto")

    def test_deterministic(self):
        run = self.state(("ball:bleed", 2), ("ball:heavy", 3))
        s = session([card(0, "ball:stone"), card(1, "ball:bleed", CardLabel.UPGRADE, 3), card(2, "ball:wind")])
        a, b = self.rec.recommend(s, run), self.rec.recommend(s, run)
        self.assertEqual([(e.card.index, e.score) for e in a.evals], [(e.card.index, e.score) for e in b.evals])

    def test_screenshot_situation(self):
        # 실제 스크린샷 상황: 난사광, 화상 1. 선택지 화상 강화 / 돌 / 지진
        run = self.state(("ball:burn", 1), char="char:itchyfinger")
        s = session([card(0, "ball:burn", CardLabel.UPGRADE, 2), card(1, "ball:stone"), card(2, "ball:earthquake")],
                    gold=0, reroll_cost=5)
        r = self.rec.recommend(s, run)
        self.assertEqual(r.reroll_status, "none")   # 골드 0, 비용 5 → 새로고침 불가
        # 화상 강화 / 돌(→유황) / 지진(→용암) 모두 근거가 있고 차이가 작다 → 억지 1위 대신 '비슷함'
        self.assertEqual(r.status, "close")
        quake = r.evals[2]
        self.assertTrue(any("용암" in x.text for x in quake.reasons))
        self.assertTrue(any("유황" in x.text for x in r.evals[1].reasons))

    def test_combined_ball_axis_reflected_in_archetype(self):
        # 산사태에 빙하를, 번개벌레에 냉동을 합쳐 넣음 — 둘 다 원래 범위(aoe) 계열이지만 합친 볼로 빙결 계열도 갖는다
        from src.domain import InventorySlot
        inv = (InventorySlot(0, (0, 0, 0, 0), True, "ball:landslide", 1, combined=("ball:glacier",)),
              InventorySlot(1, (0, 0, 0, 0), True, "ball:lightningbug", 1, combined=("ball:freeze",)))
        self.run.apply_inventory(inv, self.d)
        r = self.rec.recommend(session([card(0, "ball:freeze", CardLabel.NEW)]), self.run)
        self.assertIn("archetype", [x.rule_id for x in r.evals[0].reasons])

    def test_combined_away_ball_is_not_still_owned_for_evolution(self):
        # 피뢰침을 무쇠에 합쳐 넣으면 피뢰침은 인벤토리에서 사라진다 — '피뢰침 진화 가능'처럼
        # 이미 없어진 볼을 재료로 하는 진화를 '가능'이라고 잘못 보면 안 됨 (실제로 그렇게 되는지는 미확인)
        self.with_known_max(3)
        from src.domain import InventorySlot
        inv = (InventorySlot(0, (0, 0, 0, 0), True, "ball:heavy", 3, at_max=True, combined=("ball:bleed",)),)
        self.run.apply_inventory(inv, self.d)
        self.assertNotIn("ball:bleed", self.run.owned)     # 합쳐 넣은 볼은 따로 보유하지 않는다
        ev = self.rec.recommend(session([card(0, "ball:heavy", CardLabel.UPGRADE, 3)]), self.run).evals[0]
        ids = [x.rule_id for x in ev.reasons]
        self.assertNotIn("evo_ready", ids)
        self.assertNotIn("evo_path", ids)


class ProgressTest(unittest.TestCase):
    """런 진행 상황(게임 연동 값)에 따른 판단 — 합성 입력."""

    def setUp(self):
        self.d = game_data()
        self.rec = Recommender(self.d)
        self.run = RunState()
        self.run.start_run()
        self.run.apply_inventory(slots(("ball:burn", 1)), self.d)

    def progress(self, **kw):
        from src.domain import RunProgress
        base = dict(health=100, max_health=100, turn=20, final_boss_turn=200, max_balls=4, max_passives=4,
                    balls=1, passives=0)
        base.update(kw)
        return RunProgress(**base)

    def reasons(self, ev):
        return [r.rule_id for r in ev.reasons] + [w.rule_id for w in ev.warnings]

    def test_low_health_prefers_healing(self):
        s = session([card(0, "ball:vampire"), card(1, "ball:stone")], progress=self.progress(health=25))
        r = self.rec.recommend(s, self.run)
        self.assertIn("heal_low_hp", self.reasons(r.evals[0]))
        self.assertEqual(r.best.card.item_id, "ball:vampire")
        self.assertIn("체력 25%", r.situation)

    def test_early_empty_slots_value_new_balls(self):
        s = session([card(0, "ball:stone")], progress=self.progress(turn=20))
        # 볼이 1개뿐이면 시기와 상관없이 '볼 수 늘리기'가 먼저 (실제 플레이 확인)
        self.assertIn("slot_fill_few", self.reasons(self.rec.recommend(s, self.run).evals[0]))
        self.run.apply_inventory(slots(("ball:burn", 1), ("ball:freeze", 1), ("ball:wind", 1)), self.d)
        s = session([card(0, "ball:stone")], progress=self.progress(turn=20, balls=3))
        self.assertIn("slot_fill_early", self.reasons(self.rec.recommend(s, self.run).evals[0]))
        s = session([card(0, "ball:stone")], progress=self.progress(turn=150))
        self.assertNotIn("slot_fill_early", self.reasons(self.rec.recommend(s, self.run).evals[0]))

    def test_full_slots_warn(self):
        s = session([card(0, "passive:magnet")], progress=self.progress(passives=4))
        self.assertIn("slot_full", self.reasons(self.rec.recommend(s, self.run).evals[0]))

    def test_boss_soon_prefers_upgrades(self):
        s = session([card(0, "ball:burn", CardLabel.UPGRADE, 2)], progress=self.progress(turn=190))
        self.assertIn("boss_soon", self.reasons(self.rec.recommend(s, self.run).evals[0]))

    def test_banish_recommends_clearly_bad_card_only(self):
        self.run.apply_character("char:emptynester")
        cards = [card(0, "ball:stone"), card(1, "passive:babyrattle"), card(2, "ball:wind")]
        r = self.rec.recommend(session(cards, banish_left=2), self.run)
        self.assertEqual(r.banish_card.item_id, "passive:babyrattle")
        self.assertIn("삭제 추천", r.banish_text)
        # 삭제 횟수가 없으면 권하지 않는다
        self.assertIsNone(self.rec.recommend(session(cards, banish_left=0), self.run).banish_card)
        # 나쁜 점이 없으면 권하지 않는다
        self.run.set_character("char:default")
        self.assertIsNone(self.rec.recommend(session(cards, banish_left=2), self.run).banish_card)

    UNLINKED = RecommenderTest.UNLINKED

    def test_endless_prefers_upgrades_and_banishes_unlinked(self):
        # 보스 격퇴 후 '원정 계속'(무한의 심연): 강화 우선, 연결 없는 새 볼은 삭제 후보
        p = self.progress(endless=True, turn=300, final_boss_turn=190)
        cards = [card(0, "ball:burn", CardLabel.UPGRADE, 2), card(1, "ball:warp"), card(2, "ball:stone")]
        r = self.rec.recommend(session(cards, banish_left=2, progress=p), self.run)
        self.assertIn("endless_upgrade", self.reasons(r.evals[0]))
        self.assertIn("endless_unlinked", self.reasons(r.evals[1]))
        self.assertNotIn("endless_unlinked", self.reasons(r.evals[2]))   # 돌은 화상과 진화로 이어짐
        self.assertEqual(r.banish_card.item_id, "ball:warp")
        self.assertIn("무한의 심연", r.plan_text)
        self.assertNotIn("보스까지", r.situation)

    def test_last_slot_banish_prefers_repeated_card(self):
        p = self.progress(turn=100, balls=3)
        cards = [card(i, x) for i, x in enumerate(self.UNLINKED)]
        self.run.note_offered(("ball:petrify", "ball:stone"))
        self.run.note_offered(tuple(c.item_id for c in cards))
        r = self.rec.recommend(session(cards, banish_left=1, progress=p), self.run)
        self.assertTrue(all("last_slot" in self.reasons(e) for e in r.evals))
        self.assertEqual(r.banish_card.item_id, "ball:petrify")
        self.assertIn("2번째 등장", r.banish_text)

    def test_plan_items_are_never_banished(self):
        p = self.progress(endless=True, turn=300, final_boss_turn=190)
        from src.engine.deck_plan import build_plan
        plan = build_plan(self.run, self.d, p)
        self.assertIn("ball:stone", plan.wanted)          # 화상 + 돌 진화의 빠진 재료
        cards = [card(0, "ball:stone"), card(1, "ball:warp"), card(2, "ball:petrify")]
        r = self.rec.recommend(session(cards, banish_left=2, progress=p), self.run)
        self.assertNotEqual(r.banish_card.item_id, "ball:stone")

    def test_same_offer_is_counted_once(self):
        self.run.note_offered(("ball:stone", "ball:wind"))
        self.run.note_offered(("ball:wind", "ball:stone"))   # 같은 선택창이 다시 잡힘
        self.assertEqual(self.run.offered["ball:stone"], 1)
        self.run.note_offered(("ball:stone", "ball:warp"))
        self.assertEqual(self.run.offered["ball:stone"], 2)

    def test_expensive_reroll_is_saved(self):
        cards = [card(0, "ball:lightningbug"), card(1, "ball:warp"), card(2, "ball:petrify")]
        r = self.rec.recommend(session(cards, gold=200, reroll_cost=140), self.run)
        self.assertEqual(r.reroll_status, "keep")
        self.assertIn("아껴", r.reroll_text)
        r = self.rec.recommend(session(cards, gold=542, reroll_cost=140), self.run)
        self.assertEqual(r.reroll_status, "consider")

    def test_offered_upgrade_raises_max_lower_bound(self):
        from src.gamedata import load_game_data
        d = load_game_data()
        d.note_level_seen("ball", 4)
        self.assertEqual(d.max_level("ball"), 4)
        self.assertFalse(d.max_level_known("ball"))
        d.set_observed_max_level("ball", 5)
        d.note_level_seen("ball", 6)          # 확정 뒤에는 바뀌지 않는다
        self.assertEqual(d.max_level("ball"), 5)


if __name__ == "__main__":
    unittest.main()
