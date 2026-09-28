"""기지 조언: 설계도는 있는데 아직 안 지은 건물과 올릴 수 있는 건물 중 무엇부터 할지.

근거: 게임 연동 meta(보유 자원, 건물·설계도, 짓는/올리는 비용), 건물 설명(게임 파일 원문).
순서: 진행 중인 공사 완성 → 대상이 있는 공략 핵심 신규 건물 → 일반 게임 후보 → 공략 후순위.
     핵심 그룹 안에 고정 건설 순서는 없고, 같은 그룹은 구매 가능 여부와 부족 비용으로 정렬한다.
     공략은 완성형 기지 설계이므로 개별 건물의 효과 크기나 모든 상황의 건설 순위를 만들어 내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..gamedata import GameData
from ..i18n import tr
from ..tracking.meta_state import MetaState
from .construction_policy import (
    PRIORITY_GROUP_ORDER,
    RESOURCE_TILE_TYPES,
    RESOURCE_VARIANTS,
    construction_guidance,
    has_captain_targets,
    is_recommended_building,
    preferred_tile_types,
)

CAT_LABEL = {"kWarfare": tr("전투"), "kEconomy": tr("경제"), "kHousing": tr("주거")}


@dataclass
class BaseSuggestion:
    kind: str  # finish | build | upgrade
    type: str  # 게임 내부 이름
    name: str
    desc: str
    cost_text: str
    affordable: bool
    missing_text: str
    run_impact: bool  # 이전 소비자와의 호환용. 설명 키워드로 효율을 추정하거나 정렬하지 않는다.
    category: str
    shortfall: int = 0
    cost: Tuple[int, ...] = ()
    priority_group: str = "neutral"
    reason: str = ""
    source: str = ""

    @property
    def status(self) -> str:
        return (
            tr("지금 가능") if self.affordable else tr("부족: {missing_text}", missing_text=self.missing_text)
        )

    @property
    def policy_label(self) -> str:
        return {
            "finish": tr("먼저 완성"),
            "guide_core": tr("공략 핵심"),
            "neutral": tr("일반 후보"),
            "guide_later": tr("공략 후순위"),
        }[self.priority_group]


def _desc(data: GameData, type_name: str) -> str:
    key = type_name[1:].lower() if type_name.startswith("k") else type_name.lower()
    # 설명의 수치 자리(건물 레벨마다 다름)는 N 으로 보여 준다
    return data.buildings.get(key, {}).get("desc_ko", "").replace("?", "N")


FINISH_LABEL = {"kScaffold": tr("공사 중"), "kUpgrading": tr("강화 공사 중")}


def suggest(
    meta: MetaState, data: GameData, limit: int = 10, *, base: Optional[dict] = None
) -> List[BaseSuggestion]:
    from .layout_opt import STAT_FALLBACK, STATUE_TYPES, housing_types

    result: List[BaseSuggestion] = []
    available = meta.build_options if meta.build_options is not None else meta.blueprints
    preferred_tiles = preferred_tile_types((b.type for b in available), (b.type for b in meta.buildings))
    available_by_type = {b.type: b for b in available}
    known = set(available_by_type) | {b.type for b in meta.buildings}
    superseded = {basic for advanced, basic in RESOURCE_VARIANTS if advanced in known}
    housing = housing_types()
    has_housing = any(
        (b.type[1:] if b.type.startswith("k") else b.type).lower() in housing and b.type != "kVeteranHut"
        for b in meta.buildings
    )
    owned_types = {b.type for b in meta.buildings}
    has_stats = has_captain_targets(owned_types, base, STAT_FALLBACK)
    has_infinite_stats = bool(owned_types & STATUE_TYPES)
    has_construction = any(
        b.state in FINISH_LABEL and is_recommended_building(b.type) for b in meta.buildings
    )
    for b in meta.buildings:
        if is_recommended_building(b.type) and b.state in FINISH_LABEL:
            result.append(
                BaseSuggestion(
                    "finish",
                    b.type,
                    tr("{v0} 완성", v0=data.building_name(b.type)),
                    _desc(data, b.type),
                    tr("{v0} · 채집 때 작업자를 이 건물에 맞히면 공사가 진행됨", v0=FINISH_LABEL[b.state]),
                    True,
                    "",
                    True,
                    "",
                    0,
                )
            )
    for bp in meta.blueprints:
        if meta.build_options is not None:
            if bp.type not in available_by_type:
                continue
            bp = available_by_type[bp.type]
        if not is_recommended_building(bp.type):
            continue
        if bp.type in RESOURCE_TILE_TYPES and bp.type not in preferred_tiles:
            continue
        desc = _desc(data, bp.type)
        short = meta.shortfall(bp.cost)
        result.append(
            BaseSuggestion(
                "build",
                bp.type,
                data.building_name(bp.type),
                desc,
                meta.resource_text(bp.cost),
                meta.affordable(bp.cost),
                " · ".join(f"{field_name} {value}" for field_name, value in short.items()),
                False,
                bp.category,
                sum(short.values()),
                bp.cost,
            )
        )
    for b in meta.buildings:
        if (
            not is_recommended_building(b.type)
            or b.type in superseded
            or not b.can_upgrade
            or not b.upgrade_cost
        ):
            continue
        desc = _desc(data, b.type)
        short = meta.shortfall(b.upgrade_cost)
        result.append(
            BaseSuggestion(
                "upgrade",
                b.type,
                tr("{v0} 강화", v0=data.building_name(b.type)),
                desc,
                meta.resource_text(b.upgrade_cost),
                meta.affordable(b.upgrade_cost),
                " · ".join(f"{field_name} {value}" for field_name, value in short.items()),
                False,
                "",
                sum(short.values()),
                b.upgrade_cost,
            )
        )

    for suggestion in result:
        suggestion.priority_group, suggestion.reason, suggestion.source = construction_guidance(
            suggestion.type,
            suggestion.kind,
            has_housing=has_housing,
            has_stats=has_stats,
            has_infinite_stats=has_infinite_stats,
            has_construction=has_construction,
        )
    result.sort(key=lambda s: (PRIORITY_GROUP_ORDER[s.priority_group], not s.affordable, s.shortfall, s.name))
    # 조언에는 개별 건물 식별자가 없다. 같은 작업·종류·비용은 한 행으로 보여 다른 후보가 밀리지 않게 한다.
    # 실제 공사 완성 항목과 서로 다른 강화 비용은 그대로 보존한다.
    unique, seen = [], set()
    for suggestion in result:
        key = (suggestion.kind, suggestion.type, suggestion.cost, suggestion.priority_group)
        if suggestion.kind != "finish" and key in seen:
            continue
        seen.add(key)
        unique.append(suggestion)
    return unique[:limit]
