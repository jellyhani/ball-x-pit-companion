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
    return digest([{k: b[k] for k in fields if k in b} for b in base.get("buildings", [])])


def validate_ranges(base):
    """관측과 수식의 대조 결과를 반환하며 base를 수정하지 않는다.

    checked는 건물 수가 아닌 (효과 발생 건물, 대상 건물) 쌍의 수다.
    mismatches의 각 행은 [발생 ID, 대상 ID, 게임 판정, 수식 판정]이고,
    missing은 대상 영역이 없어 비교하지 못한 ID 목록이다.
    """
    rows = {b["id"]: b for b in base.get("buildings", []) if "id" in b}
    boxes = {i: boxes_from_row(b) for i, b in rows.items()}
    report = {"checked": 0, "mismatches": [], "missing": []}
    for sid, source in rows.items():
        ids = source.get("in_range_ids")
        if not isinstance(ids, list):
            continue
        expected = set(ids)
        for tid, target in rows.items():
            if tid == sid:
                continue
            if boxes[tid] is None:
                if tid not in report["missing"]:
                    report["missing"].append(tid)
                continue
            if not all(k in source and k in target for k in ("x", "y")) or "range" not in source:
                continue
            # row_in_range의 관측값 우선 경로를 부르지 않는다. 같은 값을 자기 자신과 비교하면 안 된다.
            actual = overlaps(
                target["x"] - source["x"], target["y"] - source["y"], source["range"], boxes[tid]
            )
            report["checked"] += 1
            if actual != (tid in expected):
                report["mismatches"].append([sid, tid, tid in expected, actual])
    return report
