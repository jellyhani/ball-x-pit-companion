"""계획도시 배치: 담금질 대신 '구역을 나눠 반복 패턴으로 다시 짓기' (패킹).

실험적 — 커뮤니티·게임 수치 근거 없음, 사용자 취향(참고 스크린샷) 기반:
  생산 구역  생산 건물 하나를 가운데 두고 둘레를 자원 타일로 한 겹 두른 유닛(농장 + 밀밭 8 = 6×6, 채석장 + 바위 12 = 4×4)을
             발사대 쪽부터 격자처럼 찍는다. 남는 자원 타일은 종류별로 네모 블록으로 묶는다.
  마을 구역  나머지 건물(서비스·거처·능력치)을 발사대에서 먼 가장자리부터 빈틈없이 채운다 (첫 맞는 자리, 큰 건물부터).
  가이드 규칙 대위 막사·잔병의 오두막·강철 요새·채집 거처 자리 (아래 FRONT_TYPES 위 주석, Steam 가이드 3개)
  금광       쓰지 않는다 (사용자 결정) — 생산 구역 뒤쪽에 모아 두고 철거 후보로.
마을 폭·위치(왼쪽·가운데·오른쪽)와 유닛 빈칸 예약 여부를 바꿔 가며 여러 번 짜 보고,
못 넣은 건물 수 → 마을 빈틈 → 범위 효과(발사대 앞 구역 포함) 순으로 가장 나은 것을 고른다.
마지막에 마을 안에서 모양이 똑같은 건물끼리만 맞바꿔 범위 효과를 조금 되찾는다 (모양이 그대로라 정돈은 안 깨짐).
이동 횟수는 아끼지 않는다 (사용자: 전체를 갈아엎어도 됨).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .layout import Grid

Cell = Tuple[int, int]

# 생산 건물 → 캐는 자원 (layout_opt.EFFECTS 의 대상과 같음)
PRODUCERS = {"kIdleFarm": 1, "kIdleLumberyard": 2, "kIdleStoneMine": 3}
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


def plan_city(grid: Grid, pieces: dict, origin0: Dict[int, Cell], launcher_rc: Optional[Cell],
              scorer, pad: float = 0.0, hit: Set[int] = frozenset()
              ) -> Tuple[Dict[int, Cell], List[str], Dict[int, int]]:
    """계획도시 배치 (건물 → 왼쪽 아래 타일, 회전해서 놓을 건물 → 시계 방향 90° 횟수). scorer: 범위 효과 채점기 (발사대 앞 구역 포함).
    hit: 쳐야 지어지는 건물 (공사 중 — 상태 값이 없는 옛 플러그인 자료에서도 알 수 있게 따로 받음)."""
    from .layout_opt import STATUE_TYPES, TILE_RES, Layout
    # 건물 회전: 마을 건물·발사대 쪽 건물·거처는 네 방향으로 돌려 넣어 볼 수 있다 (게임 재배치 모드의 회전 버튼).
    # 생산 건물·자원 타일은 꽉 찬 정사각형이라 돌려도 같다.
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

    def depth(r: int) -> int:                                 # 발사대에서 먼 정도 (0 = 가장 가까운 행)
        return r - min(rows) if near_first else max(rows) - r

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
    taken = {p.id for p in prods} | {p.id for v in by_res.values() for p in v} | set(mines)
    # 발사대 쪽: 무한 강화·공사 중 건물(발사대 바로 앞) → 강철 요새(그 옆) → 수도원·유령의 집
    brick = [pieces[i] for i in movable if kind[i] == "kBrickHouse"]
    builds = [pieces[i] for i in movable if i not in taken and kind[i] != "kBrickHouse"
              and (kind[i] in STATUE_TYPES or pieces[i].unfinished or i in hit)]
    build_ids = {p.id for p in builds}
    bounce = [pieces[i] for i in movable if i not in taken and i not in build_ids
              and kind[i] in FRONT_TYPES - {"kBrickHouse"}]
    taken |= {p.id for p in brick + builds + bounce}
    hh = sorted((pieces[i] for i in movable if i not in taken and kind[i] in _harvest_houses()),
                key=lambda p: (-len(p.rel), p.type))
    taken |= {p.id for p in hh}
    town = [pieces[i] for i in movable if i not in taken]
    stat_ids = set(scorer.targets.get("stat", ()))
    house_ids = set(scorer.targets.get("housing", ()))
    captain = next((p for p in town if p.type == CAPTAIN), None)
    veteran = next((p for p in town if p.type == VETERAN), None)
    stats = [p for p in town if p.id in stat_ids]
    plain = [p for p in town if p is not captain and p is not veteran and p.id not in stat_ids]
    # 마을 건물 넣는 순서: 높이 순(같은 높이끼리 줄이 선다) / 넓이 순 — 둘 다 짜 보고 빈틈이 적은 쪽
    plain_sorts = [sorted(plain, key=lambda p: (-p.h, -p.w, -len(p.rel), p.type, p.id)),
                   sorted(plain, key=lambda p: (-len(p.rel), -p.h, -p.w, p.type, p.id))]
    stats.sort(key=lambda p: (-len(p.rel), p.type, p.id))
    town_area = sum(len(p.rel) for p in town)

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
    wmin = max([p.w for p in town] + [1])
    height = max(rows) - min(rows) + 1
    widths = sorted({min(width_all, max(wmin, -(-town_area // h))) for h in range(2, height + 1)} | {width_all})
    towns = []                                                  # (폭, 위치, 시작 열, 넣는 순서)
    for w in widths:
        for anchor in ("left", "center", "right"):
            if anchor == "left":
                c0 = lo_c
            elif anchor == "right":
                c0 = hi_c - w + 1
            else:
                c0 = min(max(lo_c, lc - w // 2), hi_c - w + 1)
            for k in range(len(plain_sorts)):
                towns.append((w, anchor, c0, k))
    items_by_reserve = {r: build_items(r) for r in (True, False)}
    all_cells = sorted(tiles)
    # 생산 구역: 발사대 쪽 행부터, 한 행 안에서는 왼쪽부터 / 발사대 열에서 가까운 쪽부터 / 오른쪽부터
    prod_orders = {"left": sorted(all_cells, key=lambda t: (depth(t[1]), t[0])),
                   "center": sorted(all_cells, key=lambda t: (depth(t[1]), abs(t[0] - lc), t[0])),
                   "right": sorted(all_cells, key=lambda t: (depth(t[1]), -t[0]))}
    prod_ties = {s: {t: n for n, t in enumerate(o)} for s, o in prod_orders.items()}
    far_first = sorted(all_cells, key=lambda t: (-depth(t[1]), t[0]))

    best = None
    seen = set()
    for w, anchor, c0, k in towns:
        if (c0, w, k) in seen:
            continue
        seen.add((c0, w, k))
        # 1) 마을: 먼 가장자리부터, 폭 [c0, c0+w) 안에서.
        #    대위 막사를 마을 가운데쯤 먼저 놓고 능력치 건물을 그 둘레(가까운 순)에, 나머지는 첫 맞는 자리.
        free_t = set(base_free)
        out_t: Dict[int, Cell] = {}
        strip = [t for t in all_cells if c0 <= t[0] < c0 + w]
        town_order = sorted(strip, key=lambda t: (-depth(t[1]), t[0] if anchor != "right" else -t[0]))
        tie = {t: n for n, t in enumerate(town_order)}
        town_cells0: Set[Cell] = set()
        shape_t: Dict[int, tuple] = {}
        late = []

        def put_town(p, order):
            q, at, r = _fit(p, order, free_t)
            if at is None:
                late.append(p)
                return None
            _put(_single(q), at, free_t, out_t)
            shape_t[p.id] = (q, r)
            town_cells0.update(_cells(q, at))
            return _center(q, at)

        rows_town = max(1, -(-town_area // max(1, w)))
        far_row = max(rows) if near_first else min(rows)
        mid = (c0 + w / 2, far_row + 1 - rows_town / 2 if near_first else far_row + rows_town / 2)
        hub = mid
        if captain is not None:
            hub = put_town(captain, _order_near(strip, captain, mid, tie)) or mid
        for p in stats:
            put_town(p, _order_near(strip, p, hub, tie))
        for p in plain_sorts[k]:
            put_town(p, town_order)
        for reserve in (True, False):
            for side, prod_order in prod_orders.items():
                free, out, town_cells, shape = set(free_t), dict(out_t), set(town_cells0), dict(shape_t)
                ptie = prod_ties[side]
                miss = 0.0
                # 2) 생산: 무한 강화·공사 중 → 강철 요새 → 유닛 → 수도원·유령의 집 → 남는 타일 블록 → 채집 거처 → 금광
                units, blocks = items_by_reserve[reserve]

                def put_prod(p, order):
                    q, at, r = _fit(p, order, free)
                    if at is not None:
                        _put(_single(q), at, free, out)
                        shape[p.id] = (q, r)
                        return _center(q, at)
                    return None

                # 공사 중·무한 강화 건물을 먼저 발사대 바로 앞(발사대 열에 가까운 쪽)에 — 공이 닿아야 지어진다.
                # 구석은 발사 각도가 너무 누워 안 닿는다 (실제 기지 계산: 왼쪽 아래 구석에 두면 타격 0). 강철 요새는 그 옆에.
                spots = [c for c in (put_prod(p, prod_orders["center"]) for p in builds) if c]
                for p in brick:
                    if spots:
                        mid_b = (sum(x for x, _ in spots) / len(spots), sum(y for _, y in spots) / len(spots))
                        put_prod(p, _order_near(prod_order, p, mid_b, ptie))
                    else:
                        put_prod(p, prod_order)
                for it, tight in units:
                    at = _place(it, prod_order, free)
                    if at is None:                              # 빈칸 예약 없이 → 그래도 안 되면 하나씩
                        it, at = tight, _place(tight, prod_order, free)
                    if at is not None:
                        _put(it, at, free, out)
                        continue
                    for i, _ in it.members:
                        one = _single(pieces[i])
                        a1 = _place(one, prod_order, free)
                        if a1 is None:
                            break
                        _put(one, a1, free, out)
                        miss += 0.2
                for p in bounce:
                    put_prod(p, prod_order)
                for g in blocks:
                    _place_block(g, prod_order, free, out)
                # 채집·재생 거처: 자기 자원 타일이 범위에 가장 많이 드는 자리 (타일 용량만큼 무게)
                for p in hh:
                    res = _effect_kind(p.type)
                    tgt = [(*_center(pieces[t.id], out[t.id]), t.cap) for t in by_res.get(res, ()) if t.id in out]
                    q, at, r = _best_cover(p, free, tgt, (p.range + pad) / size, ptie) if tgt else (p, None, 0)
                    if at is None:
                        q, at, r = _fit(p, prod_order, free)
                    if at is not None:
                        _put(_single(q), at, free, out)
                        shape[p.id] = (q, r)
                for m in mines:                                 # 금광: 쓰지 않음 — 생산 구역 뒤쪽에 모아 둠
                    put_prod(pieces[m], prod_order[::-1])
                if veteran is not None:                         # 잔병의 오두막: 거처가 가장 많이 범위에 드는 자리
                    homes = []
                    for i in house_ids:
                        if i == veteran.id or i not in pieces:
                            continue
                        o = out.get(i, origin0.get(i) if i in fixed else None)
                        if o is not None:
                            homes.append((*_center(shape.get(i, (pieces[i],))[0], o), 1.0))
                    q, at, r = _best_cover(veteran, free, homes, (veteran.range + pad) / size,
                                           {t: n for n, t in enumerate(town_order)}) if homes else (veteran, None, 0)
                    if at is None:
                        q, at, r = _fit(veteran, town_order + far_first, free)
                    if at is not None:
                        _put(_single(q), at, free, out)
                        shape[veteran.id] = (q, r)
                        town_cells |= _cells(q, at)
                for p in late:                                  # 마을 띠에 못 넣은 건물: 아무 데나
                    q, at, r = _fit(p, town_order + far_first, free)
                    if at is None:
                        break
                    _put(_single(q), at, free, out)
                    shape[p.id] = (q, r)
                    town_cells |= _cells(q, at)
                    miss += 0.2                                 # 띠 밖이라 조금 흐트러짐
                if len(out) < len(movable):
                    continue                                    # 못 놓은 건물이 있으면 쓸 수 없는 배치
                final = dict(origin0)
                final.update(out)
                shaped = dict(pieces)
                shaped.update({i: q for i, (q, _r) in shape.items()})
                waste = _waste(town_cells)
                eff = scorer.score(Layout(grid, shaped, final))[0]
                total = eff - WASTE_W * waste - MISS_W * miss
                if best is None or total > best[0]:
                    best = (total, final, (w, anchor, reserve, waste, miss), shaped,
                            {i: r for i, (_q, r) in shape.items() if r})
    if best is None:
        return dict(origin0), ["계획도시: 땅이 모자라 패턴대로 다시 짤 수 없음 — 지금 배치 유지"], {}
    shaped, turn = best[3], best[4]
    final = _polish_town(grid, shaped, best[1], {p.id for p in plain}, scorer)
    w, anchor, reserve, waste, miss = best[2]
    side = {"left": "왼쪽", "center": "가운데", "right": "오른쪽"}[anchor]
    lay = Layout(grid, shaped, final)
    notes = [f"계획도시: 생산 유닛 {len(prods)}개(건물 + 둘레 자원 타일)를 발사대 쪽에 격자로, "
             f"마을 건물 {len(town)}개를 먼 쪽 {side}에 폭 {w}칸으로 빽빽하게"]
    notes += _guide_notes(lay, shaped, final, captain, veteran, stats, house_ids, hh, brick, builds, pad, size)
    if mines:
        notes.append(f"금광 {len(mines)}개는 쓰지 않으니 뒤쪽에 모아 둠 (무한 모드로 골드 충분 — 철거해도 됨)")
    if reserve and prods:
        notes.append("유닛 둘레 빈칸은 자원 타일을 사서 채울 자리로 비워 둠")
    if miss > 0:
        notes.append(f"땅이 모자라 {round(miss / 0.2)}개는 패턴 밖에 놓음")
    if turn:
        notes.append(f"{len(turn)}개는 회전해서 놓아야 빈틈 없이 맞물림 (ㄱ·ㅜ 자 모양 포함)")
    return final, notes, turn


def _effect_kind(t: str) -> Optional[int]:
    from .layout_opt import EFFECTS
    e = EFFECTS.get(t)
    return e[0] if e and isinstance(e[0], int) else None


def _guide_notes(lay, shaped, final, captain, veteran, stats, house_ids, hh, brick, builds, pad, size) -> List[str]:
    """가이드 규칙이 얼마나 지켜졌는지 한 줄씩 (범위는 사각형, 타일 단위)."""
    def covered(src, ids) -> int:
        sx, sy = _center(shaped[src.id], final[src.id])
        rng = (src.range + pad) / size
        n = 0
        for i in ids:
            if i == src.id or i not in final:
                continue
            cx, cy = _center(shaped[i], final[i])
            n += abs(cx - sx) <= rng + 1e-6 and abs(cy - sy) <= rng + 1e-6
        return n
    out = []
    if captain is not None and stats:
        out.append(f"대위 막사 범위 안 능력치 건물 {covered(captain, [p.id for p in stats])}/{len(stats)}개 "
                   "(가이드: 전부 — 지략가 레벨이 오르면 범위가 넓어짐)")
    homes = [i for i in house_ids if i in final]
    if veteran is not None and homes:
        others = [i for i in homes if i != veteran.id]
        out.append(f"잔병의 오두막 범위 안 거처 {covered(veteran, others)}/{len(others)}개 (가이드: 전부)")
    if brick and builds:
        out.append(f"강철 요새 범위 안 무한 강화·공사 중 건물 {covered(brick[0], [p.id for p in builds])}/{len(builds)}개 "
                   "(발사대 쪽에 둬서 공이 닿게)")
    if hh:
        out.append(f"채집·재생 거처 {len(hh)}개는 자기 자원 타일 옆에 (둘러싸이면 못 캠 — apo 가이드)")
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
