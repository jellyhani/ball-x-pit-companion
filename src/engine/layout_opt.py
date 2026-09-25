"""전체 재배치 최적화: 기지를 다 치우고 다시 놓는다고 보고, 건물마다 효과를 최대로 하는 자리를 찾는다.

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
마지막에 후보 몇 개를 채집 궤적 계산으로 다시 비교해 (범위 효과 / 지금 값) + (채집 발사량 / 지금 값) 이 가장 큰 것을 고른다.
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

from .layout import Bld, Grid, Move, buildings_from_base, grid_from_geo, shape_masks

WHEAT_T = {"kWheatField", "kDenseWheat"}
WOOD_T = {"kForest", "kGrandTree"}
STONE_T = {"kBoulder", "kGraniteSlab", "kStonePile"}
TILE_RES = {**{t: 1 for t in WHEAT_T}, **{t: 2 for t in WOOD_T}, **{t: 3 for t in STONE_T}}
# 능력치 보너스 건물 12개 (대위 막사 대상 — Steam 가이드 '100% Utilization': '12개 능력치 건물을 모두 범위 안에').
# +1 능력치 6개(병영·의료원·영사관·총대장간·사택·구두장이의 집) + 무한 강화 6개. 성장률(스케일링)만 올리는 연금술 공방·
# 양궁장·외교 회관·군사 학교·대학·바퀴 공방은 제외. 플러그인 1.8 의 게임 값(stat)이 있으면 그걸 쓴다.
STAT_FALLBACK = {"kBarracks", "kClinic", "kConsulate", "kGunsmith", "kSchoolhouse", "kShoemaker",
                 "kEnduranceStatue", "kStrengthStatue", "kLeadershipStatue", "kSpeedStatue", "kDexterityStatue",
                 "kIntelligenceStatue"}
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
HARVEST_CAP = 99         # 칸 수 상한 없음 (타일마다 캐는 방식 — 위키)
TILE_CAPACITY = {"kDenseWheat": 4, "kGrandTree": 3, "kGraniteSlab": 3}   # 고급 타일 용량 (기본 1, 위키)
EFFECTS: Dict[str, Tuple[object, float, str, str, str]] = {
    "kIdleFarm": (1, 1.0, "harvest", "worker", "붙어 있는 밀밭마다 6분에 밀 1 (일꾼 배정)"),
    "kIdleLumberyard": (2, 0.67, "harvest", "worker", "붙어 있는 숲마다 9분에 나무 1 (일꾼 배정)"),
    "kIdleStoneMine": (3, 0.6, "harvest", "worker", "붙어 있는 바위마다 10분에 돌 1 (일꾼 배정)"),
    "kSingleFamilyHome": (1, 0.25, "harvest", "upgraded", "근처 밭에서 주기적으로 채집 (강화 효과)"),
    "kCozyHome": (2, 0.37, "harvest", "upgraded", "근처 숲에서 주기적으로 채집 (강화 효과)"),
    "kHovel": (3, 0.8, "harvest", "upgraded", "근처 바위에서 주기적으로 채집 (강화 효과)"),
    "kVilla": (1, 0.3, "regen", "upgraded", "근처 밀밭 재생 속도 상승 (레벨 3은 범위 +1칸)"),
    "kCampground": (2, 0.3, "regen", "upgraded", "근처 숲 재생 속도 상승 (레벨 3은 범위 +1칸)"),
    "kRockyHill": (3, 0.3, "regen", "upgraded", "근처 바위 재생 속도 상승 (레벨 3은 범위 +1칸)"),
    "kMansion": ("all", 0.2, "count", "upgraded", "근처 건물마다 분당 골드 1 (최대 29)"),
    "kCaptainQuarters": ("stat", 1.0, "count", "upgraded", "인근 능력치 보너스 건물 +1 — 능력치 건물을 모두 범위 안에"),
    "kVeteranHut": ("housing", 0.6, "count", "upgraded", "인근 거처 입주민 추가 경험치 (캐릭터 레벨 4·7·9에서 20·25·30%) — 거처를 모두 범위 안에"),
    # 강철 요새(방패잡이): 튕기면 근처 공사장에 건설 점수 +4 (위키 Iron Fortress) → 지금 공사 중인 건물과
    # 계속 강화할 무한 강화 능력치 건물 근처에 (Steam 가이드 '100% Utilization', 토론 'Max Iron Fortress')
    "kBrickHouse": ("build", 0.5, "count", "upgraded", "튕기면 근처 공사장에 건설 점수 +4 — 공사 중·무한 강화 건물 근처에"),
}
# 무한 강화 능력치 건물 6개 (병원·사수 조합·카피톨륨·대박물관·마차 공장·전사 조합)
STATUE_TYPES = {"kEnduranceStatue", "kDexterityStatue", "kLeadershipStatue", "kIntelligenceStatue", "kSpeedStatue",
                "kStrengthStatue"}
# 거처 효과는 건물 레벨이 아니라 사는 캐릭터가 레벨 4일 때 켜지고 7·9에서 강해진다 (위키 Buildings).
# 게임 캐릭터 레벨은 0부터 (CharMetaInst.Lvl) → 3 = 화면 레벨 4.
HOUSE_ACTIVE_LVL = 3
HOUSE_INACTIVE = 0.35     # 아직 안 켜진 거처 효과 (나중에 켜질 것 — 조금만 반영)
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
CHAR_LEVELS: Dict[str, int] = {}   # 캐릭터 slug(소문자, 예: recaller) → 게임 레벨 (계산 작업마다 set_char_levels 로 넣음)


def set_char_levels(levels: Dict[str, int]):
    """meta.chars 의 {kRecaller: 6, …} 를 받아 둔다 (이 프로세스 안에서 쓰는 전역값)."""
    CHAR_LEVELS.clear()
    for k, v in (levels or {}).items():
        if isinstance(v, int):
            CHAR_LEVELS[_slug(k)] = v


@functools.lru_cache(maxsize=1)
def house_characters() -> Dict[str, Tuple[str, str]]:
    """거처 slug → (캐릭터 slug, 캐릭터 이름). 게임 문구 '○○의 거처'와 캐릭터 이름을 맞춰 찾는다."""
    from ..gamedata import DATA_DIR
    path = os.path.join(DATA_DIR, "game_text_ko.json")
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        return {}
    by_name = {c.get("name_ko"): c.get("slug") for c in (t.get("characters") or {}).values()}
    out = {}
    for slug, v in (t.get("buildings") or {}).items():
        desc = v.get("desc_ko") or ""
        if "의 거처" in desc:
            who = desc.split("의 거처")[0].strip()
            if who in by_name:
                out[slug] = (by_name[who], who)
    return out
# 여러 개 지을 수 있는 건물·타일과 짓는 비용 [골드, 밀, 나무, 돌] (위키 건물표). 나머지 건물은 1개만.
UNLIMITED_COST: Dict[str, Tuple[int, int, int, int]] = {
    "kIdleFarm": (100, 0, 0, 0), "kIdleStoneMine": (100, 0, 5, 0), "kGoldMine": (0, 0, 10, 12),
    "kWheatField": (30, 0, 0, 0), "kForest": (50, 2, 0, 0), "kBoulder": (80, 0, 2, 0),
    "kDenseWheat": (100, 1, 0, 0), "kGrandTree": (150, 0, 1, 0), "kGraniteSlab": (250, 0, 0, 1),
}
REGEN_TYPES = {"kVilla", "kCampground", "kRockyHill"}
LVL3_RANGE_BONUS = 1.125  # 커뮤니티: 레벨 3 재생 건물의 실제 효과 범위는 표시보다 1칸 넓다 (게임 값 비교로 확인 예정)
FIXED_TYPES = {"kHome"}
UNFINISHED_STATES = {"kScaffold", "kUpgrading"}


@functools.lru_cache(maxsize=1)
def housing_types() -> frozenset:
    """거처(캐릭터가 사는 건물): 게임 건물 설명이 '… 거처'인 것."""
    from ..gamedata import DATA_DIR
    path = os.path.join(DATA_DIR, "game_text_ko.json")
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)["buildings"]
    except (OSError, ValueError, KeyError):
        return frozenset()
    return frozenset(slug for slug, v in t.items() if "거처" in (v.get("desc_ko") or ""))   # 소문자 slug (kSheriffOffice → sherifffoffice 비교)


def _slug(type_name: str) -> str:
    return (type_name[1:] if type_name.startswith("k") else type_name).lower()


@dataclass
class Piece:
    id: int
    type: str
    w: int
    h: int
    rel: frozenset            # 사각형 왼쪽 아래 기준 실제로 차지하는 타일
    movable: bool
    range: float
    factor: float = 1.0       # 효과가 켜진 정도 (강화 전·일꾼 없음 → 0.5)
    cap: float = 1.0          # 자원 타일 용량 (고급 타일 3~4, 강화하면 늘어남 — 게임 값 cap)
    unfinished: bool = False  # 공사 중·강화 공사 중 (강철 요새 건설 점수 대상)


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


class Layout:
    """타일 격자 위 건물 배치 (왼쪽 아래 타일 좌표) + 점유 표."""

    def __init__(self, grid: Grid, pieces: Dict[int, Piece], origin: Dict[int, Tuple[int, int]]):
        self.grid, self.pieces, self.origin = grid, pieces, dict(origin)
        self.occ: Dict[Tuple[int, int], int] = {}
        for i, o in self.origin.items():
            for c in self.cells(i, o):
                self.occ[c] = i

    def cells(self, i: int, o: Optional[Tuple[int, int]] = None):
        p = self.pieces[i]
        c0, r0 = o if o is not None else self.origin[i]
        return [(c0 + dx, r0 + dy) for dx, dy in p.rel]

    def center(self, i: int, o: Optional[Tuple[int, int]] = None) -> Tuple[float, float]:
        p = self.pieces[i]
        c0, r0 = o if o is not None else self.origin[i]
        return self.grid.center(c0, r0, p.w, p.h)

    def rect_ids(self, c0: int, r0: int, w: int, h: int) -> Optional[Set[int]]:
        """영역 안에 걸친 건물들. 영역 밖으로 삐져나가거나 못 옮기는 건물이 있으면 None."""
        ids = set()
        for dx in range(w):
            for dy in range(h):
                i = self.occ.get((c0 + dx, r0 + dy))
                if i is not None:
                    ids.add(i)
        for i in ids:
            p = self.pieces[i]
            oc, orr = self.origin[i]
            if not p.movable or oc < c0 or orr < r0 or oc + p.w > c0 + w or orr + p.h > r0 + h:
                return None
        return ids

    def swap_regions(self, a: Tuple[int, int], b: Tuple[int, int], w: int, h: int) -> Optional[List[Tuple[int, Tuple[int, int]]]]:
        """같은 크기 두 영역의 내용을 맞바꾼다. 되돌리기용 (건물, 이전 자리) 목록을 돌려준다."""
        if abs(a[0] - b[0]) < w and abs(a[1] - b[1]) < h:
            return None                                   # 겹치는 영역
        ia, ib = self.rect_ids(*a, w, h), self.rect_ids(*b, w, h)
        if ia is None or ib is None or (not ia and not ib):
            return None
        moves = [(i, (self.origin[i][0] + b[0] - a[0], self.origin[i][1] + b[1] - a[1])) for i in ia]
        moves += [(i, (self.origin[i][0] + a[0] - b[0], self.origin[i][1] + a[1] - b[1])) for i in ib]
        tiles = self.grid.tiles
        if any(c not in tiles for i, o in moves for c in self.cells(i, o)):
            return None                                   # 산 땅 밖으로 나가는 건물 (ㄱ자 건물의 빈 모서리 자리 등)
        return self.apply(moves)

    def apply(self, moves: List[Tuple[int, Tuple[int, int]]]) -> List[Tuple[int, Tuple[int, int]]]:
        undo = [(i, self.origin[i]) for i, _ in moves]
        for i, _ in moves:
            for c in self.cells(i):
                if self.occ.get(c) == i:
                    del self.occ[c]
        for i, o in moves:
            self.origin[i] = o
            for c in self.cells(i, o):
                self.occ[c] = i
        return undo


def pieces_from_base(base: dict, grid: Grid, housing: Set[str], fixed: Sequence[int] = ()
                     ) -> Tuple[Dict[int, Piece], Dict[int, Tuple[int, int]]]:
    blds = buildings_from_base(base)
    masks = shape_masks(base.get("geo") or {}, blds, grid)
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    pieces, origin = {}, {}
    for i, b in blds.items():
        w, h = b.footprint
        rel = masks.get(i) or {(dx, dy) for dx in range(w) for dy in range(h)}
        info = raw.get(i, {})
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
        if b.type in REGEN_TYPES and int(info.get("lvl") or 0) >= 2:   # 게임 레벨은 0부터 — 2 = 화면 레벨 3
            rng_ += LVL3_RANGE_BONUS
        # 공사 중인 건물도 옮길 수 있다 (커뮤니티: 채집 구역 가장자리로 옮겨 일꾼이 치게 — Screen Rant 기지 공략)
        unfinished = info.get("state") in UNFINISHED_STATES
        movable = b.type not in FIXED_TYPES and i not in fixed
        cap = float(info.get("cap") or TILE_CAPACITY.get(b.type, 1)) if b.type in TILE_RES else 1.0
        pieces[i] = Piece(i, b.type, w, h, frozenset(rel), movable, rng_, factor, max(1.0, cap), unfinished)
        origin[i] = (round((b.x - w * grid.size / 2 - grid.ox) / grid.size),
                     round((b.y - h * grid.size / 2 - grid.oy) / grid.size))
    return pieces, origin


# 범위 모양: 게임 화면의 범위 표시가 건물 중심 기준 사각형이다 (사용자 스크린샷: 2×2 채석장, 범위 2.25 → 둘레 한 칸 4×4).
# 타일 중심이 사각형 안(|dx|, |dy| ≤ 범위)이면 범위 안. 플러그인 1.9 의 게임 값(in_range)으로 원/사각형·여유를 다시 고른다.
RANGE_SHAPE = "square"


def in_range(dx: float, dy: float, r: float, r2: Optional[float] = None) -> bool:
    if RANGE_SHAPE == "square":
        return abs(dx) <= r + 1e-6 and abs(dy) <= r + 1e-6
    return dx * dx + dy * dy <= (r2 if r2 is not None else r * r) + 1e-6


# 발사대 앞 채집 구역: 치여도 얻는 게 없는 건물(능력치·거처 등)은 뒤나 구석으로 (커뮤니티: 발사대 앞은 자원 타일·금광,
# 나머지 건물은 둘레를 막는 벽으로 — Screen Rant·TheGamer 기지 공략). 튕기면 효과가 있는 건물(게임 설명 '튕겨나갈 때')과
# 자원 타일, 공사 중인 건물(쳐야 지어짐)은 앞에 있어도 된다.
BOUNCE_TYPES = {"kGoldMine", "kBrickHouse", "kMonastery", "kHauntedHouse"}
LANE_R = 7.0             # 발사대에서 이 거리(타일)까지를 앞 구역으로 — 가까울수록 크게
LANE_W = 0.3             # 앞 구역 한 칸(가장 가까울 때) 벌점. 2×2 건물을 발사대 바로 앞에 두면 약 1 (능력치 건물 하나를 대위 막사 범위에 넣는 값)
LANE_TILE_W = 0.2        # 앞 구역 자원 타일 가산 (공을 던져 캐는 몫 — 커뮤니티: 자원 타일은 발사대 바로 앞에). 실제 기지 3곳 비교:
                         # 0 → 채집 계산 64.0·범위 효과 60.4, 0.2 → 74.0·59.3, 0.4 → 75.7·58.3
HARVEST_DAMP = 10        # 채집 발사 계산 비교 때 더하는 값 — 13 → 14 같은 작은 차이(계산 오차 수준)로 배치를 바꾸지 않게
SELECT_MOVE_COST = 0.002 # 후보 비교 때 옮기는 건물 하나당 (25개 = 5%p) — 거의 같은 효과에 많이 옮기는 배치를 막음


def lane_values(geo: dict, grid: Grid) -> Dict[Tuple[int, int], float]:
    """타일 → 앞 구역 값 (발사대에서 가까울수록 1, LANE_R 밖 0). 발사대 위치를 모르면 빈 값."""
    launcher = geo.get("launcher") or []
    if len(launcher) < 2:
        return {}
    lx, ly = float(launcher[0]), float(launcher[1])
    out = {}
    for c, r in grid.tiles:
        x, y = grid.ox + (c + 0.5) * grid.size, grid.oy + (r + 0.5) * grid.size
        d = math.hypot(x - lx, y - ly) / grid.size
        if d < LANE_R:
            out[(c, r)] = 1.0 - d / LANE_R
    return out


def lane_idle(p: "Piece") -> bool:
    return not (p.type in TILE_RES or p.type in BOUNCE_TYPES or p.unfinished)


PRESETS = {"effect": "효과 최대", "gold_u": "금광 U자"}
PRESET_W = 20.0          # 프리셋 자리에 놓인 금광 하나의 가산 (범위 효과보다 크게 — 사용자가 고른 공략 틀을 따름)
PRESET_CLEAR = 4.0       # 아직 금광이 없는 프리셋 자리를 다른 건물이 막는 칸마다 감점 — 0.5 는 약해서 채석장이 자리를 차지한 채 남음


def gold_u_spots(geo: dict, grid: Grid, n: int = 7) -> List[Tuple[int, int]]:
    """금광 U자 (커뮤니티 정석: 발사대 바로 앞에 2×2 금광 7개 — 양옆 3개씩 세로로, 위에 1개가 막음).
    가운데 통로는 2칸 폭으로 발사대 열에 맞춘다. 산 땅 밖으로 나가는 자리는 뺀다. 돌려주는 값: 금광 왼쪽 아래 타일들."""
    launcher = geo.get("launcher") or []
    if len(launcher) < 2:
        return []
    lc = int((float(launcher[0]) - grid.ox) // grid.size)
    cols = [c for c, _ in grid.tiles if abs(c - lc) <= 1]
    rows = [r for c, r in grid.tiles if c == lc] or [r for _, r in grid.tiles]
    r0 = min(rows)
    left, right = lc - 3, lc + 1                # 통로 = lc-1, lc (2칸)
    spots = [(left, r0), (right, r0), (left, r0 + 2), (right, r0 + 2), (left, r0 + 4), (right, r0 + 4), (lc - 1, r0 + 6)]
    ok = [s for s in spots if all((s[0] + dx, s[1] + dy) in grid.tiles for dx in range(2) for dy in range(2))]
    return ok[:n] if cols else []


class Scorer:
    def __init__(self, pieces: Dict[int, Piece], stat_types: Set[str], housing: Set[str],
                 res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0,
                 preset_spots: Sequence[Tuple[int, int]] = (), preset_type: str = "kGoldMine",
                 lane: Optional[Dict[Tuple[int, int], float]] = None):
        self.pieces = pieces
        self.lane = lane or {}
        self.lane_ids = [i for i, p in pieces.items() if lane_idle(p)] if self.lane else []
        self.lane_tiles = [i for i, p in pieces.items() if p.type in TILE_RES] if self.lane else []
        self.preset_spots = set(preset_spots)
        self.preset_type = preset_type
        self.preset_cells = {s: {(s[0] + dx, s[1] + dy) for dx in range(2) for dy in range(2)} for s in self.preset_spots}
        self.pad = pad                     # 범위 판정 여유 (게임 값으로 맞춘 것, calibrate_range)
        self.res_weight = res_weight or {1: 1.0, 2: 1.0, 3: 1.0}
        self.effects = [i for i, p in pieces.items() if p.type in EFFECTS and p.range > 0]
        self.targets: Dict[object, List[int]] = {1: [], 2: [], 3: [], "all": [], "stat": [], "housing": [], "statue": [], "build": []}
        for i, p in pieces.items():
            if p.type in TILE_RES:
                self.targets[TILE_RES[p.type]].append(i)
            if p.type in stat_types:
                self.targets["stat"].append(i)
            if _slug(p.type) in housing:
                self.targets["housing"].append(i)
            if p.type in STATUE_TYPES:
                self.targets["statue"].append(i)
            if p.type in STATUE_TYPES or p.unfinished:
                self.targets["build"].append(i)
            self.targets["all"].append(i)

    def score(self, lay: Layout) -> Tuple[float, Dict[str, float]]:
        ctr = {i: lay.center(i) for i in lay.origin}
        total = 0.0
        detail: Dict[str, float] = {}
        regen: Dict[Tuple[str, int], float] = {}          # (효과 종류, 타일) → 최대 가중치
        harvest: Dict[int, List[float]] = {}              # 타일 → 채집 건물 가중치들
        for e in self.effects:
            p = self.pieces[e]
            kind, w, mode, _, _ = EFFECTS[p.type]
            ex, ey = ctr[e]
            rr = p.range + self.pad
            r2 = rr * rr
            n = 0
            for t in self.targets[kind]:
                if t == e:
                    continue
                tx, ty = ctr[t]
                if not in_range(tx - ex, ty - ey, rr, r2):
                    continue
                n += 1
                if mode == "harvest" and n > HARVEST_CAP:
                    continue                           # 채집 건물 하나가 쓰는 타일 수 상한
                val = w * p.factor * (self.res_weight.get(kind, 1.0) * self.pieces[t].cap if isinstance(kind, int) else 1.0)
                if kind == "build" and self.pieces[t].unfinished:
                    val *= UNFINISHED_BUILD_W
                if mode == "regen":
                    k = (p.type, t)
                    regen[k] = max(regen.get(k, 0.0), val)
                elif mode == "harvest":
                    harvest.setdefault(t, []).append(val)
                else:
                    total += val
            detail[p.type] = detail.get(p.type, 0) + n
        total += sum(regen.values())
        for vals in harvest.values():
            vals.sort(reverse=True)
            total += sum(v * (1.0 if k == 0 else 0.5 if k == 1 else 0.0) for k, v in enumerate(vals))
        if self.lane:
            blocked = sum(self.lane.get(c, 0.0) for i in self.lane_ids if i in lay.origin for c in lay.cells(i))
            total -= LANE_W * blocked
            detail["lane"] = round(blocked, 2)
            # 생산 건물·거처가 이미 캐는 타일은 빼고 (같은 타일 자원을 나눠 쓰므로 둘 다 더하면 이중 계산)
            front = sum(self.lane.get(c, 0.0) * self.res_weight.get(TILE_RES[self.pieces[i].type], 1.0) * self.pieces[i].cap
                        for i in self.lane_tiles if i in lay.origin and i not in harvest for c in lay.cells(i))
            total += LANE_TILE_W * front
            detail["lane_tiles"] = round(front, 2)
        if self.preset_spots:
            filled = {lay.origin[i] for i, p in self.pieces.items() if p.type == self.preset_type and i in lay.origin
                      and lay.origin[i] in self.preset_spots}
            total += PRESET_W * len(filled)
            for s, cells in self.preset_cells.items():
                if s not in filled:
                    total -= PRESET_CLEAR * sum(1 for c in cells if c in lay.occ)
        return total, detail


MOVE_COST = 0.03        # 옮기는 건물 하나당 벌점 — 실제 기지에서 0.005(42번 옮김, 효과 +4%)·0.03(22번, +7.5%)·0.06(탐색 멈춤) 비교해 정함


EXPLORE_COST = 0.005    # 담금질 중에는 벌점을 작게 (크면 처음 배치에서 못 벗어남 — 실제 기지 6번 중 4번)


def _objective(lay: Layout, scorer: Scorer, origin0: Dict[int, Tuple[int, int]], cost: float = MOVE_COST) -> float:
    s, _ = scorer.score(lay)
    return s - cost * sum(1 for i, o in lay.origin.items() if o != origin0.get(i))


def _near_spots(lay: Layout, scorer: Scorer, i: int, cand: List[Tuple[int, int]], w: int, h: int,
                rng: random.Random) -> List[Tuple[int, int]]:
    """건물 i 가 효과를 주거나 받을 만한 자리 (관련 건물의 범위 안)."""
    p = lay.pieces[i]
    if scorer.preset_spots and p.type == scorer.preset_type:
        spots = [o for o in cand if o in scorer.preset_spots]      # 금광은 프리셋 자리로
        if spots:
            return spots
    partners: List[Tuple[Tuple[float, float], float]] = []
    kind = TILE_RES.get(p.type)
    for e in scorer.effects:
        ek = EFFECTS[lay.pieces[e].type][0]
        if e != i and (ek == kind or ek == "all"):
            partners.append((lay.center(e), lay.pieces[e].range))
    if p.type in EFFECTS:
        for t in scorer.targets[EFFECTS[p.type][0]]:
            if t != i:
                partners.append((lay.center(t), p.range))
    if not partners:
        return cand
    (px, py), r = rng.choice(partners)
    g = lay.grid
    out = [o for o in cand if in_range(g.center(o[0], o[1], w, h)[0] - px, g.center(o[0], o[1], w, h)[1] - py, r)]
    return out or cand


def polish(lay: Layout, scorer: Scorer, origin0: Dict[int, Tuple[int, int]], seconds: float = 3.0) -> float:
    """마무리: 건물마다 같은 크기의 모든 자리와 맞바꿔 보고 가장 좋은 것을 받아들인다 (더 나아지지 않을 때까지)."""
    grid = lay.grid
    spots = {}
    cur = _objective(lay, scorer, origin0)
    start = time.perf_counter()
    improved = True
    while improved and time.perf_counter() - start < seconds:
        improved = False
        for i, p in lay.pieces.items():
            if not p.movable:
                continue
            k = (p.w, p.h)
            if k not in spots:
                spots[k] = [(c, r) for c, r in grid.tiles
                            if all((c + dx, r + dy) in grid.tiles for dx in range(p.w) for dy in range(p.h))]
            best = (cur, None)
            for b in spots[k]:
                undo = lay.swap_regions(lay.origin[i], b, p.w, p.h)
                if undo is None:
                    continue
                v = _objective(lay, scorer, origin0)
                if v > best[0] + 1e-9:
                    best = (v, b)
                lay.apply(undo)
            if best[1] is not None:
                lay.swap_regions(lay.origin[i], best[1], p.w, p.h)
                cur = best[0]
                improved = True
    return cur


def anneal(lay: Layout, scorer: Scorer, seconds: float, rng: random.Random,
           t0: float = 0.8, t1: float = 0.005, origin0: Optional[Dict[int, Tuple[int, int]]] = None
           ) -> Tuple[Dict[int, Tuple[int, int]], float]:
    """영역 맞바꾸기 담금질. 가장 좋았던 배치(자리 표)와 점수(벌점 포함)를 돌려준다."""
    grid = lay.grid
    origin0 = origin0 if origin0 is not None else dict(lay.origin)
    movable = [i for i, p in lay.pieces.items() if p.movable]
    if not movable:
        s, _ = scorer.score(lay)
        return dict(lay.origin), s
    # 크기별 가능한 영역 왼쪽 아래 (산 땅 안에 전부 들어가는 곳)
    origins: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    def spots(w: int, h: int) -> List[Tuple[int, int]]:
        k = (w, h)
        if k not in origins:
            origins[k] = [(c, r) for c, r in grid.tiles
                          if all((c + dx, r + dy) in grid.tiles for dx in range(w) for dy in range(h))]
        return origins[k]

    cur = _objective(lay, scorer, origin0, EXPLORE_COST)
    best, best_origin = cur, dict(lay.origin)
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
        i = rng.choice(movable)
        p = lay.pieces[i]
        w, h = p.w, p.h
        if rng.random() < 0.25:                         # 가끔 더 큰 묶음 영역 (작은 타일 여러 개 교환)
            w, h = w + rng.randint(0, 2), h + rng.randint(0, 2)
        cand = spots(w, h)
        if not cand:
            continue
        a = lay.origin[i]
        if w != p.w or h != p.h:
            a = (a[0] - rng.randint(0, w - p.w), a[1] - rng.randint(0, h - p.h))
            if not all((a[0] + dx, a[1] + dy) in grid.tiles for dx in range(w) for dy in range(h)):
                continue
        b = rng.choice(_near_spots(lay, scorer, i, cand, w, h, rng) if rng.random() < 0.6 else cand)
        undo = lay.swap_regions(a, b, w, h)
        if undo is None:
            continue
        s = _objective(lay, scorer, origin0, EXPLORE_COST)
        d = s - cur
        if d >= 0 or rng.random() < math.exp(d / max(temp, 1e-6)):
            cur = s
            if s > best + 1e-9:
                best, best_origin = s, dict(lay.origin)
        else:
            lay.apply(undo)
    return best_origin, best


def canonicalize(pieces: Dict[int, Piece], origin0: Dict[int, Tuple[int, int]],
                 final: Dict[int, Tuple[int, int]]) -> Dict[int, Tuple[int, int]]:
    """같은 종류·같은 모양 건물끼리는 누가 어느 자리에 가도 같다 — 원래 자리에 있던 건물이 그 자리를 갖게 해
    쓸데없는 옮기기(숲 A ↔ 숲 B)를 없앤다."""
    groups: Dict[tuple, List[int]] = {}
    for i, p in pieces.items():
        if i in final:
            groups.setdefault((p.type, p.w, p.h, p.rel, p.factor, p.movable), []).append(i)
    out = dict(final)
    for ids in groups.values():
        if len(ids) < 2:
            continue
        spots = [final[i] for i in ids]
        free = list(spots)
        keep = {}
        for i in ids:
            if origin0[i] in free:
                keep[i] = origin0[i]
                free.remove(origin0[i])
        rest = [i for i in ids if i not in keep]
        for i in rest:                                   # 남은 건물은 가장 가까운 남은 자리로
            o = min(free, key=lambda q: (q[0] - origin0[i][0]) ** 2 + (q[1] - origin0[i][1]) ** 2)
            keep[i] = o
            free.remove(o)
        out.update(keep)
    return out


def move_sequence(grid: Grid, pieces: Dict[int, Piece], cur: Dict[int, Tuple[int, int]],
                  target: Dict[int, Tuple[int, int]]) -> List[Tuple[int, Tuple[int, int], bool]]:
    """지금 배치 → 목표 배치로 가는 옮기기 순서 (한 번에 한 건물, 항상 빈 자리로만).

    1) 목표 자리가 비어 있는 건물을 모두 옮긴다.
    2) 막히면 막는 건물이 가장 적은 목표 자리 하나를 골라, 막는 건물들을 다른 건물 목표가 아닌 빈 곳에 잠시 비켜 둔다.
       그 건물은 다음 차례에 제자리로 간다 — 매 단계 최소 한 건물이 목표 자리에 확정되므로 반드시 끝난다.
    목표 자리끼리는 겹치지 않으므로 목표 자리를 막는 건물은 항상 아직 안 옮긴 건물이다.
    돌려주는 값: [(건물, 옮길 자리(왼쪽 아래 타일), 잠시 비켜 두기인지)]."""
    lay = Layout(grid, pieces, cur)
    pending = [i for i in target if lay.origin.get(i) != target[i]]
    steps: List[Tuple[int, Tuple[int, int], bool]] = []

    def blockers(i: int) -> Set[int]:
        return {lay.occ[c] for c in lay.cells(i, target[i]) if lay.occ.get(c) not in (None, i)}

    for _ in range(len(pieces) + 5):
        moved = True
        while moved:
            moved = False
            for i in list(pending):
                if not blockers(i):
                    lay.apply([(i, target[i])])
                    steps.append((i, target[i], False))
                    pending.remove(i)
                    moved = True
        if not pending:
            break
        goal = min(pending, key=lambda i: (len(blockers(i)), sum(len(pieces[j].rel) for j in blockers(i))))
        keep_clear = set(lay.cells(goal, target[goal]))
        reserved = {c for j in pending for c in lay.cells(j, target[j])}
        for j in sorted(blockers(goal), key=lambda j: len(pieces[j].rel)):
            p = pieces[j]
            own = set(lay.cells(j, target[j]))
            best = None
            for c, r in sorted(grid.tiles):
                cells = {(c + dx, r + dy) for dx, dy in p.rel}
                if not cells <= grid.tiles or cells & keep_clear:
                    continue
                if any(lay.occ.get(x) not in (None, j) for x in cells):
                    continue
                clash = len(cells & (reserved - own))       # 다른 건물 목표 자리를 덜 막는 곳
                if best is None or clash < best[0]:
                    best = (clash, (c, r))
                    if clash == 0:
                        break
            if best is None:
                return steps                               # 비켜 둘 곳이 없음 (땅이 꽉 참) — 여기까지만
            lay.apply([(j, best[1])])
            steps.append((j, best[1], True))
    return steps


def optimize(base: dict, harvest_eval: Optional[Callable[[dict], List[int]]] = None,
             reach_ok: Optional[Callable[[dict], bool]] = None, seconds: float = 6.0, restarts: int = 3,
             res_weight: Optional[Dict[int, float]] = None, seed: int = 0,
             fixed: Sequence[int] = (), pad: float = 0.0,
             prefer: Optional[Dict[int, Tuple[float, float]]] = None, preset: str = "effect") -> Optional[FullPlan]:
    """전체 재배치 최적화. harvest_eval(geo) → [골드, 밀, 나무, 돌] (채집 발사 예상), reach_ok(base) → 미완성 건물에 닿는지."""
    from .layout import move_geo
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    if grid is None:
        return None
    housing = housing_types()
    stats = {b.get("type") for b in base.get("buildings") or []
             if b.get("stat") and not any(x in b["stat"] for x in ("None", "Invalid", "Count", "Max"))}
    stat_types = stats or STAT_FALLBACK
    pieces, origin0 = pieces_from_base(base, grid, housing, fixed)
    if not pieces:
        return None
    preset_spots = gold_u_spots(geo, grid) if preset == "gold_u" else []
    lane = lane_values(geo, grid)
    scorer = Scorer(pieces, stat_types, housing, res_weight, pad, preset_spots, lane=lane)
    plain = Scorer(pieces, stat_types, housing, res_weight, pad)        # 보고용 (프리셋 가산 없는 범위 효과)
    # 발사대 앞 구역(자원 타일은 앞에, 치여도 얻는 게 없는 건물은 뒤로)은 담금질과 후보 비교 모두에 넣는다.
    # 실제 기지 3곳에서 앞 구역을 넣은 쪽이 채집 발사 계산·범위 효과 모두 좋았다 (53.2 → 56.2, 49.8 → 52.1).
    laned = scorer
    if preset == "gold_u":
        prefer = None                                                  # 사용자가 고른 틀이므로 이전 목표를 고집하지 않음
    lay0 = Layout(grid, pieces, origin0)
    e0, d0 = scorer.score(lay0)
    rng = random.Random(seed)
    cands = [(dict(origin0), e0)]
    for k in range(restarts):
        sc = scorer
        lay = Layout(grid, pieces, origin0)
        o, _ = anneal(lay, sc, seconds * 0.7 / max(1, restarts), rng, origin0=origin0)
        lay = Layout(grid, pieces, o)
        polish(lay, sc, origin0, seconds * 0.3 / max(1, restarts))
        cands.append((canonicalize(pieces, origin0, lay.origin), scorer.score(lay)[0]))
    blds = buildings_from_base(base)

    def to_base(orig: Dict[int, Tuple[int, int]]) -> dict:
        g = geo
        cur = dict(blds)
        out_b = []
        for b in base.get("buildings") or []:
            i = b.get("id")
            if i in orig and orig[i] != origin0.get(i):
                p = pieces[i]
                cx, cy = grid.center(orig[i][0], orig[i][1], p.w, p.h)
                g = move_geo(g, cur, i, (cx, cy))
                ob = cur[i]
                cur[i] = Bld(ob.id, ob.type, cx, cy, ob.tw, ob.th, ob.rot, ob.range)
                out_b.append(dict(b, x=round(cx, 3), y=round(cy, 3)))
            else:
                out_b.append(b)
        return dict(base, buildings=out_b, geo=g)

    h0 = harvest_eval(geo) if harvest_eval else None
    rw = res_weight or {}
    wsum = lambda v: sum(v[r] * rw.get(r, 1.0) for r in (1, 2, 3))
    best = None
    alt = None                                          # 범위 효과만 가장 좋은 후보 (채집 발사 때문에 떨어진 것)
    notes: List[str] = []
    # 이전 목표 배치(재배치 도중일 수 있음)도 후보로 — 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다
    # (실제 사용: 재배치 도중 다시 계산해 목표가 바뀌면서 채석장 둘레 돌을 빼게 됨)
    prefer_orig = None
    if prefer:
        po = dict(origin0)
        ok = True
        for i, (x, y) in prefer.items():
            if i in pieces:
                p = pieces[i]
                po[i] = (round((x - p.w * grid.size / 2 - grid.ox) / grid.size),
                         round((y - p.h * grid.size / 2 - grid.oy) / grid.size))
        cells = [c for i in po for c in Layout(grid, pieces, {}).cells(i, po[i])] if pieces else []
        ok = len(cells) == len(set(cells)) and set(cells) <= grid.tiles and \
            all(po[i] == origin0[i] for i in po if not pieces[i].movable)
        if ok and po != origin0:
            prefer_orig = po
            cands.append((po, scorer.score(Layout(grid, pieces, po))[0]))
    totals = {}
    for orig, s in cands:
        current = orig is cands[0][0]
        nb = to_base(orig)
        hv = harvest_eval(nb["geo"]) if harvest_eval and not current else h0
        if reach_ok is not None and not current and not reach_ok(nb):
            continue                                    # 미완성 건물로 가는 길을 막는 배치는 뺀다
        rel_e = s / e0 if e0 > 0 else (1.0 + s)
        rel_h = ((wsum(hv) + HARVEST_DAMP) / (wsum(h0) + HARVEST_DAMP)) if hv and h0 else 1.0
        halved = bool(hv and h0 and not current and any(h0[r] > 0 and hv[r] < 0.5 * h0[r] for r in (1, 2, 3)))
        if not current and (alt is None or rel_e > alt[0]):
            alt = (rel_e, rel_h)
        if halved:
            continue                                    # 채집 발사로 얻던 자원 하나가 절반 아래로 줄면 뺀다
        total = rel_e + rel_h - SELECT_MOVE_COST * sum(1 for i in orig if orig[i] != origin0.get(i))
        totals[id(orig)] = (total, orig, s, hv)
        if best is None or total > best[0]:
            best = (total, orig, s, hv)
    if best is None:
        return None
    _, orig, s, hv = best
    if prefer_orig is not None and id(prefer_orig) in totals and best[1] is not prefer_orig:
        pt = totals[id(prefer_orig)]
        if best[0] < pt[0] * 1.02:
            best = pt
            notes.append("이전에 정한 최적 배치를 유지 (새 계산이 2% 넘게 좋지 않음)")
    _, orig, s, hv = best
    if best[1] is prefer_orig and prefer_orig is not None:
        pass
    elif best[0] < 2.0 * 1.02:
        notes.append("지금 배치가 최적에 가까움 — 범위 효과와 채집 발사량을 합쳐 2% 넘게 올리는 배치를 찾지 못함")
        if alt is not None and alt[0] > 1.02:
            notes.append(f"범위 효과만 보면 +{(alt[0] - 1) * 100:.0f}% 배치가 있지만 채집 발사량이 "
                         f"{(alt[1] - 1) * 100:+.0f}% 라 권하지 않음")
        orig, s, hv = cands[0][0], e0, h0
    if preset == "gold_u":
        # 프리셋은 사용자가 고른 틀: 2% 기준·'지금이 최적' 판단 없이 가장 좋은 프리셋 후보를 쓴다
        cand_best = max(cands[1:], key=lambda c: c[1]) if len(cands) > 1 else cands[0]
        orig, s, hv = cand_best[0], cand_best[1], (harvest_eval(to_base(cand_best[0])["geo"]) if harvest_eval else None)
        notes = [f"금광 U자: 발사대 앞 자리 {len(preset_spots)}곳 중 "
                 f"{sum(1 for i, p in pieces.items() if p.type == 'kGoldMine' and orig.get(i) in set(preset_spots))}곳에 금광"]
    lay = Layout(grid, pieces, orig)
    if lane:
        def front(o):
            L = Layout(grid, pieces, o)
            return sum(lane.get(c, 0.0) for i in laned.lane_ids if i in o for c in L.cells(i))

        def tiles(o):
            L = Layout(grid, pieces, o)
            return sum(1 for i in laned.lane_tiles if i in o and any(lane.get(c, 0.0) > 0 for c in L.cells(i)))
        f0, f1 = front(origin0), front(orig)
        if f0 - f1 >= 1.0:
            notes.append(f"능력치·거처처럼 치여도 얻는 게 없는 건물을 발사대 앞에서 뒤·구석으로 "
                         f"(앞 구역 막음 {f0:.1f} → {f1:.1f})")
        t0, t1 = tiles(origin0), tiles(orig)
        if t1 - t0 >= 2:
            notes.append(f"자원 타일을 발사대 앞으로 (앞 구역 자원 타일 {t0} → {t1}개 — 공을 던져 캐는 몫)")
    # 보고는 범위 효과만 (앞 구역 점수는 배치를 고를 때만 쓴다)
    e0, d0 = plain.score(Layout(grid, pieces, origin0))
    s, d1 = plain.score(lay)
    moved = sum(1 for i in orig if orig[i] != origin0[i])
    return FullPlan(origin0, orig, {i: lay.center(i) for i in orig}, e0, s, d0, d1, h0, hv, moved, notes)


RANGE_TARGETS = {1: WHEAT_T, 2: WOOD_T, 3: STONE_T}
SHAPES = ("square", "circle")
PAD_CANDIDATES = (0.0, 0.5625, 1.125, 1.6875)     # 0 / 반 타일 / 한 타일 / 한 타일 반


def _stat_types(base: dict) -> Set[str]:
    return {b.get("type") for b in base.get("buildings") or []
            if b.get("stat") and not any(x in b["stat"] for x in ("None", "Invalid", "Count", "Max"))} or STAT_FALLBACK


def calibrate_range(base: dict) -> Tuple[float, int, int]:
    """게임이 직접 센 '범위 안 자원 타일 수'(플러그인 1.9 in_range)와 가장 잘 맞는 범위 여유를 고른다.
    돌려주는 값: (여유, 맞은 수, 비교한 수). 게임 값이 없으면 (0, 0, 0)."""
    blds = buildings_from_base(base)
    raw = [b for b in base.get("buildings") or [] if isinstance(b.get("in_range"), dict) and b.get("id") in blds]
    if not raw:
        return 0.0, 0, 0
    global RANGE_SHAPE
    keep = RANGE_SHAPE
    best = None
    for shape, pad in [(sh, pd) for sh in SHAPES for pd in PAD_CANDIDATES]:
        RANGE_SHAPE = shape
        ok = n = 0
        for b in raw:
            e = blds[b["id"]]
            eff = EFFECTS.get(e.type)
            if not eff or not isinstance(eff[0], int):
                continue
            game = sum(v for v in b["in_range"].values() if isinstance(v, int))
            mine = sum(1 for t in blds.values() if t.type in RANGE_TARGETS[eff[0]] and t.id != e.id
                       and in_range(t.x - e.x, t.y - e.y, e.range + pad))
            n += 1
            ok += int(game == mine)
        if best is None or ok > best[1]:
            best = (pad, ok, n, shape)
    RANGE_SHAPE = best[3] if best[1] > 0 else keep     # 게임 값과 가장 잘 맞는 모양으로 (이 프로세스 안에서)
    return best[:3]


def suggest_builds(base: dict, blueprints: Sequence[dict], res_weight: Optional[Dict[int, float]] = None,
                   pad: float = 0.0, seconds: float = 2.5) -> List[Tuple[str, Tuple[float, float], Tuple[int, int], float, int, int]]:
    """지을 수 있는 범위 효과 건물마다: 새 건물을 넣고 주변을 다시 맞춘 배치에서의 자리와 늘어나는 범위 효과.
    (가장 좋은 빈 자리에 넣은 뒤 담금질·마무리로 주변 건물도 옮겨 본다 — 빈 자리에만 넣으면 효과가 작게 나온다.)
    새 건물은 강화·일꾼 배정을 마친 것으로 보고 센다 (지은 뒤 할 일).
    돌려주는 값: [(종류, 중심, 크기, 늘어나는 점수, 범위 안 대상 수, 함께 옮길 건물 수)]."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    base_score = Scorer(pieces, stats, housing, res_weight, pad).score(Layout(grid, pieces, origin))[0]
    occ = Layout(grid, pieces, origin).occ
    ranges = {p.type: p.range for p in pieces.values() if p.range > 0}
    out = []
    seen = set()
    for bp in blueprints:
        t = bp.get("type", "")
        if t not in EFFECTS or t in seen:
            continue
        seen.add(t)
        w, h = bp.get("size") or (int(bp.get("tw") or 2), int(bp.get("th") or 2))
        new_id = -1
        p = Piece(new_id, t, w, h, frozenset((dx, dy) for dx in range(w) for dy in range(h)), True,
                  float(bp.get("range") or ranges.get(t) or (2.25 if t == "kIdleStoneMine" else 3.375)), 1.0)
        pcs = dict(pieces)
        pcs[new_id] = p
        scorer = Scorer(pcs, stats, housing, res_weight, pad)
        best = None
        for c, r in grid.tiles:
            cells = [(c + dx, r + dy) for dx, dy in p.rel]
            if any(x not in grid.tiles or x in occ for x in cells):
                continue
            o = dict(origin)
            o[new_id] = (c, r)
            s, d = scorer.score(Layout(grid, pcs, o))
            if best is None or s > best[0]:
                best = (s, (c, r), d.get(t, 0))
        if best is None:
            continue
        o = dict(origin)
        o[new_id] = best[1]
        lay = Layout(grid, pcs, o)
        oo, _ = anneal(lay, scorer, seconds * 0.7, random.Random(len(out) + 7), origin0=o)
        lay = Layout(grid, pcs, oo)
        polish(lay, scorer, o, seconds * 0.3)
        fo = canonicalize(pcs, o, lay.origin)
        lay = Layout(grid, pcs, fo)
        s, d = scorer.score(lay)
        if s < best[0]:
            lay, s, d = Layout(grid, pcs, o), best[0], {t: best[2]}
        moved = sum(1 for i in lay.origin if i != new_id and lay.origin[i] != origin[i])
        if s - base_score > 0.05:
            no = lay.origin[new_id]
            out.append((t, grid.center(no[0], no[1], w, h), (w, h), s - base_score, int(d.get(t, 0)), moved))
    out.sort(key=lambda x: -x[3])
    return out


