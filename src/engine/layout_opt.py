"""현재 기지에서 적은 이동으로 공사 접근성과 건물 효과를 개선하는 재배치 추천.

건물 효과 (게임 건물 설명·강화 설명 원문, data/game_text_ko.json):
  농장·야적장·채석장     배정된 캐릭터가 '인근' 밭·숲·바위에서 주기적으로 채집
  외딴 집·아늑한 집·극장  '근처' 밭·숲·바위에서 주기적으로 채집 (거처 강화 효과)
  별장·야영지·바위 언덕   '근처' 밀밭·숲·바위 재생 속도 상승 (거처 강화 효과)
  대저택                '근처' 건물마다 분당 골드
  대위 막사              '인근' 능력치 보너스 건물 +1
  잔병의 오두막           '인근' 거처 입주민 추가 경험치
  금광·수도원·유령의 집    작업자가 튕길 때 효과 → 채집 궤적 계산으로 평가
'근처'는 게임이 건물마다 주는 범위(GetRange) 안에 중심이 들어오는 것으로 본다 (추정).
겹침 규칙(추정): 재생 속도 상승은 같은 타일에 겹쳐도 한 번, 채집 건물은 같은 타일을 나눠 쓰므로 두 번째는 절반.
강화 전 거처·일꾼이 없는 생산 건물은 효과를 절반으로 계산한다 (나중에 켜질 효과).

탐색: 타일 격자 위의 '같은 크기 두 영역 맞바꾸기'(건물 맞바꾸기·빈 자리로 옮기기·작은 타일 묶음 교환을 모두 포함)로
담금질(simulated annealing). 충돌 모양(ㄱ자 건물) 기준으로 겹침을 막는다. 공사 중인 건물도 옮긴다(커뮤니티 공략: 채집 구역 가장자리로).
가이드 배치는 입구 비움·공사 도달·공사 거리 순서로 비교하고, 같은 우선순위에서 범위 효과·채집·이동 수를 비교한다.
후보의 물리 검증은 별도로 수행하며 기존 허브 범위 효과와 가동 생산 구역을 보존한다.
"""

from __future__ import annotations

import functools
import json
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .layout import Bld, Grid, Move, MoveSequence, buildings_from_base, grid_from_geo, shape_masks
from ..i18n import tr

WHEAT_T = {"kWheatField", "kDenseWheat"}
WOOD_T = {"kForest", "kGrandTree"}
STONE_T = {"kBoulder", "kGraniteSlab"}
TILE_RES = {
    **{building_type: 1 for building_type in WHEAT_T},
    **{building_type: 2 for building_type in WOOD_T},
    **{building_type: 3 for building_type in STONE_T},
}
# 능력치 보너스 건물 12개 (대위 막사 대상 — Steam 가이드 '100% Utilization': '12개 능력치 건물을 모두 범위 안에').
# +1 능력치 6개(병영·의료원·영사관·총대장간·사택·구두장이의 집) + 무한 강화 6개. 성장률(스케일링)만 올리는 연금술 공방·
# 양궁장·외교 회관·군사 학교·대학·바퀴 공방은 제외. 플러그인 1.8 의 게임 값(stat)이 있으면 그걸 쓴다.
STAT_FALLBACK = {
    "kBarracks",
    "kClinic",
    "kConsulate",
    "kGunsmith",
    "kSchoolhouse",
    "kShoemaker",
    "kEnduranceStatue",
    "kStrengthStatue",
    "kLeadershipStatue",
    "kSpeedStatue",
    "kDexterityStatue",
    "kIntelligenceStatue",
}
# 건물 → (대상, 가중치, 겹침 방식, 켜지는 조건, 설명)
#   대상: 1 밀 / 2 나무 / 3 돌 타일, "all" 모든 건물, "stat" 능력치 건물, "housing" 거처, "build" 공사 중·무한 강화 건물
#   겹침: regen(타일당 한 번) · harvest(첫 건물 1, 둘째 0.5) · count(건물마다 따로)
# 가중치 = 범위 안 자원 타일 하나의 값 (농장 옆 밀밭 1칸 = 1.0 기준, 타일 용량을 곱함).
# 생산 건물은 '붙어 있는 타일마다' 주기적으로 1씩 캐므로 칸 수에 비례 (상한 없음):
#   농장 밀 1 / 6분 · 야적장 나무 1 / 9분 · 채석장 돌 1 / 10분 (위키 건물표, BallxPitxApp docs/baseBuilder.study.md 에 정리된 값)
#   → 1.0 · 0.67 · 0.6.
# 거처 자동 채집은 같은 자원의 생산 건물 대비 비율 (Steam 가이드 'My Optimized Town Layout' 측정 분당 값):
#   외딴 집 6 / 농장 24 → 0.25 · 아늑한 집 7.7 / 야적장 13.7 × 0.67 → 0.37 · 극장 6 / 채석장 4.5 × 0.6 → 0.8.
# 재생 속도 상승량은 찾지 못해 0.3 (추정). 대저택·대위 막사·잔병의 오두막은 건물 수로 센다.
HARVEST_CAP = 99  # 칸 수 상한 없음 (타일마다 캐는 방식 — 위키)
TILE_CAPACITY = {"kDenseWheat": 4, "kGrandTree": 3, "kGraniteSlab": 3}  # 고급 타일 용량 (기본 1, 위키)
EFFECTS: Dict[str, Tuple[object, float, str, str, str]] = {
    "kIdleFarm": (1, 1.0, "harvest", "worker", tr("붙어 있는 밀밭마다 6분에 밀 1 (일꾼 배정)")),
    "kIdleLumberyard": (2, 0.67, "harvest", "worker", tr("붙어 있는 숲마다 9분에 나무 1 (일꾼 배정)")),
    "kIdleStoneMine": (3, 0.6, "harvest", "worker", tr("붙어 있는 바위마다 10분에 돌 1 (일꾼 배정)")),
    "kSingleFamilyHome": (1, 0.25, "harvest", "upgraded", tr("근처 밭에서 주기적으로 채집 (강화 효과)")),
    "kCozyHome": (2, 0.37, "harvest", "upgraded", tr("근처 숲에서 주기적으로 채집 (강화 효과)")),
    "kHovel": (3, 0.8, "harvest", "upgraded", tr("근처 바위에서 주기적으로 채집 (강화 효과)")),
    "kVilla": (1, 0.3, "regen", "upgraded", tr("근처 밀밭 재생 속도 상승 (게임 범위 기준)")),
    "kCampground": (2, 0.3, "regen", "upgraded", tr("근처 숲 재생 속도 상승 (게임 범위 기준)")),
    "kRockyHill": (3, 0.3, "regen", "upgraded", tr("근처 바위 재생 속도 상승 (게임 범위 기준)")),
    "kMansion": ("all", 0.2, "count", "upgraded", tr("근처 건물마다 분당 골드 1 (최대 29)")),
    "kCaptainQuarters": (
        "stat",
        1.0,
        "count",
        "upgraded",
        tr("인근 능력치 보너스 건물 +1 — 능력치 건물을 모두 범위 안에"),
    ),
    "kVeteranHut": (
        "housing",
        0.6,
        "count",
        "upgraded",
        tr("인근 거처 입주민 추가 경험치 (캐릭터 레벨 4·7·9에서 20·25·30%) — 거처를 모두 범위 안에"),
    ),
    # 강철 요새(방패잡이): 튕기면 근처 공사장에 건설 점수 +4 (위키 Iron Fortress) → 지금 공사 중인 건물과
    # 계속 강화할 무한 강화 능력치 건물 근처에 (Steam 가이드 '100% Utilization', 토론 'Max Iron Fortress')
    "kBrickHouse": (
        "build",
        0.5,
        "count",
        "upgraded",
        tr("튕기면 근처 공사장에 건설 점수 +4 — 공사 중·무한 강화 건물 근처에"),
    ),
}
# 무한 강화 능력치 건물 6개 (병원·사수 조합·카피톨륨·대박물관·마차 공장·전사 조합)
STATUE_TYPES = {
    "kEnduranceStatue",
    "kDexterityStatue",
    "kLeadershipStatue",
    "kIntelligenceStatue",
    "kSpeedStatue",
    "kStrengthStatue",
}
# 거처 효과는 건물 레벨이 아니라 사는 캐릭터가 레벨 4일 때 켜지고 7·9에서 강해진다 (위키 Buildings).
# 게임 캐릭터 레벨은 0부터 (CharMetaInst.Lvl) → 3 = 화면 레벨 4.
HOUSE_ACTIVE_LVL = 3
HOUSE_INACTIVE = 0.35  # 아직 안 켜진 거처 효과 (나중에 켜질 것 — 조금만 반영)
# 레벨에 따라 세지는 거처 효과 (위키): 잔병의 오두막 경험치 20% (레벨 4) · 25% (7) · 30% (9) → 최대 대비 비율. 게임 레벨은 0부터.
HOUSE_LEVEL_SCALE = {"kVeteranHut": ((8, 1.0), (6, 25 / 30), (3, 20 / 30))}
UNFINISHED_BUILD_W = 2.0  # 강철 요새: 공사 중인 건물은 무한 강화 건물보다 두 배 (지금 바로 건설 점수가 필요)


def _next_house_level(btype: str, lvl: int) -> int:
    """효과가 다음으로 세지는 화면 레벨 (게임 레벨 +1)."""
    steps = sorted([HOUSE_ACTIVE_LVL] + [n for n, _ in HOUSE_LEVEL_SCALE.get(btype, ())])
    return next((n + 1 for n in steps if lvl < n), steps[-1] + 1)


def _house_factor(btype: str, lvl: int) -> float:
    if lvl < HOUSE_ACTIVE_LVL:
        return HOUSE_INACTIVE
    for need, f in HOUSE_LEVEL_SCALE.get(btype, ()):
        if lvl >= need:
            return f
    return 1.0


CHAR_LEVELS: Dict[
    str, int
] = {}  # 캐릭터 slug(소문자, 예: recaller) → 게임 레벨 (계산 작업마다 set_char_levels 로 넣음)


def set_char_levels(levels: Dict[str, int]):
    """meta.chars 의 {kRecaller: 6, …} 를 받아 둔다 (이 프로세스 안에서 쓰는 전역값)."""
    CHAR_LEVELS.clear()
    for field_name, value in (levels or {}).items():
        if isinstance(value, int):
            CHAR_LEVELS[_slug(field_name)] = value


# 거처 slug → 사는 캐릭터 slug. 게임 건물 설명 '○○의 거처'와 캐릭터 이름을 맞춰 뽑은 표 (2026-09-26 한국어 추출본, 21개).
# 문구로만 찾으면 윈도우가 한국어가 아닐 때(게임 문구를 다른 언어로 추출) 거처를 하나도 못 찾아 가이드 배치가 깨진다.
HOUSE_CHAR: Dict[str, str] = {
    "brickhouse": "brickhead",
    "campground": "radicalai",
    "captainquarters": "tactician",
    "cozyhome": "cohabitants",
    "falconryhut": "falconer",
    "hauntedhouse": "recaller",
    "hiddentemple": "tiptoer",
    "hovel": "wimp",
    "lab": "physicist",
    "logcabin": "backpacker",
    "mansion": "spendthrift",
    "mausoleum": "shade",
    "monastery": "flagellant",
    "partyhouse": "carouser",
    "rockyhill": "sisyphus",
    "sheriffoffice": "itchyfinger",
    "singlefamilyhome": "emptynester",
    "stonedomain": "tunneller",
    "unstabletower": "packrat",
    "veteranhut": "embedded",
    "villa": "cogitator",
}


