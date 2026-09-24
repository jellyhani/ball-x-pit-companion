"""기지 배치 효율과 자리 바꾸기 추천 (게임 연동 1.6 의 건물 위치·크기·범위 + 채집 궤적 계산).

배치 점수 = 범위 효과 점수 + 채집 발사 예상량(궤적 계산)의 가중 합.
범위 효과(게임 건물 설명):
  농장·야적장·채석장: 배정된 캐릭터가 근처 밭·숲·바위에서 주기적으로 채집
  별장·야영지·바위 언덕: 근처 밀밭·숲·바위 재생 속도 상승
  외딴 집·아늑한 집·극장: 근처 밭·숲·바위에서 주기적으로 채집
  대저택: 근처 건물마다 분당 골드
'근처'는 게임이 건물마다 알려 주는 범위(GetRange, 월드 단위) 안에 중심이 들어오는 것으로 본다 (추정).
옮기기: 크기·방향이 같은 두 건물 자리 바꾸기, 또는 빈 자리로 옮기기(기지 타일 격자 기준, 실제 건물 58개가
격자에 정확히 맞는 것 확인). 새 건물: 지을 수 있는 설계도 중 범위 효과 건물은 효과가 가장 큰 빈 자리를 권한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .harvest import RES_BY_TYPE

WHEAT, WOOD, STONE = 1, 2, 3
# 범위 효과: 건물 → (영향 받는 자원 종류, 가중치, 설명)
EFFECTS: Dict[str, Tuple[int, float, str]] = {
    "kIdleFarm": (WHEAT, 1.0, "근처 밭에서 주기적으로 채집"),
    "kIdleLumberyard": (WOOD, 1.0, "근처 숲에서 주기적으로 채집"),
    "kIdleStoneMine": (STONE, 1.0, "근처 바위에서 주기적으로 채집"),
    "kVilla": (WHEAT, 0.8, "근처 밀밭 재생 속도 상승"),
    "kCampground": (WOOD, 0.8, "근처 숲 재생 속도 상승"),
    "kRockyHill": (STONE, 0.8, "근처 바위 재생 속도 상승"),
    "kSingleFamilyHome": (WHEAT, 0.6, "근처 밭에서 주기적으로 채집"),
    "kCozyHome": (WOOD, 0.6, "근처 숲에서 주기적으로 채집"),
    "kHovel": (STONE, 0.6, "근처 바위에서 주기적으로 채집"),
}
TILE_TYPES = {"kWheatField": WHEAT, "kDenseWheat": WHEAT, "kForest": WOOD, "kGrandTree": WOOD,
              "kBoulder": STONE, "kGraniteSlab": STONE, "kStonePile": STONE}


@dataclass
class Bld:
    id: int
    type: str
    x: float
    y: float
    tw: int
    th: int
    rot: int
    range: float

    @property
    def footprint(self) -> Tuple[int, int]:
        return (self.th, self.tw) if self.rot % 2 else (self.tw, self.th)


@dataclass
class Swap:
    a: int
    b: int
    gain: float
    reason: str


@dataclass
class Move:
    """빈 자리로 옮기기. to = 옮긴 뒤 중심 (월드 좌표)."""
    a: int
    to: Tuple[float, float]
    gain: float
    reason: str
    b: int = -1              # 자리 바꾸기와 같은 모양으로 다루기 위한 자리 (-1 = 빈 자리)
    target: int = -1         # 길 열기: 이 옮기기로 작업자가 닿게 되는 미완성 건물 id (gain = 예상 타격 수)


@dataclass
class NewSpot:
    type: str
    center: Tuple[float, float]
    size: Tuple[int, int]
    covered: int             # 범위 안의 맞는 자원 타일 수
    reason: str


@dataclass
class LayoutPlan:
    score_before: float
    score_after: float
    swaps: List[Swap] = field(default_factory=list)
    detail_before: Dict[str, float] = field(default_factory=dict)
    detail_after: Dict[str, float] = field(default_factory=dict)
    harvest_before: Optional[List[int]] = None
    harvest_after: Optional[List[int]] = None
    new_spots: List[NewSpot] = field(default_factory=list)
    final: Dict[int, Tuple[float, float]] = field(default_factory=dict)   # 전체 재배치: 건물별 목표 중심
    notes: List[str] = field(default_factory=list)
    reach_after: Dict[int, int] = field(default_factory=dict)   # 최적 배치 뒤 미완성 건물별 최대 타격 수 (0 = 여전히 안 닿음)
    builds: List[tuple] = field(default_factory=list)            # 새로 지을 건물 추천 (종류, 중심, 크기, 늘어나는 점수, 대상 수)
    activations: List[tuple] = field(default_factory=list)       # 강화·일꾼 배정으로 켜지는 효과 (id, 종류, 할 일, 점수)
    calibration: Tuple[float, int, int] = (0.0, 0, 0)            # 범위 판정 게임 값 비교 (여유, 맞음, 비교 수)
    preset: str = "effect"                                       # effect(효과 최대) | gold_u(금광 U자)
    alternatives: Dict[str, "LayoutPlan"] = field(default_factory=dict)   # 다른 프리셋으로 계산한 배치
    preset_spots: List[Tuple[float, float]] = field(default_factory=list) # 프리셋 자리 중심 (금광 U자)


# ---- 기지 타일 격자 ----
@dataclass
class Grid:
    ox: float
    oy: float
    size: float
    tiles: set               # 산 청크의 모든 타일 (col, row)

    def cells(self, cx: float, cy: float, w: int, h: int) -> set:
        c0 = round((cx - w * self.size / 2 - self.ox) / self.size)
        r0 = round((cy - h * self.size / 2 - self.oy) / self.size)
        return {(c0 + i, r0 + j) for i in range(w) for j in range(h)}

    def center(self, c0: int, r0: int, w: int, h: int) -> Tuple[float, float]:
        return (self.ox + (c0 + w / 2) * self.size, self.oy + (r0 + h / 2) * self.size)


def grid_from_geo(geo: dict) -> Optional[Grid]:
    try:
        size = float(geo["space_w"])
        cw, ch = int(geo["chunk_w"]), int(geo["chunk_h"])
        chunks = [tuple(c) for c in geo["chunks"]]
        ox = float(geo["left"]) - min(c[0] for c in chunks) * cw * size
        oy = float(geo["bottom"]) - min(c[1] for c in chunks) * ch * size
        tiles = {(cx * cw + i, cy * ch + j) for cx, cy in chunks for i in range(cw) for j in range(ch)}
        return Grid(ox, oy, size, tiles)
    except (KeyError, TypeError, ValueError):
        return None


def _inside(shape: dict, x: float, y: float) -> bool:
    if shape.get("shape") == "circle":
        cx, cy = shape["c"]
        return math.hypot(x - cx, y - cy) <= float(shape["r"]) + 1e-6
    pts = shape.get("pts") or []
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def shape_masks(geo: dict, blds: Dict[int, "Bld"], grid: Grid) -> Dict[int, set]:
    """건물마다 실제 충돌 모양이 덮는 타일 (건물 사각형 왼쪽 아래 기준 상대 좌표).

    실제 게임 확인: ㄱ자 건물(별장·아늑한 집 등)의 빈 모서리 타일을 옆 건물이 쓴다 → 사각형으로 보면 10칸이 겹친다.
    원형(바위)은 중심 타일만 차지한다.
    """
    cols: Dict[int, List[dict]] = {}
    for c in geo.get("colliders") or []:
        cols.setdefault(int(c.get("id", -1)), []).append(c)
    out: Dict[int, set] = {}
    for i, b in blds.items():
        shapes = cols.get(i)
        if not shapes:
            continue
        w, h = b.footprint
        c0 = round((b.x - w * grid.size / 2 - grid.ox) / grid.size)
        r0 = round((b.y - h * grid.size / 2 - grid.oy) / grid.size)
        mask = set()
        for dx in range(w):
            for dy in range(h):
                cx, cy = grid.center(c0 + dx, r0 + dy, 1, 1)
                if any(_inside(sh, cx, cy) for sh in shapes):
                    mask.add((dx, dy))
        if mask:
            out[i] = mask
    return out


def building_cells(b: "Bld", grid: Grid, mask: Optional[set] = None) -> set:
    w, h = b.footprint
    c0 = round((b.x - w * grid.size / 2 - grid.ox) / grid.size)
    r0 = round((b.y - h * grid.size / 2 - grid.oy) / grid.size)
    if mask is None:
        return {(c0 + i, r0 + j) for i in range(w) for j in range(h)}
    return {(c0 + i, r0 + j) for i, j in mask}


def occupied(blds: Dict[int, "Bld"], grid: Grid, skip: Sequence[int] = (),
             masks: Optional[Dict[int, set]] = None) -> set:
    out = set()
    for i, b in blds.items():
        if i in skip:
            continue
        out |= building_cells(b, grid, (masks or {}).get(i))
    return out


def free_spots(grid: Grid, occ: set, w: int, h: int) -> List[Tuple[float, float]]:
    out = []
    for (c, r) in grid.tiles:
        cells = {(c + i, r + j) for i in range(w) for j in range(h)}
        if cells <= grid.tiles and not (cells & occ):
            out.append(grid.center(c, r, w, h))
    return out


def buildings_from_base(base: dict) -> Dict[int, Bld]:
    out = {}
    for b in base.get("buildings") or []:
        try:
            out[int(b["id"])] = Bld(int(b["id"]), b.get("type", ""), float(b["x"]), float(b["y"]),
                                    int(b.get("tw") or 1), int(b.get("th") or 1), int(b.get("rot") or 0),
                                    float(b.get("range") or 0))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def effect_score(blds: Dict[int, Bld]) -> Tuple[float, Dict[str, float]]:
    """범위 효과 점수: 효과 건물마다 범위 안의 맞는 자원 타일 수 × 가중치."""
    tiles = [(b.x, b.y, TILE_TYPES[b.type]) for b in blds.values() if b.type in TILE_TYPES]
    detail: Dict[str, float] = {}
    total = 0.0
    for b in blds.values():
        eff = EFFECTS.get(b.type)
        if not eff or b.range <= 0:
            continue
        kind, w, _ = eff
        n = sum(1 for x, y, k in tiles if k == kind and abs(x - b.x) <= b.range + 1e-6 and abs(y - b.y) <= b.range + 1e-6)
        total += w * n
        detail[b.type] = detail.get(b.type, 0) + n
    return total, detail


def swap_positions(blds: Dict[int, Bld], a: int, b: int) -> Dict[int, Bld]:
    out = dict(blds)
    A, B = blds[a], blds[b]
    out[a] = Bld(A.id, A.type, B.x, B.y, A.tw, A.th, A.rot, A.range)
    out[b] = Bld(B.id, B.type, A.x, A.y, B.tw, B.th, B.rot, B.range)
    return out


def move_geo(geo: dict, blds: Dict[int, "Bld"], a: int, to: Tuple[float, float]) -> dict:
    """한 건물의 충돌 모양을 새 중심으로 평행 이동."""
    A = blds[a]
    d = (to[0] - A.x, to[1] - A.y)
    cols = []
    for c in geo.get("colliders") or []:
        if int(c.get("id", -1)) != a:
            cols.append(c)
            continue
        c2 = dict(c)
        if "pts" in c:
            c2["pts"] = [[x + d[0], y + d[1]] for x, y in c["pts"]]
        if "c" in c:
            c2["c"] = [c["c"][0] + d[0], c["c"][1] + d[1]]
        cols.append(c2)
    g = dict(geo)
    g["colliders"] = cols
    return g


def swap_geo(geo: dict, blds: Dict[int, Bld], a: int, b: int) -> dict:
    """채집 궤적 계산용: 두 건물의 충돌 모양을 서로의 자리로 평행 이동한다."""
    A, B = blds[a], blds[b]
    shift = {a: (B.x - A.x, B.y - A.y), b: (A.x - B.x, A.y - B.y)}
    cols = []
    for c in geo.get("colliders") or []:
        d = shift.get(int(c.get("id", -1)))
        if d is None:
            cols.append(c)
            continue
        c2 = dict(c)
        if "pts" in c:
            c2["pts"] = [[x + d[0], y + d[1]] for x, y in c["pts"]]
        if "c" in c:
            c2["c"] = [c["c"][0] + d[0], c["c"][1] + d[1]]
        cols.append(c2)
    g = dict(geo)
    g["colliders"] = cols
    return g


def _movable(b: Bld) -> bool:
    # 엘리베이터·기지 핵심 건물은 옮기지 않는다고 본다 (확인 필요: 게임이 막는 건물)
    return b.type not in ("kHome",) and b.tw > 0


def _col_boxes(geo: dict) -> Dict[int, Tuple[float, float, float, float]]:
    """건물 id → 충돌 모양 경계 상자 (월드 좌표)."""
    out: Dict[int, Tuple[float, float, float, float]] = {}
    for c in geo.get("colliders") or []:
        if "pts" in c and c["pts"]:
            xs, ys = [q[0] for q in c["pts"]], [q[1] for q in c["pts"]]
            box = (min(xs), min(ys), max(xs), max(ys))
        elif "c" in c:
            r = float(c.get("r") or 0)
            box = (c["c"][0] - r, c["c"][1] - r, c["c"][0] + r, c["c"][1] + r)
        else:
            continue
        i = int(c.get("id", -1))
        o = out.get(i)
        out[i] = box if o is None else (min(o[0], box[0]), min(o[1], box[1]), max(o[2], box[2]), max(o[3], box[3]))
    return out


def moved_base(base: dict, blds: Dict[int, Bld], a: int, to: Tuple[float, float]) -> dict:
    """한 건물을 옮긴 뒤의 기지 (건물 위치 + 충돌 모양)."""
    out = dict(base)
    out["geo"] = move_geo(base.get("geo") or {}, blds, a, to)
    out["buildings"] = [dict(b, x=to[0], y=to[1]) if b.get("id") == a else b for b in base.get("buildings") or []]
    return out


def plan_access(base: dict, targets: Sequence[int], reach_fn, max_moves: int = 2,
                max_tries: int = 8) -> Tuple[List[Move], dict, set]:
    """어떤 발사 각도로도 작업자가 닿지 않는 미완성 건물: 옆 건물 하나를 빈 자리로 옮겨 길을 여는 방법을 찾는다.

    실제 기지 확인: 강화 공사 중인 학교·영사관이 건물에 사방이 막혀 0%에서 멈춰 있었다.
    reach_fn(geo) -> {건물 id: 모든 각도 중 최대 타격 수}. 옮길 건물은 목표에 붙어 있는 것 중
    발사대 쪽(아래)·작은 것부터, 빈 자리는 범위 효과 점수가 가장 덜 줄어드는 곳.
    돌려주는 값: (옮기기 목록, 옮긴 뒤 기지, 비워 둬야 할 타일 — 뒤의 옮기기가 길을 다시 막지 않게).
    """
    blds = buildings_from_base(base)
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    if not blds or grid is None or not targets:
        return [], base, set()
    reach = reach_fn(geo)
    moves: List[Move] = []
    reserved: set = set()
    cur_base = base
    for t in targets:
        if len(moves) >= max_moves or reach.get(t) or t not in blds:
            continue
        cur = buildings_from_base(cur_base)
        boxes = _col_boxes(cur_base.get("geo") or {})
        tb = boxes.get(t)
        if tb is None:
            continue
        m = 0.3
        near = [i for i, bb in boxes.items() if i != t and i in cur and _movable(cur[i]) and i not in targets
                and not (bb[2] + m < tb[0] or tb[2] + m < bb[0] or bb[3] + m < tb[1] or tb[3] + m < bb[1])]
        near.sort(key=lambda i: (boxes[i][1], cur[i].tw * cur[i].th))
        masks = shape_masks(cur_base.get("geo") or {}, cur, grid)
        found = None
        for i in near[:max_tries]:
            w, h = cur[i].footprint
            here = building_cells(cur[i], grid, masks.get(i))
            occ = occupied(cur, grid, skip=[i], masks=masks)
            spots = free_spots(grid, occ | here | reserved, w, h)
            if not spots:
                continue
            b = cur[i]

            def score(sp):
                moved = dict(cur)
                moved[i] = Bld(b.id, b.type, sp[0], sp[1], b.tw, b.th, b.rot, b.range)
                return effect_score(moved)[0]
            to = max(spots, key=score)
            nb = moved_base(cur_base, cur, i, to)
            r2 = reach_fn(nb["geo"])
            if r2.get(t):
                found = (Move(i, to, float(r2[t]), "미완성 건물로 가는 길 열기", target=t), nb, r2, here)
                break
        if found:
            moves.append(found[0])
            cur_base, reach = found[1], found[2]
            reserved |= found[3]
    return moves, cur_base, reserved


def plan_swaps(base: dict, max_swaps: int = 6, harvest_eval=None,
               blueprints: Sequence[dict] = (), reserved: set = frozenset()) -> Optional[LayoutPlan]:
    """범위 효과 점수를 올리는 옮기기 순서 (욕심쟁이 탐색): 같은 크기 건물 맞바꾸기 + 빈 자리로 옮기기.

    harvest_eval(geo) -> [골드, 밀, 나무, 돌] 를 주면 옮긴 뒤 채집 발사 예상량도 비교하고,
    채집량을 10% 넘게 줄이는 옮기기는 뺀다. blueprints: [{type, tw, th}] 새로 지을 수 있는 건물.
    """
    blds = buildings_from_base(base)
    if not blds:
        return None
    geo = dict(base.get("geo") or {})
    grid = grid_from_geo(geo)
    masks = shape_masks(geo, blds, grid) if grid else {}
    before, detail_b = effect_score(blds)
    hv_before = harvest_eval(geo) if harvest_eval else None
    cur, cur_geo, cur_score = blds, geo, before
    steps: List = []
    used = set()
    for _ in range(max_swaps):
        cands = []
        ids = [i for i, b in cur.items() if _movable(b)]
        relevant = [i for i in ids if cur[i].type in EFFECTS or cur[i].type in TILE_TYPES]
        for i in relevant:
            for j in ids:
                if i == j or cur[i].footprint != cur[j].footprint or cur[i].type == cur[j].type:
                    continue
                if ("s", min(i, j), max(i, j)) in used:
                    continue
                cand = swap_positions(cur, i, j)
                sc, _ = effect_score(cand)
                if sc > cur_score + 0.5:
                    cands.append((sc, ("s", i, j), cand))
            if grid is not None:
                w, h = cur[i].footprint
                occ = occupied(cur, grid, skip=[i], masks=masks)
                for to in free_spots(grid, occ | reserved, w, h):
                    if ("m", i, to) in used:
                        continue
                    moved = dict(cur)
                    b = cur[i]
                    moved[i] = Bld(b.id, b.type, to[0], to[1], b.tw, b.th, b.rot, b.range)
                    sc, _ = effect_score(moved)
                    if sc > cur_score + 0.5:
                        cands.append((sc, ("m", i, to), moved))
        if not cands:
            break
        cands.sort(key=lambda t: -t[0])
        accepted = False
        for sc, key, cand in cands[:12]:           # 채집량 조건에 걸리면 다음 후보
            if key[0] == "s":
                new_geo = swap_geo(cur_geo, cur, key[1], key[2])
            else:
                new_geo = move_geo(cur_geo, cur, key[1], key[2])
            if harvest_eval and hv_before is not None:
                hv = harvest_eval(new_geo)
                if sum(hv) < 0.9 * sum(hv_before):
                    used.add(key if key[0] == "m" else ("s", min(key[1], key[2]), max(key[1], key[2])))
                    continue
            a = cur[key[1]]
            if key[0] == "s":
                b = cur[key[2]]
                eff = EFFECTS.get(a.type) or EFFECTS.get(b.type)
                steps.append(Swap(key[1], key[2], sc - cur_score, eff[2] if eff else "자원 타일을 효과 건물 범위 안으로"))
                used.add(("s", min(key[1], key[2]), max(key[1], key[2])))
            else:
                eff = EFFECTS.get(a.type)
                steps.append(Move(key[1], key[2], sc - cur_score,
                                  eff[2] + " — 빈 자리로" if eff else "자원 타일을 효과 건물 범위 안 빈 자리로"))
                used.add(key)
            cur, cur_geo, cur_score = cand, new_geo, sc
            accepted = True
            break
        if not accepted:
            break
    after, detail_a = effect_score(cur)
    hv_after = harvest_eval(cur_geo) if harvest_eval and steps else hv_before
    plan = LayoutPlan(before, after, steps, detail_b, detail_a, hv_before, hv_after)
    if grid is not None:
        plan.new_spots = suggest_new(cur, grid, blueprints, masks, reserved)
    return plan


def suggest_new(blds: Dict[int, "Bld"], grid: Grid, blueprints: Sequence[dict],
                masks: Optional[Dict[int, set]] = None, reserved: set = frozenset()) -> List[NewSpot]:
    """지을 수 있는 범위 효과 건물의 최적 빈 자리 (옮기기를 반영한 배치 기준)."""
    out = []
    tiles = [(b.x, b.y, TILE_TYPES[b.type]) for b in blds.values() if b.type in TILE_TYPES]
    occ = occupied(blds, grid, masks=masks) | set(reserved)
    for bp in blueprints:
        t = bp.get("type", "")
        eff = EFFECTS.get(t)
        if not eff:
            continue
        w, h = int(bp.get("tw") or 2), int(bp.get("th") or 2)
        rng = float(bp.get("range") or 3.38)
        best = None
        for c in free_spots(grid, occ, w, h):
            n = sum(1 for x, y, k in tiles if k == eff[0] and abs(x - c[0]) <= rng + 1e-6 and abs(y - c[1]) <= rng + 1e-6)
            if best is None or n > best[1]:
                best = (c, n)
        if best and best[1] > 0:
            size_note = "" if bp.get("tw") else " (크기 2×2 가정)"
            out.append(NewSpot(t, best[0], (w, h), best[1], eff[2] + size_note))
    return out