def suggest_tiles(base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0,
                  max_each: int = 8) -> List[Tuple[str, Tuple[float, float], Tuple[int, int], float, int, int]]:
    """자원 타일을 더 사서 생산 건물 범위의 빈칸을 채우면 늘어나는 값 (사용자: '채석장 빈칸에 돌 넣는 게 낫지 않나').
    타일마다 가장 좋은 빈칸에 하나씩 놓아 보며(욕심쟁이) 늘어나는 값이 있는 동안 반복한다.
    돌려주는 값: [(종류, 첫 자리 중심, 크기, 늘어나는 점수 합, 놓을 개수, 0)]."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    have = {p.type for p in pieces.values()}
    kinds = {EFFECTS[t][0] for t in have if t in EFFECTS and isinstance(EFFECTS[t][0], int)}
    out = []
    for t, size in (("kBoulder", (1, 1)), ("kWheatField", (2, 2)), ("kForest", (2, 2))):
        if TILE_RES[t] not in kinds:
            continue
        pcs, o = dict(pieces), dict(origin)
        s0 = Scorer(pcs, stats, housing, res_weight, pad).score(Layout(grid, pcs, o))[0]
        total, first, n = 0.0, None, 0
        for k in range(max_each):
            nid = -100 - k
            pcs[nid] = Piece(nid, t, size[0], size[1], frozenset((dx, dy) for dx in range(size[0]) for dy in range(size[1])),
                             True, 0.0, 1.0, float(TILE_CAPACITY.get(t, 1)))
            scorer = Scorer(pcs, stats, housing, res_weight, pad)
            occ = Layout(grid, {i: pcs[i] for i in o}, o).occ
            best = None
            for c, r in grid.tiles:
                cells = [(c + dx, r + dy) for dx, dy in pcs[nid].rel]
                if any(x not in grid.tiles or x in occ for x in cells):
                    continue
                o2 = dict(o)
                o2[nid] = (c, r)
                v = scorer.score(Layout(grid, pcs, o2))[0]
                if best is None or v > best[0]:
                    best = (v, (c, r))
            if best is None or best[0] - s0 < 0.05:
                del pcs[nid]
                break
            total += best[0] - s0
            s0 = best[0]
            o[nid] = best[1]
            n += 1
            if first is None:
                first = grid.center(best[1][0], best[1][1], size[0], size[1])
        if n:
            out.append((t, first, size, total, n, 0))
    out.sort(key=lambda x: -x[3])
    return out


def activation_gains(base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0
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
    out = []
    for i, p in pieces.items():
        if p.type not in EFFECTS or p.factor >= 1.0:
            continue
        pcs = dict(pieces)
        pcs[i] = Piece(p.id, p.type, p.w, p.h, p.rel, p.movable, p.range, 1.0, p.cap, p.unfinished)
        s1 = Scorer(pcs, stats, housing, res_weight, pad).score(Layout(grid, pcs, origin))[0]
        if s1 - s0 > 0.05:
            ch = house_characters().get(_slug(p.type))
            what = ("일꾼 배정" if EFFECTS[p.type][3] == "worker"
                    else f"{ch[1]} 레벨 {_next_house_level(p.type, CHAR_LEVELS.get(ch[0], 0))}" if ch else "강화")
            out.append((i, p.type, what, s1 - s0))
    out.sort(key=lambda x: -x[3])
    return out


def final_base(base: dict, plan: FullPlan) -> dict:
    """최적 배치를 적용한 기지 (건물 위치 + 충돌 모양)."""
    from .layout import move_geo
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    blds = buildings_from_base(base)
    g, out_b = geo, []
    cur = dict(blds)
    for b in base.get("buildings") or []:
        i = b.get("id")
        if i in plan.centers_after and plan.origin_after.get(i) != plan.origin_before.get(i) and i in cur:
            cx, cy = plan.centers_after[i]
            g = move_geo(g, cur, i, (cx, cy))
            ob = cur[i]
            cur[i] = Bld(ob.id, ob.type, cx, cy, ob.tw, ob.th, ob.rot, ob.range)
            out_b.append(dict(b, x=round(cx, 3), y=round(cy, 3)))
        else:
            out_b.append(b)
    return dict(base, buildings=out_b, geo=g)


def remaining_moves(base: dict, final: Dict[int, Tuple[float, float]]) -> List[Move]:
    """지금(재배치 도중) 배치에서 목표 배치(건물별 중심)까지 남은 옮기기. 사용자가 다른 순서로 옮겨도 다시 맞춘다."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None or not final:
        return []
    pieces, cur = pieces_from_base(base, grid, housing_types())
    target = {}
    for i, (x, y) in final.items():
        if i in pieces:
            p = pieces[i]
            target[i] = (round((x - p.w * grid.size / 2 - grid.ox) / grid.size),
                         round((y - p.h * grid.size / 2 - grid.oy) / grid.size))
    out = []
    for i, o, park in move_sequence(grid, pieces, cur, target):
        p = pieces[i]
        out.append(Move(i, grid.center(o[0], o[1], p.w, p.h), 0.0,
                        "잠시 비켜 두기 (자리 비우기)" if park else "최적 배치 자리로"))
    return out


def plan_moves(plan: FullPlan, base: dict) -> List[Move]:
    """최적 배치로 가는 옮기기 (Move 목록, 잠시 비켜 두기 포함)."""
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    pieces, cur = pieces_from_base(base, grid, housing_types())
    target = {i: o for i, o in plan.origin_after.items() if i in cur}
    out = []
    for i, o, park in move_sequence(grid, pieces, cur, target):
        p = pieces[i]
        out.append(Move(i, grid.center(o[0], o[1], p.w, p.h), 0.0,
                        "잠시 비켜 두기 (자리 비우기)" if park else "최적 배치 자리로"))
    return out