def _game_text() -> dict:
    from ..gamedata import DATA_DIR

    try:
        with open(os.path.join(DATA_DIR, "game_text_ko.json"), encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except (OSError, ValueError):
        return {}


@functools.lru_cache(maxsize=1)
def house_characters() -> Dict[str, Tuple[str, str]]:
    """거처 slug → (캐릭터 slug, 캐릭터 이름). 표(HOUSE_CHAR) + 한국어 게임 문구 '○○의 거처'로 새로 생긴 거처.
    이름은 추출한 게임 문구의 언어 그대로 (없으면 slug)."""
    game_text = _game_text()
    characters = (game_text.get("characters") or {}).values()
    name_of = {c.get("slug"): c.get("name_ko") for c in characters}
    result = {height: (c, name_of.get(c) or c) for height, c in HOUSE_CHAR.items()}
    by_name = {c.get("name_ko"): c.get("slug") for c in characters}
    for slug, value in (game_text.get("buildings") or {}).items():
        desc = value.get("desc_ko") or ""
        if slug not in result and "의 거처" in desc:
            who = desc.split("의 거처")[0].strip()
            if who in by_name:
                result[slug] = (by_name[who], who)
    return result


# 여러 개 지을 수 있는 건물·타일과 짓는 비용 [골드, 밀, 나무, 돌] (위키 건물표). 나머지 건물은 1개만.
UNLIMITED_COST: Dict[str, Tuple[int, int, int, int]] = {
    "kIdleFarm": (100, 0, 0, 0),
    "kIdleStoneMine": (100, 0, 5, 0),
    "kGoldMine": (0, 0, 10, 12),
    "kWheatField": (30, 0, 0, 0),
    "kForest": (50, 2, 0, 0),
    "kBoulder": (80, 0, 2, 0),
    "kDenseWheat": (100, 1, 0, 0),
    "kGrandTree": (150, 0, 1, 0),
    "kGraniteSlab": (250, 0, 0, 1),
}
REGEN_TYPES = {"kVilla", "kCampground", "kRockyHill"}
FIXED_TYPES = {"kHome"}
UNFINISHED_STATES = {"kScaffold", "kUpgrading"}


@functools.lru_cache(maxsize=1)
def housing_types() -> frozenset:
    """거처(캐릭터가 사는 건물, 소문자 slug): 표(HOUSE_CHAR) + 한국어 게임 설명이 '… 거처'인 것."""
    game_text = _game_text().get("buildings") or {}
    return frozenset(HOUSE_CHAR) | frozenset(
        slug for slug, value in game_text.items() if "거처" in (value.get("desc_ko") or "")
    )


def _slug(type_name: str) -> str:
    return (type_name[1:] if type_name.startswith("k") else type_name).lower()


@dataclass
class Piece:
    id: int
    type: str
    w: int
    h: int
    rel: frozenset  # 사각형 왼쪽 아래 기준 실제로 차지하는 타일
    movable: bool
    range: float
    factor: float = 1.0  # 효과가 켜진 정도 (강화 전·일꾼 없음 → 0.5)
    cap: float = 1.0  # 자원 타일 용량 (고급 타일 3~4, 강화하면 늘어남 — 게임 값 cap)
    unfinished: bool = False  # 공사 중·강화 공사 중 (강철 요새 건설 점수 대상)
    range_boxes: Optional[tuple] = None  # 게임 IsInRange의 대상 영역. None은 구형 중심 근사.


def rotated(piece: Piece, k: int) -> Piece:
    """시계 방향으로 90° × k 돌린 건물 (게임 rot +1 = 시계 방향 90°). ㄱ·ㅜ·ㅠ 자 모양도 그대로 돌린다.
    근거: 같은 건물이 다른 rot 로 기록된 충돌 모양 비교 (harvest_traces·live_state, 10종 — 시계 14 : 반시계 0)."""
    k %= 4
    width, height, rel = piece.w, piece.h, piece.rel
    for _ in range(k):
        rel = frozenset((y, width - 1 - x) for x, y in rel)  # (x, y 위쪽) → 시계 방향 90°
        width, height = height, width
    from .game_range import rotate_boxes

    return (
        piece
        if k == 0
        else Piece(
            piece.id,
            piece.type,
            width,
            height,
            rel,
            piece.movable,
            piece.range,
            piece.factor,
            piece.cap,
            piece.unfinished,
            rotate_boxes(piece.range_boxes, k),
        )
    )


@dataclass
class FullPlan:
    origin_before: Dict[int, Tuple[int, int]]
    origin_after: Dict[int, Tuple[int, int]]
    centers_after: Dict[int, Tuple[float, float]]
    effect_before: float
    effect_after: float
    detail_before: Dict[str, float]
    detail_after: Dict[str, float]
    harvest_before: Optional[List[int]] = None
    harvest_after: Optional[List[int]] = None
    moved: int = 0
    notes: List[str] = field(default_factory=list)
    turned: Dict[int, int] = field(
        default_factory=dict
    )  # 회전해서 놓는 건물 → 시계 방향 90° 횟수 (1~3, 가이드 배치)


class Layout:
    """타일 격자 위 건물 배치 (왼쪽 아래 타일 좌표) + 점유 표."""

    def __init__(self, grid: Grid, pieces: Dict[int, Piece], origin: Dict[int, Tuple[int, int]]):
        self.grid, self.pieces, self.origin = grid, pieces, dict(origin)
        self.occ: Dict[Tuple[int, int], int] = {}
        for index, current_origin in self.origin.items():
            for column in self.cells(index, current_origin):
                self.occ[column] = index

    def cells(self, i: int, origin: Optional[Tuple[int, int]] = None):
        piece = self.pieces[i]
        c0, r0 = origin if origin is not None else self.origin[i]
        return [(c0 + delta_x, r0 + delta_y) for delta_x, delta_y in piece.rel]

    def center(self, i: int, origin: Optional[Tuple[int, int]] = None) -> Tuple[float, float]:
        piece = self.pieces[i]
        c0, r0 = origin if origin is not None else self.origin[i]
        return self.grid.center(c0, r0, piece.w, piece.h)

    def rect_ids(self, c0: int, r0: int, w: int, h: int) -> Optional[Set[int]]:
        """영역 안에 걸친 건물들. 영역 밖으로 삐져나가거나 못 옮기는 건물이 있으면 None."""
        ids = set()
        for delta_x in range(w):
            for delta_y in range(h):
                index = self.occ.get((c0 + delta_x, r0 + delta_y))
                if index is not None:
                    ids.add(index)
        for index in ids:
            piece = self.pieces[index]
            occupied_cells, orr = self.origin[index]
            if (
                not piece.movable
                or occupied_cells < c0
                or orr < r0
                or occupied_cells + piece.w > c0 + w
                or orr + piece.h > r0 + h
            ):
                return None
        return ids

    def swap_regions(
        self, a: Tuple[int, int], b: Tuple[int, int], w: int, h: int
    ) -> Optional[List[Tuple[int, Tuple[int, int]]]]:
        """같은 크기 두 영역의 내용을 맞바꾼다. 되돌리기용 (건물, 이전 자리) 목록을 돌려준다."""
        if abs(a[0] - b[0]) < w and abs(a[1] - b[1]) < h:
            return None  # 겹치는 영역
        ia, ib = self.rect_ids(*a, w, h), self.rect_ids(*b, w, h)
        if ia is None or ib is None or (not ia and not ib):
            return None
        moves = [
            (index, (self.origin[index][0] + b[0] - a[0], self.origin[index][1] + b[1] - a[1]))
            for index in ia
        ]
        moves += [
            (index, (self.origin[index][0] + a[0] - b[0], self.origin[index][1] + a[1] - b[1]))
            for index in ib
        ]
        tiles = self.grid.tiles
        if any(column not in tiles for index, origin in moves for column in self.cells(index, origin)):
            return None  # 산 땅 밖으로 나가는 건물 (ㄱ자 건물의 빈 모서리 자리 등)
        return self.apply(moves)

    def apply(self, moves: List[Tuple[int, Tuple[int, int]]]) -> List[Tuple[int, Tuple[int, int]]]:
        undo = [(index, self.origin[index]) for index, _ in moves]
        for index, _ in moves:
            for column in self.cells(index):
                if self.occ.get(column) == index:
                    del self.occ[column]
        for index, origin in moves:
            self.origin[index] = origin
            for column in self.cells(index, origin):
                self.occ[column] = index
        return undo


def pieces_from_base(
    base: dict, grid: Grid, housing: Set[str], fixed: Sequence[int] = ()
) -> Tuple[Dict[int, Piece], Dict[int, Tuple[int, int]]]:
    from .construction_policy import is_construction_target

    buildings = buildings_from_base(base)
    masks = shape_masks(base.get("geo") or {}, buildings, grid)
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    pieces, origin = {}, {}
    for index, b in buildings.items():
        width, height = b.footprint
        rel = masks.get(index) or {
            (delta_x, delta_y) for delta_x in range(width) for delta_y in range(height)
        }
        info = raw.get(index, {})
        eff = EFFECTS.get(b.type)
        factor = 1.0
        if eff and eff[3] == "worker" and (info.get("worker") is None or info.get("worker", -1) < 0):
            factor = 0.5
        if eff and eff[3] == "upgraded":
            ch = house_characters().get(_slug(b.type))
            if ch and CHAR_LEVELS and ch[0] in CHAR_LEVELS:
                factor = _house_factor(b.type, CHAR_LEVELS[ch[0]])
            elif int(info.get("lvl") or 0) < 1:
                factor = 0.5
        rng_ = b.range
        # 범위는 게임 GetRange 값을 그대로 쓴다. 공략의 '표시보다 +1칸'을 이미 계산된 값에 다시 더하지 않는다.
        # 공사 중인 건물도 옮길 수 있다 (커뮤니티: 채집 구역 가장자리로 옮겨 일꾼이 치게 — Screen Rant 기지 공략)
        unfinished = info.get("state") in UNFINISHED_STATES and is_construction_target(b.type)
        movable = b.type not in FIXED_TYPES and index not in fixed
        capacity = float(info.get("cap") or TILE_CAPACITY.get(b.type, 1)) if b.type in TILE_RES else 1.0
        from .game_range import boxes_from_row

        pieces[index] = Piece(
            index,
            b.type,
            width,
            height,
            frozenset(rel),
            movable,
            rng_,
            factor,
            max(1.0, capacity),
            unfinished,
            boxes_from_row(info),
        )
        origin[index] = (
            round((b.x - width * grid.size / 2 - grid.ox) / grid.size),
            round((b.y - height * grid.size / 2 - grid.oy) / grid.size),
        )
    return pieces, origin


# 범위 모양: 게임 화면의 범위 표시가 건물 중심 기준 사각형이다 (사용자 스크린샷: 2×2 채석장, 범위 2.25 → 둘레 한 칸 4×4).
# 타일 중심이 사각형 안(|dx|, |dy| ≤ 범위)이면 범위 안. 플러그인 1.9 의 게임 값(in_range)으로 원/사각형·여유를 다시 고른다.
RANGE_SHAPE = "square"


def in_range(delta_x: float, delta_y: float, r: float, r2: Optional[float] = None) -> bool:
    if RANGE_SHAPE == "square":
        return abs(delta_x) <= r + 1e-6 and abs(delta_y) <= r + 1e-6
    return delta_x * delta_x + delta_y * delta_y <= (r2 if r2 is not None else r * r) + 1e-6


def target_in_range(delta_x, delta_y, r, target, pad=0.0):
    """게임 대상 영역이 있으면 대상 크기·회전을 반영하고 임의 범위 여유는 더하지 않는다."""
    if target.range_boxes is not None:
        from .game_range import overlaps

        return overlaps(delta_x, delta_y, r - pad, target.range_boxes)
    return in_range(delta_x, delta_y, r)


# 발사대 앞 채집 구역: 치여도 얻는 게 없는 건물(능력치·거처 등)은 뒤나 구석으로 (커뮤니티: 발사대 앞은 자원 타일·금광,
# 나머지 건물은 둘레를 막는 벽으로 — Screen Rant·TheGamer 기지 공략). 튕기면 효과가 있는 건물(게임 설명 '튕겨나갈 때')과
# 자원 타일, 공사 중인 건물(쳐야 지어짐)은 앞에 있어도 된다.
BOUNCE_TYPES = {"kGoldMine", "kBrickHouse", "kMonastery", "kHauntedHouse"}
LANE_R = 7.0  # 발사대에서 이 거리(타일)까지를 앞 구역으로 — 가까울수록 크게
LANE_W = 0.3  # 앞 구역 한 칸(가장 가까울 때) 벌점. 2×2 건물을 발사대 바로 앞에 두면 약 1 (능력치 건물 하나를 대위 막사 범위에 넣는 값)
LANE_TILE_W = 0.2  # 앞 구역 자원 타일 가산 (공을 던져 캐는 몫 — 커뮤니티: 자원 타일은 발사대 바로 앞에). 실제 기지 3곳 비교:
# 0 → 채집 계산 64.0·범위 효과 60.4, 0.2 → 74.0·59.3, 0.4 → 75.7·58.3
HARVEST_DAMP = (
    10  # 채집 발사 계산 비교 때 더하는 값 — 13 → 14 같은 작은 차이(계산 오차 수준)로 배치를 바꾸지 않게
)
SELECT_MOVE_COST = (
    0.002  # 후보 비교 때 옮기는 건물 하나당 (25개 = 5%p) — 거의 같은 효과에 많이 옮기는 배치를 막음
)


def lane_values(geo: dict, grid: Grid) -> Dict[Tuple[int, int], float]:
    """타일 → 앞 구역 값 (발사대에서 가까울수록 1, LANE_R 밖 0). 발사대 위치를 모르면 빈 값."""
    launcher = geo.get("launcher") or []
    if len(launcher) < 2:
        return {}
    lx, ly = float(launcher[0]), float(launcher[1])
    result = {}
    for c, row in grid.tiles:
        x, y = grid.ox + (c + 0.5) * grid.size, grid.oy + (row + 0.5) * grid.size
        d = math.hypot(x - lx, y - ly) / grid.size
        if d < LANE_R:
            result[(c, row)] = 1.0 - d / LANE_R
    return result


def entrance_cells(geo: dict, grid: Grid) -> Set[Tuple[int, int]]:
    """입구 앞줄 전체와 중앙 두 번째 줄을 비운다. 발사 위치가 중앙에서 옮겨져도 앞줄에 막히지 않게 한다."""
    chunk = geo.get("entrance_chunk") or geo.get("entrance_grid") or []
    if not (
        isinstance(chunk, (list, tuple))
        and len(chunk) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in chunk)
    ):
        return set()
    width, height = geo.get("chunk_w"), geo.get("chunk_h")
    if not (isinstance(width, int) and isinstance(height, int) and width >= 2 and height >= 2):
        return set()
    if chunk not in (geo.get("chunks") or []):
        return set()
    # 보호 폭은 배치 정책이다. 게임의 발사 금지 판정은 launch_access에서 첫 충돌로 별도 검사한다.
    left = chunk[0] * width + width // 2 - 1
    bottom = chunk[1] * height
    cells = {(c, bottom) for c in range(chunk[0] * width, (chunk[0] + 1) * width)}
    cells.update((c, bottom + 1) for c in (left, left + 1))
    return cells & grid.tiles


def lane_idle(piece: "Piece") -> bool:
    return not (piece.type in TILE_RES or piece.type in BOUNCE_TYPES or piece.unfinished)


def preserves_production(base: dict, candidate: dict, pad: float = 0.0) -> bool:
    """게임 현재 계수와 일치한 가동 생산 건물의 자원 수·용량을 줄이는 후보는 거른다."""
    from .layout_city import PRODUCERS

    before, after = buildings_from_base(base), buildings_from_base(candidate)
    raw_before = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    raw_after = {b["id"]: b for b in candidate.get("buildings") or [] if "id" in b}

    def coverage(building, all_buildings, raw, kind):
        from .game_range import row_in_range

        targets = [
            b
            for b in all_buildings.values()
            if TILE_RES.get(b.type) == kind and row_in_range(raw[building.id], raw[b.id], pad)
        ]
        capacity = sum(max(1, float(raw.get(b.id, {}).get("cap") or 1)) for b in targets)
        return len(targets), capacity

    for index, b in before.items():
        info = raw_before[index]
        count = info.get("in_range")
        if b.type not in PRODUCERS or not isinstance(info.get("worker"), int) or info["worker"] < 0:
            continue
        if info.get("state") in UNFINISHED_STATES or not isinstance(count, dict) or not count:
            continue
        if not all(isinstance(value, int) and value >= 0 for value in count.values()):
            continue
        n0, c0 = coverage(b, before, raw_before, PRODUCERS[b.type])
        if n0 != sum(count.values()):
            continue
        if index not in after:
            return False
        n1, c1 = coverage(after[index], after, raw_after, PRODUCERS[b.type])
        if n1 < n0 or c1 + 1e-6 < c0:
            return False
    return True


