"""선택 세션 추적 (합성 관측 입력)."""
import unittest

from src.domain import CardLabel, Click, ScreenKind, ScreenObservation
from src.tracking.choice_tracker import ChoiceTracker
from tests.helpers import card, frame, levelup_obs

A = [card(0, "ball:burn", CardLabel.UPGRADE, 2), card(1, "ball:stone"), card(2, "ball:earthquake")]
B = [card(0, "ball:bleed"), card(1, "ball:heavy"), card(2, "passive:magnet")]
OTHER = ScreenObservation(ScreenKind.OTHER, frame=frame())


class ChoiceTrackerTest(unittest.TestCase):
    def open(self, tr, cards=A, t=0.0, **kw):
        tr.observe(levelup_obs(cards, **kw), t)
        ev = tr.observe(levelup_obs(cards, **kw), t + 0.7)
        self.assertEqual([e.kind for e in ev], ["opened"])
        return ev[0].session

    def test_needs_two_matching_frames(self):
        tr = ChoiceTracker()
        self.assertEqual(tr.observe(levelup_obs(A), 0.0), [])
        self.assertTrue(tr.awaiting_confirmation)
        self.assertEqual(tr.observe(levelup_obs(A), 0.7)[0].kind, "opened")

    def test_forced_scan_opens_immediately(self):
        self.assertEqual(ChoiceTracker().observe(levelup_obs(A), 0.0, forced=True)[0].kind, "opened")

    def test_partial_recognition_is_refined_not_replaced(self):
        tr = ChoiceTracker()
        partial = [A[0], card(1, None), A[2]]
        s = self.open(tr, partial)
        ev = tr.observe(levelup_obs(A), 1.5)
        self.assertEqual(ev[0].kind, "updated")
        self.assertEqual(s.session_id, ev[0].session.session_id)
        self.assertEqual(ev[0].session.cards[1].item_id, "ball:stone")

    def test_new_cards_replace_session_atomically(self):
        # 3장 → 2장만 인식된 새 선택창: 이전 세 번째 카드가 남으면 안 된다
        tr = ChoiceTracker()
        self.open(tr, A)
        two = [card(0, "ball:bleed"), card(1, "ball:heavy")]
        tr.observe(levelup_obs(two), 2.0)
        ev = tr.observe(levelup_obs(two), 2.7)
        self.assertEqual([e.kind for e in ev], ["closed", "opened"])
        self.assertEqual([c.item_id for c in ev[1].session.cards], ["ball:bleed", "ball:heavy"])

    def test_close_needs_two_misses(self):
        tr = ChoiceTracker()
        self.open(tr)
        self.assertEqual(tr.observe(OTHER, 2.0), [])
        self.assertEqual(tr.observe(OTHER, 2.7)[0].kind, "closed")

    def test_capture_failure_does_not_close(self):
        tr = ChoiceTracker()
        self.open(tr)
        for t in (2.0, 2.7, 3.4):
            self.assertEqual(tr.observe(ScreenObservation(ScreenKind.CAPTURE_FAILED), t), [])
        self.assertIsNotNone(tr.session)

    def test_click_on_card_is_pick_evidence(self):
        tr = ChoiceTracker()
        s = self.open(tr)
        x, y, w, h = s.cards[1].rect
        tr.add_click(Click(x + w // 2, y + h // 2, 1.0))
        tr.observe(OTHER, 2.0)
        out = tr.observe(OTHER, 2.7)[0].outcome
        self.assertEqual((out.kind, out.card.item_id), ("picked", "ball:stone"))

    def test_click_respects_window_origin(self):
        # 게임 창이 두 번째 모니터(음수 좌표)에 있어도 클릭을 카드에 맞게 대응시킨다
        tr = ChoiceTracker()
        cards = A
        obs = levelup_obs(cards, frame=frame(origin=(-1920, 100)))
        tr.observe(obs, 0.0)
        s = tr.observe(obs, 0.7)[0].session
        x, y, w, h = s.cards[2].rect
        tr.add_click(Click(-1920 + x + 5, 100 + y + 5, 1.0))
        tr.observe(OTHER, 2.0)
        self.assertEqual(tr.observe(OTHER, 2.7)[0].outcome.card.item_id, "ball:earthquake")

    def test_no_evidence_means_unknown_not_top_pick(self):
        tr = ChoiceTracker()
        self.open(tr)
        tr.observe(OTHER, 2.0)
        self.assertEqual(tr.observe(OTHER, 2.7)[0].outcome.kind, "unknown")

    def test_old_click_is_ignored(self):
        tr = ChoiceTracker()
        tr.add_click(Click(100, 700, -30.0))   # 선택창이 열리기 한참 전 클릭
        self.open(tr)
        tr.observe(OTHER, 2.0)
        self.assertEqual(tr.observe(OTHER, 2.7)[0].outcome.kind, "unknown")

    def test_reroll_click_and_gold_drop(self):
        tr = ChoiceTracker()
        s = self.open(tr, gold=12, reroll_cost=5, reroll_rect=(35, 965, 860, 80))
        tr.add_click(Click(400, 1000, 1.0))
        tr.observe(levelup_obs(B, gold=7, reroll_cost=5), 2.0)
        ev = tr.observe(levelup_obs(B, gold=7, reroll_cost=5), 2.7)
        self.assertEqual(ev[0].outcome.kind, "rerolled")
        self.assertEqual(ev[0].outcome.evidence, "새로고침 비용만큼 골드 감소")

    def test_log_reroll_invalidates_and_discards_late_results(self):
        tr = ChoiceTracker()
        self.open(tr)
        gen = tr.generation
        ev = tr.note_reroll(5.0)
        self.assertEqual(ev[0].outcome.kind, "rerolled")
        self.assertIsNone(tr.session)
        self.assertGreater(tr.generation, gen)   # 이전 세대로 요청한 인식 결과는 컨트롤러가 버린다
        # 새로고침 직후 사라지는 중인 옛 카드 프레임으로 다시 열리지 않는다
        tr.observe(levelup_obs(A), 5.3)
        self.assertEqual(tr.observe(levelup_obs(A), 5.9), [])

    def test_log_reroll_after_new_session_is_ignored(self):
        tr = ChoiceTracker()
        self.open(tr, t=10.0)
        self.assertEqual(tr.note_reroll(10.5), [])

    def test_reset_closes_open_session(self):
        tr = ChoiceTracker()
        self.open(tr)
        ev = tr.reset()
        self.assertEqual(ev[0].outcome.kind, "unknown")
        self.assertIsNone(tr.session)


if __name__ == "__main__":
    unittest.main()
