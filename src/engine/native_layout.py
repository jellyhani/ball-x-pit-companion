"""배치 최적화 네이티브 계산 (bxp_native.dll 의 bxp_layout_*) — layout_opt 의 Layout·Scorer 를 배열로 넘긴다.

점수 계산은 Scorer.score 와 같은 값(합 순서만 달라 1e-9 안쪽). 담금질·마무리는 같은 규칙, 난수만 다르다.
DLL 이 없으면 모든 함수가 None — layout_opt 는 파이썬으로 계산한다.
"""
from __future__ import annotations

import ctypes
import math
from typing import Optional

from .native import _arr, lib

_P = ctypes.POINTER
_I, _D, _U8 = ctypes.c_int, ctypes.c_double, ctypes.c_ubyte


class _Model(ctypes.Structure):
    _fields_ = [("gx0", _I), ("gy0", _I), ("gw", _I), ("gh", _I), ("tile", _P(_U8)),
                ("ox", _D), ("oy", _D), ("size", _D), ("square", _I),
                ("n", _I), ("pw", _P(_I)), ("ph", _P(_I)), ("movable", _P(_I)),
                ("rel_off", _P(_I)), ("rel_cnt", _P(_I)), ("rel", _P(_I)), ("origin0", _P(_I)),
                ("lane", _P(_D)), ("lane_idle", _P(_I)), ("lane_tile", _P(_D)), ("lane_w", _D), ("lane_tile_w", _D),
                ("n_spots", _I), ("spots", _P(_I)), ("is_preset", _P(_I)), ("preset_w", _D), ("preset_clear", _D),
                ("m", _I), ("eff_piece", _P(_I)), ("eff_rr", _P(_D)), ("eff_r2", _P(_D)), ("eff_mode", _P(_I)),
                ("eff_group", _P(_I)), ("eff_off", _P(_I)), ("eff_cnt", _P(_I)), ("tgt", _P(_I)), ("tgt_val", _P(_D)),
                ("harvest_cap", _I), ("n_groups", _I),
                ("part_off", _P(_I)), ("part_cnt", _P(_I)), ("part_piece", _P(_I)), ("part_r", _P(_D)),
                ("sz_off", _P(_I)), ("sz_cnt", _P(_I)), ("sz_org", _P(_I)),
                ("range_off",_P(_I)),("range_cnt",_P(_I)),("range_boxes",_P(_D)),("range_pad",_D)]


class _Work(ctypes.Structure):
    _fields_ = [("occ", _P(_I)), ("cx", _P(_D)), ("cy", _P(_D)), ("regen", _P(_D)), ("regen_set", _P(_U8)),
                ("h1", _P(_D)), ("h2", _P(_D)), ("h_set", _P(_U8)), ("stamp", _P(_I)), ("tmp_ids", _P(_I)),
                ("undo", _P(_I)), ("best_org", _P(_I)), ("cand", _P(_I)),
                ("rng", ctypes.c_uint64), ("stamp_gen", _I)]


_TPS: Optional[float] = None


def _ticks_per_second(dll) -> float:
    """CPU 시간 카운터(rdtsc) 틱/초 — 한 번 잰다 (DLL 은 C 런타임·시계 함수를 쓰지 않는다)."""
    global _TPS
    if _TPS is None:
        import time
        t0, k0 = time.perf_counter(), dll.bxp_ticks()
        time.sleep(0.05)
        t1, k1 = time.perf_counter(), dll.bxp_ticks()
        _TPS = max(1.0, (k1 - k0) / max(1e-6, t1 - t0))
    return _TPS


def _lib():
    dll = lib()
    if dll is None or not hasattr(dll, "bxp_layout_anneal"):
        return None
    if not getattr(dll, "_layout_ready", False):
        dll.bxp_ticks.restype = ctypes.c_uint64
        dll.bxp_ticks.argtypes = []
        dll.bxp_layout_score.restype = _D
        dll.bxp_layout_score.argtypes = [_P(_Model), _P(_Work), _P(_I)]
        dll.bxp_layout_anneal.restype = _D
        dll.bxp_layout_anneal.argtypes = [_P(_Model), _P(_Work), _P(_I), _D, _D, _D, ctypes.c_uint64, ctypes.c_uint64,
                                          _P(_I)]
        dll.bxp_layout_polish.restype = _D
        dll.bxp_layout_polish.argtypes = [_P(_Model), _P(_Work), _P(_I), _D, ctypes.c_uint64]
        dll._layout_ready = True
    return dll


