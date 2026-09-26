"""사용자가 사용하지 않기로 한 건물의 건설·강화·일꾼 추천 정책."""
from __future__ import annotations


# 사용자 결정: 금광은 철거 예정이고 무한 모드에서 골드가 충분하다.
# 실제 게임 건물·충돌·보유 자원은 유지하고, 추가 투자를 권하는 후보만 거른다.
EXCLUDED_BUILDINGS = frozenset({"kGoldMine"})

# 게임 resources.assets 설명은 고급 3종의 재생 속도·용량이 높다고 명시한다.
# 비용 대비 생산량 수치는 추정하지 않고, 실제 추가 건설 목록에 있는 상위형을 우선한다.
RESOURCE_VARIANTS = (("kDenseWheat", "kWheatField"), ("kGrandTree", "kForest"), ("kGraniteSlab", "kBoulder"))
RESOURCE_TILE_TYPES = frozenset(t for family in RESOURCE_VARIANTS for t in family)


def is_recommended_building(type_name: str) -> bool:
    """건설·강화·공사 완성 또는 일꾼 배정을 권할 수 있는 건물인지."""
    return type_name not in EXCLUDED_BUILDINGS


def preferred_tile_types(available, owned=()) -> set:
    """건설 가능한 상위형 우선. 상위형 보유 사실만 있고 목록이 누락됐으면 하위형을 대신 권하지 않는다."""
    available, owned = set(available), set(owned)
    chosen = set()
    for advanced, basic in RESOURCE_VARIANTS:
        if advanced in available:
            chosen.add(advanced)
        elif advanced not in owned and basic in available:
            chosen.add(basic)
    return chosen
