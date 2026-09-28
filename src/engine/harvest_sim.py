"""기지 채집 궤적 시뮬레이션 (게임 연동 1.6 의 기지 물리 모양 기준).

게임 1.301 실행 코드(BaseMgr.MoveBalls, 0x4596E0)와 대조한 규칙:
- 일반 작업자는 채집 시간이 끝날 때까지 튕긴다. 건물이 생성한 추가 작업자는 소유 건물의 반사 한도를 따른다.
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
LAUNCH_GAP = 0.3  # 1.301 _LaunchWorkers의 게임 시간 간격. 입력값을 우선한다.


def reflected(delta_x, delta_y, normal_x, normal_y):
    """MoveBalls 0x45A2DF: d - 2(d·n)n, 이후 Vector2.normalized."""
    norm2 = normal_x * normal_x + normal_y * normal_y
    if norm2 <= 0:
        return delta_x, delta_y
    scale = 2.0 * (delta_x * normal_x + delta_y * normal_y) / norm2
    rx, ry = delta_x - scale * normal_x, delta_y - scale * normal_y
    length = math.sqrt(rx * rx + ry * ry)
    return (rx / length, ry / length) if length > 0 else (delta_x, delta_y)


@dataclass
class Shape:
    bid: int  # 건물 id
    kind: str  # box | circle | poly | wall (미개방 구역)
    pts: List[Tuple[float, float]] = field(default_factory=list)
    c: Tuple[float, float] = (0.0, 0.0)
    r: float = 0.0
    bb: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # 미리 계산한 경계 상자 (빠른 제외용)
    environment: bool = False  # 실제 정적 벽: 모양과 관통 불가 성질을 분리한다.

    @property
    def is_wall(self):
        return self.environment or self.kind == "wall"

    def __post_init__(self):
        if self.kind in ("box", "wall") and any(
            abs(start_x - end_x) > EPS and abs(start_y - end_y) > EPS
            for (start_x, start_y), (end_x, end_y) in zip(self.pts, self.pts[1:] + self.pts[:1])
        ):
            # 회전한 BoxCollider의 AABB는 내부 판정에 쓸 수 없다. 실제 꼭짓점을 유지한다.
            self.environment = self.environment or self.kind == "wall"
            self.kind = "poly"
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
    radius: float = 0.0  # 게임 이동은 Physics2D.Raycast. 채집 원의 반지름과 구분한다.
    worker_speed: float = 5.0  # 게임이 알려 준 기본 작업자 속도
    worker_speed_mult: float = 1.0  # 게임의 BuildingMgr.WorkerMoveSpeedMult (기지 강화 등에 따라 변함)
    launch_gap: float = LAUNCH_GAP
    use_bounds: bool = True
    roads: tuple = ()
    road_speed_mult: float = 1.0
    model_notes: set = field(default_factory=set)
    task_clock: Optional[tuple] = None
    automatic_gain: List[int] = field(default_factory=lambda: [0, 0, 0, 0])


def _chunk_walls(geo: dict) -> List[Shape]:
    """게임의 구매 구역 사이에 남은 덮개를 벽으로 복원한다.

    게임 1.301 level2의 Chunk_Base_Cover: BoxCollider2D 9×6.75, trigger=False.
    BaseGridMgr.RefreshChunkCovers(0x44D130)는 미구매 덮개를 켠다.
    위치는 연동의 구역 크기·구매 좌표·기지 경계로 구성한다. 외곽 네 벽은 기존 판정을 유지한다.
    """
    raw = geo.get("chunks")
    if not raw:
        return []  # 구역 정보가 없는 구형 입력.
    chunks = {tuple(chunk) for chunk in raw}
    if any(
        len(chunk) != 2 or any(type(value) is not int or value < 0 for value in chunk) for chunk in chunks
    ):
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
    left_x, bottom_y = min(chunk[0] for chunk in chunks), min(chunk[1] for chunk in chunks)
    right_x, top_y = max(chunk[0] for chunk in chunks), max(chunk[1] for chunk in chunks)
    walls = []
    for x in range(left_x, right_x + 1):
        for y in range(bottom_y, top_y + 1):
            if (x, y) in chunks:
                continue
            left = float(geo["left"]) + (x - left_x) * width
            bottom = float(geo["bottom"]) + (y - bottom_y) * height
            walls.append(
                Shape(
                    -1000000 - len(walls),
                    "wall",
                    pts=[
                        (left, bottom),
                        (left + width, bottom),
                        (left + width, bottom + height),
                        (left, bottom + height),
                    ],
                )
            )
    return walls


def world_from_geo(geo: dict, radius: float = 0.0) -> Optional[World]:
    try:
        if geo.get("physics_error"):
            return None
        shapes = []
        for collider in geo.get("colliders") or []:
            if collider.get("trigger"):
                continue
            if collider.get("shape") == "circle":
                shapes.append(
                    Shape(
                        int(collider["id"]),
                        "circle",
                        c=(float(collider["c"][0]), float(collider["c"][1])),
                        r=float(collider["r"]),
                    )
                )
            elif collider.get("pts"):
                kind = "box" if collider.get("shape") in ("box", "bounds") else "poly"
                shapes.append(
                    Shape(int(collider["id"]), kind, pts=[(float(x), float(y)) for x, y in collider["pts"]])
                )
        environment = geo.get("environment")
        if isinstance(environment, dict) and (
            environment.get("queries_start_in_colliders") is True
            or environment.get("queries_hit_triggers") is True
        ):
            raise ValueError("검증한 게임 물리 질의 설정과 다름")
        exact = isinstance(environment, dict) and isinstance(environment.get("walls"), list)
        if geo.get("physics_contract", 0) >= 2 and not exact:
            return None
        if exact:
            for index, collider in enumerate(environment["walls"]):
                if collider.get("unsupported"):
                    raise ValueError("미지원 실제 벽 모양")
                building_id = -2000000 - index
                if collider.get("shape") == "circle":
                    shapes.append(
                        Shape(
                            building_id,
                            "circle",
                            c=tuple(collider["c"]),
                            r=float(collider["r"]),
                            environment=True,
                        )
                    )
                else:
                    for points in collider.get("paths", []):
                        if len(points) < 2:
                            raise ValueError("실제 벽 꼭짓점 없음")
                        shapes.append(
                            Shape(
                                building_id,
                                collider["shape"],
                                pts=[tuple(p) for p in points],
                                environment=True,
                            )
                        )
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
        roads = tuple(tuple(float(value) for value in box) for box in (environment or {}).get("roads", ()))
        road_mult = float((environment or {}).get("road_speed_mult", 1.0))
        if (
            not math.isfinite(road_mult)
            or road_mult <= 0
            or any(
                len(box) != 4
                or not all(math.isfinite(value) for value in box)
                or box[0] >= box[2]
                or box[1] >= box[3]
                for box in roads
            )
        ):
            return None
        return World(
            float(geo["left"]),
            float(geo["right"]),
            float(geo["bottom"]),
            float(geo["top"]),
            shapes,
            (float(geo["launcher"][0]), float(geo["launcher"][1])),
            radius,
            base_speed,
            speed_mult,
            gap,
            not exact,
            roads,
            road_mult,
            task_clock=(
                float(geo["world_tick_progress"]),
                float(geo["world_tick_interval"]),
                float(geo["game_speed"]),
            )
            if all(k in geo for k in ("world_tick_progress", "world_tick_interval", "game_speed"))
            else None,
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _ray_segment(
    origin_x, origin_y, delta_x, delta_y, start_x, start_y, end_x, end_y
) -> Optional[Tuple[float, float, float]]:
    """광선과 선분 교차: (거리 t, 법선 x, 법선 y)."""
    ex, ey = end_x - start_x, end_y - start_y
    den = delta_x * ey - delta_y * ex
    if abs(den) < EPS:
        return None
    distance = ((start_x - origin_x) * ey - (start_y - origin_y) * ex) / den
    segment_fraction = ((start_x - origin_x) * delta_y - (start_y - origin_y) * delta_x) / den
    if distance <= EPS or segment_fraction < -EPS or segment_fraction > 1 + EPS:
        return None
    normal_x, normal_y = -ey, ex
    if normal_x * delta_x + normal_y * delta_y > 0:
        normal_x, normal_y = -normal_x, -normal_y
    return distance, normal_x, normal_y


def _misses_box(bb, origin_x, origin_y, delta_x, delta_y, r) -> bool:
    """광선이 (r 만큼 넓힌) 경계 상자를 앞쪽에서 만나지 않으면 True — 모양별 정밀 계산 전 빠른 제외."""
    left_x, bottom_y, right_x, top_y = bb[0] - r, bb[1] - r, bb[2] + r, bb[3] + r
    tmin, tmax = -1e18, 1e18
    for o, d, lo, hi in ((origin_x, delta_x, left_x, right_x), (origin_y, delta_y, bottom_y, top_y)):
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


def _hit_shape(shape: Shape, origin_x, origin_y, delta_x, delta_y, r) -> Optional[Tuple[float, float, float]]:
    if _misses_box(shape.bb, origin_x, origin_y, delta_x, delta_y, r + 1e-3):
        return None
    # 게임 globalgamemanagers: Physics2D.queriesStartInColliders=false.
    # 건물 중심에서 생성되는 추가 작업자가 소유 건물에 즉시 반사하지 않는다.
    if _inside_shape(shape, origin_x, origin_y, r):
        return None
    if shape.kind == "circle":
        center_x, center_y = shape.c
        expanded_radius = shape.r + r
        offset_x, offset_y = origin_x - center_x, origin_y - center_y
        projection = offset_x * delta_x + offset_y * delta_y
        squared_offset = offset_x * offset_x + offset_y * offset_y - expanded_radius * expanded_radius
        disc = projection * projection - squared_offset
        if disc < 0:
            return None
        hit_distance = -projection - math.sqrt(disc)
        if hit_distance <= EPS:
            return None
        hx, hy = origin_x + delta_x * hit_distance - center_x, origin_y + delta_y * hit_distance - center_y
        return hit_distance, hx, hy
    points = shape.pts
    if shape.kind in ("box", "wall") and r > 0:
        left_x, bottom_y, right_x, top_y = shape.aabb()
        # OverlapCircle의 모서리는 사각 확장이 아니라 원호다.
        hits = []
        for start_x, start_y, end_x, end_y in (
            (left_x, bottom_y - r, right_x, bottom_y - r),
            (right_x + r, bottom_y, right_x + r, top_y),
            (right_x, top_y + r, left_x, top_y + r),
            (left_x - r, top_y, left_x - r, bottom_y),
        ):
            intersection = _ray_segment(origin_x, origin_y, delta_x, delta_y, start_x, start_y, end_x, end_y)
            if intersection:
                hits.append(intersection)
        for center_x, center_y, sx, sy in (
            (left_x, bottom_y, -1, -1),
            (right_x, bottom_y, 1, -1),
            (right_x, top_y, 1, 1),
            (left_x, top_y, -1, 1),
        ):
            offset_x, offset_y = origin_x - center_x, origin_y - center_y
            projection = offset_x * delta_x + offset_y * delta_y
            disc = projection * projection - (offset_x * offset_x + offset_y * offset_y - r * r)
            if disc < 0:
                continue
            hit_distance = -projection - math.sqrt(disc)
            normal_x, normal_y = (
                origin_x + delta_x * hit_distance - center_x,
                origin_y + delta_y * hit_distance - center_y,
            )
            if hit_distance > EPS and sx * normal_x >= -EPS and sy * normal_y >= -EPS:
                hits.append((hit_distance, normal_x, normal_y))
        return min(hits, key=lambda h: h[0]) if hits else None
    best = None
    item_count = len(points)
    for index in range(item_count if shape.kind != "edge" else item_count - 1):
        start_x, start_y = points[index]
        end_x, end_y = points[(index + 1) % item_count]
        intersection = _ray_segment(origin_x, origin_y, delta_x, delta_y, start_x, start_y, end_x, end_y)
        if intersection and (best is None or intersection[0] < best[0]):
            best = intersection
    return best


def _inside_shape(shape: Shape, x: float, y: float, radius: float) -> bool:
    if shape.kind == "edge":
        return False
    """통과 타일의 출구를 새 채집으로 세지 않기 위한 내부 판정."""
    if shape.kind == "circle":
        return (x - shape.c[0]) ** 2 + (y - shape.c[1]) ** 2 < (shape.r + radius) ** 2
    if shape.kind in ("box", "wall"):
        left_x, bottom_y, right_x, top_y = shape.bb
        if radius > 0:
            delta_x, delta_y = max(left_x - x, 0.0, x - right_x), max(bottom_y - y, 0.0, y - top_y)
            return delta_x * delta_x + delta_y * delta_y <= radius * radius
        return left_x - radius < x < right_x + radius and bottom_y - radius < y < top_y + radius
    inside = False
    for (start_x, start_y), (end_x, end_y) in zip(shape.pts, shape.pts[1:] + shape.pts[:1]):
        if (start_y > y) != (end_y > y) and x < (end_x - start_x) * (y - start_y) / (
            end_y - start_y
        ) + start_x:
            inside = not inside
    return inside


@dataclass
class PathResult:
    points: List[Tuple[float, float]]  # 꺾이는 지점들 (월드 좌표)
    hits: List[Tuple[int, float]]  # (건물 id, 닿은 시각 초)


WHEAT_TYPES = {"kWheatField", "kDenseWheat"}
# 자원 종류 안내 표에는 거처도 있으므로 충돌 성질은 실제 자원 타일만 따로 센다.
from .construction_policy import RESOURCE_TILE_TYPES


def resource_tile(building):
    value = building.get("is_resource")
    return value if type(value) is bool else building.get("type") in RESOURCE_TILE_TYPES


def resource_kind(building):
    if "resource_type" in building:
        value = building["resource_type"]
        return value if type(value) is int and 0 <= value < 4 else None
    from .harvest import building_resource

    return building_resource(building)


CONSTRUCTION_STATES = {"kScaffold", "kUpgrading"}


def passable_ids(buildings: Dict[int, dict], upgrades: Optional[dict] = None) -> set:
    """MoveBalls의 자원 관통과 비자원 건물 관통은 서로 다른 분기다."""
    from .harvest import RES_BY_TYPE

    current_upgrades = upgrades or {}
    result = set()
    for building_id, building in buildings.items():
        t = building.get("type", "")
        res = resource_kind(building)
        if current_upgrades.get("kPierceBuildings") and not resource_tile(building):
            result.add(building_id)
        elif resource_tile(building) and (res == 1 or building.get("can_harvest") is False):
            result.add(building_id)
        elif res == 3 and current_upgrades.get("kPierceStone") and resource_tile(building):
            result.add(building_id)
        elif res == 2 and current_upgrades.get("kPierceWood") and resource_tile(building):
            result.add(building_id)
    return result


def simulate(
    world: World,
    angle_deg: float,
    duration: float,
    speed0: float = 5.0,
    pierce: Sequence[int] = (),
    max_bounces: int = 400,
) -> PathResult:
    """발사대에서 angle_deg(0=오른쪽, 90=위) 로 쏜 작업자 한 명의 경로. pierce: 통과하는 건물 id."""
    origin_x, origin_y = world.launcher
    delta_x, delta_y = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    radius = world.radius
    speed, t_now = speed0, 0.0
    points = [(origin_x, origin_y)]
    hits: List[Tuple[int, float]] = []
    pierce = set(pierce)
    walls = [
        ((world.left + radius, -1e3), (world.left + radius, 1e3)),
        ((world.right - radius, -1e3), (world.right - radius, 1e3)),
        ((-1e3, world.top - radius), (1e3, world.top - radius)),
        ((-1e3, world.bottom + radius), (1e3, world.bottom + radius)),
    ]
    if not world.use_bounds:
        walls = []
    for _ in range(max_bounces):
        best = None  # (t, nx, ny, shape or None)
        for index, ((start_x, start_y), (end_x, end_y)) in enumerate(walls):
            # 벽은 안쪽에서 바깥으로 나갈 때만 튕긴다 (발사대는 아래 벽보다 아래에 있다 — 실제 궤적 확인)
            inside = (
                origin_x >= start_x - 1e-6,
                origin_x <= start_x + 1e-6,
                origin_y <= start_y + 1e-6,
                origin_y >= start_y - 1e-6,
            )[index]
            toward = (delta_x < 0, delta_x > 0, delta_y > 0, delta_y < 0)[index]
            if not (inside and toward):
                continue
            h = _ray_segment(origin_x, origin_y, delta_x, delta_y, start_x, start_y, end_x, end_y)
            if h and (best is None or h[0] < best[0]):
                best = (h[0], h[1], h[2], None)
        passed = []
        for shape in world.shapes:
            h = _hit_shape(shape, origin_x, origin_y, delta_x, delta_y, radius)
            if not h:
                continue
            if not shape.is_wall and shape.bid in pierce:
                passed.append((h[0], shape.bid))
            elif best is None or h[0] < best[0]:
                best = (h[0], h[1], h[2], shape)
        if best is None:
            break
        t, normal_x, normal_y, shape = best
        for tp, building_id in sorted(passed):
            if tp < t and t_now + tp / speed < duration:
                hits.append((building_id, t_now + tp / speed))
        dt = t / speed
        if t_now + dt >= duration:
            rest = (duration - t_now) * speed
            points.append((origin_x + delta_x * rest, origin_y + delta_y * rest))
            break
        t_now += dt
        origin_x, origin_y = origin_x + delta_x * t, origin_y + delta_y * t
        points.append((origin_x, origin_y))
        if shape is not None and not shape.is_wall:
            hits.append((shape.bid, t_now))
        delta_x, delta_y = reflected(delta_x, delta_y, normal_x, normal_y)
        origin_x, origin_y = origin_x + delta_x * 1e-4, origin_y + delta_y * 1e-4
        speed = min(MAX_SPEED, speed + SPEED_UP)
    return PathResult(points, hits)


def yield_for(
    path_hits: Sequence[Tuple[int, float]], buildings: Dict[int, dict], taken: Optional[set] = None
) -> List[int]:
    """경로가 닿은 채집 가능 건물의 자원 합 [골드, 밀, 나무, 돌]. taken: 이미 다른 작업자가 가져간 건물."""
    from .harvest import building_resource

    result = [0, 0, 0, 0]
    taken = taken if taken is not None else set()
    for building_id, _t in path_hits:
        building = buildings.get(building_id)
        if (
            building is None
            or building_id in taken
            or not building.get("can_harvest")
            or not (building.get("res") or 0)
        ):
            continue
        res = building_resource(building)
        if res is None:
            continue
        taken.add(building_id)
        result[res] += int(building["res"])
    return result


def sweep(
    world: World,
    buildings: Dict[int, dict],
    duration: float,
    need: int,
    angles: Sequence[float] = tuple(range(15, 166, 3)),
    speed0: float = 5.0,
) -> List[Tuple[float, List[int], PathResult]]:
    """각도마다 한 명이 얻는 자원. need 자원이 많은 순."""
    result = []
    for a in angles:
        p = simulate(world, a, duration, speed0)
        result.append((a, yield_for(p.hits, buildings), p))
    result.sort(key=lambda t: (-t[1][need], -sum(t[1])))
    return result


@dataclass
class Worker:
    x: float
    y: float
    dx: float
    dy: float
    speed: float
    t: float  # 이 작업자의 현재 시각 (채집 시작 기준 초)
    upgrades: dict = field(default_factory=dict)
    path: List[Tuple[float, float, float]] = field(default_factory=list)  # (x, y, t)
    gain: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    harvest_bonus: dict = field(default_factory=dict)  # 게임 GetHarvestUpgradeBonusAmt/GetBonusAmt 원값
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
    clock = [0] + [
        (
            _game_bonus(bonuses, "k" + resource + "Time")
            if _game_bonus(bonuses, "k" + resource + "Time") is not None
            else levels("k" + resource + "Time")
        )
        for resource in ("Wheat", "Wood", "Stone")
    ]
    return amounts, clock, 0.125 + 0.5 * levels("kWheatRange")


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
    return (
        float(basic)
        + len(team)
        + sum(
            _game_bonus(m.get("harvest_bonus") or {}, "kExtraHarvestLength")
            if _game_bonus(m.get("harvest_bonus") or {}, "kExtraHarvestLength") is not None
            else 2 * max(0, int((m.get("upgrades") or {}).get("kExtraHarvestLength") or 0))
            for m in team
        )
    )


def model_limitations(team: Sequence[dict], buildings: Dict[int, dict]) -> List[str]:
    """입력에서 검증하지 못한 게임 효과. 화면 문구는 호출자가 번역한다."""
    missing = set()
    supported = {
        "kHarvestSpeed",
        "kPierceWood",
        "kPierceStone",
        "kPierceBuildings",
        "kMoreBuildPts",
        "kFarmSpeed",
        "kLumberyardSpeed",
        "kStoneMineSpeed",
        "kExtraGoldMined",
        "kFasterWheat",
        "kFasterWood",
        "kFasterStone",
        "kWheatTime",
        "kWoodTime",
        "kStoneTime",
        "kExtraHarvestLength",
        "kWheatRange",
    }
    # 게임은 매 프레임 원형 겹침을 검사한다. 여기서는 연속 이동 충돌을 계산하므로 모서리 오차가 남는다.
    if any(building.get("type") in RESOURCE_TILE_TYPES for building in buildings.values()):
        missing.add("continuous_pickup_approximation")
    for building in buildings.values():
        if building.get("type") in HIT_EFFECT_TYPES:
            if "effect_value" not in building or "hit_limit" not in building:
                missing.add("missing_building_effect:" + building["type"])
            if building["type"] in HOUSING_HIT_TYPES and "housing_effect_active" not in building:
                missing.add("missing_housing_activation:" + building["type"])
        if (
            building.get("state") in CONSTRUCTION_STATES
            and building.get("type") in HIT_EFFECT_TYPES | {"kCobbler"}
            and "completion_effect_value" not in building
        ):
            missing.add("completion_global_bonuses_not_projected")
    for member in team:
        bonuses = member.get("harvest_bonus") or {}
        upgrades = member.get("upgrades") or {}
        if upgrades.get("kHarvestSpeed") and _game_bonus(bonuses, "kHarvestSpeed") is None:
            missing.add("missing_harvest_speed_bonus")
        if upgrades.get("kMoreBuildPts") and _game_bonus(bonuses, "kMoreBuildPts") is None:
            missing.add("missing_build_point_bonus")
        missing.update(
            "unsupported_harvest_upgrade:" + key
            for key, value in upgrades.items()
            if value and key not in supported
        )
    return sorted(missing)


HOUSING_HIT_TYPES = {"kHauntedHouse", "kMonastery", "kBrickHouse"}
HIT_EFFECT_TYPES = HOUSING_HIT_TYPES | {"kBabyWorkerCross", "kBabyWorkerX", "kGoldMine"}


def needs_dynamic_simulation(buildings):
    """상태 전이와 추가 작업자는 시간순 Python 경로에서 처리한다."""
    return any(
        building.get("state") in CONSTRUCTION_STATES
        or ("task_target_seconds" in building and (resource_tile(building) or building.get("task_active")))
        or (building.get("type") in HIT_EFFECT_TYPES and "effect_value" in building)
        for building in buildings.values()
    )


def _team_tables(buildings: Dict[int, dict]):
    from .harvest import RES_BY_TYPE, building_resource

    # CanHarvest(462A20)는 자원 종류와 보관량을 검사한다. 공사 상태만으로 재고를 지우지 않는다.
    res_left = {
        building_id: int(building.get("res") or 0) if building.get("can_harvest") else 0
        for building_id, building in buildings.items()
    }
    resource_types = {building_id: resource_kind(building) for building_id, building in buildings.items()}
    is_tile = {building_id: resource_tile(building) for building_id, building in buildings.items()}
    return res_left, resource_types, is_tile


def simulate_team(
    world: World,
    buildings: Dict[int, dict],
    workers: List[Worker],
    duration: float,
    max_events: int = 4000,
    counts: Optional[Dict[int, int]] = None,
    build_points: Optional[Dict[int, int]] = None,
    collected: Optional[Dict[int, int]] = None,
) -> Tuple[List[int], List[Worker]]:
    """여러 작업자를 시간 순서로 함께 돌린다 (한 명이 비운 채집지를 다른 작업자는 통과).

    채집: 자원별 강화 레벨만큼 1회 채집량을 늘리고, 채집 시간 증가는 캐릭터·자원마다 20회로 제한한다.
    계산은 네이티브 모듈(native/bxp_native.cpp, 같은 계산 — 수십 배 빠름)로 하고, 없으면 파이썬으로 한다.
    """
    from . import native

    res_left, resource_types, is_tile = _team_tables(buildings)

    def flags_of(building_id: int):
        building = buildings.get(building_id) or {}
        pickup = building.get("pickup_enabled", resource_types.get(building_id) == 1)
        f = (native.F_WHEAT if pickup and is_tile.get(building_id) else 0) | (
            native.F_TILE if is_tile.get(building_id) else 0
        )
        if building.get("state") in CONSTRUCTION_STATES:
            f |= native.F_BUILD
        if resource_tile(building):
            f |= native.F_RESOURCE
        if building.get("raycast_enabled") is False and building.get("pickup_enabled") is not True:
            f |= native.F_NO_RAY
        k = resource_types.get(building_id)
        return f, (-1 if k is None else int(k)), int(res_left.get(building_id, 0))

    total = (
        None
        if needs_dynamic_simulation(buildings)
        else native.simulate_team(
            world, buildings, workers, duration, max_events, counts, flags_of, build_points, collected
        )
    )
    if total is not None:
        return total, workers
    return simulate_team_py(
        world, buildings, workers, duration, max_events, counts, build_points, collected,
        _shape_query=native.DynamicShapeQuery(world) if native.lib() is not None else None,
    )


def simulate_team_py(
    world: World,
    buildings: Dict[int, dict],
    workers: List[Worker],
    duration: float,
    max_events: int = 4000,
    counts: Optional[Dict[int, int]] = None,
    build_points: Optional[Dict[int, int]] = None,
    collected: Optional[Dict[int, int]] = None,
    *,
    _shape_query=None,
) -> Tuple[List[int], List[Worker]]:
    """simulate_team 의 파이썬 구현 (네이티브 모듈이 없을 때, 그리고 결과 비교 기준)."""
    buildings = {building_id: dict(building) for building_id, building in buildings.items()}
    res_left, resource_types, is_tile = _team_tables(buildings)
    total = [0, 0, 0, 0]
    radius = world.radius
    walls = [
        ((world.left + radius, -1e3), (world.left + radius, 1e3)),
        ((world.right - radius, -1e3), (world.right - radius, 1e3)),
        ((-1e3, world.top - radius), (1e3, world.top - radius)),
        ((-1e3, world.bottom + radius), (1e3, world.bottom + radius)),
    ]
    if not world.use_bounds:
        walls = []

    def moving_speed(w):
        x, y = w.x + w.dx * 1e-7, w.y + w.dy * 1e-7
        on_road = any(
            left_x <= x < right_x and bottom_y <= y < top_y
            for left_x, bottom_y, right_x, top_y in world.roads
        )
        return w.speed * (world.road_speed_mult if on_road else 1.0)

    for worker in workers:
        worker.path = [(worker.x, worker.y, worker.t)]
    effects = {id(worker): harvest_effects(worker.upgrades, worker.harvest_bonus) for worker in workers}
    clock_counts = {id(worker): [0, 0, 0, 0] for worker in workers}
    touches = {building_id: 0 for building_id in buildings}
    progress = {building_id: 0 for building_id in buildings}
    spawned = set()
    launch_speed = world.worker_speed
    from .world_tasks import TaskClock

    tasks = TaskClock(world.task_clock, buildings, res_left, resource_kind, resource_tile, world.model_notes)
    world.automatic_gain = tasks.gain

    def add_points(building_id, amount, when):
        nonlocal launch_speed
        building = buildings[building_id]
        if building.get("state") not in CONSTRUCTION_STATES:
            return False
        left = None
        if type(building.get("upg_tgt")) is int and type(building.get("upg_pts")) is int:
            left = max(0, building["upg_tgt"] - building["upg_pts"] - progress[building_id])
        added = amount if left is None else min(amount, left)
        progress[building_id] += added
        if build_points is not None:
            build_points[building_id] = build_points.get(building_id, 0) + added
        if left is not None and amount >= left:
            # RefreshCollider(475E30)는 같은 Info 모양을 다시 등록한다. 상태·재고는 별도로 갱신한다.
            old_state = building.get("state")
            old_value = building.get("effect_value")
            if old_state == "kUpgrading":
                building["lvl"] = int(building.get("lvl", 0)) + 1
            building["state"] = "kNormal"
            if resource_tile(building):
                is_tile[building_id] = True
                res_left[building_id] = max(0, int(building.get("res") or 0))
                if "completion_capacity" in building:
                    building["cap"] = building["completion_capacity"]
            if "task_target_seconds" in building:
                # 다음 레벨의 작업 주기를 게임 getter로 읽지 못했으므로 현재 값으로 확정하지 않는다.
                world.model_notes.add("completion_task_period_not_projected")
                tasks.elapsed[building_id] = 0
            value = building.get("completion_effect_value")
            if type(value) is int:
                building["effect_value"] = value
                if building.get("type") == "kCobbler":
                    if old_state == "kScaffold" or type(old_value) is int:
                        delta = 5.0 * (value - (old_value if old_state == "kUpgrading" else 0)) / 100.0
                        launch_speed += delta
                        for member in workers:
                            if member.t > when:
                                bonus = _game_bonus(member.harvest_bonus, "kHarvestSpeed") or 0
                                member.speed += delta * (1.0 + bonus / 100.0)
                    else:
                        world.model_notes.add("completion_global_bonuses_not_projected")
            elif building.get("type") in HIT_EFFECT_TYPES | {"kCobbler"}:
                world.model_notes.add("completion_global_bonuses_not_projected")
            return True
        return False

    def contact_effect(building_id, w):
        nonlocal duration
        if building_id not in buildings:
            world.model_notes.add("missing_collision_owner")
            return False
        building = buildings[building_id]
        kind = building.get("type")
        changed = False
        if building.get("state") in CONSTRUCTION_STATES:
            changed = add_points(building_id, 1 + (_game_bonus(w.harvest_bonus, "kMoreBuildPts") or 0), w.t)
        n = touches[building_id]
        touches[building_id] += 1
        if building.get("state") == "kScaffold":
            return changed
        if kind in HOUSING_HIT_TYPES and building.get("housing_effect_active") is not True:
            return changed
        value = building.get("effect_value")
        limit = building.get("hit_limit")
        if type(value) not in (int, float) or not math.isfinite(value):
            return changed
        if kind == "kBrickHouse":
            from .game_range import row_in_range

            for other, row in buildings.items():
                if (
                    other != building_id
                    and row.get("state") in CONSTRUCTION_STATES
                    and row_in_range(building, row)
                ):
                    changed = add_points(other, max(0, int(value)), w.t) or changed
        elif kind in ("kHauntedHouse", "kMonastery") and type(limit) is int and n < limit:
            if kind == "kHauntedHouse":
                w.speed *= 1.0 + value / 100.0
            else:
                duration += value / 100.0
            changed = True
        elif (
            kind in ("kBabyWorkerCross", "kBabyWorkerX") and building_id not in spawned and type(limit) is int
        ):
            spawned.add(building_id)
            directions = (
                ((1, 0), (-1, 0), (0, 1), (0, -1))
                if kind == "kBabyWorkerCross"
                else ((1, 1), (1, -1), (-1, 1), (-1, -1))
            )
            for delta_x, delta_y in directions:
                length = math.sqrt(delta_x * delta_x + delta_y * delta_y)
                child = Worker(
                    float(building["x"]),
                    float(building["y"]),
                    delta_x / length,
                    delta_y / length,
                    launch_speed,
                    w.t,
                    owner_id=building_id,
                )
                child.path = [(child.x, child.y, child.t)]
                workers.append(child)
                effects[id(child)] = harvest_effects({}, {})
                clock_counts[id(child)] = [0] * 4
                events.append(None)
            changed = True
        elif kind == "kGoldMine":
            world.model_notes.add("random_gold_mine_yield_not_projected")
        return changed

    def blocks(building_id: int, worker: Worker) -> bool:
        building = buildings.get(building_id) or {}
        t = building.get("type", "")
        if building.get("raycast_enabled") is False or (
            worker.upgrades.get("kPierceBuildings") and not resource_tile(building)
        ):
            return False
        if is_tile.get(building_id):
            if building.get("pickup_enabled", resource_types.get(building_id) == 1):
                return False
            if res_left.get(building_id, 0) <= 0:
                return False
        kind = resource_types.get(building_id)
        if resource_tile(building) and (
            (kind == 3 and worker.upgrades.get("kPierceStone"))
            or (kind == 2 and worker.upgrades.get("kPierceWood"))
        ):
            return False
        return True

    def harvest(building_id: int, worker: Worker):
        nonlocal duration
        n = res_left.get(building_id, 0)
        kind = resource_types.get(building_id)
        if n > 0 and kind is not None:
            amounts, clock, _ = effects[id(worker)]
            n = min(n, amounts[kind])
            res_left[building_id] -= n
            if resource_tile(buildings.get(building_id, {})):
                building = buildings[building_id]
                building["res"] = res_left[building_id]
                building["raycast_enabled"] = res_left[building_id] > 0 and kind != 1
                building["pickup_enabled"] = res_left[building_id] > 0 and kind == 1
            worker.gain[kind] += n
            total[kind] += n
            if collected is not None:
                collected[building_id] = (
                    collected.get(building_id, 0) + n
                )  # 관통 채집도 건물별 접근 증거에 포함한다.
            if clock[kind] and clock_counts[id(worker)][kind] < 20:
                duration += clock[kind] * 0.2
                clock_counts[id(worker)][kind] += 1

    def next_event(worker: Worker):
        if not worker.active or worker.t >= duration or worker.speed <= 0:
            return None
        best = None
        for index, ((start_x, start_y), (end_x, end_y)) in enumerate(walls):
            inside = (
                worker.x >= start_x - 1e-6,
                worker.x <= start_x + 1e-6,
                worker.y <= start_y + 1e-6,
                worker.y >= start_y - 1e-6,
            )[index]
            toward = (worker.dx < 0, worker.dx > 0, worker.dy > 0, worker.dy < 0)[index]
            if inside and toward:
                h = _ray_segment(worker.x, worker.y, worker.dx, worker.dy, start_x, start_y, end_x, end_y)
                if h and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], h[2], None, True, False)
        if _shape_query is not None:
            hit = _shape_query.nearest(worker, effects[id(worker)][2])
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit
        for shape in (() if _shape_query is not None else world.shapes):
            building = buildings.get(shape.bid, {})
            pickup = is_tile.get(shape.bid) and building.get(
                "pickup_enabled", resource_types.get(shape.bid) == 1
            )
            if not shape.is_wall and not pickup and building.get("raycast_enabled") is False:
                continue
            solid = shape.is_wall or (not pickup and blocks(shape.bid, worker))
            pickup_r = effects[id(worker)][2] if pickup else radius
            # 빈 타일은 사건을 만들지 않는다. 통과 채집도 실제 접촉 시각의 사건으로 처리한다.
            if not solid and is_tile.get(shape.bid) and res_left.get(shape.bid, 0) <= 0:
                worker.touching.discard(shape.bid)
                continue
            inside = _inside_shape(shape, worker.x, worker.y, pickup_r)
            if pickup and inside:
                if shape.bid in worker.touching and not worker.just_bounced:
                    continue
                h = (0.0, -worker.dx, -worker.dy)
            else:
                if not inside:
                    worker.touching.discard(shape.bid)
                if not solid and inside:
                    continue
                h = _hit_shape(shape, worker.x, worker.y, worker.dx, worker.dy, pickup_r)
            if not h:
                continue
            if best is None or h[0] < best[0]:
                best = (h[0], h[1], h[2], shape, solid, bool(pickup))
        for left_x, bottom_y, right_x, top_y in world.roads:
            for start_x, start_y, end_x, end_y in (
                (left_x, bottom_y, right_x, bottom_y),
                (right_x, bottom_y, right_x, top_y),
                (right_x, top_y, left_x, top_y),
                (left_x, top_y, left_x, bottom_y),
            ):
                h = _ray_segment(worker.x, worker.y, worker.dx, worker.dy, start_x, start_y, end_x, end_y)
                if h and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], h[2], None, False, False)  # 도로 경계는 반사·타격이 아닌 속도 변화다.
        if best is None or worker.t + best[0] / moving_speed(worker) >= duration:
            return None
        return (worker.t + best[0] / moving_speed(worker), *best)

    # 사건 구조: (게임 시각, 이동 거리, 법선 x/y, 대상 모양, 반사 여부, 픽업 여부).
    # 접촉과 반사는 별개다. 관통도 공사 점수를 줄 수 있고 픽업은 벽 반사가 아니다.
    def refresh_shapes():
        if _shape_query is not None:
            _shape_query.refresh(buildings, res_left, resource_types, is_tile)

    refresh_shapes()
    events = [next_event(worker) for worker in workers]
    for _ in range(max_events):
        ready = [index for index, event in enumerate(events) if event is not None]
        if tasks.next < duration and (not ready or tasks.next < min(events[index][0] for index in ready)):
            when = tasks.next
            for other in workers:
                if other.active and other.t < when:
                    distance = (when - other.t) * moving_speed(other)
                    other.x, other.y, other.t = (
                        other.x + other.dx * distance,
                        other.y + other.dy * distance,
                        when,
                    )
            tasks.tick()
            refresh_shapes()
            events = [next_event(other) for other in workers]
            continue
        if not ready:
            for worker in workers:
                if worker.active and worker.t < duration:
                    rest = (duration - worker.t) * moving_speed(worker)
                    worker.x, worker.y, worker.t = (
                        worker.x + worker.dx * rest,
                        worker.y + worker.dy * rest,
                        duration,
                    )
                    worker.path.append((worker.x, worker.y, worker.t))
            break
        worker_index = min(ready, key=lambda i: (events[i][0], i))
        when, current_distance, normal_x, normal_y, shape, solid, pickup = events[worker_index]
        worker = workers[worker_index]
        worker.x, worker.y, worker.t = (
            worker.x + worker.dx * current_distance,
            worker.y + worker.dy * current_distance,
            when,
        )
        changed = shape is not None and res_left.get(shape.bid, 0) > 0
        if shape is not None and not shape.is_wall:
            harvest(shape.bid, worker)
            if pickup:
                worker.touching.add(shape.bid)
                worker.just_bounced = False
            if not pickup and counts is not None:
                counts[shape.bid] = counts.get(shape.bid, 0) + 1  # 건물에 부딪힌 횟수 (미완성 건물 건설용)
            if not pickup:
                changed = contact_effect(shape.bid, worker) or changed
        if solid:
            worker.path.append((worker.x, worker.y, worker.t))
            worker.dx, worker.dy = reflected(worker.dx, worker.dy, normal_x, normal_y)
            worker.speed = min(MAX_SPEED, worker.speed + SPEED_UP)
            worker.bounces += 1
            worker.just_bounced = True
            if worker.owner_id is not None and worker.bounces >= buildings[worker.owner_id]["hit_limit"]:
                worker.active = False
        elif shape is None:
            worker.path.append(
                (worker.x, worker.y, worker.t)
            )  # 시각 재생에는 도로 진입/이탈 지점도 필요하다.
        worker.x, worker.y = worker.x + worker.dx * 1e-4, worker.y + worker.dy * 1e-4
        if changed:
            # 타일을 비운 바로 그 시각까지만 다른 작업자를 진행시킨다. 그 이후의 반사 후보는 다시 계산한다.
            simultaneous = {}
            for other_index, other in enumerate(workers):
                old = events[other_index]
                if other_index != worker_index and old is not None and old[0] == when:
                    _, _, onx, ony, target, was_solid, was_pickup = old
                    blocking = was_solid if target is None else target.is_wall or blocks(target.bid, other)
                    if (
                        target is None
                        or blocking
                        or not is_tile.get(target.bid)
                        or res_left.get(target.bid, 0) > 0
                    ):
                        simultaneous[other_index] = (when, 0.0, onx, ony, target, blocking, was_pickup)
                if other_index != worker_index and other.active and other.t < when:
                    distance = (when - other.t) * moving_speed(other)
                    other.x, other.y, other.t = (
                        other.x + other.dx * distance,
                        other.y + other.dy * distance,
                        when,
                    )
            refresh_shapes()
            events = [next_event(other) for other in workers]
            for other_index, event in simultaneous.items():
                events[other_index] = event
        else:
            events[worker_index] = next_event(worker)
    else:
        world.model_notes.add("simulation_event_limit")
    return total, workers


# ---- 월드 ↔ 화면 (플러그인이 보낸 기지 모서리 대응점으로 호모그래피) ----
def homography(proj: Sequence[Sequence[float]]):
    """[[월드x, 월드y, 화면x, 화면y], ...] (4개 이상) → 3x3 행렬. 실패하면 None."""
    import numpy as np

    if not proj or len(proj) < 4:
        return None
    rows = []
    for world_x, world_y, screen_x, screen_y in proj:
        rows.append([world_x, world_y, 1, 0, 0, 0, -screen_x * world_x, -screen_x * world_y, -screen_x])
        rows.append([0, 0, 0, world_x, world_y, 1, -screen_y * world_x, -screen_y * world_y, -screen_y])
    _, _, vt = np.linalg.svd(np.array(rows, dtype=float))
    transform_matrix = vt[-1].reshape(3, 3)
    return transform_matrix / transform_matrix[2, 2]


def to_screen(h, x: float, y: float) -> Tuple[float, float]:
    value = h @ [x, y, 1.0]
    return value[0] / value[2], value[1] / value[2]


# ---- 팀 구성과 각도 추천 ----


def team_from_chars(chars_raw: Sequence[dict], order: Optional[Sequence[str]] = None) -> List[dict]:
    """게임 BaseMgr.SetUpActiveWorkers(45D090), CanBeSentToWork(47D090)와 같은 참가 조건.
    금광·자동 발사대 배정, 출전·회복 중, 영향력자는 제외한다. 일반 생산 건물 배정자는 참가한다.
    """
    characters = [
        character
        for character in chars_raw
        if character.get("type")
        and character.get("type") != "kInfluencer"
        and character.get("state") in (None, "kIdle", "kWorking")
        and not (
            character.get("state") == "kWorking"
            and character.get("work") in (None, "kGoldMine", "kIdleLauncher")
        )
    ]
    if order:
        rank = {t: index for index, t in enumerate(order)}
        characters.sort(key=lambda c: rank.get(c["type"], 99))
    result = []
    for character in characters:
        upgrades = character.get("harvest") or {}
        result.append(
            {
                "type": character["type"],
                "upgrades": upgrades,
                "speed": 5.0 + (1.0 if upgrades.get("kHarvestSpeed") else 0.0),
                "harvest_bonus": dict(character.get("harvest_bonus") or {}),
            }
        )
    return result


def team_from_base(base, chars_raw, order=None):
    """조준 때 게임이 확정한 참가자·순서를 우선한다. 일반 기지는 meta로 예측한다."""
    actual = (base.get("geo") or {}).get("launch_team")
    if base.get("state") in ("kAimWorkers", "kBounceWorkers") and isinstance(actual, list):
        if not all(
            isinstance(m, dict)
            and isinstance(m.get("type"), str)
            and isinstance(m.get("upgrades"), dict)
            and isinstance(m.get("harvest_bonus"), dict)
            for m in actual
        ):
            return []
        return [dict(m, speed=5.0) for m in actual]
    return team_from_chars(chars_raw, order)


def run_angle(
    world: World,
    buildings: Dict[int, dict],
    team: Sequence[dict],
    angle: float,
    duration: float,
    counts: Optional[Dict[int, int]] = None,
    build_points: Optional[Dict[int, int]] = None,
    collected: Optional[Dict[int, int]] = None,
) -> Tuple[List[int], List[Worker]]:
    world.model_notes.clear()
    world.automatic_gain = [0, 0, 0, 0]
    if world.use_bounds:
        world.model_notes.add("reconstructed_walls")
    launcher_x, launcher_y = world.launcher
    delta_x, delta_y = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    workers = []
    for index, m in enumerate(team):
        bonuses = dict(m.get("harvest_bonus") or {})
        speed_pct = _game_bonus(bonuses, "kHarvestSpeed")
        speed = (
            world.worker_speed * (1.0 + speed_pct / 100.0)
            if speed_pct is not None
            else world.worker_speed + (m["speed"] - 5.0) * world.worker_speed_mult
        )
        workers.append(
            Worker(
                launcher_x,
                launcher_y,
                delta_x,
                delta_y,
                speed,
                index * world.launch_gap,
                dict(m["upgrades"]),
                harvest_bonus=bonuses,
            )
        )
    return simulate_team(
        world, buildings, workers, duration, counts=counts, build_points=build_points, collected=collected
    )


@dataclass
class AngleResult:
    angle: float
    total: List[int]  # [골드, 밀, 나무, 돌]
    build_hits: int = 0  # 미완성 건물에 맞은 횟수 (건물마다 더 필요한 만큼까지만 셈)
    per_building: Dict[int, int] = field(default_factory=dict)
    build_points: Optional[int] = None  # 목표별 남은 공사 점수로 제한한 진행 점수. None은 구형 결과.
    per_building_points: Dict[int, int] = field(default_factory=dict)
    model_notes: tuple = ()


def angle_score(result: AngleResult, need: int) -> float:
    """공사 진행 점수 우선, 필요한 자원과 나머지 자원 순. 구형 결과만 충돌당 1점으로 비교한다."""
    points = getattr(result, "build_points", None)
    progress = points if points is not None else result.build_hits
    return 100 * progress + result.total[need] + 0.25 * (sum(result.total) - result.total[need])


def rank_angles(
    world: World,
    buildings: Dict[int, dict],
    team: Sequence[dict],
    duration: float,
    need: int,
    targets: Optional[Dict[int, int]] = None,
    angles: Sequence[float] = tuple(range(12, 169, 3)),
) -> List[AngleResult]:
    """각도별 예상 결과. 미완성 건물 건설(targets: 건물 id → 더 필요한 공사 점수)이 먼저, 그다음 필요한 자원,
    다른 자원은 4분의 1 가중."""
    targets = targets or {}
    result = []
    for angle in angles:
        counts: Dict[int, int] = {}
        points: Dict[int, int] = {}
        total, _ = run_angle(world, buildings, team, angle, duration, counts, points)
        per = {
            building_id: min(counts.get(building_id, 0), capacity)
            for building_id, capacity in targets.items()
            if counts.get(building_id)
        }
        per_points = {}
        for building_id, capacity in targets.items():
            progress = points.get(building_id, 0)
            if not buildings.get(building_id, {}).get("state"):
                # 예전 플러그인·자료는 공사 상태가 없다. 호환용 1점/접촉이며 게임의 정확한 점수가 아니다.
                progress = counts.get(building_id, 0)
            if progress > 0:
                per_points[building_id] = min(progress, capacity)
        result.append(
            AngleResult(
                angle,
                total,
                sum(per.values()),
                per,
                sum(per_points.values()),
                per_points,
                tuple(sorted(world.model_notes)),
            )
        )
    result.sort(key=lambda angle_result: -angle_score(angle_result, need))
    return result


def best_angles(
    world: World,
    buildings: Dict[int, dict],
    team: Sequence[dict],
    duration: float,
    need: int,
    angles: Sequence[float] = tuple(range(12, 169, 3)),
) -> List[Tuple[float, List[int]]]:
    """각도별 팀 전체 예상 채집량. 필요한 자원 우선, 다른 자원은 4분의 1 가중."""
    return [
        (angle_result.angle, angle_result.total)
        for angle_result in rank_angles(world, buildings, team, duration, need, None, angles)
    ]
