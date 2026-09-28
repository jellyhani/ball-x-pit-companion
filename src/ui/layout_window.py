"""배치도 창: 기지를 위에서 본 지도 + 자리 바꾸기 추천(번호 화살표) + 채집 각도 표.

일반 창이다(게임 위 오버레이가 아님). 지도는 게임 월드 좌표를 그대로 축소해 그린다(위가 기지 안쪽).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..engine.layout import (
    TILE_TYPES,
    Bld,
    LayoutPlan,
    Move,
    buildings_from_base,
    grid_from_geo,
    shape_masks,
    shape_outline_world,
    swap_positions,
)
from ..engine.layout_opt import EFFECTS
from ..gamedata import GameData
from ..i18n import tr
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
        self.shapes: Dict[int, list] = {}  # 건물 모양 윤곽 (월드 좌표) — ㄱ·ㅜ·ㅠ 자 건물을 모양 그대로 그림

    def set_state(
        self,
        geo: dict,
        buildings: Dict[int, Bld],
        swaps: List,
        names: Dict[int, str],
        new_spots=(),
        shapes=None,
    ):
        self.geo, self.blds, self.swaps, self.names, self.new_spots = (
            geo,
            buildings,
            swaps,
            names,
            list(new_spots),
        )
        self.shapes = dict(shapes or {})
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 30, 32))
        g = self.geo
        bounds = [g.get(k) for k in ("left", "right", "bottom", "top")]
        valid = (
            all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
            and bounds[0] < bounds[1]
            and bounds[2] < bounds[3]
        )
        if not valid or not self.blds:
            painter.setPen(tk.qcolor(tk.TEXT_2))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                tr("기지 화면에서 게임 연동 1.6 정보를 받으면 지도가 나옵니다"),
            )
            return
        L, R, B, T = g["left"], g["right"], g["bottom"], g["top"]
        pad = 16
        sx = (self.width() - 2 * pad) / (R - L)
        sy = (self.height() - 2 * pad) / (T - B)
        s = min(sx, sy)
        origin_x = pad + ((self.width() - 2 * pad) - s * (R - L)) / 2
        origin_y = pad + ((self.height() - 2 * pad) - s * (T - B)) / 2

        def pt(x, y):
            return QPointF(origin_x + (x - L) * s, origin_y + (T - y) * s)

        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawRect(QRectF(pt(L, T), pt(R, B)))
        tile = float(g.get("space_w") or 1.125)
        font = tk.base_font()
        font.setPixelSize(10)
        painter.setFont(font)
        for b in self.blds.values():
            w, h = b.footprint
            rectangle = QRectF(
                pt(b.x - w * tile / 2, b.y + h * tile / 2), pt(b.x + w * tile / 2, b.y - h * tile / 2)
            )
            kind = TILE_TYPES.get(b.type)
            if kind:
                c = KIND_COLOR[kind]
                painter.fillRect(rectangle.adjusted(1, 1, -1, -1), QColor(c.red(), c.green(), c.blue(), 170))
            else:
                outline = self.shapes.get(b.id)
                body = QPainterPath()
                if outline and len(outline) >= 3:
                    body.moveTo(pt(*outline[0]))
                    for x, y in outline[1:]:
                        body.lineTo(pt(x, y))
                    body.closeSubpath()
                else:
                    body.addRect(rectangle.adjusted(1, 1, -1, -1))
                painter.fillPath(body, QColor(70, 70, 78, 220))
                painter.setPen(QPen(QColor(30, 30, 32), 1.5))  # 옆 건물과 경계가 보이게
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(body)
                if b.type in EFFECTS:
                    kind2 = EFFECTS[b.type][0]
                    c = KIND_COLOR.get(kind2, QColor(200, 150, 255))
                    painter.setPen(QPen(c, 2))
                    painter.drawPath(body)
                    painter.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 110), 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    # 게임의 범위 표시와 같은 사각형 (중심에서 범위만큼)
                    painter.drawRect(
                        QRectF(pt(b.x - b.range, b.y + b.range), pt(b.x + b.range, b.y - b.range))
                    )
                name = self.names.get(b.id, "")
                if name and rectangle.width() > 26:
                    painter.setPen(QColor(235, 235, 240))
                    painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
        # 새 건물 자리: 점선 사각형
        for ns in self.new_spots:
            w, h = ns.size
            rectangle = QRectF(
                pt(ns.center[0] - w * tile / 2, ns.center[1] + h * tile / 2),
                pt(ns.center[0] + w * tile / 2, ns.center[1] - h * tile / 2),
            )
            painter.setPen(QPen(tk.qcolor(tk.OK), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(72, 209, 120, 40))
            painter.drawRect(rectangle)
            painter.setPen(tk.qcolor(tk.OK))
            painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, tr("새"))
        # 자리 바꾸기·옮기기: 번호 화살표
        for n, sw in enumerate(self.swaps[:10], 1):  # 앞 10개만 (전체 순서는 오른쪽 목록)
            a = self.blds.get(sw.a)
            b = self.blds.get(sw.b) if not isinstance(sw, Move) else None
            if not a or (b is None and not isinstance(sw, Move)):
                continue
            pa = pt(a.x, a.y)
            pb = pt(sw.to[0], sw.to[1]) if isinstance(sw, Move) else pt(b.x, b.y)
            painter.setPen(QPen(tk.qcolor(tk.WARN), 3))
            painter.drawLine(pa, pb)
            for q in (pa, pb):
                painter.setBrush(tk.qcolor(tk.WARN))
                painter.drawEllipse(q, 9, 9)
                painter.setPen(QColor(20, 20, 24))
                painter.drawText(QRectF(q.x() - 9, q.y() - 9, 18, 18), Qt.AlignmentFlag.AlignCenter, str(n))
                painter.setPen(QPen(tk.qcolor(tk.WARN), 3))


class LayoutWindow(QWidget):
    recalc_requested = Signal()

    def __init__(self, data: GameData):
        super().__init__(None)
        self.data = data
        self.setWindowTitle(tr("BALL x PIT 배치도"))
        self.resize(1020, 620)
        self.setStyleSheet(f"background: {tk.css(tk.BG_SOLID)}; color: {tk.css(tk.TEXT)};")
        horizontal_layout = QHBoxLayout(self)
        vertical_layout = QVBoxLayout()
        self.view = Segmented([tr("지금 배치"), tr("가이드 배치")], 0)
        self.view.setStyleSheet("""
            QFrame#segmented { background: #29292e; border: 1px solid #55555d; border-radius: 7px; }
            QPushButton#segment { background: transparent; color: #b9b9c2; border: 0; padding: 5px 12px; }
            QPushButton#segment:checked { background: #285b91; color: white; font-weight: bold; border-radius: 5px; }
        """)
        self.view.changed.connect(lambda _i: self._redraw())
        vertical_layout.addWidget(self.view)
        self.view_label = QLabel(tr("지금 배치"))
        self.view_label.setStyleSheet(f"color: {tk.css(tk.TEXT_2)}; font-weight: bold;")
        vertical_layout.addWidget(self.view_label)
        label = QLabel(
            tr("Steam 공략의 일부 규칙을 참고한 적은 이동안입니다. 최적 배치를 보장하지 않습니다.")
        )
        label.setWordWrap(True)
        label.setStyleSheet("color: #e6b461;")
        vertical_layout.addWidget(label)
        self.canvas = MapCanvas()
        vertical_layout.addWidget(self.canvas, 1)
        current_label = QLabel(
            tr(
                "색: 밀밭 노랑 · 숲 초록 · 바위 회색. 점선 테두리는 범위 효과입니다. 가이드 배치는 기존 핵심 효과·생산 구역·입구를 보존하는 이동 후보입니다. 오른쪽에서 충족하지 못한 조건을 확인하세요. 재배치 모드에서는 다음 이동 위치를 게임 화면에 표시합니다."
            )
        )
        current_label.setWordWrap(True)
        current_label.setStyleSheet(f"color: {tk.css(tk.TEXT_2)};")
        vertical_layout.addWidget(current_label)
        horizontal_layout.addLayout(vertical_layout, 3)
        widget = QWidget()
        current_vertical_layout = QVBoxLayout(widget)
        current_vertical_layout.addWidget(section_title(tr("가이드 배치로 옮기기")))
        self.steps = Group()
        current_vertical_layout.addWidget(self.steps)
        current_vertical_layout.addWidget(section_title(tr("새로 지을 건물 · 강화 추천 (배치 효과 기준)")))
        self.builds = Group()
        current_vertical_layout.addWidget(self.builds)
        current_vertical_layout.addWidget(section_title(tr("철거 후보 (배치 효과 기준)")))
        self.demolish = Group()
        current_vertical_layout.addWidget(self.demolish)
        current_vertical_layout.addWidget(section_title(tr("효과")))
        self.effects = Group()
        current_vertical_layout.addWidget(self.effects)
        self.effects_note = QLabel("")
        self.effects_note.setWordWrap(True)
        self.effects_note.setStyleSheet(f"color: {tk.css(tk.TEXT_2)};")
        current_vertical_layout.addWidget(self.effects_note)
        current_vertical_layout.addWidget(section_title(tr("채집 발사 조준 (궤적 계산)")))
        self.angles = Group()
        current_vertical_layout.addWidget(self.angles)
        button = QPushButton(tr("다시 계산"))
        button.clicked.connect(self.recalc_requested.emit)
        current_vertical_layout.addWidget(button)
        current_vertical_layout.addStretch(1)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(widget)
        horizontal_layout.addWidget(scroll_area, 2)
        self.base: Optional[dict] = None
        self.plan: Optional[LayoutPlan] = None
        self.sweeps: Dict[int, list] = {}

    def set_result(self, base: dict, plan: Optional[LayoutPlan], sweeps: Dict[int, list]):
        """plan: 현재 배치에서 출발한 가이드 이동안."""
        self.base, self.plan, self.sweeps = base, plan, sweeps
        game_data = self.data
        buildings = buildings_from_base(base)
        self.steps.clear()
        if plan:
            percentage = (plan.score_after / plan.score_before - 1) * 100 if plan.score_before else 0.0
            if getattr(plan, "calculation_deferred", False):
                self.steps.add(row(tr("현재 게임 정보가 부족해 배치 추천을 보류합니다.")))
            elif not getattr(plan, "movement_complete", True):
                self.steps.add(row(tr("안전한 이동 순서를 만들지 못했습니다. 배치도를 다시 계산해 주세요.")))
            else:
                self.steps.add(
                    row(
                        tr("옮기기 {v0}번", v0=len(plan.swaps)),
                        value_label(tr("범위 효과 {pct:+.0f}%", pct=percentage)),
                        tr("순서대로 옮기면 가이드 배치가 됩니다 (잠시 비켜 두기 포함)")
                        if plan.swaps
                        else "",
                    )
                )
            for note in getattr(plan, "notes", []) or []:
                self.steps.add(row(note))
            if getattr(plan, "model_limitations", None):
                self.steps.add(
                    row(tr("게임 강화 값을 반영한 예상입니다. 실제 채집 경로·수확량은 다를 수 있습니다."))
                )
        if plan and plan.swaps:
            for n, sw in enumerate(plan.swaps, 1):
                a = buildings.get(sw.a)
                if a is None:
                    continue
                if isinstance(sw, Move) and sw.target < 0 and sw.gain == 0:
                    park = sw.reason in (
                        tr("잠시 비켜 두기 (자리 비우기)"),
                        tr("잠시 비켜 두기 (다른 건물 자리 비우기)"),
                    )
                    self.steps.add(
                        row(
                            f"{n}. {game_data.building_name(a.type)} → {tr('빈 곳에 잠시') if park else tr('목표 자리')}",
                            value_label(tr("비켜 두기") if park else tr("옮기기")),
                            tr("다른 건물이 들어갈 자리를 비우려고 잠시 옮김 — 나중에 목표 자리로 다시 옮김")
                            if park
                            else sw.reason,
                        )
                    )
                elif isinstance(sw, Move) and sw.target >= 0:
                    t = buildings.get(sw.target)
                    tname = game_data.building_name(t.type) if t else tr("미완성 건물")
                    self.steps.add(
                        row(
                            tr("{n}. {v0} → 빈 자리", n=n, v0=game_data.building_name(a.type)),
                            value_label(tr("길 열기")),
                            tr(
                                "미완성 {tname}에 작업자가 닿게 됨 (예상 {gain:.0f}번) — 먼저 완성",
                                tname=tname,
                                gain=sw.gain,
                            ),
                        )
                    )
                elif isinstance(sw, Move):
                    self.steps.add(
                        row(
                            tr("{n}. {v0} → 빈 자리", n=n, v0=game_data.building_name(a.type)),
                            value_label(f"+{sw.gain:.1f}"),
                            sw.reason,
                        )
                    )
                elif buildings.get(sw.b):
                    self.steps.add(
                        row(
                            f"{n}. {game_data.building_name(a.type)} ↔ {game_data.building_name(buildings[sw.b].type)}",
                            value_label(f"+{sw.gain:.1f}"),
                            sw.reason,
                        )
                    )
        elif plan is None:
            self.steps.add(row(tr("계산 중이거나 기지 정보 없음")))
        self.builds.clear()
        if (
            plan
            and getattr(plan, "construction_pending", False)
            and not getattr(plan, "calculation_deferred", False)
        ):
            self.builds.add(row(tr("재배치 완료 후 건설 위치를 다시 계산합니다.")))
        for t, _c, _sz, gain, n, *rest in (getattr(plan, "builds", None) or []) if plan else []:
            moved = rest[0] if rest else 0
            from ..engine.layout_opt import TILE_RES

            cost = (getattr(plan, "build_costs", None) or {}).get(t)
            cost_txt = (
                (
                    tr(" · 1개 비용 ")
                    + " ".join(f"{RESOURCES[index]} {value}" for index, value in enumerate(cost) if value)
                )
                if cost
                else ""
            )
            if t in TILE_RES:
                self.builds.add(
                    row(
                        tr("더 사기: {v0} {n}개", v0=game_data.building_name(t), n=n),
                        None,
                        tr("생산 건물 범위의 빈칸 채우기 — 첫 자리 초록 점선{cost_txt}", cost_txt=cost_txt),
                    )
                )
                continue
            if t == "kGoldMine":
                self.builds.add(
                    row(
                        tr("짓기: {v0}", v0=game_data.building_name(t)),
                        value_label(tr("튕김 가장 많은 자리")),
                        tr(
                            "초록 점선 자리 — 작업자가 가장 많이 부딪히는 빈 자리 (궤적 계산, 게임은 채집당 캐는 횟수에 상한이 있어 튕김 수는 비교용)"
                        ),
                    )
                )
            else:
                self.builds.add(
                    row(
                        tr("짓기: {v0}", v0=game_data.building_name(t)),
                        value_label(tr("범위 효과 +{gain:.1f}", gain=gain)),
                        tr("초록 점선 자리 · 범위 안 {n}개", n=n)
                        + (tr(" · 주변 {moved}개도 옮기면 이 값", moved=moved) if moved else "")
                        + tr(" (지은 뒤 강화·일꾼 배정 기준)")
                        + cost_txt,
                    )
                )
        for _i, t, what, gain in (getattr(plan, "activations", None) or []) if plan else []:
            label = (tr("공략 후순위") + " · ") if t == "kMansion" else ""
            self.builds.add(
                row(
                    f"{label}{what}: {game_data.building_name(t)}",
                    value_label(tr("범위 효과 +{gain:.1f}", gain=gain)),
                    tr("지금은 효과를 절반으로 계산 중 — ")
                    + (tr("일꾼을 배정하면") if what == tr("일꾼 배정") else tr("강화하면"))
                    + tr(" 전부 켜짐"),
                )
            )
        if (
            plan is not None
            and not getattr(plan, "construction_pending", False)
            and not (getattr(plan, "builds", None) or getattr(plan, "activations", None))
        ):
            self.builds.add(row(tr("추천할 새 건물·강화 없음")))
        self.demolish.clear()
        dem = (getattr(plan, "demolish", None) or []) if plan else []
        for _i, t, score, why in dem:
            self.demolish.add(
                row(
                    tr("철거 후보: {v0}", v0=game_data.building_name(t)),
                    value_label(tr("기여 {score:.1f}", score=score)),
                    tr("{why} (환불량은 게임에서 확인)", why=why),
                )
            )
        if plan is not None and not dem:
            self.demolish.add(row(tr("철거 후보 없음")))
        cal = getattr(plan, "calibration", (0.0, 0, 0)) if plan else (0.0, 0, 0)
        contract = base.get("range_contract") or {}
        if contract.get("checked"):
            self.effects_note.setText(
                tr(
                    "게임의 대상별 범위 판정 대조: {checked}건 · 불일치 {mismatches}건",
                    checked=contract["checked"],
                    mismatches=len(contract.get("mismatches", [])),
                )
            )
        elif cal[2]:
            self.effects_note.setText(
                tr(
                    "범위 판정 검증: 게임이 센 '범위 안 자원 타일 수'와 {v0}/{v1}개 건물 일치 (범위 여유 {v2:.2f})",
                    v0=cal[1],
                    v1=cal[2],
                    v2=cal[0],
                )
            )
        else:
            self.effects_note.setText(
                tr(
                    "범위 판정: 건물 중심 사이 거리 ≤ 게임 범위 값 (추정) — 플러그인 1.9부터 게임 값과 비교해 맞춥니다"
                )
            )
        self.effects.clear()
        if plan:
            self.effects.add(
                row(tr("범위 효과 점수"), value_label(f"{plan.score_before:.1f} → {plan.score_after:.1f}"))
            )
            for t in sorted(set(plan.detail_before) | set(plan.detail_after)):
                self.effects.add(
                    row(
                        game_data.building_name(t),
                        value_label(
                            f"{plan.detail_before.get(t, 0):.0f} → {plan.detail_after.get(t, 0):.0f}"
                        ),
                        EFFECTS.get(t, (0, 0, "", "", ""))[4],
                    )
                )
            if plan.harvest_before and plan.harvest_after:
                text = " · ".join(
                    f"{RESOURCES[index]} {plan.harvest_before[index]}→{plan.harvest_after[index]}"
                    for index in range(1, 4)
                )
                self.effects.add(row(tr("채집 발사 예상 (추천 각도)"), None, text))
        self.angles.clear()
        for res, lst in sorted(self.sweeps.items()):
            if lst:
                a, tot = lst[0]
                self.angles.add(
                    row(
                        tr("{v0} 위주", v0=RESOURCES[res]),
                        value_label(f"{a:.0f}°"),
                        " · ".join(
                            f"{RESOURCES[index]} +{value}" for index, value in enumerate(tot) if value
                        ),
                    )
                )
        if not self.sweeps:
            self.angles.add(row(tr("계산 중이거나 정보 없음")))
        self._redraw()

    def _redraw(self):
        self.view_label.setText(tr("가이드 배치") if self.view.group.checkedId() == 1 else tr("지금 배치"))
        if not self.base:
            self.canvas.set_state({}, {}, [], {})
            return
        buildings = buildings_from_base(self.base)
        swaps = self.plan.swaps if self.plan else []
        if self.view.group.checkedId() == 1 and swaps:
            for sw in swaps:
                if isinstance(sw, Move) and sw.a in buildings:
                    b = buildings[sw.a]
                    buildings = dict(buildings)
                    rot = sw.rot if getattr(sw, "rot", -1) >= 0 else b.rot  # 회전해서 놓는 건물
                    buildings[sw.a] = Bld(b.id, b.type, sw.to[0], sw.to[1], b.tw, b.th, rot, b.range)
                elif sw.a in buildings and sw.b in buildings:
                    buildings = swap_positions(buildings, sw.a, sw.b)
        names = {
            index: self.data.building_name(b.type)[:4]
            for index, b in buildings.items()
            if b.type in EFFECTS or b.type not in TILE_TYPES
        }
        # 건물 모양: 지금 기지의 충돌 모양을 목표 자리·회전으로 옮겨 그린다 (사각형이 아니라 ㄱ·ㅜ·ㅠ 모양 그대로)
        geo = self.base.get("geo") or {}
        grid = grid_from_geo(geo)
        shapes = {}
        if grid is not None:
            orig = buildings_from_base(self.base)
            masks = shape_masks(geo, orig, grid)
            for index, b in buildings.items():
                o = orig.get(index)
                if o is not None and b.type not in TILE_TYPES:
                    shapes[index] = shape_outline_world(
                        o, masks.get(index), grid.size, (b.x, b.y), b.rot if b.rot != o.rot else -1
                    )
        self.canvas.set_state(
            geo,
            buildings,
            swaps if self.view.group.checkedId() != 1 else [],
            names,
            self.plan.new_spots if self.plan else [],
            shapes,
        )
