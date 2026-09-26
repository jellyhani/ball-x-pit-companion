"""합성 보유 슬롯으로 융합 효과 표시와 복사본 구분을 확인한다."""
import unittest

from PySide6.QtWidgets import QApplication, QLabel

from src.domain import InventorySlot, CardLabel, FuserCombo, FuserOptions
from src.engine.fusion import FusionAdvisor
from src.services.settings import Settings
from src.tracking.run_state import RunState
from src.ui.control_window import ControlWindow
from tests.helpers import game_data, card

app = QApplication.instance() or QApplication([])


def slot(idx, item, combined=(), level=3):
    return InventorySlot(idx,(0,0,0,0),True,item,level,at_max=True,combined=combined)


class CombinedDisplayTest(unittest.TestCase):
    def setUp(self):
        self.data = game_data()
        self.run = RunState()

    def displayed_balls(self, slots):
        self.run.apply_inventory(slots,self.data)
        window = ControlWindow(self.data,self.run,Settings())
        try:
            return [label.text() for label in window.balls_group.findChildren(QLabel)]
        finally:
            window.close()

    def test_combined_ball_shows_all_names_and_one_slot(self):
        text=self.displayed_balls((slot(0,'ball:laserbeam',('ball:sacrifice',)),))
        self.assertIn(self.data.name('ball:laserbeam')+' + '+self.data.name('ball:sacrifice'),text)
        self.assertTrue(any('보유 슬롯 1칸' in x for x in text))
        self.assertEqual(set(self.run.owned),{'ball:laserbeam'})

    def test_copies_do_not_merge_their_separate_fusion_components(self):
        slots=(slot(0,'ball:laserbeam',('ball:sacrifice',)),slot(1,'ball:laserbeam',('ball:storm',)))
        text=self.displayed_balls(slots)
        self.assertEqual(self.run.owned['ball:laserbeam'].instances,slots)
        detail=next(x for x in text if '슬롯 1:' in x)
        self.assertIn('슬롯 1: '+self.data.name('ball:laserbeam')+' + '+self.data.name('ball:sacrifice'),detail)
        self.assertIn('슬롯 2: '+self.data.name('ball:laserbeam')+' + '+self.data.name('ball:storm'),detail)
        self.assertNotIn(' + '.join(self.data.name(i) for i in ('ball:laserbeam','ball:sacrifice','ball:storm')),detail)

    def test_level_change_preserves_combined_effects(self):
        self.run.apply_inventory((slot(0,'ball:laserbeam',('ball:sacrifice',),2),),self.data)
        self.run._apply_card(card(0,'ball:laserbeam',CardLabel.UPGRADE,3),self.data,'pick')
        self.assertEqual(self.run.owned['ball:laserbeam'].combined,('ball:sacrifice',))
        self.run.set_owned('ball:laserbeam','ball',2)
        self.assertEqual(self.run.owned['ball:laserbeam'].combined,('ball:sacrifice',))

    def test_fusion_candidate_uses_matched_slots_not_effect_union(self):
        slots=(slot(0,'ball:laserbeam',('ball:sacrifice',)),slot(1,'ball:laserbeam',('ball:storm',)),
               slot(2,'ball:zombie'))
        self.run.apply_inventory(slots,self.data)
        advisor=FusionAdvisor(self.data)
        plain=advisor._combo(FuserCombo('ball:laserbeam','ball:zombie',0,2),self.run)
        shown=advisor._combo(FuserCombo('ball:laserbeam','ball:zombie',0,2),self.run,slots)
        self.assertIn(self.data.name('ball:sacrifice'),shown.title)
        self.assertNotIn(self.data.name('ball:storm'),shown.title)
        self.assertEqual(shown.parts,('ball:laserbeam','ball:zombie'))
        self.assertEqual(shown.display_parts,('ball:laserbeam','ball:sacrifice','ball:zombie'))
        self.assertEqual(plain.score,shown.score)

    def test_wrong_slot_does_not_invent_inherited_effect(self):
        slots=(slot(0,'ball:storm',('ball:sacrifice',)),)
        pick=FusionAdvisor(self.data)._combo(FuserCombo('ball:laserbeam','ball:zombie',0,2),self.run,slots)
        self.assertNotIn(self.data.name('ball:sacrifice'),pick.title)