def _repair_unused_producer_tiles(
    base: dict,
    grid: Grid,
    pieces: Dict[int, Piece],
    origin0: Dict[int, Tuple[int, int]],
    scorer: "Scorer",
    pad: float = 0.0,
    deadline: Optional[float] = None,
) -> Dict[int, Tuple[int, int]]:
    """직접 계수를 확인한 생산 건물의 빈칸에, 어느 자원 효과도 받지 않는 타일만 옮긴 결정적 후보."""
    from .layout_city import PRODUCERS
    from .layout_guide import preserves_guide

    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    lay = Layout(grid, pieces, origin0)
    entrance = entrance_cells(base.get("geo") or {}, grid)
    effects = [
        piece
        for piece in pieces.values()
        if piece.range > 0 and piece.factor > 0 and isinstance(EFFECTS.get(piece.type, (None,))[0], int)
    ]
    receivers = []
    for index, piece in sorted(pieces.items()):
        counted = raw.get(index, {}).get("in_range")
        if (
            piece.type not in PRODUCERS
            or piece.factor < 1.0
            or piece.unfinished
            or piece.range <= 0
            or not isinstance(counted, dict)
            or not counted
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in counted.values()
            )
        ):
            continue
        x, y = lay.center(index)
        actual = sum(
            target_in_range(
                lay.center(other_index)[0] - x,
                lay.center(other_index)[1] - y,
                piece.range + pad,
                other_piece,
                pad,
            )
            for other_index, other_piece in pieces.items()
            if TILE_RES.get(other_piece.type) == PRODUCERS[piece.type]
        )
        if actual == sum(counted.values()):
            receivers.append(index)
    score = scorer.score(lay)[0]
    for index in receivers:
        piece = pieces[index]
        kind = PRODUCERS[piece.type]
        x, y = lay.center(index)

        def unused(other_index):
            tx, ty = lay.center(other_index)
            return not any(
                EFFECTS[e.type][0] == kind
                and target_in_range(
                    tx - lay.center(e.id)[0],
                    ty - lay.center(e.id)[1],
                    e.range + pad,
                    pieces[other_index],
                    pad,
                )
                for e in effects
            )

        donors = [
            other_index
            for other_index, other_piece in pieces.items()
            if TILE_RES.get(other_piece.type) == kind
            and other_piece.movable
            and not other_piece.unfinished
            and unused(other_index)
        ]
        donors.sort(
            key=lambda other_index: (
                -pieces[other_index].cap,
                abs(lay.center(other_index)[0] - x) + abs(lay.center(other_index)[1] - y),
                other_index,
            )
        )
        for other_index in donors:
            if deadline is not None and time.perf_counter() >= deadline:
                break
            other_piece = pieces[other_index]
            old = lay.origin[other_index]
            spots = []
            for at in grid.tiles:
                tx, ty = grid.center(*at, other_piece.w, other_piece.h)
                if not target_in_range(tx - x, ty - y, piece.range + pad, other_piece, pad):
                    continue
                cells = {(at[0] + delta_x, at[1] + delta_y) for delta_x, delta_y in other_piece.rel}
                if cells <= grid.tiles and not cells & entrance and not cells & lay.occ.keys():
                    spots.append(at)
            spots.sort(key=lambda at: (abs(at[0] - old[0]) + abs(at[1] - old[1]), at))
            for at in spots:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                undo = lay.apply([(other_index, at)])
                gain = scorer.score(lay)[0]
                if gain > score + 1e-6:
                    score = gain
                    break
                lay.apply(undo)
    if lay.origin == origin0:
        return dict(origin0)
    proposal = final_base(
        base,
        FullPlan(
            dict(origin0), dict(lay.origin), {index: lay.center(index) for index in lay.origin}, 0, 0, {}, {}
        ),
    )
    # 타일을 공급하는 과정에서도 기존 생산·허브·입구와 실행 가능한 이동 순서를 보존한다.
    if (
        not preserves_production(base, proposal, pad)
        or not preserves_guide(base, proposal, pad)
        or not move_sequence(grid, pieces, origin0, lay.origin, avoid=entrance).complete
    ):
        return dict(origin0)
    return dict(lay.origin)


PRESETS = {"effect": tr("효과 최대"), "gold_u": tr("금광 U자"), "plan": tr("계획도시")}
PRESET_W = 20.0  # 프리셋 자리에 놓인 금광 하나의 가산 (범위 효과보다 크게 — 사용자가 고른 공략 틀을 따름)
PRESET_CLEAR = 4.0  # 아직 금광이 없는 프리셋 자리를 다른 건물이 막는 칸마다 감점 — 0.5 는 약해서 채석장이 자리를 차지한 채 남음


