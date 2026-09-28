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

import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .layout import Grid

Cell = Tuple[int, int]
HUB_TRIES = 12  # 허브 자리 후보 수 (많을수록 느림 — 후보 하나에 약 0.05초)


def hub_groups(pieces: dict, scorer) -> List[Tuple[object, List[int]]]:
    """(허브 건물, 범위에 넣을 건물 id) — 강철 요새 → 잔병의 오두막 → 대위 막사 순 (layout_city.guide_report 와 같은 분류)."""
    from .layout_city import CAPTAIN, VETERAN

    kind = {index: piece.type for index, piece in pieces.items()}
    pick = lambda t: next((pieces[index] for index in sorted(pieces) if kind[index] == t), None)
    veteran, captain, brick = pick(VETERAN), pick(CAPTAIN), pick("kBrickHouse")
    # 효과 대상은 중첩된다. 무한 강화 건물은 요새와 막사, 능력치 거처는 오두막과 막사 양쪽 대상이다.
    # apo Naturalist Update의 Captain's Quarters 절도 새 능력치 거처를 명시한다.
    members = lambda key, hub: [
        index for index in sorted(scorer.targets.get(key, ())) if hub is None or index != hub.id
    ]
    builds = members("build", brick)
    homes = members("housing", veteran)
    stats = members("stat", captain)
    return [
        (hub, member_id)
        for hub, member_id in ((brick, builds), (veteran, homes), (captain, stats))
        if hub is not None and member_id
    ]


def covered_members(grid: Grid, pieces: dict, origin: Dict[int, Cell], groups, pad: float) -> dict:
    """효과별로 실제 포함된 대상 집합. 총합이 같아도 다른 효과를 잃는 후보를 구별한다."""
    from .layout_opt import target_in_range

    result = {}
    for hub, members in groups:
        shaped_piece = pieces[hub.id]
        hx, hy = grid.center(*origin[hub.id], shaped_piece.w, shaped_piece.h)
        result[hub.id] = set()
        for member_id in members:
            piece = pieces[member_id]
            x, y = grid.center(*origin[member_id], piece.w, piece.h)
            if target_in_range(x - hx, y - hy, shaped_piece.range + pad, piece, pad):
                result[hub.id].add(member_id)
    return result


def coverage(grid: Grid, pieces: dict, origin: Dict[int, Cell], groups, pad: float) -> int:
    """허브 범위 안에 든 건물 수 (가이드 달성도 합)."""
    return sum(map(len, covered_members(grid, pieces, origin, groups, pad).values()))


def preserves_guide(base: dict, candidate: dict, pad: float = 0.0) -> bool:
    """입구를 새로 막거나 이미 받는 공략 핵심 효과를 빼앗는 이동은 거부한다. 길 열기에도 같은 조건을 쓴다."""
    from . import layout_opt as lo
    from .layout import grid_from_geo

    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return True
    p0, o0 = lo.pieces_from_base(base, grid, lo.housing_types())
    p1, o1 = lo.pieces_from_base(candidate, grid, lo.housing_types())
    if not set(p0) <= set(p1):
        return False
    groups = hub_groups(p0, lo.Scorer(p0, lo._stat_types(base), lo.housing_types(), pad=pad))
    before = covered_members(grid, p0, o0, groups, pad)
    after = covered_members(grid, p1, o1, groups, pad)
    if any(not members <= after[hub] for hub, members in before.items()):
        return False
    entrance = lo.entrance_cells(base.get("geo") or {}, grid)
    occupied0 = set(lo.Layout(grid, p0, o0).occ) & entrance
    occupied1 = set(lo.Layout(grid, p1, o1).occ) & entrance
    return occupied1 <= occupied0


