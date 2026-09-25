"""설정 창(F10) '진화' 탭의 덱 목표 고정 UI — 콤보에서 고르고 고정/해제하면 RunState 에 반영되는지."""
import sys
import unittest

from PySide6.QtWidgets import QApplication

from tests import HAS_GAME_DATA

app = QApplication.instance() or QApplication(sys.argv)

if HAS_GAME_DATA:
    from src.gamedata import load_game_data
    from src.services.settings import Settings
    from src.tracking.run_state import RunState
    from src.ui.control_window import ControlWindow


@unittest.skipUnless(HAS_GAME_DATA, "게임 자료 없음")
class DeckTargetUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = load_game_data()
        cls.recipe = next(r for r in cls.d.recipes if len(r.ingredients) == 2)

    def test_lock_and_clear_via_combo(self):
        run = RunState()
        run.start_run()
        cw = ControlWindow(self.d, run, Settings())
        try:
            idx = cw.target_combo.findData(self.recipe.result)
            self.assertGreaterEqual(idx, 0, "browsable_targets 에 이 레시피가 있어야 함")
            cw.target_combo.setCurrentIndex(idx)
            cw._lock_target()
            self.assertEqual(run.locked_target, self.recipe.result)
            self.assertEqual(cw.target_combo.currentIndex(), -1)   # 다시 고를 수 있게 콤보를 비움
            cw._clear_target()
            self.assertIsNone(run.locked_target)
        finally:
            cw.close()

    def test_run_edited_signal_fires_on_lock(self):
        run = RunState()
        run.start_run()
        cw = ControlWindow(self.d, run, Settings())
        fired = []
        cw.run_edited.connect(lambda: fired.append(True))
        try:
            idx = cw.target_combo.findData(self.recipe.result)
            cw.target_combo.setCurrentIndex(idx)
            cw._lock_target()
            self.assertTrue(fired, "고정하면 앱이 바로 추천을 다시 계산하도록 run_edited 가 떠야 함")
        finally:
            cw.close()


if __name__ == "__main__":
    unittest.main()