class LayoutModel:
    """Layout + Scorer → DLL 에 넘길 배열. 배열은 이 객체가 붙잡고 있어야 한다."""

    def __init__(self, lay, scorer, origin0):
        from . import layout_opt as lo
        keep = self._keep = []

        def a(ctype, values):
            arr = _arr(ctype, list(values))
            keep.append(arr)
            return arr

        ids = list(lay.pieces)
        self.ids = ids
        ix = {i: k for k, i in enumerate(ids)}
        P = [lay.pieces[i] for i in ids]
        g = lay.grid
        cols = [c for c, _ in g.tiles]
        rows = [r for _, r in g.tiles]
        gx0, gy0 = min(cols), min(rows)
        gw, gh = max(cols) - gx0 + 1, max(rows) - gy0 + 1
        tile = [0] * (gw * gh)
        for c, r in g.tiles:
            tile[(r - gy0) * gw + (c - gx0)] = 1
        M = self.model = _Model()
        M.gx0, M.gy0, M.gw, M.gh = gx0, gy0, gw, gh
        M.tile = a(_U8, tile)
        M.ox, M.oy, M.size = g.ox, g.oy, g.size
        M.square = 1 if lo.RANGE_SHAPE == "square" else 0
        M.n = len(ids)
        M.pw, M.ph = a(_I, [p.w for p in P]), a(_I, [p.h for p in P])
        M.movable = a(_I, [1 if p.movable else 0 for p in P])
        ro,rc,boxes=[],[],[]
        for p in P:
            ro.append(len(boxes)//4)
            rc.append(-1 if p.range_boxes is None else len(p.range_boxes))
            boxes.extend(v for box in (p.range_boxes or ()) for v in box)
        M.range_off,M.range_cnt,M.range_boxes=a(_I,ro),a(_I,rc),a(_D,boxes)
        M.range_pad=scorer.pad
        off, cnt, rel = [], [], []
        for p in P:
            off.append(len(rel) // 2)
            cells = sorted(p.rel)
            cnt.append(len(cells))
            for dx, dy in cells:
                rel += [dx, dy]
        M.rel_off, M.rel_cnt, M.rel = a(_I, off), a(_I, cnt), a(_I, rel)
        M.origin0 = a(_I, [v for i in ids for v in origin0.get(i, lay.origin[i])])
        if scorer.lane:
            lane = [0.0] * (gw * gh)
            for (c, r), v in scorer.lane.items():
                if gx0 <= c < gx0 + gw and gy0 <= r < gy0 + gh:
                    lane[(r - gy0) * gw + (c - gx0)] = v
            M.lane = a(_D, lane)
            idle, tiles = set(scorer.lane_ids), set(scorer.lane_tiles)
            M.lane_idle = a(_I, [1 if i in idle else 0 for i in ids])
            M.lane_tile = a(_D, [scorer.lane_weight.get(i, 0.0) for i in ids])
        else:
            M.lane = ctypes.cast(None, _P(_D))
            M.lane_idle = a(_I, [0] * len(ids))
            M.lane_tile = a(_D, [0.0] * len(ids))
        M.lane_w, M.lane_tile_w = lo.LANE_W, lo.LANE_TILE_W
        spots = sorted(scorer.preset_spots)
        M.n_spots = len(spots)
        M.spots = a(_I, [v for s in spots for v in s])
        M.is_preset = a(_I, [1 if p.type == scorer.preset_type else 0 for p in P])
        M.preset_w, M.preset_clear = lo.PRESET_W, lo.PRESET_CLEAR
        # 효과: 대상별 값을 미리 곱해 둔다 (Scorer.score 와 같은 식)
        mode_of = {"count": 0, "regen": 1, "harvest": 2}
        groups: dict = {}
        e_piece, e_rr, e_r2, e_mode, e_group, e_off, e_cnt, tgt, val = [], [], [], [], [], [], [], [], []
        for e in scorer.effects:
            p = lay.pieces[e]
            kind, w, mode, _, _ = lo.EFFECTS[p.type]
            rr = p.range + scorer.pad
            e_piece.append(ix[e])
            e_rr.append(rr)
            e_r2.append(rr * rr)
            e_mode.append(mode_of[mode])
            e_group.append(groups.setdefault(p.type, len(groups)) if mode == "regen" else 0)
            e_off.append(len(tgt))
            ts = scorer.targets[kind]
            e_cnt.append(len(ts))
            for t in ts:
                v = w * p.factor * (scorer.res_weight.get(kind, 1.0) * lay.pieces[t].cap if isinstance(kind, int) else 1.0)
                if kind == "build" and lay.pieces[t].unfinished:
                    v *= lo.UNFINISHED_BUILD_W
                if kind in lo.HUB_KINDS:
                    v *= scorer.hub_w
                tgt.append(ix[t])
                val.append(v)
        M.m = len(e_piece)
        M.eff_piece, M.eff_rr, M.eff_r2 = a(_I, e_piece), a(_D, e_rr), a(_D, e_r2)
        M.eff_mode, M.eff_group, M.eff_off, M.eff_cnt = a(_I, e_mode), a(_I, e_group), a(_I, e_off), a(_I, e_cnt)
        M.tgt, M.tgt_val = a(_I, tgt), a(_D, val)
        M.harvest_cap, M.n_groups = lo.HARVEST_CAP, max(1, len(groups))
        # 담금질 '관련 자리' 상대 (_near_spots 와 같은 목록)
        p_off, p_cnt, p_piece, p_r = [], [], [], []
        for i in ids:
            p = lay.pieces[i]
            parts = []
            kind = lo.TILE_RES.get(p.type)
            for e in scorer.effects:
                ek = lo.EFFECTS[lay.pieces[e].type][0]
                if e != i and (ek == kind or ek == "all"):
                    parts.append((ix[e], lay.pieces[e].range))
            if p.type in lo.EFFECTS:
                for t in scorer.targets[lo.EFFECTS[p.type][0]]:
                    if t != i:
                        parts.append((ix[t], p.range))
            p_off.append(len(p_piece))
            p_cnt.append(len(parts))
            for q, r in parts:
                p_piece.append(q)
                p_r.append(r)
        M.part_off, M.part_cnt, M.part_piece, M.part_r = a(_I, p_off), a(_I, p_cnt), a(_I, p_piece), a(_D, p_r)
        # 크기별 가능한 자리 (담금질은 건물 크기 + 0~2 영역도 쓴다)
        sz_off, sz_cnt, sz_org = [0] * 1024, [0] * 1024, []
        sizes = {(p.w + dw, p.h + dh) for p in P if p.movable for dw in range(3) for dh in range(3)}
        for w, h in sorted(sizes):
            if w >= 32 or h >= 32:
                continue
            k = w * 32 + h
            sz_off[k] = len(sz_org) // 2
            n = 0
            for c, r in sorted(g.tiles):
                if all((c + dx, r + dy) in g.tiles for dx in range(w) for dy in range(h)):
                    sz_org += [c, r]
                    n += 1
            sz_cnt[k] = n
        M.sz_off, M.sz_cnt, M.sz_org = a(_I, sz_off), a(_I, sz_cnt), a(_I, sz_org)
        # 작업 공간
        n = len(ids)
        W = self.work = _Work()
        W.occ = a(_I, [0] * (gw * gh))
        W.cx, W.cy = a(_D, [0.0] * n), a(_D, [0.0] * n)
        W.regen, W.regen_set = a(_D, [0.0] * (M.n_groups * n)), a(_U8, [0] * (M.n_groups * n))
        W.h1, W.h2, W.h_set = a(_D, [0.0] * n), a(_D, [0.0] * n), a(_U8, [0] * n)
        W.stamp, W.tmp_ids = a(_I, [0] * n), a(_I, [0] * (2 * n))
        W.undo, W.best_org = a(_I, [0] * (3 * n)), a(_I, [0] * (2 * n))
        W.cand = a(_I, [0] * (2 * gw * gh))

    def org(self, origin: dict):
        return _arr(_I, [v for i in self.ids for v in origin[i]])

    def to_dict(self, arr) -> dict:
        return {i: (arr[2 * k], arr[2 * k + 1]) for k, i in enumerate(self.ids)}


def score(lay, scorer) -> Optional[float]:
    """Scorer.score 의 총점과 같은 값 (비교·검증용)."""
    dll = _lib()
    if dll is None or not lay.pieces:
        return None
    lm = LayoutModel(lay, scorer, lay.origin)
    return dll.bxp_layout_score(ctypes.byref(lm.model), ctypes.byref(lm.work), lm.org(lay.origin))


def anneal(lay, scorer, seconds: float, seed: int, t0: float, t1: float, origin0: dict, cost: float):
    """layout_opt.anneal 과 같은 담금질. (가장 좋았던 배치, 목표값, 반복 수) 또는 None."""
    dll = _lib()
    if dll is None or not lay.pieces:
        return None
    lm = LayoutModel(lay, scorer, origin0)
    org = lm.org(lay.origin)
    iters = _I(0)
    best = dll.bxp_layout_anneal(ctypes.byref(lm.model), ctypes.byref(lm.work), org, cost, t0, math.log(t1 / t0),
                                 int(seconds * _ticks_per_second(dll)), (seed & 0xFFFFFFFFFFFFFFFF) or 1,
                                 ctypes.byref(iters))
    return lm.to_dict(org), best, iters.value


def polish(lay, scorer, origin0: dict, seconds: float, cost: float):
    """layout_opt.polish 와 같은 마무리. (배치, 목표값) 또는 None."""
    dll = _lib()
    if dll is None or not lay.pieces:
        return None
    lm = LayoutModel(lay, scorer, origin0)
    org = lm.org(lay.origin)
    cur = dll.bxp_layout_polish(ctypes.byref(lm.model), ctypes.byref(lm.work), org, cost,
                                int(seconds * _ticks_per_second(dll)))
    return lm.to_dict(org), cur
