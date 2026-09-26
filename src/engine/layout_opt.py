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
from ..i18n import tr

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
    "kCaptainQuarters": ("stat", 1.0, "count", "upgraded", tr("인근 능력치 보너스 건물 +1 — 능력치 건물을 모두 범위 안에")),
    "kVeteranHut": ("housing", 0.6, "count", "upgraded", tr("인근 거처 입주민 추가 경험치 (캐릭터 레벨 4·7·9에서 20·25·30%) — 거처를 모두 범위 안에")),
    # 강철 요새(방패잡이): 튕기면 근처 공사장에 건설 점수 +4 (위키 Iron Fortress) → 지금 공사 중인 건물과
    # 계속 강화할 무한 강화 능력치 건물 근처에 (Steam 가이드 '100% Utilization', 토론 'Max Iron Fortress')
    "kBrickHouse": ("build", 0.5, "count", "upgraded", tr("튕기면 근처 공사장에 건설 점수 +4 — 공사 중·무한 강화 건물 근처에")),
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


# 거처 slug → 사는 캐릭터 slug. 게임 건물 설명 '○○의 거처'와 캐릭터 이름을 맞춰 뽑은 표 (2026-09-26 한국어 추출본, 21개).
# 문구로만 찾으면 윈도우가 한국어가 아닐 때(게임 문구를 다른 언어로 추출) 거처를 하나도 못 찾아 가이드 배치가 깨진다.
HOUSE_CHAR: Dict[str, str] = {
    "brickhouse": "brickhead", "campground": "radicalai", "captainquarters": "tactician", "cozyhome": "cohabitants",
    "falconryhut": "falconer", "hauntedhouse": "recaller", "hiddentemple": "tiptoer", "hovel": "wimp",
    "lab": "physicist", "logcabin": "backpacker", "mansion": "spendthrift", "mausoleum": "shade",
    "monastery": "flagellant", "partyhouse": "carouser", "rockyhill": "sisyphus", "sheriffoffice": "itchyfinger",
    "singlefamilyhome": "emptynester", "stonedomain": "tunneller", "unstabletower": "packrat",
    "veteranhut": "embedded", "villa": "cogitator",
}


