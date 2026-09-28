"""배치 최적화 네이티브 계산 (bxp_native.dll 의 bxp_layout_*) — layout_opt 의 Layout·Scorer 를 배열로 넘긴다.

점수 계산은 Scorer.score 와 같은 값(합 순서만 달라 1e-9 안쪽). 담금질·마무리는 같은 규칙, 난수만 다르다.
DLL 이 없으면 모든 함수가 None — layout_opt 는 파이썬으로 계산한다.
"""

from __future__ import annotations

import ctypes
import math
from typing import Optional

from .native import _arr, lib

_pointer_type = ctypes.POINTER
_integer_type, _double_type, _byte_type = ctypes.c_int, ctypes.c_double, ctypes.c_ubyte


class _Model(ctypes.Structure):
    _fields_ = [
        ("grid_column_start", _integer_type),
        ("grid_row_start", _integer_type),
        ("grid_width", _integer_type),
        ("grid_height", _integer_type),
        ("purchased_tiles", _pointer_type(_byte_type)),
        ("world_origin_x", _double_type),
        ("world_origin_y", _double_type),
        ("cell_size", _double_type),
        ("square_range", _integer_type),
        ("piece_count", _integer_type),
        ("piece_widths", _pointer_type(_integer_type)),
        ("piece_heights", _pointer_type(_integer_type)),
        ("movable", _pointer_type(_integer_type)),
        ("cell_offsets", _pointer_type(_integer_type)),
        ("cell_counts", _pointer_type(_integer_type)),
        ("relative_cells", _pointer_type(_integer_type)),
        ("initial_origins", _pointer_type(_integer_type)),
        ("lane_values", _pointer_type(_double_type)),
        ("nonproductive_pieces", _pointer_type(_integer_type)),
        ("resource_tile_values", _pointer_type(_double_type)),
        ("lane_weight", _double_type),
        ("resource_lane_weight", _double_type),
        ("preset_spot_count", _integer_type),
        ("preset_spots", _pointer_type(_integer_type)),
        ("is_preset", _pointer_type(_integer_type)),
        ("preset_weight", _double_type),
        ("preset_clear_weight", _double_type),
        ("effect_count", _integer_type),
        ("effect_pieces", _pointer_type(_integer_type)),
        ("effect_ranges", _pointer_type(_double_type)),
        ("effect_ranges_squared", _pointer_type(_double_type)),
        ("effect_modes", _pointer_type(_integer_type)),
        ("effect_groups", _pointer_type(_integer_type)),
        ("effect_target_offsets", _pointer_type(_integer_type)),
        ("effect_target_counts", _pointer_type(_integer_type)),
        ("effect_targets", _pointer_type(_integer_type)),
        ("effect_target_values", _pointer_type(_double_type)),
        ("harvest_limit", _integer_type),
        ("regeneration_group_count", _integer_type),
        ("partner_offsets", _pointer_type(_integer_type)),
        ("partner_counts", _pointer_type(_integer_type)),
        ("partner_pieces", _pointer_type(_integer_type)),
        ("partner_ranges", _pointer_type(_double_type)),
        ("size_origin_offsets", _pointer_type(_integer_type)),
        ("size_origin_counts", _pointer_type(_integer_type)),
        ("size_origins", _pointer_type(_integer_type)),
        ("range_box_offsets", _pointer_type(_integer_type)),
        ("range_box_counts", _pointer_type(_integer_type)),
        ("range_boxes", _pointer_type(_double_type)),
        ("range_padding", _double_type),
    ]


class _Work(ctypes.Structure):
    _fields_ = [
        ("occupancy", _pointer_type(_integer_type)),
        ("centers_x", _pointer_type(_double_type)),
        ("centers_y", _pointer_type(_double_type)),
        ("regeneration", _pointer_type(_double_type)),
        ("regeneration_set", _pointer_type(_byte_type)),
        ("highest_harvest_values", _pointer_type(_double_type)),
        ("second_harvest_values", _pointer_type(_double_type)),
        ("harvest_set", _pointer_type(_byte_type)),
        ("stamp", _pointer_type(_integer_type)),
        ("temporary_piece_ids", _pointer_type(_integer_type)),
        ("undo", _pointer_type(_integer_type)),
        ("best_origins", _pointer_type(_integer_type)),
        ("candidate_origins", _pointer_type(_integer_type)),
        ("random_state", ctypes.c_uint64),
        ("stamp_generation", _integer_type),
    ]


