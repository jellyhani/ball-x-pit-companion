"""Qt 화면 구성 검사. 창을 실제로 만들고 배치·글자 잘림을 확인한다 (게임 없이, 합성 추천 입력)."""
import sys
import unittest

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication, QLabel

from src.domain import CardLabel
from src.engine.recommender import Recommender
from src.tracking.run_state import RunState
from src.ui import geometry as geo
from tests.helpers import card, game_data, session, slots

app = QApplication.instance() or QApplication(sys.argv)


def sample_recommendation():
    d = game_data()
    run = RunState()
    run.start_run()
    run.apply_inventory(slots(("ball:bleed", 2), ("ball:heavy", 3)), d)
    run.apply_character("char:itchyfinger")
    s = session([card(0, "ball:stone"), card(1, "ball:bleed", CardLabel.UPGRADE, 3), card(2, None)],
                gold=3, reroll_cost=5)
    return Recommender(d).recommend(s, run)


class HudTest(unittest.TestCase):
    def test_hud_text_fits_at_both_font_sizes(self):
        from src.ui.hud import RecommendationHud
        rec = sample_recommendation()
        for scale in (1.0, 1.2):
            hud = RecommendationHud(game_data(), scale)
            hud.show_recommendation(rec)
            hud.adjustSize()
            for lbl in hud.findChildren(QLabel):
                if lbl.isVisible() and not lbl.wordWrap():
                    self.assertLessEqual(lbl.sizeHint().width(), hud.width(), lbl.text())
            self.assertLess(hud.height(), 420 * scale)   # 스크롤 없이 한눈에
            self.assertEqual(hud.headline.text(), "1위 출혈")      # 순위 + 추천 항목 이름
            self.assertIn(hud.status.text(), ("확실", "추천"))      # 확신도
            self.assertIn("가운데 카드", hud.sub.text())
            hud.close()

    def test_font_scale_changes_every_label(self):
        from src.ui.hud import RecommendationHud
        small, large = RecommendationHud(game_data(), 1.0), RecommendationHud(game_data(), 1.2)
        self.assertGreater(large.headline.font().pixelSize(), small.headline.font().pixelSize())
        self.assertGreater(large.footer[0].font().pixelSize(), small.footer[0].font().pixelSize())
        self.assertGreater(large.width(), small.width())


class PlacementTest(unittest.TestCase):
    def test_hud_beside_panel_not_over_cards(self):
        game = QRect(0, 0, 1920, 1080)
        panel = QRect(0, 0, 935, 1080)
        cards = [QRect(35, 575, 260, 350), QRect(335, 575, 260, 350), QRect(635, 575, 260, 350)]
        p = geo.place_hud(game, cards, 368, 222, panel=panel)
        box = QRect(p.x(), p.y(), 368, 222)
        self.assertFalse(box.intersects(panel))
        self.assertEqual(p.y() + 222, game.bottom() - 12)   # 패널 아래쪽에 맞춤

    def test_falls_back_when_no_room(self):
        game = QRect(0, 0, 1000, 700)
        panel = QRect(0, 0, 990, 700)
        p = geo.place_hud(game, [], 368, 222, panel=panel)
        self.assertTrue(game.contains(QRect(p.x(), p.y(), 368, 222)))


class ControlWindowTest(unittest.TestCase):
    def test_builds_and_lists_owned_items(self):
        from src.services.settings import Settings
        from src.ui.control_window import ControlWindow
        d = game_data()
        run = RunState()
        run.start_run()
        run.apply_inventory(slots(("ball:burn", 1)), d)
        w = ControlWindow(d, run, Settings())
        self.assertEqual(w.owned_ids(), ["ball:burn"])
        w.search.setText("대출혈")
        self.assertEqual(w.pedia_list.count(), 1)
        w.close()


if __name__ == "__main__":
    unittest.main()
