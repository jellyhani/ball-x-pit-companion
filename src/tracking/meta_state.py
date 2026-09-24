"""게임 연동 meta 메시지(기지·누적 기록)를 앱 형식으로 바꾼다.

- 자원 순서는 게임 ResourceType: 골드, 밀, 나무, 돌.
- 볼·패시브 누적 기록은 게임 세이브의 HeroMetaStats / PassiveMetaStats (배열 위치 = 게임 내부 종류 번호).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..gamedata import GameData

RESOURCES = ("골드", "밀", "나무", "돌")


@dataclass(frozen=True)
class BuildingState:
    type: str                 # 게임 내부 이름 (kExorcist)
    level: int
    state: str
    can_upgrade: Optional[bool] = None
    upgrade_cost: Tuple[int, ...] = ()


@dataclass(frozen=True)
class Blueprint:
    type: str
    slug: str
    category: str             # kEconomy | kWarfare | kHousing
    cost: Tuple[int, ...] = ()
    size: Optional[Tuple[int, int]] = None      # 타일 크기 (플러그인 1.9)


@dataclass(frozen=True)
class ItemRecord:
    """이 항목에 대한 내 누적 기록 (게임 세이브)."""
    obtained: int
    completed: int            # 이 항목을 가진 채 레벨을 완료한 런 수
    rejected: int = 0
    damage: Optional[int] = None
    launches: Optional[int] = None

    @property
    def damage_per_run(self) -> Optional[float]:
        return self.damage / self.obtained if self.damage is not None and self.obtained else None

    @property
    def completion_rate(self) -> Optional[float]:
        return self.completed / self.obtained if self.obtained else None


@dataclass
class MetaState:
    resources: Tuple[int, ...] = ()
    buildings: List[BuildingState] = field(default_factory=list)
    blueprints: List[Blueprint] = field(default_factory=list)
    records: Dict[str, ItemRecord] = field(default_factory=dict)       # 항목 ID → 누적 기록
    bonuses: Dict[str, object] = field(default_factory=dict)
    chars: List[Tuple[str, int]] = field(default_factory=list)          # (캐릭터 ID, 레벨)
    levels: List[dict] = field(default_factory=list)
    battles: Optional[int] = None
    chars_raw: List[dict] = field(default_factory=list)     # 게임 연동 원본 (작업 상태·채집 강화)

    def resource_text(self, cost: Tuple[int, ...]) -> str:
        return " · ".join(f"{RESOURCES[i]} {v}" for i, v in enumerate(cost) if v and i < len(RESOURCES))

    def affordable(self, cost: Tuple[int, ...]) -> bool:
        return bool(cost) and all(i < len(self.resources) and self.resources[i] >= v for i, v in enumerate(cost))

    def shortfall(self, cost: Tuple[int, ...]) -> Dict[str, int]:
        return {RESOURCES[i]: v - (self.resources[i] if i < len(self.resources) else 0)
                for i, v in enumerate(cost) if i < len(RESOURCES) and v > (self.resources[i] if i < len(self.resources) else 0)}

    def completion_rank(self, item_id: str, min_runs: int = 3) -> Optional[Tuple[int, int, float]]:
        """같은 종류(볼/패시브) 중 '가진 런의 레벨 완료율' 순위 (순위, 비교 수, 완료율). 표본이 적으면 None."""
        kind = item_id.split(":")[0] + ":"
        rows = [(i, r.completion_rate) for i, r in self.records.items()
                if i.startswith(kind) and r.obtained >= min_runs and r.completion_rate is not None]
        rates = dict(rows)
        if item_id not in rates or len(rows) < 5:
            return None
        rows.sort(key=lambda t: -t[1])
        return [i for i, _ in rows].index(item_id) + 1, len(rows), rates[item_id]

    def damage_rank(self, item_id: str, min_runs: int = 3) -> Optional[Tuple[int, int]]:
        """내 기록에서 이 볼의 런당 평균 피해 순위 (순위, 비교한 볼 수). 표본이 적으면 None."""
        rows = [(i, r.damage_per_run) for i, r in self.records.items()
                if i.startswith("ball:") and r.obtained >= min_runs and r.damage_per_run is not None]
        if item_id not in dict(rows) or len(rows) < 5:
            return None
        rows.sort(key=lambda t: -t[1])
        return [i for i, _ in rows].index(item_id) + 1, len(rows)


def _ints(v) -> Tuple[int, ...]:
    return tuple(int(x) for x in v) if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v) else ()


def parse_meta(meta: dict, data: GameData) -> MetaState:
    st = MetaState(resources=_ints(meta.get("resources")), battles=meta.get("battles"),
                   bonuses=dict(meta.get("bonuses") or {}), levels=list(meta.get("levels") or []))
    for b in meta.get("buildings") or []:
        st.buildings.append(BuildingState(b.get("type", ""), int(b.get("lvl") or 0), b.get("state", ""),
                                          b.get("can_upgrade"), _ints(b.get("upgrade_cost"))))
    for b in meta.get("blueprints") or []:
        size = (int(b["tw"]), int(b["th"])) if isinstance(b.get("tw"), int) and isinstance(b.get("th"), int) else None
        st.blueprints.append(Blueprint(b.get("type", ""), b.get("slug", ""), b.get("cat", ""), _ints(b.get("cost")), size))
    for key, kind in (("ball_stats", "ball"), ("passive_stats", "passive")):
        for enum_name, r in (meta.get(key) or {}).items():
            iid = data.item_by_log_id(enum_name)
            if not iid or not isinstance(r, dict):
                continue
            st.records[iid] = ItemRecord(int(r.get("obtained") or 0), int(r.get("completed") or 0),
                                         int(r.get("rejected") or 0), r.get("damage"), r.get("launches"))
    st.chars_raw = [c for c in meta.get("chars") or [] if isinstance(c, dict)]
    for c in meta.get("chars") or []:
        name = c.get("type") or ""
        cid = f"char:{name[1:].lower()}" if name.startswith("k") else None
        if cid in data.characters:
            st.chars.append((cid, int(c.get("lvl") or 0)))
    return st
