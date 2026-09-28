"""자원 타일의 채집 접근 검사. 범위 효과 점수나 수확량 가중치와 별개로 확인한다."""

from __future__ import annotations

from dataclasses import dataclass

from . import harvest_sim as hs
from . import layout_opt as lo
from .layout import (
    Move,
    buildings_from_base,
    grid_from_geo,
    shape_masks,
    occupied,
    building_cells,
    free_spots,
    moved_base,
)
from .layout_opt import in_range
from .layout_city import PRODUCERS
from .layout_guide import preserves_guide
from ..i18n import tr
from .game_range import row_in_range


@dataclass
class AccessReport:
    resources: dict
    collected: dict
    automatic: set

    @property
    def served(self):
        return {index for index, value in self.collected.items() if value > 0} | self.automatic

    @property
    def blocked(self):
        return set(self.resources) - self.served


class ResourceAccess:
    """같은 배치 작업 안에서 각도 검사를 공유한다. 자동 채집은 가동 생산 건물의 실측 범위만 인정한다."""

    def __init__(self, base, team, duration, angles, pad=0.0):
        self.team, self.duration, self.angles, self.pad = team, duration, tuple(angles), pad
        self.cache = {}
        self.producers = set()
        buildings = base.get("buildings") or []
        for building in buildings:
            kind = PRODUCERS.get(building.get("type"))
            counted = building.get("in_range")
            if (
                kind is None
                or type(building.get("worker")) is not int
                or building["worker"] < 0
                or building.get("state") in hs.CONSTRUCTION_STATES
                or not isinstance(counted, dict)
            ):
                continue
            if not all(type(n) is int and n >= 0 for n in counted.values()):
                continue
            nearby = [
                resource_building
                for resource_building in buildings
                if lo.TILE_RES.get(resource_building.get("type")) == kind
                and row_in_range(building, resource_building, pad)
            ]
            if len(nearby) == sum(counted.values()):
                self.producers.add(building["id"])

    def check(self, base):
        geometry = base.get("geo") or {}
        if (
            not self.team
            or not self.angles
            or not all(k in geometry for k in ("left", "right", "bottom", "top", "launcher", "colliders"))
        ):
            return None
        rows = base.get("buildings") or []
        key = tuple(
            sorted(
                (
                    building["id"],
                    building.get("x"),
                    building.get("y"),
                    building.get("rot"),
                    building.get("state"),
                )
                for building in rows
            )
        )
        if key in self.cache:
            return self.cache[key]
        world = hs.world_from_geo(geometry, 0.0)
        if not isinstance(world, hs.World):
            return None
        from .sim_jobs import full_tiles

        buildings = full_tiles({building["id"]: building for building in rows})
        resources = {
            index: building["type"]
            for index, building in buildings.items()
            if building.get("type") in lo.TILE_RES and building.get("state") not in hs.CONSTRUCTION_STATES
        }
        if not resources or not set(resources) <= {s.bid for s in world.shapes}:
            return None  # 자원 충돌 모양이 빠진 입력을 '채집 불가능'으로 오인하지 않는다.
        collected = {}
        from .launch_access import launchable

        for angle in self.angles:
            if not launchable(world, angle):
                continue
            harvest = {}
            hs.run_angle(world, buildings, self.team, angle, self.duration, collected=harvest)
            for index, amount in harvest.items():
                if index in resources:
                    collected[index] = max(collected.get(index, 0), amount)
        automatic = set()
        for index in self.producers:
            building = buildings.get(index)
            if building is None:
                continue
            kind = PRODUCERS[building["type"]]
            for target, typ in resources.items():
                resource_building = buildings[target]
                if lo.TILE_RES[typ] == kind and row_in_range(building, resource_building, self.pad):
                    automatic.add(target)
        report = AccessReport(resources, collected, automatic)
        self.cache[key] = report
        return report

    def preserves(self, base, reference):
        report = self.check(base)
        return reference is None or (report is not None and reference.served <= report.served)


