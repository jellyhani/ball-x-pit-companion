"""사용자가 직접 고정한 덱 목표 (roadmap.build_roadmap include / deck_plan.DeckPlan.locked)."""
import unittest

from tests import HAS_GAME_DATA

if HAS_GAME_DATA:
    from src.engine.deck_plan import build_plan
    from src.engine.roadmap import browsable_targets, build_roadmap
    from src.gamedata import load_game_data
    from src.tracking.run_state import RunState


@unittest.skipUnless(HAS_GAME_DATA, "게임 자료 없음")
class DeckTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = load_game_data()
        cls.recipe = next(r for r in cls.d.recipes if len(r.ingredients) == 2)

    def test_browsable_targets_covers_all_results_once(self):
        """진화 결과마다 한 번씩 (냉동 광선 = 냉동 + 레이저 수평/수직 처럼 레시피가 둘이어도 목록엔 하나)."""
        results = [r.result for r in browsable_targets(self.d)]
        self.assertEqual(len(results), len(set(results)))
        self.assertEqual(set(results), {r.result for r in self.d.recipes})

    def test_forced_target_shows_up_with_zero_ingredients(self):
        run = RunState()
        run.start_run()
        entries = build_roadmap(run, self.d, include={self.recipe.result})
        entry = next((e for e in entries if e.recipe.result == self.recipe.result), None)
        self.assertIsNotNone(entry)
        self.assertEqual(sorted(entry.missing), sorted(self.recipe.ingredients))
        self.assertFalse(entry.levels_ready)          # 재료가 하나도 없으니 당연히 준비 안 됨

    def test_without_include_zero_ingredient_recipe_is_hidden(self):
        run = RunState()
        run.start_run()
        entries = build_roadmap(run, self.d)
        self.assertFalse(any(e.recipe.result == self.recipe.result for e in entries))

    def test_lock_target_puts_it_first_in_plan(self):
        run = RunState()
        run.start_run()
        run.lock_target(self.recipe.result, self.d)
        plan = build_plan(run, self.d, None)
        self.assertTrue(plan.locked)
        self.assertEqual(plan.targets[0].recipe.result, self.recipe.result)
        self.assertIn("고정 목표", plan.target_text)
        self.assertIn(f"덱 목표 고정: {self.d.name(self.recipe.result)}", run.history)

    def test_clear_target_reverts_to_auto_detection(self):
        run = RunState()
        run.start_run()
        run.lock_target(self.recipe.result, self.d)
        run.clear_target(self.d)
        self.assertIsNone(run.locked_target)
        plan = build_plan(run, self.d, None)
        self.assertFalse(plan.locked)

    def test_new_run_clears_previous_lock(self):
        run = RunState()
        run.start_run()
        run.lock_target(self.recipe.result, self.d)
        run.start_run()                                # 새 런 — 이전 런의 고정 목표는 안 이어짐
        self.assertIsNone(run.locked_target)


if __name__ == "__main__":
    unittest.main()