_ticks_per_second_cache: Optional[float] = None


def _ticks_per_second(native_library) -> float:
    """CPU 시간 카운터(rdtsc) 틱/초 — 한 번 잰다 (DLL 은 C 런타임·시계 함수를 쓰지 않는다)."""
    global _ticks_per_second_cache
    if _ticks_per_second_cache is None:
        import time

        t0, k0 = time.perf_counter(), native_library.bxp_ticks()
        time.sleep(0.05)
        t1, k1 = time.perf_counter(), native_library.bxp_ticks()
        _ticks_per_second_cache = max(1.0, (k1 - k0) / max(1e-6, t1 - t0))
    return _ticks_per_second_cache


def _lib():
    native_library = lib()
    if native_library is None or not hasattr(native_library, "bxp_layout_anneal"):
        return None
    if not getattr(native_library, "_layout_ready", False):
        native_library.bxp_ticks.restype = ctypes.c_uint64
        native_library.bxp_ticks.argtypes = []
        native_library.bxp_layout_score.restype = _double_type
        native_library.bxp_layout_score.argtypes = [
            _pointer_type(_Model),
            _pointer_type(_Work),
            _pointer_type(_integer_type),
        ]
        native_library.bxp_layout_anneal.restype = _double_type
        native_library.bxp_layout_anneal.argtypes = [
            _pointer_type(_Model),
            _pointer_type(_Work),
            _pointer_type(_integer_type),
            _double_type,
            _double_type,
            _double_type,
            ctypes.c_uint64,
            ctypes.c_uint64,
            _pointer_type(_integer_type),
        ]
        native_library.bxp_layout_polish.restype = _double_type
        native_library.bxp_layout_polish.argtypes = [
            _pointer_type(_Model),
            _pointer_type(_Work),
            _pointer_type(_integer_type),
            _double_type,
            ctypes.c_uint64,
        ]
        native_library._layout_ready = True
    return native_library


