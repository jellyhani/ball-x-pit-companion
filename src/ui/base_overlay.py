"""기지 채집 조준 화면 위에 그리는 안내: 필요한 자원 건물 표시, 조준 추천선, 짧은 설명. 클릭은 게임으로 통과.

게임 창 전체를 덮는 투명 창이다. 좌표는 게임 클라이언트 물리 픽셀 → 화면 논리 좌표로 바꿔 그린다.
렉 줄이기: 내용이 바뀔 때만, 바뀐 부분(이전·새 내용의 경계 상자)만 다시 그린다. 투명 창은 다시 그릴 때마다
그 영역을 통째로 화면 합성기에 넘기므로, 게임 창 전체(수백만 픽셀)를 초당 여러 번 다시 그리면 끊김이 생긴다.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..services import game_window as gw
from . import tokens as tk
from ..i18n import tr

log = logging.getLogger(__name__)

RES_COLOR = {0: (255, 204, 64, 255), 1: (255, 214, 102, 255), 2: (120, 200, 120, 255), 3: (180, 180, 196, 255)}
BUILD_COLOR = (255, 159, 10, 255)       # 미완성 건물 (주황)
BOX_W_MAX = 720
LINE_H = 22


class BaseOverlay(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle(tr("BALL x PIT 채집 안내"))
        self._scale = 1.0            # 물리 → 논리
        self._targets: List[Tuple[float, float]] = []
        self._build: List[Tuple[float, float]] = []         # 미완성 건물 (먼저 맞힐 곳)
        self._player: Optional[Tuple[float, float]] = None
        self._aim: Optional[Tuple[float, float]] = None
        self._need = 3
        self._lines: List[Tuple[str, Tuple[int, int, int, int]]] = []
        self._path_now: List[Tuple[float, float]] = []      # 지금 조준의 예상 경로 (게임 화면 좌표)
        self._path_best: List[Tuple[float, float]] = []     # 추천 각도의 예상 경로
        self._want_excluded = True
        self._swap_marks: List[Tuple[float, float, float, float, str]] = []   # 재배치 안내 (화면 좌표 두 점, 번호)
        self._box_pts: List[Tuple[float, float]] = []       # 다음 옮길 자리 윤곽 (화면 좌표 네 점)
        self._drawn = QRect()        # 마지막으로 그린 내용의 경계 (논리 좌표)
        self._font = QFont(tk.family())
        self._font.setPixelSize(14)
        self._fm = QFontMetrics(self._font)

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
            log.exception("채집 안내 창 속성 적용 실패")

    # ---- 내용 설정: 바뀐 것이 있을 때만 다시 그린다 ----
    def _changed(self):
        new = self._content_rect()
        dirty = new.united(self._drawn) if not self._drawn.isNull() else new
        self._drawn = new
        if not dirty.isNull():
            self.update(dirty)

    def set_swap_marks(self, marks):
        marks = list(marks)
        if marks != self._swap_marks:
            self._swap_marks = marks
            self._changed()

    def set_target_box(self, pts):
        pts = [(round(float(x), 1), round(float(y), 1)) for x, y in pts]
        if pts != self._box_pts:
            self._box_pts = pts
            self._changed()

    def set_land(self, pts):
        """기지 땅 네 모서리 (게임 화면 물리 좌표). 안내 글상자를 땅·건물 위에 놓지 않으려고 쓴다."""
        pts = [tuple(p) for p in pts or []]
        if pts != getattr(self, "_land", []):
            self._land = pts
            self._changed()

    def set_build_marks(self, pts):
        pts = [(float(x), float(y)) for x, y in pts]
        if pts != self._build:
            self._build = pts
            self._changed()

    def set_paths(self, now: List[Tuple[float, float]], best: List[Tuple[float, float]]):
        now = [(round(x, 1), round(y, 1)) for x, y in now]
        best = [(round(x, 1), round(y, 1)) for x, y in best]
        if now != self._path_now or best != self._path_best:
            self._path_now, self._path_best = now, best
            self._changed()

    def show_advice(self, game_rect: QRect, phys_scale: float, adv, player, texts: List[Tuple[str, tuple]]):
        """game_rect: 게임 창(논리 좌표). phys_scale: 물리 픽셀 → 논리 픽셀 배율."""
        if self.geometry() != game_rect:
            self.setGeometry(game_rect)       # 창 크기가 바뀌면 Qt 가 전체를 다시 그린다
            self._drawn = QRect()
        state = (phys_scale, list(adv.targets) if adv else [], adv.aim_point if adv else None,
                 adv.need if adv else 3,
                 (player[0], player[1]) if player and len(player) >= 2 and player[0] >= 0 else None, list(texts))
        if state == (self._scale, self._targets, self._aim, self._need, self._player, self._lines):
            return
        self._scale, self._targets, self._aim, self._need, self._player, self._lines = state
        self._changed()

    # ---- 그리기 ----
    def _box_rect(self) -> QRectF:
        """안내 글상자: 네 모서리 후보 중 기지 땅·표시(목표 번호·공사 표시)를 가장 덜 가리는 곳.
        (땅이 넓어지면 왼쪽 아래 고정 자리가 건물 이름·번호를 가렸다.) 궤적 선은 계속 움직여서 기준에서 뺀다."""
        w = max(300, min(BOX_W_MAX, max((self._fm.horizontalAdvance(t) for t, _ in self._lines), default=0) + 28))
        h = 16 + LINE_H * len(self._lines)
        W, H = self.width(), self.height()
        cands = [QRectF(24, H - h - 150, w, h), QRectF(W - w - 24, H - h - 150, w, h),
                 QRectF(24, 96, w, h), QRectF(W - w - 24, 96, w, h)]
        k = self._scale
        land = getattr(self, "_land", [])
        land_r = QRectF()
        if len(land) >= 3:
            xs, ys = [x * k for x, _ in land], [y * k for _, y in land]
            land_r = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        marks = [QRectF(x * k - 26, y * k - 26, 52, 52) for x, y in list(self._targets) + list(self._build)]
        marks += [QRectF(x * k - 30, y * k - 30, 60, 60) for m in self._swap_marks for x, y in (m[:2], m[2:4])]

        def cost(r: QRectF) -> float:
            a = r.intersected(land_r) if not land_r.isNull() else QRectF()
            c = a.width() * a.height() if not a.isEmpty() else 0.0
            for m in marks:
                i = r.intersected(m)
                if not i.isEmpty():
                    c += 20 * i.width() * i.height()     # 번호·표시를 가리는 건 훨씬 나쁘다
            return c

        # 위쪽 모서리는 게임 상단 표시(자원 등)와 겹칠 수 있어 조금 불리하게, 같으면 왼쪽 아래(예전 자리) 우선
        best = min(range(len(cands)), key=lambda i: (cost(cands[i]) + (0.3 * w * h if i >= 2 else 0.0), i))
        return cands[best]

    def _content_rect(self) -> QRect:
        k = self._scale
        r = QRectF()

        def add(x, y, pad):
            nonlocal r
            q = QRectF(x * k - pad, y * k - pad, 2 * pad, 2 * pad)
            r = q if r.isNull() else r.united(q)

        for x, y in self._targets:
            add(x, y, 22)
        for x, y in self._build:
            add(x, y, 40)
        for path in (self._path_now, self._path_best):
            for x, y in path:
                add(x, y, 4)
        if self._player and self._aim:
            add(*self._player, 10)
            add(*self._aim, 10)
        for x1, y1, x2, y2, _ in self._swap_marks:
            add(x1, y1, 26)
            add(x2, y2, 26)
        for x, y in self._box_pts:
            add(x, y, 6)
        if self._lines:
            b = self._box_rect()
            r = b if r.isNull() else r.united(b)
        return r.toAlignedRect().adjusted(-2, -2, 2, 2) if not r.isNull() else QRect()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self._scale
        c = QColor(*RES_COLOR.get(self._need, tk.ACCENT))
        # 필요한 자원 건물: 옅은 링
        for x, y in self._targets:
            p.setPen(QPen(c, 2))
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 40))
            p.drawEllipse(QPointF(x * k, y * k), 18, 18)
        # 미완성 건물: 굵은 주황 링 + '완성' (여기부터 맞히기)
        if self._build:
            bc = QColor(*BUILD_COLOR)
            f3 = QFont(tk.family())
            f3.setPixelSize(12)
            f3.setWeight(QFont.Weight.Bold)
            for x, y in self._build:
                q = QPointF(x * k, y * k)
                p.setPen(QPen(bc, 4))
                p.setBrush(QColor(bc.red(), bc.green(), bc.blue(), 50))
                p.drawEllipse(q, 26, 26)
                p.setFont(f3)
                p.setPen(bc)
                p.drawText(QRectF(q.x() - 30, q.y() - 40, 60, 14), Qt.AlignmentFlag.AlignCenter, tr("완성"))
        # 예상 경로: 추천(파란 실선), 지금 조준(흰 점선)
        for path, pen in ((self._path_best, QPen(QColor(64, 156, 255, 220), 3)),
                          (self._path_now, QPen(QColor(255, 255, 255, 170), 2, Qt.PenStyle.DashLine))):
            if len(path) >= 2:
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                pts = [QPointF(x * k, y * k) for x, y in path]
                for a, b in zip(pts, pts[1:]):
                    p.drawLine(a, b)
        # 조준 추천선: 발사대 → 추천 지점 (궤적이 없을 때만)
        if self._player and self._aim and not self._path_best:
            pen = QPen(tk.qcolor(tk.ACCENT), 3, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(self._player[0] * k, self._player[1] * k), QPointF(self._aim[0] * k, self._aim[1] * k))
            p.setBrush(tk.qcolor(tk.ACCENT))
            p.drawEllipse(QPointF(self._aim[0] * k, self._aim[1] * k), 7, 7)
        # 재배치 안내: 맞바꿀 두 건물에 번호 원과 연결선
        for x1, y1, x2, y2, label in self._swap_marks:
            a, b = QPointF(x1 * k, y1 * k), QPointF(x2 * k, y2 * k)
            p.setPen(QPen(tk.qcolor(tk.WARN), 4))
            p.drawLine(a, b)
            for q in (a, b):
                p.setBrush(QColor(20, 20, 24, 230))
                p.drawEllipse(q, 22, 22)
                f2 = QFont(tk.family())
                f2.setPixelSize(16)
                f2.setWeight(QFont.Weight.Bold)
                p.setFont(f2)
                p.setPen(tk.qcolor(tk.WARN))
                p.drawText(QRectF(q.x() - 22, q.y() - 22, 44, 44), Qt.AlignmentFlag.AlignCenter, label)
                p.setPen(QPen(tk.qcolor(tk.WARN), 4))
        # 다음 옮길 자리: 주황 점선 윤곽
        if len(self._box_pts) >= 3:
            path = QPainterPath(QPointF(self._box_pts[0][0] * k, self._box_pts[0][1] * k))
            for x, y in self._box_pts[1:]:
                path.lineTo(QPointF(x * k, y * k))
            path.closeSubpath()
            p.setPen(QPen(tk.qcolor(tk.WARN), 3, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 159, 10, 45))
            p.drawPath(path)
        # 설명 상자: 왼쪽 아래
        if self._lines:
            p.setFont(self._font)
            box = self._box_rect()
            path = QPainterPath()
            path.addRoundedRect(box, 12, 12)
            p.fillPath(path, QColor(24, 24, 27, 225))
            y = box.top() + 8
            for text, color in self._lines:
                p.setPen(QColor(*color))
                p.drawText(QRectF(box.left() + 14, y, box.width() - 28, LINE_H),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           self._fm.elidedText(text, Qt.TextElideMode.ElideRight, int(box.width() - 28)))
                y += LINE_H
