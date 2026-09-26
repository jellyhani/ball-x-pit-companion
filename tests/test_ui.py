"""Qt 화면 구성 검사. 창을 실제로 만들고 배치·글자 잘림을 확인한다 (게임 없이, 합성 추천 입력)."""
import sys
import unittest

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel

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

    def test_rows_not_squashed_when_view_changes_on_visible_hud(self):
        """떠 있는 HUD 에 줄 있는 새 내용을 그려도 줄이 눌려 겹치지 않는다 (캐릭터 조합 '다른 조합' 이 빈 칸처럼
        보이던 것: 높이가 줄을 빼고 고정돼 아이콘이 한곳에 겹침)."""
        from src.ui.hud import HudRow, HudView, RecommendationHud
        hud = RecommendationHud(game_data(), 1.2)
        hud.render_view(HudView(title="기지 조언", status="가이드", status_tone="accent"))
        hud.show()
        app.processEvents()
        v = HudView(title="난사광 + 회한자", subtitle="캐릭터 조합 추천 (알선소)", status="추천", status_tone="accent")
        v.lines = [("커뮤니티 추천 2곳: 빠른 연사로 튕길수록 세지는 회한자 볼을 좁은 틈에 계속 튕김", "secondary")]
        v.section = "다른 조합"
        v.rows = [HudRow((), f"조합 {i}", "커뮤니티 추천 2곳") for i in range(3)]
        v.footer = [("참고용", "tertiary")]
        hud.render_view(v)
        names = hud._row_widgets[1::3]
        self.assertTrue(all(n.isVisible() and n.height() > 0 for n in names))
        ys = [n.geometry().y() for n in names]
        self.assertEqual(ys, sorted(set(ys)))                    # 줄마다 다른 높이 (겹치지 않음)
        hud.setFixedHeight(120)                                  # 높이가 모자라게 고정된 상황
        hud.fit_height()
        self.assertGreater(hud.height(), 120)
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
