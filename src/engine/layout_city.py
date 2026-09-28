"""가이드 배치: Steam 공략 3개(Zarcos·Drake·apo — README '자료 출처')의 규칙으로 기지를 다시 짠다 (패킹).

허브 먼저, 둘레를 채운다:
  거처       잔병의 오두막을 먼저 놓고 거처를 전부 그 범위 안에 (채집 거처는 발사대 쪽 가장자리 — 둘러싸이면 못 캠)
  능력치     대위 막사를 먼 쪽 가운데에 놓고 능력치 건물을 전부 그 범위 안에
  강철 요새  무한 강화·공사 중 건물을 그 범위 안에 (공이 닿는 발사대 쪽)
  자원 들판  발사대 쪽(가이드: 땅의 약 70%)에 생산 건물을 자기 타일 한가운데 — 범위가 닿는 겹까지 전부 (농장 6×6),
             같은 자원 채집 거처 옆이면 먼저. 남는 타일은 채집 거처 범위에 먹인다
  금광       쓰지 않는다 (사용자 결정) — 들판 뒤쪽에 모아 두고 철거 후보로.
마을 폭·위치와 유닛 빈칸 예약 여부를 바꿔 가며 여러 번 짜 보고, 못 넣은 건물 수 → 가이드 달성도(허브 범위에 든 수)
→ 범위 효과(+ 채집 거처 가점) − 빈틈 순으로 고른다. 마지막에 같은 모양끼리 맞바꾸기와 타일 다듬기(외톨이 타일 없이 뭉치게)를 한다.
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
TIDY_W = (
    0.02  # 자원 타일 정돈 가점 (맞닿은 변 하나당) — 범위 효과 타일 하나(≈1)보다 훨씬 작게, 같을 때만 가른다
)
MISS_W = 50.0  # 못 넣어서 아무 데나 놓은 건물 하나당 감점 (정돈이 깨지므로 가장 크게)
# 채집·재생 거처가 자기 타일을 범위에 둔 것 하나당 가점 (범위 효과 점수 단위, 임의). 절대 조건으로 두면 밀밭을 농장(타일당 1.0)
# 대신 거처(0.25~0.37)에 주느라 농장 범위가 비었다 (사용자 지적 2026-09-26) — 효과 점수에 조금 얹는 정도로만.
HH_W = 1.0
WASTE_W = 0.5  # 마을 테두리 사각형 안의 빈칸 하나당 감점 (빽빽할수록 좋게) — 값은 임의


class _Item:
    """한 번에 놓는 묶음: 건물들(상대 원점)과 비워 둘 칸(예약)."""

    __slots__ = ("members", "cells", "w", "h")

    def __init__(self, members: List[Tuple[int, Cell]], cells: Set[Cell], w: int, h: int):
        self.members, self.cells, self.w, self.h = members, cells, w, h


def _single(piece) -> _Item:
    return _Item([(piece.id, (0, 0))], set(piece.rel), piece.w, piece.h)


def _unit(prod, tiles: list, pad: float, reserve: bool, size: float = 1.0) -> Tuple[_Item, list]:
    """생산 건물 + 둘레 자원 타일 — 범위가 닿는 만큼 몇 겹이든 (농장 범위 3칸이면 두 겹 = 6×6, 채석장 2칸이면 한 겹 = 4×4).
    한 겹만 두르면 바깥 겹의 범위가 빈 땅·다른 건물로 버려진다 (사용자 지적 2026-09-26).
    size: 타일 한 칸의 월드 크기 (범위는 월드 단위). 돌려주는 값: (묶음, 안 쓴 타일)."""
    from .layout_opt import in_range

    if not tiles:
        return _single(prod), []
    sw, sh = Counter((tile.w, tile.h) for tile in tiles).most_common(1)[0][0]
    fit = [tile for tile in tiles if (tile.w, tile.h) == (sw, sh)]
    other = [tile for tile in tiles if (tile.w, tile.h) != (sw, sh)]

    def rings(half: float, step: int) -> int:  # 건물 가장자리에서 범위가 닿는 겹 수
        n = 0
        while n < 8 and in_range((half + (n + 0.5) * step) * size, 0.0, prod.range + pad):
            n += 1
        return max(1, n)

    kx, ky = rings(prod.w / 2, sw), rings(prod.h / 2, sh)
    origin_x, origin_y = kx * sw, ky * sh  # 생산 건물 왼쪽 아래 (유닛 기준)
    uw, uh = prod.w + 2 * origin_x, prod.h + 2 * origin_y
    px, py = origin_x + prod.w / 2, origin_y + prod.h / 2  # 생산 건물 중심 (유닛 왼쪽 아래 기준, 타일 단위)
    slots = []
    for x in range(0, uw - sw + 1, sw):
        for y in range(0, uh - sh + 1, sh):
            if x + sw > origin_x and x < origin_x + prod.w and y + sh > origin_y and y < origin_y + prod.h:
                continue  # 생산 건물 자리
            delta_x, delta_y = x + sw / 2 - px, y + sh / 2 - py
            if in_range(delta_x * size, delta_y * size, prod.range + pad):
                slots.append(((delta_x * delta_x + delta_y * delta_y), (x, y)))
    slots = [s for _, s in sorted(slots)]
    fit.sort(key=lambda t: -t.cap)  # 고급 타일(용량 큼)부터 유닛에
    use, rest = fit[: len(slots)], fit[len(slots) :]
    members = [(prod.id, (origin_x, origin_y))] + [(tile.id, s) for tile, s in zip(use, slots)]
    cells = {(origin_x + delta_x, origin_y + delta_y) for delta_x, delta_y in prod.rel}
    for tile, (x, y) in zip(use, slots):
        cells |= {(x + delta_x, y + delta_y) for delta_x, delta_y in tile.rel}
    if reserve:  # 빈 자리도 비워 둔다 (나중에 타일을 사서 채울 곳)
        for x, y in slots[len(use) :]:
            cells |= {(x + delta_x, y + delta_y) for delta_x in range(sw) for delta_y in range(sh)}
    return _Item(members, cells, uw, uh), rest + other


def _block(tiles: list, columns: int) -> _Item:
    """같은 크기 자원 타일을 가로 cols 개씩 네모 블록으로."""
    sw, sh = tiles[0].w, tiles[0].h
    rows = -(-len(tiles) // columns)
    members, cells = [], set()
    for step_index, tile in enumerate(tiles):
        x, y = (step_index % columns) * sw, (step_index // columns) * sh
        members.append((tile.id, (x, y)))
        cells |= {(x + delta_x, y + delta_y) for delta_x, delta_y in tile.rel}
    return _Item(members, cells, columns * sw, rows * sh)


def _place_block(tiles: list, order: Sequence[Cell], free: Set[Cell], result: Dict[int, Cell]) -> int:
    """자원 타일 묶음을 네모 블록으로 놓는다: 정사각형에 가까운 모양부터, 안 들어가면 반으로 나눠서.
    돌려주는 값: 못 놓은 타일 수 (그런 배치는 쓰지 않는다)."""
    item_count = len(tiles)
    shapes = sorted(
        range(1, item_count + 1), key=lambda c: (abs(c - -(-item_count // c)), -((item_count % c) == 0), c)
    )
    for columns in shapes[:4]:  # 네모에 가까운 모양 몇 개만
        it = _block(tiles, columns)
        at = _place(it, order, free)
        if at is not None:
            _put(it, at, free, result)
            return 0
    if item_count == 1:
        return 1
    return _place_block(tiles[: item_count // 2], order, free, result) + _place_block(
        tiles[item_count // 2 :], order, free, result
    )


def _place(item: _Item, order: Sequence[Cell], free: Set[Cell]) -> Optional[Cell]:
    k = _place_idx(item, order, free)
    return order[k] if k is not None else None


def _place_idx(item: _Item, order: Sequence[Cell], free: Set[Cell]) -> Optional[int]:
    for step_index, (c, row) in enumerate(order):
        if all((c + delta_x, row + delta_y) in free for delta_x, delta_y in item.cells):
            return step_index
    return None


def _fit(piece, order: Sequence[Cell], free: Set[Cell]):
    """건물 하나를 네 방향(시계 방향 0·90·180·270°) 중 순서상 가장 앞 자리에 들어가는 방향으로.
    ㄱ·ㅜ·ㅠ 자 건물은 방향에 따라 빈 모서리가 달라 옆 건물과 맞물린다. 같은 자리면 덜 돌리는 쪽.
    돌려주는 값: (모양, 자리, 시계 방향 90° 횟수)."""
    from .layout_opt import rotated

    best = None
    seen = set()
    for rotation in range(4):
        other_piece = rotated(piece, rotation)
        if (other_piece.w, other_piece.h, other_piece.rel) in seen:
            continue  # 대칭이라 같은 모양
        seen.add((other_piece.w, other_piece.h, other_piece.rel))
        k = _place_idx(_single(other_piece), order, free)
        if k is not None and (best is None or k < best[0]):
            best = (k, other_piece, rotation)
    return (best[1], order[best[0]], best[2]) if best else (piece, None, 0)


def _put(item: _Item, at: Cell, free: Set[Cell], result: Dict[int, Cell]):
    for delta_x, delta_y in item.cells:
        free.discard((at[0] + delta_x, at[1] + delta_y))
    for index, (x, y) in item.members:
        result[index] = (at[0] + x, at[1] + y)


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

    return {tile for tile, e in EFFECTS.items() if isinstance(e[0], int) and e[3] == "upgraded"}


def _order_near(
    cells: Sequence[Cell], piece, target: Tuple[float, float], tie: Dict[Cell, int]
) -> List[Cell]:
    """건물 p 의 중심이 target(타일 좌표)에 가까운 자리 순 (범위가 사각형이라 체비쇼프 거리)."""
    return sorted(
        cells,
        key=lambda t: (
            max(abs(t[0] + piece.w / 2 - target[0]), abs(t[1] + piece.h / 2 - target[1])),
            tie.get(t, 0),
        ),
    )


def _center(piece, origins: Cell) -> Tuple[float, float]:
    return (origins[0] + piece.w / 2, origins[1] + piece.h / 2)


def _best_cover(
    piece, free: Set[Cell], targets: List[Tuple[float, float, float]], rng: float, tie: Dict[Cell, int]
):
    """범위(타일 단위, 사각형) 안에 대상(중심 x, y, 무게)이 가장 많이 드는 자리·방향. 대상 근처 자리만 본다.
    돌려주는 값: (모양, 자리, 회전) 또는 들어갈 곳이 없으면 (p, None, 0)."""
    from .layout_opt import rotated

    best = None
    seen = set()
    for rotation in range(4):
        other_piece = rotated(piece, rotation)
        if (other_piece.w, other_piece.h, other_piece.rel) in seen:
            continue
        seen.add((other_piece.w, other_piece.h, other_piece.rel))
        cand = set()
        for tx, ty, _w in targets:
            for c in range(int(tx - rng - other_piece.w / 2) - 1, int(tx + rng - other_piece.w / 2) + 2):
                for rr in range(int(ty - rng - other_piece.h / 2) - 1, int(ty + rng - other_piece.h / 2) + 2):
                    cand.add((c, rr))
        for origins in cand:
            if not all(
                (origins[0] + delta_x, origins[1] + delta_y) in free for delta_x, delta_y in other_piece.rel
            ):
                continue
            center_x, center_y = _center(other_piece, origins)
            value = sum(
                wt
                for tx, ty, wt in targets
                if abs(tx - center_x) <= rng + 1e-6 and abs(ty - center_y) <= rng + 1e-6
            )
            key = (value, -tie.get(origins, 10**6), -rotation)
            if best is None or key > best[0]:
                best = (key, other_piece, origins, rotation)
    if best is None or best[0][0] <= 0:
        return piece, None, 0
    return best[1], best[2], best[3]


def _in_box(src, so: Cell, piece, origins: Cell, rng: float) -> bool:
    """p 의 중심이 게임 값으로 보정한 범위(타일 단위 rng) 안인지."""
    from .layout_opt import in_range

    sx, sy = _center(src, so)
    center_x, center_y = _center(piece, origins)
    return in_range(center_x - sx, center_y - sy, rng)


def _hub_spot(piece, free: Set[Cell], hub: Tuple[float, float], rng: float, depth, near_side: bool):
    """허브(잔병의 오두막·대위 막사·강철 요새) 범위 안의 자리 — 가이드처럼 허브 둘레에 빽빽이.
    고르는 기준: 범위 안에서 — near_side 면 발사대 쪽 가장자리 > 옆이 막힌 칸이 많음 > 허브에서 가까움,
    아니면 옆이 막힌 칸이 많음(빈틈 없이 맞물림) > 허브에서 가까움.
    돌려주는 값: (모양, 자리, 회전) 또는 범위 안에 들어갈 곳이 없으면 (p, None, 0)."""
    from .layout_opt import rotated

    hx, hy = hub
    best = None
    seen = set()
    for rotation in range(4):
        other_piece = rotated(piece, rotation)
        if (other_piece.w, other_piece.h, other_piece.rel) in seen:
            continue
        seen.add((other_piece.w, other_piece.h, other_piece.rel))
        for c in range(int(hx - rng - other_piece.w) - 1, int(hx + rng) + 2):
            for rr in range(int(hy - rng - other_piece.h) - 1, int(hy + rng) + 2):
                cells = [(c + delta_x, rr + delta_y) for delta_x, delta_y in other_piece.rel]
                if not all(tile in free for tile in cells):
                    continue
                center_x, center_y = c + other_piece.w / 2, rr + other_piece.h / 2
                if abs(center_x - hx) > rng + 1e-6 or abs(center_y - hy) > rng + 1e-6:
                    continue
                own = set(cells)
                adj = sum(
                    1
                    for x, y in cells
                    for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                    if n not in own and n not in free
                )
                # 앞면 건물(채집 거처·강철 요새·유령의 집)은 가장자리가 먼저 — 안쪽에 박히면 둘레에 캘 타일을 못 두고
                # 공도 안 닿는다. 나머지 거처는 빈틈 없이 맞물리는 게 먼저.
                dist = max(abs(center_x - hx), abs(center_y - hy))
                key = ((min(depth(y) for _, y in cells), -adj, dist) if near_side else (-adj, dist, 0)) + (
                    rotation,
                    c,
                    rr,
                )
                if best is None or key < best[0]:
                    best = (key, other_piece, (c, rr), rotation)
    return (best[1], best[2], best[3]) if best else (piece, None, 0)


def plan_city(
    grid: Grid,
    pieces: dict,
    origin0: Dict[int, Cell],
    launcher_rc: Optional[Cell],
    scorer,
    pad: float = 0.0,
    hit: Set[int] = frozenset(),
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
    후보(마을 폭·위치·넣는 순서·유닛 빈칸 예약)마다 가이드 달성도(못 넣은 건물 없음 > 오두막 > 대위 막사 > 강철 요새)를
    먼저, 그다음 범위 효과 + 채집 거처 가점(HH_W) − 빈틈으로 고른다. 지금 배치보다 나아지지 않으면 그대로 둔다."""
    from .layout_opt import STATUE_TYPES, TILE_RES, Layout

    tiles = grid.tiles
    size = grid.size
    fixed = {index for index, piece in pieces.items() if not piece.movable}
    base_free = set(tiles)
    for index in fixed:
        base_free -= {
            (origin0[index][0] + delta_x, origin0[index][1] + delta_y)
            for delta_x, delta_y in pieces[index].rel
        }
    columns = [c for c, _ in tiles]
    rows = [row for _, row in tiles]
    lr = launcher_rc[1] if launcher_rc else min(rows) - 1
    near_first = abs(min(rows) - lr) <= abs(max(rows) - lr)  # 발사대가 아래쪽이면 행이 작을수록 가깝다
    height = max(rows) - min(rows) + 1

    def depth(r: int) -> int:  # 발사대에서 먼 정도 (0 = 가장 가까운 행)
        return r - min(rows) if near_first else max(rows) - r

    def row_at(d: float) -> float:  # depth → 행 좌표 (타일 단위, 중심)
        return min(rows) + d if near_first else max(rows) - d

    movable = [index for index in pieces if index not in fixed]
    kind = {index: pieces[index].type for index in movable}
    mines = sorted((index for index in movable if kind[index] == "kGoldMine"), key=lambda i: origin0[i])
    by_res: Dict[int, list] = {1: [], 2: [], 3: []}
    for index in movable:
        if kind[index] in TILE_RES:
            by_res[TILE_RES[kind[index]]].append(pieces[index])
    for value in by_res.values():
        value.sort(key=lambda piece: origin0[piece.id])
    prods = sorted(
        (pieces[index] for index in movable if kind[index] in PRODUCERS),
        key=lambda piece: (PRODUCERS[piece.type], origin0[piece.id]),
    )
    field_ids = (
        {piece.id for piece in prods}
        | {piece.id for value in by_res.values() for piece in value}
        | set(mines)
    )
    house_ids = set(scorer.targets.get("housing", ()))
    stat_ids = set(scorer.targets.get("stat", ()))
    hh_types = _harvest_houses()
    veteran = next((pieces[index] for index in movable if kind[index] == VETERAN), None)
    captain = next((pieces[index] for index in movable if kind[index] == CAPTAIN), None)
    brick = next((pieces[index] for index in movable if kind[index] == "kBrickHouse"), None)
    builds = [
        pieces[index]
        for index in movable
        if index not in field_ids
        and kind[index] != "kBrickHouse"
        and (kind[index] in STATUE_TYPES or pieces[index].unfinished or index in hit)
    ]
    build_ids = {piece.id for piece in builds}
    # 오두막 둘레에 넣는 순서: 발사대 쪽 면 (강철 요새 → 유령의 집·수도원 → 채집 거처) → 나머지 큰 것부터
    homes = [
        pieces[index]
        for index in movable
        if index in house_ids
        and index not in field_ids
        and index not in build_ids
        and (veteran is None or index != veteran.id)
    ]
    front = [piece for piece in homes if piece.type in FRONT_TYPES] + [
        piece for piece in homes if piece.type in hh_types
    ]
    front.sort(
        key=lambda piece: (
            piece.type != "kBrickHouse",
            piece.type not in FRONT_TYPES,
            -len(piece.rel),
            piece.type,
            piece.id,
        )
    )
    back = sorted(
        (piece for piece in homes if piece not in front),
        key=lambda piece: (-len(piece.rel), piece.type, piece.id),
    )
    stats = sorted(
        (
            pieces[index]
            for index in movable
            if index in stat_ids
            and index not in house_ids
            and index not in field_ids
            and index not in build_ids
            and (captain is None or index != captain.id)
        ),
        key=lambda piece: (-len(piece.rel), piece.type, piece.id),
    )
    mansion = next(
        (pieces[index] for index in movable if kind[index] == "kMansion" and index not in house_ids), None
    )
    placed_special = (
        {piece.id for piece in homes + stats + builds}
        | ({veteran.id} if veteran else set())
        | ({captain.id} if captain else set())
        | ({mansion.id} if mansion else set())
    )
    plain = [pieces[index] for index in movable if index not in field_ids and index not in placed_special]
    plain_sorts = [
        sorted(plain, key=lambda piece: (-piece.h, -piece.w, -len(piece.rel), piece.type, piece.id)),
        sorted(plain, key=lambda piece: (-len(piece.rel), -piece.h, -piece.w, piece.type, piece.id)),
    ]
    town_all = [pieces[index] for index in movable if index not in field_ids]
    town_area = sum(len(piece.rel) for piece in town_all)

    def build_items(reserve: bool) -> Tuple[List[Tuple[_Item, _Item]], List[list]]:
        """(유닛(빈칸 예약), 같은 유닛(예약 없음)) 목록과 남는 타일 묶음(같은 모양끼리)."""
        pool = {field_name: list(value) for field_name, value in by_res.items()}
        units = []
        for piece in prods:
            k = PRODUCERS[piece.type]
            it, rest = _unit(piece, pool[k], pad, reserve, size)
            tight, _ = _unit(piece, pool[k], pad, False, size)
            units.append((it, tight))
            pool[k] = rest
        blocks = []
        for k in (1, 2, 3):
            groups: Dict[tuple, list] = {}
            for tile in pool[k]:
                groups.setdefault((tile.w, tile.h, tile.rel), []).append(tile)
            blocks += sorted(groups.values(), key=lambda g: -len(g) * g[0].w * g[0].h)
        units.sort(key=lambda u: -u[0].w * u[0].h)
        return units, blocks

    lo_c, hi_c = min(columns), max(columns)
    lc = launcher_rc[0] if launcher_rc else (lo_c + hi_c) // 2
    width_all = hi_c - lo_c + 1
    wmin = max([piece.w for piece in town_all] + [1])
    widths = sorted(
        {min(width_all, max(wmin, -(-town_area // height))) for height in range(2, height + 1)} | {width_all}
    )
    towns = []  # (폭, 위치, 시작 열, 넣는 순서)
    for width in widths:
        for anchor in ("left", "center", "right"):
            c0 = (
                lo_c
                if anchor == "left"
                else hi_c - width + 1
                if anchor == "right"
                else min(max(lo_c, lc - width // 2), hi_c - width + 1)
            )
            for k in range(len(plain_sorts)):
                towns.append((width, anchor, c0, k))
    items_by_reserve = {row: build_items(row) for row in (True, False)}
    all_cells = sorted(tiles)
    prod_orders = {
        "left": sorted(all_cells, key=lambda t: (depth(t[1]), t[0])),
        "center": sorted(all_cells, key=lambda t: (depth(t[1]), abs(t[0] - lc), t[0])),
        "right": sorted(all_cells, key=lambda t: (depth(t[1]), -t[0])),
    }
    far_first = sorted(all_cells, key=lambda t: (-depth(t[1]), t[0]))
    rng_of = lambda piece: (piece.range + pad) / size  # noqa: E731 — 범위 (타일 단위, 사각형)

    best = None
    seen = set()
    for width, anchor, c0, k in towns:
        if (c0, width, k) in seen:
            continue
        seen.add((c0, width, k))
        free_t = set(base_free)
        out_t: Dict[int, Cell] = {}
        shape_t: Dict[int, tuple] = {}
        town_cells0: Set[Cell] = set()
        late: List = []
        strip = [tile for tile in all_cells if c0 <= tile[0] < c0 + width]
        town_order = sorted(strip, key=lambda t: (-depth(t[1]), t[0] if anchor != "right" else -t[0]))
        rows_town = max(1, -(-town_area // max(1, width)))
        edge = max(0, height - rows_town)  # 마을 띠의 발사대 쪽 경계 (depth)

        def put(piece, other_piece, at, r):
            _put(_single(other_piece), at, free_t, out_t)
            shape_t[piece.id] = (other_piece, r)
            town_cells0.update(_cells(other_piece, at))
            return other_piece, at

        def put_first(piece, order):
            other_piece, at, rotation = _fit(piece, order, free_t)
            if at is None:
                late.append(piece)
                return None
            return put(piece, other_piece, at, rotation)

        def put_hub(piece, hub_piece, hub_at, near_side):
            other_piece, at, rotation = _hub_spot(
                piece, free_t, _center(hub_piece, hub_at), rng_of(hub_piece), depth, near_side
            )
            if at is None:
                return put_first(
                    piece, town_order
                )  # 범위 안에 자리가 없으면 마을 아무 데나 (달성도에서 빠짐)
            return put(piece, other_piece, at, rotation)

        # 1) 잔병의 오두막: 마을 띠 발사대 쪽 경계에서 범위만큼 안쪽 (둘레 거처가 경계까지 닿게)
        vet_at = None
        if veteran is not None:
            target = (c0 + width / 2, row_at(min(height - 1, edge + rng_of(veteran))))
            got = put_first(veteran, _order_near(strip, veteran, target, {}))
            if got:
                vq, vet_at = got
                for piece in front + back:
                    put_hub(piece, vq, vet_at, piece in front)
        else:
            for piece in front + back:
                put_first(piece, town_order)
        # 2) 대위 막사 + 능력치 건물: 마을 띠 먼 쪽 가운데
        if captain is not None:
            target = (c0 + width / 2, row_at(max(edge, height - 1 - rng_of(captain))))
            got = put_first(captain, _order_near(strip, captain, target, {}))
            for piece in stats:
                if got:
                    put_hub(piece, got[0], got[1], False)
                else:
                    put_first(piece, town_order)
        else:
            for piece in stats:
                put_first(piece, town_order)
        # 3) 공사 중·무한 강화 건물: 강철 요새 범위 안 발사대 쪽 (요새가 없으면 발사대 바로 앞 — 아래 들판 단계)
        brick_at = out_t.get(brick.id) if brick is not None else None
        pending_builds = []
        for piece in builds:
            if brick_at is not None:
                bq = shape_t[brick.id][0]
                other_piece, at, row = _hub_spot(
                    piece, free_t, _center(bq, brick_at), rng_of(brick), depth, True
                )
                if at is not None:
                    put(piece, other_piece, at, row)
                    continue
            pending_builds.append(piece)
        # 4) 나머지 마을 건물: 먼 쪽부터 빈틈없이 / 대저택은 건물이 가장 많이 드는 자리
        for piece in plain_sorts[k]:
            put_first(piece, town_order)
        if mansion is not None:
            tgt = [(*_center(shape_t[index][0], out_t[index]), 1.0) for index in out_t]
            other_piece, at, row = (
                _best_cover(
                    mansion, free_t, tgt, rng_of(mansion), {tile: n for n, tile in enumerate(town_order)}
                )
                if tgt
                else (mansion, None, 0)
            )
            if at is None:
                put_first(mansion, town_order)
            else:
                put(mansion, other_piece, at, row)
        for reserve in (True, False):
            for side, prod_order in prod_orders.items():
                free, result, town_cells, shape = set(free_t), dict(out_t), set(town_cells0), dict(shape_t)
                miss = 0

                def put_field(piece, order):
                    other_piece, at, rotation = _fit(piece, order, free)
                    if at is not None:
                        _put(_single(other_piece), at, free, result)
                        shape[piece.id] = (other_piece, rotation)
                        return True
                    return False

                # 5) 들판: (요새가 없거나 범위에 못 넣은) 공사 중 건물은 발사대 바로 앞 → 유닛 → 타일 블록 → 금광(뒤쪽)
                for piece in pending_builds:
                    miss += not put_field(piece, prod_orders["center"])
                units, blocks = items_by_reserve[reserve]
                for it, tight in units:
                    # 같은 자원의 채집·재생 거처 범위에 유닛 타일이 닿는 자리 먼저 — 타일을 전부 생산 건물에 주면
                    # 거처가 캘 게 없다. 유닛을 거처 옆에 붙이면 둘 다 같은 타일을 쓴다.
                    houses = _house_boxes(
                        homes, hh_types, shape, result, PRODUCERS[pieces[it.members[0][0]].type], pad, size
                    )
                    at = _place_unit(it, prod_order, free, houses, pieces)
                    if at is None:
                        it, at = tight, _place_unit(tight, prod_order, free, houses, pieces)
                    if at is not None:
                        _put(it, at, free, result)
                        continue
                    for index, _ in it.members:  # 유닛이 통째로 안 들어가면 하나씩
                        one = _single(pieces[index])
                        a1 = _place(one, prod_order, free)
                        if a1 is None:
                            break
                        _put(one, a1, free, result)
                # 채집·재생 거처(마을 앞면)의 범위부터 자기 자원 타일로 채운다 — 둘러싸이면 못 캠 (apo)
                blocks = _feed_houses(homes, hh_types, shape, result, blocks, free, pad, size)
                for group in blocks:
                    _place_block(group, prod_order, free, result)
                for m in mines:
                    put_field(pieces[m], prod_order[::-1])
                for piece in late:  # 마을 띠에 못 넣은 건물: 아무 데나
                    other_piece, at, row = _fit(piece, town_order + far_first, free)
                    if at is None:
                        break
                    _put(_single(other_piece), at, free, result)
                    shape[piece.id] = (other_piece, row)
                    town_cells |= _cells(other_piece, at)
                    miss += 1
                if len(result) < len(movable):
                    continue  # 못 놓은 건물이 있으면 쓸 수 없는 배치
                final = dict(origin0)
                final.update(result)
                shaped = dict(pieces)
                shaped.update({index: other_piece for index, (other_piece, _r) in shape.items()})
                guide = _guide_score(
                    shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size
                )
                eff = scorer.score(Layout(grid, shaped, final))[0]
                key = (-miss,) + guide[:3] + (eff + HH_W * guide[3] - WASTE_W * _waste(town_cells),)
                if best is None or key > best[0]:
                    best = (
                        key,
                        final,
                        (width, anchor, reserve, miss),
                        shaped,
                        {index: row for index, (_q, row) in shape.items() if row},
                    )
    if best is None:
        return dict(origin0), [tr("가이드 배치: 땅이 모자라 다시 짤 수 없음 — 지금 배치 유지")], {}
    shaped, turn = best[3], best[4]
    final = _polish_town(grid, shaped, best[1], {piece.id for piece in plain}, scorer)
    final = _polish_field(grid, shaped, final, scorer)
    # 지금 배치와 비교: 가이드 달성도가 나아지지 않고 범위 효과도 나아지지 않으면 옮기지 않는다
    cur_guide = _guide_score(
        pieces, origin0, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size
    )
    new_guide = _guide_score(
        shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size
    )
    cur_eff = scorer.score(Layout(grid, pieces, origin0))[0]
    new_eff = scorer.score(Layout(grid, shaped, final))[0]
    if (new_guide[:3], new_eff + HH_W * new_guide[3]) <= (
        cur_guide[:3],
        (cur_eff + HH_W * cur_guide[3]) * 1.02,
    ):
        lay0 = Layout(grid, pieces, origin0)
        notes = [tr("지금 배치가 가이드 기준으로 더 낫거나 같음 — 옮기지 않음")]
        notes += _guide_notes(
            lay0,
            pieces,
            origin0,
            captain,
            veteran,
            stats,
            list(house_ids),
            homes,
            by_res,
            brick,
            builds,
            pad,
            size,
        )
        return dict(origin0), notes, {}
    width, anchor, reserve, miss = best[2]
    lay = Layout(grid, shaped, final)
    notes = [
        tr(
            "가이드 배치: 발사대 쪽은 자원 들판(생산 건물 {v0}개가 자기 타일 한가운데), 먼 쪽은 거처·능력치 덩어리와 나머지 건물 {v1}개",
            v0=len(prods),
            v1=len(town_all),
        )
    ]
    notes += _guide_notes(
        lay, shaped, final, captain, veteran, stats, list(house_ids), homes, by_res, brick, builds, pad, size
    )
    if mines:
        notes.append(
            tr(
                "금광 {v0}개는 쓰지 않으니 뒤쪽에 모아 둠 (무한 모드로 골드 충분 — 철거해도 됨)",
                v0=len(mines),
            )
        )
    if reserve and prods:
        notes.append(tr("유닛 둘레 빈칸은 자원 타일을 사서 채울 자리로 비워 둠"))
    if miss > 0:
        notes.append(tr("땅이 모자라 {v0}개는 패턴 밖에 놓음", v0=miss))
    if turn:
        notes.append(tr("{v0}개는 회전해서 놓아야 빈틈 없이 맞물림 (ㄱ·ㅜ 자 모양 포함)", v0=len(turn)))
    return final, notes, turn


def _house_boxes(
    homes, hh_types, shape, result, res: int, pad: float, size: float
) -> List[Tuple[float, float, float]]:
    """자원 res 를 캐는 채집·재생 거처의 (중심 x, y, 범위) — 타일 단위."""
    boxes = []
    for piece in homes:
        if piece.type in hh_types and piece.id in result and _effect_kind(piece.type) == res:
            other_piece = shape.get(piece.id, (piece,))[0]
            boxes.append(_center(other_piece, result[piece.id]) + ((piece.range + pad) / size,))
    return boxes


def _place_unit(it: _Item, order: Sequence[Cell], free: Set[Cell], houses, pieces) -> Optional[Cell]:
    """유닛 자리: 타일이 거처 범위에 닿는 거처 수가 많은 자리 > 발사대 쪽 순서."""
    tcent = [(x + pieces[index].w / 2, y + pieces[index].h / 2) for index, (x, y) in it.members[1:]]
    best = None
    for step_index, (c, row) in enumerate(order):
        if not all((c + delta_x, row + delta_y) in free for delta_x, delta_y in it.cells):
            continue
        n = sum(
            1
            for hx, hy, rng in houses
            if any(abs(c + tx - hx) <= rng + 1e-6 and abs(row + ty - hy) <= rng + 1e-6 for tx, ty in tcent)
        )
        if best is None or n > best[0]:
            best = (n, (c, row))
        if n == len(houses):
            break  # 더 나아질 수 없음 — 앞 순서가 이김
    return best[1] if best else None


def _feed_houses(
    homes, hh_types, shape, result, blocks, free: Set[Cell], pad: float, size: float
) -> List[list]:
    """채집·재생 거처 범위 안 빈칸에 그 거처의 자원 타일(용량 큰 것부터)을 하나씩 놓는다. 남은 타일 묶음을 돌려준다."""
    left = [list(group) for group in blocks]
    for piece in homes:
        if piece.type not in hh_types or piece.id not in result:
            continue
        resource_kind = _effect_kind(piece.type)
        other_piece = shape.get(piece.id, (piece,))[0]
        hx, hy = _center(other_piece, result[piece.id])
        rng = (piece.range + pad) / size
        for group in left:
            if not group or TILE_KIND.get(group[0].type) != resource_kind:
                continue
            group.sort(key=lambda t: -t.cap)
            while group:
                tile = group[0]
                spots = sorted(
                    (
                        (abs(c + tile.w / 2 - hx) + abs(row + tile.h / 2 - hy), c, row)
                        for c in range(int(hx - rng - tile.w) - 1, int(hx + rng) + 2)
                        for row in range(int(hy - rng - tile.h) - 1, int(hy + rng) + 2)
                        if abs(c + tile.w / 2 - hx) <= rng + 1e-6
                        and abs(row + tile.h / 2 - hy) <= rng + 1e-6
                        and all((c + delta_x, row + delta_y) in free for delta_x, delta_y in tile.rel)
                    )
                )
                if not spots:
                    break
                _, c, row = spots[0]
                _put(_single(tile), (c, row), free, result)
                group.pop(0)
    return [group for group in left if group]


def _guide_score(
    shaped, final, veteran, captain, brick, homes, stats, builds, by_res, hh_types, pad, size
) -> Tuple[int, int, int, int]:
    """가이드 달성도 (클수록 좋음): (오두막 범위 안 거처 수, 대위 막사 범위 안 능력치 건물 수,
    강철 요새 범위 안 공사 중·무한 강화 건물 수, 자기 자원 타일을 범위에 둔 채집·재생 거처 수)."""

    from .layout_opt import target_in_range

    def contains(source, target):
        source_x, source_y = _center(shaped[source.id], final[source.id])
        target_x, target_y = _center(shaped[target.id], final[target.id])
        return target_in_range((target_x - source_x) * size, (target_y - source_y) * size,
                               source.range + pad, shaped[target.id], pad)

    def cov(src, ids) -> int:
        if src is None or src.id not in final:
            return 0
        return sum(
            1
            for index in ids
            if index in final
            and index != src.id
            and contains(src, shaped[index])
        )

    hh_ok = 0
    for piece in homes:
        if piece.type not in hh_types or piece.id not in final:
            continue
        resource_kind = _effect_kind(piece.type)
        if any(
            tile.id in final
            and contains(piece, tile)
            for tile in by_res.get(resource_kind, ())
        ):
            hh_ok += 1
    return (
        cov(veteran, [piece.id for piece in homes]),
        cov(captain, [piece.id for piece in stats]),
        cov(brick, [piece.id for piece in builds]),
        hh_ok,
    )


def _effect_kind(t: str) -> Optional[int]:
    from .layout_opt import EFFECTS

    effect = EFFECTS.get(t)
    return effect[0] if effect and isinstance(effect[0], int) else None


def _guide_notes(
    lay, shaped, final, captain, veteran, stats, house_ids, homes, by_res, brick, builds, pad, size
) -> List[str]:
    """가이드 규칙이 얼마나 지켜졌는지 한 줄씩 (배치도 창 메모)."""
    value, coverage_counts, before_counts, half_height = _guide_score(
        shaped, final, veteran, captain, brick, homes, stats, builds, by_res, _harvest_houses(), pad, size
    )
    result = []
    if captain is not None and stats:
        result.append(
            tr(
                "대위 막사 범위 안 능력치 건물 {v0}/{v1}개 (가이드: 전부 — 지략가 레벨이 오르면 범위가 넓어짐)",
                v0=coverage_counts,
                v1=len(stats),
            )
        )
    if veteran is not None and homes:
        result.append(tr("잔병의 오두막 범위 안 거처 {v0}/{v1}개 (가이드: 전부)", v0=value, v1=len(homes)))
    if brick is not None and builds:
        result.append(
            tr(
                "강철 요새 범위 안 무한 강화·공사 중 건물 {v0}/{v1}개 (발사대 쪽에 둬서 공이 닿게)",
                v0=before_counts,
                v1=len(builds),
            )
        )
    n_hh = sum(1 for piece in homes if piece.type in _harvest_houses())
    if n_hh:
        result.append(
            tr(
                "채집·재생 거처 {v0}/{v1}개가 자기 자원 타일을 범위에 둠 (둘러싸이면 못 캠 — apo 가이드)",
                v0=half_height,
                v1=n_hh,
            )
        )
    return result


def guide_report(grid: Grid, pieces: dict, origin: Dict[int, Cell], scorer, pad: float = 0.0) -> List[str]:
    """아무 배치나 가이드 규칙 달성도를 메모로 (가이드 배치 담금질 결과용 — plan_city 와 같은 분류)."""
    from .layout_opt import TILE_RES, Layout
    from .layout_guide import hub_groups

    kind = {index: piece.type for index, piece in pieces.items()}
    by_res: Dict[int, list] = {1: [], 2: [], 3: []}
    for index, tile in kind.items():
        if tile in TILE_RES:
            by_res[TILE_RES[tile]].append(pieces[index])
    pick = lambda t: next((pieces[index] for index in pieces if kind[index] == t), None)
    veteran, captain, brick = pick(VETERAN), pick(CAPTAIN), pick("kBrickHouse")
    groups = {height.type: [pieces[index] for index in ids] for height, ids in hub_groups(pieces, scorer)}
    builds = groups.get("kBrickHouse", [])
    house_ids = set(scorer.targets.get("housing", ()))
    homes = groups.get(VETERAN, [pieces[index] for index in house_ids])
    stats = groups.get(CAPTAIN, [])
    return _guide_notes(
        Layout(grid, pieces, origin),
        pieces,
        origin,
        captain,
        veteran,
        stats,
        list(house_ids),
        homes,
        by_res,
        brick,
        builds,
        pad,
        grid.size,
    )


def _cells(piece, origins: Cell) -> Set[Cell]:
    return {(origins[0] + delta_x, origins[1] + delta_y) for delta_x, delta_y in piece.rel}


def _waste(cells: Set[Cell]) -> int:
    """마을 테두리 사각형 안의 빈칸 수 (적을수록 빽빽)."""
    if not cells:
        return 0
    cs = [c for c, _ in cells]
    rs = [row for _, row in cells]
    return (max(cs) - min(cs) + 1) * (max(rs) - min(rs) + 1) - len(cells)


def _polish_town(grid: Grid, pieces: dict, final: Dict[int, Cell], town: Set[int], scorer) -> Dict[int, Cell]:
    """마을 안에서 모양(차지하는 칸)이 똑같은 건물끼리만 맞바꿔 범위 효과를 올린다 (더 나아지지 않을 때까지).
    모양이 같으니 빈틈·줄은 그대로다."""
    from .layout_opt import Layout

    groups: Dict[tuple, List[int]] = {}
    for index in town:
        piece = pieces[index]
        groups.setdefault((piece.w, piece.h, piece.rel), []).append(index)
    pairs = [
        (first_id, second_id)
        for group in groups.values()
        for step_index, first_id in enumerate(group)
        for second_id in group[step_index + 1 :]
    ]
    if not pairs:
        return final
    current = dict(final)
    best = scorer.score(Layout(grid, pieces, current))[0]
    for _ in range(20):
        improved = False
        for first_id, second_id in pairs:
            current[first_id], current[second_id] = current[second_id], current[first_id]
            value = scorer.score(Layout(grid, pieces, current))[0]
            if value > best + 1e-9:
                best, improved = value, True
            else:
                current[first_id], current[second_id] = current[second_id], current[first_id]
        if not improved:
            break
    return current


def _touching(piece, origins: Cell, occupied_cells: Dict[Cell, int], me: int) -> int:
    """o 에 놓은 p 가 다른 건물·타일과 맞닿는 변의 수 — 외딴 빈칸에 한 칸씩 흩어지지 않게 (정돈)."""
    cells = _cells(piece, origins)
    return sum(
        1
        for x, y in cells
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if n not in cells and occupied_cells.get(n, me) != me
    )


def _polish_field(
    grid: Grid, pieces: dict, final: Dict[int, Cell], scorer, seconds: float = 2.5
) -> Dict[int, Cell]:
    """자원 타일만 옮겨 범위 효과를 올린다 (다른 종류 타일과 맞바꾸기 · 이웃과 두 변 이상 맞닿는 빈칸으로 옮기기).
    거처·능력치 덩어리와
    생산 건물은 그대로라 가이드 달성도는 바뀌지 않는다 — 채집·재생 거처와 생산 건물이 캘 타일을 나눠 갖는 것을
    범위 효과 점수(layout_opt.EFFECTS 의 무게)로 정한다. 시간 상한 안에서 더 나아지지 않을 때까지."""
    import time
    from .layout_opt import TILE_RES, Layout

    tile_ids = sorted(
        index
        for index, piece in pieces.items()
        if piece.type in TILE_RES and piece.movable and index in final
    )
    if not tile_ids:
        return final
    current = dict(final)
    occupied_cells: Dict[Cell, int] = {}
    for index, origins in current.items():
        for c in _cells(pieces[index], origins):
            occupied_cells[c] = index

    def value() -> float:
        # 범위 효과 + 정돈 (타일이 이웃과 맞닿은 변마다 조금) — 효과가 같으면 흩어진 타일이 덩어리 쪽으로 모인다
        occ_now = {
            c: other_index
            for other_index, origins in current.items()
            for c in _cells(pieces[other_index], origins)
        }
        tidy = sum(
            _touching(pieces[other_index], current[other_index], occ_now, other_index)
            for other_index in tile_ids
        )
        return scorer.score(Layout(grid, pieces, current))[0] + TIDY_W * tidy

    best = value()
    start = time.perf_counter()
    improved = True
    while improved and time.perf_counter() - start < seconds:
        improved = False
        for index in tile_ids:
            if time.perf_counter() - start >= seconds:
                break
            piece = pieces[index]
            mine = _cells(piece, current[index])
            cands = [
                ("swap", other_index)
                for other_index in tile_ids
                if other_index != index
                and pieces[other_index].type != piece.type
                and (pieces[other_index].w, pieces[other_index].h, pieces[other_index].rel)
                == (piece.w, piece.h, piece.rel)
            ]
            cands += [
                ("move", (c, row))
                for c, row in grid.tiles
                if (c, row) != current[index]
                and all(
                    (c + delta_x, row + delta_y) in grid.tiles
                    and occupied_cells.get((c + delta_x, row + delta_y), index) == index
                    for delta_x, delta_y in piece.rel
                )
                and _touching(piece, (c, row), occupied_cells, index) >= 2
            ]
            choice = None
            for how, x in cands:
                if how == "swap":
                    current[index], current[x] = current[x], current[index]
                else:
                    old = current[index]
                    current[index] = x
                current_value = value()
                if current_value > best + 1e-9:
                    best, choice = current_value, (how, x)
                if how == "swap":
                    current[index], current[x] = current[x], current[index]
                else:
                    current[index] = old
            if choice is None:
                continue
            how, x = choice
            if how == "swap":
                current[index], current[x] = current[x], current[index]
                for c in _cells(pieces[index], current[index]):
                    occupied_cells[c] = index
                for c in _cells(pieces[x], current[x]):
                    occupied_cells[c] = x
            else:
                for c in mine:
                    occupied_cells.pop(c, None)
                current[index] = x
                for c in _cells(piece, x):
                    occupied_cells[c] = index
            improved = True
    return current
