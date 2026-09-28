"""현재 배치 지도는 추천 작업의 지연·폐기와 독립적으로 표시한다."""

import copy
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from src.ui.layout_window import LayoutWindow
from src.engine.layout import LayoutPlan, Move
from tests.test_guide_contract import base_of, bld

app = QApplication.instance() or QApplication([])


class LiveLayoutMapTest(unittest.TestCase):
    def setUp(self):
        self.window = LayoutWindow(Mock(building_name=lambda kind: kind))
        self.base = base_of(bld(1, "kClinic", 2.5, 2.5))
        self.base["geo"].update(right=16., top=12.)

    def tearDown(self):
        self.window.close()

    def test_current_map_exists_before_first_recommendation_finishes(self):
        self.window.set_current_base(self.base)
        self.assertIsNone(self.window.plan)
        self.assertEqual(self.window.canvas.blds[1].x, 2.5)
        self.assertEqual(self.window.canvas.geo["right"], 16.)

    def test_late_result_does_not_rewind_current_map_or_change_target_snapshot(self):
        self.window.set_current_base(self.base)
        moved = copy.deepcopy(self.base)
        moved["buildings"][0].update(x=8.5, rot=1)
        self.window.set_current_base(moved)
        plan = LayoutPlan(0, 1, swaps=[Move(1, (10.5, 4.5), 0, "")])
        self.window.set_result(self.base, plan, {})
        self.assertEqual(self.window.canvas.blds[1].x, 8.5)
        self.assertEqual(self.window.canvas.blds[1].rot, 1)
        self.window.view.group.button(1).setChecked(True)
        self.window._redraw()
        self.assertEqual(self.window.canvas.blds[1].x, 10.5)
        self.assertEqual(self.window.base["buildings"][0]["x"], 2.5)

    def test_no_live_base_is_not_replaced_with_old_plan_as_current(self):
        self.window.set_result(self.base, LayoutPlan(0, 1), {})
        self.window.set_current_base(None)
        self.assertFalse(self.window.canvas.blds)

    def test_unchanged_live_geometry_does_not_repaint_every_message(self):
        self.window._redraw = Mock()
        self.window.set_current_base(self.base)
        self.window.set_current_base(copy.deepcopy(self.base))
        self.window._redraw.assert_called_once()
