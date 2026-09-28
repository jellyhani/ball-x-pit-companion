"""게임의 대상별 실제 포함 판정과 도우미 수식을 독립적으로 비교한다."""

from ..engine.game_range import boxes_from_row, overlaps


def range_signature(base):
    """범위 포함 판정에 영향을 주는 입력만 묶는다. 시계 변화로 전수 대조를 반복하지 않는다."""
    from ..engine.sim_signature import digest

    fields = (
        "id",
        "x",
        "y",
        "rot",
        "range",
        "range_boxes",
        "range_rotation",
        "observed_pose",
        "in_range_ids",
    )
    return digest(
        [
            {field_name: building[field_name] for field_name in fields if field_name in building}
            for building in base.get("buildings", [])
        ]
    )


def validate_ranges(base):
    """관측과 수식의 대조 결과를 반환하며 base를 수정하지 않는다.

    checked는 건물 수가 아닌 (효과 발생 건물, 대상 건물) 쌍의 수다.
    mismatches의 각 행은 [발생 ID, 대상 ID, 게임 판정, 수식 판정]이고,
    missing은 대상 영역이 없어 비교하지 못한 ID 목록이다.
    """
    rows = {building["id"]: building for building in base.get("buildings", []) if "id" in building}
    boxes = {building_id: boxes_from_row(building) for building_id, building in rows.items()}
    report = {"checked": 0, "mismatches": [], "missing": []}
    for source_id, source in rows.items():
        observed_ids = source.get("in_range_ids")
        if not isinstance(observed_ids, list):
            continue
        expected = set(observed_ids)
        for target_id, target in rows.items():
            if target_id == source_id:
                continue
            if boxes[target_id] is None:
                if target_id not in report["missing"]:
                    report["missing"].append(target_id)
                continue
            if (
                not all(field_name in source and field_name in target for field_name in ("x", "y"))
                or "range" not in source
            ):
                continue
            # row_in_range의 관측값 우선 경로를 부르지 않는다. 같은 값을 자기 자신과 비교하면 안 된다.
            actual = overlaps(
                target["x"] - source["x"], target["y"] - source["y"], source["range"], boxes[target_id]
            )
            report["checked"] += 1
            if actual != (target_id in expected):
                report["mismatches"].append([source_id, target_id, target_id in expected, actual])
    return report
