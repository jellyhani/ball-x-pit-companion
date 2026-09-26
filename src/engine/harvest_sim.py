"""기지 채집 궤적 시뮬레이션 (게임 연동 1.6 의 기지 물리 모양 기준).

실제 게임 궤적(harvest_traces.jsonl)으로 확인한 규칙:
- 작업자는 발사대에서 조준 방향으로 날아가 채집 시간이 끝날 때까지 계속 튕긴다 (튕김 횟수 제한 없음).
- 반사는 축 방향: 부딪힌 면에 따라 x 나 y 방향 성분만 뒤집힌다 (원형 건물도 성분 하나만 뒤집힘).
- 속도는 튕길 때마다 0.2 씩 오른다 (캐릭터마다 시작 속도가 다르다: 5 또는 6 확인).
- 기지 네 벽(왼·오른·위·아래)에서도 튕긴다.
- 밀밭은 통과하며 채집한다 (튕기지 않음). 돌·나무 채집지는 자원이 남아 있으면 튕기며 채집하고,
  '돌 관통'·'나무 관통' 채집 강화가 있으면 통과하며 채집한다. 비어 있는 채집지는 누구나 통과한다
  (먼저 날아간 작업자가 비운 바위를 뒤 작업자가 통과 — 8명 궤적으로 확인). 그 밖의 건물은 튕긴다.
채집량 가정(확인 중): 채집할 수 있는 건물에 처음 닿으면 그 건물에 쌓인 자원을 가져간다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

EPS = 1e-6
SPEED_UP = 0.2


@dataclass
class Shape:
    bid: int                       # 건물 id
    kind: str                      # box | circle | poly
    pts: List[Tuple[float, float]] = field(default_factory=list)
    c: Tuple[float, float] = (0.0, 0.0)
    r: float = 0.0
    bb: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)   # 미리 계산한 경계 상자 (빠른 제외용)

    def __post_init__(self):
        self.bb = self.aabb()

    def aabb(self) -> Tuple[float, float, float, float]:
        if self.kind == "circle":
            return self.c[0] - self.r, self.c[1] - self.r, self.c[0] + self.r, self.c[1] + self.r
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class World:
    left: float
    right: float
    bottom: float
    top: float
    shapes: List[Shape]
    launcher: Tuple[float, float]
    radius: float = 0.07             # 작업자 반지름 (실제 궤적에 맞춘 값)
    worker_speed: float = 5.0        # 게임이 알려 준 기본 작업자 속도
    worker_speed_mult: float = 1.0  # 게임의 BuildingMgr.WorkerMoveSpeedMult (기지 강화 등에 따라 변함)


def world_from_geo(geo: dict, radius: float = 0.07) -> Optional[World]:
    try:
        shapes = []
        for c in geo.get("colliders") or []:
            if c.get("trigger"):
                continue
            if c.get("shape") == "circle":
                shapes.append(Shape(int(c["id"]), "circle", c=(float(c["c"][0]), float(c["c"][1])), r=float(c["r"])))
            elif c.get("pts"):
                kind = "box" if c.get("shape") in ("box", "bounds") else "poly"
                shapes.append(Shape(int(c["id"]), kind, pts=[(float(x), float(y)) for x, y in c["pts"]]))
        speed_mult = float(geo.get("worker_speed_mult") or 1.0)
        if not math.isfinite(speed_mult) or speed_mult <= 0:
            speed_mult = 1.0
        base_speed = float(geo.get("worker_speed") or 5.0 * speed_mult)
        if not math.isfinite(base_speed) or base_speed <= 0:
            base_speed = 5.0 * speed_mult
        return World(float(geo["left"]), float(geo["right"]), float(geo["bottom"]), float(geo["top"]), shapes,
                     (float(geo["launcher"][0]), float(geo["launcher"][1])), radius, base_speed, speed_mult)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _ray_segment(ox, oy, dx, dy, ax, ay, bx, by) -> Optional[Tuple[float, float, float]]:
    """광선과 선분 교차: (거리 t, 법선 x, 법선 y)."""
    ex, ey = bx - ax, by - ay
    den = dx * ey - dy * ex
    if abs(den) < EPS:
        return None
    t = ((ax - ox) * ey - (ay - oy) * ex) / den
    u = ((ax - ox) * dy - (ay - oy) * dx) / den
    if t <= EPS or u < -EPS or u > 1 + EPS:
        return None
    nx, ny = -ey, ex
    if nx * dx + ny * dy > 0:
        nx, ny = -nx, -ny
    return t, nx, ny


def _misses_box(bb, ox, oy, dx, dy, r) -> bool:
    """광선이 (r 만큼 넓힌) 경계 상자를 앞쪽에서 만나지 않으면 True — 모양별 정밀 계산 전 빠른 제외."""
    x0, y0, x1, y1 = bb[0] - r, bb[1] - r, bb[2] + r, bb[3] + r
    tmin, tmax = -1e18, 1e18
    for o, d, lo, hi in ((ox, dx, x0, x1), (oy, dy, y0, y1)):
        if abs(d) < EPS:
            if o < lo or o > hi:
                return True
            continue
        t1, t2 = (lo - o) / d, (hi - o) / d
        if t1 > t2:
            t1, t2 = t2, t1
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return True
    return tmax <= EPS


def _hit_shape(s: Shape, ox, oy, dx, dy, r) -> Optional[Tuple[float, float, float]]:
    if _misses_box(s.bb, ox, oy, dx, dy, r + 1e-3):
        return None
    if s.kind == "circle":
        cx, cy = s.c
        R = s.r + r
        fx, fy = ox - cx, oy - cy
        b = fx * dx + fy * dy
        c = fx * fx + fy * fy - R * R
        disc = b * b - c
        if disc < 0:
            return None
        t = -b - math.sqrt(disc)
        if t <= EPS:
            return None
        hx, hy = ox + dx * t - cx, oy + dy * t - cy
        return t, hx, hy
    pts = s.pts
    if s.kind == "box" and r > 0:
        x0, y0, x1, y1 = s.aabb()
        pts = [(x0 - r, y0 - r), (x1 + r, y0 - r), (x1 + r, y1 + r), (x0 - r, y1 + r)]
    best = None
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        h = _ray_segment(ox, oy, dx, dy, ax, ay, bx, by)
        if h and (best is None or h[0] < best[0]):
            best = h
    return best


@dataclass
class PathResult:
    points: List[Tuple[float, float]]           # 꺾이는 지점들 (월드 좌표)
    hits: List[Tuple[int, float]]               # (건물 id, 닿은 시각 초)


WHEAT_TYPES = {"kWheatField", "kDenseWheat"}


def passable_ids(buildings: Dict[int, dict], upgrades: Optional[dict] = None) -> set:
    """이 작업자가 통과하는 건물: 밀밭은 항상, 돌·나무는 관통 강화가 있을 때, 모든 건물은 건물 관통 강화."""
    from .harvest import RES_BY_TYPE
    ups = upgrades or {}
    out = set()
    for bid, b in buildings.items():
        t = b.get("type", "")
        res = RES_BY_TYPE.get(t)       # 관통은 건물 종류로 정해진다 (방금 채집해 비었어도 통과 — 실제 궤적 확인)
        if t in WHEAT_TYPES or ups.get("kPierceBuildings"):
            out.add(bid)
        elif res == 3 and ups.get("kPierceStone") and t not in ("kIdleStoneMine", "kStoneMine"):
            out.add(bid)
        elif res == 2 and ups.get("kPierceWood") and t not in ("kIdleLumberyard", "kLumberyard"):
            out.add(bid)
    return out


def simulate(world: World, angle_deg: float, duration: float, speed0: float = 5.0,
             pierce: Sequence[int] = (), max_bounces: int = 400) -> PathResult:
    """발사대에서 angle_deg(0=오른쪽, 90=위) 로 쏜 작업자 한 명의 경로. pierce: 통과하는 건물 id."""
    ox, oy = world.launcher
    dx, dy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    r = world.radius
    speed, t_now = speed0, 0.0
    pts = [(ox, oy)]
    hits: List[Tuple[int, float]] = []
    pierce = set(pierce)
    walls = [((world.left + r, -1e3), (world.left + r, 1e3)), ((world.right - r, -1e3), (world.right - r, 1e3)),
             ((-1e3, world.top - r), (1e3, world.top - r)), ((-1e3, world.bottom + r), (1e3, world.bottom + r))]
    for _ in range(max_bounces):
        best = None           # (t, nx, ny, shape or None)
        for i, ((ax, ay), (bx, by)) in enumerate(walls):
            # 벽은 안쪽에서 바깥으로 나갈 때만 튕긴다 (발사대는 아래 벽보다 아래에 있다 — 실제 궤적 확인)
            inside = (ox >= ax - 1e-6, ox <= ax + 1e-6, oy <= ay + 1e-6, oy >= ay - 1e-6)[i]
            toward = (dx < 0, dx > 0, dy > 0, dy < 0)[i]
            if not (inside and toward):
                continue
            h = _ray_segment(ox, oy, dx, dy, ax, ay, bx, by)
            if h and (best is None or h[0] < best[0]):
                best = (h[0], h[1], h[2], None)
        passed = []
        for s in world.shapes:
            h = _hit_shape(s, ox, oy, dx, dy, r)
            if not h:
                continue
            if s.bid in pierce:
                passed.append((h[0], s.bid))
            elif best is None or h[0] < best[0]:
                best = (h[0], h[1], h[2], s)
        if best is None:
            break
        t, nx, ny, s = best
        for tp, bid in sorted(passed):
            if tp < t and t_now + tp / speed < duration:
                hits.append((bid, t_now + tp / speed))
        dt = t / speed
        if t_now + dt >= duration:
            rest = (duration - t_now) * speed
            pts.append((ox + dx * rest, oy + dy * rest))
            break
        t_now += dt
        ox, oy = ox + dx * t, oy + dy * t
        pts.append((ox, oy))
        if s is not None:
            hits.append((s.bid, t_now))
        # 축 방향 반사: 법선의 큰 성분 쪽 방향만 뒤집는다 (실제 궤적 확인)
        if abs(nx) >= abs(ny):
            dx = -dx
        else:
            dy = -dy
        ox, oy = ox + dx * 1e-4, oy + dy * 1e-4
        speed += SPEED_UP
    return PathResult(pts, hits)


def yield_for(path_hits: Sequence[Tuple[int, float]], buildings: Dict[int, dict], taken: Optional[set] = None
              ) -> List[int]:
    """경로가 닿은 채집 가능 건물의 자원 합 [골드, 밀, 나무, 돌]. taken: 이미 다른 작업자가 가져간 건물."""
    from .harvest import building_resource
    out = [0, 0, 0, 0]
    taken = taken if taken is not None else set()
    for bid, _t in path_hits:
        b = buildings.get(bid)
        if b is None or bid in taken or not b.get("can_harvest") or not (b.get("res") or 0):
            continue
        res = building_resource(b)
        if res is None:
            continue
        taken.add(bid)
        out[res] += int(b["res"])
    return out


def sweep(world: World, buildings: Dict[int, dict], duration: float, need: int,
          angles: Sequence[float] = tuple(range(15, 166, 3)), speed0: float = 5.0
          ) -> List[Tuple[float, List[int], PathResult]]:
    """각도마다 한 명이 얻는 자원. need 자원이 많은 순."""
    out = []
    for a in angles:
        p = simulate(world, a, duration, speed0)
        out.append((a, yield_for(p.hits, buildings), p))
    out.sort(key=lambda t: (-t[1][need], -sum(t[1])))
    return out


@dataclass
class Worker:
    x: float
    y: float
    dx: float
    dy: float
    speed: float
    t: float                       # 이 작업자의 현재 시각 (채집 시작 기준 초)
    upgrades: dict = field(default_factory=dict)
    path: List[Tuple[float, float, float]] = field(default_factory=list)   # (x, y, t)
    gain: List[int] = field(default_factory=lambda: [0, 0, 0, 0])


def _team_tables(buildings: Dict[int, dict]):
    from .harvest import RES_BY_TYPE, building_resource
    res_left = {bid: int(b.get("res") or 0) if b.get("can_harvest") else 0 for bid, b in buildings.items()}
    rtype = {bid: (building_resource(b) if building_resource(b) is not None else RES_BY_TYPE.get(b.get("type", "")))
             for bid, b in buildings.items()}
    is_tile = {bid: b.get("type", "") in RES_BY_TYPE and b.get("type", "") not in
               ("kIdleFarm", "kIdleLumberyard", "kIdleStoneMine", "kStoneMine", "kLumberyard", "kFarm", "kGoldMine")
               for bid, b in buildings.items()}
    return res_left, rtype, is_tile


def simulate_team(world: World, buildings: Dict[int, dict], workers: List[Worker], duration: float,
                  max_events: int = 4000, counts: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    """여러 작업자를 시간 순서로 함께 돌린다 (한 명이 비운 채집지를 다른 작업자는 통과).

    채집: 자원이 남은 채집지에 처음 닿으면 쌓인 자원을 모두 가져간다(가정, 실제 채집량과 맞춰 보는 중).
    계산은 네이티브 모듈(native/bxp_native.cpp, 같은 계산 — 수십 배 빠름)로 하고, 없으면 파이썬으로 한다.
    """
    from . import native
    res_left, rtype, is_tile = _team_tables(buildings)

    def flags_of(bid: int):
        b = buildings.get(bid) or {}
        f = (native.F_WHEAT if b.get("type", "") in WHEAT_TYPES else 0) | (native.F_TILE if is_tile.get(bid) else 0)
        k = rtype.get(bid)
        return f, (-1 if k is None else int(k)), int(res_left.get(bid, 0))

    total = native.simulate_team(world, buildings, workers, duration, max_events, counts, flags_of)
    if total is not None:
        return total, workers
    return simulate_team_py(world, buildings, workers, duration, max_events, counts)


def simulate_team_py(world: World, buildings: Dict[int, dict], workers: List[Worker], duration: float,
                     max_events: int = 4000, counts: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    """simulate_team 의 파이썬 구현 (네이티브 모듈이 없을 때, 그리고 결과 비교 기준)."""
    res_left, rtype, is_tile = _team_tables(buildings)
    total = [0, 0, 0, 0]
    r = world.radius
    walls = [((world.left + r, -1e3), (world.left + r, 1e3)), ((world.right - r, -1e3), (world.right - r, 1e3)),
             ((-1e3, world.top - r), (1e3, world.top - r)), ((-1e3, world.bottom + r), (1e3, world.bottom + r))]
    for w in workers:
        w.path = [(w.x, w.y, w.t)]

    def blocks(bid: int, w: Worker) -> bool:
        b = buildings.get(bid) or {}
        t = b.get("type", "")
        if t in WHEAT_TYPES or w.upgrades.get("kPierceBuildings"):
            return False
        if is_tile.get(bid):
            if res_left.get(bid, 0) <= 0:
                return False
            kind = rtype.get(bid)
            if (kind == 3 and w.upgrades.get("kPierceStone")) or (kind == 2 and w.upgrades.get("kPierceWood")):
                return False
        return True

    def harvest(bid: int, w: Worker):
        n = res_left.get(bid, 0)
        kind = rtype.get(bid)
        if n > 0 and kind is not None:
            res_left[bid] = 0
            w.gain[kind] += n
            total[kind] += n

    for _ in range(max_events):
        live = [w for w in workers if w.t < duration]
        if not live:
            break
        w = min(live, key=lambda q: q.t)
        best = None
        for i, ((ax, ay), (bx, by)) in enumerate(walls):
            inside = (w.x >= ax - 1e-6, w.x <= ax + 1e-6, w.y <= ay + 1e-6, w.y >= ay - 1e-6)[i]
            toward = (w.dx < 0, w.dx > 0, w.dy > 0, w.dy < 0)[i]
            if inside and toward:
                h = _ray_segment(w.x, w.y, w.dx, w.dy, ax, ay, bx, by)
                if h and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], h[2], None)
        passed = []
        for sh in world.shapes:
            h = _hit_shape(sh, w.x, w.y, w.dx, w.dy, r)
            if not h:
                continue
            if blocks(sh.bid, w):
                if best is None or h[0] < best[0]:
                    best = (h[0], h[1], h[2], sh)
            else:
                passed.append((h[0], sh.bid))
        if best is None:
            w.t = duration
            break
        dist, nx, ny, sh = best
        # 한 번에 너무 멀리 가지 않게: 다른 작업자와 시간 순서를 맞추려고 최대 0.5초씩 나아간다
        step = min(dist, w.speed * 0.5)
        for tp, bid in sorted(passed):
            if tp <= step:
                harvest(bid, w)
        end_t = w.t + step / w.speed
        if end_t >= duration:
            rest = (duration - w.t) * w.speed
            w.x, w.y, w.t = w.x + w.dx * rest, w.y + w.dy * rest, duration
            w.path.append((w.x, w.y, w.t))
            continue
        w.x, w.y, w.t = w.x + w.dx * step, w.y + w.dy * step, end_t
        if step < dist:
            continue
        w.path.append((w.x, w.y, w.t))
        if sh is not None:
            harvest(sh.bid, w)
            if counts is not None:
                counts[sh.bid] = counts.get(sh.bid, 0) + 1     # 건물에 부딪힌 횟수 (미완성 건물 건설용)
        if abs(nx) >= abs(ny):
            w.dx = -w.dx
        else:
            w.dy = -w.dy
        w.x, w.y = w.x + w.dx * 1e-4, w.y + w.dy * 1e-4
        w.speed += SPEED_UP
    return total, workers


# ---- 월드 ↔ 화면 (플러그인이 보낸 기지 모서리 대응점으로 호모그래피) ----
def homography(proj: Sequence[Sequence[float]]):
    """[[월드x, 월드y, 화면x, 화면y], ...] (4개 이상) → 3x3 행렬. 실패하면 None."""
    import numpy as np
    if not proj or len(proj) < 4:
        return None
    rows = []
    for wx, wy, sx, sy in proj:
        rows.append([wx, wy, 1, 0, 0, 0, -sx * wx, -sx * wy, -sx])
        rows.append([0, 0, 0, wx, wy, 1, -sy * wx, -sy * wy, -sy])
    _, _, vt = np.linalg.svd(np.array(rows, dtype=float))
    h = vt[-1].reshape(3, 3)
    return h / h[2, 2]


def to_screen(h, x: float, y: float) -> Tuple[float, float]:
    v = h @ [x, y, 1.0]
    return v[0] / v[2], v[1] / v[2]


# ---- 팀 구성과 각도 추천 ----
LAUNCH_GAP = 0.26        # 작업자 발사 간격 (초, 실제 궤적 평균)


def team_from_chars(chars_raw: Sequence[dict], order: Optional[Sequence[str]] = None) -> List[dict]:
    """발사될 작업자: 캐릭터 채집 강화, 시작 속도(채집 속도 강화 +1 — 실제 궤적에서 5·6 확인).
    건물에서 일하는 캐릭터(state kWorking)는 뺀다 — 실제 궤적 8건에서 13명 중 동시에 날아간 수가 10~11명
    (= 13 − 건물 일꾼 2~3명, harvest_traces.jsonl 2026-09-25)."""
    chars = [c for c in chars_raw if c.get("type") and c.get("state") != "kWorking"]
    if order:
        rank = {t: i for i, t in enumerate(order)}
        chars.sort(key=lambda c: rank.get(c["type"], 99))
    out = []
    for c in chars:
        ups = c.get("harvest") or {}
        out.append({"type": c["type"], "upgrades": ups, "speed": 5.0 + (1.0 if ups.get("kHarvestSpeed") else 0.0)})
    return out


def run_angle(world: World, buildings: Dict[int, dict], team: Sequence[dict], angle: float,
              duration: float, counts: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    lx, ly = world.launcher
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    ws = [Worker(lx, ly, dx, dy, world.worker_speed + (m["speed"] - 5.0) * world.worker_speed_mult,
                 i * LAUNCH_GAP, dict(m["upgrades"]))
          for i, m in enumerate(team)]
    return simulate_team(world, buildings, ws, duration, counts=counts)


@dataclass
class AngleResult:
    angle: float
    total: List[int]                         # [골드, 밀, 나무, 돌]
    build_hits: int = 0                      # 미완성 건물에 맞은 횟수 (건물마다 더 필요한 만큼까지만 셈)
    per_building: Dict[int, int] = field(default_factory=dict)


def rank_angles(world: World, buildings: Dict[int, dict], team: Sequence[dict], duration: float, need: int,
                targets: Optional[Dict[int, int]] = None,
                angles: Sequence[float] = tuple(range(12, 169, 3))) -> List[AngleResult]:
    """각도별 예상 결과. 미완성 건물 건설(targets: 건물 id → 더 필요한 타격 수)이 먼저, 그다음 필요한 자원,
    다른 자원은 4분의 1 가중."""
    targets = targets or {}
    out = []
    for a in angles:
        counts: Dict[int, int] = {}
        total, _ = run_angle(world, buildings, team, a, duration, counts)
        per = {bid: min(counts.get(bid, 0), cap) for bid, cap in targets.items() if counts.get(bid)}
        out.append(AngleResult(a, total, sum(per.values()), per))
    out.sort(key=lambda r: -(100 * r.build_hits + r.total[need] + 0.25 * (sum(r.total) - r.total[need])))
    return out


def best_angles(world: World, buildings: Dict[int, dict], team: Sequence[dict], duration: float, need: int,
                angles: Sequence[float] = tuple(range(12, 169, 3))) -> List[Tuple[float, List[int]]]:
    """각도별 팀 전체 예상 채집량. 필요한 자원 우선, 다른 자원은 4분의 1 가중."""
    return [(r.angle, r.total) for r in rank_angles(world, buildings, team, duration, need, None, angles)]
