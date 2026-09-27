"""사용자가 직접 여는 설정·상태 창(F10). macOS 시스템 설정처럼 왼쪽 목록 + 오른쪽 둥근 그룹 목록.

페이지: 현재 런 / 진화 / 백과사전 / 진단 / 설정. 플레이 중 추천은 HUD가 맡고, 이 창은 자동으로 뜨지 않는다.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout,
                             QWidget)

from ..engine.base_advisor import CAT_LABEL, suggest as suggest_base
from ..engine.planning import blueprint_targets, plan_levels
from ..engine.harvest import advise_workers
from ..engine.roadmap import browsable_targets, build_roadmap, fusion_pairs
from ..gamedata import DATA_DIR, GameData
from ..i18n import tr
from ..services.settings import APP_DIR, Settings
from ..tracking.meta_state import RESOURCES, MetaState
from ..tracking.run_history import RunRecord, describe as describe_run, item_summary
from ..tracking.run_state import SOURCE_LABEL, RunState
from . import tokens as tk
from .hud import icon_tile
from .widgets import Group, Segmented, Toggle, chip, row, section_title, value_label

KIND_LABEL = {"ball": tr("볼"), "passive": tr("패시브"), "pet": tr("펫"), "baby": tr("베이비볼")}
# 내부 식별용 키 (번역 안 함 — 화면에 보이는 이름은 PAGES). "백과사전"은 게임 자체 화면 이름 그대로 씀
# (게임 파일에서 확인: Encyclopedia/百科事典/百科全书/百科全書 — 우리 말로 지어낸 "도감" 대신 이 이름을 쓰면
# 다른 언어 번역도 게임 것을 그대로 재사용할 수 있다).
PAGE_KEYS = ["현재 런", "진화", "기록", "기지", "백과사전", "진단", "설정"]
PAGES = [tr(k) for k in PAGE_KEYS]
STAT_LABEL = {
    "crit_chance": tr("치명타 확률"), "crit_mult": tr("치명타 배율"), "fire_rate": tr("발사 속도"), "reload": tr("재장전 시간"),
    "ball_speed": tr("볼 속도"), "move_speed": tr("이동 속도"), "damage_reduction": tr("피해 감소"), "dodge": tr("회피"),
    "thorns": tr("가시 피해"), "health_per_kill": tr("처치당 회복"), "pickup_range": tr("획득 범위"), "bonus_xp": tr("추가 경험치"),
    "bonus_gold": tr("추가 골드"), "ball_damage_mult": tr("볼 피해 배율"), "bonus_ball_damage": tr("추가 볼 피해"),
    "babies": tr("베이비볼"), "multi_balls": tr("동시 발사 볼"),
}
CHAR_STAT_LABEL = (tr("인내"), tr("힘"), tr("지도력"), tr("속도"), tr("민첩"), tr("지능"))   # 게임 StatType 순서 (게임 화면 이름)
DMG_LABEL = {"bounce": tr("타격"), "status": tr("상태 이상"), "aoe": tr("범위"), "other": tr("기타")}


def _num(v) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.3g}"
    return f"{int(v):,}"


CHAR_SOURCE = {"portrait": tr("초상화로 확인"), "game": tr("게임 연동"), "manual": tr("직접 지정")}

BG = "#1e1e20"          # 창 배경
SIDEBAR = "#262628"
GROUP = "#2c2c2e"       # 둥근 그룹
HAIR = "rgba(255,255,255,18)"
TEXT = "#f5f5f7"
TEXT2 = "rgba(235,235,245,153)"
TEXT3 = "rgba(235,235,245,92)"
BLUE = "#0a84ff"


def _stylesheet(t: tk.Type) -> str:
    chip = (f"border-radius: {t.px(9)}px; padding: {t.px(2)}px {t.px(9)}px; font-size: {t.px(12)}px; "
            f"font-weight: 600;")
    return f"""
    QWidget {{ background: {BG}; color: {TEXT}; font-family: {tk.css_families()}; font-size: {t.px(13)}px; }}
    QListWidget#sidebar {{ background: {SIDEBAR}; border: none; padding: {t.px(12)}px {t.px(8)}px; outline: 0; }}
    QListWidget#sidebar::item {{ padding: {t.px(7)}px {t.px(10)}px; border-radius: {t.px(7)}px; color: {TEXT};
                                 margin-bottom: 2px; }}
    QListWidget#sidebar::item:selected {{ background: {BLUE}; color: white; }}
    QListWidget#sidebar::item:hover:!selected {{ background: rgba(255,255,255,14); }}
    QLabel#pageTitle {{ font-size: {t.px(22)}px; font-weight: 700; background: transparent;
                        padding-bottom: {t.px(4)}px; }}
    QLabel#sectionTitle {{ color: {TEXT3}; font-size: {t.px(12)}px; font-weight: 600; background: transparent;
                           padding: {t.px(16)}px {t.px(14)}px {t.px(6)}px {t.px(14)}px; }}
    QLabel#caption {{ color: {TEXT3}; font-size: {t.px(12)}px; background: transparent;
                      padding: {t.px(6)}px {t.px(14)}px; }}
    QFrame#group {{ background: {GROUP}; border-radius: {t.px(11)}px; }}
    QFrame#hairline {{ background: {HAIR}; margin-left: {t.px(14)}px; }}
    QWidget#row, QWidget#row QLabel, QWidget#row QWidget {{ background: transparent; }}
    QLabel#rowTitle {{ font-size: {t.px(13)}px; }}
    QLabel#rowSub {{ color: {TEXT2}; font-size: {t.px(12)}px; }}
    QLabel#rowValue {{ color: {TEXT2}; }}
    QLabel#chip_ok {{ color: #30d158; background: rgba(48,209,88,38); {chip} }}
    QLabel#chip_accent {{ color: #409cff; background: rgba(10,132,255,40); {chip} }}
    QLabel#chip_warn {{ color: #ff9f0a; background: rgba(255,159,10,38); {chip} }}
    QLabel#chip_neutral {{ color: {TEXT2}; background: rgba(255,255,255,16); {chip} font-weight: 400; }}
    QPushButton {{ background: rgba(255,255,255,24); border: none; border-radius: {t.px(7)}px;
                   padding: {t.px(5)}px {t.px(12)}px; color: {TEXT}; }}
    QPushButton:hover {{ background: rgba(255,255,255,36); }}
    QPushButton#mini {{ padding: {t.px(2)}px {t.px(8)}px; min-width: {t.px(22)}px; color: {TEXT2};
                        background: rgba(255,255,255,14); }}
    QFrame#segmented {{ background: rgba(118,118,128,60); border-radius: {t.px(8)}px; }}
    QPushButton#segment {{ background: transparent; border-radius: {t.px(6)}px; padding: {t.px(4)}px {t.px(16)}px; }}
    QPushButton#segment:checked {{ background: rgba(255,255,255,60); color: white; }}
    QLineEdit, QComboBox, QSpinBox {{ background: rgba(255,255,255,20); border: none; border-radius: {t.px(8)}px;
                                      padding: {t.px(6)}px {t.px(10)}px; selection-background-color: {BLUE}; }}
    QComboBox::drop-down {{ border: none; width: {t.px(18)}px; }}
    QComboBox QAbstractItemView {{ background: {GROUP}; border: none; selection-background-color: {BLUE}; }}
    QListWidget#list {{ background: {GROUP}; border: none; border-radius: {t.px(11)}px; padding: {t.px(4)}px;
                        outline: 0; }}
    QListWidget#list::item {{ padding: {t.px(5)}px; border-radius: {t.px(6)}px; }}
    QListWidget#list::item:selected {{ background: {BLUE}; color: white; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: {t.px(10)}px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,40); border-radius: {t.px(4)}px; min-height: 30px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


