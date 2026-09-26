"""사용자 선택과 Steam 완성형 기지 공략을 구분한 건설·강화·일꾼 추천 정책."""
from __future__ import annotations

from ..i18n import tr


GUIDE_SOURCES = {
    "Zarcos": "https://steamcommunity.com/sharedfiles/filedetails/?id=3602389407",
    "Drake": "https://steamcommunity.com/sharedfiles/filedetails/?id=3610566537",
}

# 사용자 결정: 금광은 철거 예정이고 무한 모드에서 골드가 충분하다.
# 실제 게임 건물·충돌·보유 자원은 유지하고, 추가 투자를 권하는 후보만 거른다.
USER_EXCLUDED_BUILDINGS = frozenset({"kGoldMine"})
# Drake의 Farms / Lumbermills / Stone Mines 절: Gatherer Huts와 War Room을 사용하지 않는 설계.
# Zarcos의 War Room 절도 제외를 권한다. 게임에서 불가능한 건물이 아니라 채택한 공략의 선택이다.
GUIDE_EXCLUDED_BUILDINGS = frozenset({"kIdleLauncher", "kWarRoom"})
EXCLUDED_BUILDINGS = USER_EXCLUDED_BUILDINGS | GUIDE_EXCLUDED_BUILDINGS

# 완성형 공략의 핵심을 한 그룹으로만 구분한다. 이 숫자는 효율 점수나 건물별 건설 순위가 아니다.
PRIORITY_GROUP_ORDER = {"finish": 0, "guide_core": 1, "neutral": 2, "guide_later": 3}

# 게임 resources.assets 설명은 고급 3종의 재생 속도·용량이 높다고 명시한다.
# 비용 대비 생산량 수치는 추정하지 않고, 실제 추가 건설 목록에 있는 상위형을 우선한다.
RESOURCE_VARIANTS = (("kDenseWheat", "kWheatField"), ("kGrandTree", "kForest"), ("kGraniteSlab", "kBoulder"))
RESOURCE_TILE_TYPES = frozenset(t for family in RESOURCE_VARIANTS for t in family)


def is_recommended_building(type_name: str) -> bool:
    """건설·강화·공사 완성 또는 일꾼 배정을 권할 수 있는 건물인지."""
    return type_name not in EXCLUDED_BUILDINGS


def construction_guidance(type_name: str, action: str, *, has_housing: bool = False,
                          has_stats: bool = False, has_infinite_stats: bool = False,
                          has_construction: bool = False) -> tuple:
    """(우선 그룹, 공략 이유, 출처). 대상 보유만 확인하며 효과 발동·배치 이득은 단정하지 않는다."""
    if action == "finish":
        return "finish", "", ""
    if type_name == "kMansion":
        # Zarcos Mansion 절: 기지 설계에서 가장 나중에 우선할 대상으로 명시.
        return "guide_later", tr("대저택의 골드 수입은 공략에서 후순위입니다."), "Steam Zarcos"
    if action == "build":
        if type_name == "kVeteranHut" and has_housing:
            return ("guide_core", tr("거처를 범위에 모으는 공략의 중심 — 현재 거처 보유 확인, 실제 범위·입주민 레벨 확인 필요"),
                    "Steam Zarcos · Drake")
        if type_name == "kCaptainQuarters" and has_stats:
            return ("guide_core", tr("능력치 건물을 범위에 모으는 공략의 중심 — 현재 능력치 건물 보유 확인, 실제 범위 확인 필요"),
                    "Steam Zarcos · Drake")
        if type_name == "kBrickHouse" and (has_infinite_stats or has_construction):
            return ("guide_core", tr("미완성·무한 강화 건물의 공사를 돕는 공략의 중심 — 채집 때 맞힐 수 있는 배치 필요"),
                    "Steam Zarcos")
    return ("neutral", tr("게임에서 가능한 항목입니다. 공략이 이 항목의 우선순위를 정하지는 않았습니다."), "")


def has_captain_targets(owned_types, base=None, fallback=()) -> bool:
    """현재 보유 건물의 게임 능력치 값을 우선하고, 값이 없는 종류만 알려진 대상으로 보완한다."""
    owned = set(owned_types)
    checked = set()
    for building in (base or {}).get("buildings") or []:
        if not isinstance(building, dict) or building.get("type") not in owned:
            continue
        stat = building.get("stat")
        if not isinstance(stat, str) or not stat:
            continue
        checked.add(building["type"])
        # 열거형 끝 값·없음은 능력치가 아니다. 게임이 명시한 없음 값을 기본 목록으로 뒤집지 않는다.
        value = stat[1:] if stat.startswith("k") else stat
        if value not in {"None", "Invalid", "Count", "Max", "Num"}:
            return True
    return bool((owned - checked) & set(fallback))


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
