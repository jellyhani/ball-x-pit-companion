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
from ..i18n import tr


def job_now(geo: dict, blds: Dict[int, dict], team: Sequence[dict], angle: float, dur: float,
            targets: Optional[Dict[int, int]] = None) -> dict:
    """지금 조준의 예상: 팀 채집량, 첫 작업자의 초기 경로(월드 좌표), 미완성 건물 타격."""
    world = hs.world_from_geo(geo, 0.03)
    if world is None:
        return {}
    counts: Dict[int, int] = {}
    points: Dict[int, int] = {}
    total, ws = hs.run_angle(world, blds, team, angle, dur, counts, points)
    targets = targets or {}
    per = {b: min(counts.get(b, 0), cap) for b, cap in targets.items() if counts.get(b)}
    per_points = {b: min(points.get(b, 0), cap) for b, cap in targets.items() if points.get(b)}
    return {"angle": angle, "total": total, "path": worker_preview(ws),
            "build_hits": sum(per.values()), "per_building": per,
            "build_points": sum(per_points.values()),
            "per_building_points": per_points,
            "model_limitations": hs.model_limitations(team, blds)}


def job_sweep(geo: dict, blds: Dict[int, dict], team: Sequence[dict], dur: float, need: int,
              targets: Optional[Dict[int, int]] = None, lo: float = 12.0, hi: float = 168.0) -> dict:
    """각도 탐색 결과 상위 5개(top, 1위는 첫 작업자의 초기 경로 포함)와 미완성 건물별 최대 타격 수(reach — 0 이면
    어떤 각도로도 닿지 않음)."""
    world = hs.world_from_geo(geo, 0.03)
    if world is None:
        return {}
    limits = (lo, hi)
    if native.lib() is not None:
        # 네이티브 계산(각도 하나 약 1ms)이면 1° 간격 전부 — 좁은 틈으로만 닿는 각도도 놓치지 않는다
        ranked = hs.rank_angles(world, blds, team, dur, need, targets, angles=_angle_grid(limits, 1))
    else:
        # 파이썬 계산: 거칠게 6° 간격 → 상위 3개 주변만 1° 간격 (전부 1°로 하면 작업자 10명·20초에 수 초)
        coarse = hs.rank_angles(world, blds, team, dur, need, targets, angles=_angle_grid(limits, 6))
        fine = sorted({a for r in coarse[:3] for a in range(int(r.angle) - 2, int(r.angle) + 3)
                       if lo <= a <= hi} - {r.angle for r in coarse})
        ranked = coarse + hs.rank_angles(world, blds, team, dur, need, targets, angles=fine)
    ranked.sort(key=lambda r: -hs.angle_score(r, need))
    reach = {b: 0 for b in targets or {}}
    for r in ranked:
        for b, v in r.per_building.items():
            reach[b] = max(reach.get(b, 0), v)
    out = []
    picked = []
    for r in ranked:                     # 후보끼리 8° 이상 떨어진 것만 (1위 옆 1° 는 다른 선택이 아니다)
        if all(abs(r.angle - q.angle) >= 8 for q in picked):
            picked.append(r)
        if len(picked) == 5:
            break
    for r in picked:
        # 남은 공사 점수까지만 반영한다. 충돌 횟수와 실제 건설 강화의 진행량을 혼동하지 않는다.
        score = hs.angle_score(r, need)
        points: Dict[int, int] = {}
        _, ws = hs.run_angle(world, blds, team, r.angle, dur, build_points=points)
        out.append({"angle": r.angle, "total": r.total, "build_hits": r.build_hits, "per_building": r.per_building,
                    "build_points": getattr(r, "build_points", None),
                    "per_building_points": getattr(r, "per_building_points", {}),
                    "score": score, "path": worker_preview(ws)})
    return {"top": out, "reach": reach, "model_limitations": hs.model_limitations(team, blds)}