class _State:
    """고치는 중인 배치: 건물 자리·회전·점유 표. clone() 으로 두 방법(하나씩 넣기 / 덩어리 다시 짜기)을 비교한다."""

    def __init__(
        self,
        grid: Grid,
        pieces: dict,
        origin0: Dict[int, Cell],
        pad: float,
        lane: Dict[Cell, float],
        entrance: Set[Cell],
        deadline: Optional[float] = None,
    ):
        self.grid, self.pieces, self.origin0, self.pad, self.lane = grid, pieces, origin0, pad, lane
        self.entrance = entrance
        self.org: Dict[int, Optional[Cell]] = dict(origin0)
        self.turn: Dict[int, int] = {}
        self.occ: Dict[Cell, int] = {}
        for index, origin in origin0.items():
            for column in self.cells(index, origin):
                self.occ[column] = index
        self.locked: Set[int] = {index for index, piece in pieces.items() if not piece.movable}
        self.evicted: List[int] = []
        self.deadline = deadline

    def expired(self) -> bool:
        return self.deadline is not None and time.perf_counter() >= self.deadline

    def clone(self) -> "_State":
        cloned_state = object.__new__(_State)
        cloned_state.grid, cloned_state.pieces, cloned_state.origin0, cloned_state.pad, cloned_state.lane = (
            self.grid,
            self.pieces,
            self.origin0,
            self.pad,
            self.lane,
        )
        cloned_state.entrance = self.entrance
        cloned_state.org, cloned_state.turn, cloned_state.occ = (
            dict(self.org),
            dict(self.turn),
            dict(self.occ),
        )
        cloned_state.locked, cloned_state.evicted = set(self.locked), list(self.evicted)
        cloned_state.deadline = self.deadline
        return cloned_state

    def shape(self, i: int, k: Optional[int] = None):
        from .layout_opt import rotated

        return rotated(self.pieces[i], self.turn.get(i, 0) if k is None else k)

    def cells(self, i: int, origin: Cell, k: Optional[int] = None) -> List[Cell]:
        return [(origin[0] + delta_x, origin[1] + delta_y) for delta_x, delta_y in self.shape(i, k).rel]

    def center(self, i: int, origin: Cell, k: Optional[int] = None) -> Tuple[float, float]:
        shaped_piece = self.shape(i, k)
        return self.grid.center(origin[0], origin[1], shaped_piece.w, shaped_piece.h)

    def near(self, h: int, i: int, origin: Cell, k: Optional[int] = None) -> bool:
        from .layout_opt import target_in_range

        (hx, hy), (x, y) = self.center(h, self.org[h]), self.center(i, origin, k)
        return target_in_range(x - hx, y - hy, self.pieces[h].range + self.pad, self.shape(i, k), self.pad)

    def blockers(self, i: int, origin: Cell, k: Optional[int] = None) -> Optional[Set[int]]:
        result = set()
        for column in self.cells(i, origin, k):
            if column not in self.grid.tiles or column in self.entrance:
                return None
            other_index = self.occ.get(column)
            if other_index is None or other_index == i:
                continue
            if other_index in self.locked:
                return None
            result.add(other_index)
        return result

    def lift(self, i: int):
        if self.org.get(i) is not None:
            for column in self.cells(i, self.org[i]):
                if self.occ.get(column) == i:
                    del self.occ[column]
        self.org[i] = None

    def put(self, i: int, origin: Cell, k: Optional[int] = None):
        k = self.turn.get(i, 0) if k is None else k
        self.lift(i)
        for other_index in self.blockers(i, origin, k) or ():
            self.lift(other_index)
            self.evicted.append(other_index)
        if k:
            self.turn[i] = k
        else:
            self.turn.pop(i, None)
        self.org[i] = origin
        for column in self.cells(i, origin):
            self.occ[column] = i

    def front(self, i: int, origin: Cell, k: Optional[int] = None) -> float:
        return sum(self.lane.get(column, 0.0) for column in self.cells(i, origin, k))

    def ok(self, h: int, i: int, origin: Cell, k: Optional[int] = None, brick: bool = False) -> bool:
        """허브 범위 안 — 강철 요새(쳐야 지어지는 건물)는 발사대 앞 구역에 걸쳐야 한다 (공이 닿게).
        실제 기지: 범위 안이지만 땅 구석에 있던 도박장은 어떤 각도로도 안 닿았다 (2026-09-26)."""
        return self.near(h, i, origin, k) and (not brick or not self.lane or self.front(i, origin, k) > 0)

    def moved(self) -> int:
        return sum(
            1 for index, origin in self.org.items() if origin != self.origin0[index] or self.turn.get(index)
        )

    def shapes_of(self, i: int) -> List[int]:
        """서로 다른 모양이 되는 회전 수 (대칭이면 줄어듦)."""
        seen, result = set(), []
        for step_index in range(4):
            shaped_piece = self.shape(i, step_index)
            if (shaped_piece.w, shaped_piece.h, shaped_piece.rel) not in seen:
                seen.add((shaped_piece.w, shaped_piece.h, shaped_piece.rel))
                result.append(step_index)
        return result


