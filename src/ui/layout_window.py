"""배치도 창: 기지를 위에서 본 지도 + 자리 바꾸기 추천(번호 화살표) + 채집 각도 표.

일반 창이다(게임 위 오버레이가 아님). 지도는 게임 월드 좌표를 그대로 축소해 그린다(위가 기지 안쪽).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..engine.layout import TILE_TYPES, Bld, LayoutPlan, Move, buildings_from_base, swap_positions
from ..engine.layout_opt import EFFECTS
from ..gamedata import GameData
from ..tracking.meta_state import RESOURCES
from . import tokens as tk
from .widgets import Group, Segmented, row, section_title, value_label

KIND_COLOR = {1: QColor(214, 178, 64), 2: QColor(76, 150, 84), 3: QColor(140, 140, 150)}


class MapCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(560, 320)
        self.geo: dict = {}
        self.blds: Dict[int, Bld] = {}
        self.swaps: List = []
        self.names: Dict[int, str] = {}
        self.new_spots: List = []

    def set_state(self, geo: dict, blds: Dict[int, Bld], swaps: List, names: Dict[int, str], new_spots=()):
        self.geo, self.blds, self.swaps, self.names, self.new_spots = geo, blds, swaps, names, list(new_spots)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(30, 30, 32))
        g = self.geo
        if not g or not self.blds:
            p.setPen(tk.qcolor(tk.TEXT_2))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "기지 화면에서 게임 연동 1.6 정보를 받으면 지도가 나옵니다")
            return
        L, R, B, T = g["left"], g["right"], g["bottom"], g["top"]
        pad = 16
        sx = (self.width() - 2 * pad) / (R - L)
        sy = (self.height() - 2 * pad) / (T - B)
        s = min(sx, sy)
        ox = pad + ((self.width() - 2 * pad) - s * (R - L)) / 2
        oy = pad + ((self.height() - 2 * pad) - s * (T - B)) / 2

        def pt(x, y):
            return QPointF(ox + (x - L) * s, oy + (T - y) * s)

        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRect(QRectF(pt(L, T), pt(R, B)))
        tile = float(g.get("space_w") or 1.125)
        f = QFont(tk.family())
        f.setPixelSize(10)
        p.setFont(f)
        for b in self.blds.values():
            w, h = b.footprint
            r = QRectF(pt(b.x - w * tile / 2, b.y + h * tile / 2), pt(b.x + w * tile / 2, b.y - h * tile / 2))
            kind = TILE_TYPES.get(b.type)
            if kind:
                c = KIND_COLOR[kind]
                p.fillRect(r.adjusted(1, 1, -1, -1), QColor(c.red(), c.green(), c.blue(), 170))
            else:
                p.fillRect(r.adjusted(1, 1, -1, -1), QColor(70, 70, 78, 220))
                if b.type in EFFECTS:
                    kind2 = EFFECTS[b.type][0]
                    c = KIND_COLOR.get(kind2, QColor(200, 150, 255))
                    p.setPen(QPen(c, 2))
                    p.drawRect(r.adjusted(1, 1, -1, -1))
                    p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 110), 1, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    # 게임의 범위 표시와 같은 사각형 (중심에서 범위만큼)
                    p.drawRect(QRectF(pt(b.x - b.range, b.y + b.range), pt(b.x + b.range, b.y - b.range)))
                name = self.names.get(b.id, "")
                if name and r.width() > 26:
                    p.setPen(QColor(235, 235, 240))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
        # 새 건물 자리: 점선 사각형
        for ns in self.new_spots:
            w, h = ns.size
            r = QRectF(pt(ns.center[0] - w * tile / 2, ns.center[1] + h * tile / 2),
                       pt(ns.center[0] + w * tile / 2, ns.center[1] - h * tile / 2))
            p.setPen(QPen(tk.qcolor(tk.OK), 2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(72, 209, 120, 40))
            p.drawRect(r)
            p.setPen(tk.qcolor(tk.OK))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "새")
        # 자리 바꾸기·옮기기: 번호 화살표
        for n, sw in enumerate(self.swaps[:10], 1):      # 앞 10개만 (전체 순서는 오른쪽 목록)
            a = self.blds.get(sw.a)
            b = self.blds.get(sw.b) if not isinstance(sw, Move) else None
            if not a or (b is None and not isinstance(sw, Move)):
                continue
            pa = pt(a.x, a.y)
            pb = pt(sw.to[0], sw.to[1]) if isinstance(sw, Move) else pt(b.x, b.y)
            p.setPen(QPen(tk.qcolor(tk.WARN), 3))
            p.drawLine(pa, pb)
            for q in (pa, pb):
                p.setBrush(tk.qcolor(tk.WARN))
                p.drawEllipse(q, 9, 9)
                p.setPen(QColor(20, 20, 24))
                p.drawText(QRectF(q.x() - 9, q.y() - 9, 18, 18), Qt.AlignmentFlag.AlignCenter, str(n))
                p.setPen(QPen(tk.qcolor(tk.WARN), 3))


class LayoutWindow(QWidget):
    recalc_requested = Signal()
    preset_chosen = Signal(str)          # effect | gold_u | plan

    def __init__(self, data: GameData):
        super().__init__(None)
        self.data = data
        self.setWindowTitle("BALL x PIT 배치도")
        self.resize(1020, 620)
        self.setStyleSheet(f"background: {tk.css(tk.BG_SOLID)}; color: {tk.css(tk.TEXT)};")
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.preset_view = Segmented(["효과 최대", "금광 U자 (공략)", "계획도시"], 0)
        self.preset_view.changed.connect(self._on_preset)
        self.preset_view.setVisible(False)
        left.addWidget(self.preset_view)
        self.view = Segmented(["지금 배치", "최적 배치"], 0)
        self.view.changed.connect(lambda _i: self._redraw())
        left.addWidget(self.view)
        self.canvas = MapCanvas()
        left.addWidget(self.canvas, 1)
        cap = QLabel("색: 밀밭 노랑 · 숲 초록 · 바위 회색. 테두리 건물은 범위 효과 건물(점선 원 = 범위). "
                     "최적 배치 = 기지를 다 치우고 다시 놓는다고 보고 건물마다 효과가 최대인 자리 (범위 효과 + 채집 발사량). "
                     "주황 번호 = 옮기는 순서 (앞 10개) — 게임에서 재배치 모드에 들어가면 다음 옮기기가 게임 화면에 표시됩니다.")
        cap.setWordWrap(True)
        cap.setStyleSheet(f"color: {tk.css(tk.TEXT_2)};")
        left.addWidget(cap)
        root.addLayout(left, 3)
        panel = QWidget()
        right = QVBoxLayout(panel)
        right.addWidget(section_title("최적 배치로 옮기기"))
        self.steps = Group()
        right.addWidget(self.steps)
        right.addWidget(section_title("새로 지을 건물 · 강화 추천 (배치 효과 기준)"))
        self.builds = Group()
        right.addWidget(self.builds)
        right.addWidget(section_title("효과"))
        self.effects = Group()
        right.addWidget(self.effects)
        self.effects_note = QLabel("")
        self.effects_note.setWordWrap(True)
        self.effects_note.setStyleSheet(f"color: {tk.css(tk.TEXT_2)};")
        right.addWidget(self.effects_note)
        right.addWidget(section_title("채집 발사 조준 (궤적 계산)"))
        self.angles = Group()
        right.addWidget(self.angles)
        btn = QPushButton("다시 계산")
        btn.clicked.connect(self.recalc_requested.emit)
        right.addWidget(btn)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        root.addWidget(scroll, 2)
        self.base: Optional[dict] = None
        self.plan_all: Optional[LayoutPlan] = None
        self.plan: Optional[LayoutPlan] = None
        self.sweeps: Dict[int, list] = {}

    def _on_preset(self, i: int):
        name = {1: "gold_u", 2: "plan"}.get(i, "effect")
        self.preset_chosen.emit(name)
        if self.base is not None:
            self.set_result(self.base, self.plan_all, self.sweeps, name)

    def set_result(self, base: dict, plan: Optional[LayoutPlan], sweeps: Dict[int, list], preset: str = "effect"):
        """plan: 기본(효과 최대) 배치. 다른 프리셋은 plan.alternatives 에 있다."""
        self.plan_all = plan
        has_alt = bool(plan is not None and getattr(plan, "alternatives", None))
        self.preset_view.setVisible(has_alt)
        if has_alt and preset in plan.alternatives:
            plan = plan.alternatives[preset]
        self.base, self.plan, self.sweeps = base, plan, sweeps
        d = self.data
        blds = buildings_from_base(base)
        self.steps.clear()
        if plan:
            pct = (plan.score_after / plan.score_before - 1) * 100 if plan.score_before else 0.0
            self.steps.add(row(f"옮기기 {len(plan.swaps)}번", value_label(f"범위 효과 {pct:+.0f}%"),
                               "순서대로 옮기면 최적 배치가 됩니다 (잠시 비켜 두기 포함)" if plan.swaps else ""))
            for note in getattr(plan, "notes", []) or []:
                self.steps.add(row(note))
        if plan and plan.swaps:
            for n, sw in enumerate(plan.swaps, 1):
                a = blds.get(sw.a)
                if a is None:
                    continue
                if isinstance(sw, Move) and sw.target < 0 and sw.gain == 0:
                    park = "비켜" in sw.reason
                    self.steps.add(row(f"{n}. {d.building_name(a.type)} → {'빈 곳에 잠시' if park else '최적 자리'}",
                                       value_label("비켜 두기" if park else "옮기기"),
                                       "다른 건물이 들어갈 자리를 비우려고 잠시 옮김 — 나중에 최적 자리로 다시 옮김" if park else ""))
                elif isinstance(sw, Move) and sw.target >= 0:
                    t = blds.get(sw.target)
                    tname = d.building_name(t.type) if t else "미완성 건물"
                    self.steps.add(row(f"{n}. {d.building_name(a.type)} → 빈 자리", value_label("길 열기"),
                                       f"미완성 {tname}에 작업자가 닿게 됨 (예상 {sw.gain:.0f}번) — 먼저 완성"))
                elif isinstance(sw, Move):
                    self.steps.add(row(f"{n}. {d.building_name(a.type)} → 빈 자리", value_label(f"+{sw.gain:.1f}"),
                                       sw.reason))
                elif blds.get(sw.b):
                    self.steps.add(row(f"{n}. {d.building_name(a.type)} ↔ {d.building_name(blds[sw.b].type)}",
                                       value_label(f"+{sw.gain:.1f}"), sw.reason))
        elif plan is None:
            self.steps.add(row("계산 중이거나 기지 정보 없음"))
        self.builds.clear()
        for t, _c, _sz, gain, n, *rest in (getattr(plan, "builds", None) or []) if plan else []:
            moved = rest[0] if rest else 0
            from ..engine.layout_opt import TILE_RES, UNLIMITED_COST
            cost = UNLIMITED_COST.get(t)
            cost_txt = (" · 비용 " + " ".join(f"{RESOURCES[i]} {v}" for i, v in enumerate(cost) if v)) if cost else ""
            if t in TILE_RES:
                self.builds.add(row(f"더 사기: {d.building_name(t)} {n}개", value_label(f"범위 효과 +{gain:.1f}"),
                                    f"생산 건물 범위의 빈칸 채우기 — 첫 자리 초록 점선{cost_txt}"))
                continue
            if t == "kGoldMine":
                self.builds.add(row(f"짓기: {d.building_name(t)}", value_label("튕김 가장 많은 자리"),
                                    "초록 점선 자리 — 작업자가 가장 많이 부딪히는 빈 자리 (궤적 계산, 게임은 채집당 "
                                    "캐는 횟수에 상한이 있어 튕김 수는 비교용)"))
            else:
                self.builds.add(row(f"짓기: {d.building_name(t)}", value_label(f"범위 효과 +{gain:.1f}"),
                                    f"초록 점선 자리 · 범위 안 {n}개" + (f" · 주변 {moved}개도 옮기면 이 값" if moved else "")
                                    + " (지은 뒤 강화·일꾼 배정 기준)" + cost_txt))
        for _i, t, what, gain in (getattr(plan, "activations", None) or []) if plan else []:
            self.builds.add(row(f"{what}: {d.building_name(t)}", value_label(f"범위 효과 +{gain:.1f}"),
                                "지금은 효과를 절반으로 계산 중 — " + ("일꾼을 배정하면" if what == "일꾼 배정" else "강화하면") + " 전부 켜짐"))
        if plan is not None and not (getattr(plan, "builds", None) or getattr(plan, "activations", None)):
            self.builds.add(row("추천할 새 건물·강화 없음", None, "지을 수 있는 설계도 중 범위 효과 건물이 없음"))
        cal = getattr(plan, "calibration", (0.0, 0, 0)) if plan else (0.0, 0, 0)
        if cal[2]:
            self.effects_note.setText(f"범위 판정 검증: 게임이 센 '범위 안 자원 타일 수'와 {cal[1]}/{cal[2]}개 건물 일치 "
                                      f"(범위 여유 {cal[0]:.2f})")
        else:
            self.effects_note.setText("범위 판정: 건물 중심 사이 거리 ≤ 게임 범위 값 (추정) — 플러그인 1.9부터 게임 값과 비교해 맞춥니다")
        self.effects.clear()
        if plan:
            self.effects.add(row("범위 효과 점수", value_label(f"{plan.score_before:.1f} → {plan.score_after:.1f}")))
            for t in sorted(set(plan.detail_before) | set(plan.detail_after)):
                self.effects.add(row(d.building_name(t), value_label(
                    f"{plan.detail_before.get(t, 0):.0f} → {plan.detail_after.get(t, 0):.0f}"), EFFECTS.get(t, (0, 0, "", "", ""))[4]))
            if plan.harvest_before and plan.harvest_after:
                txt = " · ".join(f"{RESOURCES[i]} {plan.harvest_before[i]}→{plan.harvest_after[i]}"
                                 for i in range(1, 4))
                self.effects.add(row("채집 발사 예상 (추천 각도)", None, txt))
        self.angles.clear()
        for res, lst in sorted(self.sweeps.items()):
            if lst:
                a, tot = lst[0]
                self.angles.add(row(f"{RESOURCES[res]} 위주", value_label(f"{a:.0f}°"),
                                    " · ".join(f"{RESOURCES[i]} +{v}" for i, v in enumerate(tot) if v)))
        if not self.sweeps:
            self.angles.add(row("계산 중이거나 정보 없음"))
        self._redraw()

    def _redraw(self):
        if not self.base:
            self.canvas.set_state({}, {}, [], {})
            return
        blds = buildings_from_base(self.base)
        swaps = self.plan.swaps if self.plan else []
        if self.view.group.checkedId() == 1 and swaps:
            for sw in swaps:
                if isinstance(sw, Move) and sw.a in blds:
                    b = blds[sw.a]
                    blds = dict(blds)
                    blds[sw.a] = Bld(b.id, b.type, sw.to[0], sw.to[1], b.tw, b.th, b.rot, b.range)
                elif sw.a in blds and sw.b in blds:
                    blds = swap_positions(blds, sw.a, sw.b)
        names = {i: self.data.building_name(b.type)[:4] for i, b in blds.items()
                 if b.type in EFFECTS or b.type not in TILE_TYPES}
        self.canvas.set_state(self.base.get("geo") or {}, blds, swaps if self.view.group.checkedId() != 1 else [], names,
                              self.plan.new_spots if self.plan else [])
