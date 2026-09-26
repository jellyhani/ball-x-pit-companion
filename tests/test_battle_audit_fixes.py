"""전투 감사 회귀: 게임 상태→선택 세션, 목표 방향, 진화 재료·효과·관측을 구분한다.

게임 설치 자료나 사용자 기록 없이 합성 데이터만 쓴다.
"""
import copy
import os
import tempfile
import unittest

from src.domain import (Card, CardLabel, ChoicePool, ChoiceSession, FrameInfo, FuserCombo, FuserEvo,
                        FuserOptions, InventorySlot, RunProgress)
from src.engine.deck_plan import build_plan
from src.engine.expedition import advise
from src.engine.fusion import FusionAdvisor
from src.engine.passive_value import ball_effect, passive_effect
from src.engine.recommender import Recommender
from src.gamedata import Character, GameData, Item, Recipe, apply_catalog
from src.tracking.bridge_adapter import convert
from src.tracking.choice_tracker import ChoiceTracker
from src.tracking.draw_stats import DrawStats
from src.tracking.run_state import RunState


def data():
    ids = ('ball:burn', 'ball:stone', 'ball:brimstone', 'ball:bleed', 'ball:heavy', 'ball:hemorrhage',
           'ball:freeze', 'ball:laserhorz', 'ball:laservert', 'ball:freezeray', 'ball:vampire', 'ball:final',
           'passive:babyrattle', 'passive:bandageroll', 'passive:healereffigy', 'passive:armor',
           'passive:a', 'passive:b', 'passive:c', 'passive:d', 'passive:result')
    items = {i: Item(i, i.split(':')[0], i.split(':')[1], i, i, '') for i in ids}
    chars = {i: Character(i, i.split(':')[1], i, i, '') for i in ('char:default', 'char:emptynester')}
    recipes = [Recipe('ball:brimstone', ('ball:burn', 'ball:stone'), 'game'),
               Recipe('ball:hemorrhage', ('ball:bleed', 'ball:heavy'), 'game'),
               Recipe('ball:freezeray', ('ball:freeze', 'ball:laserhorz'), 'game'),
               Recipe('ball:freezeray', ('ball:freeze', 'ball:laservert'), 'game'),
               Recipe('ball:final', ('ball:hemorrhage', 'ball:freezeray'), 'game'),
               Recipe('passive:result', ('passive:a', 'passive:b', 'passive:c', 'passive:d'), 'game')]
    rules = {'max_level': {'ball': 3, 'passive': 3, 'verified': True},
             'tags': {'heal_source': {'items': ['ball:vampire', 'passive:healereffigy']},
                      'needs_healing': {'items': ['passive:bandageroll']},
                      'baby_ball_source': {'items': ['passive:babyrattle']}},
             'characters': {'char:emptynester': {'reduces_tags': ['baby_ball_source'], 'reason': '베이비볼 없음'}}}
    return GameData(items, chars, recipes, rules, {}, {})


def inv(*ids, combined=()):
    return tuple(InventorySlot(n, (0, 0, 0, 0), True, iid, 1, at_max=False,
                               combined=combined if n == 0 else ()) for n, iid in enumerate(ids))


def session(*ids, sid=1, progress=None, upgrade=False, pool=None):
    cards = tuple(Card(n, str(n), (0, 0, 10, 10), item_id=i,
                       label=CardLabel.UPGRADE if upgrade else CardLabel.NEW,
                       shown_level=2 if upgrade else None) for n, i in enumerate(ids))
    return ChoiceSession(sid, 0, cards, FrameInfo(1, 0, (0, 0), (1920, 1080)),
                         progress=progress, pool=pool)


