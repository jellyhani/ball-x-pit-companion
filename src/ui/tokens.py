"""디자인 토큰. 글자 크기·색·간격은 모두 여기서 나온다(위젯에 고정 글자 크기를 두지 않는다).

색은 어두운 중성 배경, 밝은 본문, 강조색 하나(파랑). 주황은 주의, 빨강은 경고에만 쓴다.
QSS 의 rgba 알파는 Qt 규칙대로 0–255 정수로 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from PySide6.QtGui import QColor, QFont, QFontDatabase

from ..i18n import tr

RGBA = Tuple[int, int, int, int]

BG: RGBA = (24, 24, 27, 238)          # HUD 배경 (반투명)
BG_SOLID: RGBA = (24, 24, 27, 255)
SURFACE: RGBA = (36, 36, 40, 255)
SURFACE_2: RGBA = (48, 48, 54, 255)
BORDER: RGBA = (255, 255, 255, 26)
TEXT: RGBA = (244, 244, 246, 255)
TEXT_2: RGBA = (178, 178, 186, 255)
TEXT_3: RGBA = (126, 126, 136, 255)
ACCENT: RGBA = (64, 156, 255, 255)
WARN: RGBA = (255, 176, 64, 255)
DANGER: RGBA = (255, 99, 92, 255)
OK: RGBA = (72, 209, 120, 255)

# 카드 판정 색 (HUD 목록 · 게임 화면 카드 테두리 공통): 신호등처럼 한눈에
VERDICT = {
    "best": (ACCENT, tr("추천")),
    "alt": (OK, tr("비슷함")),
    "banish": (WARN, tr("삭제 추천")),
    "skip": (DANGER, tr("비추천")),
    "neutral": (TEXT_2, tr("보류")),
    "unknown": (TEXT_3, tr("읽지 못함")),
}

# 글꼴: 화면 언어의 글꼴을 먼저, 그다음 한국어 글꼴. 한국어 글꼴만 쓰면 일본어 신자체(闘)·간체자처럼
# 한국 한자에 없는 글자만 다른 글꼴로 대체돼 한 줄 안에서 글자 모양이 튄다. 한국어 글꼴을 뒤에 두는 건
# 게임 이름·설명이 한국어로 추출돼 있을 수 있어서 (윈도우 언어 ≠ 도우미 언어인 경우).
_KO_FAMILIES = ("Noto Sans KR", "Malgun Gothic")
_LANG_FAMILIES = {
    "ja": ("Yu Gothic UI", "Yu Gothic", "Meiryo UI", "Meiryo", "Noto Sans JP"),
    "schinese": ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC"),
    "tchinese": ("Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans TC"),
}
_families_cache = None


def families() -> Tuple[str, ...]:
    """쓸 수 있는 글꼴 목록 (앞이 우선). 설치된 것만."""
    global _families_cache
    if _families_cache is None:
        from ..i18n import current_lang
        available = set(QFontDatabase.families())
        want = _LANG_FAMILIES.get(current_lang(), ()) + _KO_FAMILIES
        _families_cache = tuple(f for f in want if f in available)
    return _families_cache


def family() -> str:
    fs = families()
    return fs[0] if fs else ""


def css_families() -> str:
    """스타일시트 font-family 값 (따옴표 붙인 목록)."""
    return ", ".join(f'"{f}"' for f in families()) or "sans-serif"


def base_font() -> QFont:
    """글꼴 목록이 들어간 QFont (크기·굵기는 호출한 쪽이 정한다)."""
    f = QFont()
    fs = families()
    if fs:
        f.setFamilies(list(fs))
    return f


def qcolor(c: RGBA) -> QColor:
    return QColor(*c)


def css(c: RGBA) -> str:
    return f"rgba({c[0]}, {c[1]}, {c[2]}, {c[3]})"


@dataclass(frozen=True)
class Type:
    """글자 크기(px, 100% 배율 기준). scale 로 한꺼번에 키운다."""
    scale: float = 1.0

    def px(self, base: float) -> int:
        return round(base * self.scale)

    @property
    def headline(self) -> int: return self.px(19)
    @property
    def title(self) -> int: return self.px(16)
    @property
    def body(self) -> int: return self.px(14)
    @property
    def caption(self) -> int: return self.px(12)
    @property
    def space(self) -> int: return self.px(8)

    def font(self, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        f = base_font()
        f.setPixelSize(size)
        f.setWeight(weight)
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return f
