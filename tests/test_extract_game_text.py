"""게임 문구 다국어 추출 — LANGS 표와 윈도우 언어 감지만 (실제 게임 파일 파싱은 게임이 있어야 해서 안 함)."""
import unittest
from unittest import mock

from tools import extract_game_text as egt


class LangsTableTest(unittest.TestCase):
    def test_16_languages_matching_game(self):
        self.assertEqual(len(egt.LANGS), egt.LANG_COUNT)
        codes = [c for c, _ in egt.LANGS]
        self.assertEqual(len(codes), len(set(codes)), "코드 중복")

    def test_known_indices(self):
        self.assertEqual(egt.LANGS[egt.LANG_EN][0], "english")
        self.assertEqual(egt.LANGS[egt.LANG_KO][0], "koreana")


class DetectSystemLangTest(unittest.TestCase):
    def _detect_with_locale(self, code: str) -> int:
        with mock.patch("locale.getdefaultlocale", return_value=(code, "UTF-8")), \
             mock.patch("ctypes.windll", create=True, new=None):
            # windll 접근에서 AttributeError 나게 해 getdefaultlocale 경로를 타게 함
            return egt.detect_system_lang_index()

    def test_korean_locale(self):
        codes = [c for c, _ in egt.LANGS]
        self.assertEqual(self._detect_with_locale("ko_KR"), codes.index("koreana"))

    def test_traditional_vs_simplified_chinese(self):
        self.assertEqual(egt.LANGS[self._detect_with_locale("zh_TW")][0], "tchinese")
        self.assertEqual(egt.LANGS[self._detect_with_locale("zh_CN")][0], "schinese")

    def test_unsupported_locale_falls_back_to_english(self):
        self.assertEqual(self._detect_with_locale("vi_VN"), egt.LANG_EN)


if __name__ == "__main__":
    unittest.main()
