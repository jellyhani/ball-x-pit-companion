"""게임의 발사 금지 조건을 이용한 배치 후보 검사와 입구 복구.

1.301 BallPreview.SetAimDirWorker(0x4B6120): 첫 Raycast 접점 y < -0.5,
normal.y < 0, abs(normal.x) <= 0.1이면 금지. GetRCHit(0x4B5C10)은
채집 관통 강화와 무관하게 첫 충돌을 읽는다. 실제 화면의 허용 여부는 브리지 값이 우선이다.
"""

import math
from . import harvest_sim as hs
from . import layout_opt as lo
from .layout import (
    buildings_from_base,
    grid_from_geo,
    shape_masks,
    building_cells,
    occupied,
    free_spots,
    moved_base,
    Move,
)
from .layout_guide import preserves_guide
from ..i18n import tr


def launchable(world, angle):
    x, y = world.launcher
    delta_x, delta_y = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    hits = [hit for s in world.shapes if (hit := hs._hit_shape(s, x, y, delta_x, delta_y, 0.0)) is not None]
    if not hits:
        return True
    distance, normal_x, normal_y = min(hits, key=lambda h: h[0])
    length = math.hypot(normal_x, normal_y)
    return not (
        y + delta_y * distance < -0.5
        and normal_y < 0
        and length > 0
        and abs(normal_x) / length <= 0.1000000015
    )


def allowed_angles(geo, angles):
    world = hs.world_from_geo(geo, 0.0)
    return (
        list(angles)
        if not isinstance(world, hs.World)
        else [angle for angle in angles if launchable(world, angle)]
    )


def modeled_current_launch(base):
    """구형 브리지용 대체 판정. 최신 조준 위치·방향·충돌 자료가 충분할 때만 반환한다."""
    if base.get("state") != "kAimWorkers":
        return None
    geometry = base.get("geo") or {}
    player = base.get("player") or []
    point = geometry.get("launcher")
    try:
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or len(player) < 4
            or not geometry.get("colliders")
            or "player_y" not in geometry
        ):
            return None
        values = [*point, player[2], player[3], geometry["player_y"], geometry["left"], geometry["right"]]
        if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
            return None
        if (
            not geometry["left"] <= point[0] <= geometry["right"]
            or abs(point[1] - geometry["player_y"]) > 0.002
            or player[3] <= 0
            or math.hypot(player[2], player[3]) < 1e-6
        ):
            return None
        world = hs.world_from_geo(geometry, 0.0)
        if world is None:
            return None
        return launchable(world, math.degrees(math.atan2(player[3], player[2])))
    except (TypeError, ValueError, KeyError):
        return None


def entrance_blockers(base):
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return set()
    buildings = buildings_from_base(base)
    masks = shape_masks(base.get("geo") or {}, buildings, grid)
    cells = lo.entrance_cells(base.get("geo") or {}, grid)
    return {
        index
        for index, building in buildings.items()
        if building_cells(building, grid, masks.get(index)) & cells
    }


def repair_entrance(base, pad=0.0, accept=None):
    """입구 정리는 점수 개선 문턱과 별개다. 가까운 빈자리 중 기존 범위 효과를 가장 많이 보존한다."""
    grid = grid_from_geo(base.get("geo") or {})
    if grid is None:
        return [], base
    entrance = lo.entrance_cells(base.get("geo") or {}, grid)
    current, moves = base, []
    for building_id in sorted(entrance_blockers(base)):
        buildings = buildings_from_base(current)
        building = buildings[building_id]
        raw = next(radius for radius in current["buildings"] if radius["id"] == building_id)
        if building.type in lo.FIXED_TYPES or raw.get("state") in hs.CONSTRUCTION_STATES:
            continue
        masks = shape_masks(current.get("geo") or {}, buildings, grid)
        occupied_cells = occupied(buildings, grid, skip=[building_id], masks=masks)
        spots = free_spots(grid, occupied_cells | entrance, *building.footprint)
        spots.sort(key=lambda piece: (piece[0] - building.x) ** 2 + (piece[1] - building.y) ** 2)
        candidates = []
        for position in spots[:32]:
            candidate = moved_base(current, buildings, building_id, position)
            if not lo.preserves_production(base, candidate, pad) or not preserves_guide(base, candidate, pad):
                continue
            pieces, origin = lo.pieces_from_base(candidate, grid, lo.housing_types())
            scorer = lo.Scorer(pieces, lo._stat_types(candidate), lo.housing_types(), pad=pad)
            score = scorer.score(lo.Layout(grid, pieces, origin))[0]
            candidates.append((score, position, candidate))
        candidates.sort(key=lambda row: -row[0])
        for _, position, candidate in candidates:
            if accept is not None and not accept(candidate):
                continue
            moves.append(Move(building_id, position, 0.0, tr("발사 입구를 비우기 위해 이동")))
            current = candidate
            break
    return moves, current
