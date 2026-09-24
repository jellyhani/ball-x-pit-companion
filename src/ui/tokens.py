"""디자인 토큰. 글자 크기·색·간격은 모두 여기서 나온다(위젯에 고정 글자 크기를 두지 않는다).

색은 어두운 중성 배경, 밝은 본문, 강조색 하나(파랑). 주황은 주의, 빨강은 경고에만 쓴다.
QSS 의 rgba 알파는 Qt 규칙대로 0–255 정수로 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from PyQt6.QtGui import QColor, QFont, QFontDatabase

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
    "best": (ACCENT, "추천"),
    "alt": (OK, "비슷함"),
    "banish": (WARN, "삭제 추천"),
    "skip": (DANGER, "비추천"),
    "neutral": (TEXT_2, "보류"),
    "unknown": (TEXT_3, "읽지 못함"),
}

_FAMILY_CANDIDATES = ("Noto Sans KR", "Malgun Gothic")
_family_cache = None


def family() -> str:
    global _family_cache
    if _family_cache is None:
        available = set(QFontDatabase.families())
        _family_cache = next((f for f in _FAMILY_CANDIDATES if f in available), "")
    return _family_cache


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
        f = QFont(family())
        f.setPixelSize(size)
        f.setWeight(weight)
        f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return f
