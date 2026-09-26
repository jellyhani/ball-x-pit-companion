"""현재 런의 보유 상태. 각 항목에 근거를 남기고, 모르는 값은 None 으로 둔다(0이나 빈 목록과 구분).

가장 권위 있는 근거는 강화 선택 화면 위쪽의 보유 칸(아이콘 + 레벨 숫자)이다. 선택창이 열릴 때마다
이것으로 보유 목록을 다시 맞춘다. 그 사이의 선택 결과(클릭 근거)는 다음 선택창 전까지의 임시 반영이다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..domain import Card, CardLabel, InventorySlot, PickOutcome
from ..gamedata import GameData
from ..i18n import tr

SOURCE_LABEL = {
    "screen": tr("게임 보유 칸"),
    "pick": tr("선택 확인(클릭)"),
    "card": tr("선택창 카드 표시"),
    "manual": tr("직접 수정"),
    "portrait": tr("초상화로 확인"),
    "game": tr("게임 연동"),
}


@dataclass
class Owned:
    item_id: str
    kind: str
    level: Optional[int]            # 여러 개면 가장 높은 레벨
    source: str
    updated_at: float = field(default_factory=time.time)
    copies: int = 1                 # 같은 볼을 따로 여러 개 가질 수 있다 (실제 화면 확인)
    at_max: Optional[bool] = None   # 게임이 알려 준 최대 레벨 여부 (게임 연동)
    combined: Tuple[str, ...] = ()  # 이 볼에 합쳐 넣은 볼들 — 그 볼들은 더 이상 따로 보유하지 않는다 (게임 연동)
    instances: Tuple[InventorySlot, ...] = ()  # 같은 대표 볼의 복사본도 슬롯별 융합 구성을 보존한다.

    @property
    def effect_ids(self) -> Set[str]:
        """현재 작동하는 효과. 진화 재료로 쓸 수 있는 별도 보유 칸과 구분한다."""
        return {self.item_id, *self.combined}


@dataclass
class RunState:
    run_seq: int = 0
    phase: str = "unknown"              # in_run | base | unknown
    joined_mid_run: bool = False
    characters: List[Tuple[str, str]] = field(default_factory=list)   # (캐릭터 ID, 근거)
    owned: Dict[str, Owned] = field(default_factory=dict)
    unreadable_slots: int = 0           # 채워져 있지만 무엇인지 읽지 못한 보유 칸
    inventory_seen: bool = False        # 이번 런에서 보유 칸을 한 번이라도 읽었는지
    pending_unknown_pick: bool = False  # 마지막 선택 결과를 아직 확인하지 못함
    history: List[str] = field(default_factory=list)
    offered: Dict[str, int] = field(default_factory=dict)   # 이번 런에 선택지로 나온 횟수 (삭제 판단용)
    damage: Dict[str, int] = field(default_factory=dict)    # 이번 런 항목별 피해 (게임 연동 통계)
    picks: List[str] = field(default_factory=list)          # 이번 런에 고른 항목 순서 (기록용)
    locked_target: Optional[str] = None   # 사용자가 직접 고른 목표 진화 결과 (없으면 자동 감지, deck_plan.py)
    _applied: Set[int] = field(default_factory=set)
    _last_offer: tuple = ()
    _offered_sessions: Set[int] = field(default_factory=set)

    # ---- 런 수명 ----
    def start_run(self):
        self.run_seq += 1
        self.phase = "in_run"
        self.joined_mid_run = False
        self._reset_items()
        self.characters = []
        self.history = [tr("새 런 시작")]

    def join_mid_run(self):
        self.run_seq += 1
        self.phase = "in_run"
        self.joined_mid_run = True
        self._reset_items()
        self.characters = []
        self.history = [tr("런 도중에 연결됨 — 다음 선택창에서 보유 칸을 읽습니다")]

    def end_run(self, reason: str):
        self.phase = "base"
        self._log(tr("런 종료 ({reason})", reason=reason))

    def _reset_items(self):
        self.owned.clear()
        self._applied.clear()
        self.unreadable_slots = 0
        self.inventory_seen = False
        self.pending_unknown_pick = False
        self.offered.clear()
        self._last_offer = ()
        self._offered_sessions.clear()
        self.damage.clear()
        self.picks.clear()
        self.locked_target = None

    # ---- 사용자가 직접 고른 덱 목표 ----
    def lock_target(self, item_id: str, data: GameData):
        self.locked_target = item_id
        self._log(tr("덱 목표 고정: {v0}", v0=data.name(item_id)))

    def clear_target(self, data: GameData):
        if self.locked_target is None:
            return
        self._log(tr("덱 목표 해제: {v0}", v0=data.name(self.locked_target)))
        self.locked_target = None

    # ---- 화면에서 읽은 권위 있는 정보 ----
    def apply_inventory(self, slots: Tuple[InventorySlot, ...], data: GameData) -> List[str]:
        """보유 칸 전체로 보유 목록을 다시 만든다. 읽지 못한 칸이 있으면 그만큼 기존 기록을 남긴다."""
        seen: Dict[str, Owned] = {}
        unreadable = 0
        for s in slots:
            if not s.occupied:
                continue
            if s.item_id is None:
                unreadable += 1
                continue
            prev = self.owned.get(s.item_id)
            level = s.level if s.level is not None else (prev.level if prev else None)
            if s.item_id in seen:
                o = seen[s.item_id]
                o.instances += (s,)
                o.copies += 1
                if level is not None and (o.level is None or level > o.level):
                    o.level = level
                if s.at_max:
                    o.at_max = True
                if s.combined:
                    o.combined = tuple(dict.fromkeys(o.combined + s.combined))
            else:
                seen[s.item_id] = Owned(s.item_id, data.items[s.item_id].kind, level, "screen", at_max=s.at_max,
                                        combined=s.combined, instances=(s,))
        notes = []
        added = [i for i in seen if i not in self.owned]
        removed = [i for i in self.owned if i not in seen]
        if unreadable:
            # 읽지 못한 칸 수만큼은 이전 기록(수동 수정 우선)을 유지한다
            keep = sorted((self.owned[i] for i in removed), key=lambda o: o.source != "manual")[:unreadable]
            for o in keep:
                seen[o.item_id] = o
            removed = [i for i in removed if i not in seen]
        for i in added:
            notes.append(tr("보유 확인: {v0}", v0=data.name(i)))
        for i in removed:
            notes.append(tr("보유 칸에 없음: {v0}", v0=data.name(i)))
        self.owned = seen
        self.unreadable_slots = unreadable
        self.inventory_seen = True
        self.pending_unknown_pick = False
        for n in notes:
            self._log(n)
        return notes

    def apply_character(self, char_id: Optional[str], extras: Tuple[str, ...] = (), source: str = "portrait"):
        """캐릭터(와 함께 쓰는 캐릭터). 직접 지정한 값은 덮어쓰지 않는다."""
        if not char_id:
            return
        if any(src == "manual" for _, src in self.characters):
            return
        ids = [char_id] + [c for c in extras if c != char_id]
        if self.character_ids != ids:
            self.characters = [(c, source) for c in ids]

    def note_offered(self, item_ids: Tuple[Optional[str], ...], session_id: Optional[int] = None):
        """새 선택 세션마다 센다. 식별자가 없는 옛 호출만 카드 묶음으로 중복을 막는다."""
        key = tuple(sorted(i for i in item_ids if i))
        if not key:
            return
        if session_id is not None:
            if session_id in self._offered_sessions:
                return
            self._offered_sessions.add(session_id)
        elif key == self._last_offer:
            return
        self._last_offer = key
        for i in key:
            self.offered[i] = self.offered.get(i, 0) + 1

    # ---- 선택 결과 (다음 선택창에서 보유 칸으로 다시 확인된다) ----
    def apply_outcome(self, outcome: PickOutcome, data: GameData) -> bool:
        if outcome.session_id in self._applied:
            return False
        self._applied.add(outcome.session_id)
        if outcome.kind == "picked" and outcome.card is not None:
            card = outcome.card
            if card.item_id is None:
                self.pending_unknown_pick = True
                self._log(tr("{position} 카드를 골랐지만 무엇인지 읽지 못함", position=tr(card.position)))
                return True
            if data.items[card.item_id].kind != "pet":     # 펫 강화는 볼·패시브 칸에 들어가지 않는다
                self._apply_card(card, data, "pick")
            self.picks.append(card.item_id)
            self._log(tr("선택: {v0} ({evidence})", v0=data.name(card.item_id), evidence=outcome.evidence))
        elif outcome.kind == "unknown":
            self.pending_unknown_pick = True
            self._log(tr("선택 결과 확인 못 함 — 다음 선택창의 보유 칸으로 맞춥니다"))
        elif outcome.kind == "rerolled":
            self._log(tr("새로고침"))
        elif outcome.kind == "skipped":
            self._log(tr("넘기기"))
        elif outcome.kind == "banished":
            self._log(tr("삭제 사용"))
        return True

    def _apply_card(self, card: Card, data: GameData, source: str):
        item = data.items[card.item_id]
        prev = self.owned.get(card.item_id)
        if card.label is CardLabel.NEW:
            if prev is not None:   # 같은 볼의 새 복사본
                prev.copies += 1
                prev.instances = ()  # 새 복사본의 실제 슬롯은 다음 게임 보유 목록에서 확인한다.
                prev.source, prev.updated_at = source, time.time()
                return
            level = 1
        elif card.label is CardLabel.UPGRADE and card.shown_level:
            level = card.shown_level   # '레벨 N' 은 고른 뒤의 레벨
        elif prev and prev.level:
            level = prev.level + 1
        else:
            level = None
        self.owned[card.item_id] = Owned(card.item_id, item.kind, level, source,
                                        copies=prev.copies if prev else 1,
                                        combined=prev.combined if prev else ())

    # ---- 카드 표시로 보유 여부 보정 (보유 칸을 못 읽었을 때의 보조 근거) ----
    def reconcile_cards(self, cards: Tuple[Card, ...], data: GameData) -> List[str]:
        notes: List[str] = []
        for c in cards:
            if not c.item_id or c.label is None:
                continue
            cur = self.owned.get(c.item_id)
            if c.label is CardLabel.UPGRADE and cur is None:
                before = c.shown_level - 1 if c.shown_level else None
                self.owned[c.item_id] = Owned(c.item_id, data.items[c.item_id].kind, before, "card")
                notes.append(tr("{v0} 보유 (강화 카드로 확인)", v0=data.name(c.item_id)))
        for n in notes:
            self._log(n)
        return notes

    # ---- 수동 보정 (인식 오류 복구용) ----
    def set_character(self, char_id: Optional[str]):
        self.characters = [(char_id, "manual")] if char_id else []

    def set_owned(self, item_id: str, kind: str, level: Optional[int]):
        prev = self.owned.get(item_id)
        self.owned[item_id] = Owned(item_id, kind, level, "manual", copies=prev.copies if prev else 1,
                                    combined=prev.combined if prev else ())

    def remove_owned(self, item_id: str):
        self.owned.pop(item_id, None)

    # ---- 조회 ----
    @property
    def character_ids(self) -> List[str]:
        return [c for c, _ in self.characters if c]

    @property
    def effect_ids(self) -> Set[str]:
        return {i for o in self.owned.values() for i in o.effect_ids}

    def damage_share(self, item_id: str) -> Optional[float]:
        """이번 런 볼 피해 중 이 볼의 비율. 피해 기록이 충분하지 않으면 None."""
        balls = {i: v for i, v in self.damage.items() if i.startswith("ball:")}
        total = sum(balls.values())
        if total < 2000 or item_id not in balls:
            return None
        return balls[item_id] / total

    def damage_rank(self, item_id: str) -> Optional[int]:
        balls = sorted(((v, i) for i, v in self.damage.items() if i.startswith("ball:")), reverse=True)
        ids = [i for _, i in balls]
        return ids.index(item_id) + 1 if item_id in ids else None

    @property
    def owned_complete(self) -> bool:
        """보유 목록을 믿고 '없음'을 판단해도 되는지."""
        return (self.phase == "in_run" and self.inventory_seen and self.unreadable_slots == 0
                and not self.pending_unknown_pick)

    def limitations(self, data: GameData) -> List[str]:
        out = []
        if self.phase != "in_run":
            out.append(tr("진행 중인 런 없음"))
        if not self.characters:
            out.append(tr("캐릭터 미확인"))
        if not self.inventory_seen:
            out.append(tr("보유 칸을 아직 읽지 못함"))
        elif self.unreadable_slots:
            out.append(tr("보유 칸 {unreadable_slots}개를 읽지 못함", unreadable_slots=self.unreadable_slots))
        if self.pending_unknown_pick:
            out.append(tr("직전 선택 결과 미확인"))
        return out

    def _log(self, text: str):
        self.history.append(text)
        del self.history[:-40]
