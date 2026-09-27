"""네이티브 계산 모듈 (native/bxp_native.cpp → src/engine/bxp_native.dll) 을 ctypes 로 부른다.

DLL 이 없거나 불러오지 못하면 None — 부르는 쪽은 파이썬 구현으로 계산한다.
환경 변수 BXP_NO_NATIVE=1 이면 쓰지 않는다 (비교·디버깅용).
"""
from __future__ import annotations

import ctypes
import logging
import os
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_DLL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bxp_native.dll")
_lib = None

F_WHEAT, F_TILE, F_BUILD = 1, 2, 4
U_PIERCE_BUILDINGS, U_PIERCE_STONE, U_PIERCE_WOOD = 1, 2, 4
KIND = {"circle": 0, "box": 1, "poly": 2}


def lib():
    """불러온 DLL (한 번만). 쓸 수 없으면 None."""
    global _lib
    if _lib is not None:
        return _lib or None
    _lib = False
    if os.environ.get("BXP_NO_NATIVE") or os.name != "nt" or not os.path.exists(_DLL):
        return None
    try:
        dll = ctypes.CDLL(_DLL)
        if dll.bxp_version() != 4:
            return None
        P = ctypes.POINTER
        d, i = ctypes.c_double, ctypes.c_int
        dll.bxp_simulate_team.restype = i
        dll.bxp_simulate_team.argtypes = [P(d), i, P(i), P(i), P(i), P(i), P(i), P(d), P(d), P(d), P(i), P(i), P(i),
                                          i, P(d), P(i), d, i, P(i), P(i), P(i), P(d), i, P(d), P(i),
                                          P(i), P(i), P(i), P(i), P(d), P(i), P(i)]
        _lib = dll
        log.info("네이티브 계산 모듈 사용 (%s)", _DLL)
    except (OSError, AttributeError) as e:
        log.warning("네이티브 계산 모듈을 불러오지 못해 파이썬으로 계산합니다: %s", e)
        return None
    return _lib


def _arr(ctype, values):
    return (ctype * max(1, len(values)))(*values)


