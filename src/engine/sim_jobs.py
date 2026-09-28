"""별도 프로세스에서 돌리는 계산 작업 (채집 궤적·배치). 화면 스레드와 파이썬 GIL 을 나눠 쓰지 않게 한다.

실제 측정: 지금 조준 궤적 계산 40ms(작업자 8명, 경계 상자 빠른 제외 뒤 15ms), 각도 탐색 0.7~1.5초,
배치 계산 수 초 — 같은 프로세스의 스레드에서 돌리면 화면 갱신이 끊겼다(사용자 체감 렉).
인자와 결과는 모두 피클 가능한 기본 자료형이다.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from . import harvest_sim as hs
from . import native
from .aim_preview import worker_preview
from .launch_access import allowed_angles, entrance_blockers, repair_entrance
from ..i18n import tr


def job_now(
    geo: dict,
    buildings: Dict[int, dict],
    team: Sequence[dict],
    angle: float,
    dur: float,
    targets: Optional[Dict[int, int]] = None,
) -> dict:
    """지금 조준의 예상: 팀 채집량, 첫 작업자의 초기 경로(월드 좌표), 미완성 건물 타격."""
    world = hs.world_from_geo(geo, 0.0)
    if world is None:
        return {"error": "geometry_unavailable", "model_limitations": ["geometry_unavailable"]}
    counts: Dict[int, int] = {}
    points: Dict[int, int] = {}
    total, workers = hs.run_angle(world, buildings, team, angle, dur, counts, points)
    targets = targets or {}
    per = {
        building_id: min(counts.get(building_id, 0), capacity)
        for building_id, capacity in targets.items()
        if counts.get(building_id)
    }
    per_points = {
        building_id: min(points.get(building_id, 0), capacity)
        for building_id, capacity in targets.items()
        if points.get(building_id)
    }
    return {
        "angle": angle,
        "total": total,
        "path": worker_preview(workers),
        "automatic_gain": list(world.automatic_gain),
        "build_hits": sum(per.values()),
        "per_building": per,
        "build_points": sum(per_points.values()),
        "per_building_points": per_points,
        "model_limitations": sorted(set(hs.model_limitations(team, buildings)) | world.model_notes),
    }


def job_sweep(
    geo: dict,
    buildings: Dict[int, dict],
    team: Sequence[dict],
    dur: float,
    need: int,
    targets: Optional[Dict[int, int]] = None,
    lo: float = 12.0,
    hi: float = 168.0,
) -> dict:
    """각도 탐색 결과 상위 5개(top, 1위는 첫 작업자의 초기 경로 포함)와 미완성 건물별 최대 타격 수(reach — 0 이면
    어떤 각도로도 닿지 않음)."""
    world = hs.world_from_geo(geo, 0.0)
    if world is None:
        return {"error": "geometry_unavailable", "model_limitations": ["geometry_unavailable"]}
    limits = (lo, hi)
    if native.lib() is not None and not hs.needs_dynamic_simulation(buildings):
        # 네이티브 계산(각도 하나 약 1ms)이면 1° 간격 전부 — 좁은 틈으로만 닿는 각도도 놓치지 않는다
        ranked = hs.rank_angles(
            world, buildings, team, dur, need, targets, angles=allowed_angles(geo, _angle_grid(limits, 1))
        )
    else:
        # 파이썬 계산: 거칠게 6° 간격 → 상위 3개 주변만 1° 간격 (전부 1°로 하면 작업자 10명·20초에 수 초)
        coarse = hs.rank_angles(
            world, buildings, team, dur, need, targets, angles=allowed_angles(geo, _angle_grid(limits, 6))
        )
        fine = sorted(
            {
                a
                for angle_result in coarse[:3]
                for a in range(int(angle_result.angle) - 2, int(angle_result.angle) + 3)
                if lo <= a <= hi
            }
            - {angle_result.angle for angle_result in coarse}
        )
        ranked = coarse + hs.rank_angles(
            world, buildings, team, dur, need, targets, angles=allowed_angles(geo, fine)
        )
    ranked.sort(key=lambda r: -hs.angle_score(r, need))
    reach = {building_id: 0 for building_id in targets or {}}
    for angle_result in ranked:
        for building_id, value in angle_result.per_building.items():
            reach[building_id] = max(reach.get(building_id, 0), value)
    current_result = []
    picked = []
    for angle_result in ranked:  # 후보끼리 8° 이상 떨어진 것만 (1위 옆 1° 는 다른 선택이 아니다)
        if all(abs(angle_result.angle - q.angle) >= 8 for q in picked):
            picked.append(angle_result)
        if len(picked) == 5:
            break
    for angle_result in picked:
        # 남은 공사 점수까지만 반영한다. 충돌 횟수와 실제 건설 강화의 진행량을 혼동하지 않는다.
        score = hs.angle_score(angle_result, need)
        points: Dict[int, int] = {}
        _, workers = hs.run_angle(world, buildings, team, angle_result.angle, dur, build_points=points)
        current_result.append(
            {
                "angle": angle_result.angle,
                "total": angle_result.total,
                "build_hits": angle_result.build_hits,
                "per_building": angle_result.per_building,
                "build_points": getattr(angle_result, "build_points", None),
                "per_building_points": getattr(angle_result, "per_building_points", {}),
                "score": score,
                "path": worker_preview(workers),
            }
        )
    notes = set(hs.model_limitations(team, buildings))
    for result in ranked:
        notes.update(getattr(result, "model_notes", ()))
    return {"top": current_result, "reach": reach, "model_limitations": sorted(notes)}


def gold_mine_spot(
    base: dict, buildings: Dict[int, dict], team: Sequence[dict], dur: float, need: int, blueprint: dict
):
    """금광: 범위 효과가 아니라 작업자가 튕길 때마다 골드. 추천 각도로 쏜 작업자 경로가 가장 많이 지나는 빈 자리를
    고르고(경로 밀도), 상위 몇 곳은 금광 충돌 상자를 넣어 궤적을 다시 계산해 실제 튕김 수로 확인한다.
    돌려주는 값: (종류, 중심, 크기, 예상 튕김 수, 튕김 수, 0) 또는 None."""
    from . import layout_opt as lo
    from .layout import grid_from_geo

    geometry = base.get("geo") or {}
    grid = grid_from_geo(geometry)
    world = hs.world_from_geo(geometry, 0.0)
    if grid is None or world is None:
        return None
    width, height = blueprint.get("size") or (2, 2)
    pieces, origin = lo.pieces_from_base(base, grid, lo.housing_types())
    occ = lo.Layout(grid, pieces, origin).occ
    best_a = hs.best_angles(world, buildings, team, dur, need, angles=range(24, 157, 8))[0][0]
    _, workers = hs.run_angle(world, buildings, team, best_a, dur)
    heat: Dict[tuple, int] = {}
    for worker in workers:
        points = worker.path
        for (left_x, bottom_y, _t0), (right_x, top_y, _t1) in zip(points, points[1:]):
            n = max(1, int(math.hypot(right_x - left_x, top_y - bottom_y) / (grid.size / 2)))
            for step_index in range(n + 1):
                x, y = (
                    left_x + (right_x - left_x) * step_index / n,
                    bottom_y + (top_y - bottom_y) * step_index / n,
                )
                cell = (math.floor((x - grid.ox) / grid.size), math.floor((y - grid.oy) / grid.size))
                heat[cell] = heat.get(cell, 0) + 1
    spots = []
    for c, row in grid.tiles:
        cells = [(c + delta_x, row + delta_y) for delta_x in range(width) for delta_y in range(height)]
        if all(x in grid.tiles and x not in occ for x in cells):
            spots.append((sum(heat.get(x, 0) for x in cells), (c, row)))
    spots.sort(reverse=True)
    best = None
    for _, (c, row) in spots[:4]:
        center_x, center_y = grid.center(c, row, width, height)
        half_width, half_height = width * grid.size / 2, height * grid.size / 2
        g2 = dict(geometry)
        g2["colliders"] = list(geometry.get("colliders") or []) + [
            {
                "id": -7,
                "shape": "box",
                "pts": [
                    [center_x - half_width, center_y - half_height],
                    [center_x + half_width, center_y - half_height],
                    [center_x + half_width, center_y + half_height],
                    [center_x - half_width, center_y + half_height],
                ],
            }
        ]
        w2 = hs.world_from_geo(g2, 0.0)
        b2 = dict(buildings)
        b2[-7] = {"id": -7, "type": "kGoldMine", "res": 0}
        top = 0
        for a in range(24, 157, 8):
            counts: Dict[int, int] = {}
            hs.run_angle(w2, b2, team, a, dur, counts)
            top = max(top, counts.get(-7, 0))
        if best is None or top > best[0]:
            best = (top, (center_x, center_y))
    if not best or best[0] <= 0:
        return None
    return ("kGoldMine", best[1], (width, height), float(best[0]), best[0], 0)


def full_tiles(buildings: Dict[int, dict]) -> Dict[int, dict]:
    """자원 타일을 가득 찬 상태로 (배치는 오래 쓰는 것 — 오늘 이미 캐서 빈 타일로 비교하면 모든 배치가 0이 된다)."""
    from .layout_opt import TILE_RES

    result = {}
    for building_id, building in buildings.items():
        if building.get("type") not in TILE_RES:
            result[building_id] = building
            continue
        capacity = int(building.get("cap") or building.get("res") or 1)
        held = [0, 0, 0, 0]
        held[TILE_RES[building["type"]]] = capacity
        # res뿐 아니라 held도 같은 '가득 찬 상태'로 맞춘다. 자동 생산의 저장량 변화로 배치 결과가 버려지면 안 된다.
        result[building_id] = dict(
            building,
            res=capacity,
            held=held,
            can_harvest=True,
            raycast_enabled=TILE_RES[building["type"]] != 1,
            pickup_enabled=TILE_RES[building["type"]] == 1,
        )
        if "task_active" in building:
            result[building_id]["task_active"] = False
    return result


def _angle_grid(limits: Optional[Sequence[float]], step: int) -> List[float]:
    """실제 조준 한계 안의 정수 표본과 양 끝. 좁은 소수 각도 구간도 버리거나 범위 밖으로 반올림하지 않는다."""
    lower, upper = (25.0, 155.0) if limits is None else (float(limits[0]), float(limits[1]))
    if not (math.isfinite(lower) and math.isfinite(upper)) or lower > upper:
        return []
    lower, upper = max(0.0, lower), min(180.0, upper)
    if lower > upper:
        return []
    return sorted({lower, upper, *range(math.ceil(lower), math.floor(upper) + 1, max(1, step))})


def HV_ANGLES(aim_limits: Optional[Sequence[float]] = None):
    """배치 후보 비교에 쓰는 각도 (네이티브면 3°, 파이썬이면 10° 간격)."""
    return _angle_grid(aim_limits, 3 if native.lib() is not None else 10)


def REACH_ANGLES(aim_limits: Optional[Sequence[float]] = None):
    return _angle_grid(aim_limits, 3 if native.lib() is not None else 6)


def job_layout(
    snapshot: dict,
    team: Sequence[dict],
    dur: float,
    blueprints: Sequence[dict],
    targets: Optional[Dict[int, int]] = None,
    need: int = 1,
    seconds: float = 12.0,
    prefer: Optional[Dict[int, tuple]] = None,
    char_levels: Optional[Dict[str, int]] = None,
    resources: Optional[Sequence[int]] = None,
    aim_limits: Optional[Sequence[float]] = None,
):
    """가이드 배치(지금 배치에서 출발하는 담금질 + 가이드 허브 규칙, preset "guide") → 닿지 않는 미완성 건물로 길 열기 → 새 건물 자리. 자원별 추천 각도.
    seconds: 탐색 시간 상한. prefer: 이전 목표 배치(건물별 중심) — 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다.

    돌려주는 값: (LayoutPlan, 자원별 각도 순위). LayoutPlan.swaps 는 게임에서 할 옮기기 순서(잠시 비켜 두기 포함),
    LayoutPlan.final 은 건물별 목표 중심(월드 좌표)."""
    from . import layout_opt as lo
    from .layout import (
        LayoutPlan,
        Move,
        buildings_from_base,
        grid_from_geo,
        plan_access,
        shape_masks,
        suggest_new,
    )
    from .resource_access import ResourceAccess

    if any(
        "range_boxes" in building and building["range_boxes"] is None
        for building in snapshot.get("buildings", [])
    ) or (snapshot.get("range_contract") or {}).get("mismatches"):
        # 새 브리지의 명시적인 읽기 실패를 구형 중심 근사로 덮지 않는다.
        plan = LayoutPlan(
            0.0,
            0.0,
            calculation_deferred=True,
            construction_pending=True,
            model_limitations=["missing_range_geometry"],
            final={
                building["id"]: (building["x"], building["y"])
                for building in snapshot.get("buildings", [])
                if all(k in building for k in ("id", "x", "y"))
            },
        )
        return plan, {}
    targets = targets or {}
    buildings = full_tiles(
        {building["id"]: building for building in snapshot.get("buildings") or [] if "id" in building}
    )
    limitations = hs.model_limitations(team, buildings)
    res_weight = {angle_result: (1.5 if angle_result == need else 1.0) for angle_result in (1, 2, 3)}

    lo.set_char_levels(char_levels or {})

    def hv(geo):
        """시간순 시뮬레이션의 작업자 수확량. 건물 효과를 결과에 재차 곱하지 않는다."""
        world = hs.world_from_geo(geo, 0.0)
        if not world:
            return [0, 0, 0, 0]
        angles = allowed_angles(geo, HV_ANGLES(aim_limits))
        if not angles:
            return [0, 0, 0, 0]
        a, total = hs.best_angles(world, buildings, team, dur, need, angles=angles)[0]
        return total

    def reach(geo):
        if not targets:
            return {}
        world = hs.world_from_geo(geo, 0.0)
        result: Dict[int, int] = {}
        if world:
            for angle_result in hs.rank_angles(
                world,
                buildings,
                team,
                dur,
                need,
                targets,
                angles=allowed_angles(geo, REACH_ANGLES(aim_limits)),
            ):
                for building_id, value in angle_result.per_building.items():
                    result[building_id] = max(result.get(building_id, 0), value)
        return result

    calib = lo.calibrate_range(snapshot)  # 게임이 직접 센 범위 안 타일 수와 맞춤 (플러그인 1.9)
    pad = calib[0] if calib[2] else 0.0
    resource_check = ResourceAccess(
        snapshot, team, dur, _angle_grid(aim_limits, 5 if native.lib() else 15), pad
    )
    resource_before = resource_check.check(snapshot)

    # 배치 추천은 가이드 배치 하나 (사용자 결정 2026-09-26: '적게 움직이고 효율 최대, 가이드대로').
    # 처음부터 다시 짜는 계획도시(layout_city.plan_city)는 이미 거의 된 기지에서도 80개를 옮기라고 해서(+2%),
    # 지금 배치에서 출발하는 담금질(옮기기 하나당 벌점)에 가이드 허브 규칙을 크게 쳐서 쓴다 (preset "guide").
    # 쳐야 지어지는 건물은 어떤 각도로든 공이 닿는 배치만 고른다 (사용자 스크린샷: 도박장이 마을 구석에 묻힘).
    def reach_all(nb: dict) -> bool:
        got = reach(nb.get("geo") or {})
        return all(got.get(t, 0) > 0 for t in targets) and resource_check.preserves(nb, resource_before)

    # 아직 게임과 맞추지 못한 수익 모형은 이동안을 고르는 효율 점수에 쓰지 않는다. 참고 수치는 아래에서 따로 계산한다.
    full = lo.optimize(
        snapshot,
        hv if team and not limitations else None,
        reach_all if team and (targets or resource_before is not None) else None,
        res_weight=res_weight,
        fixed=list(targets or {}),
        pad=pad,
        preset="guide",
        seconds=min(seconds, 8.0),
        prefer=prefer,
    )
    grid = grid_from_geo(snapshot.get("geo") or {})
    if full is None or grid is None:
        return None, {}
    plan = _plan_from(
        snapshot,
        full,
        grid,
        targets,
        team,
        reach,
        hv,
        blueprints,
        res_weight,
        pad,
        buildings,
        dur,
        need,
        calib,
        resources,
        resource_check,
        resource_before,
    )
    if (resource_before is not None and resource_before.blocked) or entrance_blockers(snapshot):
        # 자원을 먼저 옮긴 뒤에는 기존 최적화의 생산 건물 이동이 무의미해질 수 있다.
        # 현재 배치에서 접근만 복구한 안과 최종 상태끼리 비교해 불필요한 추가 이동을 막는다.
        pieces, origins = lo.pieces_from_base(snapshot, grid, lo.housing_types())
        centers = {
            building["id"]: (building["x"], building["y"]) for building in snapshot.get("buildings") or []
        }
        neutral = lo.FullPlan(origins, dict(origins), centers, 0.0, 0.0, {}, {})
        simple = _plan_from(
            snapshot,
            neutral,
            grid,
            targets,
            team,
            reach,
            hv,
            blueprints,
            res_weight,
            pad,
            buildings,
            dur,
            need,
            calib,
            resources,
            resource_check,
            resource_before,
        )
        reached = {index for index, n in plan.reach_after.items() if n > 0}
        safe = (
            simple.movement_complete
            and all(simple.reach_after.get(index, 0) > 0 for index in reached)
            and len(entrance_blockers(simple.evaluated_base)) <= len(entrance_blockers(plan.evaluated_base))
        )
        fewer_blocked = len(simple.resource_unreachable_after) < len(plan.resource_unreachable_after)
        clearer_entrance = len(entrance_blockers(simple.evaluated_base)) < len(
            entrance_blockers(plan.evaluated_base)
        )
        dominates = (
            len(simple.resource_unreachable_after) == len(plan.resource_unreachable_after)
            and simple.score_after >= plan.score_after - 1e-6
            and len(simple.swaps) <= len(plan.swaps)
        )
        if safe and (clearer_entrance or fewer_blocked or dominates):
            plan = simple
    plan.preset = "guide"
    plan.model_limitations = limitations
    source = (snapshot.get("geo") or {}).get("launcher_source")
    if source == "last_observed":
        plan.notes.append(tr("발사 위치는 같은 기지의 최근 실제 채집 기록을 사용했습니다."))
    elif source == "unavailable":
        plan.notes.append(tr("실제 채집 발사 위치를 아직 확인하지 못해 경로 검사를 보류했습니다."))
    final_state = plan.evaluated_base
    world = hs.world_from_geo(final_state.get("geo") or {}, 0.0)
    final_blds = full_tiles(
        {building["id"]: building for building in final_state.get("buildings") or [] if "id" in building}
    )
    sweeps = {}
    if world and team:
        for resource_kind in (1, 2, 3):
            sweeps[resource_kind] = [
                (angle_result.angle, angle_result.total)
                for angle_result in hs.rank_angles(
                    world,
                    final_blds,
                    team,
                    dur,
                    resource_kind,
                    None,
                    angles=allowed_angles(final_state.get("geo") or {}, _angle_grid(aim_limits, 5)),
                )
            ]
    return plan, sweeps


def _plan_from(
    snapshot,
    full,
    grid,
    targets,
    team,
    reach,
    hv,
    blueprints,
    res_weight,
    pad,
    buildings,
    dur,
    need,
    calib,
    resources=None,
    resource_check=None,
    resource_before=None,
):
    """최적화 결과(FullPlan) → 게임에서 할 옮기기 순서·새로 지을 것·강화 추천이 붙은 LayoutPlan."""
    from . import layout_opt as lo
    from .layout import LayoutPlan, Move, buildings_from_base, plan_access
    from .layout_guide import preserves_guide

    final_base = lo.final_base(snapshot, full)
    entrance = lo.entrance_cells(snapshot.get("geo") or {}, grid)
    original_blockers = entrance_blockers(snapshot)
    reached0 = {index for index, n in reach(final_base.get("geo") or {}).items() if n > 0}

    def entrance_safe(candidate):
        if resource_check is not None and not resource_check.preserves(candidate, resource_before):
            return False
        got = reach(candidate.get("geo") or {}) if reached0 else {}
        return all(got.get(index, 0) > 0 for index in reached0)

    entrance_moves, final_base = repair_entrance(final_base, pad, entrance_safe)
    access, final_base, reserved = (
        plan_access(
            final_base,
            list(targets),
            reach,
            avoid=entrance,
            accept_fn=lambda nb: (
                lo.preserves_production(snapshot, nb, pad)
                and preserves_guide(snapshot, nb, pad)
                and (resource_check is None or resource_check.preserves(nb, resource_before))
            ),
        )
        if targets and team
        else ([], final_base, set())
    )
    resource_moves = []
    if resource_check is not None and resource_before is not None:
        from .resource_access import repair_resources

        reached = {index for index, count in reach(final_base.get("geo") or {}).items() if count > 0}

        def safe_resource_move(candidate):
            if not reached:
                return True
            checked = reach(candidate.get("geo") or {})
            return all(checked.get(index, 0) > 0 for index in reached)

        resource_moves, final_base = repair_resources(final_base, resource_check, accept=safe_resource_move)
    all_access = entrance_moves + access + resource_moves
    pieces, current = lo.pieces_from_base(snapshot, grid, lo.housing_types())
    target_origin = dict(full.origin_after)
    for m in all_access:  # 공사·자원 접근 개선도 목표 자리에 반영
        current_pieces = lo.rotated(pieces[m.a], (getattr(full, "turned", None) or {}).get(m.a, 0))
        target_origin[m.a] = (
            round((m.to[0] - current_pieces.w * grid.size / 2 - grid.ox) / grid.size),
            round((m.to[1] - current_pieces.h * grid.size / 2 - grid.oy) / grid.size),
        )
    opener = {m.a: m.target for m in access}
    resource_moved_ids = {m.a for m in resource_moves}
    resource_reasons = {m.a: m.reason for m in resource_moves}
    turn = {
        index: field_name
        for index, field_name in (getattr(full, "turned", None) or {}).items()
        if index in current
    }
    rots = {
        building["id"]: int(building.get("rot") or 0)
        for building in snapshot.get("buildings") or []
        if "id" in building
    }
    sequence = lo.move_sequence(
        grid,
        pieces,
        current,
        {index: origins for index, origins in target_origin.items() if index in current},
        turn,
        entrance,
    )
    movement_complete = sequence.complete
    if not movement_complete:
        # 끝까지 갈 수 없는 가상 목표의 점수·그림·구매 제안이 남지 않도록 현재 상태로 되돌려 평가한다.
        final_base, access, opener, turn = snapshot, [], {}, {}
        resource_moves, resource_moved_ids = [], set()
    access_gains = {m.a: m.gain for m in access}
    steps = []
    for index, origins, park in sequence:
        k = 0 if park else turn.get(index, 0)
        current_pieces = lo.rotated(pieces[index], k)
        to = grid.center(origins[0], origins[1], current_pieces.w, current_pieces.h)
        rot = (rots.get(index, 0) + k) % 4 if k else -1
        if park:
            steps.append(Move(index, to, 0.0, tr("잠시 비켜 두기 (다른 건물 자리 비우기)")))
        elif index in original_blockers:
            steps.append(Move(index, to, 0.0, tr("발사 입구를 비우기 위해 이동"), rot=rot))
        elif index in resource_moved_ids:
            steps.append(Move(index, to, 0.0, resource_reasons[index], rot=rot))
        elif index in opener:
            steps.append(
                Move(
                    index,
                    to,
                    access_gains[index],
                    tr("미완성 건물로 가는 길 열기"),
                    target=opener[index],
                    rot=rot,
                )
            )
        else:
            steps.append(
                Move(
                    index,
                    to,
                    0.0,
                    tr("{v0} 이 자리로", v0=lo.turn_text(k)) if k else tr("가이드 배치 자리로"),
                    rot=rot,
                )
            )
    final_blds = buildings_from_base(final_base)

    def effect_state(state):
        pieces, origins = lo.pieces_from_base(state, grid, lo.housing_types())
        scorer = lo.Scorer(pieces, lo._stat_types(state), lo.housing_types(), res_weight, pad)
        return scorer.score(lo.Layout(grid, pieces, origins))

    score_before, detail_before = effect_state(snapshot)
    score_after, detail_after = effect_state(final_base)
    harvest_before = full.harvest_before
    if team and harvest_before is None:
        harvest_before = hv(snapshot.get("geo") or {})
    harvest_after = (
        hv(final_base.get("geo") or {})
        if team
        else (harvest_before if not movement_complete else full.harvest_after)
    )
    plan = LayoutPlan(
        score_before, score_after, steps, detail_before, detail_after, harvest_before, harvest_after
    )
    plan.movement_complete = movement_complete
    plan.unresolved_moves = sequence.unresolved
    plan.evaluated_base = final_base
    plan.final = {index: (building.x, building.y) for index, building in final_blds.items()}
    plan.final_rot = {index: building.rot for index, building in final_blds.items()}
    if targets and team:
        plan.reach_after = {
            building: value
            for building, value in reach(final_base.get("geo") or {}).items()
            if building in targets
        }
        plan.reach_after.update({building: 0 for building in targets if building not in plan.reach_after})
    if not movement_complete:
        plan.notes = [
            tr("목표 배치로 끝까지 옮길 빈자리가 없어 이동을 보류했습니다. 현재 배치를 유지합니다.")
        ]
    elif access:
        plan.notes = [tr("공사 경로를 연 최종 배치로 범위 효과와 채집 예상을 다시 계산했습니다.")]
    else:
        plan.notes = [] if resource_moves or entrance_moves else list(full.notes)
    remaining_entrance = entrance_blockers(final_base)
    if original_blockers or remaining_entrance:
        plan.notes.append(
            tr(
                "입구를 막는 건물: {before}개 → {after}개 (앞줄 전체와 중앙 통로 보호)",
                before=len(original_blockers),
                after=len(remaining_entrance),
            )
        )
    probe_angles = resource_check.angles if resource_check is not None else tuple(range(25, 156, 5))
    if isinstance(hs.world_from_geo((snapshot.get("geo") or {}), 0.0), hs.World):
        plan.notes.append(
            tr(
                "발사 가능 각도 계산: {before}/{total} → {after}/{total} (이동 후 게임 판정 확인)",
                before=len(allowed_angles(snapshot["geo"], probe_angles)),
                total=len(probe_angles),
                after=len(allowed_angles(final_base["geo"], probe_angles)),
            )
        )
    if resource_check is not None and resource_before is not None:
        resource_after = resource_check.check(final_base)
        plan.resource_access_checked = resource_after is not None
        if resource_after is not None:
            plan.resource_unreachable_before = tuple(sorted(resource_before.blocked))
            plan.resource_unreachable_after = tuple(sorted(resource_after.blocked))
            plan.notes.append(
                tr(
                    "자원 접근 검사: {before}/{total} → {after}/{total} (발사 각도 검사·가동 생산 범위)",
                    before=len(resource_before.served),
                    after=len(resource_after.served),
                    total=len(resource_before.resources),
                )
            )
            if resource_moves:
                plan.notes.append(
                    tr(
                        "채집 경로가 없던 자원 {count}개를 우선 이동합니다. 범위 점수 2% 기준과 별도로 판단했습니다.",
                        count=sum(m.a in resource_before.resources for m in resource_moves),
                    )
                )
            if resource_after.blocked:
                from collections import Counter

                names = lo._game_text().get("buildings") or {}
                counts = Counter(resource_after.resources[index] for index in resource_after.blocked)
                labels = ", ".join(
                    f"{(names.get(lo._slug(typ)) or {}).get('name_ko') or typ} ×{count}"
                    for typ, count in sorted(counts.items())
                )
                plan.notes.append(
                    tr(
                        "검사한 각도에서 채집 경로 미확인: {targets}. 새 자원 구매보다 배치 확인이 먼저입니다.",
                        targets=labels,
                    )
                )
    if access or not movement_complete:
        from .layout_city import guide_report

        final_pieces, final_origins = lo.pieces_from_base(final_base, grid, lo.housing_types())
        scorer = lo.Scorer(final_pieces, lo._stat_types(final_base), lo.housing_types(), res_weight, pad)
        plan.notes += guide_report(grid, final_pieces, final_origins, scorer, pad)
    names = None
    for building in snapshot.get("buildings") or []:
        eff = lo.EFFECTS.get(building.get("type"))
        if not eff or not isinstance(eff[0], int) or building.get("id") not in final_blds:
            continue
        index = building["id"]
        if index not in current or (building.get("x"), building.get("y"), building.get("rot", 0)) == (
            final_blds[index].x,
            final_blds[index].y,
            final_blds[index].rot,
        ):
            continue
        counted = building.get("in_range")
        if not isinstance(counted, dict) or not all(isinstance(value, int) for value in counted.values()):
            continue
        q = final_blds[index]
        after = sum(
            lo.in_range(t.x - q.x, t.y - q.y, q.range + pad)
            for t in final_blds.values()
            if lo.TILE_RES.get(t.type) == eff[0]
        )
        if names is None:
            names = lo._game_text().get("buildings") or {}
        name = (names.get(lo._slug(building["type"])) or {}).get("name_ko") or building["type"]
        plan.notes.append(
            tr(
                "{name} 자원 범위: 게임 현재 {before}개 → 목표 자리 계산 {after}개 (이동 뒤 게임 값 확인)",
                name=name,
                before=sum(counted.values()),
                after=after,
            )
        )
    plan.calibration = calib
    from .construction_policy import (
        is_recommended_building,
        construction_guidance,
        PRIORITY_GROUP_ORDER,
        has_captain_targets,
    )

    options = [blueprint for blueprint in blueprints if is_recommended_building(blueprint.get("type", ""))]
    plan.build_costs = {
        blueprint["type"]: tuple(blueprint["cost"])
        for blueprint in options
        if blueprint.get("cost") is not None
    }
    # 게임이 추가 건설 가능하다고 보낸 항목만 쓴다. 보유 건물을 근거로 설계도를 만들어 내지 않는다.
    plan.construction_pending = (
        bool(steps)
        or not movement_complete
        or bool(plan.resource_unreachable_after)
        or bool(remaining_entrance)
    )
    if not plan.construction_pending:
        plan.builds = lo.suggest_builds(snapshot, options, res_weight, pad, resources=resources)
        plan.builds += lo.suggest_tiles(snapshot, res_weight, pad, build_options=options, resources=resources)
    owned = {building.get("type") for building in snapshot.get("buildings") or []}

    def build_priority(item):
        kind = item[0]
        group, _, _ = construction_guidance(
            kind,
            "build",
            has_housing=any(lo._slug(t) in lo.housing_types() for t in owned if t),
            has_stats=has_captain_targets(owned, snapshot, lo.STAT_FALLBACK),
            has_infinite_stats=bool(owned & lo.STATUE_TYPES),
            has_construction=bool(targets),
        )
        return (PRIORITY_GROUP_ORDER[group], -item[3])

    plan.builds.sort(key=build_priority)
    # 금광은 추천하지 않는다 (사용자 결정 2026-09-26: 무한 모드로 골드 충분 — 철거 후보는 suggest_demolish)
    plan.builds = [building for building in plan.builds if is_recommended_building(building[0])]
    plan.builds = [
        building
        for building in plan.builds
        if not grid.cells(building[1][0], building[1][1], building[2][0], building[2][1]) & entrance
    ]
    plan.activations = lo.activation_gains(final_base, res_weight, pad)
    # 철거는 실제 현재 범위 계수만 근거로 한다. 아직 실행하지 않은 이동안의 가상 위치로 철거를 권하지 않는다.
    plan.demolish = lo.suggest_demolish(snapshot, res_weight, pad)
    if steps:
        # 같은 화면에서 옮기라고 한 건물이나 이동 뒤 자원을 쓰게 되는 건물을 동시에 철거하라고 하지 않는다.
        still_unused = {row[0] for row in lo.suggest_demolish(final_base, res_weight, pad)}
        moved_ids = {m.a for m in steps}
        plan.demolish = [row for row in plan.demolish if row[0] in still_unused and row[0] not in moved_ids]
    from .layout import NewSpot

    plan.new_spots = [
        NewSpot(
            t,
            c,
            size,
            n,
            tr("생산 건물 범위의 빈칸 채우기 — 첫 자리 초록 점선{cost_txt}", cost_txt="")
            if t in lo.TILE_RES
            else tr("범위 효과 +{g:.1f} (지은 뒤 강화·일꾼 배정 기준)", g=g),
        )
        for t, c, size, g, n, *_ in plan.builds[:3]
    ]
    return plan
