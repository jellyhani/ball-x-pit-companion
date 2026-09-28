"""애플 설정 앱 느낌의 작은 위젯들: 토글 스위치, 세그먼트 버튼, 둥근 그룹 목록."""

from __future__ import annotations

from typing import List, Optional, Sequence

from PySide6.QtCore import QPropertyAnimation, QRectF, QSize, Qt, Property, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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

    def set_knob(self, value: float):
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rectangle = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        color = QColor(120, 120, 128, 90)
        current_color = QColor(
            int(color.red() + (GREEN.red() - color.red()) * self._pos),
            int(color.green() + (GREEN.green() - color.green()) * self._pos),
            int(color.blue() + (GREEN.blue() - color.blue()) * self._pos),
            int(color.alpha() + (255 - color.alpha()) * self._pos),
        )
        path = QPainterPath()
        path.addRoundedRect(rectangle, rectangle.height() / 2, rectangle.height() / 2)
        painter.fillPath(path, current_color)
        d = rectangle.height() - 4
        x = rectangle.left() + 2 + (rectangle.width() - d - 4) * self._pos
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(x, rectangle.top() + 2, d, d))


class Segmented(QFrame):
    """세그먼트 버튼 (하나만 선택)."""

    changed = Signal(int)

    def __init__(self, labels: Sequence[str], current: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        horizontal_layout = QHBoxLayout(self)
        horizontal_layout.setContentsMargins(2, 2, 2, 2)
        horizontal_layout.setSpacing(2)
        self.group = QButtonGroup(self)
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setChecked(index == current)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button, index)
            horizontal_layout.addWidget(button)
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

    def add(self, widget: QWidget):
        if self._rows:
            frame = QFrame()
            frame.setObjectName("hairline")
            frame.setFixedHeight(1)
            self._lay.addWidget(frame)
        self._rows.append(widget)
        self._lay.addWidget(widget)
        return widget

    def clear(self):
        while self._lay.count():
            layout_item = self._lay.takeAt(0)
            widget = layout_item.widget()
            if widget:
                widget.hide()  # 다시 그릴 때 이전 행이 잠깐이라도 겹쳐 보이지 않게 바로 떼어 낸다
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []


def row(
    title: str, value: Optional[QWidget] = None, subtitle: str = "", icon: Optional[QLabel] = None
) -> QWidget:
    """그룹 안의 한 줄: [아이콘] 제목/부제 ........ 값(위젯)."""
    widget = QWidget()
    widget.setObjectName("row")
    horizontal_layout = QHBoxLayout(widget)
    horizontal_layout.setContentsMargins(14, 9, 14, 9)
    horizontal_layout.setSpacing(10)
    if icon is not None:
        horizontal_layout.addWidget(icon)
    vertical_layout = QVBoxLayout()
    vertical_layout.setSpacing(3)
    label = QLabel(title)
    label.setObjectName("rowTitle")
    label.setWordWrap(True)
    vertical_layout.addWidget(label)
    if subtitle:
        current_label = QLabel(subtitle)
        current_label.setObjectName("rowSub")
        current_label.setWordWrap(True)
        vertical_layout.addWidget(current_label)
    horizontal_layout.addLayout(vertical_layout, 1)
    if value is not None:
        horizontal_layout.addWidget(value, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return widget


def value_label(text: str, role: str = "rowValue", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    return label


def chip(text: str, tone: str = "neutral") -> QLabel:
    label = QLabel(text)
    label.setObjectName(f"chip_{tone}")
    return label


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label