def _icon_label(item_ids, size: int = 32) -> QLabel:
    ids = item_ids if isinstance(item_ids, tuple) else (item_ids,)
    lbl = QLabel()
    lbl.setPixmap(icon_tile(ids, size))
    lbl.setFixedSize(size, size)
    lbl.setStyleSheet("background: transparent;")
    return lbl


def _portrait(char_id: Optional[str], size: int = 52) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background: rgba(255,255,255,16); border-radius: {size // 4}px;")
    if char_id:
        pm = QPixmap(os.path.join(DATA_DIR, "portraits", char_id.replace(":", "_") + ".png"))
        if not pm.isNull():
            lbl.setPixmap(pm.scaled(size - 6, size - 6, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.FastTransformation))
    return lbl


def _caption(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("caption")
    lbl.setWordWrap(True)
    return lbl


class _Page(QScrollArea):
    def __init__(self, title: str):
        super().__init__()
        self.setWidgetResizable(True)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(24, 20, 24, 24)
        self.body.setSpacing(0)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        self.body.addWidget(t)
        self.setWidget(inner)

    def add(self, w: QWidget):
        self.body.addWidget(w)
        return w

    def end(self):
        self.body.addStretch(1)


class ControlWindow(QWidget):
    scan_requested = Signal()
    save_frame_requested = Signal()
    hud_edit_toggled = Signal(bool)
    hud_reset_requested = Signal()
    settings_changed = Signal()
    run_edited = Signal()
    mod_install_requested = Signal()
    layout_requested = Signal()

    def __init__(self, data: GameData, run: RunState, settings: Settings):
        super().__init__(None)
        self.data = data
        self.run = run
        self.settings = settings
        from ..version import APP_VERSION
        self.setWindowTitle(f'{tr("BALL x PIT 도우미")} {APP_VERSION}')
        self.resize(780, 660)
        self._owned_ids: List[str] = []
        self._history: List[RunRecord] = []
        self.battle = None        # 게임 연동 1.4 전투 정보 (BattleInfo)
        self.meta: Optional[MetaState] = None
        self._build()
        self.apply_scale(settings.font_scale)
        self.refresh_run()

    # ---- 뼈대 ----
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        for name in PAGES:
            self.sidebar.addItem(QListWidgetItem(name))
        side.addWidget(self.sidebar, 1)
        self.conn_label = QLabel("")
        self.conn_label.setStyleSheet(f"background: {SIDEBAR}; color: {TEXT2}; padding: 10px 16px 16px 18px;")
        self.conn_label.setWordWrap(True)
        self.conn_label.setFixedWidth(180)
        side.addWidget(self.conn_label)
        root.addLayout(side)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_run_page())
        self.stack.addWidget(self._build_evo_page())
        self.stack.addWidget(self._build_history_page())
        self.stack.addWidget(self._build_base_page())
        self.stack.addWidget(self._build_pedia_page())
        self.stack.addWidget(self._build_diag_page())
        self.stack.addWidget(self._build_settings_page())
        self.sidebar.currentRowChanged.connect(self._on_page)
        self.sidebar.setCurrentRow(0)

    def _on_page(self, i: int):
        self.stack.setCurrentIndex(i)
        if PAGE_KEYS[i] in ("현재 런", "진화"):
            self.refresh_run()

    def select_page(self, name: str):
        """name: PAGE_KEYS 의 내부 키(한국어, 번역 안 됨) — 화면에 보이는 번역된 이름이 아니다."""
        self.sidebar.setCurrentRow(PAGE_KEYS.index(name))

    def apply_scale(self, scale: float):
        self.setStyleSheet(_stylesheet(tk.Type(scale)))

    # ---- 현재 런 ----
    def _build_run_page(self) -> QWidget:
        pg = _Page(tr("현재 런"))
        pg.add(section_title(tr("캐릭터")))
        self.char_group = pg.add(Group())
        pg.add(section_title(tr("보유 볼")))
        self.balls_group = pg.add(Group())
        pg.add(section_title(tr("보유 패시브")))
        self.passives_group = pg.add(Group())
        pg.add(_caption(tr("선택창이 열릴 때마다 게임 상태로 다시 맞춥니다. 인식이 틀렸을 때만 직접 고치세요.")))
        pg.add(section_title(tr("이번 런 피해 (게임 통계)")))
        self.damage_group = pg.add(Group())
        pg.add(section_title(tr("전투 정보")))
        self.battle_group = pg.add(Group())
        pg.add(section_title(tr("캐릭터 능력치 (게임 내부 값)")))
        self.stats_group = pg.add(Group())
        pg.add(_caption(tr("능력치는 게임 내부 값을 그대로 보여 줍니다. 단위(배율·초·퍼센트)는 아직 실제 화면과 맞춰 보지 않았습니다. 캐릭터 기본 스탯은 건물·레벨 보너스 전 값이라 게임 능력치 화면보다 작을 수 있습니다.")))

        pg.add(section_title(tr("직접 추가")))
        add_group = pg.add(Group())
        box = QWidget()
        box.setObjectName("row")
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 8, 14, 8)
        self.add_combo = QComboBox()
        self.add_combo.setEditable(True)
        self.add_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for it in sorted(self.data.items.values(), key=lambda i: (i.kind, i.name_ko)):
            self.add_combo.addItem(f"{it.name_ko} · {KIND_LABEL[it.kind]}", it.id)
        comp = self.add_combo.completer()
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.add_combo.setCurrentIndex(-1)
        self.add_combo.lineEdit().setPlaceholderText(tr("항목 이름으로 찾기"))
        h.addWidget(self.add_combo, 1)
        b = QPushButton(tr("추가"))
        b.clicked.connect(self._add)
        h.addWidget(b)
        add_group.add(box)

        pg.add(section_title(tr("최근 기록")))
        self.history_group = pg.add(Group())
        pg.end()
        return pg

    def refresh_run(self):
        run, d = self.run, self.data
        self.char_group.clear()
        cid = run.character_ids[0] if run.character_ids else None
        src = run.characters[0][1] if run.characters else ""
        combo = QComboBox()
        combo.addItem(tr("미확인"), None)
        for c in sorted(d.characters.values(), key=lambda c: c.name_ko):
            combo.addItem(c.name_ko, c.id)
        combo.setCurrentIndex(max(0, combo.findData(cid)))
        combo.activated.connect(lambda _i, cb=combo: self._on_char_changed(cb.currentData()))
        combo.setFixedWidth(150)
        extra = [d.name(c) for c in run.character_ids[1:]]
        title = (d.name(cid) if cid else tr("캐릭터 미확인")) + (f" + {', '.join(extra)}" if extra else "")
        sub = d.characters[cid].desc_ko if cid else tr("게임에서 확인되면 자동으로 채워집니다")
        if src:
            sub += f"\n{CHAR_SOURCE.get(src, src)}"
        self.char_group.add(row(title, combo, sub, _portrait(cid)))

        self._owned_ids = []
        for group, kind in ((self.balls_group, "ball"), (self.passives_group, "passive")):
            group.clear()
            items = sorted((o for o in run.owned.values() if o.kind == kind), key=lambda o: d.name(o.item_id))
            if not items:
                group.add(row(tr("없음") if run.inventory_seen else tr("아직 확인 전"), None,
                              "" if run.inventory_seen else tr("선택창이 열리면 채워집니다")))
            for o in items:
                self._owned_ids.append(o.item_id)
                right = QWidget()
                hl = QHBoxLayout(right)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(4)
                hl.addWidget(value_label(tr("레벨 {level}", level=o.level) if o.level else tr("레벨 ?")))
                hl.addSpacing(6)
                for text, delta in (("−", -1), ("+", 1)):
                    bt = QPushButton(text)
                    bt.setObjectName("mini")
                    bt.clicked.connect(lambda _=False, i=o.item_id, dd=delta: self._bump(i, dd))
                    hl.addWidget(bt)
                rm = QPushButton(tr("빼기"))
                rm.setObjectName("mini")
                rm.clicked.connect(lambda _=False, i=o.item_id: self._remove(i))
                hl.addWidget(rm)
                copies = f"  ×{o.copies}" if o.copies > 1 else ""
                parts = tuple(dict.fromkeys((o.item_id, *o.combined)))
                title = " + ".join(d.name(i) for i in parts) if o.copies == 1 else d.name(o.item_id) + copies
                details = SOURCE_LABEL.get(o.source, o.source)
                if o.combined:
                    if o.copies == 1:
                        details = tr("융합 볼 · 보유 슬롯 1칸") + " · " + details
                    elif o.instances:
                        # 복사본의 효과 합집합을 한 볼에 전부 합쳐진 것처럼 표시하지 않는다.
                        details = "\n".join(tr("슬롯 {slot}: {parts}", slot=s.index + 1,
                                                parts=" + ".join(d.name(i) for i in (s.item_id, *s.combined)))
                                            for s in o.instances) + "\n" + details
                    else:
                        details = tr("합쳐진 효과 포함: {parts}", parts=" + ".join(d.name(i) for i in o.combined)) + " · " + details
                group.add(row(title, right, details, _icon_label(parts if o.copies == 1 else o.item_id, 30)))

        self.damage_group.clear()
        balls = sorted(((v, i) for i, v in run.damage.items() if i.startswith("ball:")), reverse=True)
        total = sum(v for v, _ in balls) or 1
        if not balls:
            self.damage_group.add(row(tr("아직 없음"), None, tr("게임 연동 1.2 이상에서 런 중에 채워집니다")))
        by = self.battle.dmg_by if self.battle is not None else {}
        for v, i in balls:
            parts = [tr("피해 {v:,}", v=v)]
            b = by.get(i) or {}
            tot = sum(b.values())
            if tot:
                parts += [f"{DMG_LABEL.get(k, k)} {round(x * 100 / tot)}%" for k, x in
                          sorted(b.items(), key=lambda t: -t[1]) if x]
            self.damage_group.add(row(d.name(i), value_label(f"{round(v * 100 / total)}%"), " · ".join(parts),
                                      _icon_label(i, 30)))
        self._refresh_battle()

        self.history_group.clear()
        for h in list(reversed(run.history[-8:])) or [tr("기록 없음")]:
            self.history_group.add(row(h))
        self._refresh_evo()

    def set_battle(self, info):
        self.battle = info

    def _refresh_battle(self):
        b = self.battle
        self.battle_group.clear()
        self.stats_group.clear()
        if b is None:
            self.battle_group.add(row(tr("아직 없음"), None, tr("게임 연동 1.4 이상, 런 중에 채워집니다")))
            self.stats_group.add(row(tr("아직 없음")))
            return
        if b.xp is not None and b.xp_next:
            self.battle_group.add(row(tr("다음 레벨 업까지"), value_label(f"{max(0, round(b.xp_next - b.xp)):,}"),
                                      tr("경험치 {v0:,} / {xp_next:,}", v0=round(b.xp), xp_next=b.xp_next)))
        if b.enemies is not None:
            self.battle_group.add(row(tr("화면의 적"), value_label(str(b.enemies))))
        if b.boss_ratio is not None:
            self.battle_group.add(row(tr("보스 체력"), value_label(f"{round(b.boss_ratio * 100)}%"),
                                      f"{b.boss_hp:,} / {b.boss_max:,}"))
        if b.effects:
            self.battle_group.add(row(tr("내 상태 효과"), None, ", ".join(tr("{v0} {left:.0f}초", v0=t.lstrip('k'), left=left) for t, left in b.effects)))
        if b.treasures:
            self.battle_group.add(row(tr("보물"), value_label(str(b.treasures))))
        for key, label in STAT_LABEL.items():
            if key in b.stats:
                self.stats_group.add(row(label, value_label(_num(b.stats[key]))))
        if b.char_stats:
            self.stats_group.add(row(tr("캐릭터 기본 스탯"), None, " · ".join(
                f"{CHAR_STAT_LABEL[i] if i < len(CHAR_STAT_LABEL) else i} {v}" for i, v in enumerate(b.char_stats))))

    def owned_ids(self) -> List[str]:
        return list(self._owned_ids)

    def _on_char_changed(self, char_id):
        self.run.set_character(char_id)
        self._edited()

    def _bump(self, item_id: str, delta: int):
        o = self.run.owned.get(item_id)
        if o is None:
            return
        self.run.set_owned(item_id, o.kind, max(1, (o.level or 1) + delta))
        self._edited()

    def _remove(self, item_id: str):
        self.run.remove_owned(item_id)
        self._edited()

    def _add(self):
        item_id = self.add_combo.currentData()
        if not item_id or self.add_combo.currentText() != self.add_combo.itemText(self.add_combo.currentIndex()):
            return
        self.run.set_owned(item_id, self.data.items[item_id].kind, 1)
        self.add_combo.setCurrentIndex(-1)
        self._edited()

    def _edited(self):
        self.refresh_run()
        self.run_edited.emit()

    # ---- 진화 ----
    def _build_evo_page(self) -> QWidget:
        pg = _Page(tr("진화"))
        pg.add(section_title(tr("덱 목표 (직접 고정)")))
        self.target_group = pg.add(Group())
        target_add_group = pg.add(Group())
        box = QWidget()
        box.setObjectName("row")
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 8, 14, 8)
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for r in browsable_targets(self.data):
            self.target_combo.addItem(self.data.name(r.result), r.result)
        comp = self.target_combo.completer()
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.target_combo.setCurrentIndex(-1)
        self.target_combo.lineEdit().setPlaceholderText(tr("목표로 삼을 진화 찾기"))
        h.addWidget(self.target_combo, 1)
        lock_btn = QPushButton(tr("고정"))
        lock_btn.clicked.connect(self._lock_target)
        h.addWidget(lock_btn)
        target_add_group.add(box)
        pg.add(_caption(tr("고정하면 재료가 하나도 없어도 이 진화를 목표로 보고 추천이 거기 맞춰집니다. 새 런을 시작하면 자동으로 풀립니다.")))
        pg.add(section_title(tr("보유 볼로 만들 수 있는 진화")))
        self.evo_group = pg.add(Group())
        self.evo_caption = pg.add(_caption(""))
        pg.add(section_title(tr("지금 융합할 수 있는 최대 레벨 볼")))
        self.fusion_group = pg.add(Group())
        pg.add(_caption(tr("융합 화면이 열리면 HUD가 게임이 제시한 진화·융합 후보 중 무엇을 고를지 추천합니다.")))
        pg.add(section_title(tr("전체 진화표")))
        self.evo_search = QLineEdit()
        self.evo_search.setPlaceholderText(tr("결과나 재료 이름으로 찾기"))
        self.evo_search.textChanged.connect(lambda _t: self._refresh_all_recipes())
        pg.add(self.evo_search)
        self.all_recipes_group = pg.add(Group())
        pg.end()
        return pg

    def _refresh_all_recipes(self):
        d, run = self.data, self.run
        q = self.evo_search.text().strip().lower()
        key = (q, frozenset(run.owned), len(d.recipes), d.recipe_source, len(d.available or ()))
        if key == getattr(self, "_recipes_key", None):
            return     # 보유 목록·검색어가 그대로면 다시 그리지 않는다
        self._recipes_key = key
        self.all_recipes_group.clear()
        shown = 0
        for r in sorted(d.recipes, key=lambda r: d.name(r.result)):
            names = [d.name(r.result)] + [d.name(i) for i in r.ingredients]
            if q and not any(q in n.lower() for n in names):
                continue
            have = sum(1 for i in r.ingredients if i in run.owned)
            parts = " + ".join(d.name(i) + (" ✓" if i in run.owned else "") for i in r.ingredients)
            tone = "ok" if have == len(r.ingredients) else "accent" if have else "neutral"
            locked = d.locked_ingredients(r)
            if locked:
                parts += " · " + tr("해금 안 된 재료: {v0}", v0=", ".join(d.name(i) for i in locked))
            status = chip(tr("잠김"), "neutral") if locked else chip(tr("재료 {have}/{v0}", have=have, v0=len(r.ingredients)), tone)
            self.all_recipes_group.add(row(d.name(r.result), status, parts,
                                           _icon_label(r.result, 30)))
            shown += 1
            if shown >= 60:
                break
        if not shown:
            self.all_recipes_group.add(row(tr("찾는 레시피 없음")))

    def _lock_target(self):
        item_id = self.target_combo.currentData()
        if not item_id or self.target_combo.currentText() != self.target_combo.itemText(self.target_combo.currentIndex()):
            return
        self.run.lock_target(item_id, self.data)
        self.target_combo.setCurrentIndex(-1)
        self._edited()

    def _clear_target(self):
        self.run.clear_target(self.data)
        self._edited()

    def _refresh_evo(self):
        d, run = self.data, self.run
        self.target_group.clear()
        if run.locked_target:
            btn = QPushButton(tr("해제"))
            btn.setObjectName("mini")
            btn.clicked.connect(self._clear_target)
            self.target_group.add(row(d.name(run.locked_target), btn, tr("고정된 덱 목표 — 추천이 이쪽으로 맞춰집니다"),
                                      _icon_label(run.locked_target, 34)))
        else:
            self.target_group.add(row(tr("고정한 목표 없음"), None, tr("지금은 보유 볼로 자동 감지합니다")))
        self.evo_group.clear()
        entries = build_roadmap(run, d)[:10]
        if not entries:
            self.evo_group.add(row(tr("진행 중인 진화 없음"), None, tr("보유 볼이 재료인 레시피가 없습니다")))
        for e in entries:
            parts = " + ".join(d.name(i) + ("" if i in e.have else tr(" (없음)")) for i in e.recipe.ingredients)
            tone = "ok" if e.status == tr("진화 가능") else "accent" if not e.missing else "neutral"
            self.evo_group.add(row(d.name(e.recipe.result), chip(e.status, tone), parts,
                                   _icon_label(e.recipe.result, 34)))
        self._refresh_all_recipes()
        src = (tr("게임 안 레시피") if d.recipe_source == "game"
               else tr("위키 레시피(미검증) — 게임 연동이 연결되면 게임 레시피로 바뀝니다"))
        self.evo_caption.setText(tr("레시피: {src} · 최대 레벨 {v0}", src=src, v0=d.max_level('ball')))
        self.fusion_group.clear()
        pairs = fusion_pairs(run, d)
        if not pairs:
            self.fusion_group.add(row(tr("없음"), None, tr("서로 다른 최대 레벨 볼이 두 개 이상 필요합니다")))
        for a, b in pairs[:8]:
            self.fusion_group.add(row(f"{d.name(a)} + {d.name(b)}", chip(tr("융합 가능"), "accent"),
                                      tr("두 볼의 효과를 한 볼에"), _icon_label((a, b), 34)))

    # ---- 기록 ----
    def _build_history_page(self) -> QWidget:
        pg = _Page(tr("기록"))
        self.hist_summary = pg.add(_caption(""))
        pg.add(section_title(tr("최근 런")))
        self.hist_group = pg.add(Group())
        pg.add(section_title(tr("자주 가진 항목 · 보스 격퇴")))
        self.hist_items = pg.add(Group())
        pg.add(section_title(tr("새로고침 확률 보정")))
        self.draw_label = pg.add(_caption(tr("뽑기 관측 없음")))
        pg.add(section_title(tr("원정 계속 판단 기록")))
        self.exp_group = pg.add(Group())
        pg.add(section_title(tr("내 누적 기록 (게임 세이브) · 런당 평균 피해 상위 볼")))
        self.hist_meta = pg.add(Group())
        pg.add(_caption(tr("런 기록은 이 PC에만 저장됩니다: {v0}. 누적 기록은 게임 세이브 값이며, 새 볼 카드 추천에 '내 기록' 이유로 쓰입니다.", v0=os.path.join(APP_DIR, 'runs.jsonl'))))
        pg.end()
        return pg

    def set_history(self, records: List[RunRecord]):
        self._history = records
        if self.meta is not None:
            self.set_meta(self.meta)
        d = self.data
        n = len(records)
        wins = sum(r.result == "보스 격퇴" for r in records)
        self.hist_summary.setText(tr("기록된 런 {n}판 · 보스 격퇴 {wins}판", n=n, wins=wins) if n else
                                  tr("아직 기록이 없습니다. 게임 연동 중에 한 판을 마치면 여기에 쌓입니다."))
        self.hist_group.clear()
        for r in list(reversed(records))[:15]:
            title, sub = describe_run(r, d)
            self.hist_group.add(row(title, None, sub, _portrait(r.char, 36)))
        if not records:
            self.hist_group.add(row(tr("기록 없음")))
        self.hist_items.clear()
        summ = sorted(((a, b, i) for i, (a, b) in item_summary(records).items() if a >= 2),
                      key=lambda t: (-t[0], -t[1]))[:10]
        for a, b, i in summ:
            self.hist_items.add(row(d.name(i), value_label(tr("{b}/{a}판 격퇴", b=b, a=a)), KIND_LABEL.get(i.split(":")[0], ""),
                                    _icon_label(i, 30)))
        if not summ:
            self.hist_items.add(row(tr("두 판 이상 가진 항목이 아직 없음")))
        self._refresh_exp_history()
        self._refresh_meta_records()

    def set_draw_summary(self, text: str):
        self.draw_label.setText(text + tr(" — 선택창마다 후보와 실제로 나온 카드를 기록해 새로고침 확률을 실제 경향에 맞춥니다."))

    def _refresh_exp_history(self):
        self.exp_group.clear()
        rows = [r for r in self._history if r.expedition]
        if not rows:
            self.exp_group.add(row(tr("아직 없음"), None, tr("보스를 깬 뒤 원정 계속 화면을 볼 때마다 판단과 결과가 쌓입니다")))
            return
        label = {"continue": tr("계속 추천"), "return": tr("복귀 추천"), "either": tr("선택")}
        for verdict in ("continue", "either", "return"):
            rs = [r for r in rows if (r.expedition or {}).get("verdict") == verdict]
            if not rs:
                continue
            cont = [r.endless_turns for r in rs if r.continued]
            med = sorted(cont)[len(cont) // 2] if cont else None
            sub = tr("계속한 {v0}번", v0=len(cont)) + (tr(" · 버틴 턴 중간값 {med}", med=med) if med is not None else "")
            self.exp_group.add(row(label[verdict], value_label(tr("{v0}번", v0=len(rs))), sub))

    def _refresh_meta_records(self):
        self.hist_meta.clear()
        m, d = self.meta, self.data
        rows = []
        if m is not None:
            rows = sorted(((r.damage_per_run, i, r) for i, r in m.records.items()
                           if i.startswith("ball:") and r.obtained >= 3 and r.damage_per_run), reverse=True)[:10]
        for dpr, i, r in rows:
            self.hist_meta.add(row(d.name(i), value_label(f"{round(dpr):,}"),
                                   tr("가진 런 {obtained}번 · 완료 {completed}번", obtained=r.obtained, completed=r.completed), _icon_label(i, 30)))
        if not rows:
            self.hist_meta.add(row(tr("게임 연동 1.2 이상 연결 후 표시"), None, tr("게임 세이브의 볼별 누적 기록")))

    # ---- 기지 ----
    def _build_base_page(self) -> QWidget:
        pg = _Page(tr("기지"))
        pg.add(section_title(tr("보유 자원")))
        self.base_res = pg.add(Group())
        pg.add(section_title(tr("다음에 할 일")))
        self.base_todo = pg.add(Group())
        pg.add(_caption(tr("공략 핵심 건물을 먼저 표시하며, 비용·해금은 게임 값을 사용합니다. 일반 후보 사이의 효율 순위는 계산하지 않습니다.")))
        pg.add(section_title(tr("배치도")))
        lg = pg.add(Group())
        lb = QPushButton(tr("배치도 열기"))
        lb.clicked.connect(self.layout_requested.emit)
        lg.add(row(tr("가이드 배치 · 채집 궤적"), lb, tr("Steam 공략의 일부 규칙을 참고한 적은 이동안입니다. 최적 배치를 보장하지 않습니다.")))
        self.base_layout = pg.add(Group())
        self.base_layout.add(row(tr("기지 화면에 들어가면 계산합니다")))
        pg.add(section_title(tr("스파 재채집 손익")))
        self.base_spa = pg.add(Group())
        self.base_spa.add(row(tr("스파(목욕탕)를 지으면 표시합니다"), None, tr("비용은 게임 값, 얻는 양은 내 채집 기록 평균")))
        pg.add(section_title(tr("작업자 배치 · 채집 강화가 맞는 캐릭터")))
        self.base_workers = pg.add(Group())
        pg.add(_caption(tr("캐릭터의 채집 강화(빠른 돌 채집 등)와 생산 건물 자원을 맞춘 추천입니다. 강화 효과의 크기는 확인하지 못했습니다. 채집 조준 안내는 채집 화면에서 게임 위에 표시됩니다.")))
        pg.add(section_title(tr("설계도 파밍 · 아직 못 얻은 설계도가 많은 지역")))
        self.base_bp = pg.add(Group())
        pg.add(section_title(tr("다음 런 캐릭터 · 지역별로 아직 안 깬 캐릭터")))
        self.base_chars = pg.add(Group())
        pg.add(_caption(tr("캐릭터 순서는 내 런 기록의 보스 격퇴율, 그다음 캐릭터 레벨입니다. 캐릭터 강함은 비교하지 않습니다.")))
        pg.add(section_title(tr("건물로 얻은 런 보너스")))
        self.base_bonus = pg.add(Group())
        pg.end()
        return pg

    def set_spa(self, adv):
        self.base_spa.clear()
        if adv is None:
            self.base_spa.add(row(tr("스파(목욕탕)를 지으면 표시합니다"), None, tr("비용은 게임 값, 얻는 양은 내 채집 기록 평균")))
            return
        label = {"profit": tr("이득"), "resources": tr("자원 필요 시"), "loss": tr("손해"), "unknown": tr("기록 부족")}[adv.verdict]
        self.base_spa.add(row(tr("비용 {cost:,}골드", cost=adv.cost), chip(label, adv.tone), adv.text))

    def set_layout_plan(self, plan, unmanned=()):
        """배치 계산 결과 요약: 최적 배치까지 옮기기, 새로 지을 건물(자리 포함), 강화하면 켜지는 효과."""
        d = self.data
        self.base_layout.clear()
        if unmanned:
            from collections import Counter
            names = " · ".join(f"{d.building_name(t)}{tr(' {n}개', n=n) if n > 1 else ''}" for t, n in Counter(unmanned).items())
            self.base_layout.add(row(tr("일꾼 없음: {names}", names=names), chip(tr("생산 0"), "warn")))
        if plan is None:
            self.base_layout.add(row(tr("배치 계산 실패 또는 기지 정보 없음")))
            return
        pct = (plan.score_after / plan.score_before - 1) * 100 if plan.score_before else 0.0
        if not getattr(plan, "movement_complete", True):
            self.base_layout.add(row(tr("안전한 이동 순서를 만들지 못했습니다. 배치도를 다시 계산해 주세요."),
                                     chip(tr("보류"), "warn")))
        elif plan.swaps:
            self.base_layout.add(row(tr("가이드 배치까지 옮기기 {v0}번", v0=len(plan.swaps)), chip(tr("범위 효과 {pct:+.0f}%", pct=pct), "accent"),
                                     tr("재배치 모드에 들어가면 다음 옮기기가 게임 화면에 번호로 나옵니다")))
        else:
            self.base_layout.add(row(tr("조건을 지키는 더 나은 이동을 찾지 못했습니다."), chip(tr("유지"), "neutral")))
        for t, _c, _sz, gain, n, *_ in (getattr(plan, "builds", None) or [])[:3]:
            from ..engine.layout_opt import TILE_RES
            what = tr("가이드") if t in TILE_RES else tr("범위 효과 +{gain:.1f}", gain=gain)
            title = tr("더 사기: {v0} {n}개", v0=d.building_name(t), n=n) if t in TILE_RES else tr("짓기: {v0}", v0=d.building_name(t))
            self.base_layout.add(row(title, chip(what, "accent"), tr("배치도의 초록 점선 자리")))
        if getattr(plan, "construction_pending", False):
            self.base_layout.add(row(tr("재배치 완료 후 건설 위치를 다시 계산합니다.")))
        for _i, t, what, gain in (getattr(plan, "activations", None) or [])[:3]:
            label = (tr("공략 후순위") + " · ") if t == "kMansion" else ""
            self.base_layout.add(row(f"{label}{what}: {d.building_name(t)}", chip(tr("범위 효과 +{gain:.1f}", gain=gain), "neutral"),
                                     tr("지금은 효과를 절반으로 계산 중")))

    def set_base_context(self, base: Optional[dict]):
        """능력치 대상의 게임 값을 건설 조언에도 전달한다. 대상 구성이 바뀔 때만 화면을 갱신한다."""
        if not base:
            return
        key = frozenset((b.get("type"), b.get("stat")) for b in base.get("buildings") or [])
        self._base = base
        if key != getattr(self, "_base_context_key", None):
            self._base_context_key = key
            if getattr(self, "meta", None) is not None:
                self.set_meta(self.meta)

    def set_meta(self, meta: MetaState, resource_note: Optional[str] = None):
        self.meta = meta
        d = self.data
        self.base_res.clear()
        self.base_res.add(row(" · ".join(f"{RESOURCES[i]} {v:,}" for i, v in enumerate(meta.resources)
                                         if i < len(RESOURCES)) or tr("확인 전")))
        if resource_note:
            self.base_res.add(row(resource_note))
        self.base_todo.clear()
        todo = suggest_base(meta, d, base=getattr(self, "_base", None))
        for sg in todo:
            if sg.kind == "finish":
                self.base_todo.add(row(sg.name, chip(tr("먼저 완성"), "warn"), " · ".join(x for x in (sg.cost_text, sg.desc) if x)))
                continue
            tone = "ok" if sg.affordable else "neutral"
            what = tr("짓기") if sg.kind == "build" else tr("강화")
            cat = CAT_LABEL.get(sg.category, "")
            need = "" if sg.affordable or not sg.missing_text else tr("{missing_text} 부족", missing_text=sg.missing_text)
            sub = " · ".join(x for x in (f"{what} {sg.cost_text}", need, cat, sg.reason, sg.source, sg.desc) if x)
            self.base_todo.add(row(sg.name, chip(f"{sg.policy_label} · {sg.status}", tone), sub))
        if not todo:
            self.base_todo.add(row(tr("추천할 새 건물·강화 없음")))
        self.base_workers.clear()
        from ..engine.harvest import need_resource
        need, _ = need_resource(meta, {})
        wa = advise_workers(meta, meta.chars_raw, [b.type for b in meta.buildings], d, need)
        for a in wa:
            self.base_workers.add(row(d.building_name(a.building), chip(d.name(a.char_id), "accent"),
                                      f"{a.reason} · {a.current}", _portrait(a.char_id, 32)))
        if not wa:
            self.base_workers.add(row(tr("바꿀 일꾼 없음"), None, tr("빈 생산 건물이 없고, 발사 채집 강화가 많은 캐릭터가 건물에서 일하고 있지도 않음 (건물 일꾼은 채집 때 발사되지 않음)")))
        plans = plan_levels(meta, d, self._history)
        self.base_bp.clear()
        targets = blueprint_targets(plans)
        for lp in targets[:5]:
            names = ", ".join(d.building_name(t) for t in lp.blueprints_left[:4])
            more = tr(" 외 {v0}개", v0=len(lp.blueprints_left) - 4) if len(lp.blueprints_left) > 4 else ""
            self.base_bp.add(row(lp.name, value_label(tr("{v0}개", v0=len(lp.blueprints_left))), names + more))
        if not targets:
            self.base_bp.add(row(tr("표시할 지역 없음"), None, tr("게임 연동 1.3 이상에서 채워집니다")
                                 if not any("blueprints_left" in lv for lv in meta.levels) else tr("해금 지역의 설계도를 모두 얻음")))
        self.base_chars.clear()
        shown = 0
        for lp in plans:
            if not lp.chars_left:
                continue
            top = ", ".join(d.name(c) for c in lp.chars_left[:3])
            more = tr(" 외 {v0}명", v0=len(lp.chars_left) - 3) if len(lp.chars_left) > 3 else ""
            self.base_chars.add(row(lp.name, value_label(tr("완료 {chars_done}명", chars_done=lp.chars_done)), tr("추천: {top}{more}", top=top, more=more),
                                    _portrait(lp.chars_left[0], 36)))
            shown += 1
        if not shown:
            self.base_chars.add(row(tr("표시할 지역 없음"), None, tr("게임 연동 1.3 이상에서 채워집니다")
                                    if not any("chars_done" in lv for lv in meta.levels) else tr("모든 캐릭터가 해금 지역을 완료")))
        self.base_bonus.clear()
        b = meta.bonuses
        labels = (("banishes", tr("삭제 횟수")), ("free_rerolls", tr("무료 새로고침")), ("revives", tr("부활")),
                  ("choices", tr("선택지 수")), ("ball_slots", tr("볼 칸")), ("passive_slots", tr("패시브 칸")))
        for key, label in labels:
            if key in b:
                self.base_bonus.add(row(label, value_label(str(b[key]))))
        if "endless" in b:
            self.base_bonus.add(row(tr("보스 격퇴 후 원정 계속"), value_label(tr("해금") if b["endless"] else tr("잠김"))))
        self._refresh_meta_records()

    # ---- 백과사전 ----
    def _build_pedia_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(12)
        t = QLabel(tr("백과사전"))
        t.setObjectName("pageTitle")
        v.addWidget(t)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("검색 (모든 언어의 이름)"))
        self.search.textChanged.connect(self._filter_pedia)
        v.addWidget(self.search)
        h = QHBoxLayout()
        h.setSpacing(14)
        self.pedia_list = QListWidget()
        self.pedia_list.setObjectName("list")
        self.pedia_list.setIconSize(QSize(26, 26))
        self.pedia_list.setFixedWidth(230)
        self.pedia_list.currentItemChanged.connect(self._show_item)
        h.addWidget(self.pedia_list)
        detail = QScrollArea()
        detail.setWidgetResizable(True)
        inner = QWidget()
        self.pedia_body = QVBoxLayout(inner)
        self.pedia_body.setContentsMargins(0, 0, 0, 0)
        self.pedia_body.setSpacing(0)
        detail.setWidget(inner)
        h.addWidget(detail, 1)
        v.addLayout(h, 1)
        self._filter_pedia("")
        return w

    def _filter_pedia(self, text: str):
        q = text.strip().lower()
        self.pedia_list.clear()
        for it in sorted(self.data.items.values(), key=lambda i: (i.kind, i.name_ko)):
            if q and not any(q in n.lower() for n in it.all_names()):
                continue
            li = QListWidgetItem(QIcon(icon_tile((it.id,), 26)), it.name_ko)
            li.setData(Qt.ItemDataRole.UserRole, it.id)
            self.pedia_list.addItem(li)

    def _show_item(self, cur: Optional[QListWidgetItem], _prev=None):
        while self.pedia_body.count():
            x = self.pedia_body.takeAt(0)
            if x.widget():
                x.widget().deleteLater()
        if cur is None:
            return
        d = self.data
        it = d.items[cur.data(Qt.ItemDataRole.UserRole)]
        head = Group()
        head.add(row(it.name_ko, chip(KIND_LABEL[it.kind]), it.name_en, _icon_label(it.id, 44)))
        desc = QLabel(d.describe(it.id) or tr("설명 없음"))
        desc.setWordWrap(True)
        desc.setStyleSheet(f"background: transparent; color: {TEXT2}; padding: 10px 14px 12px 14px;")
        head.add(desc)
        self.pedia_body.addWidget(head)
        src = tr("게임 레시피") if d.recipe_source == "game" else tr("위키 레시피")
        made = d.recipes_for(it.id)
        if made:
            self.pedia_body.addWidget(section_title(tr("만드는 법 · {src}", src=src)))
            g = Group()
            for r in made:
                locked = d.locked_ingredients(r)
                g.add(row(" + ".join(d.name(i) for i in r.ingredients), chip(tr("잠김"), "neutral") if locked else None,
                          tr("해금 안 된 재료: {v0}", v0=", ".join(d.name(i) for i in locked)) if locked else "",
                          _icon_label(tuple(r.ingredients[:2]), 30)))
            self.pedia_body.addWidget(g)
        into = d.recipes_using(it.id)
        if into:
            self.pedia_body.addWidget(section_title(tr("재료로 쓰이는 곳 · {src}", src=src)))
            g = Group()
            for r in into:
                locked = d.locked_ingredients(r)
                sub = " + ".join(d.name(i) for i in r.ingredients)
                if locked:
                    sub += " · " + tr("해금 안 된 재료: {v0}", v0=", ".join(d.name(i) for i in locked))
                g.add(row(d.name(r.result), chip(tr("잠김"), "neutral") if locked else None, sub,
                          _icon_label(r.result, 30)))
            self.pedia_body.addWidget(g)
        lv = d.level_props.get(it.id)
        if lv:
            dmg = [d.damage_range(it.id, i + 1) for i in range(len(lv))]
            if any(dmg):
                self.pedia_body.addWidget(section_title(tr("레벨별 기본 피해 · 게임 수치")))
                g = Group()
                for i, rng in enumerate(dmg):
                    if rng:
                        g.add(row(tr("레벨 {v0}", v0=i + 1), value_label(f"{rng[0]}–{rng[1]}")))
                self.pedia_body.addWidget(g)
        tags = [d.tag_label(t) for t in d.rules.get("tags", {}) if d.has_tag(it.id, t)]
        if tags:
            self.pedia_body.addWidget(_caption(tr("분류: ") + ", ".join(tags)))
        self.pedia_body.addWidget(_caption(tr("이름·설명은 게임 파일 원문, 설명 속 수치는 게임 연동으로 받은 값입니다 (여러 개면 레벨 1/2/3).")))
        self.pedia_body.addStretch(1)

    # ---- 진단 ----
    def _build_diag_page(self) -> QWidget:
        pg = _Page(tr("진단"))
        pg.add(section_title(tr("동작")))
        g = pg.add(Group())
        b1 = QPushButton(tr("다시 읽기"))
        b1.clicked.connect(self.scan_requested.emit)
        g.add(row(tr("지금 다시 읽기"), b1, tr("F8 과 같음")))
        b2 = QPushButton(tr("저장"))
        b2.clicked.connect(self.save_frame_requested.emit)
        g.add(row(tr("게임 화면 저장"), b2, tr("문제 재현용. 게임 창 영역만 이 PC에 저장")))
        self.edit_btn = Toggle(False)
        self.edit_btn.toggled.connect(self.hud_edit_toggled.emit)
        g.add(row(tr("HUD 위치 조정"), self.edit_btn, tr("켠 뒤 HUD를 끌어서 옮기세요")))
        b3 = QPushButton(tr("초기화"))
        b3.clicked.connect(self.hud_reset_requested.emit)
        g.add(row(tr("HUD 위치 초기화"), b3))
        pg.add(section_title(tr("게임 연동 모드")))
        mg = pg.add(Group())
        self.mod_label = value_label(tr("확인 중"), wrap=True)
        self.mod_label.setMaximumWidth(360)
        mg.add(row(tr("상태"), self.mod_label))
        b4 = QPushButton(tr("설치 · 복구"))
        b4.clicked.connect(self.mod_install_requested.emit)
        mg.add(row(tr("BepInEx + 연동 플러그인"), b4, tr("없거나 옛 버전이면 설치합니다. 게임이 켜져 있으면 끈 뒤에 설치")))
        pg.add(section_title(tr("상태")))
        self.diag_group = pg.add(Group())
        self._diag_rows: Dict[str, QLabel] = {}
        pg.add(section_title(tr("최근 인식")))
        self.diag_cards = pg.add(_caption("-"))
        pg.add(section_title(tr("처리 시간 (최근 50회)")))
        self.diag_timing = pg.add(_caption(tr("아직 없음")))
        self.saved_label = pg.add(_caption(""))
        pg.end()
        return pg

    def update_diagnostics(self, rows: Dict[str, str], cards: str, timing: str):
        for key, value in rows.items():
            lbl = self._diag_rows.get(key)
            if lbl is None:
                lbl = value_label("", wrap=True)
                lbl.setMaximumWidth(340)
                self.diag_group.add(row(key, lbl))
                self._diag_rows[key] = lbl
            lbl.setText(value)
        self.diag_cards.setText(cards)
        self.diag_timing.setText(timing)

    def set_mod_status(self, text: str, tone: str):
        color = {"ok": TEXT2, "wait": TEXT2}.get(tone, "#ff9f0a")
        self.mod_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.mod_label.setText(text)

    def set_connection(self, text: str):
        dot = "●" if text == tr("게임 연동 중") else "○"
        self.conn_label.setText(f"{dot}  {text}")

    # ---- 설정 ----
    def _build_settings_page(self) -> QWidget:
        s = self.settings
        pg = _Page(tr("설정"))
        pg.add(section_title(tr("표시")))
        g = pg.add(Group())
        seg = Segmented([tr("보통"), tr("크게")], 1 if s.font_scale > 1.05 else 0)
        seg.changed.connect(lambda i: self._set_scale(1.2 if i else 1.0))
        g.add(row(tr("글자 크기"), seg, tr("HUD와 이 창에 함께 적용")))
        lengths = ["short", "normal", "long"]
        path_length = Segmented([tr("짧게"), tr("보통"), tr("길게")],
                                lengths.index(s.aim_path_length) if s.aim_path_length in lengths else 1)
        path_length.changed.connect(lambda i: self._set("aim_path_length", lengths[i]))
        g.add(row(tr("조준 경로 길이"), path_length,
                  tr("현재 조준 1 / 3 / 7회 반사 · 추천은 1회 · Shift를 누르는 동안 둘 다 7회")))
        discovery = Toggle(s.encyclopedia_mode)
        discovery.toggled.connect(lambda on: self._set("encyclopedia_mode", on))
        g.add(row(tr("백과사전 해금 모드"), discovery,
                  tr("보유 볼에서 이어지는 미발견 상위 볼·융합 조합을 우선 추천")))
        self.auto_cb = Toggle(s.hud_auto_show)
        self.auto_cb.toggled.connect(lambda on: self._set("hud_auto_show", on))
        g.add(row(tr("선택창에서 HUD 자동 표시"), self.auto_cb))
        self.outline_cb = Toggle(s.card_outline)
        self.outline_cb.toggled.connect(lambda on: self._set("card_outline", on))
        g.add(row(tr("카드 판정 테두리"), self.outline_cb, tr("추천 파랑 · 비슷함 초록 · 삭제 추천 주황 · 비추천 빨강 (비추천은 살짝 어둡게)")))
        modes = ["auto", "never", "always"]
        cap = Segmented([tr("자동"), tr("보이기"), tr("숨기기")], modes.index(s.hide_from_capture)
                        if s.hide_from_capture in modes else 0)
        cap.changed.connect(lambda i: self._set("hide_from_capture", modes[i]))
        g.add(row(tr("스크린샷·녹화에 HUD"), cap,
                  tr("자동: 게임 연동 중엔 보이고, 화면 인식 중엔 숨김 (자기 글자를 게임 글자로 읽지 않도록)")))
        self.dps_cb = Toggle(s.dps_meter)
        self.dps_cb.toggled.connect(lambda on: self._set("dps_meter", on))
        g.add(row(tr("전투 중 초당 피해 창"), self.dps_cb, tr("볼별 초당 피해 (게임 전투 시간 기준, 게임 연동 1.2 이상)")))
        corners = ["right", "left"]
        dc = Segmented([tr("오른쪽 위"), tr("왼쪽 위")], corners.index(s.dps_corner) if s.dps_corner in corners else 0)
        dc.changed.connect(lambda i: self._set("dps_corner", corners[i]))
        g.add(row(tr("초당 피해 창 위치"), dc))
        self.compact_cb = Toggle(s.hud_compact)
        self.compact_cb.toggled.connect(lambda on: self._set("hud_compact", on))
        g.add(row(tr("HUD 간단히"), self.compact_cb, tr("추천·이유 한 줄·삭제만 (F7로 전환)")))

        pg.add(section_title(tr("실행")))
        g = pg.add(Group())
        self.mod_cb = Toggle(s.auto_install_mod)
        self.mod_cb.toggled.connect(lambda on: self._set("auto_install_mod", on))
        g.add(row(tr("게임 연동 자동 설치"), self.mod_cb, tr("없거나 옛 버전이면 게임이 꺼져 있을 때 설치 (프로젝트 안 설치 파일 사용)")))
        self.boot_cb = Toggle(s.start_with_windows)
        self.boot_cb.toggled.connect(lambda on: self._set("start_with_windows", on))
        g.add(row(tr("윈도우 시작 시 자동 실행"), self.boot_cb, tr("트레이로 조용히 시작해 게임을 기다립니다")))

        pg.add(section_title(tr("화면 인식 · 게임 연동이 없을 때만")))
        g = pg.add(Group())
        self.watch_cb = Toggle(s.watch_enabled)
        self.watch_cb.toggled.connect(lambda on: self._set("watch_enabled", on))
        g.add(row(tr("게임 화면 자동 감시"), self.watch_cb))
        self.interval = QSpinBox()
        self.interval.setRange(300, 3000)
        self.interval.setSingleStep(100)
        self.interval.setSuffix(" ms")
        self.interval.setValue(s.scan_interval_ms)
        self.interval.valueChanged.connect(lambda val: self._set("scan_interval_ms", int(val)))
        g.add(row(tr("감시 간격"), self.interval))

        pg.add(section_title(tr("단축키")))
        g = pg.add(Group())
        for key, text in (("F7", tr("HUD 간단히 / 자세히")), ("F8", tr("지금 다시 읽기")), ("F9", tr("HUD 숨기기 / 보이기")),
                          ("F10", tr("이 창 열기 / 닫기"))):
            g.add(row(text, value_label(key)))

        pg.add(section_title(tr("데이터")))
        g = pg.add(Group())
        src = self.data.source
        g.add(row(tr("이름·설명"), value_label(tr("게임 파일 · 빌드 {v0}", v0=src.get('steam_build_id', '?')))))
        g.add(row(tr("진화 레시피"), value_label(tr("게임 연동 시 게임 안 레시피"))))
        g.add(row(tr("추천 규칙"), value_label(f"rules.json {self.data.rules.get('version', '')}")))
        pg.add(_caption(tr("설정·로그 위치: {APP_DIR}", APP_DIR=APP_DIR)))
        pg.end()
        return pg

    def _set(self, key: str, value):
        setattr(self.settings, key, value)
        self.settings.save()
        self.settings_changed.emit()

    def _set_scale(self, value: float):
        self._set("font_scale", value)
        self.apply_scale(value)
