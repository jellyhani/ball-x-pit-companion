"""계산에 쓰는 입력의 지문. 인원수·건물 이름만 같다는 이유로 다른 물리 결과를 재사용하지 않는다."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def physical_base(base, *, full=False):
    """화면 좌표·투영·날아가는 작업자 관측은 빼고, 모양·상태·범위·속도는 그대로 비교한다."""
    geo = base.get("geo") or {}
    geometry = {k: v for k, v in geo.items() if k not in ("proj", "workers")}
    buildings = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
    if full:
        from .sim_jobs import full_tiles
        buildings = full_tiles(buildings)
    fields = {"id", "type", "x", "y", "tw", "th", "rot", "lvl", "range", "cap", "state", "worker",
              "res", "can_harvest", "held", "stat", "in_range", "upg_pts", "upg_tgt", "upgrade_pct"}
    # task는 자동생산 진행률이다. 계산에서 사용하지 않는 매 프레임 변화로 완료 결과를 계속 버리지 않는다.
    rows = [{k: v for k, v in b.items() if k in fields} for _, b in sorted(buildings.items())]
    return {"geo": geometry, "buildings": rows}


def aim_signature(base, team, duration, need, targets, limits):
    return digest((physical_base(base), team, duration, need, targets, limits))


def layout_signature(base, chars, options, resources, duration, need, limits):
    # 소액의 자동 수입마다 무거운 배치를 다시 찾지 않는다. 구매 가능 수량이 바뀔 때 갱신하고,
    # 실제 표시 직전에는 각 행의 현재 비용을 다시 확인한다.
    budget = tuple(min([8] + [resources[i] // amount if i < len(resources) else 0
                             for i, amount in enumerate(option.get("cost") or ()) if amount > 0])
                   for option in options)
    return digest((physical_base(base, full=True), chars, options, budget, duration, need, limits))
