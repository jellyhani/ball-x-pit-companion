"""게임 연동 meta 메시지(기지·누적 기록)를 앱 형식으로 바꾼다.

- 자원 순서는 게임 ResourceType: 골드, 밀, 나무, 돌.
- 볼·패시브 누적 기록은 게임 세이브의 HeroMetaStats / PassiveMetaStats (배열 위치 = 게임 내부 종류 번호).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..gamedata import GameData
from ..i18n import tr

RESOURCES = (tr("골드"), tr("밀"), tr("나무"), tr("돌"))


@dataclass(frozen=True)
class BuildingState:
    type: str  # 게임 내부 이름 (kExorcist)
    level: int
    state: str
    can_upgrade: Optional[bool] = None
    upgrade_cost: Tuple[int, ...] = ()


@dataclass(frozen=True)
class Blueprint:
    type: str
    slug: str
    category: str  # kEconomy | kWarfare | kHousing
    cost: Tuple[int, ...] = ()
    size: Optional[Tuple[int, int]] = None  # 타일 크기 (플러그인 1.9)
    can_build_more: Optional[bool] = None
    max_instances: Optional[int] = None
    range_boxes: Optional[tuple] = None

    def construction_data(self) -> dict:
        return {
            "type": self.type,
            "size": self.size,
            "cost": self.cost,
            "can_build_more": self.can_build_more,
            "max_instances": self.max_instances,
            "range_boxes": self.range_boxes,
            "range_rotation": 0,
        }


@dataclass(frozen=True)
class ItemRecord:
    """이 항목에 대한 내 누적 기록 (게임 세이브)."""

    obtained: int
    completed: int  # 이 항목을 가진 채 레벨을 완료한 런 수
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
    build_options: Optional[List[Blueprint]] = None  # 1.14: 이미 지은 반복 건설형까지, None 은 옛 연동
    records: Dict[str, ItemRecord] = field(default_factory=dict)  # 항목 ID → 누적 기록
    bonuses: Dict[str, object] = field(default_factory=dict)
    chars: List[Tuple[str, int]] = field(default_factory=list)  # (캐릭터 ID, 레벨)
    levels: List[dict] = field(default_factory=list)
    battles: Optional[int] = None
    chars_raw: List[dict] = field(default_factory=list)  # 게임 연동 원본 (작업 상태·채집 강화)
    discovery: Optional[Dict[str, dict]] = (
        None  # 게임의 발견 횟수·현재 등장 가능 여부·융합 기록. None은 미수신.
    )

    def resource_text(self, cost: Tuple[int, ...]) -> str:
        return " · ".join(
            f"{RESOURCES[index]} {value}"
            for index, value in enumerate(cost)
            if value and index < len(RESOURCES)
        )

    def affordable(self, cost: Tuple[int, ...]) -> bool:
        return bool(cost) and all(
            index < len(self.resources) and self.resources[index] >= value for index, value in enumerate(cost)
        )

    def shortfall(self, cost: Tuple[int, ...]) -> Dict[str, int]:
        return {
            RESOURCES[index]: value - (self.resources[index] if index < len(self.resources) else 0)
            for index, value in enumerate(cost)
            if index < len(RESOURCES)
            and value > (self.resources[index] if index < len(self.resources) else 0)
        }

    def completion_rank(self, item_id: str, min_runs: int = 3) -> Optional[Tuple[int, int, float]]:
        """같은 종류(볼/패시브) 중 '가진 런의 레벨 완료율' 순위 (순위, 비교 수, 완료율). 표본이 적으면 None."""
        kind = item_id.split(":")[0] + ":"
        rows = [
            (index, record.completion_rate)
            for index, record in self.records.items()
            if index.startswith(kind) and record.obtained >= min_runs and record.completion_rate is not None
        ]
        rates = dict(rows)
        if item_id not in rates or len(rows) < 5:
            return None
        rows.sort(key=lambda t: -t[1])
        return [index for index, _ in rows].index(item_id) + 1, len(rows), rates[item_id]

    def damage_rank(self, item_id: str, min_runs: int = 3) -> Optional[Tuple[int, int]]:
        """내 기록에서 이 볼의 런당 평균 피해 순위 (순위, 비교한 볼 수). 표본이 적으면 None."""
        rows = [
            (index, record.damage_per_run)
            for index, record in self.records.items()
            if index.startswith("ball:") and record.obtained >= min_runs and record.damage_per_run is not None
        ]
        if item_id not in dict(rows) or len(rows) < 5:
            return None
        rows.sort(key=lambda t: -t[1])
        return [index for index, _ in rows].index(item_id) + 1, len(rows)


def _ints(v) -> Tuple[int, ...]:
    return (
        tuple(int(x) for x in v)
        if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)
        else ()
    )


def parse_meta(meta: dict, data: GameData) -> MetaState:
    state = MetaState(
        resources=_ints(meta.get("resources")),
        battles=meta.get("battles"),
        bonuses=dict(meta.get("bonuses") or {}),
        levels=list(meta.get("levels") or []),
    )
    if isinstance(meta.get("discovery"), dict):
        state.discovery = {}
        for enum_name, row in meta["discovery"].items():
            item_id = data.item_by_log_id(enum_name)
            if not item_id or not isinstance(row, dict) or type(row.get("obtained")) is not int:
                continue
            entry = dict(row)
            if isinstance(row.get("combos"), dict):
                entry["combos"] = {
                    partner: count
                    for name, count in row["combos"].items()
                    if (partner := data.item_by_log_id(name)) and type(count) is int and count > 0
                }
            state.discovery[item_id] = entry
    for building in meta.get("buildings") or []:
        state.buildings.append(
            BuildingState(
                building.get("type", ""),
                int(building.get("lvl") or 0),
                building.get("state", ""),
                building.get("can_upgrade"),
                _ints(building.get("upgrade_cost")),
            )
        )
    for building in meta.get("blueprints") or []:
        size = (
            (int(building["tw"]), int(building["th"]))
            if isinstance(building.get("tw"), int) and isinstance(building.get("th"), int)
            else None
        )
        state.blueprints.append(
            Blueprint(
                building.get("type", ""),
                building.get("slug", ""),
                building.get("cat", ""),
                _ints(building.get("cost")),
                size,
            )
        )
    if isinstance(meta.get("build_options"), list):
        state.build_options = []
        for building in meta["build_options"]:
            if not isinstance(building, dict) or building.get("can_build_more") is not True:
                continue
            size = (
                (building["tw"], building["th"])
                if all(isinstance(building.get(k), int) and building[k] > 0 for k in ("tw", "th"))
                else None
            )
            from ..engine.game_range import boxes_from_row

            limit = building.get("max_instances")
            limit = limit if type(limit) is int and limit >= 0 else None
            state.build_options.append(
                Blueprint(
                    building.get("type", ""),
                    building.get("slug", ""),
                    building.get("cat", ""),
                    _ints(building.get("cost")),
                    size,
                    True,
                    limit,
                    boxes_from_row(building),
                )
            )
    for key, kind in (("ball_stats", "ball"), ("passive_stats", "passive")):
        for enum_name, record in (meta.get(key) or {}).items():
            item_id = data.item_by_log_id(enum_name)
            if not item_id or not isinstance(record, dict):
                continue
            state.records[item_id] = ItemRecord(
                int(record.get("obtained") or 0),
                int(record.get("completed") or 0),
                int(record.get("rejected") or 0),
                record.get("damage"),
                record.get("launches"),
            )
    state.chars_raw = [character for character in meta.get("chars") or [] if isinstance(character, dict)]
    for character in meta.get("chars") or []:
        name = character.get("type") or ""
        character_id = f"char:{name[1:].lower()}" if name.startswith("k") else None
        if character_id in data.characters:
            state.chars.append((character_id, int(character.get("lvl") or 0)))
    return state