def _game_text() -> dict:
    from ..gamedata import DATA_DIR
    try:
        with open(os.path.join(DATA_DIR, "game_text_ko.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@functools.lru_cache(maxsize=1)
def house_characters() -> Dict[str, Tuple[str, str]]:
    """거처 slug → (캐릭터 slug, 캐릭터 이름). 표(HOUSE_CHAR) + 한국어 게임 문구 '○○의 거처'로 새로 생긴 거처.
    이름은 추출한 게임 문구의 언어 그대로 (없으면 slug)."""
    t = _game_text()
    chars = (t.get("characters") or {}).values()
    name_of = {c.get("slug"): c.get("name_ko") for c in chars}
    out = {h: (c, name_of.get(c) or c) for h, c in HOUSE_CHAR.items()}
    by_name = {c.get("name_ko"): c.get("slug") for c in chars}
    for slug, v in (t.get("buildings") or {}).items():
        desc = v.get("desc_ko") or ""
        if slug not in out and "의 거처" in desc:
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
FIXED_TYPES = {"kHome"}
UNFINISHED_STATES = {"kScaffold", "kUpgrading"}


@functools.lru_cache(maxsize=1)
def housing_types() -> frozenset:
    """거처(캐릭터가 사는 건물, 소문자 slug): 표(HOUSE_CHAR) + 한국어 게임 설명이 '… 거처'인 것."""
    t = _game_text().get("buildings") or {}
    return frozenset(HOUSE_CHAR) | frozenset(slug for slug, v in t.items() if "거처" in (v.get("desc_ko") or ""))


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


def rotated(p: Piece, k: int) -> Piece:
    """시계 방향으로 90° × k 돌린 건물 (게임 rot +1 = 시계 방향 90°). ㄱ·ㅜ·ㅠ 자 모양도 그대로 돌린다.
    근거: 같은 건물이 다른 rot 로 기록된 충돌 모양 비교 (harvest_traces·live_state, 10종 — 시계 14 : 반시계 0)."""
    k %= 4
    w, h, rel = p.w, p.h, p.rel
    for _ in range(k):
        rel = frozenset((y, w - 1 - x) for x, y in rel)       # (x, y 위쪽) → 시계 방향 90°
        w, h = h, w
    return p if k == 0 else Piece(p.id, p.type, w, h, rel, p.movable, p.range, p.factor, p.cap, p.unfinished)


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
    turned: Dict[int, int] = field(default_factory=dict)   # 회전해서 놓는 건물 → 시계 방향 90° 횟수 (1~3, 가이드 배치)


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
    from .construction_policy import is_recommended_building
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
        # 범위는 게임 GetRange 값을 그대로 쓴다. 공략의 '표시보다 +1칸'을 이미 계산된 값에 다시 더하지 않는다.
        # 공사 중인 건물도 옮길 수 있다 (커뮤니티: 채집 구역 가장자리로 옮겨 일꾼이 치게 — Screen Rant 기지 공략)
        unfinished = info.get("state") in UNFINISHED_STATES and is_recommended_building(b.type)
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


def entrance_cells(geo: dict, grid: Grid) -> Set[Tuple[int, int]]:
    """게임의 입구 청크 아래 중앙 두 칸 × 두 줄을 비운다 (이전 플러그인은 보호 범위 없음)."""
    chunk = geo.get("entrance_chunk") or geo.get("entrance_grid") or []
    if not (isinstance(chunk, (list, tuple)) and len(chunk) == 2 and
            all(isinstance(v, int) and not isinstance(v, bool) for v in chunk)):
        return set()
    width, height = geo.get("chunk_w"), geo.get("chunk_h")
    if not (isinstance(width, int) and isinstance(height, int) and width >= 2 and height >= 2):
        return set()
    if chunk not in (geo.get("chunks") or []):
        return set()
    # 실제 게임 입구는 8×6 청크 한 덩어리다. 전체를 비우면 이사량이 커지므로 아래 중앙의 최소 통로만 보호한다.
    left = chunk[0] * width + width // 2 - 1
    bottom = chunk[1] * height
    return {(c, r) for c in (left, left + 1) for r in (bottom, bottom + 1) if (c, r) in grid.tiles}


def lane_idle(p: "Piece") -> bool:
    return not (p.type in TILE_RES or p.type in BOUNCE_TYPES or p.unfinished)


def preserves_production(base: dict, candidate: dict, pad: float = 0.0) -> bool:
    """게임 현재 계수와 일치한 가동 생산 건물의 자원 수·용량을 줄이는 후보는 거른다."""
    from .layout_city import PRODUCERS
    before, after = buildings_from_base(base), buildings_from_base(candidate)
    raw_before = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    raw_after = {b["id"]: b for b in candidate.get("buildings") or [] if "id" in b}

    def coverage(building, all_buildings, raw, kind):
        targets = [b for b in all_buildings.values() if TILE_RES.get(b.type) == kind and
                   in_range(b.x - building.x, b.y - building.y, building.range + pad)]
        capacity = sum(max(1, float(raw.get(b.id, {}).get("cap") or 1)) for b in targets)
        return len(targets), capacity

    for i, b in before.items():
        info = raw_before[i]
        count = info.get("in_range")
        if b.type not in PRODUCERS or not isinstance(info.get("worker"), int) or info["worker"] < 0:
            continue
        if info.get("state") in UNFINISHED_STATES or not isinstance(count, dict) or not count:
            continue
        if not all(isinstance(v, int) and v >= 0 for v in count.values()):
            continue
        n0, c0 = coverage(b, before, raw_before, PRODUCERS[b.type])
        if n0 != sum(count.values()):
            continue
        if i not in after:
            return False
        n1, c1 = coverage(after[i], after, raw_after, PRODUCERS[b.type])
        if n1 < n0 or c1 + 1e-6 < c0:
            return False
    return True


PRESETS = {"effect": tr("효과 최대"), "gold_u": tr("금광 U자"), "plan": tr("계획도시")}
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
                 lane: Optional[Dict[Tuple[int, int], float]] = None, hub_w: float = 1.0, build_lane: float = 0.0):
        self.pieces = pieces
        self.res_weight = res_weight or {1: 1.0, 2: 1.0, 3: 1.0}
        self.hub_w = hub_w                 # 가이드 허브(잔병의 오두막·대위 막사·강철 요새) 효과에 곱하는 값 (GUIDE_HUB_W)
        self.lane = lane or {}
        self.lane_ids = [i for i, p in pieces.items() if lane_idle(p)] if self.lane else []
        # 앞 구역 가산: 자원 타일(자원 가중 × 용량) + build_lane 이면 공사 중 건물도 (쳐야 지어지므로 공이 닿는 발사대 앞에)
        self.lane_weight: Dict[int, float] = {}
        if self.lane:
            for i, p in pieces.items():
                if p.type in TILE_RES:
                    self.lane_weight[i] = self.res_weight_of(p)
                elif build_lane and p.unfinished:
                    self.lane_weight[i] = build_lane
        self.lane_tiles = list(self.lane_weight)
        self.preset_spots = set(preset_spots)
        self.preset_type = preset_type
        self.preset_cells = {s: {(s[0] + dx, s[1] + dy) for dx in range(2) for dy in range(2)} for s in self.preset_spots}
        self.pad = pad                     # 범위 판정 여유 (게임 값으로 맞춘 것, calibrate_range)
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

    def res_weight_of(self, p: "Piece") -> float:
        return self.res_weight.get(TILE_RES[p.type], 1.0) * p.cap

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
                if kind in HUB_KINDS:
                    val *= self.hub_w
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
            front = sum(self.lane.get(c, 0.0) * self.lane_weight[i]
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



# 가이드 배치(preset "guide"): Steam 공략 3개(Zarcos·Drake·apo)가 '전부 범위 안에'라고 하는 허브 효과를 이만큼 크게 친다
# — 잔병의 오두막(거처)·대위 막사(능력치 건물)·강철 요새(공사 중·무한 강화). 값은 임의: 3배면 거처 하나가 범위 밖일 때
# 1.8 (밀밭 두 개 가까이) 손해라 옮길 만하고, 자원 들판(타일당 1.0)보다 먼저 맞춘다.
HUB_KINDS = ("housing", "stat", "build")
GUIDE_HUB_W = 3.0
# 가이드 배치: 공사 중 건물의 앞 구역 가산 (자원 타일 가중 1 과 같은 식, 칸마다 앞 구역 값 × 이 값 × LANE_TILE_W). 값은 임의:
# 3×2 건물을 발사대 가까이 두면 약 +4 — 거처 두 개를 범위에 넣는 것만큼. 사용자 스크린샷(2026-09-26): 가이드 배치대로
# 옮겼더니 쳐야 하는 도박장이 마을 구석에 묻혀 어떤 각도로도 안 닿음.
GUIDE_BUILD_LANE = 5.0
MOVE_COST = 0.03        # 옮기는 건물 하나당 벌점 — 실제 기지에서 0.005(42번 옮김, 효과 +4%)·0.03(22번, +7.5%)·0.06(탐색 멈춤) 비교해 정함


# 네이티브 탐색: 한 번 0.5초(담금질 0.4 + 다듬기 0.1), 최소 4번, 연속 3번 더 좋은 배치가 없으면 멈춤
RESTART_SECONDS = 0.5
MIN_RESTARTS = 4
CONVERGED = 3

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
    """마무리: 건물마다 같은 크기의 모든 자리와 맞바꿔 보고 가장 좋은 것을 받아들인다 (더 나아지지 않을 때까지).
    네이티브 모듈(같은 규칙)이 있으면 그것으로."""
    from . import native_layout
    got = native_layout.polish(lay, scorer, origin0, seconds, MOVE_COST)
    if got is not None:
        org, cur = got
        lay.apply([(i, o) for i, o in org.items() if lay.origin.get(i) != o])
        return cur
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
    """영역 맞바꾸기 담금질. 가장 좋았던 배치(자리 표)와 점수(벌점 포함)를 돌려준다.
    네이티브 모듈(같은 규칙, 수십 배 많이 시도)이 있으면 그것으로."""
    from . import native_layout
    grid = lay.grid
    origin0 = origin0 if origin0 is not None else dict(lay.origin)
    got = native_layout.anneal(lay, scorer, seconds, rng.getrandbits(64), t0, t1, origin0, EXPLORE_COST)
    if got is not None:
        return got[0], got[1]
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
                  target: Dict[int, Tuple[int, int]], turn: Optional[Dict[int, int]] = None,
                  avoid: Set[Tuple[int, int]] = frozenset()
                  ) -> List[Tuple[int, Tuple[int, int], bool]]:
    """지금 배치 → 목표 배치로 가는 옮기기 순서 (한 번에 한 건물, 항상 빈 자리로만).

    1) 목표 자리가 비어 있는 건물을 모두 옮긴다.
    2) 막히면 막는 건물이 가장 적은 목표 자리 하나를 골라, 막는 건물들을 다른 건물 목표가 아닌 빈 곳에 잠시 비켜 둔다.
       그 건물은 다음 차례에 제자리로 간다 — 매 단계 최소 한 건물이 목표 자리에 확정되므로 반드시 끝난다.
    목표 자리끼리는 겹치지 않으므로 목표 자리를 막는 건물은 항상 아직 안 옮긴 건물이다.
    turn: 목표 자리에 회전해서 놓을 건물 → 시계 방향 90° 횟수. 목표 자리로 가는 한 번에 회전도 한다 (비켜 두기는 그대로).
    돌려주는 값: [(건물, 옮길 자리(왼쪽 아래 타일), 잠시 비켜 두기인지)]."""
    lay = Layout(grid, dict(pieces), cur)
    turn = turn or {}
    goal_piece = {i: rotated(pieces[i], turn.get(i, 0)) for i in target}
    pending = [i for i in target if lay.origin.get(i) != target[i] or i in turn]
    steps: List[Tuple[int, Tuple[int, int], bool]] = []

    def goal_cells(i: int) -> List[Tuple[int, int]]:
        return [(target[i][0] + dx, target[i][1] + dy) for dx, dy in goal_piece[i].rel]

    def blockers(i: int) -> Set[int]:
        return {lay.occ[c] for c in goal_cells(i) if lay.occ.get(c) not in (None, i)}

    def settle(i: int):
        for c in lay.cells(i):
            if lay.occ.get(c) == i:
                del lay.occ[c]
        lay.pieces[i] = goal_piece[i]
        lay.origin[i] = target[i]
        for c in lay.cells(i):
            lay.occ[c] = i

    for _ in range(len(pieces) + 5):
        moved = True
        while moved:
            moved = False
            for i in list(pending):
                if not blockers(i):
                    settle(i)
                    steps.append((i, target[i], False))
                    pending.remove(i)
                    moved = True
        if not pending:
            break
        goal = min(pending, key=lambda i: (len(blockers(i)), sum(len(pieces[j].rel) for j in blockers(i))))
        keep_clear = set(goal_cells(goal))
        reserved = {c for j in pending for c in goal_cells(j)}
        for j in sorted(blockers(goal), key=lambda j: len(pieces[j].rel)):
            p = lay.pieces[j]
            own = set(goal_cells(j))
            best = None
            for c, r in sorted(grid.tiles):
                cells = {(c + dx, r + dy) for dx, dy in p.rel}
                if not cells <= grid.tiles or cells & (keep_clear | avoid):
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
    stat_types = _stat_types(base)       # kNum(능력치 없음) 제외 — 전에는 모든 건물을 능력치 건물로 셌다
    # 가이드 배치는 '쳐야 지어지는' 건물(fixed 로 넘어온 공사 중 건물)을 제자리에 묶지 않고 발사대 쪽으로 옮긴다
    # 가이드 배치(plan·guide)는 공사 중 건물도 묶지 않는다 — 공이 닿는 자리로 옮긴다 (guide 는 reach_ok 로 확인)
    pieces, origin0 = pieces_from_base(base, grid, housing, () if preset in ("plan", "guide") else fixed)
    if not pieces:
        return None
    preset_spots = gold_u_spots(geo, grid) if preset == "gold_u" else []
    lane = lane_values(geo, grid)
    guide = preset == "guide"
    entrance = entrance_cells(geo, grid) if guide else set()
    scorer = Scorer(pieces, stat_types, housing, res_weight, pad, preset_spots, lane=lane,
                    hub_w=GUIDE_HUB_W if guide else 1.0, build_lane=GUIDE_BUILD_LANE if guide else 0.0)
    plain = Scorer(pieces, stat_types, housing, res_weight, pad)        # 보고용 (프리셋 가산 없는 범위 효과)
    # 발사대 앞 구역(자원 타일은 앞에, 치여도 얻는 게 없는 건물은 뒤로)은 담금질과 후보 비교 모두에 넣는다.
    # 실제 기지 3곳에서 앞 구역을 넣은 쪽이 채집 발사 계산·범위 효과 모두 좋았다 (53.2 → 56.2, 49.8 → 52.1).
    laned = scorer
    if preset == "plan":
        return _optimize_city(base, grid, pieces, origin0, scorer, plain, harvest_eval, pad, set(fixed))
    if preset == "gold_u":
        prefer = None                                                  # 사용자가 고른 틀이므로 이전 목표를 고집하지 않음
    lay0 = Layout(grid, pieces, origin0)
    e0, d0 = scorer.score(lay0)
    rng = random.Random(seed)
    cands = [(dict(origin0), e0)]
    turn_of: Dict[int, Dict[int, int]] = {}             # 후보(id) → 회전 (가이드 배치가 거처 덩어리를 다시 짤 때)
    starts: List[Tuple[Dict[int, Tuple[int, int]], Dict[int, int], Set[int]]] = [(origin0, {}, set())]
    groups = []

    def shaped_of(turn: Dict[int, int]) -> Dict[int, Piece]:
        return {i: rotated(p, turn.get(i, 0)) for i, p in pieces.items()} if turn else pieces

    def pinned_of(turn: Dict[int, int], pin: Set[int]) -> Dict[int, Piece]:
        """담금질용 모양: pin 에 든 건물은 못 옮기게 (가이드 규칙을 지킨 허브·거처를 다시 빼지 않게)."""
        sp = shaped_of(turn)
        if not pin:
            return sp
        return {i: (Piece(p.id, p.type, p.w, p.h, p.rel, False, p.range, p.factor, p.cap, p.unfinished)
                    if i in pin else p) for i, p in sp.items()}

    def add_cand(orig: Dict[int, Tuple[int, int]], turn: Dict[int, int]):
        sp = shaped_of(turn)
        o = canonicalize(sp, origin0, orig)
        cands.append((o, scorer.score(Layout(grid, sp, o))[0]))
        if turn:
            turn_of[id(o)] = dict(turn)
    producer_ids = []
    producer_targets = {}
    if guide:
        from .layout_city import PRODUCERS
        producer_ids = [i for i, p in pieces.items() if p.type in PRODUCERS and p.factor >= 1.0 and p.movable]
        producer_targets = {kind: list(scorer.targets[kind]) for kind in set(PRODUCERS.values())}

    def producer_coverage(orig, turn):
        sp = shaped_of(turn)
        out = {}
        for i in producer_ids:
            p = sp[i]
            x, y = grid.center(*orig[i], p.w, p.h)
            kind = PRODUCERS[p.type]
            out[i] = sum(in_range(grid.center(*orig[t], sp[t].w, sp[t].h)[0] - x,
                                  grid.center(*orig[t], sp[t].w, sp[t].h)[1] - y, p.range + pad)
                         for t in producer_targets[kind] if t != i)
        return out

    producer0 = producer_coverage(origin0, {})
    production_pin: Set[int] = set()
    if guide:
        raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
        # 게임이 직접 센 현재 범위와 기하 계산이 일치할 때만 가상 자리 후보를 만든다.
        # 후보 자리는 게임 값을 읽을 수 없으므로 이동 뒤 다시 게임의 in_range 로 검증해야 한다.
        producer_ids = [i for i in producer_ids if isinstance(raw.get(i, {}).get("in_range"), dict) and
                        bool(raw[i]["in_range"]) and
                        all(isinstance(v, int) and v >= 0 for v in raw[i]["in_range"].values()) and
                        sum(raw[i]["in_range"].values()) == producer0[i]]
        producer0 = {i: producer0[i] for i in producer_ids}
        for i in producer_ids:
            if pieces[i].unfinished:
                continue
            production_pin.add(i)
            x, y = lay0.center(i)
            production_pin.update(t for t in producer_targets[PRODUCERS[pieces[i].type]]
                                  if in_range(lay0.center(t)[0] - x, lay0.center(t)[1] - y, pieces[i].range + pad))
        # 가동 중 생산 구역을 보존하는 시작점. 뒤에서 탈락시키기만 하면 모든 탐색이 밀밭을 빼는 데 낭비된다.
        starts[0] = (origin0, {}, production_pin)

    def producer_gain(orig, turn):
        after = producer_coverage(orig, turn)
        if any(after[i] < count for i, count in producer0.items()):
            return 0
        return max((after[i] - count for i, count in producer0.items()), default=0)

    house_counts = {}
    if guide:
        # 이미 배정된 생산 건물이 자기 자원 타일을 거의 못 쓰면, 건물 하나만 빈 자리로 옮기는 후보를 먼저 넣는다.
        # 실제 기지: 채석장 범위 바위 1/12, 빈 자리 한 곳으로 옮기면 4/12인데 전체 점수 2% 문턱에 묻혔다.
        for i in producer_ids:
            p = pieces[i]
            kind = PRODUCERS[p.type]
            best_spot = None
            for at in sorted(grid.tiles):
                cells = {(at[0] + dx, at[1] + dy) for dx, dy in p.rel}
                if not cells <= grid.tiles or cells & entrance or any(lay0.occ.get(c) not in (None, i) for c in cells):
                    continue
                x, y = grid.center(*at, p.w, p.h)
                covered = sum(in_range(grid.center(*origin0[t], pieces[t].w, pieces[t].h)[0] - x,
                                       grid.center(*origin0[t], pieces[t].w, pieces[t].h)[1] - y, p.range + pad)
                              for t in producer_targets[kind] if t != i)
                if covered < producer0[i] + 2:
                    continue
                key = (covered, -abs(at[0] - origin0[i][0]) - abs(at[1] - origin0[i][1]))
                if best_spot is None or key > best_spot[0]:
                    best_spot = (key, at)
            if best_spot is not None:
                add_cand({**origin0, i: best_spot[1]}, {})
        # 가이드 배치: 허브 규칙을 먼저 고친 배치에서도 담금질을 시작한다 — 담금질만으로는 '다른 건물을 비켜야
        # 들어가는' 거처를 못 넣었다 (실제 기지: 잔병의 오두막 범위 거처 7/12 에서 멈춤 → 고치면 10/12, 12번 옮김)
        from .layout_guide import coverage, covered_members, hub_groups, repair
        groups = hub_groups(pieces, scorer)
        group0 = covered_members(grid, pieces, origin0, groups, pad)
        # 거처를 생산 구역 옆으로 옮기는 공략 규칙을 직접 후보화한다.
        # 범위 수치는 게임 현재 계수와 맞을 때만 사용하며, 다른 허브 효과를 잃는 후보는 아래에서 거른다.
        house_candidates = []
        for i, p in pieces.items():
            eff = EFFECTS.get(p.type)
            count = raw.get(i, {}).get("in_range")
            if (not eff or not isinstance(eff[0], int) or eff[3] != "upgraded" or p.factor < 1.0
                    or not p.movable or not isinstance(count, dict) or not count
                    or not all(isinstance(v, int) and v >= 0 for v in count.values())):
                continue
            targets = scorer.targets[eff[0]]
            def reached(at):
                x, y = grid.center(*at, p.w, p.h)
                return sum(in_range(lay0.center(t)[0] - x, lay0.center(t)[1] - y, p.range + pad) for t in targets)
            n0 = reached(origin0[i])
            if n0 != sum(count.values()):
                continue
            house_counts[i] = (eff[0], n0)
            house_spots = []
            for at in sorted(grid.tiles):
                cells = {(at[0] + dx, at[1] + dy) for dx, dy in p.rel}
                if not cells <= grid.tiles or cells & entrance or any(lay0.occ.get(c) not in (None, i) for c in cells):
                    continue
                n = reached(at)
                if n <= n0:
                    continue
                o = {**origin0, i: at}
                members = covered_members(grid, pieces, o, groups, pad)
                if any(not prev <= members[h] for h, prev in group0.items()):
                    continue
                key = (sum(map(len, members.values())), n, -abs(at[0] - origin0[i][0]) - abs(at[1] - origin0[i][1]))
                house_spots.append((key, o))
            # 범위가 같아도 충돌 위치가 달라 채집 경로는 다르다. 가장 가까운 한 자리로 확정하지 않는다.
            # 6은 검색량 제한이며 게임 효과 수치가 아니다. 각 후보는 아래 채집·도달 검사로 비교한다.
            house_spots.sort(key=lambda item: item[0], reverse=True)
            for _, o in house_spots[:6]:
                house_candidates.append(o)
                add_cand(o, {})
        variants = [(pieces, groups, lane)]
        if production_pin:
            protected = pinned_of({}, production_pin)
            variants.append((protected, groups, lane))
            # 건설 앞구역을 강제하는 단계가 안전한 거처 개선까지 취소하지 않도록 허브별 후보도 비교한다.
            variants.extend((protected, [group], {}) for group in groups)
        for repair_pieces, repair_groups, repair_lane in variants:
            rep = repair(grid, repair_pieces, origin0, repair_groups, pad, repair_lane, entrance)
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
            pin.update(i for i, p in rep_pieces.items() if p.unfinished and
                       any(lane.get(c, 0.0) > 0 for c in rep_lay.cells(i)))
            starts.append((rep[0], rep[1], pin))
            add_cand(rep[0], rep[1])
            lay = Layout(grid, pinned_of(rep[1], pin), rep[0])       # 담금질 없이 다듬기만 (채집 발사를 덜 흔든다)
            polish(lay, scorer, origin0, RESTART_SECONDS)
            add_cand(lay.origin, rep[1])
    search_note = ""
    from . import native_layout
    if native_layout._lib() is not None:
        # 네이티브 담금질은 초당 약 180만 번 (파이썬 약 2천 번) — 0.5초 한 번이면 파이썬 3초보다 좋은 배치로
        # 수렴한다 (실제 기지 4곳). 고정 시간 대신: 시작점을 바꿔 가며 돌리고, 연속 CONVERGED 번 더 좋은 배치가
        # 안 나오면 멈춘다 (seconds 는 안전 상한). 경우의 수가 너무 많아 '전부 보고 최적 증명'은 불가능.
        deadline = time.perf_counter() + seconds
        best, stale, k = -1e18, 0, 0
        while k < max(restarts, MIN_RESTARTS) or (stale < CONVERGED and time.perf_counter() < deadline):
            k += 1
            so, st, pin = starts[k % len(starts)]
            lay = Layout(grid, pinned_of(st, pin), so)
            o, _ = anneal(lay, scorer, RESTART_SECONDS * 0.8, rng, origin0=origin0)
            lay = Layout(grid, pinned_of(st, pin), o)
            v = polish(lay, scorer, origin0, RESTART_SECONDS * 0.2)
            add_cand(lay.origin, st)
            if v > best + max(1e-6, abs(best) * 0.001):
                best, stale = v, 0
            else:
                stale += 1
            if time.perf_counter() >= deadline:
                break
        search_note = (tr("탐색 {k}번 (연속 {stale}번 더 좋은 배치 없음 — 수렴)", k=k, stale=stale) if stale >= CONVERGED
                       else tr("탐색 {k}번 (시간 상한)", k=k))
        restarts = 0
    for k in range(restarts):
        sc = scorer
        so, st, pin = starts[(k + 1) % len(starts)]
        lay = Layout(grid, pinned_of(st, pin), so)
        o, _ = anneal(lay, sc, seconds * 0.7 / max(1, restarts), rng, origin0=origin0)
        lay = Layout(grid, pinned_of(st, pin), o)
        polish(lay, sc, origin0, seconds * 0.3 / max(1, restarts))
        add_cand(lay.origin, st)
    blds = buildings_from_base(base)

    def to_base(orig: Dict[int, Tuple[int, int]]) -> dict:
        turn = turn_of.get(id(orig))
        if turn:                                        # 회전해서 놓는 건물이 있으면 충돌 모양도 돌린다
            L = Layout(grid, shaped_of(turn), orig)
            return final_base(base, FullPlan(origin0, orig, {i: L.center(i) for i in orig}, 0.0, 0.0, {}, {},
                                             turned=turn))
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
    notes: List[str] = [search_note] if search_note else []
    # 이전 목표 배치(재배치 도중일 수 있음)도 후보로 — 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다
    # (실제 사용: 재배치 도중 다시 계산해 목표가 바뀌면서 채석장 둘레 돌을 빼게 됨)
    prefer_orig = None
    if prefer:
        po = dict(origin0)
        pturn: Dict[int, int] = {}
        for i, v in prefer.items():
            if i in pieces:
                x, y = v[0], v[1]
                k = int(v[2]) % 4 if len(v) > 2 else 0
                if k:
                    pturn[i] = k
                p = rotated(pieces[i], k)
                po[i] = (round((x - p.w * grid.size / 2 - grid.ox) / grid.size),
                         round((y - p.h * grid.size / 2 - grid.oy) / grid.size))
        sp = shaped_of(pturn)
        cells = [c for i in po for c in Layout(grid, sp, {}).cells(i, po[i])] if pieces else []
        ok = len(cells) == len(set(cells)) and set(cells) <= grid.tiles and \
            all(po[i] == origin0[i] and not pturn.get(i) for i in po if not pieces[i].movable)
        if ok and (po != origin0 or pturn):
            prefer_orig = po
            cands.append((po, scorer.score(Layout(grid, sp, po))[0]))
            if pturn:
                turn_of[id(po)] = pturn
    totals = {}
    best_cov = (0, 0, 0)

    def cov_of(o):
        if not guide:
            return (0, 0, 0)
        sp = shaped_of(turn_of.get(id(o), {}))
        lay = Layout(grid, sp, o)
        build_front = sum(1 for i, p in sp.items() if p.unfinished and
                          any(lane.get(c, 0.0) > 0 for c in lay.cells(i)))
        occupied = {c for i in o for c in lay.cells(i)}
        clear = len(entrance - occupied)
        # 입구와 허브가 앞구역 점수 때문에 희생되지 않게 한다. 실제 공사 도달은 reach_ok에서 별도로 검사한다.
        return (clear, coverage(grid, sp, o, groups, pad), build_front if reach_ok is None else 0)

    cov0 = cov_of(origin0)
    plain0 = plain.score(lay0)[0]

    def house_gain(orig):
        """게임과 맞춘 자원 거처의 빈 범위를 살리는 개선은 전체 점수 2%에 묻지 않는다."""
        sp = shaped_of(turn_of.get(id(orig), {}))
        lay = Layout(grid, sp, orig)
        gained = 0
        for i, (kind, n0) in house_counts.items():
            x, y = lay.center(i)
            n1 = sum(in_range(lay.center(t)[0] - x, lay.center(t)[1] - y, sp[i].range + pad)
                     for t in scorer.targets[kind])
            if n1 < n0:
                return 0
            gained += n1 - n0
        return gained

    def meaningful_guide(orig, hv):
        """이전 목표의 남은 이동이 실제 범위·채집·생산 규칙 중 하나라도 개선하는지."""
        turn = turn_of.get(id(orig), {})
        if cov_of(orig) > cov0 or producer_gain(orig, turn) >= 2 or house_gain(orig) > 0:
            return True
        if plain.score(Layout(grid, shaped_of(turn), orig))[0] > plain0 + 1e-6:
            return True
        return bool(hv and h0 and wsum(hv) > wsum(h0))

    for orig, s in cands:
        current = orig is cands[0][0]
        nb = to_base(orig)
        if guide and not current:
            from .layout_guide import preserves_guide
            if not preserves_production(base, nb, pad) or not preserves_guide(base, nb, pad):
                continue
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
        tn = turn_of.get(id(orig), {})
        total = rel_e + rel_h - SELECT_MOVE_COST * sum(1 for i in orig if orig[i] != origin0.get(i) or tn.get(i))
        totals[id(orig)] = (total, orig, s, hv)
        # 가이드 배치: 허브 범위 달성도가 먼저, 같으면 효과·채집·옮기는 수
        cov = cov_of(orig)
        if best is None or (cov, total) > (best_cov, best[0]):
            best, best_cov = (total, orig, s, hv), cov
    if best is None:
        return None
    _, orig, s, hv = best
    if prefer_orig is not None and id(prefer_orig) in totals and best[1] is not prefer_orig:
        pt = totals[id(prefer_orig)]
        if best[0] < pt[0] * 1.02 and (not guide or (cov_of(prefer_orig) >= best_cov and
                                                            producer_gain(prefer_orig, turn_of.get(id(prefer_orig), {})) >=
                                                            producer_gain(best[1], turn_of.get(id(best[1]), {})) and
                                                            house_gain(prefer_orig) >= house_gain(best[1]) and
                                                            meaningful_guide(prefer_orig, pt[3]))):
            best = pt
            notes.append(tr("이전 목표 배치를 유지했습니다 (새 계산의 개선이 2% 미만)."))
    _, orig, s, hv = best
    if guide and best[1] is prefer_orig and not meaningful_guide(orig, hv):
        best = totals[id(cands[0][0])]
        _, orig, s, hv = best
    if best[1] is prefer_orig and prefer_orig is not None:
        pass
    elif best[0] < 2.0 * 1.02 and not (guide and (cov_of(orig) > cov0 or house_gain(orig) > 0 or
                                                 producer_gain(orig, turn_of.get(id(orig), {})) >= 2)):
        notes.append(tr("이번 탐색에서 범위 효과와 채집 예상량을 합쳐 2% 넘는 개선을 찾지 못했습니다."))
        if alt is not None and alt[0] > 1.02:
            notes.append(tr("범위 효과만 보면 +{v0:.0f}% 배치가 있지만 채집 발사량이 {v1:+.0f}% 라 권하지 않음", v0=(alt[0] - 1) * 100, v1=(alt[1] - 1) * 100))
        orig, s, hv = cands[0][0], e0, h0
    if preset == "gold_u":
        # 프리셋은 사용자가 고른 틀: 2% 기준·'지금이 최적' 판단 없이 가장 좋은 프리셋 후보를 쓴다
        cand_best = max(cands[1:], key=lambda c: c[1]) if len(cands) > 1 else cands[0]
        orig, s, hv = cand_best[0], cand_best[1], (harvest_eval(to_base(cand_best[0])["geo"]) if harvest_eval else None)
        notes = [tr("금광 U자: 발사대 앞 자리 {v0}곳 중 {v1}곳에 금광", v0=len(preset_spots), v1=sum(1 for i, p in pieces.items() if p.type == 'kGoldMine' and orig.get(i) in set(preset_spots)))]
    turn = dict(turn_of.get(id(orig), {}))
    shaped = shaped_of(turn)
    lay = Layout(grid, shaped, orig)
    if lane:
        def front(o, sp=pieces):
            L = Layout(grid, sp, o)
            return sum(lane.get(c, 0.0) for i in laned.lane_ids if i in o for c in L.cells(i))

        def tiles(o, sp=pieces):
            L = Layout(grid, sp, o)
            return sum(1 for i in laned.lane_tiles if i in o and any(lane.get(c, 0.0) > 0 for c in L.cells(i)))
        f0, f1 = front(origin0), front(orig, shaped)
        if f0 - f1 >= 1.0:
            notes.append(tr("능력치·거처처럼 치여도 얻는 게 없는 건물을 발사대 앞에서 뒤·구석으로 (앞 구역 막음 {f0:.1f} → {f1:.1f})", f0=f0, f1=f1))
        t0, t1 = tiles(origin0), tiles(orig, shaped)
        if t1 - t0 >= 2:
            notes.append(tr("자원 타일을 발사대 앞으로 (앞 구역 자원 타일 {t0} → {t1}개 — 공을 던져 캐는 몫)", t0=t0, t1=t1))
    # 보고는 범위 효과만 (앞 구역 점수는 배치를 고를 때만 쓴다)
    e0, d0 = plain.score(Layout(grid, pieces, origin0))
    s, d1 = plain.score(lay)
    moved = sum(1 for i in orig if orig[i] != origin0[i] or turn.get(i))
    if guide:
        from .layout_city import guide_report
        head = [tr("가이드 배치: 지금 배치에서 출발해 핵심 범위 효과·생산 구역·입구를 보존하며 {v0}개 옮김", v0=moved)]
        if turn:
            head.append(tr("{v0}개는 회전해서 놓아야 빈틈 없이 맞물림 (ㄱ·ㅜ 자 모양 포함)", v0=len(turn)))
        notes = head + notes + guide_report(grid, shaped, orig, plain, pad)
    return FullPlan(origin0, orig, {i: lay.center(i) for i in orig}, e0, s, d0, d1, h0, hv, moved, notes,
                    turned=turn)


def _optimize_city(base: dict, grid: Grid, pieces: Dict[int, Piece], origin0: Dict[int, Tuple[int, int]],
                   scorer: Scorer, plain: Scorer, harvest_eval: Optional[Callable[[dict], List[int]]],
                   pad: float, hit: Set[int] = frozenset()) -> FullPlan:
    """가이드 배치: Steam 공략 3개 규칙으로 다시 짠다 (layout_city.plan_city)."""
    from .layout_city import plan_city
    geo = base.get("geo") or {}
    launcher = geo.get("launcher") or []
    lrc = (int((float(launcher[0]) - grid.ox) // grid.size), int((float(launcher[1]) - grid.oy) // grid.size)) \
        if len(launcher) >= 2 else None
    orig, notes, turn = plan_city(grid, pieces, origin0, lrc, scorer, pad, hit)
    shaped = {i: rotated(p, turn.get(i, 0)) for i, p in pieces.items()}
    orig = canonicalize(shaped, origin0, orig)
    e0, d0 = plain.score(Layout(grid, pieces, origin0))
    lay = Layout(grid, shaped, orig)
    s, d1 = plain.score(lay)
    centers = {i: lay.center(i) for i in orig}
    h0 = hv = None
    if harvest_eval:
        h0 = harvest_eval(geo)
        hv = harvest_eval(final_base(base, FullPlan(origin0, orig, centers, 0, 0, {}, {}, turned=turn)).get("geo") or {})
    moved = sum(1 for i in orig if orig[i] != origin0[i] or i in turn)
    return FullPlan(origin0, orig, centers, e0, s, d0, d1, h0, hv, moved, notes, turned=turn)


RANGE_TARGETS = {1: WHEAT_T, 2: WOOD_T, 3: STONE_T}
SHAPES = ("square", "circle")
PAD_CANDIDATES = (0.0, 0.5625, 1.125, 1.6875)     # 0 / 반 타일 / 한 타일 / 한 타일 반


def _stat_types(base: dict) -> Set[str]:
    from .construction_policy import has_captain_targets
    # 직접 '없음'이라고 받은 값은 옛 버전용 기본 목록으로 뒤집지 않는다.
    return {t for t in {b.get("type") for b in base.get("buildings") or []} if t
            and has_captain_targets({t}, base, STAT_FALLBACK)}


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
                   pad: float = 0.0, seconds: float = 2.5, *, resources: Optional[Sequence[int]] = None
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
    occ = Layout(grid, pieces, origin).occ
    from .construction_policy import is_recommended_building
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    ranges = {p.type: p.range for i, p in pieces.items() if p.range > 0 and raw.get(i, {}).get("lvl") == 0}
    protected = entrance_cells(base.get("geo") or {}, grid)
    out = []
    seen = set()
    for bp in blueprints:
        t = bp.get("type", "")
        if t not in EFFECTS or t in seen or not is_recommended_building(t) or bp.get("can_build_more") is False:
            continue
        seen.add(t)
        existing = [b for b in raw.values() if b.get("type") == t]
        if any(b.get("state") in UNFINISHED_STATES for b in existing):
            continue
        if EFFECTS[t][3] == "worker" and any(not isinstance(b.get("worker"), int) or b["worker"] < 0 for b in existing):
            continue
        size = bp.get("size")
        cost = bp.get("cost")
        radius = bp.get("range") or ranges.get(t)
        if not size or len(size) != 2 or not all(isinstance(v, int) and v > 0 for v in size) or not radius:
            continue
        if not isinstance(cost, (list, tuple)) or len(cost) != 4 or not all(isinstance(v, int) and v >= 0 for v in cost):
            continue
        if resources is not None and any(i >= len(resources) or resources[i] < v for i, v in enumerate(cost)):
            continue
        w, h = size
        new_id = -1
        p = Piece(new_id, t, w, h, frozenset((dx, dy) for dx in range(w) for dy in range(h)), True,
                  float(radius), 1.0)
        pcs = dict(pieces)
        pcs[new_id] = p
        scorer = Scorer(pcs, stats, housing, res_weight, pad)
        best = None
        for c, r in grid.tiles:
            cells = [(c + dx, r + dy) for dx, dy in p.rel]
            if any(x not in grid.tiles or x in occ or x in protected for x in cells):
                continue
            o = dict(origin)
            o[new_id] = (c, r)
            s, d = scorer.score(Layout(grid, pcs, o))
            if best is None or s > best[0]:
                x, y = grid.center(c, r, w, h)
                covered = sum(in_range(grid.center(*origin[j], pieces[j].w, pieces[j].h)[0] - x,
                                       grid.center(*origin[j], pieces[j].w, pieces[j].h)[1] - y, p.range + pad)
                              for j in scorer.targets[EFFECTS[t][0]] if j in origin)
                best = (s, (c, r), covered)
        if best is None:
            continue
        o = dict(origin)
        o[new_id] = best[1]
        # 건설 안내에는 기존 건물을 옮기는 순서가 없다. 안내하지 않은 대규모 재배치 이득을 끼워 넣지 않는다.
        lay, s, d = Layout(grid, pcs, o), best[0], {t: best[2]}
        if s - base_score > 0.05:
            no = lay.origin[new_id]
            out.append((t, grid.center(no[0], no[1], w, h), (w, h), s - base_score, int(d.get(t, 0)), 0))
    out.sort(key=lambda x: -x[3])
    return out


def suggest_tiles(base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0,
                  max_each: int = 8, *, build_options: Sequence[dict] = (), resources: Optional[Sequence[int]] = None
                  ) -> List[Tuple[str, Tuple[float, float], Tuple[int, int], float, int, int]]:
    """자원 타일을 더 사서 생산 건물 범위의 빈칸을 채우면 늘어나는 값 (사용자: '채석장 빈칸에 돌 넣는 게 낫지 않나').
    게임의 추가 건설 목록·크기·현재 비용만 사용한다. 상위형이 있으면 하위형을 대신 권하지 않는다.
    내부 점수는 빈자리 선택용이며 새 타일의 생산량 예측으로 표시하지 않는다.
    돌려주는 값: [(종류, 첫 자리 중심, 크기, 배치 우선순위, 놓을 개수, 0)]."""
    from .construction_policy import is_recommended_building, preferred_tile_types
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    have = {p.type for p in pieces.values()}
    raw = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    sources = {}
    for i, p in pieces.items():
        eff = EFFECTS.get(p.type)
        counted = raw.get(i, {}).get("in_range")
        if not eff or not isinstance(eff[0], int) or p.factor < 1 or not isinstance(counted, dict) or not counted:
            continue
        if not all(isinstance(v, int) and v >= 0 for v in counted.values()):
            continue
        x, y = grid.center(*origin[i], p.w, p.h)
        actual = sum(in_range(grid.center(*origin[j], q.w, q.h)[0] - x,
                              grid.center(*origin[j], q.w, q.h)[1] - y, p.range + pad)
                     for j, q in pieces.items() if TILE_RES.get(q.type) == eff[0])
        if actual == sum(counted.values()):
            sources[i] = (eff[0], x, y, p.range + pad)
    kinds = {v[0] for v in sources.values()}
    available = {bp.get("type"): bp for bp in build_options if isinstance(bp, dict)
                 and bp.get("type") in TILE_RES and bp.get("can_build_more") is not False
                 and is_recommended_building(bp.get("type", ""))}
    selected = preferred_tile_types(available, have)
    protected = entrance_cells(base.get("geo") or {}, grid)
    remaining = list(resources) if resources is not None else None
    pcs, o = dict(pieces), dict(origin)
    out = []
    for t in sorted(selected, key=lambda t: (-(res_weight or {}).get(TILE_RES[t], 1.0), t)):
        if TILE_RES[t] not in kinds:
            continue
        option = available[t]
        size, cost = option.get("size"), option.get("cost")
        if not isinstance(size, (list, tuple)) or len(size) != 2 or not all(isinstance(v, int) and v > 0 for v in size):
            continue
        if not isinstance(cost, (list, tuple)) or len(cost) != 4 or not all(isinstance(v, int) and v >= 0 for v in cost):
            continue
        s0 = Scorer(pcs, stats, housing, res_weight, pad).score(Layout(grid, pcs, o))[0]
        total, first, n = 0.0, None, 0
        for k in range(max_each):
            if remaining is not None and any(i >= len(remaining) or remaining[i] < v for i, v in enumerate(cost)):
                break
            nid = min([-100] + list(pcs)) - 1
            pcs[nid] = Piece(nid, t, size[0], size[1], frozenset((dx, dy) for dx in range(size[0]) for dy in range(size[1])),
                             True, 0.0, 1.0, 1.0)
            scorer = Scorer(pcs, stats, housing, res_weight, pad)
            occ = Layout(grid, {i: pcs[i] for i in o}, o).occ
            best = None
            for c, r in grid.tiles:
                cells = [(c + dx, r + dy) for dx, dy in pcs[nid].rel]
                if any(x not in grid.tiles or x in occ or x in protected for x in cells):
                    continue
                tx, ty = grid.center(c, r, size[0], size[1])
                if not any(kind == TILE_RES[t] and in_range(tx - x, ty - y, radius)
                           for kind, x, y, radius in sources.values()):
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
            if remaining is not None:
                remaining = [v - cost[i] if i < 4 else v for i, v in enumerate(remaining)]
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
            what = (tr("일꾼 배정") if EFFECTS[p.type][3] == "worker"
                    else tr("{v0} 레벨 {v1}", v0=ch[1], v1=_next_house_level(p.type, CHAR_LEVELS.get(ch[0], 0))) if ch else tr("강화"))
            out.append((i, p.type, what, s1 - s0))
    out.sort(key=lambda x: -x[3])
    return out


# 철거 후보 대상: 여러 개 지어도 되고, Scorer 가 값을 제대로 아는 생산 건물만.
# 뺀 것들 — 잘못 추천하면 되돌릴 수 없는 손해라 안전하게 좁힘:
#   금광(kGoldMine): Scorer 는 범위 효과만 보고 채집 발사 가치를 모름 (harvest_sim 이 따로 계산) — 항상 0으로 보여 잘못 추천함.
#   거처(주택 6종): 캐릭터 한 명에 고정 배정(house_characters)이라 보통 하나뿐이고, 철거하면 그 캐릭터 보너스를 통째로 잃음.
#   능력치·무한 강화 건물: 보통 하나뿐이고 레벨 투자가 크다.
DEMOLISH_CANDIDATE_TYPES = {"kIdleFarm", "kIdleLumberyard", "kIdleStoneMine"}
DEMOLISH_MAX_SCORE = 0.5   # 이 밑으로 기여하면 후보 (범위 안에 캘 타일이 없다는 뜻) — 근거 없이 임의로 정함
# 있기만 하면 철거 후보 — Steam 가이드·사용자 결정 (자리를 차지하고, 금광은 일꾼 캐릭터를 발사에서 뺀다)
GUIDE_DEMOLISH = {
    "kWarRoom": tr("쓸모없음 — 30분에 골드 1천·돌 100, 게임을 끄면 멈춤 (Steam 가이드 Zarcos·apo·Drake 모두)"),
    "kIdleLauncher": tr("효과가 별로라 안 지음 (Steam 가이드 Drake)"),
    "kGoldMine": tr("무한 모드로 골드가 모자라지 않음 — 금광은 권하지 않음 (Zarcos), 일꾼을 발사에 돌릴 수 있음"),
}
TILE_DEMOLISH_REASON = tr("범위 안에 캘 자원 타일이 없어 지금 자리에서 거의 도움이 안 됨 — 옮기거나 철거 고려")


def suggest_demolish(base: dict, res_weight: Optional[Dict[int, float]] = None, pad: float = 0.0,
                     limit: int = 8) -> List[Tuple[int, str, float, str]]:
    """지금 배치에서 있으나 마나 한 생산 건물(범위 안에 캘 타일이 없어 점수에 거의 안 보탬) 철거 후보.
    돌려주는 값: [(건물 id, 종류, 지금 기여하는 점수, 이유)] — 가이드 철거 후보(GUIDE_DEMOLISH)가 먼저, 나머지는 점수가 낮을수록 위.
    공사 중인 건물은 빼고(방금 짓기 시작한 걸 철거하라고 하면 안 됨), DEMOLISH_CANDIDATE_TYPES 만 본다."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return []
    housing = housing_types()
    stats = _stat_types(base)
    pieces, origin = pieces_from_base(base, grid, housing)
    s0 = Scorer(pieces, stats, housing, res_weight, pad).score(Layout(grid, pieces, origin))[0]
    out = [(i, p.type, 0.0, GUIDE_DEMOLISH[p.type]) for i, p in sorted(pieces.items())
           if p.type in GUIDE_DEMOLISH and not p.unfinished]
    for i, p in pieces.items():
        if p.type not in DEMOLISH_CANDIDATE_TYPES or p.unfinished:
            continue
        pcs = {k: v for k, v in pieces.items() if k != i}
        org = {k: v for k, v in origin.items() if k != i}
        s1 = Scorer(pcs, stats, housing, res_weight, pad).score(Layout(grid, pcs, org))[0] if pcs else 0.0
        marginal = round(s0 - s1, 2)
        if marginal < DEMOLISH_MAX_SCORE:
            out.append((i, p.type, marginal, TILE_DEMOLISH_REASON))
    out.sort(key=lambda x: (x[1] not in GUIDE_DEMOLISH, x[2]))
    return out[:limit]


def final_base(base: dict, plan: FullPlan) -> dict:
    """최적 배치를 적용한 기지 (건물 위치 + 충돌 모양)."""
    from .layout import move_geo, turn_geo
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    blds = buildings_from_base(base)
    g, out_b = geo, []
    cur = dict(blds)
    for b in base.get("buildings") or []:
        i = b.get("id")
        turn = plan.turned.get(i, 0)
        if i in plan.centers_after and (plan.origin_after.get(i) != plan.origin_before.get(i) or turn) and i in cur:
            cx, cy = plan.centers_after[i]
            ob = cur[i]
            if turn:
                g = turn_geo(g, i, (ob.x, ob.y), (cx, cy), turn)
                rot = (ob.rot + turn) % 4
            else:
                g = move_geo(g, cur, i, (cx, cy))
                rot = ob.rot
            cur[i] = Bld(ob.id, ob.type, cx, cy, ob.tw, ob.th, rot, ob.range)
            out_b.append(dict(b, x=round(cx, 3), y=round(cy, 3), rot=rot))
        else:
            out_b.append(b)
    return dict(base, buildings=out_b, geo=g)


def remaining_moves(base: dict, final: Dict[int, Tuple[float, float]],
                    final_rot: Optional[Dict[int, int]] = None) -> List[Move]:
    """지금(재배치 도중) 배치에서 목표 배치(건물별 중심)까지 남은 옮기기. 사용자가 다른 순서로 옮겨도 다시 맞춘다.
    final_rot: 건물별 목표 회전(게임 rot 값, +1 = 시계 방향 90°)."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None or not final:
        return []
    pieces, cur = pieces_from_base(base, grid, housing_types())
    rots = {b["id"]: int(b.get("rot") or 0) for b in base.get("buildings") or [] if "id" in b}
    turn = {i: (r - rots.get(i, 0)) % 4 for i, r in (final_rot or {}).items()
            if i in pieces and (r - rots.get(i, 0)) % 4}
    target = {}
    for i, (x, y) in final.items():
        if i in pieces:
            p = rotated(pieces[i], turn.get(i, 0))
            target[i] = (round((x - p.w * grid.size / 2 - grid.ox) / grid.size),
                         round((y - p.h * grid.size / 2 - grid.oy) / grid.size))
    for i in cur:                     # 계획 뒤에 새로 지은 건물(계획에 없음)은 그 자리에 둔다 — 없으면 KeyError 로 기지 화면이 멈춤
        target.setdefault(i, cur[i])
    return _moves(grid, pieces, cur, target, turn, rots)


def _moves(grid: Grid, pieces: Dict[int, Piece], cur: Dict[int, Tuple[int, int]], target: Dict[int, Tuple[int, int]],
           turn: Dict[int, int], rots: Dict[int, int]) -> List[Move]:
    out = []
    for i, o, park in move_sequence(grid, pieces, cur, target, turn):
        k = 0 if park else turn.get(i, 0)
        p = rotated(pieces[i], k)
        out.append(Move(i, grid.center(o[0], o[1], p.w, p.h), 0.0,
                        tr("잠시 비켜 두기 (자리 비우기)") if park else
                        tr("{v0} 이 자리로", v0=turn_text(k)) if k else tr("가이드 배치 자리로"),
                        rot=(rots.get(i, 0) + k) % 4 if k else -1))
    return out


def turn_text(k: int) -> str:
    """회전 안내 (게임 회전 버튼 = 시계 방향 90°)."""
    return {1: tr("회전 버튼 1번 눌러서"), 2: tr("회전 버튼 2번 눌러서"), 3: tr("회전 버튼 3번 눌러서")}.get(k % 4, "")


def plan_moves(plan: FullPlan, base: dict) -> List[Move]:
    """최적 배치로 가는 옮기기 (Move 목록, 잠시 비켜 두기 포함)."""
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    pieces, cur = pieces_from_base(base, grid, housing_types())
    target = {i: plan.origin_after.get(i, o) for i, o in cur.items()}
    rots = {b["id"]: int(b.get("rot") or 0) for b in base.get("buildings") or [] if "id" in b}
    return _moves(grid, pieces, cur, target, {i: k for i, k in plan.turned.items() if i in cur}, rots)
