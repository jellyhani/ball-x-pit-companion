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

F_WHEAT, F_TILE = 1, 2
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
        if dll.bxp_version() != 1:
            return None
        P = ctypes.POINTER
        d, i = ctypes.c_double, ctypes.c_int
        dll.bxp_simulate_team.restype = i
        dll.bxp_simulate_team.argtypes = [P(d), i, P(i), P(i), P(i), P(i), P(i), P(d), P(d), P(d), P(i), P(i), P(i),
                                          i, P(d), P(i), d, i, P(i), P(i), P(i), P(d), i, P(d), P(i)]
        _lib = dll
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
        self.tmp_t = (ctypes.c_double * max(1, self.n))()
        self.tmp_i = (ctypes.c_int * max(1, 2 * self.n))()


def simulate_team(world, buildings: Dict[int, dict], workers: list, duration: float, max_events: int,
                  counts: Optional[Dict[int, int]], flags_of) -> Optional[List[int]]:
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
    cap = max_events + nw + 8
    path = (ctypes.c_double * (4 * cap))()
    n = dll.bxp_simulate_team(pw.world, pw.n, pw.kind, pw.slot, pw.bid, pw.pt_off, pw.pt_cnt, pw.pts, pw.circ, pw.bb,
                              flags, rtype, res, nw, wk, ups, float(duration), int(max_events),
                              total, gain, cnt, path, cap, pw.tmp_t, pw.tmp_i)
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
    return list(total)