class PackedWorld:
    """기지 모양을 DLL 에 넘길 배열로 (World 마다 한 번)."""

    def __init__(self, world):
        self.world = _arr(ctypes.c_double, [world.left, world.right, world.bottom, world.top, world.radius])
        shapes = world.shapes
        self.n = len(shapes)
        self.bids = [s.bid for s in shapes]
        self.kind = _arr(ctypes.c_int, [KIND[s.kind] for s in shapes])
        self.bid = _arr(ctypes.c_int, self.bids)
        off, cnt, pts, circ, bb = [], [], [], [], []
        for s in shapes:
            off.append(len(pts) // 2)
            cnt.append(len(s.pts))
            for x, y in s.pts:
                pts += [x, y]
            circ += [s.c[0], s.c[1], s.r]
            bb += list(s.bb)
        self.pt_off, self.pt_cnt = _arr(ctypes.c_int, off), _arr(ctypes.c_int, cnt)
        self.pts, self.circ, self.bb = _arr(ctypes.c_double, pts), _arr(ctypes.c_double, circ), _arr(ctypes.c_double, bb)
        # 건물 슬롯: 모양의 건물 id 마다 하나 (id 가 같은 모양은 같은 슬롯 — 자원·횟수가 건물 단위)
        self.slot_ids: List[int] = []
        idx: Dict[int, int] = {}
        for b in self.bids:
            if b not in idx:
                idx[b] = len(self.slot_ids)
                self.slot_ids.append(b)
        self.slot = _arr(ctypes.c_int, [idx[b] for b in self.bids])


def simulate_team(world, buildings: Dict[int, dict], workers: list, duration: float, max_events: int,
                  counts: Optional[Dict[int, int]], flags_of,
                  build_points: Optional[Dict[int, int]] = None,
                  collected: Optional[Dict[int, int]] = None) -> Optional[List[int]]:
    """harvest_sim.simulate_team 과 같은 계산. 작업자(Worker)의 위치·경로·획득을 채우고 합계를 돌려준다.
    flags_of(bid) → (flags, rtype, res). 쓸 수 없으면 None (파이썬으로 계산)."""
    dll = lib()
    if dll is None:
        return None
    pw = getattr(world, "_packed", None)
    if pw is None:
        pw = world._packed = PackedWorld(world)
    fl, rt, rs = [], [], []
    for b in pw.slot_ids:
        f, r, n = flags_of(b)
        fl.append(f)
        rt.append(r)
        rs.append(n)
    ns = len(pw.slot_ids)
    flags, rtype, res = _arr(ctypes.c_int, fl), _arr(ctypes.c_int, rt), _arr(ctypes.c_int, rs)
    nw = len(workers)
    wk = _arr(ctypes.c_double, [v for w in workers for v in (w.x, w.y, w.dx, w.dy, w.speed, w.t)])
    ups = _arr(ctypes.c_int, [(U_PIERCE_BUILDINGS if w.upgrades.get("kPierceBuildings") else 0)
                              | (U_PIERCE_STONE if w.upgrades.get("kPierceStone") else 0)
                              | (U_PIERCE_WOOD if w.upgrades.get("kPierceWood") else 0) for w in workers])
    total = (ctypes.c_int * 4)()
    gain = (ctypes.c_int * max(1, 4 * nw))()
    cnt = (ctypes.c_int * max(1, ns))()
    points = (ctypes.c_int * max(1, ns))()
    harvested = (ctypes.c_int * max(1, ns))()
    from .harvest_sim import _game_bonus
    build_bonus = _arr(ctypes.c_int, [(_game_bonus(w.harvest_bonus, "kMoreBuildPts") or 0) for w in workers])
    from .harvest_sim import harvest_effects
    effects = [harvest_effects(w.upgrades, w.harvest_bonus) for w in workers]
    amounts = _arr(ctypes.c_int, [v for amount, _, _ in effects for v in amount])
    clocks = _arr(ctypes.c_int, [v for _, clock, _ in effects for v in clock])
    radii = _arr(ctypes.c_double, [radius for _, _, radius in effects])
    clock_counts = (ctypes.c_int * max(1, 4 * nw))()
    cap = max_events + nw + 8
    path = (ctypes.c_double * (4 * cap))()
    # 다음 사건의 시각·거리·법선과 충돌 대상을 작업자별로 저장한다.
    event_values = (ctypes.c_double * max(1, 4 * nw))()
    event_kinds = (ctypes.c_int * max(1, 2 * nw))()
    n = dll.bxp_simulate_team(pw.world, pw.n, pw.kind, pw.slot, pw.bid, pw.pt_off, pw.pt_cnt, pw.pts, pw.circ, pw.bb,
                              flags, rtype, res, nw, wk, ups, float(duration), int(max_events),
                              total, gain, cnt, path, cap, event_values, event_kinds, build_bonus, points,
                              amounts, clocks, radii, clock_counts, harvested)
    if n < 0:
        return None
    for i, w in enumerate(workers):
        w.x, w.y, w.dx, w.dy, w.speed, w.t = wk[6 * i:6 * i + 6]
        w.gain = list(gain[4 * i:4 * i + 4])
        w.path = []
    for k in range(n):
        wi = int(path[4 * k])
        workers[wi].path.append((path[4 * k + 1], path[4 * k + 2], path[4 * k + 3]))
    if counts is not None:
        for s, b in enumerate(pw.slot_ids):
            if cnt[s]:
                counts[b] = counts.get(b, 0) + cnt[s]
    if build_points is not None:
        for s, b in enumerate(pw.slot_ids):
            if points[s]:
                build_points[b] = build_points.get(b, 0) + points[s]
    if collected is not None:
        for s, b in enumerate(pw.slot_ids):
            if harvested[s]:
                collected[b] = collected.get(b, 0) + harvested[s]
    return list(total)
