"""공사 건물을 실제 발사 위치 쪽으로 당기는 적은 이동 후보.

거리 우선순위는 사용자의 2026-09-28 배치 요청이다. 게임 효과나 채집량을 뜻하지 않는다.
입구 보호 칸·기존 효과를 지키고, 최종 후보의 발사·도달 검사는 layout_opt에서 별도로 한다.
"""

import math
import time


def distances(grid, pieces, origins, launcher, turns=None):
    from .layout_opt import rotated

    if not isinstance(launcher, (list, tuple)) or len(launcher) < 2:
        return {}
    if not all(type(value) in (int, float) and math.isfinite(value) for value in launcher[:2]):
        return {}
    turns = turns or {}
    result = {}
    for building_id, piece in pieces.items():
        if not piece.unfinished or building_id not in origins:
            continue
        shaped = rotated(piece, turns.get(building_id, 0))
        column, row = origins[building_id]
        result[building_id] = min(
            math.hypot(grid.ox + (column + dx + .5) * grid.size - launcher[0],
                       grid.oy + (row + dy + .5) * grid.size - launcher[1]) / grid.size
            for dx, dy in shaped.rel
        )
    return result


def priority(grid, pieces, origins, launcher, turns=None):
    """가장 먼 공사 건물부터 가까워지게 비교한다. 한 칸 단위로 묶어 미세한 차이로 반복 이동을 줄인다."""
    return tuple(-math.floor(distance + 1e-6) for distance in
                 sorted(distances(grid, pieces, origins, launcher, turns).values(), reverse=True))


def base_distances(base):
    from .layout import grid_from_geo
    from .layout_opt import housing_types, pieces_from_base

    geometry = base.get("geo") or {}
    grid = grid_from_geo(geometry)
    if grid is None or geometry.get("launcher_source") == "unavailable":
        return {}
    pieces, origins = pieces_from_base(base, grid, housing_types())
    return distances(grid, pieces, origins, geometry.get("launcher"))


def base_priority(base):
    return tuple(-math.floor(distance + 1e-6) for distance in sorted(base_distances(base).values(), reverse=True))


def candidates(grid, pieces, original, launcher, entrance, valid, deadline):
    """빈 자리 이동/같은 크기 구역 교환을 비교한다. 공사·고정 건물을 뒤로 밀어 자리를 만들지 않는다."""
    from .layout_opt import Layout, rotated

    before = distances(grid, pieces, original, launcher)
    if not before:
        return []
    current, turns, results = dict(original), {}, []
    # 후보 수는 물리 검증 비용을 제한하는 탐색 예산이다. 공사 우선순위의 가중치가 아니다.
    for building_id in sorted(before, key=lambda index: (-before[index], -len(pieces[index].rel), index)):
        if not pieces[building_id].movable or time.perf_counter() >= deadline:
            continue
        shaped = {index: rotated(piece, turns.get(index, 0)) for index, piece in pieces.items()}
        layout = Layout(grid, shaped, current)
        current_band = math.floor(distances(grid, pieces, current, launcher, turns)[building_id] + 1e-6)
        trials, seen_shapes = [], set()
        for rotation in range(4):
            piece = rotated(pieces[building_id], rotation)
            signature = (piece.w, piece.h, piece.rel, piece.range_boxes)
            if signature in seen_shapes:
                continue
            seen_shapes.add(signature)
            for origin in sorted(grid.tiles):
                if time.perf_counter() >= deadline:
                    break
                cells = {(origin[0] + dx, origin[1] + dy) for dx, dy in piece.rel}
                if not cells <= grid.tiles or cells & entrance:
                    continue
                distance = min(math.hypot(grid.ox + (column + .5) * grid.size - launcher[0],
                                          grid.oy + (row + .5) * grid.size - launcher[1]) / grid.size
                               for column, row in cells)
                band = math.floor(distance + 1e-6)
                if band >= current_band:
                    continue
                blockers = {layout.occ[cell] for cell in cells if cell in layout.occ} - {building_id}
                if any(not shaped[index].movable or shaped[index].unfinished for index in blockers):
                    continue
                if blockers and rotation != turns.get(building_id, 0):
                    continue  # 다른 건물 교환과 회전을 한 번에 하지 않고, 빈 자리에서만 돌린다.
                trials.append(((band, len(blockers), distance, rotation, origin), origin, rotation, bool(blockers)))
        trials.sort()
        accepted = []
        for _, origin, rotation, occupied in trials:
            if time.perf_counter() >= deadline:
                break
            proposal, proposal_turns = dict(current), dict(turns)
            if occupied:
                piece = shaped[building_id]
                undo = layout.swap_regions(current[building_id], origin, piece.w, piece.h)
                if undo is None:
                    continue
                proposal = dict(layout.origin)
                layout.apply(undo)
                if any(index != building_id and pieces[index].unfinished for index, _ in undo):
                    continue
            else:
                proposal[building_id] = origin
                proposal_turns[building_id] = rotation
            proposal_turns = {index: rotation for index, rotation in proposal_turns.items() if rotation}
            candidate_pieces = {index: rotated(piece, proposal_turns.get(index, 0)) for index, piece in pieces.items()}
            candidate = Layout(grid, candidate_pieces, proposal)
            if set(candidate.occ) & entrance or not valid(proposal, proposal_turns):
                continue
            accepted.append((proposal, proposal_turns))
            if len(accepted) == 2:
                break
        if accepted:
            current, turns = accepted[0]
            if len(results) < 4:
                results.extend(accepted[:4 - len(results)])
    if current != original and not any(origins == current and rotations == turns for origins, rotations in results):
        results.append((current, turns))
    return results
