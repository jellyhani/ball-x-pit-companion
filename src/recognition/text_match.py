"""OCR 문자열을 게임 공식 이름과 대조한다.

원칙
- 한 줄 전체가 이름과 같을 때만 확정한다(부분 문자열 검색 없음). 그래서 '강철' 줄에서 '철'이,
  설명문 속 '얼어붙은 불꽃' 같은 단어가 후보로 잡히지 않는다.
- 유사 비교는 호출하는 쪽이 제목 자리라고 판단한 줄에만 쓰고, 세 글자 이상·편집 거리 1·유일한 후보일 때만 받아들인다.
- 두 항목의 이름이 같아 구분할 수 없으면 미확인으로 돌려준다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, Optional, Tuple

_STRIP = re.compile(r"[\s\(\)\[\]\{\}!?.,:;·•'\"“”‘’\-_~/|]+")
_LEVEL_SUFFIX = re.compile(r"(레벨\s*\d+|lvl?\s*\d+|lv\.?\s*\d+|\+\d+)$", re.IGNORECASE)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").strip().lower()
    return _STRIP.sub("", t)


def strip_level_suffix(text: str) -> str:
    return _LEVEL_SUFFIX.sub("", (text or "").strip()).strip()


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for index, ca in enumerate(a, 1):
        current = [index]
        best = index
        for other_index, cb in enumerate(b, 1):
            value = min(
                previous[other_index] + 1,
                current[other_index - 1] + 1,
                previous[other_index - 1] + (ca != cb),
            )
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


class NameIndex:
    """정규화한 이름 → ID. 같은 이름이 두 개 이상이면 모호한 이름으로 따로 둔다."""

    def __init__(self, names: Iterable[Tuple[str, str]]):
        self._map: Dict[str, str] = {}
        self.ambiguous: Dict[str, set] = {}
        for ident, name in names:
            key = normalize(name)
            if not key:
                continue
            if key in self.ambiguous:
                self.ambiguous[key].add(ident)
            elif key in self._map and self._map[key] != ident:
                self.ambiguous[key] = {self._map.pop(key), ident}
            else:
                self._map[key] = ident

    def exact(self, text: str) -> Optional[str]:
        key = normalize(strip_level_suffix(text))
        return self._map.get(key)

    def fuzzy(self, text: str) -> Optional[str]:
        key = normalize(strip_level_suffix(text))
        if len(key) < 3:
            return None
        if key in self._map:
            return self._map[key]
        hits = [
            ident for name, ident in self._map.items() if len(name) >= 3 and edit_distance(key, name, 1) <= 1
        ]
        return hits[0] if len(hits) == 1 else None

    def __len__(self) -> int:
        return len(self._map)