def gold_mine_spot(base: dict, blds: Dict[int, dict], team: Sequence[dict], dur: float, need: int, bp: dict):
    """금광: 범위 효과가 아니라 작업자가 튕길 때마다 골드. 추천 각도로 쏜 작업자 경로가 가장 많이 지나는 빈 자리를
    고르고(경로 밀도), 상위 몇 곳은 금광 충돌 상자를 넣어 궤적을 다시 계산해 실제 튕김 수로 확인한다.
    돌려주는 값: (종류, 중심, 크기, 예상 튕김 수, 튕김 수, 0) 또는 None."""
    from . import layout_opt as lo
    from .layout import grid_from_geo
    geo = base.get("geo") or {}
    grid = grid_from_geo(geo)
    world = hs.world_from_geo(geo, 0.03)
    if grid is None or world is None:
        return None
    w, h = bp.get("size") or (2, 2)
    pieces, origin = lo.pieces_from_base(base, grid, lo.housing_types())
    occ = lo.Layout(grid, pieces, origin).occ
    best_a = hs.best_angles(world, blds, team, dur, need, angles=range(24, 157, 8))[0][0]
    _, ws = hs.run_angle(world, blds, team, best_a, dur)
    heat: Dict[tuple, int] = {}
    for wk in ws:
        pts = wk.path
        for (x0, y0, _t0), (x1, y1, _t1) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (grid.size / 2)))
            for k in range(n + 1):
                x, y = x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n
                cell = (math.floor((x - grid.ox) / grid.size), math.floor((y - grid.oy) / grid.size))
                heat[cell] = heat.get(cell, 0) + 1
    spots = []
    for c, r in grid.tiles:
        cells = [(c + dx, r + dy) for dx in range(w) for dy in range(h)]
        if all(x in grid.tiles and x not in occ for x in cells):
            spots.append((sum(heat.get(x, 0) for x in cells), (c, r)))
    spots.sort(reverse=True)
    best = None
    for _, (c, r) in spots[:4]:
        cx, cy = grid.center(c, r, w, h)
        hw, hh = w * grid.size / 2, h * grid.size / 2
        g2 = dict(geo)
        g2["colliders"] = list(geo.get("colliders") or []) + [
            {"id": -7, "shape": "box", "pts": [[cx - hw, cy - hh], [cx + hw, cy - hh], [cx + hw, cy + hh], [cx - hw, cy + hh]]}]
        w2 = hs.world_from_geo(g2, 0.03)
        b2 = dict(blds)
        b2[-7] = {"id": -7, "type": "kGoldMine", "res": 0}
        top = 0
        for a in range(24, 157, 8):
            counts: Dict[int, int] = {}
            hs.run_angle(w2, b2, team, a, dur, counts)
            top = max(top, counts.get(-7, 0))
        if best is None or top > best[0]:
            best = (top, (cx, cy))
    if not best or best[0] <= 0:
        return None
    return ("kGoldMine", best[1], (w, h), float(best[0]), best[0], 0)


def full_tiles(blds: Dict[int, dict]) -> Dict[int, dict]:
    """자원 타일을 가득 찬 상태로 (배치는 오래 쓰는 것 — 오늘 이미 캐서 빈 타일로 비교하면 모든 배치가 0이 된다)."""
    from .layout_opt import TILE_RES
    return {i: (dict(b, res=int(b.get("cap") or b.get("res") or 1), can_harvest=True) if b.get("type") in TILE_RES else b)
            for i, b in blds.items()}


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


def job_layout(snap: dict, team: Sequence[dict], dur: float, blueprints: Sequence[dict],
               targets: Optional[Dict[int, int]] = None, need: int = 1, seconds: float = 12.0,
               prefer: Optional[Dict[int, tuple]] = None, char_levels: Optional[Dict[str, int]] = None,
               resources: Optional[Sequence[int]] = None, aim_limits: Optional[Sequence[float]] = None):
    """가이드 배치(지금 배치에서 출발하는 담금질 + 가이드 허브 규칙, preset "guide") → 닿지 않는 미완성 건물로 길 열기 → 새 건물 자리. 자원별 추천 각도.
    seconds: 탐색 시간 상한. prefer: 이전 목표 배치(건물별 중심) — 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다.

    돌려주는 값: (LayoutPlan, 자원별 각도 순위). LayoutPlan.swaps 는 게임에서 할 옮기기 순서(잠시 비켜 두기 포함),
    LayoutPlan.final 은 건물별 목표 중심(월드 좌표)."""
    from . import layout_opt as lo
    from .layout import LayoutPlan, Move, buildings_from_base, grid_from_geo, plan_access, shape_masks, suggest_new
    blds = full_tiles({b["id"]: b for b in snap.get("buildings") or [] if "id" in b})
    limitations = hs.model_limitations(team, blds)
    res_weight = {r: (1.5 if r == need else 1.0) for r in (1, 2, 3)}

    mines = [i for i, b in blds.items() if b.get("type") == "kGoldMine"]
    monks = [i for i, b in blds.items() if b.get("type") == "kMonastery"]
    lo.set_char_levels(char_levels or {})

    def hv(geo):
        """추천 각도의 채집 발사 예상 [골드, 밀, 나무, 돌]. 골드 = 금광 튕김 수 × 1.5 (튕길 때 1~2, 광마다 최대 100번 — 위키)."""
        w = hs.world_from_geo(geo, 0.03)
        if not w:
            return [0, 0, 0, 0]
        angles = HV_ANGLES(aim_limits)
        if not angles:
            return [0, 0, 0, 0]
        a, total = hs.best_angles(w, blds, team, dur, need, angles=angles)[0]
        if mines or monks:
            counts: Dict[int, int] = {}
            hs.run_angle(w, blds, team, a, dur, counts)
            total = list(total)
            total[0] += int(sum(min(counts.get(m, 0), 100) for m in mines) * 1.5)
            # 수도원: 튕길 때마다 채집 시간 +0.07초 (채집당 최대 100번 — 위키) → 늘어난 시간만큼 자원도 늘어난다고 봄
            extra = 0.07 * sum(min(counts.get(m, 0), 100) for m in monks)
            if extra:
                total = [total[0]] + [round(v * (1 + extra / max(dur, 1.0))) for v in total[1:]]
        return total

    def reach(geo):
        w = hs.world_from_geo(geo, 0.03)
        out: Dict[int, int] = {}
        if w:
            for r in hs.rank_angles(w, blds, team, dur, need, targets, angles=REACH_ANGLES(aim_limits)):
                for b, v in r.per_building.items():
                    out[b] = max(out.get(b, 0), v)
        return out

    calib = lo.calibrate_range(snap)                  # 게임이 직접 센 범위 안 타일 수와 맞춤 (플러그인 1.9)
    pad = calib[0] if calib[2] else 0.0
    # 배치 추천은 가이드 배치 하나 (사용자 결정 2026-09-26: '적게 움직이고 효율 최대, 가이드대로').
    # 처음부터 다시 짜는 계획도시(layout_city.plan_city)는 이미 거의 된 기지에서도 80개를 옮기라고 해서(+2%),
    # 지금 배치에서 출발하는 담금질(옮기기 하나당 벌점)에 가이드 허브 규칙을 크게 쳐서 쓴다 (preset "guide").
    # 쳐야 지어지는 건물은 어떤 각도로든 공이 닿는 배치만 고른다 (사용자 스크린샷: 도박장이 마을 구석에 묻힘).
    def reach_all(nb: dict) -> bool:
        got = reach(nb.get("geo") or {})
        return all(got.get(t, 0) > 0 for t in targets)
    # 아직 게임과 맞추지 못한 수익 모형은 이동안을 고르는 효율 점수에 쓰지 않는다. 참고 수치는 아래에서 따로 계산한다.
    full = lo.optimize(snap, hv if team and not limitations else None, reach_all if (targets and team) else None, res_weight=res_weight,
                       fixed=list(targets or {}), pad=pad, preset="guide", seconds=min(seconds, 8.0), prefer=prefer)
    grid = grid_from_geo(snap.get("geo") or {})
    if full is None or grid is None:
        return None, {}
    plan = _plan_from(snap, full, grid, targets, team, reach, hv, blueprints, res_weight, pad, blds, dur, need, calib, resources)
    plan.preset = "guide"
    plan.model_limitations = limitations
    final_state = plan.evaluated_base
    world = hs.world_from_geo(final_state.get("geo") or {}, 0.03)
    final_blds = full_tiles({b["id"]: b for b in final_state.get("buildings") or [] if "id" in b})
    sweeps = {}
    if world and team:
        for res in (1, 2, 3):
            sweeps[res] = [(r.angle, r.total) for r in
                           hs.rank_angles(world, final_blds, team, dur, res, None, angles=_angle_grid(aim_limits, 5))]
    return plan, sweeps


def _plan_from(snap, full, grid, targets, team, reach, hv, blueprints, res_weight, pad, blds, dur, need, calib, resources=None):
    """최적화 결과(FullPlan) → 게임에서 할 옮기기 순서·새로 지을 것·강화 추천이 붙은 LayoutPlan."""
    from . import layout_opt as lo
    from .layout import LayoutPlan, Move, buildings_from_base, plan_access
    from .layout_guide import preserves_guide
    final_base = lo.final_base(snap, full)
    entrance = lo.entrance_cells(snap.get("geo") or {}, grid)
    access, final_base, reserved = (plan_access(final_base, list(targets), reach, avoid=entrance,
                                               accept_fn=lambda nb: lo.preserves_production(snap, nb, pad)
                                               and preserves_guide(snap, nb, pad)) if targets and team
                                    else ([], final_base, set()))
    pieces, cur = lo.pieces_from_base(snap, grid, lo.housing_types())
    target_origin = dict(full.origin_after)
    for m in access:                                  # 길 열기 옮기기도 목표 자리에 반영
        p = pieces[m.a]
        target_origin[m.a] = (round((m.to[0] - p.w * grid.size / 2 - grid.ox) / grid.size),
                              round((m.to[1] - p.h * grid.size / 2 - grid.oy) / grid.size))
    opener = {m.a: m.target for m in access}
    turn = {i: k for i, k in (getattr(full, "turned", None) or {}).items() if i in cur}
    rots = {b["id"]: int(b.get("rot") or 0) for b in snap.get("buildings") or [] if "id" in b}
    sequence = lo.move_sequence(grid, pieces, cur, {i: o for i, o in target_origin.items() if i in cur},
                                turn, entrance)
    movement_complete = sequence.complete
    if not movement_complete:
        # 끝까지 갈 수 없는 가상 목표의 점수·그림·구매 제안이 남지 않도록 현재 상태로 되돌려 평가한다.
        final_base, access, opener, turn = snap, [], {}, {}
    access_gains = {m.a: m.gain for m in access}
    steps = []
    for i, o, park in sequence:
        k = 0 if park else turn.get(i, 0)
        p = lo.rotated(pieces[i], k)
        to = grid.center(o[0], o[1], p.w, p.h)
        rot = (rots.get(i, 0) + k) % 4 if k else -1
        if park:
            steps.append(Move(i, to, 0.0, tr("잠시 비켜 두기 (다른 건물 자리 비우기)")))
        elif i in opener:
            steps.append(Move(i, to, access_gains[i], tr("미완성 건물로 가는 길 열기"), target=opener[i], rot=rot))
        else:
            steps.append(Move(i, to, 0.0, tr("{v0} 이 자리로", v0=lo.turn_text(k)) if k else tr("가이드 배치 자리로"), rot=rot))
    final_blds = buildings_from_base(final_base)
    def effect_state(state):
        pcs, origins = lo.pieces_from_base(state, grid, lo.housing_types())
        scorer = lo.Scorer(pcs, lo._stat_types(state), lo.housing_types(), res_weight, pad)
        return scorer.score(lo.Layout(grid, pcs, origins))

    score_before, detail_before = effect_state(snap)
    score_after, detail_after = effect_state(final_base)
    harvest_before = full.harvest_before
    if team and harvest_before is None:
        harvest_before = hv(snap.get("geo") or {})
    harvest_after = hv(final_base.get("geo") or {}) if team else (harvest_before if not movement_complete else full.harvest_after)
    plan = LayoutPlan(score_before, score_after, steps, detail_before, detail_after, harvest_before, harvest_after)
    plan.movement_complete = movement_complete
    plan.unresolved_moves = sequence.unresolved
    plan.evaluated_base = final_base
    plan.final = {i: (b.x, b.y) for i, b in final_blds.items()}
    plan.final_rot = {i: b.rot for i, b in final_blds.items()}
    if targets and team:
        plan.reach_after = {b: v for b, v in reach(final_base.get("geo") or {}).items() if b in targets}
        plan.reach_after.update({b: 0 for b in targets if b not in plan.reach_after})
    if not movement_complete:
        plan.notes = [tr("목표 배치로 끝까지 옮길 빈자리가 없어 이동을 보류했습니다. 현재 배치를 유지합니다.")]
    elif access:
        plan.notes = [tr("공사 경로를 연 최종 배치로 범위 효과와 채집 예상을 다시 계산했습니다.")]
    else:
        plan.notes = list(full.notes)
    if access or not movement_complete:
        from .layout_city import guide_report
        final_pieces, final_origins = lo.pieces_from_base(final_base, grid, lo.housing_types())
        scorer = lo.Scorer(final_pieces, lo._stat_types(final_base), lo.housing_types(), res_weight, pad)
        plan.notes += guide_report(grid, final_pieces, final_origins, scorer, pad)
    names = None
    for b in snap.get("buildings") or []:
        eff = lo.EFFECTS.get(b.get("type"))
        if not eff or not isinstance(eff[0], int) or b.get("id") not in final_blds:
            continue
        i = b["id"]
        if i not in cur or (b.get("x"), b.get("y"), b.get("rot", 0)) == (final_blds[i].x, final_blds[i].y, final_blds[i].rot):
            continue
        counted = b.get("in_range")
        if not isinstance(counted, dict) or not all(isinstance(v, int) for v in counted.values()):
            continue
        q = final_blds[i]
        after = sum(lo.in_range(t.x - q.x, t.y - q.y, q.range + pad)
                    for t in final_blds.values() if lo.TILE_RES.get(t.type) == eff[0])
        if names is None:
            names = lo._game_text().get("buildings") or {}
        name = (names.get(lo._slug(b["type"])) or {}).get("name_ko") or b["type"]
        plan.notes.append(tr("{name} 자원 범위: 게임 현재 {before}개 → 목표 자리 계산 {after}개 (이동 뒤 게임 값 확인)",
                             name=name, before=sum(counted.values()), after=after))
    plan.calibration = calib
    from .construction_policy import (is_recommended_building, construction_guidance, PRIORITY_GROUP_ORDER,
                                      has_captain_targets)
    options = [bp for bp in blueprints if is_recommended_building(bp.get("type", ""))]
    plan.build_costs = {bp["type"]: tuple(bp["cost"]) for bp in options if bp.get("cost") is not None}
    # 게임이 추가 건설 가능하다고 보낸 항목만 쓴다. 보유 건물을 근거로 설계도를 만들어 내지 않는다.
    plan.construction_pending = bool(steps) or not movement_complete
    if not plan.construction_pending:
        plan.builds = lo.suggest_builds(snap, options, res_weight, pad, resources=resources)
        plan.builds += lo.suggest_tiles(snap, res_weight, pad, build_options=options, resources=resources)
    owned = {b.get("type") for b in snap.get("buildings") or []}
    def build_priority(item):
        kind = item[0]
        group, _, _ = construction_guidance(kind, "build",
            has_housing=any(lo._slug(t) in lo.housing_types() for t in owned if t),
            has_stats=has_captain_targets(owned, snap, lo.STAT_FALLBACK),
            has_infinite_stats=bool(owned & lo.STATUE_TYPES),
            has_construction=bool(targets))
        return (PRIORITY_GROUP_ORDER[group], -item[3])
    plan.builds.sort(key=build_priority)
    # 금광은 추천하지 않는다 (사용자 결정 2026-09-26: 무한 모드로 골드 충분 — 철거 후보는 suggest_demolish)
    plan.builds = [b for b in plan.builds if is_recommended_building(b[0])]
    plan.builds = [b for b in plan.builds if not grid.cells(b[1][0], b[1][1], b[2][0], b[2][1]) & entrance]
    plan.activations = lo.activation_gains(final_base, res_weight, pad)
    # 철거는 실제 현재 범위 계수만 근거로 한다. 아직 실행하지 않은 이동안의 가상 위치로 철거를 권하지 않는다.
    plan.demolish = lo.suggest_demolish(snap, res_weight, pad)
    if steps:
        # 같은 화면에서 옮기라고 한 건물이나 이동 뒤 자원을 쓰게 되는 건물을 동시에 철거하라고 하지 않는다.
        still_unused = {row[0] for row in lo.suggest_demolish(final_base, res_weight, pad)}
        moved_ids = {m.a for m in steps}
        plan.demolish = [row for row in plan.demolish if row[0] in still_unused and row[0] not in moved_ids]
    from .layout import NewSpot
    plan.new_spots = [NewSpot(t, c, sz, n, tr("생산 건물 범위의 빈칸 채우기 — 첫 자리 초록 점선{cost_txt}", cost_txt="")
                             if t in lo.TILE_RES else tr("범위 효과 +{g:.1f} (지은 뒤 강화·일꾼 배정 기준)", g=g))
                      for t, c, sz, g, n, *_ in plan.builds[:3]]
    return plan
