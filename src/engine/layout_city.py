"""계획도시 배치: 담금질 대신 '구역을 나눠 반복 패턴으로 다시 짓기' (패킹).

실험적 — 커뮤니티·게임 수치 근거 없음, 사용자 취향(참고 스크린샷) 기반:
  생산 구역  생산 건물 하나를 가운데 두고 둘레를 자원 타일로 한 겹 두른 유닛(농장 + 밀밭 8 = 6×6, 채석장 + 바위 12 = 4×4)을
             발사대 쪽부터 격자처럼 찍는다. 남는 자원 타일은 종류별로 네모 블록으로 묶는다.
  마을 구역  나머지 건물(서비스·거처·능력치)을 발사대에서 먼 가장자리부터 빈틈없이 채운다 (첫 맞는 자리, 큰 건물부터).
  금광       금광 U자(gold_u_spots) 자리를 그대로 쓰고 가운데 통로는 비워 둔다. 남는 금광은 생산 구역에.
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


def _unit(prod, tiles: list, pad: float, reserve: bool) -> Tuple[_Item, list]:
    """생산 건물 + 둘레 한 겹 자원 타일. 돌려주는 값: (묶음, 안 쓴 타일)."""
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
            if in_range(dx, dy, prod.range + pad):
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
    for c, r in order:
        if all((c + dx, r + dy) in free for dx, dy in item.cells):
            return (c, r)
    return None


def _put(item: _Item, at: Cell, free: Set[Cell], out: Dict[int, Cell]):
    for dx, dy in item.cells:
        free.discard((at[0] + dx, at[1] + dy))
    for i, (x, y) in item.members:
        out[i] = (at[0] + x, at[1] + y)


def _u_reserve(spots: List[Cell]) -> Set[Cell]:
    """금광 U자 자리 + 가운데 통로 (공이 드나드는 길이라 비워 둔다)."""
    cells = {(s[0] + dx, s[1] + dy) for s in spots for dx in range(2) for dy in range(2)}
    if len(spots) >= 2:
        left = min(s[0] for s in spots)
        r0 = min(s[1] for s in spots)
        top = max(s[1] for s in spots)
        cells |= {(left + 2 + dx, r) for dx in range(2) for r in range(r0, top)}
    return cells


def plan_city(grid: Grid, pieces: dict, origin0: Dict[int, Cell], launcher_rc: Optional[Cell],
              gold_spots: List[Cell], scorer, pad: float = 0.0) -> Tuple[Dict[int, Cell], List[str]]:
    """계획도시 배치 (건물 → 왼쪽 아래 타일). scorer: 범위 효과 채점기 (발사대 앞 구역 포함 — 후보 비교용)."""
    from .layout_opt import TILE_RES, Layout
    tiles = grid.tiles
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
    mines = sorted((i for i in movable if pieces[i].type == "kGoldMine"), key=lambda i: origin0[i])
    spots = [s for s in gold_spots if all((s[0] + dx, s[1] + dy) in base_free for dx in range(2) for dy in range(2))]
    spots = spots[:len(mines)] if mines else []
    reserve_u = _u_reserve(gold_spots) & base_free if mines else set()
    by_res: Dict[int, list] = {1: [], 2: [], 3: []}
    for i in movable:
        if pieces[i].type in TILE_RES:
            by_res[TILE_RES[pieces[i].type]].append(pieces[i])
    for v in by_res.values():
        v.sort(key=lambda p: origin0[p.id])
    prods = sorted((pieces[i] for i in movable if pieces[i].type in PRODUCERS), key=lambda p: (PRODUCERS[p.type], origin0[p.id]))
    # 공사 중인 건물은 쳐야 지어지므로 마을이 아니라 생산 구역(발사대 쪽)에
    extra = [pieces[i] for i in movable if pieces[i].unfinished and pieces[i].type not in PRODUCERS
             and pieces[i].type not in TILE_RES and pieces[i].type != "kGoldMine"]
    used = {p.id for p in prods} | {p.id for v in by_res.values() for p in v} | set(mines) | {p.id for p in extra}
    town = [pieces[i] for i in movable if i not in used]
    # 마을 건물 넣는 순서: 높이 순(같은 높이끼리 줄이 선다) / 넓이 순 — 둘 다 짜 보고 빈틈이 적은 쪽
    town_sorts = [sorted(town, key=lambda p: (-p.h, -p.w, -len(p.rel), p.type, p.id)),
                  sorted(town, key=lambda p: (-len(p.rel), -p.h, -p.w, p.type, p.id))]
    town_area = sum(len(p.rel) for p in town)

    def build_items(reserve: bool) -> Tuple[List[Tuple[_Item, _Item]], List[list]]:
        """(유닛(빈칸 예약), 같은 유닛(예약 없음)) 목록과 남는 타일 묶음(같은 모양끼리)."""
        pool = {k: list(v) for k, v in by_res.items()}
        units = []
        for p in prods:
            k = PRODUCERS[p.type]
            it, rest = _unit(p, pool[k], pad, reserve)
            tight, _ = _unit(p, pool[k], pad, False)
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
    towns = []                                                  # (폭, 위치, 마을 띠 순서, 넣은 순서)
    for w in widths:
        for anchor in ("left", "center", "right"):
            if anchor == "left":
                c0 = lo_c
            elif anchor == "right":
                c0 = hi_c - w + 1
            else:
                c0 = min(max(lo_c, lc - w // 2), hi_c - w + 1)
            for k, ts in enumerate(town_sorts):
                towns.append((w, anchor, c0, k))
    items_by_reserve = {r: build_items(r) for r in (True, False)}
    all_cells = sorted(tiles)
    # 생산 구역: 발사대 쪽 행부터, 한 행 안에서는 왼쪽부터 / 발사대 열에서 가까운 쪽부터 / 오른쪽부터
    prod_orders = {"left": sorted(all_cells, key=lambda t: (depth(t[1]), t[0])),
                   "center": sorted(all_cells, key=lambda t: (depth(t[1]), abs(t[0] - lc), t[0])),
                   "right": sorted(all_cells, key=lambda t: (depth(t[1]), -t[0]))}
    start = set(base_free) - reserve_u
    out0 = dict(zip(mines, spots))

    best = None
    seen = set()
    for w, anchor, c0, k in towns:
        if (c0, w, k) in seen:
            continue
        seen.add((c0, w, k))
        # 1) 마을: 먼 가장자리부터, 폭 [c0, c0+w) 안에서 첫 맞는 자리
        free_t = set(start)
        out_t = dict(out0)
        strip = [t for t in all_cells if c0 <= t[0] < c0 + w]
        town_order = sorted(strip, key=lambda t: (-depth(t[1]), t[0] if anchor != "right" else -t[0]))
        town_cells0: Set[Cell] = set()
        late = []
        for p in town_sorts[k]:
            it = _single(p)
            at = _place(it, town_order, free_t)
            if at is None:
                late.append(p)
                continue
            _put(it, at, free_t, out_t)
            town_cells0 |= _cells(p, at)
        for reserve in (True, False):
            for side, prod_order in prod_orders.items():
                free, out, town_cells = set(free_t), dict(out_t), set(town_cells0)
                miss = 0.0
                # 2) 생산: 유닛 → 공사 중 건물 → 남는 금광 → 남는 타일 블록
                units, blocks = items_by_reserve[reserve]
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
                for p in extra + [pieces[m] for m in mines[len(spots):]]:
                    it = _single(p)
                    at = _place(it, prod_order, free)
                    if at is not None:
                        _put(it, at, free, out)
                for g in blocks:
                    _place_block(g, prod_order, free, out)
                for p in late:                                  # 마을 띠에 못 넣은 건물: 아무 데나
                    it = _single(p)
                    at = _place(it, town_order + prod_order[::-1], free)
                    if at is None:
                        break
                    _put(it, at, free, out)
                    town_cells |= _cells(p, at)
                    miss += 0.2                                 # 띠 밖이라 조금 흐트러짐
                if len(out) < len(movable):
                    continue                                    # 못 놓은 건물이 있으면 쓸 수 없는 배치
                final = dict(origin0)
                final.update(out)
                waste = _waste(town_cells)
                eff = scorer.score(Layout(grid, pieces, final))[0]
                total = eff - WASTE_W * waste - MISS_W * miss
                if best is None or total > best[0]:
                    best = (total, final, (w, anchor, reserve, waste, miss))
    if best is None:
        return dict(origin0), ["계획도시: 땅이 모자라 패턴대로 다시 짤 수 없음 — 지금 배치 유지"]
    final = _polish_town(grid, pieces, best[1], {p.id for p in town}, scorer)
    w, anchor, reserve, waste, miss = best[2]
    side = {"left": "왼쪽", "center": "가운데", "right": "오른쪽"}[anchor]
    notes = [f"계획도시: 생산 유닛 {len(prods)}개(건물 + 둘레 자원 타일)를 발사대 쪽에 격자로, "
             f"마을 건물 {len(town)}개를 먼 쪽 {side}에 폭 {w}칸으로 빽빽하게"]
    if mines:
        notes.append(f"금광 {len(mines)}개 중 {len(spots)}개는 금광 U자 자리 (가운데 통로는 비워 둠)")
    if reserve and prods:
        notes.append("유닛 둘레 빈칸은 자원 타일을 사서 채울 자리로 비워 둠")
    if miss > 0:
        notes.append(f"땅이 모자라 {round(miss / 0.2)}개는 패턴 밖에 놓음")
    return final, notes


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