class LayoutModel:
    """Layout + Scorer → DLL 에 넘길 배열. 배열은 이 객체가 붙잡고 있어야 한다."""

    def __init__(self, layout, scorer, origin0):
        from . import layout_opt as lo

        keep = self._keep = []

        def allocate_buffer(element_type, values):
            buffer = _arr(element_type, list(values))
            keep.append(buffer)
            return buffer

        building_ids = list(layout.pieces)
        self.ids = building_ids
        index_by_building_id = {index: step_index for step_index, index in enumerate(building_ids)}
        pieces_in_order = [layout.pieces[index] for index in building_ids]
        grid = layout.grid
        columns = [c for c, _ in grid.tiles]
        rows = [row for _, row in grid.tiles]
        grid_column_start, grid_row_start = min(columns), min(rows)
        grid_width, grid_height = max(columns) - grid_column_start + 1, max(rows) - grid_row_start + 1
        tile = [0] * (grid_width * grid_height)
        for c, row in grid.tiles:
            tile[(row - grid_row_start) * grid_width + (c - grid_column_start)] = 1
        model = self.model = _Model()
        model.grid_column_start, model.grid_row_start, model.grid_width, model.grid_height = (
            grid_column_start,
            grid_row_start,
            grid_width,
            grid_height,
        )
        model.purchased_tiles = allocate_buffer(_byte_type, tile)
        model.world_origin_x, model.world_origin_y, model.cell_size = grid.ox, grid.oy, grid.size
        model.square_range = 1 if lo.RANGE_SHAPE == "square" else 0
        model.piece_count = len(building_ids)
        model.piece_widths, model.piece_heights = (
            allocate_buffer(_integer_type, [piece.w for piece in pieces_in_order]),
            allocate_buffer(_integer_type, [piece.h for piece in pieces_in_order]),
        )
        model.movable = allocate_buffer(
            _integer_type, [1 if piece.movable else 0 for piece in pieces_in_order]
        )
        range_offsets, range_counts, boxes = [], [], []
        for piece in pieces_in_order:
            range_offsets.append(len(boxes) // 4)
            range_counts.append(-1 if piece.range_boxes is None else len(piece.range_boxes))
            boxes.extend(value for box in (piece.range_boxes or ()) for value in box)
        model.range_box_offsets, model.range_box_counts, model.range_boxes = (
            allocate_buffer(_integer_type, range_offsets),
            allocate_buffer(_integer_type, range_counts),
            allocate_buffer(_double_type, boxes),
        )
        model.range_padding = scorer.pad
        offset, count, relative_cells = [], [], []
        for piece in pieces_in_order:
            offset.append(len(relative_cells) // 2)
            cells = sorted(piece.rel)
            count.append(len(cells))
            for delta_x, delta_y in cells:
                relative_cells += [delta_x, delta_y]
        model.cell_offsets, model.cell_counts, model.relative_cells = (
            allocate_buffer(_integer_type, offset),
            allocate_buffer(_integer_type, count),
            allocate_buffer(_integer_type, relative_cells),
        )
        model.initial_origins = allocate_buffer(
            _integer_type,
            [value for index in building_ids for value in origin0.get(index, layout.origin[index])],
        )
        if scorer.lane:
            lane = [0.0] * (grid_width * grid_height)
            for (c, row), value in scorer.lane.items():
                if (
                    grid_column_start <= c < grid_column_start + grid_width
                    and grid_row_start <= row < grid_row_start + grid_height
                ):
                    lane[(row - grid_row_start) * grid_width + (c - grid_column_start)] = value
            model.lane_values = allocate_buffer(_double_type, lane)
            idle, tiles = set(scorer.lane_ids), set(scorer.lane_tiles)
            model.nonproductive_pieces = allocate_buffer(
                _integer_type, [1 if index in idle else 0 for index in building_ids]
            )
            model.resource_tile_values = allocate_buffer(
                _double_type, [scorer.lane_weight.get(index, 0.0) for index in building_ids]
            )
        else:
            model.lane_values = ctypes.cast(None, _pointer_type(_double_type))
            model.nonproductive_pieces = allocate_buffer(_integer_type, [0] * len(building_ids))
            model.resource_tile_values = allocate_buffer(_double_type, [0.0] * len(building_ids))
        model.lane_weight, model.resource_lane_weight = lo.LANE_W, lo.LANE_TILE_W
        spots = sorted(scorer.preset_spots)
        model.preset_spot_count = len(spots)
        model.preset_spots = allocate_buffer(_integer_type, [value for s in spots for value in s])
        model.is_preset = allocate_buffer(
            _integer_type, [1 if piece.type == scorer.preset_type else 0 for piece in pieces_in_order]
        )
        model.preset_weight, model.preset_clear_weight = lo.PRESET_W, lo.PRESET_CLEAR
        # 효과: 대상별 값을 미리 곱해 둔다 (Scorer.score 와 같은 식)
        mode_of = {"count": 0, "regen": 1, "harvest": 2}
        groups: dict = {}
        (
            effect_pieces,
            effect_ranges,
            effect_ranges_squared,
            effect_modes,
            effect_groups,
            effect_offsets,
            effect_counts,
            target_indices,
            target_values,
        ) = [], [], [], [], [], [], [], [], []
        for effect_id in scorer.effects:
            piece = layout.pieces[effect_id]
            kind, effect_weight, mode, _, _ = lo.EFFECTS[piece.type]
            effect_radius = piece.range + scorer.pad
            effect_pieces.append(index_by_building_id[effect_id])
            effect_ranges.append(effect_radius)
            effect_ranges_squared.append(effect_radius * effect_radius)
            effect_modes.append(mode_of[mode])
            effect_groups.append(groups.setdefault(piece.type, len(groups)) if mode == "regen" else 0)
            effect_offsets.append(len(target_indices))
            target_ids = scorer.targets[kind]
            effect_counts.append(len(target_ids))
            for target_id in target_ids:
                value = (
                    effect_weight
                    * piece.factor
                    * (
                        scorer.res_weight.get(kind, 1.0) * layout.pieces[target_id].cap
                        if isinstance(kind, int)
                        else 1.0
                    )
                )
                if kind == "build" and layout.pieces[target_id].unfinished:
                    value *= lo.UNFINISHED_BUILD_W
                if kind in lo.HUB_KINDS:
                    value *= scorer.hub_w
                target_indices.append(index_by_building_id[target_id])
                target_values.append(value)
        model.effect_count = len(effect_pieces)
        model.effect_pieces, model.effect_ranges, model.effect_ranges_squared = (
            allocate_buffer(_integer_type, effect_pieces),
            allocate_buffer(_double_type, effect_ranges),
            allocate_buffer(_double_type, effect_ranges_squared),
        )
        model.effect_modes, model.effect_groups, model.effect_target_offsets, model.effect_target_counts = (
            allocate_buffer(_integer_type, effect_modes),
            allocate_buffer(_integer_type, effect_groups),
            allocate_buffer(_integer_type, effect_offsets),
            allocate_buffer(_integer_type, effect_counts),
        )
        model.effect_targets, model.effect_target_values = (
            allocate_buffer(_integer_type, target_indices),
            allocate_buffer(_double_type, target_values),
        )
        model.harvest_limit, model.regeneration_group_count = lo.HARVEST_CAP, max(1, len(groups))
        # 담금질 '관련 자리' 상대 (_near_spots 와 같은 목록)
        partner_offsets, partner_counts, partner_indices, partner_ranges = [], [], [], []
        for index in building_ids:
            piece = layout.pieces[index]
            parts = []
            kind = lo.TILE_RES.get(piece.type)
            for effect_id in scorer.effects:
                effect_kind = lo.EFFECTS[layout.pieces[effect_id].type][0]
                if effect_id != index and (effect_kind == kind or effect_kind == "all"):
                    parts.append((index_by_building_id[effect_id], layout.pieces[effect_id].range))
            if piece.type in lo.EFFECTS:
                for target_id in scorer.targets[lo.EFFECTS[piece.type][0]]:
                    if target_id != index:
                        parts.append((index_by_building_id[target_id], piece.range))
            partner_offsets.append(len(partner_indices))
            partner_counts.append(len(parts))
            for other_piece, row in parts:
                partner_indices.append(other_piece)
                partner_ranges.append(row)
        model.partner_offsets, model.partner_counts, model.partner_pieces, model.partner_ranges = (
            allocate_buffer(_integer_type, partner_offsets),
            allocate_buffer(_integer_type, partner_counts),
            allocate_buffer(_integer_type, partner_indices),
            allocate_buffer(_double_type, partner_ranges),
        )
        # 크기별 가능한 자리 (담금질은 건물 크기 + 0~2 영역도 쓴다)
        size_offsets, size_counts, size_origins = [0] * 1024, [0] * 1024, []
        sizes = {
            (piece.w + dw, piece.h + dh)
            for piece in pieces_in_order
            if piece.movable
            for dw in range(3)
            for dh in range(3)
        }
        for size_width, height in sorted(sizes):
            if size_width >= 32 or height >= 32:
                continue
            size_key = size_width * 32 + height
            size_offsets[size_key] = len(size_origins) // 2
            item_count = 0
            for c, row in sorted(grid.tiles):
                if all(
                    (c + delta_x, row + delta_y) in grid.tiles
                    for delta_x in range(size_width)
                    for delta_y in range(height)
                ):
                    size_origins += [c, row]
                    item_count += 1
            size_counts[size_key] = item_count
        model.size_origin_offsets, model.size_origin_counts, model.size_origins = (
            allocate_buffer(_integer_type, size_offsets),
            allocate_buffer(_integer_type, size_counts),
            allocate_buffer(_integer_type, size_origins),
        )
        # 작업 공간
        item_count = len(building_ids)
        workspace = self.work = _Work()
        workspace.occupancy = allocate_buffer(_integer_type, [0] * (grid_width * grid_height))
        workspace.centers_x, workspace.centers_y = (
            allocate_buffer(_double_type, [0.0] * item_count),
            allocate_buffer(_double_type, [0.0] * item_count),
        )
        workspace.regeneration, workspace.regeneration_set = (
            allocate_buffer(_double_type, [0.0] * (model.regeneration_group_count * item_count)),
            allocate_buffer(_byte_type, [0] * (model.regeneration_group_count * item_count)),
        )
        workspace.highest_harvest_values, workspace.second_harvest_values, workspace.harvest_set = (
            allocate_buffer(_double_type, [0.0] * item_count),
            allocate_buffer(_double_type, [0.0] * item_count),
            allocate_buffer(_byte_type, [0] * item_count),
        )
        workspace.stamp, workspace.temporary_piece_ids = (
            allocate_buffer(_integer_type, [0] * item_count),
            allocate_buffer(_integer_type, [0] * (2 * item_count)),
        )
        workspace.undo, workspace.best_origins = (
            allocate_buffer(_integer_type, [0] * (3 * item_count)),
            allocate_buffer(_integer_type, [0] * (2 * item_count)),
        )
        workspace.candidate_origins = allocate_buffer(_integer_type, [0] * (2 * grid_width * grid_height))

    def org(self, origin: dict):
        return _arr(_integer_type, [value for index in self.ids for value in origin[index]])

    def to_dict(self, arr) -> dict:
        return {
            index: (arr[2 * step_index], arr[2 * step_index + 1]) for step_index, index in enumerate(self.ids)
        }


def score(layout, scorer) -> Optional[float]:
    """Scorer.score 의 총점과 같은 값 (비교·검증용)."""
    native_library = _lib()
    if native_library is None or not layout.pieces:
        return None
    layout_model = LayoutModel(layout, scorer, layout.origin)
    return native_library.bxp_layout_score(
        ctypes.byref(layout_model.model), ctypes.byref(layout_model.work), layout_model.org(layout.origin)
    )


def anneal(layout, scorer, seconds: float, seed: int, t0: float, t1: float, origin0: dict, cost: float):
    """layout_opt.anneal 과 같은 담금질. (가장 좋았던 배치, 목표값, 반복 수) 또는 None."""
    native_library = _lib()
    if native_library is None or not layout.pieces:
        return None
    layout_model = LayoutModel(layout, scorer, origin0)
    origins_buffer = layout_model.org(layout.origin)
    iteration_count = _integer_type(0)
    best = native_library.bxp_layout_anneal(
        ctypes.byref(layout_model.model),
        ctypes.byref(layout_model.work),
        origins_buffer,
        cost,
        t0,
        math.log(t1 / t0),
        int(seconds * _ticks_per_second(native_library)),
        (seed & 0xFFFFFFFFFFFFFFFF) or 1,
        ctypes.byref(iteration_count),
    )
    return layout_model.to_dict(origins_buffer), best, iteration_count.value


def polish(layout, scorer, origin0: dict, seconds: float, cost: float):
    """layout_opt.polish 와 같은 마무리. (배치, 목표값) 또는 None."""
    native_library = _lib()
    if native_library is None or not layout.pieces:
        return None
    layout_model = LayoutModel(layout, scorer, origin0)
    origins_buffer = layout_model.org(layout.origin)
    current = native_library.bxp_layout_polish(
        ctypes.byref(layout_model.model),
        ctypes.byref(layout_model.work),
        origins_buffer,
        cost,
        int(seconds * _ticks_per_second(native_library)),
    )
    return layout_model.to_dict(origins_buffer), current