class BattleAuditFixTest(unittest.TestCase):
    def setUp(self):
        self.d = data()
        self.run = RunState()
        self.run.start_run()
        self.run.apply_inventory(inv('ball:burn'), self.d)

    def test_combined_character_survives_open_and_update_session(self):
        snap = {'game_state': 'kLevelUp',
                'battle': {'char': 'kDefault', 'chars_combined': ['kEmptyNester'], 'balls': [], 'passives': []},
                'levelup': {'type': 'kNormal', 'choices': [
                    {'idx': 0, 'type': 'kBabyRattle', 'kind': 'kPassive', 'is_new': True}]}}
        tracker = ChoiceTracker()
        obs = convert(snap, self.d).observation
        opened = tracker.observe(obs, 1, immediate=True)[0].session
        self.run.apply_character(opened.character_id, opened.extra_characters, source='game')
        rec = Recommender(self.d).recommend(opened, self.run)
        self.assertEqual(self.run.character_ids, ['char:default', 'char:emptynester'])
        self.assertIn('char_reduces', [w.rule_id for w in rec.evals[0].warnings])
        snap['battle']['chars_combined'] = []
        updated = tracker.observe(convert(snap, self.d).observation, 2, immediate=True)[0].session
        self.assertEqual(updated.extra_characters, ())

    def test_target_changes_choice_order_without_inventing_performance_score(self):
        s = session('ball:bleed', 'ball:stone', progress=RunProgress(balls=1, max_balls=4))
        engine = Recommender(self.d)
        before = engine.recommend(s, self.run)
        self.assertEqual(before.best.card.item_id, 'ball:stone')
        self.run.lock_target('ball:hemorrhage', self.d)
        after = engine.recommend(s, self.run)
        self.assertEqual(after.best.card.item_id, 'ball:bleed')
        self.assertEqual(after.ranked[0].card.item_id, 'ball:bleed')
        self.assertEqual([x.score for x in before.evals], [x.score for x in after.evals])
        self.assertEqual(after.confidence, '추천')
        self.assertTrue(after.plan_locked)
        self.assertIn('고정 목표', after.plan_text)
        self.assertEqual(after.reroll_status, 'keep')

    def test_low_health_can_take_healing_before_target(self):
        self.run.lock_target('ball:hemorrhage', self.d)
        s = session('ball:bleed', 'ball:vampire', progress=RunProgress(health=20, max_health=100, balls=1, max_balls=4))
        rec = Recommender(self.d).recommend(s, self.run)
        self.assertEqual(rec.best.card.item_id, 'ball:vampire')
        self.assertIn('target_survival', [r.rule_id for r in rec.best.reasons])

    def test_target_traces_intermediate_evolution_and_alternative_owned_recipe(self):
        self.run.apply_inventory(inv('ball:bleed', 'ball:laservert'), self.d)
        self.run.lock_target('ball:final', self.d)
        plan = build_plan(self.run, self.d, None)
        self.assertEqual(plan.locked_wanted, {'ball:heavy', 'ball:freeze'})
        self.assertEqual(plan.locked_core, {'ball:bleed', 'ball:laservert'})
        rec = Recommender(self.d).recommend(session('ball:bleed', upgrade=True), self.run)
        self.assertTrue(rec.best.target_step)

    def test_banished_or_locked_material_cannot_be_target_step(self):
        self.run.lock_target('ball:hemorrhage', self.d)
        plan = build_plan(self.run, self.d, RunProgress(banished=('ball:heavy',)))
        self.assertEqual(plan.locked_wanted, set())
        self.d.available = {'ball:burn', 'ball:bleed'}
        self.assertFalse(build_plan(self.run, self.d, None).locked)

    def test_target_does_not_bypass_full_slot_or_character_restriction(self):
        self.d.apply_game_recipes([('passive:result', ('passive:babyrattle', 'passive:armor'))])
        self.run.lock_target('passive:result', self.d)
        self.run.apply_character('char:emptynester')
        rec = Recommender(self.d).recommend(session('passive:babyrattle'), self.run)
        self.assertFalse(rec.evals[0].target_step)
        self.run.apply_character('char:default')
        rec = Recommender(self.d).recommend(session('passive:armor', progress=RunProgress(passives=4, max_passives=4)), self.run)
        self.assertFalse(rec.evals[0].target_step)

    def test_evolution_uses_actual_alternative_ingredients(self):
        slots = inv('ball:freeze', 'ball:laservert')
        self.run.apply_inventory(slots, self.d)
        rec = FusionAdvisor(self.d).recommend(FuserOptions(evos=(FuserEvo('ball:freezeray', 0, 7),)), slots, self.run)
        self.assertEqual(rec.best.parts, ('ball:freeze', 'ball:laservert'))
        self.assertNotIn('ball:laserhorz', rec.best.detail)

    def test_ambiguous_recipe_does_not_invent_material_damage_or_heal_loss(self):
        slots = inv('ball:freeze', 'ball:laservert', 'ball:laserhorz')
        self.run.apply_inventory(slots, self.d)
        rec = FusionAdvisor(self.d).recommend(FuserOptions(evos=(FuserEvo('ball:freezeray', 0),)), slots, self.run)
        self.assertEqual(rec.best.parts, ())
        self.assertIn('evo_parts_unknown', [w.rule_id for w in rec.best.warnings])
        self.assertFalse(any(r.rule_id in ('evo_dmg_up', 'fz_run_share') for r in rec.best.reasons))

    def test_four_passive_evolution_counts_actual_slots(self):
        slots = inv('passive:a', 'passive:b', 'passive:c', 'passive:d')
        rec = FusionAdvisor(self.d).recommend(FuserOptions(evos=(FuserEvo('passive:result', 0),)), slots, self.run)
        base = next(r for r in rec.best.reasons if r.rule_id == 'evo_base')
        self.assertIn('4개', base.text)
        self.assertIn('3칸', base.text)

    def test_unknown_evolution_and_combo_are_not_recommended(self):
        fz = FuserOptions(evos=(FuserEvo(None, 0),), combos=(FuserCombo(None, 'ball:burn', 0, 1, 999),))
        rec = FusionAdvisor(self.d).recommend(fz, (), self.run)
        self.assertEqual(rec.status, 'hold')
        self.assertIsNone(rec.best)
        known = FuserCombo('ball:burn', 'ball:freeze', 0, 1)
        rec = FusionAdvisor(self.d).recommend(FuserOptions(combos=(*fz.combos, known)), (), self.run)
        self.assertEqual(rec.best.parts, ('ball:burn', 'ball:freeze'))

    def test_fuser_candidate_indices_survive_bridge(self):
        snap = {'game_state': 'kLevelUp', 'levelup': {'type': 'kFuser', 'fuser': {
            'evos': [{'type': 'kFreezeRay', 'equip_idx': 2, 'evo_idx': 7}]}}}
        e = convert(snap, self.d).observation.fuser.evos[0]
        self.assertEqual((e.equip_idx, e.evo_idx), (2, 7))

    def test_upgrading_current_healer_does_not_claim_healing_gap(self):
        self.run.apply_inventory(inv('ball:vampire'), self.d)
        rec = Recommender(self.d).recommend(session('ball:vampire', upgrade=True), self.run)
        self.assertNotIn('heal_gap', [r.rule_id for r in rec.evals[0].reasons])

    def test_combined_healing_works_but_is_not_a_separate_recipe_material(self):
        self.run.apply_inventory(inv('ball:stone', combined=('ball:vampire',)), self.d)
        rec = Recommender(self.d).recommend(session('passive:bandageroll'), self.run)
        self.assertNotIn('heal_needed', [w.rule_id for w in rec.evals[0].warnings])
        self.assertNotIn('ball:vampire', self.run.owned)
        self.assertIn('회복 수단 보유', advise(self.run, self.d, RunProgress(health=80, max_health=100)).reasons)

    def test_healing_passive_prevents_false_unique_healer_loss(self):
        self.d.apply_game_recipes([('ball:brimstone', ('ball:vampire', 'ball:stone'))])
        slots = inv('ball:vampire', 'ball:stone', 'passive:healereffigy')
        rec = FusionAdvisor(self.d).recommend(FuserOptions(evos=(FuserEvo('ball:brimstone', 0),)), slots, self.run)
        self.assertNotIn('evo_lose_heal', [w.rule_id for w in rec.best.warnings])

    def test_same_healing_effect_combined_elsewhere_is_not_lost(self):
        self.d.apply_game_recipes([('ball:brimstone', ('ball:vampire', 'ball:stone'))])
        slots = inv('ball:burn', 'ball:vampire', 'ball:stone', combined=('ball:vampire',))
        rec = FusionAdvisor(self.d).recommend(FuserOptions(evos=(FuserEvo('ball:brimstone', 0),)), slots, self.run)
        self.assertNotIn('evo_lose_heal', [w.rule_id for w in rec.best.warnings])

    def test_each_distinct_choice_session_counts_even_with_same_card_ids(self):
        pool = ChoicePool(new_balls=('ball:stone',), num_choices=1)
        a, b = session('ball:burn', sid=1, pool=pool), session('ball:burn', sid=2, pool=pool, upgrade=True)
        with tempfile.TemporaryDirectory() as tmp:
            stats = DrawStats(os.path.join(tmp, 'draws.jsonl'))
            for s in (a, b, a):
                stats.record(s)
                self.run.note_offered(tuple(c.item_id for c in s.cards), session_id=s.session_id)
            self.assertEqual(stats.count, 2)
            self.assertEqual(self.run.offered['ball:burn'], 2)
        self.run.start_run()
        self.run.note_offered(('ball:burn',), session_id=1)
        self.assertEqual(self.run.offered['ball:burn'], 1)

    def test_cached_and_live_catalog_use_identical_tag_priority(self):
        item = Item('ball:burn', 'ball', 'burn', '화상', 'Burn', '화상을 입힘')
        a = GameData({item.id: item}, {}, [], {}, {}, {})
        b = copy.deepcopy(a)
        props = [{'kMinBurnDamage': 4, 'kAOERadius': 2}]
        catalog = {'balls': [{'type': 'kBurn', 'lvl_props': props}], 'passives': [],
                   'levels': [{'type': 'kSnowy', 'boss_turns': [10, 20], 'fuser_turns': [5]}]}
        apply_catalog(a, catalog)
        b.apply_level_props({item.id: props})
        self.assertEqual(a.status_tags(item.id), b.status_tags(item.id))
        self.assertEqual(a.level_schedules['kSnowy'], ((10, 20), (5,)))
        apply_catalog(a, catalog)
        self.assertEqual(a.status_tags(item.id), b.status_tags(item.id))

    def test_uninterpreted_change_is_not_claimed_to_have_no_effect(self):
        props = [{'kMinDamage': 4, 'kMaxDamage': 8, 'kUnknownPct': 10},
                 {'kMinDamage': 4, 'kMaxDamage': 8, 'kUnknownPct': 20}]
        self.assertNotEqual(ball_effect(props, 1, 2), '수치 변화 없음')
        self.assertIsNone(passive_effect(props, 1, 2).gain)
        self.assertEqual(ball_effect([props[0], props[0]], 1, 2), '수치 변화 없음')


if __name__ == '__main__':
    unittest.main()
