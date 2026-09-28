"""BuildingUtl.IsInRange의 대상 영역 겹침. 중심 근사와 구분한다."""

import math


def rotate_boxes(boxes, turns):
    if boxes is None:
        return None
    result = []
    for left_x, bottom_y, right_x, top_y in boxes:
        points = [(left_x, bottom_y), (right_x, bottom_y), (right_x, top_y), (left_x, top_y)]
        for _ in range(turns % 4):
            points = [(y, -x) for x, y in points]
        result.append(
            (
                min(x for x, y in points),
                min(y for x, y in points),
                max(x for x, y in points),
                max(y for x, y in points),
            )
        )
    return tuple(result)


def boxes_from_row(row):
    """관측 당시 영역을 후보의 회전으로 옮긴다. 모양을 읽지 못했으면 None이다."""
    boxes = row.get("range_boxes")
    if not isinstance(boxes, (list, tuple)):
        return None
    if any(
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
        or not all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
        or bounds[0] > bounds[2]
        or bounds[1] > bounds[3]
        for bounds in boxes
    ):
        return None
    return rotate_boxes(boxes, int(row.get("rot", 0)) - int(row.get("range_rotation", row.get("rot", 0))))


def overlaps(delta_x, delta_y, radius, boxes):
    """대상 영역은 상대 좌표다. 경계 접촉도 게임처럼 범위 안으로 인정한다."""
    return any(
        delta_x + left_x <= radius
        and delta_x + right_x >= -radius
        and delta_y + bottom_y <= radius
        and delta_y + top_y >= -radius
        for left_x, bottom_y, right_x, top_y in boxes
    )


def row_in_range(source, target, pad=0.0):
    """현재 배치는 관측을, 이동·회전 후보는 대상 모양의 겹침을 사용한다.

    pad는 모양을 보내지 않던 구형 입력의 보정에만 사용한다. 게임에서 읽은 대상 영역에
    다시 여유를 더하면 범위 밖 대상을 잘못 포함할 수 있다.
    """

    def unchanged(row):
        return row.get("observed_pose") == [row.get("x"), row.get("y"), row.get("rot", 0)]

    if isinstance(source.get("in_range_ids"), list) and unchanged(source) and unchanged(target):
        return target.get("id") in source["in_range_ids"]
    boxes = boxes_from_row(target)
    delta_x, delta_y = target["x"] - source["x"], target["y"] - source["y"]
    if boxes is not None:
        return overlaps(delta_x, delta_y, float(source.get("range", 0)), boxes)
    # 옛 브리지의 근사 경로는 정확한 겹침 자료가 없을 때만 유지한다.
    from .layout_opt import in_range

    return in_range(delta_x, delta_y, float(source.get("range", 0)) + pad)
