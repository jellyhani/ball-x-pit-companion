"""도우미 자체 화면 문구 번역 (src/i18n.py) — 원문을 키로 쓰고, 없으면 원문 그대로."""
import os
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
        with mock.patch.dict("os.environ"), \
             mock.patch("locale.getdefaultlocale", return_value=("vi_VN", "UTF-8")), \
             mock.patch("ctypes.windll", create=True, new=None):
            os.environ.pop("BXP_LANG", None)          # tests/__init__.py 가 넣은 강제 언어를 빼고 자동 감지만 본다
            self.assertEqual(i18n.detect_ui_lang(), "ko")

    def test_font_follows_language_with_korean_fallback(self):
        """일본어·중국어 화면은 그 언어 글꼴을 먼저 (한국어 글꼴만 쓰면 闘·简体 같은 글자가 대체 글꼴로 튄다)."""
        import sys
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv)
        from src.ui import tokens as tk
        try:
            for lang in ("ja", "schinese", "tchinese"):
                i18n.set_lang(lang)
                tk._families_cache = None
                fs = tk.families()
                own = [f for f in fs if f in tk._LANG_FAMILIES[lang]]
                ko = [f for f in fs if f in tk._KO_FAMILIES]
                if own and ko:
                    self.assertLess(fs.index(own[0]), fs.index(ko[0]), lang)
                self.assertTrue(set(fs) <= set(tk._LANG_FAMILIES[lang]) | set(tk._KO_FAMILIES))
        finally:
            tk._families_cache = None

    def test_all_locales_have_same_keys_and_placeholders(self):
        import json
        import os
        import re
        tables = {}
        for c in i18n.SUPPORTED:
            with open(os.path.join(i18n.DATA_DIR, f"{c}.json"), encoding="utf-8") as fh:
                tables[c] = json.load(fh)
        base = set(tables["en"])
        for code, t in tables.items():
            self.assertEqual(set(t), base, code)
            for k, v in t.items():
                self.assertEqual(sorted(re.findall(r"\{\w+\}", k)), sorted(re.findall(r"\{\w+\}", v)), (code, k))

    def test_page_navigation_uses_internal_keys(self):
        """표시 이름이 번역돼도 페이지 이동(내부 키)은 그대로여야 한다."""
        from src.ui import control_window as cw
        self.assertEqual(len(cw.PAGES), len(cw.PAGE_KEYS))
        self.assertIn("진단", cw.PAGE_KEYS)

    def test_resource_names_consistent_across_modules(self):
        """자원 이름은 dict 키로도 쓰이므로 한 곳(RESOURCES)에서만 나와야 한다."""
        from src.tracking.meta_state import RESOURCES, MetaState
        ms = MetaState(resources=[0, 0, 0, 0])
        self.assertEqual(set(ms.shortfall((5, 5, 5, 5))), set(RESOURCES))

    def test_every_literal_tr_key_is_translated(self):
        """소스의 tr("원문") 호출은 모든 지원 언어 파일에 번역이 있어야 한다 (자리표시자도 같아야 함)."""
        import ast
        import json
        import os
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        keys = {}
        for base, _, files in os.walk(os.path.join(root, "src")):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(base, f)
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read())
                    for n in ast.walk(tree):
                        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr"
                                and n.args and isinstance(n.args[0], ast.Constant)):
                            keys.setdefault(n.args[0].value, f)
        for code in i18n.SUPPORTED:
            with open(os.path.join(i18n.DATA_DIR, f"{code}.json"), encoding="utf-8") as fh:
                table = json.load(fh)
            missing = sorted(k for k in keys if k not in table)
            self.assertEqual(missing, [], f"{code}: 번역 없는 문구 {len(missing)}개")
            for k in keys:
                self.assertEqual(sorted(re.findall(r"\{\w+", k)), sorted(re.findall(r"\{\w+", table[k])), (code, k))

    def test_forced_language_env(self):
        with mock.patch.dict("os.environ", {"BXP_LANG": "ja"}):
            self.assertEqual(i18n.detect_ui_lang(), "ja")
        with mock.patch.dict("os.environ", {"BXP_LANG": "zz"}):
            self.assertEqual(i18n.detect_ui_lang(), "ko")

    def test_format_kwargs(self):
        i18n.set_lang("ko")
        self.assertEqual(i18n.tr("{n}개 남음", n=3), "3개 남음")


if __name__ == "__main__":
    unittest.main()
