"""전투 중 작은 초당 피해(DPS) 창. 포커스를 가져가지 않고 클릭은 게임으로 통과한다.

한 위젯이 직접 그린다: 제목 줄(전체 DPS), 볼마다 아이콘 · 이름 · 비율 막대 · 숫자.
색은 HUD 와 같은 재질, 막대는 강조색 하나(파랑)를 비율만큼.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ..gamedata import GameData
from ..services import game_window as gw
from ..tracking.dps import DpsRow
from . import tokens as tk
from .hud import icon_tile

log = logging.getLogger(__name__)

MAX_ROWS = 6


def fmt(v: float) -> str:
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v:.0f}"


class DpsMeter(QWidget):
    def __init__(self, data: GameData, scale: float = 1.0):
        super().__init__(None)
        self.data = data
        self.scale = scale
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("BALL x PIT 초당 피해")
        self._rows: List[DpsRow] = []
        self._total = 0.0
        self._window_s = 10
        self._want_excluded = True
        self._battle = None       # BattleInfo: 보스 체력·적 수 줄
        self._resize()

    def px(self, v: float) -> int:
        return round(v * self.scale)

    def apply_scale(self, scale: float):
        self.scale = scale
        self._resize()
        self.update()

    def _extra_line(self) -> str:
        b = self._battle
        if b is None:
            return ""
        parts = []
        if b.boss_ratio is not None:
            parts.append(f"보스 체력 {round(b.boss_ratio * 100)}%")
        if b.enemies is not None:
            parts.append(f"적 {b.enemies}")
        return " · ".join(parts)

    def set_battle(self, b):
        changed = bool(self._extra_line()) != bool(b and (b.boss_ratio is not None or b.enemies is not None))
        self._battle = b
        if changed:
            self._resize()

    def _resize(self):
        n = max(1, min(MAX_ROWS, len(self._rows)))
        extra = 20 if self._extra_line() else 0
        self.setFixedSize(self.px(250), self.px(44 + extra + 28 * n + 12))

    def set_capture_excluded(self, on: bool):
        self._want_excluded = on
        if self.isVisible():
            gw.set_capture_exclusion(int(self.winId()), on)

    def showEvent(self, e):
        super().showEvent(e)
        try:
            hwnd = int(self.winId())
            gw.set_capture_exclusion(hwnd, self._want_excluded)
            gw.set_click_through(hwnd, True)
        except OSError:
            log.exception("DPS 창 속성 적용 실패")

    def set_rows(self, rows: List[DpsRow], total: float, window_s: float):
        self._rows, self._total, self._window_s = rows[:MAX_ROWS], total, int(window_s)
        self._resize()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(card, self.px(12), self.px(12))
        p.fillPath(path, QColor(24, 24, 27, 214))
        p.setPen(QColor(255, 255, 255, 26))
        p.drawPath(path)

        pad = self.px(12)
        title = QFont(tk.family())
        title.setPixelSize(self.px(12))
        title.setWeight(QFont.Weight.DemiBold)
        body = QFont(tk.family())
        body.setPixelSize(self.px(12))
        num = QFont(tk.family())
        num.setPixelSize(self.px(12))
        num.setWeight(QFont.Weight.DemiBold)

        # 제목 줄: '초당 피해 · 최근 10초' ... 전체
        p.setFont(title)
        p.setPen(tk.qcolor(tk.TEXT_2))
        head = QRectF(pad, self.px(10), card.width() - 2 * pad, self.px(18))
        p.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"초당 피해 · 최근 {self._window_s}초")
        p.setPen(tk.qcolor(tk.TEXT))
        p.drawText(head, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt(self._total))

        y0 = self.px(38)
        extra = self._extra_line()
        if extra:
            p.setFont(body)
            b = self._battle
            p.setPen(tk.qcolor(tk.DANGER) if b.boss_ratio is not None else tk.qcolor(tk.TEXT_2))
            p.drawText(QRectF(pad, self.px(30), card.width() - 2 * pad, self.px(18)),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, extra)
            if b.boss_ratio is not None:
                bh = self.px(3)
                track = QPainterPath()
                track.addRoundedRect(QRectF(pad, self.px(50), card.width() - 2 * pad, bh), bh / 2, bh / 2)
                p.fillPath(track, QColor(255, 255, 255, 18))
                fill = QPainterPath()
                fill.addRoundedRect(QRectF(pad, self.px(50), (card.width() - 2 * pad) * b.boss_ratio, bh), bh / 2, bh / 2)
                p.fillPath(fill, tk.qcolor(tk.DANGER))
            y0 += self.px(20)
        if not self._rows:
            p.setFont(body)
            p.setPen(tk.qcolor(tk.TEXT_3))
            p.drawText(QRectF(pad, y0 - self.px(2), card.width() - 2 * pad, self.px(24)),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "피해 기록을 모으는 중")
            return
        fm = QFontMetricsF(body)
        icon = self.px(20)
        y = y0
        name_w = self.px(92)
        bar_x = pad + icon + self.px(8) + name_w
        num_w = self.px(40)
        bar_w = card.width() - pad - num_w - self.px(6) - bar_x
        top = self._rows[0].dps or 1.0
        for r in self._rows:
            pm = icon_tile((r.item_id,), icon)
            p.drawPixmap(int(pad), int(y), pm)
            p.setFont(body)
            p.setPen(tk.qcolor(tk.TEXT))
            name = fm.elidedText(self.data.name(r.item_id), Qt.TextElideMode.ElideRight, name_w - self.px(4))
            p.drawText(QRectF(pad + icon + self.px(8), y, name_w, icon),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            # 막대: 1위 대비 길이, 비율이 클수록 진한 파랑
            bh = self.px(6)
            by = y + (icon - bh) / 2
            track = QPainterPath()
            track.addRoundedRect(QRectF(bar_x, by, bar_w, bh), bh / 2, bh / 2)
            p.fillPath(track, QColor(255, 255, 255, 18))
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(bar_x, by, max(bh, bar_w * r.dps / top), bh), bh / 2, bh / 2)
            c = tk.qcolor(tk.ACCENT)
            c.setAlpha(120 + int(135 * min(1.0, r.share * 2)))
            p.fillPath(fill, c)
            p.setFont(num)
            p.setPen(tk.qcolor(tk.TEXT))
            p.drawText(QRectF(card.width() - pad - num_w, y, num_w, icon),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt(r.dps))
            y += self.px(28)
