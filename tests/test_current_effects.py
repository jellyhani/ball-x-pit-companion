"""게임 실효값 전달, 레벨 대응, 원본/실효 혼용 금지를 확인한다."""
import copy
import unittest
from types import SimpleNamespace as NS
from src.domain import Card
from src.engine.current_effects import level_rows,is_current
from src.engine.passive_value import passive_effect
from src.engine.recommender import Recommender,ActionEval
from src.tracking.bridge_adapter import _effective


class CurrentEffectsTest(unittest.TestCase):
    def card(self,effective):
        return Card(0,'왼쪽',(0,0,1,1),effective=effective)

    def test_current_damage_reaches_recommendation_without_replacing_catalog(self):
        raw=[{'kMinLightningDamage':1,'kMaxLightningDamage':20}];original=copy.deepcopy(raw)
        card=self.card(_effective({'scope':'current_run_uncombined','level':1,
                                  'after':{'kMinLightningDamage':3,'kMaxLightningDamage':61}}))
        rec=object.__new__(Recommender);rec.data=NS(level_props={'ball:lightning':raw})
        ev=ActionEval(card,'new_ball',None,1)
        rec._ball_effect(ev,'ball:lightning',False)
        self.assertIn('3~61',ev.effect)
        self.assertIn('현재 능력치 반영',ev.effect)
        self.assertEqual(raw,original)

    def test_effective_upgrade_delta_uses_effective_before_and_after(self):
        raw=[{'kReduceDmgPct':10},{'kReduceDmgPct':20}]
        card=self.card(_effective({'scope':'current_run_uncombined','level':2,
                       'before':{'kReduceDmgPct':20},'after':{'kReduceDmgPct':30}}))
        self.assertEqual(passive_effect(level_rows(raw,card,1,2),1,2).gain,.5)

    def test_missing_before_wrong_level_or_scope_does_not_mix_raw_and_effective(self):
        raw=[{'kReduceDmgPct':10},{'kReduceDmgPct':20}]
        value={'scope':'current_run_uncombined','level':2,'after':{'kReduceDmgPct':30}}
        self.assertIs(level_rows(raw,self.card(value),1,2),raw)
        self.assertIs(level_rows(raw,self.card(value),None,1),raw)
        self.assertIsNone(_effective(dict(value,scope='combined')))
        self.assertIsNone(_effective(dict(value,after={'kReduceDmgPct':True})))