def _dist(a: Optional[Cell], b: Cell) -> float:
    return 1e9 if a is None else abs(a[0] - b[0]) + abs(a[1] - b[1])


def _one_by_one(state: _State, hid: int, members: List[int], spots, brick: bool):
    """범위 밖 건물만 하나씩: 범위 안에서 비켜 둘 건물이 가장 적은 자리 (회전 포함, 안 돌리는 쪽 먼저)."""
    for member_id in sorted(members, key=lambda i: (-len(state.pieces[i].rel), i)):
        if state.expired():
            return
        if member_id in state.locked:
            continue
        if state.org.get(member_id) is not None and state.ok(
            hid, member_id, state.org[member_id], brick=brick
        ):
            state.locked.add(member_id)
            continue
        best = None
        for k in state.shapes_of(member_id):
            for n, origin in enumerate(spots):
                if n % 64 == 0 and state.expired():
                    return
                if not state.ok(hid, member_id, origin, k, brick):
                    continue
                bl = state.blockers(member_id, origin, k)
                if bl is None:
                    continue
                key = (
                    len(bl),
                    k != 0,
                    -state.front(member_id, origin, k) if brick else 0.0,
                    _dist(state.origin0[member_id], origin),
                )
                if best is None or key < best[0]:
                    best = (key, origin, k)
        if best is not None:
            state.put(member_id, best[1], best[2])
        state.locked.add(member_id)


def _repack(state: _State, hid: int, members: List[int], spots, brick: bool):
    """범위 둘레를 비우고 거처 덩어리를 다시 짠다 (layout_city._hub_spot 처럼 빈틈 없이, 회전 포함).
    지금 자리가 범위 안이면 그대로 두는 쪽을 먼저."""
    from .layout_opt import in_range

    hx, hy = state.center(hid, state.org[hid])
    reach = state.pieces[hid].range + state.pad + 2 * state.grid.size
    zone = [
        index
        for index, origin in state.org.items()
        if origin is not None
        and index not in state.locked
        and index != hid
        and in_range(state.center(index, origin)[0] - hx, state.center(index, origin)[1] - hy, reach)
    ]
    for index in zone:
        state.lift(index)
        if index not in members:
            state.evicted.append(index)
    for member_id in sorted(members, key=lambda i: (-len(state.pieces[i].rel), i)):
        if state.expired():
            return
        if member_id in state.locked:
            continue
        best = None
        for k in state.shapes_of(member_id):
            for n, origin in enumerate(spots):
                if n % 64 == 0 and state.expired():
                    return
                if not state.ok(hid, member_id, origin, k, brick):
                    continue
                cells = state.cells(member_id, origin, k)
                if not all(
                    c in state.grid.tiles and c not in state.entrance and c not in state.occ for c in cells
                ):
                    continue
                own = set(cells)
                adj = sum(
                    1
                    for x, y in cells
                    for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                    if n not in own and (n in state.occ or n not in state.grid.tiles)
                )
                stay = origin == state.origin0[member_id] and k == 0
                key = (
                    not stay,
                    -adj,
                    -state.front(member_id, origin, k) if brick else 0.0,
                    _dist(state.origin0[member_id], origin),
                    k,
                )
                if best is None or key < best[0]:
                    best = (key, origin, k)
        if best is not None:
            state.put(member_id, best[1], best[2])
        else:
            state.evicted.append(member_id)
        state.locked.add(member_id)


