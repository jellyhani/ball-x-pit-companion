"""플레이 중에 띄우는 작은 추천 HUD (애플 계열의 절제된 디자인).

- 포커스를 가져가지 않고(WS_EX_NOACTIVATE), 마우스 입력은 게임으로 통과시킨다(WS_EX_TRANSPARENT).
- 화면 캡처에서 제외한다(WDA_EXCLUDEFROMCAPTURE). 적용 여부는 실제로 다시 읽어 확인하고 진단에 표시한다.
- 위치 조정 모드에서만 클릭을 받고 끌어서 옮길 수 있다.

디자인: 반투명 어두운 재질 + 머리카락 굵기 테두리 + 부드러운 그림자, 모서리 16px.
글자 위계는 굵기와 밝기(100% / 60% / 35%)로만 나누고, 강조색(파랑)은 상태 표시 한 곳에만 쓴다.
아이콘은 게임 원본 픽셀 아트를 둥근 사각 타일 안에 픽셀 그대로 키운다.

같은 틀(HudView)로 강화 선택창 추천과 융합 화면 추천을 모두 그린다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..engine.fusion import FusionRecommendation
from ..engine.recommender import ActionEval, Recommendation, card_badge, card_rank, card_verdict
from ..gamedata import DATA_DIR, GameData
from ..i18n import tr
from ..services import game_window as gw
from . import tokens as tk

log = logging.getLogger(__name__)

W = QFont.Weight
SHADOW = 14          # 그림자 여백 (px, 배율 1 기준)

STATUS_TEXT = {"recommend": tr("추천"), "close": tr("차이 작음"), "hold": tr("판단 보류"), "auto": tr("자동 선택"),
              "none": tr("미확인")}


@lru_cache(maxsize=256)
def _sprite(item_id: str) -> Optional[QPixmap]:
    if item_id.startswith("baby:"):
        item_id = "passive:babyrattle"     # 베이비볼은 아이콘이 없어 베이비 딸랑이 아이콘을 빌린다
    pm = QPixmap(os.path.join(DATA_DIR, "icons", item_id.replace(":", "_") + ".png"))
    return None if pm.isNull() else pm


def icon_tile(item_ids: Sequence[Optional[str]], size: int, radius_ratio: float = 0.24) -> QPixmap:
    """둥근 사각 타일 안에 아이콘(1개 또는 2개)을 픽셀 그대로 그린다."""
    dpr = 2.0
    out = QPixmap(int(size * dpr), int(size * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * radius_ratio, size * radius_ratio)
    p.fillPath(tile, QColor(255, 255, 255, 16))
    p.setPen(QPen(QColor(255, 255, 255, 22), 1))
    p.drawPath(tile)
    ids = [i for i in item_ids if i] or [None]
    n = len(ids)
    inner = size * (0.78 if n == 1 else 0.52)
    for k, iid in enumerate(ids[:2]):
        src = _sprite(iid) if iid else None
        if src is None:
            continue
        scaled = src.scaled(int(inner * dpr), int(inner * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation)
        scaled.setDevicePixelRatio(dpr)
        w, h = scaled.width() / dpr, scaled.height() / dpr
        if n == 1:
            x, y = (size - w) / 2, (size - h) / 2
        else:   # 두 개: 왼쪽 위 / 오른쪽 아래로 겹쳐 배치
            x = size * 0.08 if k == 0 else size - w - size * 0.08
            y = size * 0.08 if k == 0 else size - h - size * 0.08
        p.drawPixmap(QRectF(x, y, w, h), scaled, QRectF(0, 0, scaled.width(), scaled.height()))
    p.end()
    return out


@dataclass
class HudRow:
    icons: Tuple[Optional[str], ...]
    text: str
    note: str = ""
    note_tone: str = "tertiary"          # tertiary | secondary | warn
    verdict: str = ""                    # 강화 선택지: best | alt | banish | skip | neutral | unknown
    badge: str = ""                      # 판정 알약 글자 (없으면 판정 이름). 예: '2위 비슷함' '3위'


@dataclass
class HudView:
    title: str
    subtitle: str = ""
    status: str = ""
    status_tone: str = "accent"          # accent | neutral | warn | ok
    icons: Tuple[Optional[str], ...] = ()
    lines: List[Tuple[str, str]] = field(default_factory=list)   # (문장, tone)
    section: str = ""
    rows: List[HudRow] = field(default_factory=list)
    footer: List[Tuple[str, str]] = field(default_factory=list)


TONE = {
    "primary": (245, 245, 247, 255), "secondary": (235, 235, 245, 153), "tertiary": (235, 235, 245, 92),
    "accent": (64, 156, 255, 255), "warn": (255, 159, 10, 255), "ok": (48, 209, 88, 255), "neutral": (235, 235, 245, 153),
    "danger": tk.DANGER, "dim": (235, 235, 245, 120),
}
# 카드 판정 → 색 이름 (tokens.VERDICT 와 같은 색)
VERDICT_TONE = {"best": "accent", "alt": "ok", "banish": "warn", "skip": "danger", "neutral": "neutral", "pick": "accent",
                "unknown": "tertiary"}


class _Label(QLabel):
    def __init__(self, parent=None, wrap=False):
        super().__init__(parent)
        self.setWordWrap(wrap)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def style(self, font: QFont, tone: str, extra: str = ""):
        self.setFont(font)
        self.setStyleSheet(f"color: {tk.css(TONE[tone])}; background: transparent; {extra}")


class _Pill(_Label):
    """상태 표시: 옅게 물든 둥근 배경 + 같은 색 글자."""

    def set(self, text: str, tone: str, font: QFont, pad_h: int, pad_v: int):
        r, g, b, _ = TONE[tone]
        self.setFont(font)
        self.setText(text)
        self.setStyleSheet(f"color: rgba({r},{g},{b},255); background: rgba({r},{g},{b},40);"
                           f"border-radius: {pad_v + font.pixelSize() // 2 + 1}px; padding: {pad_v}px {pad_h}px;")
        self.setVisible(bool(text))


class RecommendationHud(QWidget):
    moved = Signal(QPoint)

    BASE_WIDTH = 372

    def __init__(self, data: GameData, scale: float = 1.0):
        super().__init__(None)
        self.data = data
        self.type = tk.Type(scale)
        self.edit_mode = False
        self.compact = False            # F7: 추천·이유 한 줄·삭제만
        self.capture_excluded: Optional[bool] = None
        self.click_through: Optional[bool] = None
        self._drag_from: Optional[QPoint] = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(tr("BALL x PIT 추천"))
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._view: Optional[HudView] = None
        self._build()
        self.apply_scale(scale)

    # ---- 구성 ----
    def _build(self):
        root = QVBoxLayout(self)
        self._root = root
        top = QHBoxLayout()
        self.icon = _Label(self)
        top.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.headline = _Label(self, wrap=True)
        self.status = _Pill(self)
        title_row.addWidget(self.headline, 1)
        title_row.addWidget(self.status, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        col.addLayout(title_row)
        self.sub = _Label(self, wrap=True)
        col.addWidget(self.sub)
        top.addLayout(col, 1)
        self._top = top
        root.addLayout(top)

        # 이유 줄끼리는 붙이고(한 덩어리), 덩어리 사이만 띄운다
        self._lines_box = QVBoxLayout()
        self.lines = [_Label(self, wrap=True) for _ in range(3)]
        for r in self.lines:
            self._lines_box.addWidget(r)
        root.addLayout(self._lines_box)
        self.sep1 = self._hairline()
        root.addWidget(self.sep1)
        self._rows_box = QVBoxLayout()                 # '다른 선택지' 제목 + 목록 한 덩어리
        self.section = _Label(self)
        self._rows_box.addWidget(self.section)
        self.rows = QGridLayout()
        self._rows_box.addLayout(self.rows)
        root.addLayout(self._rows_box)
        self.sep2 = self._hairline()
        root.addWidget(self.sep2)
        self._footer_box = QVBoxLayout()
        self.footer = [_Label(self, wrap=True) for _ in range(4)]
        for f in self.footer:
            self._footer_box.addWidget(f)
        root.addLayout(self._footer_box)
        self._row_widgets: List[QLabel] = []

    def _hairline(self) -> QFrame:
        f = QFrame(self)
        f.setFixedHeight(1)
        f.setStyleSheet("background: rgba(255,255,255,20);")
        return f

    def apply_scale(self, scale: float):
        self.type = tk.Type(scale)
        t = self.type
        m = t.px(SHADOW)
        self._root.setContentsMargins(m + t.px(18), m + t.px(16), m + t.px(18), m + t.px(16))
        self._root.setSpacing(t.px(12))
        self._lines_box.setSpacing(t.px(3))
        self._rows_box.setSpacing(t.px(6))
        self._footer_box.setSpacing(t.px(3))
        self._top.setSpacing(t.px(12))
        self.rows.setHorizontalSpacing(t.px(10))
        self.rows.setVerticalSpacing(t.px(10))
        self.setFixedWidth(t.px(self.BASE_WIDTH) + 2 * m)
        self.icon.setFixedSize(t.px(48), t.px(48))
        self.headline.style(t.font(t.px(18), W.Bold), "primary")
        self.sub.style(t.font(t.px(13)), "secondary")
        self.section.style(t.font(t.px(11), W.DemiBold), "tertiary", "letter-spacing: 0.4px;")
        for f in self.footer:
            f.style(t.font(t.px(12)), "tertiary")
        if self._view is not None:
            self.render_view(self._view)
        self.adjustSize()

    # ---- 창 속성 ----
    def set_capture_excluded(self, on: bool):
        self._want_excluded = on
        if self.isVisible():
            self.capture_excluded = gw.set_capture_exclusion(int(self.winId()), on)

    def showEvent(self, e):
        super().showEvent(e)
        self.fit_height()                              # 숨겨져 있을 때 그린 내용도 보일 때 높이를 다시 맞춘다
        hwnd = int(self.winId())
        try:
            self.capture_excluded = gw.set_capture_exclusion(hwnd, getattr(self, "_want_excluded", True))
            style = gw.set_click_through(hwnd, not self.edit_mode)
            self.click_through = bool(style & gw.WS_EX_TRANSPARENT)
        except OSError:
            log.exception("HUD 창 속성 적용 실패")

    def set_edit_mode(self, on: bool):
        self.edit_mode = on
        if self.isVisible():
            style = gw.set_click_through(int(self.winId()), not on)
            self.click_through = bool(style & gw.WS_EX_TRANSPARENT)
        self.update()

    def paintEvent(self, e):
        t = self.type
        m = t.px(SHADOW)
        radius = t.px(16)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(m, m, -m, -m)
        # 부드러운 그림자: 바깥으로 갈수록 옅어지는 둥근 사각형 여러 겹
        for i in range(m, 0, -2):
            a = int(34 * (1 - i / m) ** 2)
            sp = QPainterPath()
            sp.addRoundedRect(card.adjusted(-i, -i + t.px(4), i, i + t.px(4)), radius + i, radius + i)
            p.fillPath(sp, QColor(0, 0, 0, a))
        path = QPainterPath()
        path.addRoundedRect(card, radius, radius)
        p.fillPath(path, QColor(30, 30, 32, 236))
        # 위쪽이 아주 조금 밝은 재질감
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(card.left(), card.top(), card.width(), card.height() * 0.5), QColor(255, 255, 255, 6))
        p.restore()
        pen = QPen(QColor(10, 132, 255, 200) if self.edit_mode else QColor(255, 255, 255, 26))
        pen.setWidthF(1.5 if self.edit_mode else 1.0)
        if self.edit_mode:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawPath(path)

    # 위치 조정 모드에서만 끌기
    def mousePressEvent(self, e):
        if self.edit_mode and e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self.edit_mode and self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, e):
        if self._drag_from is not None:
            self._drag_from = None
            self.moved.emit(self.pos())

    # ---- 그리기 ----
    def render_view(self, v: HudView):
        self._view = v
        t = self.type
        if v.icons:
            self.icon.setPixmap(icon_tile(v.icons, t.px(48)))
            self.icon.show()
        else:
            self.icon.hide()
        self.headline.setText(v.title)
        self.sub.setText(v.subtitle)
        self.sub.setVisible(bool(v.subtitle))
        self.status.set(v.status, v.status_tone, t.font(t.px(12), W.DemiBold), t.px(9), t.px(3))

        for lbl, item in zip(self.lines, v.lines[:3] + [None] * (3 - len(v.lines[:3]))):
            if item is None:
                lbl.hide()
                continue
            lbl.style(t.font(t.px(13)), item[1])
            lbl.setText(item[0])
            lbl.show()

        for w in self._row_widgets:
            self.rows.removeWidget(w)
            w.deleteLater()
        self._row_widgets = []
        for r, row in enumerate(v.rows):
            if row.verdict:
                self._add_verdict_row(r, row)
                continue
            ic, name, note = _Label(self), _Label(self), _Label(self)
            ic.setPixmap(icon_tile(row.icons, t.px(26)))
            ic.setFixedSize(t.px(26), t.px(26))
            name.style(t.font(t.px(13)), "primary")
            name.setText(row.text)
            note.style(t.font(t.px(12)), row.note_tone)
            note.setText(row.note)
            self.rows.addWidget(ic, r, 1)
            self.rows.addWidget(name, r, 2)
            self.rows.addWidget(note, r, 3, alignment=Qt.AlignmentFlag.AlignRight)
            self._row_widgets += [ic, name, note]
        for w in self._row_widgets:
            # 이미 떠 있는 창에 새로 넣은 자식은 Qt 가 '다음 이벤트 때' 보이게 한다 — 그 전에 높이를 재면 줄이 빠진
            # 채 고정돼 줄들이 한곳에 눌려 겹친다 (캐릭터 조합 HUD '다른 조합' 이 빈 칸처럼 보이던 것). 바로 보이게 함
            w.show()
        self.rows.setColumnStretch(2, 1)
        has_rows = bool(v.rows)
        self.sep1.setVisible(has_rows or bool(v.section))
        self.section.setText(v.section)
        self.section.setVisible(has_rows and bool(v.section))

        for lbl, item in zip(self.footer, v.footer[:4] + [None] * (4 - len(v.footer[:4]))):
            if item is None:
                lbl.hide()
                continue
            lbl.style(t.font(t.px(12)), item[1])
            lbl.setText(item[0])
            lbl.show()
        self.sep2.setVisible(bool(v.footer))
        self.fit_height()
        QTimer.singleShot(0, self.fit_height)          # 늦게 보이는 자식·글꼴 적용 뒤에 한 번 더 (높이가 모자라면 줄이 눌림)
        self.update()

    def fit_height(self):
        """내용에 맞는 높이로 고정. 줄바꿈 글자가 있으면 adjustSize 가 높이를 넉넉히 잡아 남는 공간이 줄 사이로
        퍼지므로 폭에 맞는 높이(heightForWidth)로 — 고정해 두면 뒤에 부르는 adjustSize 가 되돌리지 않는다."""
        self.setMinimumHeight(0)                       # 지난번 고정 높이 풀기
        self.setMaximumHeight(16777215)
        self.adjustSize()
        self._root.activate()
        if self._root.hasHeightForWidth():
            self.setFixedHeight(self._root.totalHeightForWidth(self.width()))

    def _add_verdict_row(self, r: int, row: HudRow):
        """판정 색 막대 · 아이콘 · 이름과 이유 두 줄 · 판정 알약. 비추천·보류는 이름을 흐리게."""
        t = self.type
        tone = VERDICT_TONE.get(row.verdict, "tertiary")
        cr, cg, cb, _ = TONE[tone]
        bar = QFrame(self)
        bar.setFixedWidth(max(3, t.px(3)))
        bar.setStyleSheet(f"background: rgba({cr},{cg},{cb},230); border-radius: {max(1, t.px(1))}px;")
        ic = _Label(self)
        ic.setPixmap(icon_tile(row.icons, t.px(28)))
        ic.setFixedSize(t.px(28), t.px(28))
        box = QWidget(self)
        box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        box.setStyleSheet("background: transparent;")
        vb = QVBoxLayout(box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)
        name = _Label(box)
        dim = row.verdict in ("skip", "neutral", "unknown")
        name.style(t.font(t.px(13), W.Normal if dim else W.DemiBold), "dim" if dim else "primary")
        name.setText(row.text)
        vb.addWidget(name)
        if row.note:
            why = _Label(box, wrap=True)
            why.style(t.font(t.px(12)), "neutral" if row.verdict in ("best", "alt") else "tertiary")
            why.setText(row.note)
            vb.addWidget(why)
        pill = _Pill(self)
        label = tk.VERDICT.get(row.verdict, (None, ""))[1]
        if row.badge and row.verdict in ("neutral", "skip", "alt", "pick"):
            label = row.badge                            # 순위 + 판정 (예: '2위 비슷함', '3위 비추천', 보류면 '2위')
        pill.set(label, tone, t.font(t.px(12), W.DemiBold), t.px(8), t.px(3))
        self.rows.addWidget(bar, r, 0)
        self.rows.addWidget(ic, r, 1)
        self.rows.addWidget(box, r, 2)
        self.rows.addWidget(pill, r, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._row_widgets += [bar, ic, box, pill]

    # ---- 강화 선택창 ----
    def show_recommendation(self, rec: Recommendation, points_left: Optional[int] = None):
        self._hide_timer.stop()
        d = self.data
        best = rec.best
        tone = {"recommend": "accent", "close": "accent", "none": "warn"}.get(rec.status, "neutral")
        # 확신도: 1위와 2위의 차이·근거 (확실 / 추천 / 근소 / 근거 약함)
        status = rec.confidence or STATUS_TEXT.get(rec.status, "")
        if best is not None:
            v = HudView(title=tr("1위 {v0}", v0=d.name(best.card.item_id)),
                        subtitle=tr("{action_text} · {position} 카드", action_text=best.action_text, position=tr(best.card.position)),
                        status=status, status_tone="ok" if rec.confidence == tr("확실") else tone, icons=(best.card.item_id,))
            v.lines = [(best.effect, "primary")] if best.effect else []
            v.lines += [(r.text, "secondary") for r in best.top_reasons(1 if best.effect else 2)]   # 결론 + 이유 한두 줄
            if best.warnings:
                v.lines.append((best.warnings[0].text, "warn"))
        elif rec.ranked:
            # 판단 보류여도 순서는 보여 준다 (근거가 약하다는 것을 함께)
            fb = rec.fallback or rec.ranked[0]
            v = HudView(title=tr("1위 {v0} (무난한 선택)", v0=d.name(fb.card.item_id)),
                        subtitle=tr("{action_text} · {position} 카드", action_text=fb.action_text, position=tr(fb.card.position)),
                        status=status, status_tone=tone, icons=(fb.card.item_id,))
            v.lines = [(fb.effect, "primary")] if fb.effect else []
            # 왜 그나마 1위인지 (덱 계열·캐릭터 궁합·평가·내 기록 중 가장 큰 근거)와 걸리는 점
            v.lines += [(r.text, "secondary") for r in fb.top_reasons(1)]
            if fb.warnings:
                v.lines.append((fb.warnings[0].text, "warn"))
            v.lines.append((tr("어느 카드도 현재 덱과 뚜렷하게 이어지지 않음 — 새로고침도 고려"), "tertiary"))
        else:
            v = HudView(title=rec.headline, subtitle=rec.limitations[0] if rec.limitations else "",
                        status=STATUS_TEXT.get(rec.status, ""), status_tone=tone)
        top = best if best is not None else (rec.fallback or (rec.ranked[0] if rec.ranked else None))
        if rec.discovery_text:
            v.lines.insert(0, (rec.discovery_text, "primary"))
            if len(v.lines) > 3:
                warnings = [line for line in v.lines[1:] if line[1] == "warn"][:1]
                details = [line for line in v.lines[1:] if line[1] != "warn"]
                v.lines = v.lines[:1] + details[:2 - len(warnings)] + warnings
        # 나머지는 순위 순서로 (게임 화면 위치는 각 줄에 적혀 있다)
        others = sorted((e for e in rec.evals if e is not top),
                        key=lambda e: card_rank(rec, e) or 99)
        if self.compact:
            v.lines = v.lines[:1]
            if rec.banish_text:
                v.footer.append((rec.banish_text, "warn"))
            if rec.reroll_status == "consider":
                v.footer.append((rec.reroll_text, "warn"))
            self.render_view(v)
            return
        v.section = tr("다른 선택지")
        v.rows = [self._row(e, rec) for e in others]
        # 아래 줄은 행동이 필요한 것만, 최대 2줄 (체력·다음 보스 같은 상황 요약은 설정 창에 있다)
        if rec.banish_text:
            v.footer.append((rec.banish_text, "warn"))
        if rec.reroll_status == "consider" and rec.reroll_text:
            v.footer.append((rec.reroll_text, "warn"))
        if rec.plan_text:
            v.footer.append((rec.plan_text, "secondary"))
        if points_left and points_left > 1:
            v.footer.append((tr("강화 포인트 {points_left}개 남음", points_left=points_left), "tertiary"))
        if best is None and rec.limitations:
            v.footer.append((rec.limitations[0], "tertiary"))
        v.footer = v.footer[:2]
        # 고정한 덱 목표는 경고 두 줄에 밀려 안 보이면 안 된다 — 경고 하나를 양보한다
        if rec.plan_text and rec.plan_locked and (rec.plan_text, "secondary") not in v.footer:
            v.footer = v.footer[:1] + [(rec.plan_text, "secondary")]
        self.render_view(v)

    def _row(self, e: ActionEval, rec: Recommendation) -> HudRow:
        verdict = card_verdict(rec, e)
        if not e.evaluated:
            return HudRow((None,), tr("{position} 카드", position=tr(e.card.position)), "", verdict=verdict)
        n = card_rank(rec, e)
        act = tr("레벨 {level_after}", level_after=e.level_after) if e.action.startswith("upgrade") and e.level_after else e.action_text
        # 게임 카드에 이미 순위 배지가 있으니 이름을 앞에, 행동·이유는 아랫줄 한마디로
        text = tr("{n}위  {v0}", n=n, v0=self.data.name(e.card.item_id)) if n else self.data.name(e.card.item_id)
        top = e.top_reasons(1)
        good = (top[0].short or top[0].text) if top else ""
        bad = e.warnings[0].text if e.warnings else ""
        if verdict in ("banish", "skip"):
            why = bad or (tr("{good}, 1위보다 약함", good=good) if good else tr("현재 덱과 연결 없음"))
        else:
            why = " · ".join(x for x in (good, bad) if x)
        if e.effect:
            why = f"{e.effect} · {why}" if why else e.effect
        why = f"{act} · {why}" if why else act
        return HudRow((e.card.item_id,), text, why, verdict=verdict, badge=card_badge(rec, e))

    # ---- 보스 격퇴 후: 원정 계속 / 복귀 ----
    def show_expedition(self, adv):
        self._hide_timer.stop()
        tone = {"continue": "ok", "return": "warn"}.get(adv.verdict, "neutral")
        status = {"continue": tr("계속"), "return": tr("복귀")}.get(adv.verdict, tr("선택"))
        v = HudView(title=adv.headline, subtitle=tr("보스 격퇴 · 원정 계속 또는 복귀"), status=status, status_tone=tone)
        v.lines = [(r, "secondary") for r in adv.reasons[:3]]
        if adv.best_depth:
            v.footer.append((tr("이 지역 무한의 심연 최고 기록 {best_depth}m", best_depth=adv.best_depth), "tertiary"))
        v.footer += [(c, "tertiary") for c in adv.cautions[:2]]
        self.render_view(v)

    # ---- 융합 화면 ----
    def show_fusion(self, rec: FusionRecommendation):
        self._hide_timer.stop()
        best = rec.best
        tone = {"recommend": "accent", "close": "accent", "none": "warn"}.get(rec.status, "neutral")
        if best is not None:
            icons = (best.result_id,) if best.kind == "evo" else tuple(best.parts)
            v = HudView(title=rec.headline, subtitle=best.detail, status=STATUS_TEXT.get(rec.status, ""),
                        status_tone=tone, icons=icons)
            v.lines = [(r.text, "secondary") for r in sorted(best.reasons, key=lambda r: -r.weight)[:2]]
            if best.warnings:
                v.lines.append((best.warnings[0].text, "warn"))
        else:
            v = HudView(title=rec.headline, status=STATUS_TEXT.get(rec.status, ""), status_tone=tone)
        rows = []
        for p in rec.evos + rec.combos + ([rec.free] if rec.free else []):
            if p is best:
                continue
            icons = (p.result_id,) if p.kind == "evo" else tuple(p.parts)
            verb = {"evo": tr("진화"), "combo": tr("융합"), "free": tr("강화")}[p.kind]
            note, tone2 = ((p.warnings[0].short, "warn") if p.warnings else
                           (next((r.short for r in p.reasons if r.rule_id in ("combo_ai", "evo_chain")), ""), "tertiary"))
            rows.append(HudRow(icons, f"{p.title}  {verb}", note, tone2))
        v.section = tr("다른 후보")
        v.rows = rows[:4]
        if rec.notes:
            v.footer = [(" · ".join(rec.notes[:2]), "tertiary")]
        self.render_view(v)

    # ---- 짧은 알림 ----
    def show_message(self, headline: str, body: str = "", tone: tk.RGBA = tk.TEXT_3, hide_after_ms: int = 0,
                     item_id: Optional[str] = None):
        tone_name = "ok" if tone == tk.OK else "warn" if tone == tk.WARN else "neutral"
        v = HudView(title=headline, subtitle=body, icons=(item_id,) if item_id else (),
                    status={"ok": tr("반영됨"), "warn": tr("확인 필요")}.get(tone_name, ""), status_tone=tone_name)
        self.render_view(v)
        if hide_after_ms:
            self._hide_timer.start(hide_after_ms)
        else:
            self._hide_timer.stop()

    @property
    def message_pending(self) -> bool:
        return self._hide_timer.isActive()
