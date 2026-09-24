"""애플 설정 앱 느낌의 작은 위젯들: 토글 스위치, 세그먼트 버튼, 둥근 그룹 목록."""
from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt6.QtCore import QPropertyAnimation, QRectF, QSize, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QSizePolicy, QVBoxLayout, QWidget)

ACCENT = QColor(10, 132, 255)
GREEN = QColor(48, 209, 88)


class Toggle(QAbstractButton):
    """iOS 스타일 켜기/끄기 스위치."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(42, 24)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float):
        self._pos = v
        self.update()

    knob = pyqtProperty(float, get_knob, set_knob)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor(120, 120, 128, 90)
        track = QColor(
            int(off.red() + (GREEN.red() - off.red()) * self._pos),
            int(off.green() + (GREEN.green() - off.green()) * self._pos),
            int(off.blue() + (GREEN.blue() - off.blue()) * self._pos),
            int(off.alpha() + (255 - off.alpha()) * self._pos))
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, track)
        d = r.height() - 4
        x = r.left() + 2 + (r.width() - d - 4) * self._pos
        p.setBrush(QColor(255, 255, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class Segmented(QFrame):
    """세그먼트 버튼 (하나만 선택)."""

    changed = pyqtSignal(int)

    def __init__(self, labels: Sequence[str], current: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setChecked(i == current)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.idClicked.connect(self.changed.emit)


class Group(QFrame):
    """둥근 모서리 그룹. 행 사이에 머리카락 굵기 구분선을 넣는다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("group")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._rows: List[QWidget] = []

    def add(self, w: QWidget):
        if self._rows:
            sep = QFrame()
            sep.setObjectName("hairline")
            sep.setFixedHeight(1)
            self._lay.addWidget(sep)
        self._rows.append(w)
        self._lay.addWidget(w)
        return w

    def clear(self):
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()              # 다시 그릴 때 이전 행이 잠깐이라도 겹쳐 보이지 않게 바로 떼어 낸다
                w.setParent(None)
                w.deleteLater()
        self._rows = []


def row(title: str, value: Optional[QWidget] = None, subtitle: str = "", icon: Optional[QLabel] = None) -> QWidget:
    """그룹 안의 한 줄: [아이콘] 제목/부제 ........ 값(위젯)."""
    w = QWidget()
    w.setObjectName("row")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 9, 14, 9)
    lay.setSpacing(10)
    if icon is not None:
        lay.addWidget(icon)
    col = QVBoxLayout()
    col.setSpacing(3)
    t = QLabel(title)
    t.setObjectName("rowTitle")
    t.setWordWrap(True)
    col.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("rowSub")
        s.setWordWrap(True)
        col.addWidget(s)
    lay.addLayout(col, 1)
    if value is not None:
        lay.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return w


def value_label(text: str, role: str = "rowValue", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(role)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setWordWrap(wrap)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    return lbl


def chip(text: str, tone: str = "neutral") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName(f"chip_{tone}")
    return lbl


def section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl
