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


def job_now(geo: dict, blds: Dict[int, dict], team: Sequence[dict], angle: float, dur: float,
            targets: Optional[Dict[int, int]] = None) -> dict:
    """지금 조준의 예상: 팀 채집량, 첫 작업자 경로(월드 좌표, 앞 11개 꺾임), 미완성 건물 타격."""
    world = hs.world_from_geo(geo, 0.03)
    if world is None:
        return {}
    counts: Dict[int, int] = {}
    total, ws = hs.run_angle(world, blds, team, angle, dur, counts)
    targets = targets or {}
    per = {b: min(counts.get(b, 0), cap) for b, cap in targets.items() if counts.get(b)}
    return {"angle": angle, "total": total, "path": [(x, y) for x, y, _t in ws[0].path[:11]] if ws else [],
            "build_hits": sum(per.values()), "per_building": per}


def job_sweep(geo: dict, blds: Dict[int, dict], team: Sequence[dict], dur: float, need: int,
              targets: Optional[Dict[int, int]] = None, lo: float = 12.0, hi: float = 168.0) -> dict:
    """각도 탐색 결과 상위 5개(top, 1위는 첫 작업자 경로 포함)와 미완성 건물별 최대 타격 수(reach — 0 이면
    어떤 각도로도 닿지 않음)."""
    world = hs.world_from_geo(geo, 0.03)
    if world is None:
        return {}
    lo_i, hi_i = int(round(lo)), int(round(hi))
    if native.lib() is not None:
        # 네이티브 계산(각도 하나 약 1ms)이면 1° 간격 전부 — 좁은 틈으로만 닿는 각도도 놓치지 않는다
        ranked = hs.rank_angles(world, blds, team, dur, need, targets, angles=range(lo_i, hi_i + 1))
    else:
        # 파이썬 계산: 거칠게 6° 간격 → 상위 3개 주변만 1° 간격 (전부 1°로 하면 작업자 10명·20초에 수 초)
        coarse = hs.rank_angles(world, blds, team, dur, need, targets, angles=range(lo_i, hi_i + 1, 6))
        fine = sorted({a for r in coarse[:3] for a in range(int(r.angle) - 2, int(r.angle) + 3)
                       if lo_i <= a <= hi_i} - {int(r.angle) for r in coarse})
        ranked = coarse + hs.rank_angles(world, blds, team, dur, need, targets, angles=fine)
    ranked.sort(key=lambda r: -(100 * r.build_hits + r.total[need] + 0.25 * (sum(r.total) - r.total[need])))
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
        # score: 순위를 정한 값 (미완성 건물 타격 100배 + 필요한 자원 + 다른 자원 1/4). 비슷한 후보를 가리는 데 쓴다
        score = 100 * r.build_hits + r.total[need] + 0.25 * (sum(r.total) - r.total[need])
        _, ws = hs.run_angle(world, blds, team, r.angle, dur)
        out.append({"angle": r.angle, "total": r.total, "build_hits": r.build_hits, "per_building": r.per_building,
                    "score": score, "path": [(x, y) for x, y, _t in ws[0].path[:11]] if ws else []})
    return {"top": out, "reach": reach}


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


def HV_ANGLES():
    """배치 후보 비교에 쓰는 각도 (네이티브면 3°, 파이썬이면 10° 간격)."""
    return range(15, 166, 3) if native.lib() is not None else range(20, 161, 10)


def REACH_ANGLES():
    return range(15, 166, 3) if native.lib() is not None else range(15, 166, 6)


def job_layout(snap: dict, team: Sequence[dict], dur: float, blueprints: Sequence[dict],
               targets: Optional[Dict[int, int]] = None, need: int = 1, seconds: float = 12.0,
               prefer: Optional[Dict[int, tuple]] = None, char_levels: Optional[Dict[str, int]] = None):
    """계획도시 배치(전체 재배치, layout_city) → 닿지 않는 미완성 건물로 길 열기 → 새 건물 자리. 자원별 추천 각도.
    seconds·prefer 는 예전 담금질용 — 지금은 쓰지 않는다 (호출 쪽 호환용).

    돌려주는 값: (LayoutPlan, 자원별 각도 순위). LayoutPlan.swaps 는 게임에서 할 옮기기 순서(잠시 비켜 두기 포함),
    LayoutPlan.final 은 건물별 목표 중심(월드 좌표)."""
    from . import layout_opt as lo
    from .layout import LayoutPlan, Move, buildings_from_base, grid_from_geo, plan_access, shape_masks, suggest_new
    blds = full_tiles({b["id"]: b for b in snap.get("buildings") or [] if "id" in b})
    res_weight = {r: (1.5 if r == need else 1.0) for r in (1, 2, 3)}

    mines = [i for i, b in blds.items() if b.get("type") == "kGoldMine"]
    monks = [i for i, b in blds.items() if b.get("type") == "kMonastery"]
    lo.set_char_levels(char_levels or {})

    def hv(geo):
        """추천 각도의 채집 발사 예상 [골드, 밀, 나무, 돌]. 골드 = 금광 튕김 수 × 1.5 (튕길 때 1~2, 광마다 최대 100번 — 위키)."""
        w = hs.world_from_geo(geo, 0.03)
        if not w:
            return [0, 0, 0, 0]
        a, total = hs.best_angles(w, blds, team, dur, need, angles=HV_ANGLES())[0]
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
            for r in hs.rank_angles(w, blds, team, dur, need, targets, angles=REACH_ANGLES()):
                for b, v in r.per_building.items():
                    out[b] = max(out.get(b, 0), v)
        return out

    calib = lo.calibrate_range(snap)                  # 게임이 직접 센 범위 안 타일 수와 맞춤 (플러그인 1.9)
    pad = calib[0] if calib[2] else 0.0
    # 배치 추천은 계획도시 하나 (사용자 결정 2026-09-25): 생산 유닛 격자 + 빽빽한 마을 + 금광 U자 (layout_city).
    # 담금질이 아니라 정해진 규칙으로 짜서 1초 안팎, 재배치 도중 다시 계산해도 목표가 같다.
    full = lo.optimize(snap, hv if team else None, None, res_weight=res_weight,
                       fixed=list(targets or {}), pad=pad, preset="plan")
    grid = grid_from_geo(snap.get("geo") or {})
    if full is None or grid is None:
        return None, {}
    plan = _plan_from(snap, full, grid, targets, team, reach, hv, blueprints, res_weight, pad, blds, dur, need, calib)
    plan.preset = "plan"
    if mines:
        # 금광 U자 빈 자리: 금광을 더 지으면 채울 곳 (정석: 발사대 앞 7개, 광마다 일꾼 1명 — 위키·커뮤니티)
        spots = lo.gold_u_spots(snap.get("geo") or {}, grid)
        plan.preset_spots = [grid.center(c, r, 2, 2) for c, r in spots]
        filled = {full.origin_after.get(i) for i in mines}
        missing = [s for s in spots if s not in filled]
        if missing:
            from .layout import NewSpot
            plan.builds = [("kGoldMine", grid.center(missing[0][0], missing[0][1], 2, 2), (2, 2), 0.0, len(missing), 0)] + \
                [b for b in plan.builds if b[0] != "kGoldMine"]
            plan.new_spots = [NewSpot("kGoldMine", grid.center(c, r, 2, 2), (2, 2), 0, "금광 U자 빈 자리") for c, r in missing]
            plan.notes.append(f"금광 {len(missing)}개를 더 지으면 U자 완성 (초록 점선, 광마다 일꾼 1명)")
            # 완성했을 때의 채집 골드 추정: 빈 자리에 금광 충돌 상자를 넣고 궤적 계산 (튕김 × 1.5, 광마다 최대 100번)
            if team:
                fb = lo.final_base(snap, full)
                g2 = dict(fb.get("geo") or {})
                b2 = dict(blds)
                extra = []
                for k, (c, r) in enumerate(missing):
                    cx, cy = grid.center(c, r, 2, 2)
                    hw = grid.size
                    nid = -300 - k
                    extra.append(nid)
                    g2["colliders"] = list(g2.get("colliders") or []) + [
                        {"id": nid, "shape": "box",
                         "pts": [[cx - hw, cy - hw], [cx + hw, cy - hw], [cx + hw, cy + hw], [cx - hw, cy + hw]]}]
                    b2[nid] = {"id": nid, "type": "kGoldMine", "res": 0}
                w2 = hs.world_from_geo(g2, 0.03)
                if w2:
                    best_gold = 0
                    for ang in range(24, 157, 6):
                        counts: Dict[int, int] = {}
                        hs.run_angle(w2, b2, team, ang, dur, counts)
                        gold = sum(min(counts.get(m, 0), 100) for m in extra + mines) * 1.5
                        best_gold = max(best_gold, gold)
                    plan.notes.append(f"U자 완성 시 채집 한 번에 골드 약 {best_gold:,.0f} 예상 (궤적 계산, 튕길 때 1~2골드 평균)")
    world = hs.world_from_geo(lo.final_base(snap, full).get("geo") or {}, 0.03)
    sweeps = {}
    if world and team:
        for res in (1, 2, 3):
            sweeps[res] = [(r.angle, r.total) for r in
                           hs.rank_angles(world, blds, team, dur, res, None, angles=range(15, 166, 5))]
    return plan, sweeps


def _plan_from(snap, full, grid, targets, team, reach, hv, blueprints, res_weight, pad, blds, dur, need, calib):
    """최적화 결과(FullPlan) → 게임에서 할 옮기기 순서·새로 지을 것·강화 추천이 붙은 LayoutPlan."""
    from . import layout_opt as lo
    from .layout import LayoutPlan, Move, buildings_from_base, plan_access
    final_base = lo.final_base(snap, full)
    access, final_base, reserved = (plan_access(final_base, list(targets), reach) if targets and team
                                    else ([], final_base, set()))
    pieces, cur = lo.pieces_from_base(snap, grid, lo.housing_types())
    target_origin = dict(full.origin_after)
    for m in access:                                  # 길 열기 옮기기도 목표 자리에 반영
        p = pieces[m.a]
        target_origin[m.a] = (round((m.to[0] - p.w * grid.size / 2 - grid.ox) / grid.size),
                              round((m.to[1] - p.h * grid.size / 2 - grid.oy) / grid.size))
    opener = {m.a: m.target for m in access}
    turn = set(getattr(full, "turned", ()) or ()) & set(cur)
    rots = {b["id"]: int(b.get("rot") or 0) for b in snap.get("buildings") or [] if "id" in b}
    steps = []
    for i, o, park in lo.move_sequence(grid, pieces, cur, {i: o for i, o in target_origin.items() if i in cur}, turn):
        spin = i in turn and not park
        p = lo.turned(pieces[i]) if spin else pieces[i]
        to = grid.center(o[0], o[1], p.w, p.h)
        rot = (rots.get(i, 0) + 1) % 4 if spin else -1
        if park:
            steps.append(Move(i, to, 0.0, "잠시 비켜 두기 (다른 건물 자리 비우기)"))
        elif i in opener:
            steps.append(Move(i, to, 1.0, "미완성 건물로 가는 길 열기", target=opener[i], rot=rot))
        else:
            steps.append(Move(i, to, 0.0, "회전(가로↔세로)해서 이 자리로" if spin else "최적 배치 자리로", rot=rot))
    final_blds = buildings_from_base(final_base)
    plan = LayoutPlan(full.effect_before, full.effect_after, steps, full.detail_before, full.detail_after,
                      full.harvest_before, hv(final_base.get("geo") or {}) if team else full.harvest_after)
    plan.final = {i: (b.x, b.y) for i, b in final_blds.items()}
    plan.final_rot = {i: b.rot for i, b in final_blds.items()}
    if targets and team:
        plan.reach_after = {b: v for b, v in reach(final_base.get("geo") or {}).items() if b in targets}
        plan.reach_after.update({b: 0 for b in targets if b not in plan.reach_after})
    plan.notes = list(full.notes)
    plan.calibration = calib
    # 새로 지을 것: 설계도 건물 + 여러 개 지을 수 있는 생산 건물(이미 있는 농장·채석장 하나 더) + 자원 타일 더 사기
    have = {b.get("type") for b in final_base.get("buildings") or []}
    more = [{"type": t} for t in ("kIdleFarm", "kIdleStoneMine") if t in have and t not in {bp.get("type") for bp in blueprints}]
    plan.builds = lo.suggest_builds(final_base, list(blueprints) + more, res_weight, pad)
    plan.builds += lo.suggest_tiles(final_base, res_weight, pad)
    plan.builds.sort(key=lambda x: -x[3])
    if team and (any(bp.get("type") == "kGoldMine" for bp in blueprints) or "kGoldMine" in have):
        # 금광은 여러 개 지을 수 있다 (정석: 발사대 앞 U자 7개, 광마다 일꾼 1명 — 위키·커뮤니티)
        gm = gold_mine_spot(final_base, blds, team, dur, need,
                            next((bp for bp in blueprints if bp.get("type") == "kGoldMine"), {"type": "kGoldMine"}))
        if gm:
            plan.builds.append(gm)
    plan.activations = lo.activation_gains(final_base, res_weight, pad)
    plan.demolish = lo.suggest_demolish(final_base, res_weight, pad)
    from .layout import NewSpot
    plan.new_spots = [NewSpot(t, c, sz, n, f"범위 효과 +{g:.1f} (지은 뒤 강화·일꾼 배정 기준)")
                      for t, c, sz, g, n, *_ in plan.builds[:3]]
    return plan
