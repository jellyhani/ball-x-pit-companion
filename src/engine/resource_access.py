"""자원 타일의 채집 접근 검사. 범위 효과 점수나 수확량 가중치와 별개로 확인한다."""
from __future__ import annotations

from dataclasses import dataclass

from . import harvest_sim as hs
from . import layout_opt as lo
from .layout import (Move, buildings_from_base, grid_from_geo, shape_masks, occupied,
                     building_cells, free_spots, moved_base)
from .layout_opt import in_range
from .layout_city import PRODUCERS
from .layout_guide import preserves_guide
from ..i18n import tr


@dataclass
class AccessReport:
    resources: dict
    collected: dict
    automatic: set

    @property
    def served(self):
        return {i for i, value in self.collected.items() if value > 0} | self.automatic

    @property
    def blocked(self):
        return set(self.resources) - self.served


class ResourceAccess:
    """같은 배치 작업 안에서 각도 검사를 공유한다. 자동 채집은 가동 생산 건물의 실측 범위만 인정한다."""
    def __init__(self, base, team, duration, angles, pad=0.):
        self.team, self.duration, self.angles, self.pad = team, duration, tuple(angles), pad
        self.cache = {}
        self.producers = set()
        buildings = base.get("buildings") or []
        for b in buildings:
            kind = PRODUCERS.get(b.get("type"))
            counted = b.get("in_range")
            if (kind is None or type(b.get("worker")) is not int or b["worker"] < 0
                    or b.get("state") in hs.CONSTRUCTION_STATES or not isinstance(counted, dict)):
                continue
            if not all(type(n) is int and n >= 0 for n in counted.values()):
                continue
            nearby = [r for r in buildings if lo.TILE_RES.get(r.get("type")) == kind and
                      in_range(r["x"] - b["x"], r["y"] - b["y"], b.get("range", 0) + pad)]
            if len(nearby) == sum(counted.values()):
                self.producers.add(b["id"])

    def check(self, base):
        geo = base.get("geo") or {}
        if not self.team or not self.angles or not all(k in geo for k in ("left","right","bottom","top","launcher","colliders")):
            return None
        rows = base.get("buildings") or []
        key = tuple(sorted((b["id"], b.get("x"), b.get("y"), b.get("rot"), b.get("state")) for b in rows))
        if key in self.cache:
            return self.cache[key]
        world = hs.world_from_geo(geo, .03)
        if not isinstance(world, hs.World):
            return None
        from .sim_jobs import full_tiles
        buildings = full_tiles({b["id"]: b for b in rows})
        resources = {i: b["type"] for i, b in buildings.items() if b.get("type") in lo.TILE_RES
                     and b.get("state") not in hs.CONSTRUCTION_STATES}
        if not resources or not set(resources) <= {s.bid for s in world.shapes}:
            return None  # 자원 충돌 모양이 빠진 입력을 '채집 불가능'으로 오인하지 않는다.
        collected = {}
        for angle in self.angles:
            harvest = {}
            hs.run_angle(world, buildings, self.team, angle, self.duration, collected=harvest)
            for i, amount in harvest.items():
                if i in resources:
                    collected[i] = max(collected.get(i, 0), amount)
        automatic = set()
        for i in self.producers:
            b = buildings.get(i)
            if b is None:
                continue
            kind = PRODUCERS[b["type"]]
            for target, typ in resources.items():
                r = buildings[target]
                if lo.TILE_RES[typ] == kind and in_range(r["x"]-b["x"], r["y"]-b["y"], b.get("range", 0)+self.pad):
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
    raw = {b["id"]: b for b in base.get("buildings") or []}
    served_kinds = {lo.TILE_RES[initial.resources[i]] for i in initial.served if i in initial.resources}
    targets = sorted(initial.blocked, key=lambda i: (lo.TILE_RES[initial.resources[i]] in served_kinds,
                                                     -float(raw[i].get("cap") or 1), i))
    for target in targets:
        if len(moves) >= max_moves:
            break
        if target not in report.blocked:
            continue
        blds = buildings_from_base(current)
        if target not in blds:
            continue
        b = blds[target]
        if b.type in lo.FIXED_TYPES:
            continue
        masks = shape_masks(current.get("geo") or {}, blds, grid)
        here = building_cells(b, grid, masks.get(target))
        occ = occupied(blds, grid, skip=[target], masks=masks)
        w, h = b.footprint
        spots = free_spots(grid, occ | here | entrance, w, h)
        kind = lo.TILE_RES[b.type]
        producers = [p for p in blds.values() if PRODUCERS.get(p.type) == kind]
        def production(pos):
            return sum(in_range(pos[0]-p.x, pos[1]-p.y, p.range+checker.pad) for p in producers)
        def front(pos):
            return sum(lane.get(cell, 0) for cell in grid.cells(*pos, w, h))
        def distance(pos):
            return (pos[0]-b.x)**2 + (pos[1]-b.y)**2
        near_prod = sorted(spots, key=lambda pos: (-production(pos), -front(pos), distance(pos), pos))
        near_front = sorted(spots, key=lambda pos: (-front(pos), distance(pos), pos))
        candidates = []
        for pair in zip(near_prod, near_front):
            for pos in pair:
                if pos not in candidates:
                    candidates.append(pos)
            if len(candidates) >= max_candidates:
                break
        for pos in candidates[:max_candidates]:
            candidate = moved_base(current, blds, target, pos)
            if (not lo.preserves_production(base, candidate, checker.pad)
                    or not preserves_guide(base, candidate, checker.pad)
                    or (accept is not None and not accept(candidate))):
                continue
            checked = checker.check(candidate)
            if checked is None or target not in checked.served or not report.served <= checked.served:
                continue
            moves.append(Move(target, pos, 0., tr("채집 가능한 자리로 자원 이동")))
            current, report = candidate, checked
            break
    if moves:
        # 옮긴 자원을 뒤에 남겨 두고 미가동 생산 건물만 옛 위치에 남는 모순을 피한다.
        changed_kinds = {lo.TILE_RES[raw[m.a]["type"]] for m in moves}
        for provider in sorted(raw):
            source = raw[provider]
            kind = PRODUCERS.get(source.get("type"))
            if (kind not in changed_kinds or type(source.get("worker")) is not int or source["worker"] >= 0
                    or source.get("state") in hs.CONSTRUCTION_STATES):
                continue
            blds = buildings_from_base(current)
            p = blds.get(provider)
            if p is None:
                continue
            tiles = [b for i,b in blds.items() if lo.TILE_RES.get(b.type) == kind and i in report.resources]
            def covered(pos):
                return sum(in_range(b.x-pos[0], b.y-pos[1], p.range+checker.pad) for b in tiles)
            count = covered((p.x,p.y))
            if count == len(tiles):
                continue
            masks = shape_masks(current.get("geo") or {}, blds, grid)
            occ = occupied(blds, grid, skip=[provider], masks=masks)
            spots = free_spots(grid, occ | entrance, *p.footprint)
            spots.sort(key=lambda pos: (-covered(pos), (pos[0]-p.x)**2+(pos[1]-p.y)**2, pos))
            for pos in spots[:max_candidates]:
                if covered(pos) <= count:
                    break
                candidate = moved_base(current,blds,provider,pos)
                if (not lo.preserves_production(base,candidate,checker.pad)
                        or not preserves_guide(base,candidate,checker.pad)
                        or (accept is not None and not accept(candidate))):
                    continue
                checked = checker.check(candidate)
                if checked is None or not report.served <= checked.served:
                    continue
                moves.append(Move(provider,pos,0.,tr("생산 건물을 채집 가능한 자원 쪽으로 이동")))
                current,report = candidate,checked
                break
    return moves, current
