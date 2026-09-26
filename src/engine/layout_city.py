"""가이드 배치: Steam 공략 3개(Zarcos·Drake·apo — README '자료 출처')의 규칙으로 기지를 다시 짠다 (패킹).

허브 먼저, 둘레를 채운다:
  거처       잔병의 오두막을 먼저 놓고 거처를 전부 그 범위 안에 (채집 거처는 발사대 쪽 가장자리 — 둘러싸이면 못 캠)
  능력치     대위 막사를 먼 쪽 가운데에 놓고 능력치 건물을 전부 그 범위 안에
  강철 요새  무한 강화·공사 중 건물을 그 범위 안에 (공이 닿는 발사대 쪽)
  자원 들판  발사대 쪽(가이드: 땅의 약 70%)에 생산 건물을 자기 타일 한가운데 두고, 채집 거처 범위에 자기 타일을 먹인다
  금광       쓰지 않는다 (사용자 결정) — 들판 뒤쪽에 모아 두고 철거 후보로.
마을 폭·위치와 유닛 빈칸 예약 여부를 바꿔 가며 여러 번 짜 보고, 못 넣은 건물 수 → 가이드 달성도(허브 범위에 든 수)
→ 범위 효과 − 빈틈 순으로 고른다. 마지막에 같은 모양끼리 맞바꾸기와 타일 다듬기(외톨이 타일 없이 뭉치게)를 한다.
지금 배치보다 가이드 달성도·효과가 나아지지 않으면 옮기지 않는다. 이동 횟수는 아끼지 않는다 (사용자: 전체를 갈아엎어도 됨).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .layout import Grid
from ..i18n import tr

Cell = Tuple[int, int]

# 생산 건물 → 캐는 자원 (layout_opt.EFFECTS 의 대상과 같음)
PRODUCERS = {"kIdleFarm": 1, "kIdleLumberyard": 2, "kIdleStoneMine": 3}


class _TileKind(dict):
    """자원 타일 종류 → 자원 번호 (layout_opt.TILE_RES, 늦게 불러 순환 import 를 피함)."""
    def get(self, k, default=None):
        from .layout_opt import TILE_RES
        return TILE_RES.get(k, default)


TILE_KIND = _TileKind()
TIDY_W = 0.02        # 자원 타일 정돈 가점 (맞닿은 변 하나당) — 범위 효과 타일 하나(≈1)보다 훨씬 작게, 같을 때만 가른다
MISS_W = 50.0        # 못 넣어서 아무 데나 놓은 건물 하나당 감점 (정돈이 깨지므로 가장 크게)
WASTE_W = 0.5        # 마을 테두리 사각형 안의 빈칸 하나당 감점 (빽빽할수록 좋게) — 값은 임의


class _Item:
    """한 번에 놓는 묶음: 건물들(상대 원점)과 비워 둘 칸(예약)."""
    __slots__ = ("members", "cells", "w", "h")

    def __init__(self, members: List[Tuple[int, Cell]], cells: Set[Cell], w: int, h: int):
        self.members, self.cells, self.w, self.h = members, cells, w, h


def _single(p) -> _Item:
    return _Item([(p.id, (0, 0))], set(p.rel), p.w, p.h)


def _unit(prod, tiles: list, pad: float, reserve: bool, size: float = 1.0) -> Tuple[_Item, list]:
    """생산 건물 + 둘레 한 겹 자원 타일. size: 타일 한 칸의 월드 크기 (범위는 월드 단위). 돌려주는 값: (묶음, 안 쓴 타일)."""
    from .layout_opt import in_range
    if not tiles:
        return _single(prod), []
    sw, sh = Counter((t.w, t.h) for t in tiles).most_common(1)[0][0]
    fit = [t for t in tiles if (t.w, t.h) == (sw, sh)]
    other = [t for t in tiles if (t.w, t.h) != (sw, sh)]
    uw, uh = prod.w + 2 * sw, prod.h + 2 * sh
    px, py = sw + prod.w / 2, sh + prod.h / 2          # 생산 건물 중심 (유닛 왼쪽 아래 기준, 타일 단위)
    slots = []
    for x in range(0, uw - sw + 1, sw):
        for y in range(0, uh - sh + 1, sh):
            if x + sw > sw and x < sw + prod.w and y + sh > sh and y < sh + prod.h:
                continue                                    # 생산 건물 자리
            dx, dy = x + sw / 2 - px, y + sh / 2 - py
            if in_range(dx * size, dy * size, prod.range + pad):
                slots.append(((dx * dx + dy * dy), (x, y)))
    slots = [s for _, s in sorted(slots)]
    fit.sort(key=lambda t: -t.cap)                          # 고급 타일(용량 큼)부터 유닛에
    use, rest = fit[:len(slots)], fit[len(slots):]
    members = [(prod.id, (sw, sh))] + [(t.id, s) for t, s in zip(use, slots)]
    cells = {(sw + dx, sh + dy) for dx, dy in prod.rel}
    for t, (x, y) in zip(use, slots):
        cells |= {(x + dx, y + dy) for dx, dy in t.rel}
    if reserve:                                             # 빈 자리도 비워 둔다 (나중에 타일을 사서 채울 곳)
        for x, y in slots[len(use):]:
            cells |= {(x + dx, y + dy) for dx in range(sw) for dy in range(sh)}
    return _Item(members, cells, uw, uh), rest + other


def _block(tiles: list, cols: int) -> _Item:
    """같은 크기 자원 타일을 가로 cols 개씩 네모 블록으로."""
    sw, sh = tiles[0].w, tiles[0].h
    rows = -(-len(tiles) // cols)
    members, cells = [], set()
    for k, t in enumerate(tiles):
        x, y = (k % cols) * sw, (k // cols) * sh
        members.append((t.id, (x, y)))
        cells |= {(x + dx, y + dy) for dx, dy in t.rel}
    return _Item(members, cells, cols * sw, rows * sh)


def _place_block(tiles: list, order: Sequence[Cell], free: Set[Cell], out: Dict[int, Cell]) -> int:
    """자원 타일 묶음을 네모 블록으로 놓는다: 정사각형에 가까운 모양부터, 안 들어가면 반으로 나눠서.
    돌려주는 값: 못 놓은 타일 수 (그런 배치는 쓰지 않는다)."""
    n = len(tiles)
    shapes = sorted(range(1, n + 1), key=lambda c: (abs(c - -(-n // c)), -((n % c) == 0), c))
    for cols in shapes[:4]:                                   # 네모에 가까운 모양 몇 개만
        it = _block(tiles, cols)
        at = _place(it, order, free)
        if at is not None:
            _put(it, at, free, out)
            return 0
    if n == 1:
        return 1
    return _place_block(tiles[:n // 2], order, free, out) + _place_block(tiles[n // 2:], order, free, out)


def _place(item: _Item, order: Sequence[Cell], free: Set[Cell]) -> Optional[Cell]:
    k = _place_idx(item, order, free)
    return order[k] if k is not None else None


def _place_idx(item: _Item, order: Sequence[Cell], free: Set[Cell]) -> Optional[int]:
    for k, (c, r) in enumerate(order):
        if all((c + dx, r + dy) in free for dx, dy in item.cells):
            return k
    return None


def _fit(p, order: Sequence[Cell], free: Set[Cell]):
    """건물 하나를 네 방향(시계 방향 0·90·180·270°) 중 순서상 가장 앞 자리에 들어가는 방향으로.
    ㄱ·ㅜ·ㅠ 자 건물은 방향에 따라 빈 모서리가 달라 옆 건물과 맞물린다. 같은 자리면 덜 돌리는 쪽.
    돌려주는 값: (모양, 자리, 시계 방향 90° 횟수)."""
    from .layout_opt import rotated
    best = None
    seen = set()
    for r in range(4):
        q = rotated(p, r)
        if (q.w, q.h, q.rel) in seen:
            continue                                            # 대칭이라 같은 모양
        seen.add((q.w, q.h, q.rel))
        k = _place_idx(_single(q), order, free)
        if k is not None and (best is None or k < best[0]):
            best = (k, q, r)
    return (best[1], order[best[0]], best[2]) if best else (p, None, 0)


def _put(item: _Item, at: Cell, free: Set[Cell], out: Dict[int, Cell]):
    for dx, dy in item.cells:
        free.discard((at[0] + dx, at[1] + dy))
    for i, (x, y) in item.members:
        out[i] = (at[0] + x, at[1] + y)


# 가이드 규칙 (Steam 가이드 3개 — README '자료 출처'):
#   Zarcos '100% Utilization Base Layout' · Drake Ravenwolf 'My Optimized Town Layout' · apo 'Base Layout (Naturalist Update)'
# - 대위 막사가 능력치 건물을 모두 범위 안에, 잔병의 오두막이 거처를 모두 범위 안에 (Zarcos·Drake)
# - 강철 요새는 무한 강화 건물을 덮고, 그 구역은 공이 드나들 수 있게 (apo: 입구가 좁아도 한 번 채집에 다 강화)
#   → 강철 요새·무한 강화·공사 중 건물을 발사대 쪽(생산 구역)에
# - 수도원·유령의 집은 채집 때 잘 맞는 자리에 (Zarcos·Drake) → 발사대 쪽
# - 채집하는 거처(외딴 집·아늑한 집·극장)와 재생 거처(별장·야영지·바위 언덕)는 자기 자원 타일 옆에 —
#   건물에 둘러싸이면 아무것도 못 캔다 (apo) → 생산 구역에서 자기 타일이 가장 많이 범위에 드는 자리
# - 금광은 쓰지 않는다 (사용자 결정 2026-09-26: 무한 모드로 골드가 모자라지 않음 / Zarcos: 금광은 권하지 않음)
#   → U자 자리를 비워 두지 않고, 있으면 생산 구역 뒤쪽에 모아 둔다 (철거 후보 — layout_opt.GUIDE_DEMOLISH)
FRONT_TYPES = {"kBrickHouse", "kMonastery", "kHauntedHouse"}
CAPTAIN, VETERAN = "kCaptainQuarters", "kVeteranHut"


def _harvest_houses() -> Set[str]:
    from .layout_opt import EFFECTS
    return {t for t, e in EFFECTS.items() if isinstance(e[0], int) and e[3] == "upgraded"}


def _order_near(cells: Sequence[Cell], p, target: Tuple[float, float], tie: Dict[Cell, int]) -> List[Cell]:
    """건물 p 의 중심이 target(타일 좌표)에 가까운 자리 순 (범위가 사각형이라 체비쇼프 거리)."""
    return sorted(cells, key=lambda t: (max(abs(t[0] + p.w / 2 - target[0]), abs(t[1] + p.h / 2 - target[1])),
                                        tie.get(t, 0)))


def _center(p, o: Cell) -> Tuple[float, float]:
    return (o[0] + p.w / 2, o[1] + p.h / 2)


def _best_cover(p, free: Set[Cell], targets: List[Tuple[float, float, float]], rng: float,
                tie: Dict[Cell, int]):
    """범위(타일 단위, 사각형) 안에 대상(중심 x, y, 무게)이 가장 많이 드는 자리·방향. 대상 근처 자리만 본다.
    돌려주는 값: (모양, 자리, 회전) 또는 들어갈 곳이 없으면 (p, None, 0)."""
    from .layout_opt import rotated
    best = None
    seen = set()
    for r in range(4):
        q = rotated(p, r)
        if (q.w, q.h, q.rel) in seen:
            continue
        seen.add((q.w, q.h, q.rel))
        cand = set()
        for tx, ty, _w in targets:
            for c in range(int(tx - rng - q.w / 2) - 1, int(tx + rng - q.w / 2) + 2):
                for rr in range(int(ty - rng - q.h / 2) - 1, int(ty + rng - q.h / 2) + 2):
                    cand.add((c, rr))
        for o in cand:
            if not all((o[0] + dx, o[1] + dy) in free for dx, dy in q.rel):
                continue
            cx, cy = _center(q, o)
            v = sum(wt for tx, ty, wt in targets if abs(tx - cx) <= rng + 1e-6 and abs(ty - cy) <= rng + 1e-6)
            key = (v, -tie.get(o, 10 ** 6), -r)
            if best is None or key > best[0]:
                best = (key, q, o, r)
    if best is None or best[0][0] <= 0:
        return p, None, 0
    return best[1], best[2], best[3]


def _in_box(src, so: Cell, p, o: Cell, rng: float) -> bool:
    """p 의 중심이 src 의 범위(사각형, 타일 단위 rng) 안인지."""
    sx, sy = _center(src, so)
    cx, cy = _center(p, o)
    return abs(cx - sx) <= rng + 1e-6 and abs(cy - sy) <= rng + 1e-6


def _hub_spot(p, free: Set[Cell], hub: Tuple[float, float], rng: float, depth, near_side: bool):
    """허브(잔병의 오두막·대위 막사·강철 요새) 범위 안의 자리 — 가이드처럼 허브 둘레에 빽빽이.
    고르는 기준: 범위 안에서 — near_side 면 발사대 쪽 가장자리 > 옆이 막힌 칸이 많음 > 허브에서 가까움,
    아니면 옆이 막힌 칸이 많음(빈틈 없이 맞물림) > 허브에서 가까움.
    돌려주는 값: (모양, 자리, 회전) 또는 범위 안에 들어갈 곳이 없으면 (p, None, 0)."""
    from .layout_opt import rotated
    hx, hy = hub
    best = None
    seen = set()
    for r in range(4):
        q = rotated(p, r)
        if (q.w, q.h, q.rel) in seen:
            continue
        seen.add((q.w, q.h, q.rel))
        for c in range(int(hx - rng - q.w) - 1, int(hx + rng) + 2):
            for rr in range(int(hy - rng - q.h) - 1, int(hy + rng) + 2):
                cells = [(c + dx, rr + dy) for dx, dy in q.rel]
                if not all(t in free for t in cells):
                    continue
                cx, cy = c + q.w / 2, rr + q.h / 2
                if abs(cx - hx) > rng + 1e-6 or abs(cy - hy) > rng + 1e-6:
                    continue
                own = set(cells)
                adj = sum(1 for x, y in cells for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                          if n not in own and n not in free)
                # 앞면 건물(채집 거처·강철 요새·유령의 집)은 가장자리가 먼저 — 안쪽에 박히면 둘레에 캘 타일을 못 두고
                # 공도 안 닿는다. 나머지 거처는 빈틈 없이 맞물리는 게 먼저.
                dist = max(abs(cx - hx), abs(cy - hy))
                key = ((min(depth(y) for _, y in cells), -adj, dist) if near_side else (-adj, dist, 0)) + (r, c, rr)
                if best is None or key < best[0]:
                    best = (key, q, (c, rr), r)
    return (best[1], best[2], best[3]) if best else (p, None, 0)


def plan_city(grid: Grid, pieces: dict, origin0: Dict[int, Cell], launcher_rc: Optional[Cell],
              scorer, pad: float = 0.0, hit: Set[int] = frozenset()
              ) -> Tuple[Dict[int, Cell], List[str], Dict[int, int]]:
    """가이드 배치 (건물 → 왼쪽 아래 타일, 회전해서 놓을 건물 → 시계 방향 90° 횟수). scorer: 범위 효과 채점기.
    hit: 쳐야 지어지는 건물 (공사 중 — 상태 값이 없는 옛 플러그인 자료에서도 알 수 있게 따로 받음).

    Steam 가이드 3개(Zarcos·apo·Drake — 위 주석)의 배치 순서:
      1) 먼 쪽 마을 띠의 발사대 쪽 경계에 잔병의 오두막 → 거처 전부를 그 범위 안에 빽빽이. 강철 요새·유령의 집·
         수도원과 채집 거처는 오두막의 발사대 쪽 면(공이 닿고 자원 들판에 닿게).
      2) 대위 막사가 있으면 능력치 건물 전부를 그 범위 안에.
      3) 공사 중·무한 강화 건물은 강철 요새 범위 안 발사대 쪽 (한 번 채집에 건설 끝나게).
      4) 나머지 마을 건물로 빈틈을 채우고, 대저택은 건물이 가장 많이 드는 자리.
      5) 발사대 쪽은 자원 들판 — 생산 건물을 자기 타일 한가운데 둔 유닛, 남는 타일 블록. 금광(안 씀)은 들판 뒤쪽.
    후보(마을 폭·위치·넣는 순서·유닛 빈칸 예약)마다 가이드 달성도(못 넣은 건물 없음 > 오두막 > 대위 막사 > 강철 요새 >
    채집 거처가 자기 타일을 범위에 둠)를 먼저, 그다음 범위 효과·빈틈으로 고른다. 지금 배치보다 나아지지 않으면 그대로 둔다."""
    from .layout_opt import STATUE_TYPES, TILE_RES, Layout
    tiles = grid.tiles
    size = grid.size
    fixed = {i for i, p in pieces.items() if not p.movable}
    base_free = set(tiles)
    for i in fixed:
        base_free -= {(origin0[i][0] + dx, origin0[i][1] + dy) for dx, dy in pieces[i].rel}
    cols = [c for c, _ in tiles]
    rows = [r for _, r in tiles]
    lr = launcher_rc[1] if launcher_rc else min(rows) - 1
    near_first = abs(min(rows) - lr) <= abs(max(rows) - lr)   # 발사대가 아래쪽이면 행이 작을수록 가깝다
    height = max(rows) - min(rows) + 1

    def depth(r: int) -> int:                                 # 발사대에서 먼 정도 (0 = 가장 가까운 행)
        return r - min(rows) if near_first else max(rows) - r

    def row_at(d: float) -> float:                            # depth → 행 좌표 (타일 단위, 중심)
        return min(rows) + d if near_first else max(rows) - d

    movable = [i for i in pieces if i not in fixed]
    kind = {i: pieces[i].type for i in movable}
    mines = sorted((i for i in movable if kind[i] == "kGoldMine"), key=lambda i: origin0[i])
    by_res: Dict[int, list] = {1: [], 2: [], 3: []}
    for i in movable:
        if kind[i] in TILE_RES:
            by_res[TILE_RES[kind[i]]].append(pieces[i])
    for v in by_res.values():
        v.sort(key=lambda p: origin0[p.id])
    prods = sorted((pieces[i] for i in movable if kind[i] in PRODUCERS), key=lambda p: (PRODUCERS[p.type], origin0[p.id]))
    field_ids = {p.id for p in prods} | {p.id for v in by_res.values() for p in v} | set(mines)
    house_ids = set(scorer.targets.get("housing", ()))
    stat_ids = set(scorer.targets.get("stat", ()))
    hh_types = _harvest_houses()
    veteran = next((pieces[i] for i in movable if kind[i] == VETERAN), None)
    captain = next((pieces[i] for i in movable if kind[i] == CAPTAIN), None)
    brick = next((pieces[i] for i in movable if kind[i] == "kBrickHouse"), None)
    builds = [pieces[i] for i in movable if i not in field_ids and kind[i] != "kBrickHouse"
              and (kind[i] in STATUE_TYPES or pieces[i].unfinished or i in hit)]
    build_ids = {p.id for p in builds}
    # 오두막 둘레에 넣는 순서: 발사대 쪽 면 (강철 요새 → 유령의 집·수도원 → 채집 거처) → 나머지 큰 것부터
    homes = [pieces[i] for i in movable if i in house_ids and i not in field_ids and i not in build_ids
             and (veteran is None or i != veteran.id)]
    front = [p for p in homes if p.type in FRONT_TYPES] + [p for p in homes if p.type in hh_types]
    front.sort(key=lambda p: (p.type != "kBrickHouse", p.type not in FRONT_TYPES, -len(p.rel), p.type, p.id))
    back = sorted((p for p in homes if p not in front), key=lambda p: (-len(p.rel), p.type, p.id))
    stats = sorted((pieces[i] for i in movable if i in stat_ids and i not in house_ids and i not in field_ids
                    and i not in build_ids and (captain is None or i != captain.id)),
                   key=lambda p: (-len(p.rel), p.type, p.id))
    mansion = next((pieces[i] for i in movable if kind[i] == "kMansion" and i not in house_ids), None)
    placed_special = {p.id for p in homes + stats + builds} | ({veteran.id} if veteran else set()) \
        | ({captain.id} if captain else set()) | ({mansion.id} if mansion else set())
    plain = [pieces[i] for i in movable if i not in field_ids and i not in placed_special]
    plain_sorts = [sorted(plain, key=lambda p: (-p.h, -p.w, -len(p.rel), p.type, p.id)),
                   sorted(plain, key=lambda p: (-len(p.rel), -p.h, -p.w, p.type, p.id))]
    town_all = [pieces[i] for i in movable if i not in field_ids]
    town_area = sum(len(p.rel) for p in town_all)

    def build_items(reserve: bool) -> Tuple[List[Tuple[_Item, _Item]], List[list]]:
        """(유닛(빈칸 예약), 같은 유닛(예약 없음)) 목록과 남는 타일 묶음(같은 모양끼리)."""
        pool = {k: list(v) for k, v in by_res.items()}
        units = []
        for p in prods:
            k = PRODUCERS[p.type]
            it, rest = _unit(p, pool[k], pad, reserve, size)
            tight, _ = _unit(p, pool[k], pad, False, size)
            units.append((it, tight))
            pool[k] = rest
        blocks = []
        for k in (1, 2, 3):
            groups: Dict[tuple, list] = {}
            for t in pool[k]:
                groups.setdefault((t.w, t.h, t.rel), []).append(t)
            blocks += sorted(groups.values(), key=lambda g: -len(g) * g[0].w * g[0].h)
        units.sort(key=lambda u: (-u[0].w * u[0].h))
        return units, blocks

    lo_c, hi_c = min(cols), max(cols)
    lc = launcher_rc[0] if launcher_rc else (lo_c + hi_c) // 2
    width_all = hi_c - lo_c + 1
    wmin = max([p.w for p in town_all] + [1])
    widths = sorted({min(width_all, max(wmin, -(-town_area // h))) for h in range(2, height + 1)} | {width_all})
    towns = []                                                  # (폭, 위치, 시작 열, 넣는 순서)
    for w in widths:
        for anchor in ("left", "center", "right"):
            c0 = lo_c if anchor == "left" else hi_c - w + 1 if anchor == "right" \
                else min(max(lo_c, lc - w // 2), hi_c - w + 1)
            for k in range(len(plain_sorts)):
                towns.append((w, anchor, c0, k))
    items_by_reserve = {r: build_items(r) for r in (True, False)}
    all_cells = sorted(tiles)
    prod_orders = {"left": sorted(all_cells, key=lambda t: (depth(t[1]), t[0])),
                   "center": sorted(all_cells, key=lambda t: (depth(t[1]), abs(t[0] - lc), t[0])),
                   "right": sorted(all_cells, key=lambda t: (depth(t[1]), -t[0]))}
    far_first = sorted(all_cells, key=lambda t: (-depth(t[1]), t[0]))
    rng_of = lambda p: (p.range + pad) / size                   # noqa: E731 — 범위 (타일 단위, 사각형)

    best = None
    seen = set()
    for w, anchor, c0, k in towns:
        if (c0, w, k) in seen:
            continue
        seen.add((c0, w, k))
        free_t = set(base_free)
        out_t: Dict[int, Cell] = {}
        shape_t: Dict[int, tuple] = {}
        town_cells0: Set[Cell] = set()
        late: List = []
        strip = [t for t in all_cells if c0 <= t[0] < c0 + w]
        town_order = sorted(strip, key=lambda t: (-depth(t[1]), t[0] if anchor != "right" else -t[0]))
        rows_town = max(1, -(-town_area // max(1, w)))
        edge = max(0, height - rows_town)                     # 마을 띠의 발사대 쪽 경계 (depth)

        def put(p, q, at, r):
            _put(_single(q), at, free_t, out_t)
            shape_t[p.id] = (q, r)
            town_cells0.update(_cells(q, at))
            return q, at

        def put_first(p, order):
            q, at, r = _fit(p, order, free_t)
            if at is None:
                late.append(p)
                return None
            return put(p, q, at, r)

        def put_hub(p, hub_piece, hub_at, near_side):
            q, at, r = _hub_spot(p, free_t, _center(hub_piece, hub_at), rng_of(hub_piece), depth, near_side)
            if at is None:
                return put_first(p, town_order)              # 범위 안에 자리가 없으면 마을 아무 데나 (달성도에서 빠짐)
            return put(p, q, at, r)

        # 1) 잔병의 오두막: 마을 띠 발사대 쪽 경계에서 범위만큼 안쪽 (둘레 거처가 경계까지 닿게)
        vet_at = None
        if veteran is not None:
            target = (c0 + w / 2, row_at(min(height - 1, edge + rng_of(veteran))))
            got = put_first(veteran, _order_near(strip, veteran, target, {}))
            if got:
                vq, vet_at = got
                for p in front + back:
                    put_hub(p, vq, vet_at, p in front)
        else:
            for p in front + back:
                put_first(p, town_order)
        # 2) 대위 막사 + 능력치 건물: 마을 띠 먼 쪽 가운데
        if captain is not None:
            target = (c0 + w / 2, row_at(max(edge, height - 1 - rng_of(captain))))
            got = put_first(captain, _order_near(strip, captain, target, {}))
            for p in stats:
                if got:
                    put_hub(p, got[0], got[1], False)
                else:
                    put_first(p, town_order)
        else:
            for p in stats:
                put_first(p, town_order)
        # 3) 공사 중·무한 강화 건물: 강철 요새 범위 안 발사대 쪽 (요새가 없으면 발사대 바로 앞 — 아래 들판 단계)
        brick_at = out_t.get(brick.id) if brick is not None else None
        pending_builds = []
        for p in builds:
            if brick_at is not None:
                bq = shape_t[brick.id][0]
                q, at, r = _hub_spot(p, free_t, _center(bq, brick_at), rng_of(brick), depth, True)
                if at is not None:
                    put(p, q, at, r)
                    continue
            pending_builds.append(p)
        # 4) 나머지 마을 건물: 먼 쪽부터 빈틈없이 / 대저택은 건물이 가장 많이 드는 자리
        for p in plain_sorts[k]:
            put_first(p, town_order)
        if mansion is not None:
            tgt = [(*_center(shape_t[i][0], out_t[i]), 1.0) for i in out_t]
            q, at, r = _best_cover(mansion, free_t, tgt, rng_of(mansion), {t: n for n, t in enumerate(town_order)}) \
                if tgt else (mansion, None, 0)
            if at is None:
                put_first(mansion, town_order)
            else:
                put(mansion, q, at, r)
        for reserve in (True, False):
            for side, prod_order in prod_orders.items():
                free, out, town_cells, shape = set(free_t), dict(out_t), set(town_cells0), dict(shape_t)
                miss = 0

                def put_field(p, order):
                    q, at, r = _fit(p, order, free)
                    if at is not None:
                        _put(_single(q), at, free, out)
                        shape[p.id] = (q, r)
                        return True
                    return False

                # 5) 들판: (요새가 없거나 범위에 못 넣은) 공사 중 건물은 발사대 바로 앞 → 유닛 → 타일 블록 → 금광(뒤쪽)
                for p in pending_builds:
                    miss += not put_field(p, prod_orders["center"])
                units, blocks = items_by_reserve[reserve]
                for it, tight in units:
                    at = _place(it, prod_order, free)
                    if at is None:
                        it, at = tight, _place(tight, prod_order, free)
                    if at is not None:
                        _put(it, at, free, out)
                        continue
                    for i, _ in it.members:                     # 유닛이 통째로 안 들어가면 하나씩
                        one = _single(pieces[i])
                        a1 = _place(one, prod_order, free)
                        if a1 is None:
                            break
                        _put(one, a1, free, out)
                # 채집·재생 거처(마을 앞면)의 범위부터 자기 자원 타일로 채운다 — 둘러싸이면 못 캠 (apo)
                blocks = _feed_houses(homes, hh_types, shape, out, blocks, free, pad, size)
                for g in blocks:
                    _place_block(g, prod_order, free, out)
                for m in mines:
                    put_field(pieces[m], prod_order[::-1])
                for p in late:                                  # 마을 띠에 못 넣은 건물: 아무 데나
                    q, at, r = _fit(p, town_order + far_first, free)
                    if at is None:
                        break
                    _put(_single(q), at, free, out)
                    shape[p.id] = (q, r)
                    town_cells |= _cells(q, at)
                    miss += 1
                if len(out) < len(movable):
                    continue                                    # 못 놓은 건물이 있으면 쓸 수 없는 배치
                final = dict(origin0)
                final.update(out)
                shaped = dict(pieces)
                shaped.update({i: q for i, (q, _r) in shape.items()})
                guide = _guide_score(shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types,
                                     pad, size)
                eff = scorer.score(Layout(grid, shaped, final))[0]
                key = (-miss,) + guide + (eff - WASTE_W * _waste(town_cells),)
                if best is None or key > best[0]:
                    best = (key, final, (w, anchor, reserve, miss), shaped,
                            {i: r for i, (_q, r) in shape.items() if r})
    if best is None:
        return dict(origin0), [tr("가이드 배치: 땅이 모자라 다시 짤 수 없음 — 지금 배치 유지")], {}
    shaped, turn = best[3], best[4]
    final = _polish_town(grid, shaped, best[1], {p.id for p in plain}, scorer)
    final = _polish_field(grid, shaped, final, scorer)
    # 지금 배치와 비교: 가이드 달성도가 나아지지 않고 범위 효과도 나아지지 않으면 옮기지 않는다
    cur_guide = _guide_score(pieces, origin0, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size)
    new_guide = _guide_score(shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size)
    cur_eff = scorer.score(Layout(grid, pieces, origin0))[0]
    new_eff = scorer.score(Layout(grid, shaped, final))[0]
    if (new_guide, new_eff) <= (cur_guide, cur_eff * 1.02):
        lay0 = Layout(grid, pieces, origin0)
        notes = [tr("지금 배치가 가이드 기준으로 더 낫거나 같음 — 옮기지 않음")]
        notes += _guide_notes(lay0, pieces, origin0, captain, veteran, stats, list(house_ids), homes, by_res, brick,
                              builds, pad, size)
        return dict(origin0), notes, {}
    w, anchor, reserve, miss = best[2]
    lay = Layout(grid, shaped, final)
    notes = [tr("가이드 배치: 발사대 쪽은 자원 들판(생산 건물 {v0}개가 자기 타일 한가운데), 먼 쪽은 거처·능력치 덩어리와 나머지 건물 {v1}개",
                v0=len(prods), v1=len(town_all))]
    notes += _guide_notes(lay, shaped, final, captain, veteran, stats, list(house_ids), homes, by_res, brick, builds,
                          pad, size)
    if mines:
        notes.append(tr("금광 {v0}개는 쓰지 않으니 뒤쪽에 모아 둠 (무한 모드로 골드 충분 — 철거해도 됨)", v0=len(mines)))
    if reserve and prods:
        notes.append(tr("유닛 둘레 빈칸은 자원 타일을 사서 채울 자리로 비워 둠"))
    if miss > 0:
        notes.append(tr("땅이 모자라 {v0}개는 패턴 밖에 놓음", v0=miss))
    if turn:
        notes.append(tr("{v0}개는 회전해서 놓아야 빈틈 없이 맞물림 (ㄱ·ㅜ 자 모양 포함)", v0=len(turn)))
    return final, notes, turn


def _feed_houses(homes, hh_types, shape, out, blocks, free: Set[Cell], pad: float, size: float) -> List[list]:
    """채집·재생 거처 범위 안 빈칸에 그 거처의 자원 타일(용량 큰 것부터)을 하나씩 놓는다. 남은 타일 묶음을 돌려준다."""
    left = [list(g) for g in blocks]
    for p in homes:
        if p.type not in hh_types or p.id not in out:
            continue
        res = _effect_kind(p.type)
        q = shape.get(p.id, (p,))[0]
        hx, hy = _center(q, out[p.id])
        rng = (p.range + pad) / size
        for g in left:
            if not g or TILE_KIND.get(g[0].type) != res:
                continue
            g.sort(key=lambda t: -t.cap)
            while g:
                t = g[0]
                spots = sorted(((abs(c + t.w / 2 - hx) + abs(r + t.h / 2 - hy), c, r)
                                for c in range(int(hx - rng - t.w) - 1, int(hx + rng) + 2)
                                for r in range(int(hy - rng - t.h) - 1, int(hy + rng) + 2)
                                if abs(c + t.w / 2 - hx) <= rng + 1e-6 and abs(r + t.h / 2 - hy) <= rng + 1e-6
                                and all((c + dx, r + dy) in free for dx, dy in t.rel)))
                if not spots:
                    break
                _, c, r = spots[0]
                _put(_single(t), (c, r), free, out)
                g.pop(0)
    return [g for g in left if g]


def _guide_score(shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size
                 ) -> Tuple[int, int, int, int]:
    """가이드 달성도 (클수록 좋음): (오두막 범위 안 거처 수, 대위 막사 범위 안 능력치 건물 수,
    강철 요새 범위 안 공사 중·무한 강화 건물 수, 자기 자원 타일을 범위에 둔 채집·재생 거처 수)."""
    def cov(src, ids) -> int:
        if src is None or src.id not in final:
            return 0
        rng = (src.range + pad) / size
        return sum(1 for i in ids if i in final and i != src.id
                   and _in_box(shaped[src.id], final[src.id], shaped[i], final[i], rng))
    hh_ok = 0
    for p in homes:
        if p.type not in hh_types or p.id not in final:
            continue
        res = _effect_kind(p.type)
        rng = (p.range + pad) / size
        if any(t.id in final and _in_box(shaped[p.id], final[p.id], shaped[t.id], final[t.id], rng)
               for t in by_res.get(res, ())):
            hh_ok += 1
    return (cov(veteran, [p.id for p in homes]),
            cov(captain, [p.id for p in stats]), cov(brick, [p.id for p in builds]), hh_ok)


def _effect_kind(t: str) -> Optional[int]:
    from .layout_opt import EFFECTS
    e = EFFECTS.get(t)
    return e[0] if e and isinstance(e[0], int) else None


def _guide_notes(lay, shaped, final, captain, veteran, stats, house_ids, homes, by_res, brick, builds, pad, size
                 ) -> List[str]:
    """가이드 규칙이 얼마나 지켜졌는지 한 줄씩 (배치도 창 메모)."""
    v, c, b, hh = _guide_score(shaped, final, veteran, captain, brick, homes, stats, builds, by_res,
                               _harvest_houses(), pad, size)
    out = []
    if captain is not None and stats:
        out.append(tr("대위 막사 범위 안 능력치 건물 {v0}/{v1}개 (가이드: 전부 — 지략가 레벨이 오르면 범위가 넓어짐)", v0=c, v1=len(stats)))
    if veteran is not None and homes:
        out.append(tr("잔병의 오두막 범위 안 거처 {v0}/{v1}개 (가이드: 전부)", v0=v, v1=len(homes)))
    if brick is not None and builds:
        out.append(tr("강철 요새 범위 안 무한 강화·공사 중 건물 {v0}/{v1}개 (발사대 쪽에 둬서 공이 닿게)", v0=b, v1=len(builds)))
    n_hh = sum(1 for p in homes if p.type in _harvest_houses())
    if n_hh:
        out.append(tr("채집·재생 거처 {v0}/{v1}개가 자기 자원 타일을 범위에 둠 (둘러싸이면 못 캠 — apo 가이드)", v0=hh, v1=n_hh))
    return out


def _cells(p, o: Cell) -> Set[Cell]:
    return {(o[0] + dx, o[1] + dy) for dx, dy in p.rel}


def _waste(cells: Set[Cell]) -> int:
    """마을 테두리 사각형 안의 빈칸 수 (적을수록 빽빽)."""
    if not cells:
        return 0
    cs = [c for c, _ in cells]
    rs = [r for _, r in cells]
    return (max(cs) - min(cs) + 1) * (max(rs) - min(rs) + 1) - len(cells)


def _polish_town(grid: Grid, pieces: dict, final: Dict[int, Cell], town: Set[int], scorer) -> Dict[int, Cell]:
    """마을 안에서 모양(차지하는 칸)이 똑같은 건물끼리만 맞바꿔 범위 효과를 올린다 (더 나아지지 않을 때까지).
    모양이 같으니 빈틈·줄은 그대로다."""
    from .layout_opt import Layout
    groups: Dict[tuple, List[int]] = {}
    for i in town:
        p = pieces[i]
        groups.setdefault((p.w, p.h, p.rel), []).append(i)
    pairs = [(a, b) for g in groups.values() for k, a in enumerate(g) for b in g[k + 1:]]
    if not pairs:
        return final
    cur = dict(final)
    best = scorer.score(Layout(grid, pieces, cur))[0]
    for _ in range(20):
        improved = False
        for a, b in pairs:
            cur[a], cur[b] = cur[b], cur[a]
            v = scorer.score(Layout(grid, pieces, cur))[0]
            if v > best + 1e-9:
                best, improved = v, True
            else:
                cur[a], cur[b] = cur[b], cur[a]
        if not improved:
            break
    return cur


def _touching(p, o: Cell, occ: Dict[Cell, int], me: int) -> int:
    """o 에 놓은 p 가 다른 건물·타일과 맞닿는 변의 수 — 외딴 빈칸에 한 칸씩 흩어지지 않게 (정돈)."""
    cells = _cells(p, o)
    return sum(1 for x, y in cells for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
               if n not in cells and occ.get(n, me) != me)


def _polish_field(grid: Grid, pieces: dict, final: Dict[int, Cell], scorer, seconds: float = 2.5) -> Dict[int, Cell]:
    """자원 타일만 옮겨 범위 효과를 올린다 (다른 종류 타일과 맞바꾸기 · 이웃과 두 변 이상 맞닿는 빈칸으로 옮기기).
    거처·능력치 덩어리와
    생산 건물은 그대로라 가이드 달성도는 바뀌지 않는다 — 채집·재생 거처와 생산 건물이 캘 타일을 나눠 갖는 것을
    범위 효과 점수(layout_opt.EFFECTS 의 무게)로 정한다. 시간 상한 안에서 더 나아지지 않을 때까지."""
    import time
    from .layout_opt import TILE_RES, Layout
    tile_ids = sorted(i for i, p in pieces.items() if p.type in TILE_RES and p.movable and i in final)
    if not tile_ids:
        return final
    cur = dict(final)
    occ: Dict[Cell, int] = {}
    for i, o in cur.items():
        for c in _cells(pieces[i], o):
            occ[c] = i
    def value() -> float:
        # 범위 효과 + 정돈 (타일이 이웃과 맞닿은 변마다 조금) — 효과가 같으면 흩어진 타일이 덩어리 쪽으로 모인다
        occ_now = {c: j for j, o in cur.items() for c in _cells(pieces[j], o)}
        tidy = sum(_touching(pieces[j], cur[j], occ_now, j) for j in tile_ids)
        return scorer.score(Layout(grid, pieces, cur))[0] + TIDY_W * tidy

    best = value()
    start = time.perf_counter()
    improved = True
    while improved and time.perf_counter() - start < seconds:
        improved = False
        for i in tile_ids:
            if time.perf_counter() - start >= seconds:
                break
            p = pieces[i]
            mine = _cells(p, cur[i])
            cands = [("swap", j) for j in tile_ids if j != i and pieces[j].type != p.type
                     and (pieces[j].w, pieces[j].h, pieces[j].rel) == (p.w, p.h, p.rel)]
            cands += [("move", (c, r)) for c, r in grid.tiles
                      if (c, r) != cur[i] and all((c + dx, r + dy) in grid.tiles
                                                  and occ.get((c + dx, r + dy), i) == i for dx, dy in p.rel)
                      and _touching(p, (c, r), occ, i) >= 2]
            choice = None
            for how, x in cands:
                if how == "swap":
                    cur[i], cur[x] = cur[x], cur[i]
                else:
                    old = cur[i]
                    cur[i] = x
                v = value()
                if v > best + 1e-9:
                    best, choice = v, (how, x)
                if how == "swap":
                    cur[i], cur[x] = cur[x], cur[i]
                else:
                    cur[i] = old
            if choice is None:
                continue
            how, x = choice
            if how == "swap":
                cur[i], cur[x] = cur[x], cur[i]
                for c in _cells(pieces[i], cur[i]):
                    occ[c] = i
                for c in _cells(pieces[x], cur[x]):
                    occ[c] = x
            else:
                for c in mine:
                    occ.pop(c, None)
                cur[i] = x
                for c in _cells(p, x):
                    occ[c] = i
            improved = True
    return cur
