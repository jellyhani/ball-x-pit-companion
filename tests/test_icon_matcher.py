"""아이콘 비교의 구별력 검사 (합성: 원본 아이콘을 카드 배경 위에 화면 배율로 그린 이미지)."""
import unittest

from PIL import Image

from src.recognition.icon_matcher import IconLibrary
from src.recognition.level_up_reader import CARD_MAX_ERROR, CARD_MIN_MARGIN

CARD_BG = (42, 26, 32)


def render(sprite: Image.Image, scale: float, box: int = 180) -> Image.Image:
    big = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (box, box), CARD_BG)
    canvas.paste(big, ((box - big.width) // 2, (box - big.height) // 2), big)
    return canvas


class IconMatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = IconLibrary.items()

    def _run(self, prefix, scales):
        wrong_confident, unsure = [], []
        for ident, sp in self.lib.sprites.items():
            if not ident.startswith(prefix):
                continue
            for s in scales:
                m = self.lib.identify(render(sp, s), 3.0)
                ok = m is not None and m.error <= CARD_MAX_ERROR and m.margin >= CARD_MIN_MARGIN
                if not ok:
                    unsure.append(ident)
                elif m.ident != ident:
                    wrong_confident.append((ident, s, m.ident))
        return wrong_confident, unsure

    def test_balls_never_confidently_wrong(self):
        wrong, unsure = self._run("ball:", (3.0,))
        self.assertEqual(wrong, [])
        # 배경과 색이 거의 같은 모기떼 아이콘은 '미확인'으로 남을 수 있다 (틀린 답보다 낫다)
        self.assertLessEqual(set(unsure), {"ball:mosquito"})

    def test_passives_at_both_candidate_scales(self):
        # 패시브가 카드에서 어떤 배율로 그려지는지 아직 실제 화면으로 확인하지 못해 두 가지 모두 검사
        wrong, unsure = self._run("passive:", (3.0, 3.0 * 50 / 27))
        self.assertEqual(wrong, [])
        self.assertEqual(unsure, [])

    def test_empty_card_is_not_an_icon(self):
        self.assertIsNone(self.lib.identify(Image.new("RGB", (180, 180), CARD_BG), 3.0))


if __name__ == "__main__":
    unittest.main()