def repair(
    grid: Grid,
    pieces: dict,
    origin0: Dict[int, Cell],
    groups,
    pad: float,
    lane: Optional[Dict[Cell, float]] = None,
    entrance: Optional[Set[Cell]] = None,
    *,
    deadline: Optional[float] = None,
) -> Optional[Tuple[Dict[int, Cell], Dict[int, int]]]:
    """허브 규칙을 맞춘 배치와 회전 (못 맞추면 가능한 만큼). 비켜 둔 건물을 다시 놓을 곳이 없으면 None."""
    from .layout_opt import in_range

    state = _State(grid, pieces, origin0, pad, lane or {}, entrance or set(), deadline)
    if state.expired():
        return None
    # 게임이 지정한 입구를 차지한 건물은 가장 가까운 빈 자리로 옮긴다.
    for index in sorted({state.occ[c] for c in state.entrance if c in state.occ}):
        if index in state.locked:
            return None
        state.lift(index)
        state.evicted.append(index)
    columns = [c for c, _ in grid.tiles]
    rows = [row for _, row in grid.tiles]
    spots = [
        (c, row) for row in range(min(rows), max(rows) + 1) for c in range(min(columns), max(columns) + 1)
    ]
    for hub, members in groups:
        if state.expired():
            return None
        hid = hub.id
        brick = hub.type == "kBrickHouse"
        # 허브 자리 후보: 지금 범위 밖인 건물 수 + 비켜 둘 건물 수 + (옮기면 1) 가 적은 곳 몇 개 (강철 요새는 발사대 쪽 우선)
        # — 후보마다 거처를 넣어 보고 범위 안 수가 가장 많고 덜 옮기는 쪽 (지금 자리에 묶으면 7/12 → 9/12 에서 멈춤)
        cands = [state.org[hid]]
        if hid not in state.locked:
            scored = []
            for n, origin in enumerate(spots):
                if n % 64 == 0 and state.expired():
                    return None
                bl = state.blockers(hid, origin)
                if bl is None:
                    continue
                result = 0
                hx, hy = state.center(hid, origin)
                for member_id in members:
                    if member_id in bl or state.org.get(member_id) is None:
                        result += 1
                        continue
                    x, y = state.center(member_id, state.org[member_id])
                    from .layout_opt import target_in_range

                    result += not target_in_range(
                        x - hx, y - hy, hub.range + pad, state.shape(member_id), pad
                    ) or (brick and bool(state.lane) and state.front(member_id, state.org[member_id]) <= 0)
                scored.append(
                    (
                        (
                            result + len(bl - set(members)) + (origin != state.org[hid]),
                            -state.front(hid, origin) if brick else 0.0,
                            _dist(origin0[hid], origin),
                        ),
                        origin,
                    )
                )
            scored.sort()
            cands = [origin for _, origin in scored[:HUB_TRIES]] or cands

        def got(candidate_state: _State) -> int:
            return sum(
                1
                for member_id in members
                if candidate_state.org.get(member_id) is not None
                and candidate_state.ok(hid, member_id, candidate_state.org[member_id], brick=brick)
            )

        best = None
        for origin in cands:
            if state.expired():
                return None
            base_st = state.clone()
            if origin is not None and origin != base_st.org[hid]:
                base_st.put(hid, origin)
            base_st.locked.add(hid)
            if base_st.org.get(hid) is None:
                continue
            first_candidate = base_st.clone()
            _one_by_one(first_candidate, hid, members, spots, brick)
            if state.expired():
                return None
            tries = [first_candidate]
            if got(first_candidate) < len(members):
                second_candidate = base_st.clone()
                _repack(second_candidate, hid, members, spots, brick)
                if state.expired():
                    return None
                tries.append(second_candidate)
            for candidate_state in tries:
                key = (got(candidate_state), -candidate_state.moved())
                if best is None or key > best[0]:
                    best = (key, candidate_state)
        if best is None:
            state.locked.add(hid)
            continue  # 허브 자리를 못 찾음 — 아래에서 빈 곳에 다시 놓는다
        state = best[1]
    # 공사·강화 중인 건물은 종류와 허브 유무에 관계없이 공이 지나는 앞 구역으로 옮긴다.
    # 허브 단계에서 이미 앞에 둔 건물은 움직이지 않는다.
    if state.lane:
        for index in sorted(
            (index for index, piece in pieces.items() if piece.unfinished and piece.movable),
            key=lambda i: (-len(pieces[i].rel), i),
        ):
            if state.expired():
                return None
            if state.org.get(index) is not None and state.front(index, state.org[index]) > 0:
                state.locked.add(index)
                continue
            best = None
            for k in state.shapes_of(index):
                for origin in spots:
                    front = state.front(index, origin, k)
                    if front <= 0:
                        continue
                    bl = state.blockers(index, origin, k)
                    if bl is None:
                        continue
                    key = (len(bl), _dist(origin0[index], origin), -front, k)
                    if best is None or key < best[0]:
                        best = (key, origin, k)
            if best is not None:
                state.put(index, best[1], best[2])
                state.locked.add(index)
    # 비켜 둔 건물: 원래 자리에서 가장 가까운 빈 곳 (큰 것부터, 원래 방향)
    for e in sorted(
        set(index for index in state.evicted if state.org.get(index) is None),
        key=lambda i: (-len(pieces[i].rel), i),
    ):
        if state.expired():
            return None
        best = None
        for origin in spots:
            if all(
                column in grid.tiles and column not in state.entrance and column not in state.occ
                for column in state.cells(e, origin, 0)
            ):
                k = _dist(origin0[e], origin)
                if best is None or k < best[0]:
                    best = (k, origin)
        if best is None:
            return None
        state.put(e, best[1], 0)
    if any(origin is None for origin in state.org.values()):
        return None
    return dict(state.org), dict(state.turn)


