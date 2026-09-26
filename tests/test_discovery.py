"""합성 백과사전 기록으로 미발견/미수신, 슬롯, 재료 경로와 모드 우선순위를 검증한다."""
import copy
import unittest
from types import SimpleNamespace as NS

from src.domain import CardLabel, RunProgress
from src.engine.discovery import apply_discovery, apply_fusion_discovery
from src.engine.recommender import ActionEval, Recommendation, Recommender
from src.gamedata import GameData, Item, Recipe
from src.tracking.meta_state import MetaState, parse_meta
from src.tracking.run_state import RunState, Owned
from tests.helpers import card, session


def fixture():
    names = {"a": "재료 가", "b": "재료 나", "c": "다른 볼", "x": "상위 볼", "z": "최상위 볼"}
    data = GameData({"ball:"+i: Item("ball:"+i, "ball", i, name, name, "") for i, name in names.items()},
                    {}, [Recipe("ball:x", ("ball:a", "ball:b"), "game")],
                    {"max_level": {"ball": 3, "verified": True}}, {}, {})
    records = {"ball:"+i: {"obtained": 1 if i in "abc" else 0, "in_game": True,
                            "available": i in "abc", "merged": i in "xz"} for i in names}
    run = RunState(owned={"ball:a": Owned("ball:a", "ball", 3, "game", at_max=True)}, inventory_seen=True)
    cards = [card(0, "ball:b"), card(1, "ball:c")]
    s = session(cards, progress=RunProgress(max_balls=4, balls=1))
    evals = [ActionEval(cards[0], "new_ball", level_after=1, score=1),
             ActionEval(cards[1], "new_ball", level_after=1, score=100)]
    rec = Recommendation(1, "test", "recommend", "", evals[1], evals, ranked=evals[::-1])
    return data, MetaState(discovery=records), run, s, rec


class DiscoveryTest(unittest.TestCase):
    def test_unseen_result_outranks_higher_combat_score_without_changing_scores(self):
        d,m,r,s,rec=fixture()
        out=apply_discovery(rec,s,r,d,m)
        self.assertEqual(out.best.card.item_id,"ball:b")
        self.assertEqual([e.score for e in out.evals],[1,100])
        self.assertIn("상위 볼",out.discovery_text)
        self.assertIn("재료 강화 2",out.discovery_text)
        self.assertEqual(out.confidence,"해금 우선")

    def test_known_result_or_unknown_metadata_does_not_become_false_unlock(self):
        for meta in (None, MetaState(), MetaState(discovery={})):
            d,_,r,s,rec=fixture();out=apply_discovery(rec,s,r,d,meta)
            self.assertEqual(out.best.card.item_id,"ball:c")
        d,m,r,s,rec=fixture();m.discovery['ball:x']['obtained']=1
        self.assertEqual(apply_discovery(rec,s,r,d,m).best.card.item_id,'ball:c')

    def test_full_slots_locked_partner_and_banished_partner_are_rejected(self):
        for case in ('slots','locked','banished'):
            d,m,r,s,rec=fixture()
            if case=='slots':s.progress=RunProgress(max_balls=1,balls=1)
            if case=='locked':m.discovery['ball:b']['available']=False
            if case=='banished':s.progress=RunProgress(max_balls=4,banished=('ball:b',))
            self.assertEqual(apply_discovery(rec,s,r,d,m).best.card.item_id,'ball:c',case)

    def test_upgrade_of_owned_material_advances_new_evolution(self):
        d,m,r,s,rec=fixture()
        r.owned['ball:b']=Owned('ball:b','ball',2,'game')
        s.cards=(card(0,'ball:b',CardLabel.UPGRADE,3),card(1,'ball:c'))
        rec.evals[0]=ActionEval(s.cards[0],'upgrade_ball',level_before=2,level_after=3,score=1)
        rec.ranked=rec.evals[::-1]
        out=apply_discovery(rec,s,r,d,m)
        self.assertEqual(out.best.card.item_id,'ball:b')
        self.assertIn('재료 강화 0',out.discovery_text)

    def test_combined_effect_is_not_treated_as_separate_ingredient(self):
        d,m,r,s,rec=fixture()
        r.owned={'ball:c':Owned('ball:c','ball',3,'game',combined=('ball:a',))}
        self.assertEqual(apply_discovery(rec,s,r,d,m).best.card.item_id,'ball:c')

    def test_unseen_fusion_pair_requires_complete_records_on_both_sides(self):
        d,m,r,s,rec=fixture();m.discovery['ball:x']['obtained']=1
        m.discovery['ball:a']['combos']={};m.discovery['ball:b']['combos']={}
        out=apply_discovery(rec,s,r,d,m)
        self.assertEqual(out.best.card.item_id,'ball:b');self.assertIn('미기록 융합',out.discovery_text)
        d,m,r,s,rec=fixture();m.discovery['ball:x']['obtained']=1
        m.discovery['ball:a']['combos']={'ball:b':1};m.discovery['ball:b']['combos']={}
        self.assertEqual(apply_discovery(rec,s,r,d,m).best.card.item_id,'ball:c')

    def test_multi_step_route_and_cycle_do_not_invent_missing_unlocked_parts(self):
        d,m,r,s,rec=fixture();m.discovery['ball:x']['obtained']=1
        d.recipes.append(Recipe('ball:z',('ball:x','ball:c'),'game'))
        out=apply_discovery(rec,s,r,d,m)
        self.assertIn('최상위 볼',out.discovery_text)
        d,m,r,s,rec=fixture();d.recipes=[Recipe('ball:x',('ball:x','ball:b'),'game')]
        self.assertEqual(apply_discovery(rec,s,r,d,m).best.card.item_id,'ball:c')

    def test_mode_off_keeps_normal_result(self):
        d,m,r,s,_=fixture();engine=Recommender(d);engine.meta=m
        before=engine.recommend(s,r)
        self.assertEqual(before.discovery_text,'')
        engine.discovery_mode=True
        self.assertEqual(engine.recommend(s,r).confidence,'해금 우선')
        engine.discovery_mode=False
        after=engine.recommend(s,r)
        self.assertEqual(after,before)

    def test_meta_parser_preserves_explicit_zero_and_missing_combo_distinction(self):
        d,_,_,_,_=fixture()
        meta=parse_meta({'discovery':{'kA':{'obtained':0,'combos':{'kB':2}},'kB':{'obtained':1}}},d)
        self.assertEqual(meta.discovery['ball:a']['obtained'],0)
        self.assertEqual(meta.discovery['ball:a']['combos'],{'ball:b':2})
        self.assertNotIn('combos',meta.discovery['ball:b'])

    def test_fuser_prioritizes_only_currently_offered_unseen_candidate(self):
        d,m,_,_,_=fixture()
        known=NS(selectable=True,result_id='ball:a',title='이미 발견',parts=('ball:a','ball:b'))
        new=NS(selectable=True,result_id='ball:x',title='상위 볼',parts=('ball:a','ball:b'))
        rec=NS(evos=[known,new],combos=[],best=known,status='recommend',headline='',notes=[])
        self.assertIs(apply_fusion_discovery(rec,m,d).best,new)
        new.selectable=False
        rec.best=known
        self.assertIs(apply_fusion_discovery(rec,m,d).best,known)
