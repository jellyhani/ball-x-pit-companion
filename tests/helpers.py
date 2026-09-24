"""테스트 공용 도구. 합성 입력은 '합성'이라고 이름에 드러낸다."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional, Sequence, Tuple

from src.domain import (Card, CardLabel, ChoiceSession, FrameInfo, InventorySlot, OcrLine, ScreenKind,
                        ScreenObservation)
from src.gamedata import GameData, load_game_data

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
LEVELUP_PNG = os.path.join(FIXTURES, "levelup_1080p_itchy.png")


@lru_cache(maxsize=1)
def game_data() -> GameData:
    return load_game_data()


def frame(size=(1920, 1080), origin=(0, 0), fid=1) -> FrameInfo:
    return FrameInfo(fid, 0.0, origin, size, "test")


def synthetic_levelup_lines(labels: Sequence[Tuple[float, str]] = ((161, "레벨 2+"), (464, "신규!"), (764, "신규!")),
                            header: str = "강화 선택", reroll: Optional[str] = "새로고침 (5 이",
                            scale: float = 1.0):
    """사용자 스크린샷(1920×1080)에서 Windows OCR이 실제로 돌려준 줄 위치를 본뜬 합성 입력."""
    s = scale
    out = [OcrLine("|픠 백과사전", int(282 * s), int(43 * s), int(202 * s), int(58 * s)),
           OcrLine(header, int(396 * s), int(525 * s), int(135 * s), int(34 * s))]
    for cx, text in labels:
        out.append(OcrLine(text, int((cx - 35) * s), int(869 * s), int(70 * s), int(33 * s)))
    if reroll:
        out.append(OcrLine(reroll, int(356 * s), int(990 * s), int(226 * s), int(37 * s)))
    return out


def card(i: int, item: Optional[str], label: Optional[CardLabel] = CardLabel.NEW, level: Optional[int] = None,
         position: Optional[str] = None) -> Card:
    pos = position or ["왼쪽", "가운데", "오른쪽"][i]
    x = 35 + 300 * i
    return Card(index=i, position=pos, rect=(x, 575, 260, 350), icon_rect=(x + 40, 580, 180, 180),
                item_id=item, label=label, shown_level=level)


def slots(*items: Tuple[Optional[str], Optional[int]], unreadable: int = 0) -> Tuple[InventorySlot, ...]:
    out = []
    for i, (item, lvl) in enumerate(items):
        out.append(InventorySlot(i, (0, 0, 10, 10), True, item, lvl))
    for j in range(unreadable):
        out.append(InventorySlot(len(out), (0, 0, 10, 10), True, None, None))
    while len(out) < 8:
        out.append(InventorySlot(len(out), (0, 0, 10, 10), False))
    return tuple(out)


def levelup_obs(cards: Sequence[Card], **kw) -> ScreenObservation:
    kw.setdefault("frame", frame())
    return ScreenObservation(kind=ScreenKind.LEVEL_UP, cards=tuple(cards), **kw)


def session(cards: Sequence[Card], sid: int = 1, **kw) -> ChoiceSession:
    return ChoiceSession(session_id=sid, opened_at=0.0, cards=tuple(cards), frame=frame(), **kw)
