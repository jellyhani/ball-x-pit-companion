"""합성 성숙 덱: 새 재료 두 칸의 성장 경로, 비용, 합법적인 단독 재료와 생존 우선."""
import unittest
from dataclasses import replace

from src.domain import CardLabel, FuserCombo, FuserOptions, InventorySlot, RunProgress
from src.engine.fusion import FusionAdvisor
from src.engine.recommender import Recommender, card_verdict
from src.gamedata import GameData, Item, Recipe
from src.tracking.meta_state import MetaState
from src.tracking.run_state import RunState, Owned
from tests.helpers import card, session


def fixture():
    ids=('spark','toxin','split','heavy','laser','wind','rod','beam','cloud','carry','carry2','shade')
    items={'ball:'+i:Item('ball:'+i,'ball',i,i,i,'',wiki_damage=('AOE',) if i in
                         ('spark','rod','beam','carry','carry2') else ()) for i in ids}
    items['passive:staff']=Item('passive:staff','passive','staff','지팡이','staff','')
    items['passive:armor']=Item('passive:armor','passive','armor','갑옷','armor','')
    recipes=[Recipe('ball:rod',('ball:spark','ball:heavy'),'game'),
             Recipe('ball:beam',('ball:split','ball:laser'),'game'),
             Recipe('ball:cloud',('ball:toxin','ball:wind'),'game')]
    data=GameData(items,{},recipes,{'max_level':{'ball':3,'passive':3,'verified':True}}, {}, {})
    data.level_props={i:[{'kMinDamage':1,'kMaxDamage':10}]*3 for i in items}
    data.level_props['ball:spark']=[{'kMinLightningDamage':1,'kMaxLightningDamage':20,'kLightningLimit':3}]*3
    data.level_props['ball:split']=[{'kCellLimit':n} for n in (2,3,4)]
    data.level_props['ball:cloud']=[{'kMinPoisonDamage':2,'kMaxPoisonDamage':5,'kMaxPoisonStacks':8}]*3
    data.level_props['passive:staff']=[{'kAOEDmgPct':x} for x in (20,35,50)]
    data.level_props['passive:armor']=[{'kReduceDmgPct':x} for x in (10,20,30)]
    data.available={'ball:'+i for i in ids[:6]}
    records={i:{'obtained':1,'available':i in data.available,'in_game':True,'merged':i not in data.available,'combos':{}}
             for i in items if i.startswith('ball:')}
    meta=MetaState(discovery=records)
    run=RunState(phase='in_run',inventory_seen=True,owned={
        'ball:carry':Owned('ball:carry','ball',3,'game',at_max=False,combined=('ball:shade',)),
        'ball:carry2':Owned('ball:carry2','ball',3,'game',at_max=False,combined=('ball:wind',)),
        'passive:staff':Owned('passive:staff','passive',3,'game')})
    progress=RunProgress(endless=True,health=100,max_health=100,max_balls=4,balls=2,max_passives=4,passives=1)
    s=session([card(n,'ball:'+i) for n,i in enumerate(('spark','toxin','split'))],progress=progress)
    engine=Recommender(data);engine.meta=meta
    return data,meta,run,s,engine


