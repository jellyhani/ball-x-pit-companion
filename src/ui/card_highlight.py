"""선택지 카드마다 판정 색 테두리와 작은 이름표를 그리는 오버레이. 카드 내용(아이콘·문구)은 가리지 않는다.

색은 HUD 목록과 같다: 추천 파랑(굵은 실선) · 비슷함 초록 · 삭제 추천 주황 · 비추천 빨강 · 보류 회색.
비추천 카드는 살짝 어둡게 덮어 추천 카드가 먼저 보이게 한다. 이름표는 카드 위쪽 가장자리에 걸친다.
클릭은 게임으로 통과하고, 캡처 제외 여부는 HUD 설정을 따른다.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..services import game_window as gw
from . import tokens as tk

log = logging.getLogger(__name__)


class CardHighlight(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._marks: List[Tuple] = []   # (카드 영역, best | alt | banish | skip | neutral, 이름표)
        self._want_excluded = True

    def set_capture_excluded(self, on: bool):
        self._want_excluded = on
        if self.isVisible():
            gw.set_capture_exclusion(int(self.winId()), on)

    def set_marks(self, marks: List[Tuple]):
        """marks: (화면 논리 좌표 사각형, 판정, [이름표 글자]). 같은 내용이면 다시 그리지 않는다."""
        marks = [m if len(m) >= 3 else (m[0], m[1], "") for m in marks]
        if marks == self._marks and (self.isVisible() or not marks):
            return
        self._marks = marks
        if not marks:
            self.hide()
            return
        area = marks[0][0]
        for m in marks[1:]:
            area = area.united(m[0])
        area = area.adjusted(-8, -24, 8, 8)     # 위쪽은 이름표 자리
        self.setGeometry(area)
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        try:
            hwnd = int(self.winId())
            gw.set_capture_exclusion(hwnd, self._want_excluded)
            gw.set_click_through(hwnd, True)
        except OSError:
            log.exception("카드 강조 창 속성 적용 실패")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin = self.geometry().topLeft()
        font = QFont(tk.family())
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.Bold)
        fm = QFontMetricsF(font)
        for rect, kind, badge in self._marks:
            r = QRectF(rect.translated(-origin)).adjusted(-3, -3, 3, 3)
            color, label = tk.VERDICT.get(kind, (tk.TEXT_2, ""))
            label = badge or label
            c = tk.qcolor(color)
            p.setBrush(Qt.BrushStyle.NoBrush)       # 이전 이름표의 바탕 붓이 남지 않게
            if kind == "skip":
                p.fillRect(r.adjusted(3, 3, -3, -3), QColor(0, 0, 0, 70))    # 비추천: 살짝 어둡게
            if kind == "best":
                glow = QColor(c)
                glow.setAlpha(70)
                gp = QPen(glow)
                gp.setWidthF(7.0)
                p.setPen(gp)
                p.drawRoundedRect(r, 9, 9)
            pen = QPen(c)
            pen.setWidthF({"best": 3.0, "alt": 2.5}.get(kind, 2.0))
            if kind in ("skip", "neutral", "banish"):
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)
            if label:
                self._badge(p, r, label, c, font, fm)

    @staticmethod
    def _badge(p: QPainter, r: QRectF, text: str, color: QColor, font: QFont, fm: QFontMetricsF):
        """카드 위쪽 가장자리 가운데에 걸친 이름표 (진한 바탕 + 판정 색 글자·테두리)."""
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 8
        b = QRectF(r.center().x() - w / 2, r.top() - h / 2, w, h)
        p.setPen(QPen(color, 1.5))
        p.setBrush(QColor(20, 20, 24, 235))
        p.drawRoundedRect(b, h / 2, h / 2)
        p.setFont(font)
        p.setPen(color)
        p.drawText(b, Qt.AlignmentFlag.AlignCenter, text)