def repair_resources(base, checker, accept=None, max_moves=8, max_candidates=16):
    """채집 경로가 없는 자원 자체를 빈 자리에 옮긴다. 입구·가동 생산·공략 효과·기존 접근을 보호한다.

    먼저 생산 건물 주변과 발사대 앞 후보를 번갈아 검사한다. 범위 점수 2% 문턱은 적용하지 않는다.
    """
    grid = grid_from_geo(base.get("geo") or {})
    initial = checker.check(base)
    if grid is None or initial is None or not initial.blocked:
        return [], base
    entrance = lo.entrance_cells(base.get("geo") or {}, grid)
    lane = lo.lane_values(base.get("geo") or {}, grid)
    current, report, moves = base, initial, []
    raw = {building["id"]: building for building in base.get("buildings") or []}
    served_kinds = {
        lo.TILE_RES[initial.resources[index]] for index in initial.served if index in initial.resources
    }
    targets = sorted(
        initial.blocked,
        key=lambda i: (lo.TILE_RES[initial.resources[i]] in served_kinds, -float(raw[i].get("cap") or 1), i),
    )
    for target in targets:
        if len(moves) >= max_moves:
            break
        if target not in report.blocked:
            continue
        buildings = buildings_from_base(current)
        if target not in buildings:
            continue
        building = buildings[target]
        if building.type in lo.FIXED_TYPES:
            continue
        masks = shape_masks(current.get("geo") or {}, buildings, grid)
        here = building_cells(building, grid, masks.get(target))
        occupied_cells = occupied(buildings, grid, skip=[target], masks=masks)
        width, height = building.footprint
        spots = free_spots(grid, occupied_cells | here | entrance, width, height)
        kind = lo.TILE_RES[building.type]
        producers = [pieces for pieces in buildings.values() if PRODUCERS.get(pieces.type) == kind]

        def production(position):
            return sum(
                in_range(position[0] - pieces.x, position[1] - pieces.y, pieces.range + checker.pad)
                for pieces in producers
            )

        def front(position):
            return sum(lane.get(cell, 0) for cell in grid.cells(*position, width, height))

        def distance(position):
            return (position[0] - building.x) ** 2 + (position[1] - building.y) ** 2

        near_prod = sorted(
            spots,
            key=lambda position: (-production(position), -front(position), distance(position), position),
        )
        near_front = sorted(spots, key=lambda position: (-front(position), distance(position), position))
        candidates = []
        for pair in zip(near_prod, near_front):
            for position in pair:
                if position not in candidates:
                    candidates.append(position)
            if len(candidates) >= max_candidates:
                break
        for position in candidates[:max_candidates]:
            candidate = moved_base(current, buildings, target, position)
            if (
                not lo.preserves_production(base, candidate, checker.pad)
                or not preserves_guide(base, candidate, checker.pad)
                or (accept is not None and not accept(candidate))
            ):
                continue
            checked = checker.check(candidate)
            if checked is None or target not in checked.served or not report.served <= checked.served:
                continue
            moves.append(Move(target, position, 0.0, tr("채집 가능한 자리로 자원 이동")))
            current, report = candidate, checked
            break
    if moves:
        # 옮긴 자원을 뒤에 남겨 두고 미가동 생산 건물만 옛 위치에 남는 모순을 피한다.
        changed_kinds = {lo.TILE_RES[raw[m.a]["type"]] for m in moves}
        for provider in sorted(raw):
            source = raw[provider]
            kind = PRODUCERS.get(source.get("type"))
            if (
                kind not in changed_kinds
                or type(source.get("worker")) is not int
                or source["worker"] >= 0
                or source.get("state") in hs.CONSTRUCTION_STATES
            ):
                continue
            buildings = buildings_from_base(current)
            pieces = buildings.get(provider)
            if pieces is None:
                continue
            tiles = [
                building
                for index, building in buildings.items()
                if lo.TILE_RES.get(building.type) == kind and index in report.resources
            ]

            def covered(position):
                return sum(
                    in_range(building.x - position[0], building.y - position[1], pieces.range + checker.pad)
                    for building in tiles
                )

            count = covered((pieces.x, pieces.y))
            if count == len(tiles):
                continue
            masks = shape_masks(current.get("geo") or {}, buildings, grid)
            occupied_cells = occupied(buildings, grid, skip=[provider], masks=masks)
            spots = free_spots(grid, occupied_cells | entrance, *pieces.footprint)
            spots.sort(
                key=lambda position: (
                    -covered(position),
                    (position[0] - pieces.x) ** 2 + (position[1] - pieces.y) ** 2,
                    position,
                )
            )
            for position in spots[:max_candidates]:
                if covered(position) <= count:
                    break
                candidate = moved_base(current, buildings, provider, position)
                if (
                    not lo.preserves_production(base, candidate, checker.pad)
                    or not preserves_guide(base, candidate, checker.pad)
                    or (accept is not None and not accept(candidate))
                ):
                    continue
                checked = checker.check(candidate)
                if checked is None or not report.served <= checked.served:
                    continue
                moves.append(Move(provider, position, 0.0, tr("생산 건물을 채집 가능한 자원 쪽으로 이동")))
                current, report = candidate, checked
                break
    return moves, current
