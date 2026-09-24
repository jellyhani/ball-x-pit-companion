"""커뮤니티 평가(티어·캐릭터 추천 빌드) 반영 — 게임 자료가 있을 때만."""
import unittest

from tests import HAS_GAME_DATA

if HAS_GAME_DATA:
    from src.engine.recommender import Recommender
    from src.gamedata import load_game_data
    from src.tracking.run_state import RunState
    from tests.helpers import card, session, slots


@unittest.skipUnless(HAS_GAME_DATA, "게임 자료 없음")
class CommunityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = load_game_data()

    def run_with(self, *owned, char=None):
        run = RunState()
        run.start_run()
        run.apply_inventory(slots(*[(i, 1) for i in owned]), self.d)
        if char:
            run.set_character(char)
        return run

    def evals(self, run, *ids):
        r = Recommender(self.d).recommend(session([card(i, x) for i, x in enumerate(ids)]), run)
        return {e.card.item_id: e for e in r.evals}

    def rules(self, ev):
        return {x.rule_id for x in ev.reasons} | {x.rule_id for x in ev.warnings}

    def test_tiers_map_all_names(self):
        for key in ("ball_tiers", "passive_tiers"):
            for names in self.d.community[key].values():
                for n in names:
                    self.assertIsNotNone(self.d.item_by_english(n), n)
        self.assertEqual(self.d.community_tier("ball:sun"), "S")

    def test_evo_tier_only_with_owned_partner(self):
        # 독을 가졌으면 지진은 늪(S) 진화, 짝 재료가 없으면(빛) 진화 티어 보너스 없음
        ev = self.evals(self.run_with("ball:poison"), "ball:earthquake")["ball:earthquake"]
        self.assertIn("community_evo_tier", self.rules(ev))
        ev = self.evals(self.run_with("ball:light"), "ball:earthquake")["ball:earthquake"]
        self.assertNotIn("community_evo_tier", self.rules(ev))

    def test_low_tier_not_warned_when_it_leads_to_good_evolution(self):
        ev = self.evals(self.run_with("ball:burn"), "ball:stone")["ball:stone"]   # 돌 D, 그러나 빙하 S 재료
        self.assertNotIn("community_tier_low", self.rules(ev))

    def test_character_build_core(self):
        ev = self.evals(self.run_with("ball:burn", char="char:shade"), "ball:lasercross")["ball:lasercross"]
        self.assertIn("char_build", self.rules(ev))
        ev = self.evals(self.run_with("ball:burn", char="char:tiptoer"), "ball:lasercross")["ball:lasercross"]
        self.assertNotIn("char_build", self.rules(ev))


if __name__ == "__main__":
    unittest.main()
