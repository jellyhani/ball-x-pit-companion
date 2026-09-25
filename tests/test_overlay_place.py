"""오버레이 자리: 강화 선택창 HUD 는 초상화 자리, 기지 안내 글상자는 땅·번호를 덜 가리는 모서리."""
import sys
import unittest

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from src.ui import geometry as geo

app = QApplication.instance() or QApplication(sys.argv)


class LevelUpSpotTest(unittest.TestCase):
    GAME = QRect(0, 0, 1920, 1080)
    CARDS = [QRect(30, 570, 270, 360), QRect(330, 570, 270, 360), QRect(630, 570, 270, 360)]   # 실제 기록

    def test_hud_goes_over_portrait(self):
        p = geo.levelup_hud_spot(self.GAME, self.CARDS, 396, 355)
        self.assertEqual((p.x(), p.y()), (20, 115))                 # 초상화 틀 (20, 115)
        self.assertLess(p.x() + 396, 455)                           # 볼 슬롯(455~)을 가리지 않음
        self.assertLessEqual(p.y() + 355, 570 - 40)                 # 제목·순위 배지 위

    def test_tall_hud_moves_up_not_onto_cards(self):
        p = geo.levelup_hud_spot(self.GAME, self.CARDS, 396, 500)
        self.assertLessEqual(p.y() + 500, 530)
        self.assertGreaterEqual(p.y(), 8)

    def test_scales_with_card_size(self):
        cards = [QRect(15, 285, 135, 180)]                          # 960×540 창
        p = geo.levelup_hud_spot(QRect(0, 0, 960, 540), cards, 180, 150)
        self.assertEqual((p.x(), p.y()), (10, 57))                 # 285 − round(227.5)

    def test_unknown_layout_falls_back(self):
        self.assertIsNone(geo.levelup_hud_spot(self.GAME, [QRect(900, 570, 270, 360)], 396, 355))
        self.assertIsNone(geo.levelup_hud_spot(self.GAME, [], 396, 355))


class BaseBoxTest(unittest.TestCase):
    def test_box_avoids_land_and_marks(self):
        from src.ui.base_overlay import BaseOverlay
        o = BaseOverlay()
        o.resize(1920, 1080)
        o._lines = [("최적 배치까지 옮기기 3개 남음", (255, 255, 255, 255))]
        o.set_land([(0, 1000), (1300, 1000), (1300, 100), (0, 100)])   # 땅이 왼쪽에 넓게
        r = o._box_rect()
        self.assertGreater(r.left(), 1300)                            # 오른쪽 모서리로
        o.close()


if __name__ == "__main__":
    unittest.main()
