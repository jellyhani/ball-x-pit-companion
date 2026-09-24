"""캐릭터 궁합 (공식 설명에서 끌어낸 프로필)과 캐릭터별 내 기록."""
import unittest

from src.domain import CardLabel
from src.engine.recommender import Recommender
from src.tracking.run_history import RunRecord
from src.tracking.run_state import RunState
from tests.helpers import card, game_data, session, slots


def evaluate(char_id, cards, owned=(), history=()):
    d = game_data()
    run = RunState()
    run.start_run()
    if owned:
        run.apply_inventory(slots(*owned), d)
    run.apply_character(char_id)
    r = Recommender(d)
    r.history = list(history)
    return r.recommend(session(cards), run)


class CharStrategyTest(unittest.TestCase):
    def test_every_profile_names_known_keys(self):
        d = game_data()
        known = {"status", "aoe", "direct", "crit", "speed", "defense", "baby", "sustain", "new_ball", "upgrade_ball",
                 "passive", "power"}
        for cid, rule in d.rules["characters"].items():
            fav = rule.get("strategy", {}).get("favor", {})
            self.assertTrue(set(fav) <= known, (cid, fav))
            self.assertIn(cid, d.characters)

    def test_wimp_prefers_aoe_balls(self):
        rec = evaluate("char:wimp", [card(0, "ball:earthquake"), card(1, "ball:bleed")])
        eq = next(e for e in rec.evals if e.card.item_id == "ball:earthquake")
        self.assertIn("char_fit", [r.rule_id for r in eq.reasons])

    def test_backpacker_prefers_new_balls(self):
        rec = evaluate("char:backpacker", [card(0, "ball:bleed")])
        self.assertIn("char_fit", [r.rule_id for r in rec.evals[0].reasons])

    def test_history_reason_needs_enough_runs(self):
        hist = [RunRecord(0, char="char:shade", result="보스 격퇴", balls=[("ball:bleed", 3)]) for _ in range(3)]
        hist += [RunRecord(0, char="char:shade", result="실패", balls=[("ball:burn", 3)]) for _ in range(3)]
        rec = evaluate("char:shade", [card(0, "ball:bleed", CardLabel.NEW)], history=hist)
        ids = [r.rule_id for r in rec.evals[0].reasons]
        self.assertIn("char_record_good", ids)
        rec2 = evaluate("char:shade", [card(0, "ball:bleed", CardLabel.NEW)], history=hist[:2])
        self.assertNotIn("char_record_good", [r.rule_id for r in rec2.evals[0].reasons])


if __name__ == "__main__":
    unittest.main()
