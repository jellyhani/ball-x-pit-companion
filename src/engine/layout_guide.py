"""가이드 배치 (적게 옮기기): 지금 배치에서 출발해 가이드 허브 규칙만 '고쳐' 맞춘다.

Steam 공략 3개(Zarcos·Drake·apo — README '자료 출처')의 허브 규칙:
  강철 요새  공사 중·무한 강화 건물을 범위 안에, 발사대 쪽 (쳐야 지어지므로 공이 닿게)
  잔병의 오두막  거처를 모두 범위 안에
  대위 막사  능력치 건물을 모두 범위 안에
처음부터 다시 짜는 계획도시(layout_city.plan_city)는 이미 거의 된 기지에서도 80개를 옮기라고 했다(+2%, 2026-09-26).
여기서는 이미 범위 안에 있는 건물은 그대로 두고, 밖에 있는 것만 범위 안 자리로 옮긴다 — 그 자리를 막는 건물은
가장 가까운 빈 곳으로 비켜 둔다. 그 뒤 담금질(layout_opt.optimize preset "guide")이 옮기기 하나당 벌점을 두고 효과를 올린다.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from .layout import Grid

Cell = Tuple[int, int]
HUB_TRIES = 12      # 허브 자리 후보 수 (많을수록 느림 — 후보 하나에 약 0.05초)


def hub_groups(pieces: dict, scorer) -> List[Tuple[object, List[int]]]:
    """(허브 건물, 범위에 넣을 건물 id) — 강철 요새 → 잔병의 오두막 → 대위 막사 순 (layout_city.guide_report 와 같은 분류)."""
    from .layout_city import CAPTAIN, PRODUCERS, VETERAN
    from .layout_opt import STATUE_TYPES, TILE_RES
    kind = {i: p.type for i, p in pieces.items()}
    field = {i for i, t in kind.items() if t in TILE_RES or t in PRODUCERS or t == "kGoldMine"}
    pick = lambda t: next((pieces[i] for i in sorted(pieces) if kind[i] == t), None)
    veteran, captain, brick = pick(VETERAN), pick(CAPTAIN), pick("kBrickHouse")
    builds = [i for i in sorted(pieces) if i not in field and kind[i] != "kBrickHouse"
              and (kind[i] in STATUE_TYPES or pieces[i].unfinished)]
    house = set(scorer.targets.get("housing", ()))
    homes = [i for i in sorted(pieces) if i in house and i not in field and i not in builds
             and (veteran is None or i != veteran.id)]
    stats = [i for i in sorted(pieces) if i in set(scorer.targets.get("stat", ())) and i not in house
             and i not in field and i not in builds and (captain is None or i != captain.id)]
    return [(h, m) for h, m in ((brick, builds), (veteran, homes), (captain, stats)) if h is not None and m]


def coverage(grid: Grid, pieces: dict, origin: Dict[int, Cell], groups, pad: float) -> int:
    """허브 범위 안에 든 건물 수 (가이드 달성도 합)."""
    from .layout_opt import in_range
    n = 0
    for h, members in groups:
        hx, hy = grid.center(*origin[h.id], h.w, h.h)
        for m in members:
            p = pieces[m]
            x, y = grid.center(*origin[m], p.w, p.h)
            n += in_range(x - hx, y - hy, h.range + pad)
    return n


class _State:
    """고치는 중인 배치: 건물 자리·회전·점유 표. clone() 으로 두 방법(하나씩 넣기 / 덩어리 다시 짜기)을 비교한다."""

    def __init__(self, grid: Grid, pieces: dict, origin0: Dict[int, Cell], pad: float, lane: Dict[Cell, float],
                 entrance: Set[Cell]):
        self.grid, self.pieces, self.origin0, self.pad, self.lane = grid, pieces, origin0, pad, lane
        self.entrance = entrance
        self.org: Dict[int, Optional[Cell]] = dict(origin0)
        self.turn: Dict[int, int] = {}
        self.occ: Dict[Cell, int] = {}
        for i, o in origin0.items():
            for c in self.cells(i, o):
                self.occ[c] = i
        self.locked: Set[int] = {i for i, p in pieces.items() if not p.movable}
        self.evicted: List[int] = []

    def clone(self) -> "_State":
        c = object.__new__(_State)
        c.grid, c.pieces, c.origin0, c.pad, c.lane = self.grid, self.pieces, self.origin0, self.pad, self.lane
        c.entrance = self.entrance
        c.org, c.turn, c.occ = dict(self.org), dict(self.turn), dict(self.occ)
        c.locked, c.evicted = set(self.locked), list(self.evicted)
        return c

    def shape(self, i: int, k: Optional[int] = None):
        from .layout_opt import rotated
        return rotated(self.pieces[i], self.turn.get(i, 0) if k is None else k)

    def cells(self, i: int, o: Cell, k: Optional[int] = None) -> List[Cell]:
        return [(o[0] + dx, o[1] + dy) for dx, dy in self.shape(i, k).rel]

    def center(self, i: int, o: Cell, k: Optional[int] = None) -> Tuple[float, float]:
        q = self.shape(i, k)
        return self.grid.center(o[0], o[1], q.w, q.h)

    def near(self, h: int, i: int, o: Cell, k: Optional[int] = None) -> bool:
        from .layout_opt import in_range
        (hx, hy), (x, y) = self.center(h, self.org[h]), self.center(i, o, k)
        return in_range(x - hx, y - hy, self.pieces[h].range + self.pad)

    def blockers(self, i: int, o: Cell, k: Optional[int] = None) -> Optional[Set[int]]:
        out = set()
        for c in self.cells(i, o, k):
            if c not in self.grid.tiles or c in self.entrance:
                return None
            j = self.occ.get(c)
            if j is None or j == i:
                continue
            if j in self.locked:
                return None
            out.add(j)
        return out

    def lift(self, i: int):
        if self.org.get(i) is not None:
            for c in self.cells(i, self.org[i]):
                if self.occ.get(c) == i:
                    del self.occ[c]
        self.org[i] = None

    def put(self, i: int, o: Cell, k: Optional[int] = None):
        k = self.turn.get(i, 0) if k is None else k
        self.lift(i)
        for j in self.blockers(i, o, k) or ():
            self.lift(j)
            self.evicted.append(j)
        if k:
            self.turn[i] = k
        else:
            self.turn.pop(i, None)
        self.org[i] = o
        for c in self.cells(i, o):
            self.occ[c] = i

    def front(self, i: int, o: Cell, k: Optional[int] = None) -> float:
        return sum(self.lane.get(c, 0.0) for c in self.cells(i, o, k))

    def ok(self, h: int, i: int, o: Cell, k: Optional[int] = None, brick: bool = False) -> bool:
        """허브 범위 안 — 강철 요새(쳐야 지어지는 건물)는 발사대 앞 구역에 걸쳐야 한다 (공이 닿게).
        실제 기지: 범위 안이지만 땅 구석에 있던 도박장은 어떤 각도로도 안 닿았다 (2026-09-26)."""
        return self.near(h, i, o, k) and (not brick or not self.lane or self.front(i, o, k) > 0)

    def moved(self) -> int:
        return sum(1 for i, o in self.org.items() if o != self.origin0[i] or self.turn.get(i))

    def shapes_of(self, i: int) -> List[int]:
        """서로 다른 모양이 되는 회전 수 (대칭이면 줄어듦)."""
        seen, out = set(), []
        for k in range(4):
            q = self.shape(i, k)
            if (q.w, q.h, q.rel) not in seen:
                seen.add((q.w, q.h, q.rel))
                out.append(k)
        return out


def _dist(a: Optional[Cell], b: Cell) -> float:
    return 1e9 if a is None else abs(a[0] - b[0]) + abs(a[1] - b[1])


def _one_by_one(st: _State, hid: int, members: List[int], spots, brick: bool):
    """범위 밖 건물만 하나씩: 범위 안에서 비켜 둘 건물이 가장 적은 자리 (회전 포함, 안 돌리는 쪽 먼저)."""
    for m in sorted(members, key=lambda i: (-len(st.pieces[i].rel), i)):
        if m in st.locked:
            continue
        if st.org.get(m) is not None and st.ok(hid, m, st.org[m], brick=brick):
            st.locked.add(m)
            continue
        best = None
        for k in st.shapes_of(m):
            for o in spots:
                if not st.ok(hid, m, o, k, brick):
                    continue
                bl = st.blockers(m, o, k)
                if bl is None:
                    continue
                key = (len(bl), k != 0, -st.front(m, o, k) if brick else 0.0, _dist(st.origin0[m], o))
                if best is None or key < best[0]:
                    best = (key, o, k)
        if best is not None:
            st.put(m, best[1], best[2])
        st.locked.add(m)


def _repack(st: _State, hid: int, members: List[int], spots, brick: bool):
    """범위 둘레를 비우고 거처 덩어리를 다시 짠다 (layout_city._hub_spot 처럼 빈틈 없이, 회전 포함).
    지금 자리가 범위 안이면 그대로 두는 쪽을 먼저."""
    from .layout_opt import in_range
    hx, hy = st.center(hid, st.org[hid])
    reach = st.pieces[hid].range + st.pad + 2 * st.grid.size
    zone = [i for i, o in st.org.items() if o is not None and i not in st.locked and i != hid
            and in_range(st.center(i, o)[0] - hx, st.center(i, o)[1] - hy, reach)]
    for i in zone:
        st.lift(i)
        if i not in members:
            st.evicted.append(i)
    for m in sorted(members, key=lambda i: (-len(st.pieces[i].rel), i)):
        if m in st.locked:
            continue
        best = None
        for k in st.shapes_of(m):
            for o in spots:
                if not st.ok(hid, m, o, k, brick):
                    continue
                cells = st.cells(m, o, k)
                if not all(c in st.grid.tiles and c not in st.entrance and c not in st.occ for c in cells):
                    continue
                own = set(cells)
                adj = sum(1 for x, y in cells for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                          if n not in own and (n in st.occ or n not in st.grid.tiles))
                stay = o == st.origin0[m] and k == 0
                key = (not stay, -adj, -st.front(m, o, k) if brick else 0.0, _dist(st.origin0[m], o), k)
                if best is None or key < best[0]:
                    best = (key, o, k)
        if best is not None:
            st.put(m, best[1], best[2])
        else:
            st.evicted.append(m)
        st.locked.add(m)


def repair(grid: Grid, pieces: dict, origin0: Dict[int, Cell], groups, pad: float,
           lane: Optional[Dict[Cell, float]] = None, entrance: Optional[Set[Cell]] = None
           ) -> Optional[Tuple[Dict[int, Cell], Dict[int, int]]]:
    """허브 규칙을 맞춘 배치와 회전 (못 맞추면 가능한 만큼). 비켜 둔 건물을 다시 놓을 곳이 없으면 None."""
    from .layout_opt import in_range
    st = _State(grid, pieces, origin0, pad, lane or {}, entrance or set())
    # 게임이 지정한 입구를 차지한 건물은 가장 가까운 빈 자리로 옮긴다.
    for i in sorted({st.occ[c] for c in st.entrance if c in st.occ}):
        if i in st.locked:
            return None
        st.lift(i)
        st.evicted.append(i)
    cols = [c for c, _ in grid.tiles]
    rows = [r for _, r in grid.tiles]
    spots = [(c, r) for r in range(min(rows), max(rows) + 1) for c in range(min(cols), max(cols) + 1)]
    for h, members in groups:
        hid = h.id
        brick = h.type == "kBrickHouse"
        # 허브 자리 후보: 지금 범위 밖인 건물 수 + 비켜 둘 건물 수 + (옮기면 1) 가 적은 곳 몇 개 (강철 요새는 발사대 쪽 우선)
        # — 후보마다 거처를 넣어 보고 범위 안 수가 가장 많고 덜 옮기는 쪽 (지금 자리에 묶으면 7/12 → 9/12 에서 멈춤)
        cands = [st.org[hid]]
        if hid not in st.locked:
            scored = []
            for o in spots:
                bl = st.blockers(hid, o)
                if bl is None:
                    continue
                out = 0
                hx, hy = st.center(hid, o)
                for m in members:
                    if m in bl or st.org.get(m) is None:
                        out += 1
                        continue
                    x, y = st.center(m, st.org[m])
                    out += not in_range(x - hx, y - hy, h.range + pad) or (brick and bool(st.lane)
                                                                          and st.front(m, st.org[m]) <= 0)
                scored.append(((out + len(bl - set(members)) + (o != st.org[hid]),
                                -st.front(hid, o) if brick else 0.0, _dist(origin0[hid], o)), o))
            scored.sort()
            cands = [o for _, o in scored[:HUB_TRIES]] or cands

        def got(s: _State) -> int:
            return sum(1 for m in members if s.org.get(m) is not None and s.ok(hid, m, s.org[m], brick=brick))
        best = None
        for o in cands:
            base_st = st.clone()
            if o is not None and o != base_st.org[hid]:
                base_st.put(hid, o)
            base_st.locked.add(hid)
            if base_st.org.get(hid) is None:
                continue
            a = base_st.clone()
            _one_by_one(a, hid, members, spots, brick)
            tries = [a]
            if got(a) < len(members):
                b = base_st.clone()
                _repack(b, hid, members, spots, brick)
                tries.append(b)
            for t in tries:
                key = (got(t), -t.moved())
                if best is None or key > best[0]:
                    best = (key, t)
        if best is None:
            st.locked.add(hid)
            continue                                        # 허브 자리를 못 찾음 — 아래에서 빈 곳에 다시 놓는다
        st = best[1]
    # 공사·강화 중인 건물은 종류와 허브 유무에 관계없이 공이 지나는 앞 구역으로 옮긴다.
    # 허브 단계에서 이미 앞에 둔 건물은 움직이지 않는다.
    if st.lane:
        for i in sorted((i for i, p in pieces.items() if p.unfinished and p.movable),
                        key=lambda i: (-len(pieces[i].rel), i)):
            if st.org.get(i) is not None and st.front(i, st.org[i]) > 0:
                st.locked.add(i)
                continue
            best = None
            for k in st.shapes_of(i):
                for o in spots:
                    front = st.front(i, o, k)
                    if front <= 0:
                        continue
                    bl = st.blockers(i, o, k)
                    if bl is None:
                        continue
                    key = (len(bl), _dist(origin0[i], o), -front, k)
                    if best is None or key < best[0]:
                        best = (key, o, k)
            if best is not None:
                st.put(i, best[1], best[2])
                st.locked.add(i)
    # 비켜 둔 건물: 원래 자리에서 가장 가까운 빈 곳 (큰 것부터, 원래 방향)
    for e in sorted(set(i for i in st.evicted if st.org.get(i) is None), key=lambda i: (-len(pieces[i].rel), i)):
        best = None
        for o in spots:
            if all(c in grid.tiles and c not in st.entrance and c not in st.occ for c in st.cells(e, o, 0)):
                k = _dist(origin0[e], o)
                if best is None or k < best[0]:
                    best = (k, o)
        if best is None:
            return None
        st.put(e, best[1], 0)
    if any(o is None for o in st.org.values()):
        return None
    return dict(st.org), dict(st.turn)


def satisfied(grid: Grid, pieces: dict, origin: Dict[int, Cell], groups, pad: float,
              lane: Optional[Dict[Cell, float]] = None) -> Set[int]:
    """가이드 규칙을 이미 지킨 건물 (허브 + 범위 안에 든 건물) — 담금질이 다시 빼지 않게 묶는다.
    pieces 는 회전을 반영한 모양."""
    from .layout_opt import in_range
    lane = lane or {}
    out: Set[int] = set()
    for h, members in groups:
        q = pieces[h.id]
        hx, hy = grid.center(*origin[h.id], q.w, q.h)
        brick = h.type == "kBrickHouse"
        out.add(h.id)
        for m in members:
            p = pieces[m]
            x, y = grid.center(*origin[m], p.w, p.h)
            if not in_range(x - hx, y - hy, h.range + pad):
                continue
            if brick and lane and sum(lane.get((origin[m][0] + dx, origin[m][1] + dy), 0.0) for dx, dy in p.rel) <= 0:
                continue
            out.add(m)
    return out
