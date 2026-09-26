"""도우미 자체 화면 문구 번역 (게임 문구가 아니라 우리가 만드는 라벨·버튼·상태어).

원문(한국어)을 키로 쓴다 — gettext 방식과 비슷하게, 번역이 없으면 원문이 그대로 나온다.
그래서 tr("지금 가능") 처럼 기존 코드의 리터럴을 감싸기만 하면 되고, 번역 파일이 비어 있어도 안 깨진다.

로케일 파일: data/i18n/<코드>.json, {"원문": "번역"} 형태 (로컬 파일 — 사용자 PC 에 그대로 둠).
일부 값(설정·진화·백과사전 등 게임에도 같은 개념의 화면이 있는 것)은 우리가 새로 지어내지 않고 게임 자체
번역(resources.assets 의 I2Loc, tools/extract_game_text.py 로 확인)을 그대로 재사용했다 — 플레이어가
게임에서 보던 말과 같아야 헷갈리지 않는다.
언어 감지는 tools/extract_game_text.py 의 게임 문구 언어 감지와 같은 원리(윈도우 UI 언어)지만 별도
구현이다 — 여긴 우리가 실제로 번역해 둔 언어(en/ja/schinese/tchinese)만 지원하고, 그 밖의 언어는
한국어 원문 그대로 (게임이 지원하는 16개 언어보다 훨씬 적다 — 번역을 다 못 채웠으니까).
"""
from __future__ import annotations

import json
import locale as _locale
import os
from typing import Dict, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "i18n")
SUPPORTED = {
    "en": "English", "ja": "日本語", "schinese": "简体中文", "tchinese": "繁體中文",
    "french": "Français", "german": "Deutsch", "russian": "Русский",
    "brazilian": "Português (Brasil)",
}

_cache: Dict[str, Dict[str, str]] = {}
_lang: Optional[str] = None


def detect_ui_lang() -> str:
    """윈도우 UI 언어 → 지원 코드(en/ja/schinese/tchinese), 그 밖엔 'ko' (번역 안 함, 원문 그대로).
    환경 변수 BXP_LANG 로 강제할 수 있다 (ko/en/ja/schinese/tchinese — 테스트·확인용)."""
    forced = os.environ.get("BXP_LANG", "").strip()
    if forced:
        return forced if forced in SUPPORTED or forced == "ko" else "ko"
    try:
        import ctypes
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        code = _locale.windows_locale.get(lcid, "")
    except (AttributeError, OSError):
        code = _locale.getdefaultlocale()[0] or ""
    low = code.lower()
    if low.startswith("ko"):
        return "ko"
    if low.startswith("ja"):
        return "ja"
    if low in ("zh_tw", "zh_hk", "zh_mo"):
        return "tchinese"
    if low.startswith("zh"):
        return "schinese"
    if low.startswith("en"):
        return "en"
    if low.startswith("fr"):
        return "french"
    if low.startswith("de"):
        return "german"
    if low.startswith("ru"):
        return "russian"
    if low.startswith("pt"):
        return "brazilian"
    return "ko"


def set_lang(code: Optional[str]):
    """언어를 강제로 지정한다 (테스트·수동 전환용). None 이면 다시 자동 감지하게 한다."""
    global _lang
    _lang = code


def current_lang() -> str:
    global _lang
    if _lang is None:
        _lang = detect_ui_lang()
    return _lang


def _table(code: str) -> Dict[str, str]:
    if code not in _cache:
        path = os.path.join(DATA_DIR, f"{code}.json")
        try:
            with open(path, encoding="utf-8") as f:
                _cache[code] = json.load(f)
        except (OSError, ValueError):
            _cache[code] = {}
    return _cache[code]


def tr(text: str, /, **kwargs) -> str:
    """text: 한국어 원문 (번역 키를 겸함). 번역이 있으면 그걸, 없으면 원문 그대로.
    kwargs 를 주면 str.format 으로 채운다 — 원문·번역 모두 같은 {이름} 자리표시자를 써야 한다."""
    lang = current_lang()
    out = _table(lang).get(text, text) if lang != "ko" else text
    return out.format(**kwargs) if kwargs else out
