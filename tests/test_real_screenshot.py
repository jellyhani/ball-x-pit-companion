"""실제 게임 스크린샷으로 전체 인식 경로를 검사한다 (Windows OCR 필요).

- levelup_1080p_itchy.png: 사용자가 찍은 실제 강화 선택 화면 (1920×1080, 버전 1.301)
- scratch/game_window_print.png: 실제 기지 화면 (선택창이 아님)
해상도 변경 검사는 같은 스크린샷을 늘리거나 줄인 '합성' 입력이다. 실제 다른 해상도 화면은 아니다.
"""
import os
import unittest

from PIL import Image

from src.domain import CardLabel, ScreenKind
from src.recognition.ocr import check_ocr
from tests.helpers import LEVELUP_PNG, frame

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_OK = check_ocr().ok


def run_pipeline(img: Image.Image):
    from src.recognition.level_up_reader import LevelUpReader
    from src.recognition.ocr import OcrReader
    from src.recognition.screen_parser import parse_layout
    reader = OcrReader()
    parsed = parse_layout(reader.read(img), frame(img.size))
    if parsed.kind != ScreenKind.LEVEL_UP:
        return parsed.kind, None
    lu = LevelUpReader(OcrReader(target_height=100000).read)
    return parsed.kind, lu.read(img, frame(img.size), parsed.layout)


@unittest.skipUnless(OCR_OK, "Windows 한국어 OCR 없음")
@unittest.skipUnless(os.path.exists(LEVELUP_PNG), "게임 스크린샷 없음 (공개판에는 넣지 않음)")
class RealScreenshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.img = Image.open(LEVELUP_PNG).convert("RGB")

    def check(self, obs):
        self.assertEqual([c.item_id for c in obs.cards], ["ball:burn", "ball:stone", "ball:earthquake"])
        self.assertEqual([c.label for c in obs.cards], [CardLabel.UPGRADE, CardLabel.NEW, CardLabel.NEW])
        self.assertEqual(obs.cards[0].shown_level, 2)
        held = [(s.item_id, s.level) for s in obs.inventory if s.occupied]
        self.assertEqual(held, [("ball:burn", 1)])
        self.assertEqual(sum(1 for s in obs.inventory if not s.occupied), 7)
        self.assertEqual(obs.character_id, "char:itchyfinger")
        self.assertEqual(obs.reroll_cost, 5)

    def test_levelup_screen_1080p(self):
        kind, obs = run_pipeline(self.img)
        self.assertEqual(kind, ScreenKind.LEVEL_UP)
        self.check(obs)
        self.assertEqual(obs.gold, 0)
        for c in obs.cards:
            self.assertGreaterEqual(c.icon_margin, 0.12, c)

    def test_synthetic_resize_1440p(self):
        kind, obs = run_pipeline(self.img.resize((2560, 1440), Image.Resampling.NEAREST))
        self.assertEqual(kind, ScreenKind.LEVEL_UP)
        self.check(obs)

    def test_synthetic_resize_720p(self):
        kind, obs = run_pipeline(self.img.resize((1280, 720), Image.Resampling.LANCZOS))
        self.assertEqual(kind, ScreenKind.LEVEL_UP)
        self.assertEqual([c.item_id for c in obs.cards], ["ball:burn", "ball:stone", "ball:earthquake"])

    def test_banish_layout_with_duplicate_ball_and_hover_panel(self):
        # 실제 게임 화면 2: '삭제 (2남음)' 버튼 때문에 카드가 짧고, 오른쪽에 카드 설명 패널이 떠 있다.
        # 같은 볼(레이저 수평)을 이미 1개 가졌는데 '신규!'로 또 제시된다 — 같은 볼을 여러 개 가질 수 있다.
        img = Image.open(os.path.join(os.path.dirname(LEVELUP_PNG), "levelup_1080p_banish.png")).convert("RGB")
        from src.recognition.level_up_reader import LevelUpReader
        from src.recognition.ocr import OcrReader
        from src.recognition.screen_parser import parse_layout
        from src.recognition.text_match import NameIndex
        from tests.helpers import game_data
        parsed = parse_layout(OcrReader().read(img), frame(img.size))
        self.assertEqual(parsed.kind, ScreenKind.LEVEL_UP)
        names = NameIndex((i.id, i.name_ko) for i in game_data().items.values())
        obs = LevelUpReader(OcrReader(target_height=100000).read, names=names).read(img, frame(img.size), parsed.layout)
        self.assertEqual([c.item_id for c in obs.cards], ["ball:laserhorz", "passive:etherealcloak", "ball:freeze"])
        self.assertEqual([c.label for c in obs.cards], [CardLabel.NEW, CardLabel.NEW, CardLabel.UPGRADE])
        held = [(s.item_id, s.level) for s in obs.inventory if s.occupied]
        self.assertEqual(held, [("ball:freeze", 1), ("ball:heavy", 1), ("ball:laserhorz", 1)])
        self.assertEqual((obs.gold, obs.reroll_cost, obs.banish_left), (12, 5, 2))
        self.assertEqual(obs.character_id, "char:recaller")
        self.assertEqual(obs.hover_item_id, "ball:laserhorz")

    def test_base_screen_is_not_a_choice(self):
        path = os.path.join(ROOT, "scratch", "game_window_print.png")
        if not os.path.exists(path):
            self.skipTest("기지 화면 스크린샷 없음")
        kind, _ = run_pipeline(Image.open(path).convert("RGB"))
        self.assertNotEqual(kind, ScreenKind.LEVEL_UP)


if __name__ == "__main__":
    unittest.main()
