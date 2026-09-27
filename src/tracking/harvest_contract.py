"""발사 순간 입력을 고정한다. 다른 날의 meta를 과거 궤적에 결합하지 않는다.

complete_for_replay는 도우미 입력 확보 여부다. 게임의 모든 프레임과 난수를 재현했다는 뜻은 아니다.
"""

import copy


def capture(base, team, resources, *, game_version="", plugin_version="", sequence=None):
    geo = base.get("geo") or {}
    missing = []
    for field in (
        "game_time",
        "physics_time",
        "launch_team",
        "environment",
        "ball_time_dist",
        "world_tick_progress",
        "world_tick_interval",
        "game_speed",
    ):
        if field not in geo:
            missing.append(field)
    if geo.get("physics_error") or geo.get("team_error"):
        missing.append("bridge_read_error")
    for b in base.get("buildings", []):
        from ..engine.harvest_sim import resource_tile

        if resource_tile(b) or b.get("is_idle_harvester"):
            if not all(k in b for k in ("task_seconds", "task_target_seconds", "task_active")):
                missing.append("building_task:" + str(b.get("id")))
    return copy.deepcopy(
        {
            "schema": 2,
            "game_version": game_version,
            "plugin_version": plugin_version,
            "sequence": sequence,
            "base": base,
            "team": team,
            "resources": list(resources),
            "complete_for_replay": not missing,
            "missing": missing,
        }
    )


def observation(geo, local_time, aim):
    return copy.deepcopy(
        {
            "t": local_time,
            "game_time": geo.get("game_time"),
            "physics_time": geo.get("physics_time"),
            "physics_step": geo.get("physics_step"),
            "w": geo.get("workers") or [],
            "aim": list(aim),
        }
    )
