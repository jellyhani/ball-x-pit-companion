"""계산에 쓰는 입력의 지문. 인원수·건물 이름만 같다는 이유로 다른 물리 결과를 재사용하지 않는다."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def physical_base(base, *, full=False):
    """화면 좌표·투영·날아가는 작업자 관측은 빼고, 모양·상태·범위·속도는 그대로 비교한다."""
    geo = base.get("geo") or {}
    geometry = {
        k: value
        for k, value in geo.items()
        if k
        not in (
            "proj",
            "workers",
            "launcher_source",
            "game_time",
            "physics_time",
            "physics_step",
            "environment_age",
            "world_tick_progress",
        )
    }
    buildings = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    if full:
        from .sim_jobs import full_tiles

        buildings = full_tiles(buildings)
    fields = {
        "id",
        "type",
        "x",
        "y",
        "tw",
        "th",
        "rot",
        "lvl",
        "range",
        "cap",
        "state",
        "worker",
        "res",
        "can_harvest",
        "held",
        "stat",
        "in_range",
        "upg_pts",
        "upg_tgt",
        "upgrade_pct",
        "range_boxes",
        "range_rotation",
        "effect_value",
        "completion_effect_value",
        "housing_effect_active",
        "hit_limit",
        "hits_this_harvest",
        "is_resource",
        "resource_type",
        "raycast_enabled",
        "pickup_enabled",
        "observed_pose",
        "in_range_ids",
    }
    fields.update(
        (
            "task_target_seconds",
            "task_active",
            "is_idle_harvester",
            "production_resource",
            "completion_capacity",
        )
    )
    # 초 단위 진행은 10초 묶음에서 갱신한다. 매 초 바뀌는 입력으로 장시간 탐색을 영원히 버리지 않는다.
    rows = [
        {field_name: value for field_name, value in b.items() if field_name in fields}
        for _, b in sorted(buildings.items())
    ]
    for row, (_, b) in zip(rows, sorted(buildings.items())):
        if not full and "task_seconds" in b:
            row["task_bucket"] = int(b["task_seconds"]) // 10
    return {"geo": geometry, "buildings": rows}


def aim_signature(base, team, duration, need, targets, limits):
    return digest((physical_base(base), team, duration, need, targets, limits))


def layout_signature(base, characters, options, resources, duration, need, limits):
    # 소액의 자동 수입마다 무거운 배치를 다시 찾지 않는다. 구매 가능 수량이 바뀔 때 갱신하고,
    # 실제 표시 직전에는 각 행의 현재 비용을 다시 확인한다.
    budget = tuple(
        min(
            [8]
            + [
                resources[index] // amount if index < len(resources) else 0
                for index, amount in enumerate(option.get("cost") or ())
                if amount > 0
            ]
        )
        for option in options
    )
    return digest((physical_base(base, full=True), characters, options, budget, duration, need, limits))
