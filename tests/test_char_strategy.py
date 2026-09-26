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

    def test_profiles_cite_sources(self):
        """성향은 추정 가중치라 근거(공식 설명·공략)를 같이 적어 둔다."""
        d = game_data()
        for cid, rule in d.rules["characters"].items():
            st = rule.get("strategy")
            if st:
                self.assertTrue(st.get("why") and st.get("source"), cid)
                self.assertTrue(all(1 <= v <= 5 for v in st["favor"].values()), cid)

    def test_sisyphus_aoe_ball_not_counted_twice(self):
        """시시포스 범위 볼은 전용 규칙(4배)만 — 성향(aoe)으로 한 번 더 올리지 않는다. 범위 패시브에는 성향이 간다."""
        from unittest import mock
        # 테스트 자료(fixture)에는 패시브 레벨 수치가 없어 역할을 못 정한다 — 게임 값(마법 지팡이 범위 피해 20%)을 넣어 줌
        with mock.patch.dict(game_data().level_props, {"passive:magicstaff": [{"kAOEDmgPct": 20}]}):
            rec = evaluate("char:sisyphus", [card(0, "ball:earthquake"), card(1, "passive:magicstaff")])
        ev = {e.card.item_id: [r.rule_id for r in e.reasons] for e in rec.evals}
        self.assertIn("char_sisyphus_boost", ev["ball:earthquake"])
        self.assertNotIn("char_fit", ev["ball:earthquake"])
        self.assertIn("char_fit", ev["passive:magicstaff"])

    def test_new_profiles_reach_cards(self):
        """보강한 캐릭터: 베이비볼형(노부부·고행자), 범위형(굴착가·매사냥꾼·지략가)."""
        for cid, good in (("char:cohabitants", "ball:broodmother"), ("char:flagellant", "ball:eggsac"),
                          ("char:tunneller", "ball:earthquake"), ("char:falconer", "ball:blizzard"),
                          ("char:tactician", "ball:earthquake")):
            rec = evaluate(cid, [card(0, good)])
            self.assertIn("char_fit", [r.rule_id for r in rec.evals[0].reasons], cid)

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