def gold_u_spots(geo: dict, grid: Grid, n: int = 7) -> List[Tuple[int, int]]:
    """금광 U자 (커뮤니티 정석: 발사대 바로 앞에 2×2 금광 7개 — 양옆 3개씩 세로로, 위에 1개가 막음).
    가운데 통로는 2칸 폭으로 발사대 열에 맞춘다. 산 땅 밖으로 나가는 자리는 뺀다. 돌려주는 값: 금광 왼쪽 아래 타일들."""
    launcher = geo.get("launcher") or []
    if len(launcher) < 2:
        return []
    lc = int((float(launcher[0]) - grid.ox) // grid.size)
    columns = [c for c, _ in grid.tiles if abs(c - lc) <= 1]
    rows = [row for c, row in grid.tiles if c == lc] or [row for _, row in grid.tiles]
    r0 = min(rows)
    left, right = lc - 3, lc + 1  # 통로 = lc-1, lc (2칸)
    spots = [
        (left, r0),
        (right, r0),
        (left, r0 + 2),
        (right, r0 + 2),
        (left, r0 + 4),
        (right, r0 + 4),
        (lc - 1, r0 + 6),
    ]
    ok = [
        s
        for s in spots
        if all((s[0] + delta_x, s[1] + delta_y) in grid.tiles for delta_x in range(2) for delta_y in range(2))
    ]
    return ok[:n] if columns else []


class Scorer:
    def __init__(
        self,
        pieces: Dict[int, Piece],
        stat_types: Set[str],
        housing: Set[str],
        res_weight: Optional[Dict[int, float]] = None,
        pad: float = 0.0,
        preset_spots: Sequence[Tuple[int, int]] = (),
        preset_type: str = "kGoldMine",
        lane: Optional[Dict[Tuple[int, int], float]] = None,
        hub_w: float = 1.0,
        build_lane: float = 0.0,
    ):
        self.pieces = pieces
        self.res_weight = res_weight or {1: 1.0, 2: 1.0, 3: 1.0}
        self.hub_w = hub_w  # 가이드 허브(잔병의 오두막·대위 막사·강철 요새) 효과에 곱하는 값 (GUIDE_HUB_W)
        self.lane = lane or {}
        self.lane_ids = [index for index, piece in pieces.items() if lane_idle(piece)] if self.lane else []
        # 앞 구역 가산: 자원 타일(자원 가중 × 용량) + build_lane 이면 공사 중 건물도 (쳐야 지어지므로 공이 닿는 발사대 앞에)
        self.lane_weight: Dict[int, float] = {}
        if self.lane:
            for index, piece in pieces.items():
                if piece.type in TILE_RES:
                    self.lane_weight[index] = self.res_weight_of(piece)
                elif build_lane and piece.unfinished:
                    self.lane_weight[index] = build_lane
        self.lane_tiles = list(self.lane_weight)
        self.preset_spots = set(preset_spots)
        self.preset_type = preset_type
        self.preset_cells = {
            s: {(s[0] + delta_x, s[1] + delta_y) for delta_x in range(2) for delta_y in range(2)}
            for s in self.preset_spots
        }
        self.pad = pad  # 범위 판정 여유 (게임 값으로 맞춘 것, calibrate_range)
        self.effects = [index for index, piece in pieces.items() if piece.type in EFFECTS and piece.range > 0]
        self.targets: Dict[object, List[int]] = {
            1: [],
            2: [],
            3: [],
            "all": [],
            "stat": [],
            "housing": [],
            "statue": [],
            "build": [],
        }
        for index, piece in pieces.items():
            if piece.type in TILE_RES:
                self.targets[TILE_RES[piece.type]].append(index)
            if piece.type in stat_types:
                self.targets["stat"].append(index)
            if _slug(piece.type) in housing:
                self.targets["housing"].append(index)
            if piece.type in STATUE_TYPES:
                self.targets["statue"].append(index)
            if piece.type in STATUE_TYPES or piece.unfinished:
                self.targets["build"].append(index)
            self.targets["all"].append(index)

    def res_weight_of(self, piece: "Piece") -> float:
        return self.res_weight.get(TILE_RES[piece.type], 1.0) * piece.cap

    def score(self, layout: Layout) -> Tuple[float, Dict[str, float]]:
        ctr = {index: layout.center(index) for index in layout.origin}
        total = 0.0
        detail: Dict[str, float] = {}
        regen: Dict[Tuple[str, int], float] = {}  # (효과 종류, 타일) → 최대 가중치
        harvest: Dict[int, List[float]] = {}  # 타일 → 채집 건물 가중치들
        for effect_id in self.effects:
            piece = self.pieces[effect_id]
            kind, weight, mode, _, _ = EFFECTS[piece.type]
            ex, ey = ctr[effect_id]
            rr = piece.range + self.pad
            r2 = rr * rr
            target_count = 0
            for target_id in self.targets[kind]:
                if target_id == effect_id:
                    continue
                tx, ty = ctr[target_id]
                if not target_in_range(tx - ex, ty - ey, rr, layout.pieces[target_id], self.pad):
                    continue
                target_count += 1
                if mode == "harvest" and target_count > HARVEST_CAP:
                    continue  # 채집 건물 하나가 쓰는 타일 수 상한
                val = (
                    weight
                    * piece.factor
                    * (
                        self.res_weight.get(kind, 1.0) * self.pieces[target_id].cap
                        if isinstance(kind, int)
                        else 1.0
                    )
                )
                if kind == "build" and self.pieces[target_id].unfinished:
                    val *= UNFINISHED_BUILD_W
                if kind in HUB_KINDS:
                    val *= self.hub_w
                if mode == "regen":
                    k = (piece.type, target_id)
                    regen[k] = max(regen.get(k, 0.0), val)
                elif mode == "harvest":
                    harvest.setdefault(target_id, []).append(val)
                else:
                    total += val
            detail[piece.type] = detail.get(piece.type, 0) + target_count
        total += sum(regen.values())
        for vals in harvest.values():
            vals.sort(reverse=True)
            total += sum(
                value * (1.0 if step_index == 0 else 0.5 if step_index == 1 else 0.0)
                for step_index, value in enumerate(vals)
            )
        if self.lane:
            blocked = sum(
                self.lane.get(column, 0.0)
                for index in self.lane_ids
                if index in layout.origin
                for column in layout.cells(index)
            )
            total -= LANE_W * blocked
            detail["lane"] = round(blocked, 2)
            # 생산 건물·거처가 이미 캐는 타일은 빼고 (같은 타일 자원을 나눠 쓰므로 둘 다 더하면 이중 계산)
            front = sum(
                self.lane.get(column, 0.0) * self.lane_weight[index]
                for index in self.lane_tiles
                if index in layout.origin and index not in harvest
                for column in layout.cells(index)
            )
            total += LANE_TILE_W * front
            detail["lane_tiles"] = round(front, 2)
        if self.preset_spots:
            filled = {
                layout.origin[index]
                for index, piece in self.pieces.items()
                if piece.type == self.preset_type
                and index in layout.origin
                and layout.origin[index] in self.preset_spots
            }
            total += PRESET_W * len(filled)
            for target_cell, cells in self.preset_cells.items():
                if target_cell not in filled:
                    total -= PRESET_CLEAR * sum(1 for c in cells if c in layout.occ)
        return total, detail


# 가이드 배치(preset "guide"): Steam 공략 3개(Zarcos·Drake·apo)가 '전부 범위 안에'라고 하는 허브 효과를 이만큼 크게 친다
# — 잔병의 오두막(거처)·대위 막사(능력치 건물)·강철 요새(공사 중·무한 강화). 값은 임의: 3배면 거처 하나가 범위 밖일 때
# 1.8 (밀밭 두 개 가까이) 손해라 옮길 만하고, 자원 들판(타일당 1.0)보다 먼저 맞춘다.
HUB_KINDS = ("housing", "stat", "build")
GUIDE_HUB_W = 3.0
# 가이드 배치: 공사 중 건물의 앞 구역 가산 (자원 타일 가중 1 과 같은 식, 칸마다 앞 구역 값 × 이 값 × LANE_TILE_W). 값은 임의:
# 3×2 건물을 발사대 가까이 두면 약 +4 — 거처 두 개를 범위에 넣는 것만큼. 사용자 스크린샷(2026-09-26): 가이드 배치대로
# 옮겼더니 쳐야 하는 도박장이 마을 구석에 묻혀 어떤 각도로도 안 닿음.
GUIDE_BUILD_LANE = 5.0
MOVE_COST = 0.03  # 옮기는 건물 하나당 벌점 — 실제 기지에서 0.005(42번 옮김, 효과 +4%)·0.03(22번, +7.5%)·0.06(탐색 멈춤) 비교해 정함


# 네이티브 탐색: 한 번 0.5초(담금질 0.4 + 다듬기 0.1), 최소 4번, 연속 3번 더 좋은 배치가 없으면 멈춤
RESTART_SECONDS = 0.5
MIN_RESTARTS = 4
CONVERGED = 3

EXPLORE_COST = 0.005  # 담금질 중에는 벌점을 작게 (크면 처음 배치에서 못 벗어남 — 실제 기지 6번 중 4번)


def _objective(
    layout: Layout, scorer: Scorer, origin0: Dict[int, Tuple[int, int]], cost: float = MOVE_COST
) -> float:
    s, _ = scorer.score(layout)
    return s - cost * sum(1 for index, origins in layout.origin.items() if origins != origin0.get(index))


def _near_spots(
    layout: Layout, scorer: Scorer, i: int, cand: List[Tuple[int, int]], w: int, h: int, rng: random.Random
) -> List[Tuple[int, int]]:
    """건물 i 가 효과를 주거나 받을 만한 자리 (관련 건물의 범위 안)."""
    piece = layout.pieces[i]
    if scorer.preset_spots and piece.type == scorer.preset_type:
        spots = [origin for origin in cand if origin in scorer.preset_spots]  # 금광은 프리셋 자리로
        if spots:
            return spots
    partners: List[Tuple[Tuple[float, float], float]] = []
    kind = TILE_RES.get(piece.type)
    for e in scorer.effects:
        ek = EFFECTS[layout.pieces[e].type][0]
        if e != i and (ek == kind or ek == "all"):
            partners.append((layout.center(e), layout.pieces[e].range))
    if piece.type in EFFECTS:
        for target_id in scorer.targets[EFFECTS[piece.type][0]]:
            if target_id != i:
                partners.append((layout.center(target_id), piece.range))
    if not partners:
        return cand
    (px, py), partner_id = rng.choice(partners)
    grid = layout.grid
    result = [
        origin
        for origin in cand
        if in_range(
            grid.center(origin[0], origin[1], w, h)[0] - px,
            grid.center(origin[0], origin[1], w, h)[1] - py,
            partner_id,
        )
    ]
    return result or cand


def polish(
    layout: Layout, scorer: Scorer, origin0: Dict[int, Tuple[int, int]], seconds: float = 3.0
) -> float:
    """마무리: 건물마다 같은 크기의 모든 자리와 맞바꿔 보고 가장 좋은 것을 받아들인다 (더 나아지지 않을 때까지).
    네이티브 모듈(같은 규칙)이 있으면 그것으로."""
    if seconds <= 0:
        return _objective(layout, scorer, origin0)
    from . import native_layout

    got = native_layout.polish(layout, scorer, origin0, seconds, MOVE_COST)
    if got is not None:
        org, current = got
        layout.apply([(index, origin) for index, origin in org.items() if layout.origin.get(index) != origin])
        return current
    grid = layout.grid
    spots = {}
    current = _objective(layout, scorer, origin0)
    start = time.perf_counter()
    improved = True
    while improved and time.perf_counter() - start < seconds:
        improved = False
        for index, piece in layout.pieces.items():
            if not piece.movable:
                continue
            k = (piece.w, piece.h)
            if k not in spots:
                spots[k] = [
                    (c, row)
                    for c, row in grid.tiles
                    if all(
                        (c + delta_x, row + delta_y) in grid.tiles
                        for delta_x in range(piece.w)
                        for delta_y in range(piece.h)
                    )
                ]
            best = (current, None)
            for candidate_origin in spots[k]:
                if time.perf_counter() - start >= seconds:
                    return current
                undo = layout.swap_regions(layout.origin[index], candidate_origin, piece.w, piece.h)
                if undo is None:
                    continue
                value = _objective(layout, scorer, origin0)
                if value > best[0] + 1e-9:
                    best = (value, candidate_origin)
                layout.apply(undo)
            if best[1] is not None:
                layout.swap_regions(layout.origin[index], best[1], piece.w, piece.h)
                current = best[0]
                improved = True
    return current


def anneal(
    layout: Layout,
    scorer: Scorer,
    seconds: float,
    rng: random.Random,
    t0: float = 0.8,
    t1: float = 0.005,
    origin0: Optional[Dict[int, Tuple[int, int]]] = None,
) -> Tuple[Dict[int, Tuple[int, int]], float]:
    """영역 맞바꾸기 담금질. 가장 좋았던 배치(자리 표)와 점수(벌점 포함)를 돌려준다.
    네이티브 모듈(같은 규칙, 수십 배 많이 시도)이 있으면 그것으로."""
    from . import native_layout

    grid = layout.grid
    origin0 = origin0 if origin0 is not None else dict(layout.origin)
    if seconds <= 0:
        return dict(layout.origin), scorer.score(layout)[0]
    got = native_layout.anneal(layout, scorer, seconds, rng.getrandbits(64), t0, t1, origin0, EXPLORE_COST)
    if got is not None:
        return got[0], got[1]
    movable = [index for index, piece in layout.pieces.items() if piece.movable]
    if not movable:
        score, _ = scorer.score(layout)
        return dict(layout.origin), score
    # 크기별 가능한 영역 왼쪽 아래 (산 땅 안에 전부 들어가는 곳)
    origins: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    def spots(w: int, h: int) -> List[Tuple[int, int]]:
        k = (w, h)
        if k not in origins:
            origins[k] = [
                (c, row)
                for c, row in grid.tiles
                if all(
                    (c + delta_x, row + delta_y) in grid.tiles for delta_x in range(w) for delta_y in range(h)
                )
            ]
        return origins[k]

    current = _objective(layout, scorer, origin0, EXPLORE_COST)
    best, best_origin = current, dict(layout.origin)
    start = time.perf_counter()
    it = 0
    while True:
        it += 1
        if it % 64 == 0:
            el = (time.perf_counter() - start) / seconds
            if el >= 1:
                break
            temp = t0 * (t1 / t0) ** el
        elif it == 1:
            temp = t0
        index = rng.choice(movable)
        piece = layout.pieces[index]
        width, height = piece.w, piece.h
        if rng.random() < 0.25:  # 가끔 더 큰 묶음 영역 (작은 타일 여러 개 교환)
            width, height = width + rng.randint(0, 2), height + rng.randint(0, 2)
        cand = spots(width, height)
        if not cand:
            continue
        original_origin = layout.origin[index]
        if width != piece.w or height != piece.h:
            original_origin = (
                original_origin[0] - rng.randint(0, width - piece.w),
                original_origin[1] - rng.randint(0, height - piece.h),
            )
            if not all(
                (original_origin[0] + delta_x, original_origin[1] + delta_y) in grid.tiles
                for delta_x in range(width)
                for delta_y in range(height)
            ):
                continue
        candidate_origin = rng.choice(
            _near_spots(layout, scorer, index, cand, width, height, rng) if rng.random() < 0.6 else cand
        )
        undo = layout.swap_regions(original_origin, candidate_origin, width, height)
        if undo is None:
            continue
        score = _objective(layout, scorer, origin0, EXPLORE_COST)
        score_delta = score - current
        if score_delta >= 0 or rng.random() < math.exp(score_delta / max(temp, 1e-6)):
            current = score
            if score > best + 1e-9:
                best, best_origin = score, dict(layout.origin)
        else:
            layout.apply(undo)
    return best_origin, best


def canonicalize(
    pieces: Dict[int, Piece], origin0: Dict[int, Tuple[int, int]], final: Dict[int, Tuple[int, int]]
) -> Dict[int, Tuple[int, int]]:
    """같은 종류·같은 모양 건물끼리는 누가 어느 자리에 가도 같다 — 원래 자리에 있던 건물이 그 자리를 갖게 해
    쓸데없는 옮기기(숲 A ↔ 숲 B)를 없앤다."""
    groups: Dict[tuple, List[int]] = {}
    for index, piece in pieces.items():
        if index in final:
            groups.setdefault(
                (
                    piece.type,
                    piece.w,
                    piece.h,
                    piece.rel,
                    piece.factor,
                    piece.movable,
                    piece.range,
                    piece.cap,
                    piece.unfinished,
                    piece.range_boxes,
                ),
                [],
            ).append(index)
    result = dict(final)
    for ids in groups.values():
        if len(ids) < 2:
            continue
        spots = [final[index] for index in ids]
        free = list(spots)
        keep = {}
        for index in ids:
            if origin0[index] in free:
                keep[index] = origin0[index]
                free.remove(origin0[index])
        rest = [index for index in ids if index not in keep]
        for index in rest:  # 남은 건물은 가장 가까운 남은 자리로
            origin = min(
                free,
                key=lambda candidate_origin: (
                    (candidate_origin[0] - origin0[index][0]) ** 2
                    + (candidate_origin[1] - origin0[index][1]) ** 2
                ),
            )
            keep[index] = origin
            free.remove(origin)
        result.update(keep)
    return result


def move_sequence(
    grid: Grid,
    pieces: Dict[int, Piece],
    current: Dict[int, Tuple[int, int]],
    target: Dict[int, Tuple[int, int]],
    turn: Optional[Dict[int, int]] = None,
    avoid: Set[Tuple[int, int]] = frozenset(),
) -> MoveSequence:
    """지금 배치 → 목표 배치로 가는 옮기기 순서 (한 번에 한 건물, 항상 빈 자리로만).

    1) 목표 자리가 비어 있는 건물을 모두 옮긴다.
    2) 막히면 막는 건물이 가장 적은 목표 자리 하나를 골라, 막는 건물들을 다른 건물 목표가 아닌 빈 곳에 잠시 비켜 둔다.
       그 건물은 다음 차례에 제자리로 간다 — 매 단계 최소 한 건물이 목표 자리에 확정되므로 반드시 끝난다.
    목표 자리끼리는 겹치지 않으므로 목표 자리를 막는 건물은 항상 아직 안 옮긴 건물이다.
    turn: 목표 자리에 회전해서 놓을 건물 → 시계 방향 90° 횟수. 목표 자리로 가는 한 번에 회전도 한다 (비켜 두기는 그대로).
    돌려주는 값: 목록 호환 MoveSequence. 실패하면 complete=False, unresolved에 남은 건물을 넣고
    부분 이동은 반환하지 않는다 (도중에 멈추는 순서를 완성 가능한 안내로 내보내지 않도록)."""
    if not set(target) <= set(pieces) or not set(target) <= set(current):
        return MoveSequence(complete=False, unresolved=sorted(set(target) - (set(pieces) & set(current))))
    target = {**current, **target}
    lay = Layout(grid, dict(pieces), current)
    turn = turn or {}
    goal_piece = {index: rotated(pieces[index], turn.get(index, 0)) for index in target}
    pending = [index for index in target if lay.origin.get(index) != target[index] or index in turn]
    requested = tuple(pending)
    goal_cells_all = [
        (target[index][0] + delta_x, target[index][1] + delta_y)
        for index, piece in goal_piece.items()
        for delta_x, delta_y in piece.rel
    ]
    if (
        len(goal_cells_all) != len(set(goal_cells_all))
        or not set(goal_cells_all) <= grid.tiles
        or any(not pieces[index].movable for index in pending)
    ):
        return MoveSequence(complete=False, unresolved=pending)
    steps: List[Tuple[int, Tuple[int, int], bool]] = []

    def goal_cells(i: int) -> List[Tuple[int, int]]:
        return [(target[i][0] + delta_x, target[i][1] + delta_y) for delta_x, delta_y in goal_piece[i].rel]

    def blockers(i: int) -> Set[int]:
        return {lay.occ[c] for c in goal_cells(i) if lay.occ.get(c) not in (None, i)}

    def settle(i: int):
        for column in lay.cells(i):
            if lay.occ.get(column) == i:
                del lay.occ[column]
        lay.pieces[i] = goal_piece[i]
        lay.origin[i] = target[i]
        for column in lay.cells(i):
            lay.occ[column] = i

    for _ in range(len(pieces) + 5):
        moved = True
        while moved:
            moved = False
            for index in list(pending):
                if not blockers(index):
                    settle(index)
                    steps.append((index, target[index], False))
                    pending.remove(index)
                    moved = True
        if not pending:
            break
        goal = min(
            pending,
            key=lambda i: (
                len(blockers(i)),
                sum(len(pieces[other_index].rel) for other_index in blockers(i)),
            ),
        )
        keep_clear = set(goal_cells(goal))
        reserved = {c for other_index in pending for c in goal_cells(other_index)}
        for other_index in sorted(blockers(goal), key=lambda other_index: len(pieces[other_index].rel)):
            piece = lay.pieces[other_index]
            own = set(goal_cells(other_index))
            best = None
            for c, row in sorted(grid.tiles):
                cells = {(c + delta_x, row + delta_y) for delta_x, delta_y in piece.rel}
                if not cells <= grid.tiles or cells & (keep_clear | avoid):
                    continue
                if any(lay.occ.get(x) not in (None, other_index) for x in cells):
                    continue
                clash = len(cells & (reserved - own))  # 다른 건물 목표 자리를 덜 막는 곳
                if best is None or clash < best[0]:
                    best = (clash, (c, row))
                    if clash == 0:
                        break
            if best is None:
                return MoveSequence(complete=False, unresolved=requested)
            lay.apply([(other_index, best[1])])
            steps.append((other_index, best[1], True))
    return MoveSequence(steps, complete=not pending, unresolved=requested if pending else ())


def optimize(
    base: dict,
    harvest_eval: Optional[Callable[[dict], List[int]]] = None,
    reach_ok: Optional[Callable[[dict], bool]] = None,
    seconds: float = 6.0,
    restarts: int = 3,
    res_weight: Optional[Dict[int, float]] = None,
    seed: int = 0,
    fixed: Sequence[int] = (),
    pad: float = 0.0,
    prefer: Optional[Dict[int, Tuple[float, float]]] = None,
    preset: str = "effect",
) -> Optional[FullPlan]:
    """전체 재배치 최적화. harvest_eval(geo) → [골드, 밀, 나무, 돌] (채집 발사 예상), reach_ok(base) → 미완성 건물에 닿는지."""
    # 후보 준비도 탐색 예산에 포함한다. 선택된 후보의 물리 검증 시간은 별도이며 생략하지 않는다.
    search_deadline = time.perf_counter() + max(0.0, seconds)
    from .layout import move_geo

    geometry = base.get("geo") or {}
    grid = grid_from_geo(geometry)
    if grid is None:
        return None
    housing = housing_types()
    stat_types = _stat_types(base)  # kNum(능력치 없음) 제외 — 전에는 모든 건물을 능력치 건물로 셌다
    # 가이드 배치는 '쳐야 지어지는' 건물(fixed 로 넘어온 공사 중 건물)을 제자리에 묶지 않고 발사대 쪽으로 옮긴다
    # 가이드 배치(plan·guide)는 공사 중 건물도 묶지 않는다 — 공이 닿는 자리로 옮긴다 (guide 는 reach_ok 로 확인)
    pieces, origin0 = pieces_from_base(base, grid, housing, () if preset in ("plan", "guide") else fixed)
    if not pieces:
        return None
    preset_spots = gold_u_spots(geometry, grid) if preset == "gold_u" else []
    lane = lane_values(geometry, grid)
    guide = preset == "guide"
    entrance = entrance_cells(geometry, grid) if guide else set()
    scorer = Scorer(
        pieces,
        stat_types,
        housing,
        res_weight,
        pad,
        preset_spots,
        lane=lane,
        hub_w=GUIDE_HUB_W if guide else 1.0,
        build_lane=GUIDE_BUILD_LANE if guide else 0.0,
    )
    plain = Scorer(pieces, stat_types, housing, res_weight, pad)  # 보고용 (프리셋 가산 없는 범위 효과)
    # 발사대 앞 구역(자원 타일은 앞에, 치여도 얻는 게 없는 건물은 뒤로)은 담금질과 후보 비교 모두에 넣는다.
    # 실제 기지 3곳에서 앞 구역을 넣은 쪽이 채집 발사 계산·범위 효과 모두 좋았다 (53.2 → 56.2, 49.8 → 52.1).
    laned = scorer
    if preset == "plan":
        return _optimize_city(base, grid, pieces, origin0, scorer, plain, harvest_eval, pad, set(fixed))
    if preset == "gold_u":
        prefer = None  # 사용자가 고른 틀이므로 이전 목표를 고집하지 않음
    lay0 = Layout(grid, pieces, origin0)
    e0, d0 = scorer.score(lay0)
    rng = random.Random(seed)
    cands = [(dict(origin0), e0)]
    turn_of: Dict[int, Dict[int, int]] = {}  # 후보(id) → 회전 (가이드 배치가 거처 덩어리를 다시 짤 때)
    starts: List[Tuple[Dict[int, Tuple[int, int]], Dict[int, int], Set[int]]] = [(origin0, {}, set())]
    groups = []

    def shaped_of(turn: Dict[int, int]) -> Dict[int, Piece]:
        return (
            {index: rotated(piece, turn.get(index, 0)) for index, piece in pieces.items()} if turn else pieces
        )

    def pinned_of(turn: Dict[int, int], pin: Set[int]) -> Dict[int, Piece]:
        """담금질용 모양: pin 에 든 건물은 못 옮기게 (가이드 규칙을 지킨 허브·거처를 다시 빼지 않게)."""
        sp = shaped_of(turn)
        if not pin:
            return sp
        return {
            index: (
                Piece(
                    piece.id,
                    piece.type,
                    piece.w,
                    piece.h,
                    piece.rel,
                    False,
                    piece.range,
                    piece.factor,
                    piece.cap,
                    piece.unfinished,
                    piece.range_boxes,
                )
                if index in pin
                else piece
            )
            for index, piece in sp.items()
        }

    def add_cand(orig: Dict[int, Tuple[int, int]], turn: Dict[int, int]):
        sp = shaped_of(turn)
        origins = canonicalize(sp, origin0, orig)
        cands.append((origins, scorer.score(Layout(grid, sp, origins))[0]))
        if turn:
            turn_of[id(origins)] = dict(turn)

    launcher = geometry.get("launcher") if geometry.get("launcher_source") != "unavailable" else None
    if guide:
        from . import construction_front
        from .layout_guide import preserves_guide

        if entrance & set(lay0.occ):
            from .layout_guide import repair as repair_access

            # 허브 탐색이 시간 예산을 쓰기 전에 입구 비움·공사 앞배치 후보부터 확보한다.
            # 후보는 아래의 기존 효과·생산·이동 순서·물리 검사를 모두 거친다.
            access = repair_access(grid, pieces, origin0, [], pad, lane, entrance, deadline=search_deadline)
            if access is not None:
                add_cand(access[0], access[1])
                starts.append((access[0], access[1], set()))

        def preserves_front_move(origins, turns):
            candidate_layout = Layout(grid, shaped_of(turns), origins)
            proposal = FullPlan(origin0, origins,
                                {index: candidate_layout.center(index) for index in origins},
                                0., 0., {}, {}, turned=turns)
            candidate_base = final_base(base, proposal)
            return preserves_production(base, candidate_base, pad) and preserves_guide(base, candidate_base, pad)

        # 공사 앞배치는 허브 다듬기가 시간 예산을 다 쓰기 전에 후보로 만든다.
        front_deadline = min(search_deadline, time.perf_counter() + min(2., max(0., seconds) * .3))
        for origins, turns in construction_front.candidates(
            grid, pieces, origin0, launcher, entrance, preserves_front_move, front_deadline
        ):
            add_cand(origins, turns)
            starts.append((origins, turns, {index for index, piece in pieces.items() if piece.unfinished}))

    # 단순히 생산 범위 안의 빈칸을 복구하는 일은 시간 제한 무작위 탐색의 성공 여부에 맡기지 않는다.
    restored_origin = None
    if preset in ("effect", "guide"):
        restored = _repair_unused_producer_tiles(base, grid, pieces, origin0, scorer, pad, search_deadline)
        if restored != origin0:
            add_cand(restored, {})
            restored_origin = cands[-1][0]
            starts.append((restored, {}, set()))
    producer_ids = []
    producer_targets = {}
    if guide:
        from .layout_city import PRODUCERS

        producer_ids = [
            index
            for index, piece in pieces.items()
            if piece.type in PRODUCERS and piece.factor >= 1.0 and piece.movable
        ]
        producer_targets = {kind: list(scorer.targets[kind]) for kind in set(PRODUCERS.values())}

    def producer_coverage(orig, turn):
        sp = shaped_of(turn)
        result = {}
        for index in producer_ids:
            piece = sp[index]
            x, y = grid.center(*orig[index], piece.w, piece.h)
            kind = PRODUCERS[piece.type]
            result[index] = sum(
                target_in_range(
                    grid.center(*orig[target_id], sp[target_id].w, sp[target_id].h)[0] - x,
                    grid.center(*orig[target_id], sp[target_id].w, sp[target_id].h)[1] - y,
                    piece.range + pad,
                    sp[target_id],
                    pad,
                )
                for target_id in producer_targets[kind]
                if target_id != index
            )
        return result

    producer0 = producer_coverage(origin0, {})
    production_pin: Set[int] = set()
    if guide:
        raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
        # 게임이 직접 센 현재 범위와 기하 계산이 일치할 때만 가상 자리 후보를 만든다.
        # 후보 자리는 게임 값을 읽을 수 없으므로 이동 뒤 다시 게임의 in_range 로 검증해야 한다.
        producer_ids = [
            index
            for index in producer_ids
            if isinstance(raw.get(index, {}).get("in_range"), dict)
            and bool(raw[index]["in_range"])
            and all(isinstance(value, int) and value >= 0 for value in raw[index]["in_range"].values())
            and sum(raw[index]["in_range"].values()) == producer0[index]
        ]
        producer0 = {index: producer0[index] for index in producer_ids}
        for index in producer_ids:
            if pieces[index].unfinished:
                continue
            production_pin.add(index)
            x, y = lay0.center(index)
            production_pin.update(
                building_type
                for building_type in producer_targets[PRODUCERS[pieces[index].type]]
                if target_in_range(
                    lay0.center(building_type)[0] - x,
                    lay0.center(building_type)[1] - y,
                    pieces[index].range + pad,
                    pieces[building_type],
                    pad,
                )
            )
        # 가동 중 생산 구역을 보존하는 시작점. 뒤에서 탈락시키기만 하면 모든 탐색이 밀밭을 빼는 데 낭비된다.
        starts[0] = (origin0, {}, production_pin)

    def producer_gain(orig, turn):
        after = producer_coverage(orig, turn)
        if any(after[index] < count for index, count in producer0.items()):
            return 0
        return max((after[index] - count for index, count in producer0.items()), default=0)

    house_counts = {}
    if guide:
        # 이미 배정된 생산 건물이 자기 자원 타일을 거의 못 쓰면, 건물 하나만 빈 자리로 옮기는 후보를 먼저 넣는다.
        # 실제 기지: 채석장 범위 바위 1/12, 빈 자리 한 곳으로 옮기면 4/12인데 전체 점수 2% 문턱에 묻혔다.
        for index in producer_ids:
            if time.perf_counter() >= search_deadline:
                break
            piece = pieces[index]
            kind = PRODUCERS[piece.type]
            best_spot = None
            for at in sorted(grid.tiles):
                if time.perf_counter() >= search_deadline:
                    break
                cells = {(at[0] + delta_x, at[1] + delta_y) for delta_x, delta_y in piece.rel}
                if (
                    not cells <= grid.tiles
                    or cells & entrance
                    or any(lay0.occ.get(c) not in (None, index) for c in cells)
                ):
                    continue
                x, y = grid.center(*at, piece.w, piece.h)
                covered = sum(
                    target_in_range(
                        grid.center(
                            *origin0[building_type], pieces[building_type].w, pieces[building_type].h
                        )[0]
                        - x,
                        grid.center(
                            *origin0[building_type], pieces[building_type].w, pieces[building_type].h
                        )[1]
                        - y,
                        piece.range + pad,
                        pieces[building_type],
                        pad,
                    )
                    for building_type in producer_targets[kind]
                    if building_type != index
                )
                if covered < producer0[index] + 2:
                    continue
                key = (covered, -abs(at[0] - origin0[index][0]) - abs(at[1] - origin0[index][1]))
                if best_spot is None or key > best_spot[0]:
                    best_spot = (key, at)
            if best_spot is not None:
                add_cand({**origin0, index: best_spot[1]}, {})
        # 가이드 배치: 허브 규칙을 먼저 고친 배치에서도 담금질을 시작한다 — 담금질만으로는 '다른 건물을 비켜야
        # 들어가는' 거처를 못 넣었다 (실제 기지: 잔병의 오두막 범위 거처 7/12 에서 멈춤 → 고치면 10/12, 12번 옮김)
        from .layout_guide import coverage, covered_members, hub_groups, repair

        groups = hub_groups(pieces, scorer)
        group0 = covered_members(grid, pieces, origin0, groups, pad)
        # 거처를 생산 구역 옆으로 옮기는 공략 규칙을 직접 후보화한다.
        # 범위 수치는 게임 현재 계수와 맞을 때만 사용하며, 다른 허브 효과를 잃는 후보는 아래에서 거른다.
        house_candidates = []
        for index, piece in pieces.items():
            if time.perf_counter() >= search_deadline:
                break
            eff = EFFECTS.get(piece.type)
            count = raw.get(index, {}).get("in_range")
            if (
                not eff
                or not isinstance(eff[0], int)
                or eff[3] != "upgraded"
                or piece.factor < 1.0
                or not piece.movable
                or not isinstance(count, dict)
                or not count
                or not all(isinstance(value, int) and value >= 0 for value in count.values())
            ):
                continue
            targets = scorer.targets[eff[0]]

            def reached(at):
                x, y = grid.center(*at, piece.w, piece.h)
                return sum(
                    target_in_range(
                        lay0.center(target_id)[0] - x,
                        lay0.center(target_id)[1] - y,
                        piece.range + pad,
                        pieces[target_id],
                        pad,
                    )
                    for target_id in targets
                )

            n0 = reached(origin0[index])
            if n0 != sum(count.values()):
                continue
            house_counts[index] = (eff[0], n0)
            house_spots = []
            for at in sorted(grid.tiles):
                if time.perf_counter() >= search_deadline:
                    break
                cells = {(at[0] + delta_x, at[1] + delta_y) for delta_x, delta_y in piece.rel}
                if (
                    not cells <= grid.tiles
                    or cells & entrance
                    or any(lay0.occ.get(c) not in (None, index) for c in cells)
                ):
                    continue
                n = reached(at)
                if n <= n0:
                    continue
                origins = {**origin0, index: at}
                members = covered_members(grid, pieces, origins, groups, pad)
                if any(not previous <= members[height] for height, previous in group0.items()):
                    continue
                key = (
                    sum(map(len, members.values())),
                    n,
                    -abs(at[0] - origin0[index][0]) - abs(at[1] - origin0[index][1]),
                )
                house_spots.append((key, origins))
            # 범위가 같아도 충돌 위치가 달라 채집 경로는 다르다. 가장 가까운 한 자리로 확정하지 않는다.
            # 6은 검색량 제한이며 게임 효과 수치가 아니다. 각 후보는 아래 채집·도달 검사로 비교한다.
            house_spots.sort(key=lambda item: item[0], reverse=True)
            for _, origins in house_spots[:6]:
                house_candidates.append(origins)
                add_cand(origins, {})
        variants = []
        if production_pin:
            protected = pinned_of({}, production_pin)
            variants.append((protected, groups, lane))
            # 건설 앞구역을 강제하는 단계가 안전한 거처 개선까지 취소하지 않도록 허브별 후보도 비교한다.
            variants.extend((protected, [group], {}) for group in groups)
        variants.append((pieces, groups, lane))
        for repair_pieces, repair_groups, repair_lane in variants:
            if time.perf_counter() >= search_deadline:
                break
            rep = repair(
                grid,
                repair_pieces,
                origin0,
                repair_groups,
                pad,
                repair_lane,
                entrance,
                deadline=search_deadline,
            )
            if rep is None or (rep[0] == origin0 and not rep[1]):
                continue
            # 고친 배치에서 시작하는 담금질은 규칙을 지킨 허브·거처를 묶고 나머지(자원 타일 등)만 옮긴다
            # (묶지 않으면 거처를 범위 밖으로 빼서 효과를 올렸다: 범위 거처 12 → 8)
            from .layout_guide import satisfied

            pin = satisfied(grid, shaped_of(rep[1]), rep[0], groups, pad, lane)
            if repair_pieces is not pieces:
                pin.update(production_pin)
            rep_pieces = shaped_of(rep[1])
            rep_lay = Layout(grid, rep_pieces, rep[0])
            pin.update(
                index
                for index, piece in rep_pieces.items()
                if piece.unfinished and any(lane.get(column, 0.0) > 0 for column in rep_lay.cells(index))
            )
            starts.append((rep[0], rep[1], pin))
            add_cand(rep[0], rep[1])
            lay = Layout(grid, pinned_of(rep[1], pin), rep[0])  # 담금질 없이 다듬기만 (채집 발사를 덜 흔든다)
            remaining = search_deadline - time.perf_counter()
            if remaining > 0:
                polish(lay, scorer, origin0, min(RESTART_SECONDS, remaining))
            add_cand(lay.origin, rep[1])
    search_note = ""
    from . import native_layout

    if native_layout._lib() is not None:
        # 네이티브 담금질은 초당 약 180만 번 (파이썬 약 2천 번) — 0.5초 한 번이면 파이썬 3초보다 좋은 배치로
        # 수렴한다 (실제 기지 4곳). 고정 시간 대신: 시작점을 바꿔 가며 돌리고, 연속 CONVERGED 번 더 좋은 배치가
        # 안 나오면 멈춘다 (seconds 는 안전 상한). 경우의 수가 너무 많아 '전부 보고 최적 증명'은 불가능.
        best, stale, step_index = -1e18, 0, 0
        while time.perf_counter() < search_deadline and (
            step_index < max(restarts, MIN_RESTARTS) or stale < CONVERGED
        ):
            step_index += 1
            so, st, pin = starts[step_index % len(starts)]
            lay = Layout(grid, pinned_of(st, pin), so)
            origins, _ = anneal(
                lay,
                scorer,
                min(RESTART_SECONDS * 0.8, search_deadline - time.perf_counter()),
                rng,
                origin0=origin0,
            )
            lay = Layout(grid, pinned_of(st, pin), origins)
            remaining = search_deadline - time.perf_counter()
            value = (
                polish(lay, scorer, origin0, min(RESTART_SECONDS * 0.2, remaining))
                if remaining > 0
                else _objective(lay, scorer, origin0)
            )
            add_cand(lay.origin, st)
            if value > best + max(1e-6, abs(best) * 0.001):
                best, stale = value, 0
            else:
                stale += 1
            if time.perf_counter() >= search_deadline:
                break
        search_note = (
            tr("탐색 {k}번 (연속 {stale}번 더 좋은 배치 없음 — 수렴)", k=step_index, stale=stale)
            if stale >= CONVERGED
            else tr("탐색 {k}번 (시간 상한)", k=step_index)
        )
        if step_index == 0:
            search_note = ""  # 준비 단계에서도 후보를 비교한다. 담금질 0회를 전체 탐색 0회로 표시하지 않는다.
        restarts = 0
    for step_index in range(restarts):
        remaining = search_deadline - time.perf_counter()
        if remaining <= 0:
            break
        current_scorer = scorer
        so, st, pin = starts[(step_index + 1) % len(starts)]
        lay = Layout(grid, pinned_of(st, pin), so)
        allotment = remaining / max(1, restarts - step_index)
        origins, _ = anneal(lay, current_scorer, allotment * 0.7, rng, origin0=origin0)
        lay = Layout(grid, pinned_of(st, pin), origins)
        remaining = search_deadline - time.perf_counter()
        if remaining > 0:
            polish(lay, current_scorer, origin0, min(allotment * 0.3, remaining))
        add_cand(lay.origin, st)
    buildings = buildings_from_base(base)

    def to_base(orig: Dict[int, Tuple[int, int]]) -> dict:
        turn = turn_of.get(id(orig))
        if turn:  # 회전해서 놓는 건물이 있으면 충돌 모양도 돌린다
            L = Layout(grid, shaped_of(turn), orig)
            return final_base(
                base,
                FullPlan(
                    origin0, orig, {index: L.center(index) for index in orig}, 0.0, 0.0, {}, {}, turned=turn
                ),
            )
        candidate_geometry = geometry
        current = dict(buildings)
        out_b = []
        for b in base.get("buildings") or []:
            index = b.get("id")
            if index in orig and orig[index] != origin0.get(index):
                piece = pieces[index]
                center_x, center_y = grid.center(orig[index][0], orig[index][1], piece.w, piece.h)
                candidate_geometry = move_geo(candidate_geometry, current, index, (center_x, center_y))
                ob = current[index]
                current[index] = Bld(ob.id, ob.type, center_x, center_y, ob.tw, ob.th, ob.rot, ob.range)
                out_b.append(dict(b, x=round(center_x, 3), y=round(center_y, 3)))
            else:
                out_b.append(b)
        return dict(base, buildings=out_b, geo=candidate_geometry)

    h0 = harvest_eval(geometry) if harvest_eval else None
    rw = res_weight or {}
    wsum = lambda v: sum(v[row] * rw.get(row, 1.0) for row in (1, 2, 3))
    best = None
    alt = None  # 범위 효과만 가장 좋은 후보 (채집 발사 때문에 떨어진 것)
    notes: List[str] = [search_note] if search_note else []
    # 이전 목표 배치(재배치 도중일 수 있음)도 후보로 — 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다
    # (실제 사용: 재배치 도중 다시 계산해 목표가 바뀌면서 채석장 둘레 돌을 빼게 됨)
    prefer_orig = None
    if prefer:
        po = dict(origin0)
        pturn: Dict[int, int] = {}
        for index, value in prefer.items():
            if index in pieces:
                x, y = value[0], value[1]
                step_index = int(value[2]) % 4 if len(value) > 2 else 0
                if step_index:
                    pturn[index] = step_index
                piece = rotated(pieces[index], step_index)
                po[index] = (
                    round((x - piece.w * grid.size / 2 - grid.ox) / grid.size),
                    round((y - piece.h * grid.size / 2 - grid.oy) / grid.size),
                )
        sp = shaped_of(pturn)
        cells = (
            [column for index in po for column in Layout(grid, sp, {}).cells(index, po[index])]
            if pieces
            else []
        )
        ok = (
            len(cells) == len(set(cells))
            and set(cells) <= grid.tiles
            and all(
                po[index] == origin0[index] and not pturn.get(index)
                for index in po
                if not pieces[index].movable
            )
        )
        if ok and (po != origin0 or pturn):
            prefer_orig = po
            cands.append((po, scorer.score(Layout(grid, sp, po))[0]))
            if pturn:
                turn_of[id(po)] = pturn
    totals = {}
    best_cov = (0, 0, (), 0, 0)
    # 현재 안만 도달 검사를 면제해 두면, 더 가까워도 실제로 못 치는 원위치가 계속 이긴다.
    current_reachable = (
        reach_ok(base) if guide and reach_ok is not None and any(piece.unfinished for piece in pieces.values())
        else True
    )

    def cov_of(origins):
        if not guide:
            return (0, 0, (), 0, 0)
        sp = shaped_of(turn_of.get(id(origins), {}))
        lay = Layout(grid, sp, origins)
        build_front = sum(
            1
            for index, piece in sp.items()
            if piece.unfinished and any(lane.get(column, 0.0) > 0 for column in lay.cells(index))
        )
        occupied = {column for index in origins for column in lay.cells(index)}
        clear = len(entrance - occupied)
        # 입구→실제 도달→공사 거리→허브 순서. 기존 허브 효과·생산은 위의 보존 검사로 보호한다.
        proximity = construction_front.priority(grid, sp, origins, launcher)
        reachable = current_reachable if origins == origin0 and not turn_of.get(id(origins)) else True
        return (clear, int(reachable), proximity, coverage(grid, sp, origins, groups, pad),
                build_front if reach_ok is None else 0)

    cov0 = cov_of(origin0)
    plain0 = plain.score(lay0)[0]

    def house_gain(orig):
        """게임과 맞춘 자원 거처의 빈 범위를 살리는 개선은 전체 점수 2%에 묻지 않는다."""
        sp = shaped_of(turn_of.get(id(orig), {}))
        lay = Layout(grid, sp, orig)
        gained = 0
        for index, (kind, n0) in house_counts.items():
            x, y = lay.center(index)
            n1 = sum(
                target_in_range(
                    lay.center(target_id)[0] - x,
                    lay.center(target_id)[1] - y,
                    sp[index].range + pad,
                    sp[target_id],
                    pad,
                )
                for target_id in scorer.targets[kind]
            )
            if n1 < n0:
                return 0
            gained += n1 - n0
        return gained

    def meaningful_guide(orig, hv):
        """이전 목표의 남은 이동이 실제 범위·채집·생산 규칙 중 하나라도 개선하는지."""
        turn = turn_of.get(id(orig), {})
        if (
            orig == restored_origin
            or cov_of(orig) > cov0
            or producer_gain(orig, turn) >= 2
            or house_gain(orig) > 0
        ):
            return True
        if plain.score(Layout(grid, shaped_of(turn), orig))[0] > plain0 + 1e-6:
            return True
        return bool(hv and h0 and wsum(hv) > wsum(h0))

    # 우선순위가 더 낮은 안은 수확 점수가 높아도 이길 수 없다. 앞배치부터 검증해 불필요한 재계산을 줄인다.
    ordered_candidates = [cands[0]] + sorted(cands[1:], key=lambda candidate: cov_of(candidate[0]), reverse=True)
    for orig, s in ordered_candidates:
        current = orig is cands[0][0]
        cov = cov_of(orig)
        if guide and best is not None and cov < best_cov:
            continue
        nb = to_base(orig)
        if guide and not current:
            from .layout_guide import preserves_guide

            if not preserves_production(base, nb, pad) or not preserves_guide(base, nb, pad):
                continue
            if not move_sequence(grid, pieces, origin0, orig, turn_of.get(id(orig)), entrance).complete:
                continue  # 실행 불가능한 최상위 안 때문에 가능한 차선책까지 버리지 않는다
        hv = harvest_eval(nb["geo"]) if harvest_eval and not current else h0
        if reach_ok is not None and not current and not reach_ok(nb):
            continue  # 미완성 건물로 가는 길을 막는 배치는 뺀다
        rel_e = s / e0 if e0 > 0 else (1.0 + s)
        rel_h = ((wsum(hv) + HARVEST_DAMP) / (wsum(h0) + HARVEST_DAMP)) if hv and h0 else 1.0
        halved = bool(
            hv and h0 and not current and any(h0[row] > 0 and hv[row] < 0.5 * h0[row] for row in (1, 2, 3))
        )
        if not current and (alt is None or rel_e > alt[0]):
            alt = (rel_e, rel_h)
        if halved:
            continue  # 채집 발사로 얻던 자원 하나가 절반 아래로 줄면 뺀다
        tn = turn_of.get(id(orig), {})
        total = (
            rel_e
            + rel_h
            - SELECT_MOVE_COST
            * sum(1 for index in orig if orig[index] != origin0.get(index) or tn.get(index))
        )
        totals[id(orig)] = (total, orig, s, hv)
        # 가이드 배치: 입구·공사 접근 우선순위가 같으면 효과·채집·옮기는 수를 비교한다.
        if best is None or (cov, total) > (best_cov, best[0]):
            best, best_cov = (total, orig, s, hv), cov
    if best is None:
        return None
    _, orig, s, hv = best
    if prefer_orig is not None and id(prefer_orig) in totals and best[1] is not prefer_orig:
        pt = totals[id(prefer_orig)]
        if best[0] < pt[0] * 1.02 and (
            not guide
            or (
                cov_of(prefer_orig) >= best_cov
                and producer_gain(prefer_orig, turn_of.get(id(prefer_orig), {}))
                >= producer_gain(best[1], turn_of.get(id(best[1]), {}))
                and house_gain(prefer_orig) >= house_gain(best[1])
                and meaningful_guide(prefer_orig, pt[3])
            )
        ):
            best = pt
            notes.append(tr("이전 목표 배치를 유지했습니다 (새 계산의 개선이 2% 미만)."))
    _, orig, s, hv = best
    if guide and best[1] is prefer_orig and not meaningful_guide(orig, hv):
        best = totals[id(cands[0][0])]
        _, orig, s, hv = best
    if best[1] is prefer_orig and prefer_orig is not None:
        pass
    elif (
        best[0] < 2.0 * 1.02
        and orig != restored_origin
        and not (
            guide
            and (
                cov_of(orig) > cov0
                or house_gain(orig) > 0
                or producer_gain(orig, turn_of.get(id(orig), {})) >= 2
            )
        )
    ):
        notes.append(tr("이번 탐색에서 범위 효과와 채집 예상량을 합쳐 2% 넘는 개선을 찾지 못했습니다."))
        if alt is not None and alt[0] > 1.02:
            notes.append(
                tr(
                    "범위 효과만 보면 +{v0:.0f}% 배치가 있지만 채집 발사량이 {v1:+.0f}% 라 권하지 않음",
                    v0=(alt[0] - 1) * 100,
                    v1=(alt[1] - 1) * 100,
                )
            )
        orig, s, hv = cands[0][0], e0, h0
    if preset == "gold_u":
        # 프리셋은 사용자가 고른 틀: 2% 기준·'지금이 최적' 판단 없이 가장 좋은 프리셋 후보를 쓴다
        cand_best = max(cands[1:], key=lambda c: c[1]) if len(cands) > 1 else cands[0]
        orig, s, hv = (
            cand_best[0],
            cand_best[1],
            (harvest_eval(to_base(cand_best[0])["geo"]) if harvest_eval else None),
        )
        notes = [
            tr(
                "금광 U자: 발사대 앞 자리 {v0}곳 중 {v1}곳에 금광",
                v0=len(preset_spots),
                v1=sum(
                    1
                    for index, piece in pieces.items()
                    if piece.type == "kGoldMine" and orig.get(index) in set(preset_spots)
                ),
            )
        ]
    turn = dict(turn_of.get(id(orig), {}))
    shaped = shaped_of(turn)
    lay = Layout(grid, shaped, orig)
    if lane:

        def front(origins, sp=pieces):
            L = Layout(grid, sp, origins)
            return sum(
                lane.get(column, 0.0)
                for index in laned.lane_ids
                if index in origins
                for column in L.cells(index)
            )

        def tiles(origins, sp=pieces):
            L = Layout(grid, sp, origins)
            return sum(
                1
                for index in laned.lane_tiles
                if index in origins and any(lane.get(column, 0.0) > 0 for column in L.cells(index))
            )

        f0, f1 = front(origin0), front(orig, shaped)
        if f0 - f1 >= 1.0:
            notes.append(
                tr(
                    "능력치·거처처럼 치여도 얻는 게 없는 건물을 발사대 앞에서 뒤·구석으로 (앞 구역 막음 {f0:.1f} → {f1:.1f})",
                    f0=f0,
                    f1=f1,
                )
            )
        t0, t1 = tiles(origin0), tiles(orig, shaped)
        if t1 - t0 >= 2:
            notes.append(
                tr(
                    "자원 타일을 발사대 앞으로 (앞 구역 자원 타일 {t0} → {t1}개 — 공을 던져 캐는 몫)",
                    t0=t0,
                    t1=t1,
                )
            )
    # 보고는 범위 효과만 (앞 구역 점수는 배치를 고를 때만 쓴다)
    e0, d0 = plain.score(Layout(grid, pieces, origin0))
    s, d1 = plain.score(lay)
    moved = sum(1 for index in orig if orig[index] != origin0[index] or turn.get(index))
    if guide:
        from .layout_city import guide_report

        head = [
            tr(
                "가이드 배치: 지금 배치에서 출발해 핵심 범위 효과·생산 구역·입구를 보존하며 {v0}개 옮김",
                v0=moved,
            )
        ]
        if turn:
            head.append(tr("{v0}개는 회전해서 놓아야 빈틈 없이 맞물림 (ㄱ·ㅜ 자 모양 포함)", v0=len(turn)))
        notes = head + notes + guide_report(grid, shaped, orig, plain, pad)
    return FullPlan(
        origin0,
        orig,
        {index: lay.center(index) for index in orig},
        e0,
        s,
        d0,
        d1,
        h0,
        hv,
        moved,
        notes,
        turned=turn,
    )


def _optimize_city(
    base: dict,
    grid: Grid,
    pieces: Dict[int, Piece],
    origin0: Dict[int, Tuple[int, int]],
    scorer: Scorer,
    plain: Scorer,
    harvest_eval: Optional[Callable[[dict], List[int]]],
    pad: float,
    hit: Set[int] = frozenset(),
) -> FullPlan:
    """가이드 배치: Steam 공략 3개 규칙으로 다시 짠다 (layout_city.plan_city)."""
    from .layout_city import plan_city

    geometry = base.get("geo") or {}
    launcher = geometry.get("launcher") or []
    lrc = (
        (int((float(launcher[0]) - grid.ox) // grid.size), int((float(launcher[1]) - grid.oy) // grid.size))
        if len(launcher) >= 2
        else None
    )
    orig, notes, turn = plan_city(grid, pieces, origin0, lrc, scorer, pad, hit)
    shaped = {index: rotated(piece, turn.get(index, 0)) for index, piece in pieces.items()}
    orig = canonicalize(shaped, origin0, orig)
    e0, d0 = plain.score(Layout(grid, pieces, origin0))
    lay = Layout(grid, shaped, orig)
    s, d1 = plain.score(lay)
    centers = {index: lay.center(index) for index in orig}
    h0 = hv = None
    if harvest_eval:
        h0 = harvest_eval(geometry)
        hv = harvest_eval(
            final_base(base, FullPlan(origin0, orig, centers, 0, 0, {}, {}, turned=turn)).get("geo") or {}
        )
    moved = sum(1 for index in orig if orig[index] != origin0[index] or index in turn)
    return FullPlan(origin0, orig, centers, e0, s, d0, d1, h0, hv, moved, notes, turned=turn)


RANGE_TARGETS = {1: WHEAT_T, 2: WOOD_T, 3: STONE_T}
SHAPES = ("square", "circle")
PAD_CANDIDATES = (0.0, 0.5625, 1.125, 1.6875)  # 0 / 반 타일 / 한 타일 / 한 타일 반


def _stat_types(base: dict) -> Set[str]:
    from .construction_policy import has_captain_targets

    # 직접 '없음'이라고 받은 값은 옛 버전용 기본 목록으로 뒤집지 않는다.
    return {
        building_type
        for building_type in {b.get("type") for b in base.get("buildings") or []}
        if building_type and has_captain_targets({building_type}, base, STAT_FALLBACK)
    }


def calibrate_range(base: dict) -> Tuple[float, int, int]:
    """게임이 직접 센 '범위 안 자원 타일 수'(플러그인 1.9 in_range)와 가장 잘 맞는 범위 여유를 고른다.
    돌려주는 값: (여유, 맞은 수, 비교한 수). 게임 값이 없으면 (0, 0, 0)."""
    buildings = buildings_from_base(base)
    raw = [
        b
        for b in base.get("buildings") or []
        if isinstance(b.get("in_range"), dict) and b.get("id") in buildings
    ]
    if not raw:
        return 0.0, 0, 0
    global RANGE_SHAPE
    keep = RANGE_SHAPE
    best = None
    for shape, pad in [(sh, pd) for sh in SHAPES for pd in PAD_CANDIDATES]:
        RANGE_SHAPE = shape
        ok = checked_count = 0
        for b in raw:
            effect_building = buildings[b["id"]]
            eff = EFFECTS.get(effect_building.type)
            if not eff or not isinstance(eff[0], int):
                continue
            game = sum(value for value in b["in_range"].values() if isinstance(value, int))
            from .game_range import row_in_range

            by_id = {row["id"]: row for row in base.get("buildings", []) if "id" in row}
            mine = sum(
                1
                for target_building in buildings.values()
                if target_building.type in RANGE_TARGETS[eff[0]]
                and target_building.id != effect_building.id
                and row_in_range(b, by_id[target_building.id], pad)
            )
            checked_count += 1
            ok += int(game == mine)
        if best is None or ok > best[1]:
            best = (pad, ok, checked_count, shape)
    RANGE_SHAPE = best[3] if best[1] > 0 else keep  # 게임 값과 가장 잘 맞는 모양으로 (이 프로세스 안에서)
    return best[:3]


def suggest_builds(
    base: dict,
    blueprints: Sequence[dict],
    res_weight: Optional[Dict[int, float]] = None,
    pad: float = 0.0,
    seconds: float = 2.5,
    *,
    resources: Optional[Sequence[int]] = None,
) -> List[Tuple[str, Tuple[float, float], Tuple[int, int], float, int, int]]:
    """게임이 건설 가능하다고 보낸 범위 효과 건물을 현재 배치의 빈 자리에 넣었을 때의 효과.
    크기와 레벨 0 범위를 확인할 수 있는 항목만 계산하고, 안내 없는 주변 재배치는 포함하지 않는다.
    새 생산 건물은 일꾼 배정을 마친 것으로 보고 센다 (지은 뒤 할 일).
    돌려주는 값: [(종류, 중심, 크기, 늘어나는 점수, 범위 안 대상 수, 함께 옮길 건물 수)]."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    base_score = Scorer(pieces, stats, housing, res_weight, pad).score(Layout(grid, pieces, origin))[0]
    occupied_cells = Layout(grid, pieces, origin).occ
    from .construction_policy import is_recommended_building

    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    ranges = {
        piece.type: piece.range
        for index, piece in pieces.items()
        if piece.range > 0 and raw.get(index, {}).get("lvl") == 0
    }
    protected = entrance_cells(base.get("geo") or {}, grid)
    result = []
    seen = set()
    for blueprint in blueprints:
        building_type = blueprint.get("type", "")
        if (
            building_type not in EFFECTS
            or building_type in seen
            or not is_recommended_building(building_type)
            or blueprint.get("can_build_more") is False
        ):
            continue
        seen.add(building_type)
        existing = [b for b in raw.values() if b.get("type") == building_type]
        if type(blueprint.get("max_instances")) is int and len(existing) >= blueprint["max_instances"]:
            continue
        if any(b.get("state") in UNFINISHED_STATES for b in existing):
            continue
        if EFFECTS[building_type][3] == "worker" and any(
            not isinstance(b.get("worker"), int) or b["worker"] < 0 for b in existing
        ):
            continue
        size = blueprint.get("size")
        cost = blueprint.get("cost")
        radius = blueprint.get("range") or ranges.get(building_type)
        if (
            not size
            or len(size) != 2
            or not all(isinstance(value, int) and value > 0 for value in size)
            or not radius
        ):
            continue
        if (
            not isinstance(cost, (list, tuple))
            or len(cost) != 4
            or not all(isinstance(value, int) and value >= 0 for value in cost)
        ):
            continue
        if resources is not None and any(
            index >= len(resources) or resources[index] < value for index, value in enumerate(cost)
        ):
            continue
        width, height = size
        new_id = -1
        piece = Piece(
            new_id,
            building_type,
            width,
            height,
            frozenset((delta_x, delta_y) for delta_x in range(width) for delta_y in range(height)),
            True,
            float(radius),
            1.0,
        )
        current_pieces = dict(pieces)
        current_pieces[new_id] = piece
        scorer = Scorer(current_pieces, stats, housing, res_weight, pad)
        best = None
        for c, row in grid.tiles:
            cells = [(c + delta_x, row + delta_y) for delta_x, delta_y in piece.rel]
            if any(x not in grid.tiles or x in occupied_cells or x in protected for x in cells):
                continue
            origins = dict(origin)
            origins[new_id] = (c, row)
            score, details = scorer.score(Layout(grid, current_pieces, origins))
            if best is None or score > best[0]:
                x, y = grid.center(c, row, width, height)
                covered = sum(
                    target_in_range(
                        grid.center(*origin[other_index], pieces[other_index].w, pieces[other_index].h)[0]
                        - x,
                        grid.center(*origin[other_index], pieces[other_index].w, pieces[other_index].h)[1]
                        - y,
                        piece.range + pad,
                        pieces[other_index],
                        pad,
                    )
                    for other_index in scorer.targets[EFFECTS[building_type][0]]
                    if other_index in origin
                )
                best = (score, (c, row), covered)
        if best is None:
            continue
        origins = dict(origin)
        origins[new_id] = best[1]
        # 건설 안내에는 기존 건물을 옮기는 순서가 없다. 안내하지 않은 대규모 재배치 이득을 끼워 넣지 않는다.
        lay, score, details = Layout(grid, current_pieces, origins), best[0], {building_type: best[2]}
        if score - base_score > 0.05:
            no = lay.origin[new_id]
            result.append(
                (
                    building_type,
                    grid.center(no[0], no[1], width, height),
                    (width, height),
                    score - base_score,
                    int(details.get(building_type, 0)),
                    0,
                )
            )
    result.sort(key=lambda x: -x[3])
    return result


def suggest_tiles(
    base: dict,
    res_weight: Optional[Dict[int, float]] = None,
    pad: float = 0.0,
    max_each: int = 8,
    *,
    build_options: Sequence[dict] = (),
    resources: Optional[Sequence[int]] = None,
) -> List[Tuple[str, Tuple[float, float], Tuple[int, int], float, int, int]]:
    """자원 타일을 더 사서 생산 건물 범위의 빈칸을 채우면 늘어나는 값 (사용자: '채석장 빈칸에 돌 넣는 게 낫지 않나').
    게임의 추가 건설 목록·크기·현재 비용만 사용한다. 상위형이 있으면 하위형을 대신 권하지 않는다.
    내부 점수는 빈자리 선택용이며 새 타일의 생산량 예측으로 표시하지 않는다.
    돌려주는 값: [(종류, 첫 자리 중심, 크기, 배치 우선순위, 놓을 개수, 0)]."""
    from .construction_policy import is_recommended_building, preferred_tile_types
    from .game_range import boxes_from_row

    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    have = {piece.type for piece in pieces.values()}
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    sources = {}
    for index, piece in pieces.items():
        eff = EFFECTS.get(piece.type)
        counted = raw.get(index, {}).get("in_range")
        if (
            not eff
            or not isinstance(eff[0], int)
            or piece.factor < 1
            or not isinstance(counted, dict)
            or not counted
        ):
            continue
        if not all(isinstance(value, int) and value >= 0 for value in counted.values()):
            continue
        x, y = grid.center(*origin[index], piece.w, piece.h)
        actual = sum(
            target_in_range(
                grid.center(*origin[other_index], other_piece.w, other_piece.h)[0] - x,
                grid.center(*origin[other_index], other_piece.w, other_piece.h)[1] - y,
                piece.range + pad,
                other_piece,
                pad,
            )
            for other_index, other_piece in pieces.items()
            if TILE_RES.get(other_piece.type) == eff[0]
        )
        if actual == sum(counted.values()):
            sources[index] = (eff[0], x, y, piece.range + pad)
    kinds = {value[0] for value in sources.values()}
    available = {
        blueprint.get("type"): blueprint
        for blueprint in build_options
        if isinstance(blueprint, dict)
        and blueprint.get("type") in TILE_RES
        and blueprint.get("can_build_more") is not False
        and is_recommended_building(blueprint.get("type", ""))
    }
    selected = preferred_tile_types(available, have)
    protected = entrance_cells(base.get("geo") or {}, grid)
    remaining = list(resources) if resources is not None else None
    current_pieces, origins = dict(pieces), dict(origin)
    result = []
    for building_type in sorted(selected, key=lambda t: (-(res_weight or {}).get(TILE_RES[t], 1.0), t)):
        if TILE_RES[building_type] not in kinds:
            continue
        option = available[building_type]
        size, cost = option.get("size"), option.get("cost")
        if (
            not isinstance(size, (list, tuple))
            or len(size) != 2
            or not all(isinstance(value, int) and value > 0 for value in size)
        ):
            continue
        if (
            not isinstance(cost, (list, tuple))
            or len(cost) != 4
            or not all(isinstance(value, int) and value >= 0 for value in cost)
        ):
            continue
        s0 = Scorer(current_pieces, stats, housing, res_weight, pad).score(
            Layout(grid, current_pieces, origins)
        )[0]
        total, first, n = 0.0, None, 0
        limit = option.get("max_instances")
        available_count = (
            max_each
            if type(limit) is not int
            else max(0, min(max_each, limit - sum(b.get("type") == building_type for b in raw.values())))
        )
        for step_index in range(available_count):
            if remaining is not None and any(
                index >= len(remaining) or remaining[index] < value for index, value in enumerate(cost)
            ):
                break
            nid = min([-100] + list(current_pieces)) - 1
            current_pieces[nid] = Piece(
                nid,
                building_type,
                size[0],
                size[1],
                frozenset((delta_x, delta_y) for delta_x in range(size[0]) for delta_y in range(size[1])),
                True,
                0.0,
                1.0,
                1.0,
                range_boxes=boxes_from_row(option),
            )
            scorer = Scorer(current_pieces, stats, housing, res_weight, pad)
            occupied_cells = Layout(grid, {index: current_pieces[index] for index in origins}, origins).occ
            best = None
            for c, row in grid.tiles:
                cells = [(c + delta_x, row + delta_y) for delta_x, delta_y in current_pieces[nid].rel]
                if any(x not in grid.tiles or x in occupied_cells or x in protected for x in cells):
                    continue
                tx, ty = grid.center(c, row, size[0], size[1])
                if not any(
                    kind == TILE_RES[building_type]
                    and target_in_range(tx - x, ty - y, radius, current_pieces[nid], pad)
                    for kind, x, y, radius in sources.values()
                ):
                    continue
                candidate_origins = dict(origins)
                candidate_origins[nid] = (c, row)
                value = scorer.score(Layout(grid, current_pieces, candidate_origins))[0]
                if best is None or value > best[0]:
                    best = (value, (c, row))
            if best is None or best[0] - s0 < 0.05:
                del current_pieces[nid]
                break
            total += best[0] - s0
            s0 = best[0]
            origins[nid] = best[1]
            n += 1
            if remaining is not None:
                remaining = [
                    value - cost[index] if index < 4 else value for index, value in enumerate(remaining)
                ]
            if first is None:
                first = grid.center(best[1][0], best[1][1], size[0], size[1])
        if n:
            result.append((building_type, first, size, total, n, 0))
    result.sort(key=lambda x: -x[3])
    return result


def activation_gains(
    base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0
) -> List[Tuple[int, str, str, float]]:
    """효과를 절반으로 계산하는 건물(강화 전 거처, 일꾼 없는 생산 건물)을 켜면 늘어나는 점수.
    돌려주는 값: [(건물 id, 종류, 할 일(강화 또는 일꾼 배정), 늘어나는 점수)]."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    s0 = Scorer(pieces, stats, housing, res_weight, pad).score(Layout(grid, pieces, origin))[0]
    result = []
    for index, piece in pieces.items():
        if piece.type not in EFFECTS or piece.factor >= 1.0:
            continue
        current_pieces = dict(pieces)
        current_pieces[index] = Piece(
            piece.id,
            piece.type,
            piece.w,
            piece.h,
            piece.rel,
            piece.movable,
            piece.range,
            1.0,
            piece.cap,
            piece.unfinished,
            piece.range_boxes,
        )
        s1 = Scorer(current_pieces, stats, housing, res_weight, pad).score(
            Layout(grid, current_pieces, origin)
        )[0]
        if s1 - s0 > 0.05:
            ch = house_characters().get(_slug(piece.type))
            what = (
                tr("일꾼 배정")
                if EFFECTS[piece.type][3] == "worker"
                else tr(
                    "{v0} 레벨 {v1}", v0=ch[1], v1=_next_house_level(piece.type, CHAR_LEVELS.get(ch[0], 0))
                )
                if ch
                else tr("강화")
            )
            result.append((index, piece.type, what, s1 - s0))
    result.sort(key=lambda x: -x[3])
    return result


# 철거 후보 대상: 여러 개 지어도 되고, Scorer 가 값을 제대로 아는 생산 건물만.
# 뺀 것들 — 잘못 추천하면 되돌릴 수 없는 손해라 안전하게 좁힘:
#   금광(kGoldMine): Scorer 는 범위 효과만 보고 채집 발사 가치를 모름 (harvest_sim 이 따로 계산) — 항상 0으로 보여 잘못 추천함.
#   거처(주택 6종): 캐릭터 한 명에 고정 배정(house_characters)이라 보통 하나뿐이고, 철거하면 그 캐릭터 보너스를 통째로 잃음.
#   능력치·무한 강화 건물: 보통 하나뿐이고 레벨 투자가 크다.
DEMOLISH_CANDIDATE_TYPES = {"kIdleFarm", "kIdleLumberyard", "kIdleStoneMine"}
DEMOLISH_MAX_SCORE = 0.5  # 이 밑으로 기여하면 후보 (범위 안에 캘 타일이 없다는 뜻) — 근거 없이 임의로 정함
# 있기만 하면 철거 후보 — Steam 가이드·사용자 결정 (자리를 차지하고, 금광은 일꾼 캐릭터를 발사에서 뺀다)
GUIDE_DEMOLISH = {
    "kWarRoom": tr(
        "쓸모없음 — 30분에 골드 1천·돌 100, 게임을 끄면 멈춤 (Steam 가이드 Zarcos·apo·Drake 모두)"
    ),
    "kIdleLauncher": tr("효과가 별로라 안 지음 (Steam 가이드 Drake)"),
    "kGoldMine": tr(
        "무한 모드로 골드가 모자라지 않음 — 금광은 권하지 않음 (Zarcos), 일꾼을 발사에 돌릴 수 있음"
    ),
}
TILE_DEMOLISH_REASON = tr(
    "범위 안에 캘 자원 타일이 없어 지금 자리에서 거의 도움이 안 됨 — 옮기거나 철거 고려"
)


def suggest_demolish(
    base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0, limit: int = 8
) -> List[Tuple[int, str, float, str]]:
    """지금 배치에서 있으나 마나 한 생산 건물(범위 안에 캘 타일이 없어 점수에 거의 안 보탬) 철거 후보.
    돌려주는 값: [(건물 id, 종류, 지금 기여하는 점수, 이유)] — 가이드 철거 후보(GUIDE_DEMOLISH)가 먼저, 나머지는 점수가 낮을수록 위.
    공사 중인 건물은 빼고(방금 짓기 시작한 걸 철거하라고 하면 안 됨), DEMOLISH_CANDIDATE_TYPES 만 본다."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    s0 = Scorer(pieces, stats, housing, res_weight, pad).score(Layout(grid, pieces, origin))[0]
    result = [
        (index, piece.type, 0.0, GUIDE_DEMOLISH[piece.type])
        for index, piece in sorted(pieces.items())
        if piece.type in GUIDE_DEMOLISH and not piece.unfinished
    ]
    for index, piece in pieces.items():
        if piece.type not in DEMOLISH_CANDIDATE_TYPES or piece.unfinished:
            continue
        counted = raw.get(index, {}).get("in_range")
        if (
            not isinstance(counted, dict)
            or not counted
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in counted.values()
            )
            or sum(counted.values()) != 0
        ):
            continue
        # 가상 재배치에 남은 예전 직접 계수도 철거 근거로 쓰지 않는다.
        x, y = grid.center(*origin[index], piece.w, piece.h)
        resource = EFFECTS[piece.type][0]
        if any(
            TILE_RES.get(other_piece.type) == resource
            and target_in_range(
                grid.center(*origin[other_index], other_piece.w, other_piece.h)[0] - x,
                grid.center(*origin[other_index], other_piece.w, other_piece.h)[1] - y,
                piece.range + pad,
                other_piece,
                pad,
            )
            for other_index, other_piece in pieces.items()
        ):
            continue
        current_pieces = {field_name: value for field_name, value in pieces.items() if field_name != index}
        org = {field_name: value for field_name, value in origin.items() if field_name != index}
        s1 = (
            Scorer(current_pieces, stats, housing, res_weight, pad).score(Layout(grid, current_pieces, org))[
                0
            ]
            if current_pieces
            else 0.0
        )
        marginal = round(s0 - s1, 2)
        if marginal < DEMOLISH_MAX_SCORE:
            result.append((index, piece.type, marginal, TILE_DEMOLISH_REASON))
    result.sort(key=lambda x: (x[1] not in GUIDE_DEMOLISH, x[2]))
    return result[:limit]


def final_base(base: dict, plan: FullPlan) -> dict:
    """최적 배치를 적용한 기지 (건물 위치 + 충돌 모양)."""
    from .layout import move_geo, turn_geo

    geometry = base.get("geo") or {}
    grid = grid_from_geo(geometry)
    buildings = buildings_from_base(base)
    current_geometry, out_b = geometry, []
    current = dict(buildings)
    for b in base.get("buildings") or []:
        index = b.get("id")
        turn = plan.turned.get(index, 0)
        if (
            index in plan.centers_after
            and (plan.origin_after.get(index) != plan.origin_before.get(index) or turn)
            and index in current
        ):
            center_x, center_y = plan.centers_after[index]
            ob = current[index]
            if turn:
                current_geometry = turn_geo(current_geometry, index, (ob.x, ob.y), (center_x, center_y), turn)
                rot = (ob.rot + turn) % 4
            else:
                current_geometry = move_geo(current_geometry, current, index, (center_x, center_y))
                rot = ob.rot
            current[index] = Bld(ob.id, ob.type, center_x, center_y, ob.tw, ob.th, rot, ob.range)
            out_b.append(dict(b, x=round(center_x, 3), y=round(center_y, 3), rot=rot))
        else:
            out_b.append(b)
    return dict(base, buildings=out_b, geo=current_geometry)


def remaining_moves(
    base: dict, final: Dict[int, Tuple[float, float]], final_rot: Optional[Dict[int, int]] = None
) -> List[Move]:
    """지금(재배치 도중) 배치에서 목표 배치(건물별 중심)까지 남은 옮기기. 사용자가 다른 순서로 옮겨도 다시 맞춘다.
    final_rot: 건물별 목표 회전(게임 rot 값, +1 = 시계 방향 90°)."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None or not final:
        return MoveSequence(complete=not final, unresolved=sorted(final or {}))
    pieces, current = pieces_from_base(base, grid, housing_types())
    rots = {b["id"]: int(b.get("rot") or 0) for b in base.get("buildings") or [] if "id" in b}
    turn = {
        index: (rotation - rots.get(index, 0)) % 4
        for index, rotation in (final_rot or {}).items()
        if index in pieces and (rotation - rots.get(index, 0)) % 4
    }
    target = {}
    for index, (x, y) in final.items():
        if index in pieces:
            piece = rotated(pieces[index], turn.get(index, 0))
            target[index] = (
                round((x - piece.w * grid.size / 2 - grid.ox) / grid.size),
                round((y - piece.h * grid.size / 2 - grid.oy) / grid.size),
            )
    for index in (
        current
    ):  # 계획 뒤에 새로 지은 건물(계획에 없음)은 그 자리에 둔다 — 없으면 KeyError 로 기지 화면이 멈춤
        target.setdefault(index, current[index])
    return _moves(grid, pieces, current, target, turn, rots, entrance_cells(base.get("geo") or {}, grid))


def _moves(
    grid: Grid,
    pieces: Dict[int, Piece],
    current: Dict[int, Tuple[int, int]],
    target: Dict[int, Tuple[int, int]],
    turn: Dict[int, int],
    rots: Dict[int, int],
    avoid: Set[Tuple[int, int]] = frozenset(),
) -> MoveSequence:
    result = []
    sequence = move_sequence(grid, pieces, current, target, turn, avoid)
    if not sequence.complete:
        return MoveSequence(complete=False, unresolved=sequence.unresolved)
    for index, origin, park in sequence:
        k = 0 if park else turn.get(index, 0)
        piece = rotated(pieces[index], k)
        result.append(
            Move(
                index,
                grid.center(origin[0], origin[1], piece.w, piece.h),
                0.0,
                tr("잠시 비켜 두기 (자리 비우기)")
                if park
                else tr("{v0} 이 자리로", v0=turn_text(k))
                if k
                else tr("가이드 배치 자리로"),
                rot=(rots.get(index, 0) + k) % 4 if k else -1,
            )
        )
    return MoveSequence(result)


def turn_text(k: int) -> str:
    """회전 안내 (게임 회전 버튼 = 시계 방향 90°)."""
    return {1: tr("회전 버튼 1번 눌러서"), 2: tr("회전 버튼 2번 눌러서"), 3: tr("회전 버튼 3번 눌러서")}.get(
        k % 4, ""
    )


def plan_moves(plan: FullPlan, base: dict) -> List[Move]:
    """최적 배치로 가는 옮기기 (Move 목록, 잠시 비켜 두기 포함)."""
    geometry = base.get("geo") or {}
    grid = grid_from_geo(geometry)
    if grid is None:
        return MoveSequence(complete=False, unresolved=sorted(plan.origin_after))
    pieces, current = pieces_from_base(base, grid, housing_types())
    target = {index: plan.origin_after.get(index, origin) for index, origin in current.items()}
    rots = {b["id"]: int(b.get("rot") or 0) for b in base.get("buildings") or [] if "id" in b}
    return _moves(
        grid,
        pieces,
        current,
        target,
        {index: field_name for index, field_name in plan.turned.items() if index in current},
        rots,
        entrance_cells(geometry, grid),
    )
