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

F_WHEAT, F_TILE, F_BUILD, F_RESOURCE, F_NO_RAY = 1, 2, 4, 8, 16
# 아래 비트와 모양 번호는 bxp_native.cpp와 공유한다. 표시용 enum처럼 임의로 재정렬하면 안 된다.
U_PIERCE_BUILDINGS, U_PIERCE_STONE, U_PIERCE_WOOD = 1, 2, 4
KIND = {"circle": 0, "box": 1, "poly": 2, "wall": 3, "edge": 4}


def lib():
    """불러온 DLL (한 번만). 쓸 수 없으면 None."""
    global _lib
    if _lib is not None:
        return _lib or None
    _lib = False
    if os.environ.get("BXP_NO_NATIVE") or os.name != "nt" or not os.path.exists(_DLL):
        return None
    try:
        native_library = ctypes.CDLL(_DLL)
        if native_library.bxp_version() != 10:
            return None
        pointer_type = ctypes.POINTER
        double_type, integer_type = ctypes.c_double, ctypes.c_int
        native_library.bxp_next_shape.restype = integer_type
        native_library.bxp_next_shape.argtypes = [
            integer_type, *([pointer_type(integer_type)] * 4),
            *([pointer_type(double_type)] * 3), *([pointer_type(integer_type)] * 3),
            pointer_type(double_type), integer_type, double_type, double_type,
            pointer_type(integer_type), integer_type, pointer_type(double_type),
        ]
        native_library.bxp_simulate_team.restype = integer_type
        native_library.bxp_simulate_team.argtypes = [
            pointer_type(double_type),
            integer_type,
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(double_type),
            pointer_type(double_type),
            pointer_type(double_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            integer_type,
            pointer_type(double_type),
            pointer_type(integer_type),
            double_type,
            integer_type,
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(double_type),
            integer_type,
            pointer_type(double_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            pointer_type(double_type),
            pointer_type(integer_type),
            pointer_type(integer_type),
            integer_type,
            pointer_type(double_type),
            double_type,
            integer_type,
            pointer_type(integer_type),
            pointer_type(integer_type),
        ]
        _lib = native_library
        log.info("네이티브 계산 모듈 사용 (%s)", _DLL)
    except (OSError, AttributeError) as error:
        log.warning("네이티브 계산 모듈을 불러오지 못해 파이썬으로 계산합니다: %s", error)
        return None
    return _lib


def _arr(ctype, values):
    return (ctype * max(1, len(values)))(*values)


class PackedWorld:
    """기지 모양을 DLL 에 넘길 배열로 (World 마다 한 번)."""

    def __init__(self, world):
        self.world_bounds = _arr(
            ctypes.c_double,
            [world.left, world.right, world.bottom, world.top, world.radius, float(world.use_bounds)],
        )
        self.roads = _arr(ctypes.c_double, [value for box in world.roads for value in box])
        shapes = world.shapes
        self.shape_count = len(shapes)
        self.building_ids = [shape.bid for shape in shapes]
        self.shape_types = _arr(
            ctypes.c_int, [KIND[shape.kind] | (8 if shape.environment else 0) for shape in shapes]
        )
        self.shape_building_ids = _arr(ctypes.c_int, self.building_ids)
        point_offsets, point_counts, points, circles, bounds = [], [], [], [], []
        for shape in shapes:
            point_offsets.append(len(points) // 2)
            point_counts.append(len(shape.pts))
            for x, y in shape.pts:
                points += [x, y]
            circles += [shape.c[0], shape.c[1], shape.r]
            bounds += list(shape.bb)
        self.point_offsets, self.point_counts = (
            _arr(ctypes.c_int, point_offsets),
            _arr(ctypes.c_int, point_counts),
        )
        self.vertices, self.circles, self.bounds = (
            _arr(ctypes.c_double, points),
            _arr(ctypes.c_double, circles),
            _arr(ctypes.c_double, bounds),
        )
        # 건물 슬롯: 모양의 건물 id 마다 하나 (id 가 같은 모양은 같은 슬롯 — 자원·횟수가 건물 단위)
        self.slot_ids: List[int] = []
        index: Dict[int, int] = {}
        for building_id in self.building_ids:
            if building_id not in index:
                index[building_id] = len(self.slot_ids)
                self.slot_ids.append(building_id)
        self.shape_slots = _arr(ctypes.c_int, [index[building_id] for building_id in self.building_ids])


class DynamicShapeQuery:
    """재생 등 상태 변화는 호출자가 처리하고, 모양의 교차만 DLL에서 계산한다."""

    def __init__(self, world):
        self.library = lib()
        self.world = world
        self.packed = getattr(world, "_packed", None) or PackedWorld(world)
        world._packed = self.packed
        self.slots = {building_id: index for index, building_id in enumerate(self.packed.slot_ids)}
        slot_count = max(1, len(self.slots))
        self.flags = (ctypes.c_int * slot_count)()
        self.resource_types = (ctypes.c_int * slot_count)()
        self.stocks = (ctypes.c_int * slot_count)()
        self.touching = (ctypes.c_int * slot_count)()
        self.worker_state = (ctypes.c_double * 4)()
        self.result = (ctypes.c_double * 5)()

    def refresh(self, buildings, stocks, resource_types, tiles):
        """동일 시각의 모든 작업자 질의 전에 건물 상태를 한 번 갱신한다."""
        for building_id, slot in self.slots.items():
            building = buildings.get(building_id, {})
            resource_type = resource_types.get(building_id)
            tile = tiles.get(building_id, False)
            self.flags[slot] = (
                (F_WHEAT if tile and building.get("pickup_enabled", resource_type == 1) else 0)
                | (F_TILE | F_RESOURCE if tile else 0)
                | (F_NO_RAY if building.get("raycast_enabled") is False else 0)
            )
            self.resource_types[slot] = -1 if resource_type is None else resource_type
            self.stocks[slot] = stocks.get(building_id, 0)

    def nearest(self, worker, pickup_radius):
        packed = self.packed
        self.worker_state[:] = worker.x, worker.y, worker.dx, worker.dy
        contacts = [(building_id, self.slots[building_id]) for building_id in worker.touching
                    if building_id in self.slots]
        for _, slot in contacts:
            self.touching[slot] = 1
        upgrades = ((U_PIERCE_BUILDINGS if worker.upgrades.get("kPierceBuildings") else 0)
                    | (U_PIERCE_STONE if worker.upgrades.get("kPierceStone") else 0)
                    | (U_PIERCE_WOOD if worker.upgrades.get("kPierceWood") else 0))
        index = self.library.bxp_next_shape(
            packed.shape_count, packed.shape_types, packed.shape_slots,
            packed.point_offsets, packed.point_counts, packed.vertices, packed.circles, packed.bounds,
            self.flags, self.resource_types, self.stocks, self.worker_state, upgrades,
            self.world.radius, pickup_radius, self.touching, int(worker.just_bounced), self.result,
        )
        for building_id, slot in contacts:
            if not self.touching[slot]:
                worker.touching.discard(building_id)
            self.touching[slot] = 0
        if index < 0:
            return None
        distance, normal_x, normal_y, solid, pickup = self.result
        return distance, normal_x, normal_y, self.world.shapes[index], bool(solid), bool(pickup)


def simulate_team(
    world,
    buildings: Dict[int, dict],
    workers: list,
    duration: float,
    max_events: int,
    counts: Optional[Dict[int, int]],
    flags_of,
    build_points: Optional[Dict[int, int]] = None,
    collected: Optional[Dict[int, int]] = None,
) -> Optional[List[int]]:
    """harvest_sim.simulate_team 과 같은 계산. 작업자(Worker)의 위치·경로·획득을 채우고 합계를 돌려준다.
    flags_of(bid) → (flags, rtype, res). 쓸 수 없으면 None (파이썬으로 계산)."""
    native_library = lib()
    if native_library is None:
        return None
    packed_world = getattr(world, "_packed", None)
    if packed_world is None:
        packed_world = world._packed = PackedWorld(world)
    building_flags, resource_types, resource_stocks = [], [], []
    for building_id in packed_world.slot_ids:
        building_flag, resource_type, remaining_stock = flags_of(building_id)
        building_flags.append(building_flag)
        resource_types.append(resource_type)
        resource_stocks.append(remaining_stock)
    slot_count = len(packed_world.slot_ids)
    flags_buffer, resource_type_buffer, resource_buffer = (
        _arr(ctypes.c_int, building_flags),
        _arr(ctypes.c_int, resource_types),
        _arr(ctypes.c_int, resource_stocks),
    )
    worker_count = len(workers)
    worker_buffer = _arr(
        ctypes.c_double,
        [
            value
            for worker in workers
            for value in (worker.x, worker.y, worker.dx, worker.dy, worker.speed, worker.t)
        ],
    )
    upgrade_flags = _arr(
        ctypes.c_int,
        [
            (U_PIERCE_BUILDINGS if worker.upgrades.get("kPierceBuildings") else 0)
            | (U_PIERCE_STONE if worker.upgrades.get("kPierceStone") else 0)
            | (U_PIERCE_WOOD if worker.upgrades.get("kPierceWood") else 0)
            for worker in workers
        ],
    )
    total = (ctypes.c_int * 4)()
    gain = (ctypes.c_int * max(1, 4 * worker_count))()
    hit_counts = (ctypes.c_int * max(1, slot_count))()
    points = (ctypes.c_int * max(1, slot_count))()
    harvested = (ctypes.c_int * max(1, slot_count))()
    from .harvest_sim import _game_bonus

    build_bonus = _arr(
        ctypes.c_int, [(_game_bonus(worker.harvest_bonus, "kMoreBuildPts") or 0) for worker in workers]
    )
    from .harvest_sim import harvest_effects

    effects = [harvest_effects(worker.upgrades, worker.harvest_bonus) for worker in workers]
    amounts = _arr(ctypes.c_int, [value for amount, _, _ in effects for value in amount])
    clocks = _arr(ctypes.c_int, [value for _, clock, _ in effects for value in clock])
    radii = _arr(ctypes.c_double, [radius for _, _, radius in effects])
    clock_counts = (ctypes.c_int * max(1, 4 * worker_count))()
    touching = _arr(
        ctypes.c_int,
        [int(building_id in worker.touching) for worker in workers for building_id in packed_world.slot_ids],
    )
    bounced = _arr(ctypes.c_int, [int(worker.just_bounced) for worker in workers])
    path_capacity = max_events + worker_count + 8
    path = (ctypes.c_double * (4 * path_capacity))()
    # 다음 사건의 시각·거리·법선과 충돌 대상을 작업자별로 저장한다.
    event_values = (ctypes.c_double * max(1, 4 * worker_count))()
    event_kinds = (ctypes.c_int * max(1, 2 * worker_count))()
    path_point_count = native_library.bxp_simulate_team(
        packed_world.world_bounds,
        packed_world.shape_count,
        packed_world.shape_types,
        packed_world.shape_slots,
        packed_world.shape_building_ids,
        packed_world.point_offsets,
        packed_world.point_counts,
        packed_world.vertices,
        packed_world.circles,
        packed_world.bounds,
        flags_buffer,
        resource_type_buffer,
        resource_buffer,
        worker_count,
        worker_buffer,
        upgrade_flags,
        float(duration),
        int(max_events),
        total,
        gain,
        hit_counts,
        path,
        path_capacity,
        event_values,
        event_kinds,
        build_bonus,
        points,
        amounts,
        clocks,
        radii,
        clock_counts,
        harvested,
        len(world.roads),
        packed_world.roads,
        world.road_speed_mult,
        slot_count,
        touching,
        bounced,
    )
    if path_point_count < 0:
        return None
    for index, worker in enumerate(workers):
        worker.x, worker.y, worker.dx, worker.dy, worker.speed, worker.t = worker_buffer[
            6 * index : 6 * index + 6
        ]
        worker.gain = list(gain[4 * index : 4 * index + 4])
        worker.path = []
        worker.touching = {
            building_id
            for other_index, building_id in enumerate(packed_world.slot_ids)
            if touching[index * slot_count + other_index]
        }
        worker.just_bounced = bool(bounced[index])
    for step_index in range(path_point_count):
        worker_index = int(path[4 * step_index])
        workers[worker_index].path.append(
            (path[4 * step_index + 1], path[4 * step_index + 2], path[4 * step_index + 3])
        )
    if counts is not None:
        for slot_index, building_id in enumerate(packed_world.slot_ids):
            if hit_counts[slot_index]:
                counts[building_id] = counts.get(building_id, 0) + hit_counts[slot_index]
    if build_points is not None:
        for slot_index, building_id in enumerate(packed_world.slot_ids):
            if points[slot_index]:
                build_points[building_id] = build_points.get(building_id, 0) + points[slot_index]
    if collected is not None:
        for slot_index, building_id in enumerate(packed_world.slot_ids):
            if harvested[slot_index]:
                collected[building_id] = collected.get(building_id, 0) + harvested[slot_index]
    return list(total)