def satisfied(
    grid: Grid,
    pieces: dict,
    origin: Dict[int, Cell],
    groups,
    pad: float,
    lane: Optional[Dict[Cell, float]] = None,
) -> Set[int]:
    """가이드 규칙을 이미 지킨 건물 (허브 + 범위 안에 든 건물) — 담금질이 다시 빼지 않게 묶는다.
    pieces 는 회전을 반영한 모양."""
    from .layout_opt import in_range

    lane = lane or {}
    result: Set[int] = set()
    for hub, members in groups:
        shaped_piece = pieces[hub.id]
        hx, hy = grid.center(*origin[hub.id], shaped_piece.w, shaped_piece.h)
        brick = hub.type == "kBrickHouse"
        result.add(hub.id)
        for member_id in members:
            piece = pieces[member_id]
            x, y = grid.center(*origin[member_id], piece.w, piece.h)
            from .layout_opt import target_in_range

            if not target_in_range(x - hx, y - hy, hub.range + pad, piece, pad):
                continue
            if (
                brick
                and lane
                and sum(
                    lane.get((origin[member_id][0] + delta_x, origin[member_id][1] + delta_y), 0.0)
                    for delta_x, delta_y in piece.rel
                )
                <= 0
            ):
                continue
            result.add(member_id)
    return result
