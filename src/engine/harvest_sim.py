"""기지 채집 궤적 시뮬레이션 (게임 연동 1.6 의 기지 물리 모양 기준).

게임 1.301 실행 코드(BaseMgr.MoveBalls, 0x4596E0)와 대조한 규칙:
- 작업자는 발사대에서 조준 방향으로 날아가 채집 시간이 끝날 때까지 계속 튕긴다 (튕김 횟수 제한 없음).
- 반사는 충돌 법선으로 계산한다. 원형·비스듬한 면에서도 방향 벡터 전체가 변한다.
- 속도는 튕길 때마다 0.2 씩 오르며 100을 넘지 않는다.
- 기지 네 벽(왼·오른·위·아래)에서도 튕긴다.
- 밀밭은 통과하며 채집한다 (튕기지 않음). 돌·나무 채집지는 자원이 남아 있으면 튕기며 채집하고,
  '돌 관통'·'나무 관통' 채집 강화가 있으면 통과하며 채집한다. 비어 있는 채집지는 누구나 통과한다
  (먼저 날아간 작업자가 비운 바위를 뒤 작업자가 통과 — 8명 궤적으로 확인). 그 밖의 건물은 튕긴다.
채집량·시간·범위는 게임 1.301의 실제 메서드를 확인해 캐릭터별 강화 값을 반영한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

EPS = 1e-6
SPEED_UP = 0.2
MAX_SPEED = 100.0
LAUNCH_GAP = 0.3                    # 1.301 _LaunchWorkers의 게임 시간 간격. 입력값을 우선한다.


def reflected(dx, dy, nx, ny):
    """MoveBalls 0x45A2DF: d - 2(d·n)n, 이후 Vector2.normalized."""
    norm2 = nx * nx + ny * ny
    if norm2 <= 0:
        return dx, dy
    scale = 2.0 * (dx * nx + dy * ny) / norm2
    rx, ry = dx - scale * nx, dy - scale * ny
    length = math.sqrt(rx * rx + ry * ry)
    return (rx / length, ry / length) if length > 0 else (dx, dy)


@dataclass
class Shape:
    bid: int                       # 건물 id
    kind: str                      # box | circle | poly | wall (미개방 구역)
    pts: List[Tuple[float, float]] = field(default_factory=list)
    c: Tuple[float, float] = (0.0, 0.0)
    r: float = 0.0
    bb: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)   # 미리 계산한 경계 상자 (빠른 제외용)
    environment: bool = False       # 실제 정적 벽: 모양과 관통 불가 성질을 분리한다.

    @property
    def is_wall(self):
        return self.environment or self.kind == "wall"

    def __post_init__(self):
        if self.kind in ('box','wall') and any(abs(ax-bx)>EPS and abs(ay-by)>EPS
                for (ax,ay),(bx,by) in zip(self.pts,self.pts[1:]+self.pts[:1])):
            # 회전한 BoxCollider의 AABB는 내부 판정에 쓸 수 없다. 실제 꼭짓점을 유지한다.
            self.environment=self.environment or self.kind=='wall'
            self.kind='poly'
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
    radius: float = 0.0              # 게임 이동은 Physics2D.Raycast. 채집 원의 반지름과 구분한다.
    worker_speed: float = 5.0        # 게임이 알려 준 기본 작업자 속도
    worker_speed_mult: float = 1.0  # 게임의 BuildingMgr.WorkerMoveSpeedMult (기지 강화 등에 따라 변함)
    launch_gap: float = LAUNCH_GAP
    use_bounds: bool = True
    roads: tuple = ()
    road_speed_mult: float = 1.0
    model_notes: set = field(default_factory=set)
    task_clock: Optional[tuple] = None
    automatic_gain: List[int] = field(default_factory=lambda:[0,0,0,0])


def _chunk_walls(geo: dict) -> List[Shape]:
    """게임의 구매 구역 사이에 남은 덮개를 벽으로 복원한다.

    게임 1.301 level2의 Chunk_Base_Cover: BoxCollider2D 9×6.75, trigger=False.
    BaseGridMgr.RefreshChunkCovers(0x44D130)는 미구매 덮개를 켠다.
    위치는 연동의 구역 크기·구매 좌표·기지 경계로 구성한다. 외곽 네 벽은 기존 판정을 유지한다.
    """
    raw = geo.get("chunks")
    if not raw:
        return []  # 구역 정보가 없는 구형 입력.
    chunks = {tuple(c) for c in raw}
    if any(len(c) != 2 or any(type(v) is not int or v < 0 for v in c) for c in chunks):
        raise ValueError("잘못된 구역 좌표")
    width = geo.get("chunk_world_w")
    height = geo.get("chunk_world_h")
    if width is None:
        width = geo.get("chunk_w", 0) * geo.get("space_w", 0)
    if height is None:
        height = geo.get("chunk_h", 0) * geo.get("space_h", geo.get("space_w", 0))
    width, height = float(width), float(height)
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise ValueError("구역 크기 없음")
    x0, y0 = min(c[0] for c in chunks), min(c[1] for c in chunks)
    x1, y1 = max(c[0] for c in chunks), max(c[1] for c in chunks)
    walls = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            if (x, y) in chunks:
                continue
            left = float(geo["left"]) + (x - x0) * width
            bottom = float(geo["bottom"]) + (y - y0) * height
            walls.append(Shape(-1000000-len(walls), "wall",
                               pts=[(left, bottom), (left+width, bottom),
                                    (left+width, bottom+height), (left, bottom+height)]))
    return walls


def world_from_geo(geo: dict, radius: float = 0.0) -> Optional[World]:
    try:
        if geo.get('physics_error'):
            return None
        shapes = []
        for c in geo.get("colliders") or []:
            if c.get("trigger"):
                continue
            if c.get("shape") == "circle":
                shapes.append(Shape(int(c["id"]), "circle", c=(float(c["c"][0]), float(c["c"][1])), r=float(c["r"])))
            elif c.get("pts"):
                kind = "box" if c.get("shape") in ("box", "bounds") else "poly"
                shapes.append(Shape(int(c["id"]), kind, pts=[(float(x), float(y)) for x, y in c["pts"]]))
        environment = geo.get("environment")
        if isinstance(environment,dict) and (environment.get('queries_start_in_colliders') is True
                                           or environment.get('queries_hit_triggers') is True):
            raise ValueError('검증한 게임 물리 질의 설정과 다름')
        exact = isinstance(environment, dict) and isinstance(environment.get("walls"), list)
        if geo.get('physics_contract',0)>=2 and not exact:
            return None
        if exact:
            for i, c in enumerate(environment['walls']):
                if c.get('unsupported'):
                    raise ValueError('미지원 실제 벽 모양')
                bid = -2000000-i
                if c.get('shape') == 'circle':
                    shapes.append(Shape(bid,'circle',c=tuple(c['c']),r=float(c['r']),environment=True))
                else:
                    for pts in c.get('paths',[]):
                        if len(pts)<2:
                            raise ValueError('실제 벽 꼭짓점 없음')
                        shapes.append(Shape(bid,c['shape'],pts=[tuple(p) for p in pts],environment=True))
        else:
            shapes.extend(_chunk_walls(geo))
        speed_mult = float(geo.get("worker_speed_mult") or 1.0)
        if not math.isfinite(speed_mult) or speed_mult <= 0:
            speed_mult = 1.0
        base_speed = float(geo.get("worker_speed") or 5.0 * speed_mult)
        if not math.isfinite(base_speed) or base_speed <= 0:
            base_speed = 5.0 * speed_mult
        gap = float(geo.get("ball_time_dist", LAUNCH_GAP))
        if not math.isfinite(gap) or gap <= 0:
            return None
        roads = tuple(tuple(float(v) for v in box) for box in (environment or {}).get('roads',()))
        road_mult = float((environment or {}).get('road_speed_mult',1.0))
        if (not math.isfinite(road_mult) or road_mult <= 0 or any(len(box)!=4 or
                not all(math.isfinite(v) for v in box) or box[0]>=box[2] or box[1]>=box[3] for box in roads)):
            return None
        return World(float(geo["left"]), float(geo["right"]), float(geo["bottom"]), float(geo["top"]), shapes,
                     (float(geo["launcher"][0]), float(geo["launcher"][1])), radius, base_speed, speed_mult, gap,
                     not exact, roads, road_mult,task_clock=(
                         float(geo['world_tick_progress']),float(geo['world_tick_interval']),float(geo['game_speed']))
                     if all(k in geo for k in ('world_tick_progress','world_tick_interval','game_speed')) else None)
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
    # 게임 globalgamemanagers: Physics2D.queriesStartInColliders=false.
    # 건물 중심에서 생성되는 추가 작업자가 소유 건물에 즉시 반사하지 않는다.
    if _inside_shape(s,ox,oy,r):
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
    if s.kind in ("box", "wall") and r > 0:
        x0, y0, x1, y1 = s.aabb()
        # OverlapCircle의 모서리는 사각 확장이 아니라 원호다.
        hits=[]
        for ax,ay,bx,by in ((x0,y0-r,x1,y0-r),(x1+r,y0,x1+r,y1),
                           (x1,y1+r,x0,y1+r),(x0-r,y1,x0-r,y0)):
            h=_ray_segment(ox,oy,dx,dy,ax,ay,bx,by)
            if h:hits.append(h)
        for cx,cy,sx,sy in ((x0,y0,-1,-1),(x1,y0,1,-1),(x1,y1,1,1),(x0,y1,-1,1)):
            fx,fy=ox-cx,oy-cy
            b=fx*dx+fy*dy
            disc=b*b-(fx*fx+fy*fy-r*r)
            if disc<0:continue
            t=-b-math.sqrt(disc)
            nx,ny=ox+dx*t-cx,oy+dy*t-cy
            if t>EPS and sx*nx>=-EPS and sy*ny>=-EPS:
                hits.append((t,nx,ny))
        return min(hits,key=lambda h:h[0]) if hits else None
    best = None
    n = len(pts)
    for i in range(n if s.kind != 'edge' else n-1):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % n]
        h = _ray_segment(ox, oy, dx, dy, ax, ay, bx, by)
        if h and (best is None or h[0] < best[0]):
            best = h
    return best


def _inside_shape(s: Shape, x: float, y: float, radius: float) -> bool:
    if s.kind == 'edge':
        return False
    """통과 타일의 출구를 새 채집으로 세지 않기 위한 내부 판정."""
    if s.kind == "circle":
        return (x - s.c[0]) ** 2 + (y - s.c[1]) ** 2 < (s.r + radius) ** 2
    if s.kind in ("box", "wall"):
        x0, y0, x1, y1 = s.bb
        if radius>0:
            dx,dy=max(x0-x,0.,x-x1),max(y0-y,0.,y-y1)
            return dx*dx+dy*dy<=radius*radius
        return x0 - radius < x < x1 + radius and y0 - radius < y < y1 + radius
    inside = False
    for (ax, ay), (bx, by) in zip(s.pts, s.pts[1:] + s.pts[:1]):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


@dataclass
class PathResult:
    points: List[Tuple[float, float]]           # 꺾이는 지점들 (월드 좌표)
    hits: List[Tuple[int, float]]               # (건물 id, 닿은 시각 초)


WHEAT_TYPES = {"kWheatField", "kDenseWheat"}
# 자원 종류 안내 표에는 거처도 있으므로 충돌 성질은 실제 자원 타일만 따로 센다.
from .construction_policy import RESOURCE_TILE_TYPES


def resource_tile(building):
    value=building.get('is_resource')
    return value if type(value) is bool else building.get('type') in RESOURCE_TILE_TYPES


def resource_kind(building):
    if 'resource_type' in building:
        value=building['resource_type']
        return value if type(value) is int and 0<=value<4 else None
    from .harvest import building_resource
    return building_resource(building)
CONSTRUCTION_STATES = {"kScaffold", "kUpgrading"}


def passable_ids(buildings: Dict[int, dict], upgrades: Optional[dict] = None) -> set:
    """MoveBalls의 자원 관통과 비자원 건물 관통은 서로 다른 분기다."""
    from .harvest import RES_BY_TYPE
    ups = upgrades or {}
    out = set()
    for bid, b in buildings.items():
        t = b.get("type", "")
        res = resource_kind(b)
        if ups.get("kPierceBuildings") and not resource_tile(b):
            out.add(bid)
        elif resource_tile(b) and (res==1 or b.get('can_harvest') is False):
            out.add(bid)
        elif res == 3 and ups.get("kPierceStone") and resource_tile(b):
            out.add(bid)
        elif res == 2 and ups.get("kPierceWood") and resource_tile(b):
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
    if not world.use_bounds:
        walls = []
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
            if not s.is_wall and s.bid in pierce:
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
        if s is not None and not s.is_wall:
            hits.append((s.bid, t_now))
        dx, dy = reflected(dx, dy, nx, ny)
        ox, oy = ox + dx * 1e-4, oy + dy * 1e-4
        speed = min(MAX_SPEED, speed + SPEED_UP)
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
    harvest_bonus: dict = field(default_factory=dict)    # 게임 GetHarvestUpgradeBonusAmt/GetBonusAmt 원값
    owner_id: Optional[int] = None
    bounces: int = 0
    active: bool = True
    touching: set = field(default_factory=set)
    just_bounced: bool = False


def _game_bonus(bonuses: dict, key: str) -> Optional[int]:
    value = bonuses.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def harvest_effects(upgrades: dict, bonuses: dict):
    """1.301 게임 본문 확인: Harvest=1+강화 레벨, 시간 보너스=GetBonusAmt*0.2초.

    BuildingInst.Harvest RVA 464990, BaseGridMgr.WorkerHarvestResource 4552C0,
    BallObj.InitWorker 5B7360. 범위는 기본 0.125 + 긴 낫 레벨*0.5.
    """
    levels = lambda key: max(0, int(upgrades.get(key) or 0))
    amounts = [1] + [1 + levels("kFaster" + resource) for resource in ("Wheat", "Wood", "Stone")]
    clock = [0] + [(_game_bonus(bonuses, "k" + resource + "Time")
                    if _game_bonus(bonuses, "k" + resource + "Time") is not None
                    else levels("k" + resource + "Time")) for resource in ("Wheat", "Wood", "Stone")]
    return amounts, clock, .125 + .5 * levels("kWheatRange")


def initial_harvest_duration(base: dict, team: Sequence[dict], fallback=16.0) -> float:
    """BaseMgr.InitWorkerMode(458670): 건물 기본 시간 + 작업자마다 1초 + 시간 엄수 보너스.
    조준 중 게임이 보낸 시계에는 이미 이 보너스가 들어 있으므로 중복 가산하지 않는다.
    """
    current = base.get("harvest_secs_left")
    if base.get("state") == "kAimWorkers" and isinstance(current, (int, float)) and current > 0:
        return float(current)
    basic = (base.get("geo") or {}).get("harvest_len")
    if not isinstance(basic, (int, float)) or basic <= 0:
        return float(fallback)
    return float(basic) + len(team) + sum(
        _game_bonus(m.get("harvest_bonus") or {}, "kExtraHarvestLength")
        if _game_bonus(m.get("harvest_bonus") or {}, "kExtraHarvestLength") is not None
        else 2 * max(0, int((m.get("upgrades") or {}).get("kExtraHarvestLength") or 0)) for m in team)


def model_limitations(team: Sequence[dict], buildings: Dict[int, dict]) -> List[str]:
    """입력에서 검증하지 못한 게임 효과. 화면 문구는 호출자가 번역한다."""
    missing = set()
    supported = {"kHarvestSpeed", "kPierceWood", "kPierceStone", "kPierceBuildings", "kMoreBuildPts",
                 "kFarmSpeed", "kLumberyardSpeed", "kStoneMineSpeed", "kExtraGoldMined",
                 "kFasterWheat", "kFasterWood", "kFasterStone", "kWheatTime", "kWoodTime", "kStoneTime",
                 "kExtraHarvestLength", "kWheatRange"}
    # 게임은 매 프레임 원형 겹침을 검사한다. 여기서는 연속 이동 충돌을 계산하므로 모서리 오차가 남는다.
    if any(b.get("type") in RESOURCE_TILE_TYPES for b in buildings.values()):
        missing.add("continuous_pickup_approximation")
    for b in buildings.values():
        if b.get('type') in HIT_EFFECT_TYPES:
            if 'effect_value' not in b or 'hit_limit' not in b:
                missing.add('missing_building_effect:'+b['type'])
            if b['type'] in HOUSING_HIT_TYPES and 'housing_effect_active' not in b:
                missing.add('missing_housing_activation:'+b['type'])
        if b.get('state') in CONSTRUCTION_STATES and b.get('type') in HIT_EFFECT_TYPES|{'kCobbler'} and 'completion_effect_value' not in b:
            missing.add('completion_global_bonuses_not_projected')
    for member in team:
        bonuses = member.get("harvest_bonus") or {}
        upgrades = member.get("upgrades") or {}
        if upgrades.get("kHarvestSpeed") and _game_bonus(bonuses, "kHarvestSpeed") is None:
            missing.add("missing_harvest_speed_bonus")
        if upgrades.get("kMoreBuildPts") and _game_bonus(bonuses, "kMoreBuildPts") is None:
            missing.add("missing_build_point_bonus")
        missing.update("unsupported_harvest_upgrade:" + key for key, value in upgrades.items()
                       if value and key not in supported)
    return sorted(missing)


HOUSING_HIT_TYPES={'kHauntedHouse','kMonastery','kBrickHouse'}
HIT_EFFECT_TYPES=HOUSING_HIT_TYPES|{'kBabyWorkerCross','kBabyWorkerX','kGoldMine'}


def needs_dynamic_simulation(buildings):
    """상태 전이와 추가 작업자는 시간순 Python 경로에서 처리한다."""
    return any(b.get('state') in CONSTRUCTION_STATES or
               ('task_target_seconds' in b and (resource_tile(b) or b.get('task_active'))) or
               (b.get('type') in HIT_EFFECT_TYPES and 'effect_value' in b) for b in buildings.values())


def _team_tables(buildings: Dict[int, dict]):
    from .harvest import RES_BY_TYPE, building_resource
    # CanHarvest(462A20)는 자원 종류와 보관량을 검사한다. 공사 상태만으로 재고를 지우지 않는다.
    res_left = {bid:int(b.get('res') or 0) if b.get('can_harvest') else 0 for bid,b in buildings.items()}
    rtype = {bid:resource_kind(b) for bid,b in buildings.items()}
    is_tile = {bid: resource_tile(b)
               for bid, b in buildings.items()}
    return res_left, rtype, is_tile


def simulate_team(world: World, buildings: Dict[int, dict], workers: List[Worker], duration: float,
                  max_events: int = 4000, counts: Optional[Dict[int, int]] = None,
                  build_points: Optional[Dict[int, int]] = None,
                  collected: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    """여러 작업자를 시간 순서로 함께 돌린다 (한 명이 비운 채집지를 다른 작업자는 통과).

    채집: 자원별 강화 레벨만큼 1회 채집량을 늘리고, 채집 시간 증가는 캐릭터·자원마다 20회로 제한한다.
    계산은 네이티브 모듈(native/bxp_native.cpp, 같은 계산 — 수십 배 빠름)로 하고, 없으면 파이썬으로 한다.
    """
    from . import native
    res_left, rtype, is_tile = _team_tables(buildings)

    def flags_of(bid: int):
        b = buildings.get(bid) or {}
        pickup=b.get('pickup_enabled',rtype.get(bid)==1)
        f = (native.F_WHEAT if pickup and is_tile.get(bid) else 0) | (native.F_TILE if is_tile.get(bid) else 0)
        if b.get("state") in CONSTRUCTION_STATES:
            f |= native.F_BUILD
        if resource_tile(b):
            f |= native.F_RESOURCE
        if b.get('raycast_enabled') is False and b.get('pickup_enabled') is not True:
            f |= native.F_NO_RAY
        k = rtype.get(bid)
        return f, (-1 if k is None else int(k)), int(res_left.get(bid, 0))

    total = (None if needs_dynamic_simulation(buildings) else
             native.simulate_team(world, buildings, workers, duration, max_events, counts, flags_of, build_points, collected))
    if total is not None:
        return total, workers
    return simulate_team_py(world, buildings, workers, duration, max_events, counts, build_points, collected)


def simulate_team_py(world: World, buildings: Dict[int, dict], workers: List[Worker], duration: float,
                     max_events: int = 4000, counts: Optional[Dict[int, int]] = None,
                     build_points: Optional[Dict[int, int]] = None,
                     collected: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    """simulate_team 의 파이썬 구현 (네이티브 모듈이 없을 때, 그리고 결과 비교 기준)."""
    buildings={bid:dict(b) for bid,b in buildings.items()}
    res_left, rtype, is_tile = _team_tables(buildings)
    total = [0, 0, 0, 0]
    r = world.radius
    walls = [((world.left + r, -1e3), (world.left + r, 1e3)), ((world.right - r, -1e3), (world.right - r, 1e3)),
             ((-1e3, world.top - r), (1e3, world.top - r)), ((-1e3, world.bottom + r), (1e3, world.bottom + r))]
    if not world.use_bounds:
        walls = []

    def moving_speed(w):
        x,y=w.x+w.dx*1e-7,w.y+w.dy*1e-7
        on_road=any(x0<=x<x1 and y0<=y<y1 for x0,y0,x1,y1 in world.roads)
        return w.speed*(world.road_speed_mult if on_road else 1.0)
    for w in workers:
        w.path = [(w.x, w.y, w.t)]
    effects = {id(w): harvest_effects(w.upgrades, w.harvest_bonus) for w in workers}
    clock_counts = {id(w): [0, 0, 0, 0] for w in workers}
    touches={bid:0 for bid in buildings}
    progress={bid:0 for bid in buildings}
    spawned=set()
    launch_speed=world.worker_speed
    from .world_tasks import TaskClock
    tasks=TaskClock(world.task_clock,buildings,res_left,resource_kind,resource_tile,world.model_notes)
    world.automatic_gain=tasks.gain

    def add_points(bid,amount,when):
        nonlocal launch_speed
        b=buildings[bid]
        if b.get('state') not in CONSTRUCTION_STATES:
            return False
        left=None
        if type(b.get('upg_tgt')) is int and type(b.get('upg_pts')) is int:
            left=max(0,b['upg_tgt']-b['upg_pts']-progress[bid])
        added=amount if left is None else min(amount,left)
        progress[bid]+=added
        if build_points is not None:
            build_points[bid]=build_points.get(bid,0)+added
        if left is not None and amount>=left:
            # RefreshCollider(475E30)는 같은 Info 모양을 다시 등록한다. 상태·재고는 별도로 갱신한다.
            old_state=b.get('state');old_value=b.get('effect_value')
            if old_state=='kUpgrading':
                b['lvl']=int(b.get('lvl',0))+1
            b['state']='kNormal'
            if resource_tile(b):
                is_tile[bid]=True
                res_left[bid]=max(0,int(b.get('res') or 0))
                if 'completion_capacity' in b:b['cap']=b['completion_capacity']
            if 'task_target_seconds' in b:
                # 다음 레벨의 작업 주기를 게임 getter로 읽지 못했으므로 현재 값으로 확정하지 않는다.
                world.model_notes.add('completion_task_period_not_projected')
                tasks.elapsed[bid]=0
            value=b.get('completion_effect_value')
            if type(value) is int:
                b['effect_value']=value
                if b.get('type')=='kCobbler':
                    if old_state=='kScaffold' or type(old_value) is int:
                        delta=5.*(value-(old_value if old_state=='kUpgrading' else 0))/100.
                        launch_speed+=delta
                        for member in workers:
                            if member.t>when:
                                bonus=_game_bonus(member.harvest_bonus,'kHarvestSpeed') or 0
                                member.speed+=delta*(1.+bonus/100.)
                    else:
                        world.model_notes.add('completion_global_bonuses_not_projected')
            elif b.get('type') in HIT_EFFECT_TYPES|{'kCobbler'}:
                world.model_notes.add('completion_global_bonuses_not_projected')
            return True
        return False

    def contact_effect(bid,w):
        nonlocal duration
        if bid not in buildings:
            world.model_notes.add('missing_collision_owner')
            return False
        b=buildings[bid];kind=b.get('type');changed=False
        if b.get('state') in CONSTRUCTION_STATES:
            changed=add_points(bid,1+(_game_bonus(w.harvest_bonus,'kMoreBuildPts') or 0),w.t)
        n=touches[bid];touches[bid]+=1
        if b.get('state')=='kScaffold':
            return changed
        if kind in HOUSING_HIT_TYPES and b.get('housing_effect_active') is not True:
            return changed
        value=b.get('effect_value');limit=b.get('hit_limit')
        if type(value) not in (int,float) or not math.isfinite(value):
            return changed
        if kind=='kBrickHouse':
            from .game_range import row_in_range
            for other,row in buildings.items():
                if other!=bid and row.get('state') in CONSTRUCTION_STATES and row_in_range(b,row):
                    changed=add_points(other,max(0,int(value)),w.t) or changed
        elif kind in ('kHauntedHouse','kMonastery') and type(limit) is int and n<limit:
            if kind=='kHauntedHouse':
                w.speed*=1.+value/100.
            else:
                duration+=value/100.
            changed=True
        elif kind in ('kBabyWorkerCross','kBabyWorkerX') and bid not in spawned and type(limit) is int:
            spawned.add(bid)
            directions=((1,0),(-1,0),(0,1),(0,-1)) if kind=='kBabyWorkerCross' else \
                       ((1,1),(1,-1),(-1,1),(-1,-1))
            for dx,dy in directions:
                length=math.sqrt(dx*dx+dy*dy)
                child=Worker(float(b['x']),float(b['y']),dx/length,dy/length,launch_speed,w.t,owner_id=bid)
                child.path=[(child.x,child.y,child.t)]
                workers.append(child);effects[id(child)]=harvest_effects({},{});clock_counts[id(child)]=[0]*4
                events.append(None)
            changed=True
        elif kind=='kGoldMine':
            world.model_notes.add('random_gold_mine_yield_not_projected')
        return changed

    def blocks(bid: int, w: Worker) -> bool:
        b = buildings.get(bid) or {}
        t = b.get("type", "")
        if b.get('raycast_enabled') is False or (w.upgrades.get("kPierceBuildings") and not resource_tile(b)):
            return False
        if is_tile.get(bid):
            if b.get('pickup_enabled',rtype.get(bid)==1):
                return False
            if res_left.get(bid, 0) <= 0:
                return False
        kind = rtype.get(bid)
        if resource_tile(b) and ((kind == 3 and w.upgrades.get("kPierceStone"))
                                        or (kind == 2 and w.upgrades.get("kPierceWood"))):
            return False
        return True

    def harvest(bid: int, w: Worker):
        nonlocal duration
        n = res_left.get(bid, 0)
        kind = rtype.get(bid)
        if n > 0 and kind is not None:
            amounts, clock, _ = effects[id(w)]
            n = min(n, amounts[kind])
            res_left[bid] -= n
            if resource_tile(buildings.get(bid,{})):
                b=buildings[bid];b['res']=res_left[bid]
                b['raycast_enabled']=res_left[bid]>0 and kind!=1
                b['pickup_enabled']=res_left[bid]>0 and kind==1
            w.gain[kind] += n
            total[kind] += n
            if collected is not None:
                collected[bid] = collected.get(bid, 0) + n  # 관통 채집도 건물별 접근 증거에 포함한다.
            if clock[kind] and clock_counts[id(w)][kind] < 20:
                duration += clock[kind] * .2
                clock_counts[id(w)][kind] += 1

    def next_event(w: Worker):
        if not w.active or w.t >= duration or w.speed <= 0:
            return None
        best = None
        for i, ((ax, ay), (bx, by)) in enumerate(walls):
            inside = (w.x >= ax - 1e-6, w.x <= ax + 1e-6, w.y <= ay + 1e-6, w.y >= ay - 1e-6)[i]
            toward = (w.dx < 0, w.dx > 0, w.dy > 0, w.dy < 0)[i]
            if inside and toward:
                h = _ray_segment(w.x, w.y, w.dx, w.dy, ax, ay, bx, by)
                if h and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], h[2], None, True, False)
        for sh in world.shapes:
            b=buildings.get(sh.bid,{})
            pickup=is_tile.get(sh.bid) and b.get('pickup_enabled',rtype.get(sh.bid)==1)
            if not sh.is_wall and not pickup and b.get('raycast_enabled') is False:
                continue
            solid = sh.is_wall or (not pickup and blocks(sh.bid,w))
            pickup_r = effects[id(w)][2] if pickup else r
            # 빈 타일은 사건을 만들지 않는다. 통과 채집도 실제 접촉 시각의 사건으로 처리한다.
            if not solid and is_tile.get(sh.bid) and res_left.get(sh.bid, 0) <= 0:
                w.touching.discard(sh.bid)
                continue
            inside=_inside_shape(sh,w.x,w.y,pickup_r)
            if pickup and inside:
                if sh.bid in w.touching and not w.just_bounced:
                    continue
                h=(0.,-w.dx,-w.dy)
            else:
                if not inside:
                    w.touching.discard(sh.bid)
                if not solid and inside:
                    continue
                h = _hit_shape(sh, w.x, w.y, w.dx, w.dy, pickup_r)
            if not h:
                continue
            if best is None or h[0] < best[0]:
                best = (h[0], h[1], h[2], sh, solid, bool(pickup))
        for x0,y0,x1,y1 in world.roads:
            for ax,ay,bx,by in ((x0,y0,x1,y0),(x1,y0,x1,y1),(x1,y1,x0,y1),(x0,y1,x0,y0)):
                h=_ray_segment(w.x,w.y,w.dx,w.dy,ax,ay,bx,by)
                if h and (best is None or h[0]<best[0]):
                    best=(h[0],h[1],h[2],None,False,False)  # 도로 경계는 반사·타격이 아닌 속도 변화다.
        if best is None or w.t + best[0] / moving_speed(w) >= duration:
            return None
        return (w.t + best[0] / moving_speed(w), *best)

    events = [next_event(w) for w in workers]
    for _ in range(max_events):
        ready = [i for i, event in enumerate(events) if event is not None]
        if tasks.next<duration and (not ready or tasks.next<min(events[i][0] for i in ready)):
            when=tasks.next
            for other in workers:
                if other.active and other.t<when:
                    distance=(when-other.t)*moving_speed(other)
                    other.x,other.y,other.t=other.x+other.dx*distance,other.y+other.dy*distance,when
            tasks.tick()
            events=[next_event(other) for other in workers]
            continue
        if not ready:
            for w in workers:
                if w.active and w.t < duration:
                    rest = (duration - w.t) * moving_speed(w)
                    w.x, w.y, w.t = w.x + w.dx * rest, w.y + w.dy * rest, duration
                    w.path.append((w.x, w.y, w.t))
            break
        wi = min(ready, key=lambda i: (events[i][0], i))
        when, dist, nx, ny, sh, solid, pickup = events[wi]
        w = workers[wi]
        w.x, w.y, w.t = w.x + w.dx * dist, w.y + w.dy * dist, when
        changed = sh is not None and res_left.get(sh.bid, 0) > 0
        if sh is not None and not sh.is_wall:
            harvest(sh.bid, w)
            if pickup:
                w.touching.add(sh.bid)
                w.just_bounced=False
            if not pickup and counts is not None:
                counts[sh.bid] = counts.get(sh.bid, 0) + 1     # 건물에 부딪힌 횟수 (미완성 건물 건설용)
            if not pickup:
                changed=contact_effect(sh.bid,w) or changed
        if solid:
            w.path.append((w.x, w.y, w.t))
            w.dx, w.dy = reflected(w.dx, w.dy, nx, ny)
            w.speed = min(MAX_SPEED, w.speed + SPEED_UP)
            w.bounces+=1
            w.just_bounced=True
            if w.owner_id is not None and w.bounces>=buildings[w.owner_id]['hit_limit']:
                w.active=False
        elif sh is None:
            w.path.append((w.x,w.y,w.t))  # 시각 재생에는 도로 진입/이탈 지점도 필요하다.
        w.x, w.y = w.x + w.dx * 1e-4, w.y + w.dy * 1e-4
        if changed:
            # 타일을 비운 바로 그 시각까지만 다른 작업자를 진행시킨다. 그 이후의 반사 후보는 다시 계산한다.
            simultaneous = {}
            for j, other in enumerate(workers):
                old = events[j]
                if j != wi and old is not None and old[0] == when:
                    _, _, onx, ony, target, was_solid, was_pickup = old
                    blocking = was_solid if target is None else target.is_wall or blocks(target.bid, other)
                    if target is None or blocking or not is_tile.get(target.bid) or res_left.get(target.bid, 0) > 0:
                        simultaneous[j] = (when, 0.0, onx, ony, target, blocking, was_pickup)
                if j != wi and other.active and other.t < when:
                    distance = (when - other.t) * moving_speed(other)
                    other.x, other.y, other.t = other.x + other.dx * distance, other.y + other.dy * distance, when
            events = [next_event(other) for other in workers]
            for j, event in simultaneous.items():
                events[j] = event
        else:
            events[wi] = next_event(w)
    else:
        world.model_notes.add('simulation_event_limit')
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


def team_from_chars(chars_raw: Sequence[dict], order: Optional[Sequence[str]] = None) -> List[dict]:
    """게임 BaseMgr.SetUpActiveWorkers(45D090), CanBeSentToWork(47D090)와 같은 참가 조건.
    금광·자동 발사대 배정, 출전·회복 중, 영향력자는 제외한다. 일반 생산 건물 배정자는 참가한다.
    """
    chars = [c for c in chars_raw if c.get("type") and c.get("type") != "kInfluencer"
             and c.get("state") in (None, "kIdle", "kWorking")
             and not (c.get("state") == "kWorking" and c.get("work") in (None, "kGoldMine", "kIdleLauncher"))]
    if order:
        rank = {t: i for i, t in enumerate(order)}
        chars.sort(key=lambda c: rank.get(c["type"], 99))
    out = []
    for c in chars:
        ups = c.get("harvest") or {}
        out.append({"type": c["type"], "upgrades": ups, "speed": 5.0 + (1.0 if ups.get("kHarvestSpeed") else 0.0),
                    "harvest_bonus": dict(c.get("harvest_bonus") or {})})
    return out


def team_from_base(base, chars_raw, order=None):
    """조준 때 게임이 확정한 참가자·순서를 우선한다. 일반 기지는 meta로 예측한다."""
    actual=(base.get('geo') or {}).get('launch_team')
    if base.get('state') in ('kAimWorkers','kBounceWorkers') and isinstance(actual,list):
        if not all(isinstance(m,dict) and isinstance(m.get('type'),str)
                   and isinstance(m.get('upgrades'),dict) and isinstance(m.get('harvest_bonus'),dict) for m in actual):
            return []
        return [dict(m,speed=5.) for m in actual]
    return team_from_chars(chars_raw,order)


def run_angle(world: World, buildings: Dict[int, dict], team: Sequence[dict], angle: float,
              duration: float, counts: Optional[Dict[int, int]] = None,
              build_points: Optional[Dict[int, int]] = None,
              collected: Optional[Dict[int, int]] = None) -> Tuple[List[int], List[Worker]]:
    world.model_notes.clear()
    world.automatic_gain=[0,0,0,0]
    if world.use_bounds:
        world.model_notes.add('reconstructed_walls')
    lx, ly = world.launcher
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    ws = []
    for i, m in enumerate(team):
        bonuses = dict(m.get("harvest_bonus") or {})
        speed_pct = _game_bonus(bonuses, "kHarvestSpeed")
        speed = (world.worker_speed * (1.0 + speed_pct / 100.0) if speed_pct is not None else
                 world.worker_speed + (m["speed"] - 5.0) * world.worker_speed_mult)
        ws.append(Worker(lx, ly, dx, dy, speed, i * world.launch_gap, dict(m["upgrades"]), harvest_bonus=bonuses))
    return simulate_team(world, buildings, ws, duration, counts=counts, build_points=build_points, collected=collected)


@dataclass
class AngleResult:
    angle: float
    total: List[int]                         # [골드, 밀, 나무, 돌]
    build_hits: int = 0                      # 미완성 건물에 맞은 횟수 (건물마다 더 필요한 만큼까지만 셈)
    per_building: Dict[int, int] = field(default_factory=dict)
    build_points: Optional[int] = None       # 목표별 남은 공사 점수로 제한한 진행 점수. None은 구형 결과.
    per_building_points: Dict[int, int] = field(default_factory=dict)
    model_notes: tuple = ()


def angle_score(result: AngleResult, need: int) -> float:
    """공사 진행 점수 우선, 필요한 자원과 나머지 자원 순. 구형 결과만 충돌당 1점으로 비교한다."""
    points = getattr(result, "build_points", None)
    progress = points if points is not None else result.build_hits
    return 100 * progress + result.total[need] + 0.25 * (sum(result.total) - result.total[need])


def rank_angles(world: World, buildings: Dict[int, dict], team: Sequence[dict], duration: float, need: int,
                targets: Optional[Dict[int, int]] = None,
                angles: Sequence[float] = tuple(range(12, 169, 3))) -> List[AngleResult]:
    """각도별 예상 결과. 미완성 건물 건설(targets: 건물 id → 더 필요한 공사 점수)이 먼저, 그다음 필요한 자원,
    다른 자원은 4분의 1 가중."""
    targets = targets or {}
    out = []
    for a in angles:
        counts: Dict[int, int] = {}
        points: Dict[int, int] = {}
        total, _ = run_angle(world, buildings, team, a, duration, counts, points)
        per = {bid: min(counts.get(bid, 0), cap) for bid, cap in targets.items() if counts.get(bid)}
        per_points = {}
        for bid, cap in targets.items():
            progress = points.get(bid, 0)
            if not buildings.get(bid, {}).get("state"):
                # 예전 플러그인·자료는 공사 상태가 없다. 호환용 1점/접촉이며 게임의 정확한 점수가 아니다.
                progress = counts.get(bid, 0)
            if progress > 0:
                per_points[bid] = min(progress, cap)
        out.append(AngleResult(a,total,sum(per.values()),per,sum(per_points.values()),per_points,tuple(sorted(world.model_notes))))
    out.sort(key=lambda r: -angle_score(r, need))
    return out


def best_angles(world: World, buildings: Dict[int, dict], team: Sequence[dict], duration: float, need: int,
                angles: Sequence[float] = tuple(range(12, 169, 3))) -> List[Tuple[float, List[int]]]:
    """각도별 팀 전체 예상 채집량. 필요한 자원 우선, 다른 자원은 4분의 1 가중."""
    return [(r.angle, r.total) for r in rank_angles(world, buildings, team, duration, need, None, angles)]
