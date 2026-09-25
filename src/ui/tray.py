"""작업 표시줄 알림 영역 아이콘. 실행 중임을 알리고 열기·숨기기·종료를 제공한다."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..i18n import tr
from . import tokens as tk


def app_icon() -> QIcon:
    """단색 원 두 개로 그린 앱 아이콘 (외부 이미지 없이)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(tk.qcolor(tk.SURFACE_2))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.setBrush(tk.qcolor(tk.ACCENT))
        r = size * 0.34
        p.drawEllipse(QRectF(size * 0.5 - r / 2, size * 0.22, r, r))
        pen = QPen(tk.qcolor(tk.TEXT))
        pen.setWidthF(max(1.0, size / 16))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(size * 0.2, size * 0.45, size * 0.6, size * 0.4), 200 * 16, 140 * 16)
        p.end()
        icon.addPixmap(pm)
    return icon


class Tray(QSystemTrayIcon):
    open_requested = Signal()
    hud_toggle_requested = Signal()
    scan_requested = Signal()
    quit_requested = Signal()

    def __init__(self):
        super().__init__(app_icon())
        self.setToolTip("BALL x PIT 도우미")
        menu = QMenu()
        for text, sig in ((tr("창 열기") + "  (F10)", self.open_requested),
                          (tr("HUD 숨기기 / 보이기") + "  (F9)", self.hud_toggle_requested),
                          (tr("지금 다시 읽기") + "  (F8)", self.scan_requested)):
            a = QAction(text, menu)
            a.triggered.connect(sig.emit)
            menu.addAction(a)
        menu.addSeparator()
        q = QAction(tr("종료"), menu)
        q.triggered.connect(self.quit_requested.emit)
        menu.addAction(q)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()