class GrowthPlanTest(unittest.TestCase):
    def test_mature_deck_gets_growth_pick_and_costs_instead_of_blank_hold(self):
        d,m,r,s,e=fixture();rec=e.recommend(s,r)
        self.assertIsNotNone(rec.best)
        self.assertIsNotNone(rec.growth_plan)
        self.assertNotEqual(rec.status,'hold')
        self.assertEqual(rec.growth_plan.upgrades,4)
        self.assertEqual(len(rec.growth_plan.missing),1)
        self.assertIn('50%',rec.growth_plan.benefit)
        self.assertTrue(all('endless_unlinked' not in [w.rule_id for w in ev.warnings]
                            for ev in rec.evals if ev.growth_plan))
        self.assertTrue(any(card_verdict(rec,ev)=='alt' for ev in rec.evals if ev is not rec.best))

    def test_fused_carriers_and_absorbed_effects_are_not_future_materials(self):
        d,m,r,s,e=fixture();rec=e.recommend(s,r)
        for ev in rec.evals:
            if ev.growth_plan:
                self.assertFalse(set(ev.growth_plan.ingredients)&{'ball:carry','ball:carry2','ball:shade'})
                if 'ball:wind' in ev.growth_plan.ingredients:
                    self.assertIn('ball:wind',ev.growth_plan.missing)

    def test_last_empty_slot_cannot_promise_a_second_new_material(self):
        d,m,r,s,e=fixture();s.progress=replace(s.progress,max_balls=3)
        rec=e.recommend(s,r)
        self.assertIsNone(rec.growth_plan)
        self.assertTrue(all(ev.growth_plan is None for ev in rec.evals))

    def test_available_solo_partner_can_use_last_empty_slot(self):
        d,m,r,s,e=fixture()
        r.owned['ball:laser']=Owned('ball:laser','ball',3,'game',at_max=True)
        s.progress=replace(s.progress,balls=3)
        rec=e.recommend(s,r)
        # 단독 재료를 이미 가진 경우에는 기존 직접 진화 정책을 유지한다.
        self.assertIsNone(rec.growth_plan)
        self.assertEqual(rec.best.card.item_id,'ball:split')

    def test_locked_and_banished_future_partner_cannot_be_promised(self):
        d,m,r,s,e=fixture();s.progress=replace(s.progress,banished=('ball:heavy','ball:laser','ball:wind'))
        for target in ('ball:heavy','ball:laser','ball:wind'):m.discovery[target]['available']=False
        rec=e.recommend(s,r)
        for ev in rec.evals:
            if ev.growth_plan:self.assertFalse(set(ev.growth_plan.missing)&set(s.progress.banished))

    def test_current_urgent_survival_wins_over_future_growth(self):
        d,m,r,s,e=fixture();s.progress=replace(s.progress,health=20)
        s.cards=(*s.cards[:2],card(2,'passive:armor'))
        rec=e.recommend(s,r)
        self.assertEqual(rec.best.card.item_id,'passive:armor')
        self.assertIsNone(rec.growth_plan)

    def test_unknown_game_maximum_and_early_deck_keep_previous_policy(self):
        d,m,r,s,e=fixture();d.rules['max_level']['verified']=False
        self.assertIsNone(e.recommend(s,r).growth_plan)
        d,m,r,s,e=fixture();s.progress=replace(s.progress,endless=False)
        r.owned={'ball:spark':Owned('ball:spark','ball',1,'game')}
        self.assertIsNone(e.recommend(s,r).growth_plan)

    def test_slot_specific_solo_selection_excludes_only_fused_copy(self):
        d,m,r,s,e=fixture()
        r.apply_inventory((InventorySlot(0,(0,0,0,0),True,'ball:spark',3,at_max=False,combined=('ball:shade',)),
                           InventorySlot(1,(0,0,0,0),True,'ball:spark',2,at_max=False)),d)
        self.assertEqual(r.solo_balls()['ball:spark'].level,2)
        self.assertEqual(r.solo_balls()['ball:spark'].copies,1)

    def test_stale_fuser_candidates_cannot_recombine_already_fused_ball(self):
        d,m,r,s,e=fixture()
        inv=(InventorySlot(0,(0,0,0,0),True,'ball:carry',3,at_max=False,combined=('ball:shade',)),
             InventorySlot(1,(0,0,0,0),True,'ball:spark',3,at_max=True))
        fz=FuserOptions(combos=(FuserCombo('ball:carry','ball:spark',0,1),))
        rec=FusionAdvisor(d).recommend(fz,inv,r)
        self.assertIsNone(rec.best)
        self.assertFalse(rec.combos[0].selectable)

    def test_discovery_override_does_not_show_conflicting_growth_goal(self):
        from PySide6.QtWidgets import QApplication
        from src.ui.hud import RecommendationHud
        app=QApplication.instance() or QApplication([])
        d,m,r,s,e=fixture();rec=e.recommend(s,r)
        old_goal=rec.growth_plan.summary
        rec.growth_plan=None
        rec.discovery_text='미발견 다른 목표'
        hud=RecommendationHud(d)
        try:
            hud.show_recommendation(rec,2)
            shown=[label.text() for label in hud.lines]
            self.assertIn(rec.discovery_text,shown)
            self.assertNotIn(old_goal,shown)
        finally:
            hud.close()
