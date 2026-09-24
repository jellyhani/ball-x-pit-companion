"""배치 해석 테스트 (합성 OCR 입력 — 실제 OCR이 돌려준 줄 위치를 본뜸)."""
import unittest

from src.domain import CardLabel, OcrLine, ScreenKind
from src.recognition.screen_parser import label_of, parse_layout
from src.recognition.text_match import NameIndex, normalize
from tests.helpers import frame, synthetic_levelup_lines


class LabelTest(unittest.TestCase):
    def test_labels_as_ocr_returns_them(self):
        # 실제 OCR 결과: '레벨2수', '레벨 2+', '신규!'
        self.assertEqual(label_of(OcrLine("레벨2수", 0, 0, 1, 1)), (CardLabel.UPGRADE, 2))
        self.assertEqual(label_of(OcrLine("레벨 2+", 0, 0, 1, 1)), (CardLabel.UPGRADE, 2))
        self.assertEqual(label_of(OcrLine("신규!", 0, 0, 1, 1)), (CardLabel.NEW, None))
        self.assertEqual(label_of(OcrLine("레벨 업! +1 속도", 0, 0, 1, 1)), (None, None))


class LayoutTest(unittest.TestCase):
    def test_three_cards_positions_labels_and_reroll_cost(self):
        p = parse_layout(synthetic_levelup_lines(), frame())
        self.assertEqual(p.kind, ScreenKind.LEVEL_UP)
        cards = p.layout.cards
        self.assertEqual([c.position for c in cards], ["왼쪽", "가운데", "오른쪽"])
        self.assertEqual([(c.label, c.shown_level) for c in cards],
                         [(CardLabel.UPGRADE, 2), (CardLabel.NEW, None), (CardLabel.NEW, None)])
        self.assertEqual(p.layout.reroll_cost, 5)
        self.assertEqual(len(p.layout.inventory_slots), 8)
        self.assertAlmostEqual(p.layout.card_scale, 3.0, delta=0.1)

    def test_card_rect_contains_card_on_screen(self):
        p = parse_layout(synthetic_levelup_lines(), frame())
        x, y, w, h = p.layout.cards[1].rect
        # 스크린샷의 가운데 카드 테두리는 x 335–595, y 575–925
        self.assertTrue(x <= 345 and x + w >= 585 and y <= 590 and y + h >= 915)

    def test_missing_middle_label_keeps_positions(self):
        # 가운데 카드 문구를 OCR이 놓쳐도 오른쪽 카드가 '가운데'로 밀리지 않는다
        lines = synthetic_levelup_lines(labels=((161, "레벨 2+"), (764, "신규!")))
        cards = parse_layout(lines, frame()).layout.cards
        self.assertEqual([c.position for c in cards], ["왼쪽", "가운데", "오른쪽"])
        self.assertIsNone(cards[1].label)
        self.assertEqual(cards[2].label, CardLabel.NEW)

    def test_two_cards_are_not_forced_to_three(self):
        lines = synthetic_levelup_lines(labels=((313, "신규!"), (613, "신규!")))
        p = parse_layout(lines, frame())
        self.assertEqual(len(p.layout.cards), 2)

    def test_not_a_choice_screen_without_header_or_labels(self):
        self.assertEqual(parse_layout([OcrLine("신규!", 400, 870, 70, 33)], frame()).kind, ScreenKind.OTHER)
        only_header = [OcrLine("강화 선택", 396, 525, 135, 34)]
        self.assertEqual(parse_layout(only_header, frame()).kind, ScreenKind.OTHER)

    def test_unrelated_text_mentioning_header_is_rejected(self):
        # 설명문이나 다른 창에 '강화 선택' 과 '신규' 가 흩어져 있어도 배치가 맞지 않으면 선택창이 아니다
        lines = [OcrLine("강화 선택", 396, 525, 135, 34), OcrLine("신규!", 1500, 560, 70, 33),
                 OcrLine("신규!", 1510, 590, 70, 33)]
        self.assertEqual(parse_layout(lines, frame()).kind, ScreenKind.OTHER)

    def test_free_reroll_and_points_variant(self):
        lines = synthetic_levelup_lines(header="강화를 선택하세요 (2 포인트 남음)", reroll="무료 새로고침 (3 남음)")
        p = parse_layout(lines, frame())
        self.assertEqual(p.layout.points_left, 2)
        self.assertEqual(p.layout.free_rerolls, 3)
        self.assertIsNone(p.layout.reroll_cost)

    def test_scales_with_resolution(self):
        p1 = parse_layout(synthetic_levelup_lines(), frame())
        p2 = parse_layout(synthetic_levelup_lines(scale=4 / 3), frame((2560, 1440)))
        self.assertAlmostEqual(p2.layout.pitch / p1.layout.pitch, 4 / 3, delta=0.02)
        self.assertAlmostEqual(p2.layout.card_scale / p1.layout.card_scale, 4 / 3, delta=0.02)


class NameMatchTest(unittest.TestCase):
    """도감·수동 입력용 이름 대조. 부분 문자열로 다른 항목을 만들지 않는다."""

    def setUp(self):
        from tests.helpers import game_data
        self.idx = NameIndex((i.id, i.name_ko) for i in game_data().items.values())

    def test_exact_only(self):
        self.assertEqual(self.idx.exact("대출혈"), "ball:hemorrhage")
        self.assertEqual(self.idx.exact("강철"), "ball:steel")
        self.assertIsNone(self.idx.exact("대출혈을 입힌다"))
        self.assertEqual(normalize("레이저 (수평)"), "레이저수평")

    def test_fuzzy_needs_unique_close_match(self):
        self.assertEqual(self.idx.fuzzy("대출헐"), "ball:hemorrhage")
        self.assertIsNone(self.idx.fuzzy("철"))


if __name__ == "__main__":
    unittest.main()
