"""물리 픽셀(Win32·캡처 좌표) ↔ Qt 논리 좌표 변환과 HUD 배치.

Qt6(Windows)는 각 모니터의 왼쪽 위 좌표를 물리 좌표 그대로 두고 크기만 배율로 나눈다.
그래서 점이 속한 모니터를 물리 좌표로 찾은 뒤, 그 모니터 원점 기준으로 배율을 나눈다.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

Rect = Tuple[int, int, int, int]


def _screens():
    for s in QGuiApplication.screens():
        g = s.geometry()
        dpr = s.devicePixelRatio()
        native = QRect(g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr))
        yield s, g, dpr, native


def phys_to_logical_point(x: int, y: int) -> QPoint:
    for s, g, dpr, native in _screens():
        if native.contains(x, y):
            return QPoint(g.x() + round((x - g.x()) / dpr), g.y() + round((y - g.y()) / dpr))
    return QPoint(x, y)


def phys_to_logical_rect(rect: Rect) -> QRect:
    x, y, w, h = rect
    tl = phys_to_logical_point(x, y)
    br = phys_to_logical_point(x + w - 1, y + h - 1)
    return QRect(tl, br)


# 강화 선택창 (1920×1080 기준, 실제 화면): 첫 카드 (30, 570, 270×360), 캐릭터 초상화 틀 (20, 115, 370×370).
# 초상화는 장식이라 가려도 잃는 정보가 없다 — 볼 슬롯(455~)·카드·삭제 버튼·오른쪽 설명 패널은 모두 글자·정보.
CARD_REF_W = 270
PORTRAIT_FROM_CARD = (-10, -455, 370, 370)      # 첫 카드 왼쪽 위 기준 초상화 틀 (x, y, w, h)
TITLE_GAP = 40                                  # 카드 위 '강화 선택' 제목·순위 배지 자리


def levelup_hud_spot(game: QRect, cards: Sequence[QRect], hud_w: int, hud_h: int, margin: int = 8) -> Optional[QPoint]:
    """강화 선택창: HUD 를 캐릭터 초상화 자리에 (카드 위치로 계산). 카드 배치가 예상과 다르면 None."""
    if not cards:
        return None
    first = min(cards, key=lambda c: c.left())
    if first.width() <= 0 or first.left() - game.left() > game.width() * 0.3:
        return None                                # 카드가 왼쪽에 있는 배치가 아님 (다른 해상도·UI) → 기본 규칙
    s = first.width() / CARD_REF_W
    dx, dy, w, h = PORTRAIT_FROM_CARD
    left = first.left() + round(dx * s)
    top = first.top() + round(dy * s)
    bottom = first.top() - round(TITLE_GAP * s)    # 제목·배지 위까지
    if top + hud_h > bottom:                       # HUD 가 길면 위쪽 여백(백과사전 표시 쪽)으로 올린다
        top = max(game.top() + margin, bottom - hud_h)
    return QPoint(max(game.left() + margin, left), top)


def place_hud(game: QRect, cards: Sequence[QRect], hud_w: int, hud_h: int, margin: int = 12,
              offset: Tuple[int, int] = (0, 0), panel: Optional[QRect] = None) -> QPoint:
    """게임의 강화 패널·카드를 가리지 않는 자리를 고른다.

    패널 옆 빈 공간(아래쪽 정렬) → 카드 위 → 카드 아래 → 게임 창 오른쪽 위 순서.
    """
    cx = game.center().x() - hud_w // 2
    cands: List[QPoint] = []
    avoid: List[QRect] = list(cards)
    if panel is not None:
        avoid.append(panel)
        # 패널 옆 아래쪽에 붙인다. 카드에 마우스를 올리면 게임이 패널 오른쪽에 설명(시너지 장비 등)을
        # 띄우는데, 그 창의 아래쪽은 비어 있어 가려도 정보가 줄지 않는다.
        top = min(panel.bottom(), game.bottom()) - hud_h - margin
        top = max(game.top() + margin, top)
        if game.right() - panel.right() >= hud_w + 3 * margin:
            cands.append(QPoint(panel.right() + 2 * margin, top))
        if panel.left() - game.left() >= hud_w + 3 * margin:
            cands.append(QPoint(panel.left() - hud_w - 2 * margin, top))
    if cards:
        top = min(c.top() for c in cards)
        bottom = max(c.bottom() for c in cards)
        if top - game.top() >= hud_h + 2 * margin:
            cands.append(QPoint(cx, top - hud_h - margin))
        if game.bottom() - bottom >= hud_h + 2 * margin:
            cands.append(QPoint(cx, bottom + margin))
    cands.append(QPoint(game.right() - hud_w - margin, game.top() + margin))
    chosen = cands[0]
    for p in cands:
        box = QRect(p.x(), p.y(), hud_w, hud_h)
        if not any(box.intersects(c) for c in avoid):
            chosen = p
            break
    return QPoint(chosen.x() + offset[0], chosen.y() + offset[1])


def clamp_to_screen(p: QPoint, w: int, h: int) -> QPoint:
    screen = QGuiApplication.screenAt(p) or QGuiApplication.primaryScreen()
    if screen is None:
        return p
    a = screen.availableGeometry()
    return QPoint(min(max(p.x(), a.left()), a.right() - w), min(max(p.y(), a.top()), a.bottom() - h))


def optional_rect(r: Optional[Rect]) -> Optional[QRect]:
    return phys_to_logical_rect(r) if r else None
