"""도우미 자체 화면 문구 번역 (src/i18n.py) — 원문을 키로 쓰고, 없으면 원문 그대로."""
import unittest
from unittest import mock

from src import i18n


class TrTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang(None)          # 다른 테스트에 영향 안 주게 되돌림

    def test_korean_returns_source_text(self):
        i18n.set_lang("ko")
        self.assertEqual(i18n.tr("설정"), "설정")

    def test_english_uses_locale_file(self):
        i18n.set_lang("en")
        self.assertEqual(i18n.tr("설정"), "Settings")
        self.assertEqual(i18n.tr("현재 런"), "Current Run")

    def test_encyclopedia_reuses_game_translation(self):
        """도감 대신 게임 자체 화면 이름(백과사전)을 씀 — 4개 언어 다 게임 원문 그대로."""
        for lang, expect in (("en", "Encyclopedia"), ("ja", "百科事典"), ("schinese", "百科全书"),
                             ("tchinese", "百科全書")):
            i18n.set_lang(lang)
            self.assertEqual(i18n.tr("백과사전"), expect)

    def test_missing_key_falls_back_to_source(self):
        i18n.set_lang("en")
        self.assertEqual(i18n.tr("아직 번역 안 한 문구입니다"), "아직 번역 안 한 문구입니다")

    def test_unsupported_language_stays_korean(self):
        with mock.patch("locale.getdefaultlocale", return_value=("vi_VN", "UTF-8")), \
             mock.patch("ctypes.windll", create=True, new=None):
            self.assertEqual(i18n.detect_ui_lang(), "ko")

    def test_all_locales_have_same_keys_and_placeholders(self):
        import json
        import os
        import re
        tables = {c: json.load(open(os.path.join(i18n.DATA_DIR, f"{c}.json"), encoding="utf-8"))
                  for c in i18n.SUPPORTED}
        base = set(tables["en"])
        for code, t in tables.items():
            self.assertEqual(set(t), base, code)
            for k, v in t.items():
                self.assertEqual(sorted(re.findall(r"\{\w+\}", k)), sorted(re.findall(r"\{\w+\}", v)), (code, k))

    def test_page_navigation_uses_internal_keys(self):
        """표시 이름이 번역돼도 페이지 이동(내부 키)은 그대로여야 한다."""
        i18n.set_lang("en")
        from src.ui import control_window as cw
        self.assertEqual(len(cw.PAGES), len(cw.PAGE_KEYS))
        self.assertIn("진단", cw.PAGE_KEYS)

    def test_resource_names_consistent_across_modules(self):
        """자원 이름은 dict 키로도 쓰이므로 한 곳(RESOURCES)에서만 나와야 한다."""
        from src.tracking.meta_state import RESOURCES, MetaState
        ms = MetaState(resources=[0, 0, 0, 0])
        self.assertEqual(set(ms.shortfall((5, 5, 5, 5))), set(RESOURCES))

    def test_format_kwargs(self):
        i18n.set_lang("ko")
        self.assertEqual(i18n.tr("{n}개 남음", n=3), "3개 남음")


if __name__ == "__main__":
    unittest.main()
