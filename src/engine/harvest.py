"""기지 채집·작업자 조언 (게임 연동 1.5 의 base 정보 + meta).

채집(작업자 발사) — 실제 게임 확인: 작업자는 튕겨 다니므로 조준한 곳 자원만 얻는 게 아니다(돌을 조준했는데 나무가 가장 많이 나옴).
그래서 두 단계로 권한다.
  1) 필요한 자원: 곧 지을·올릴 건물의 부족분, 없으면 보유량이 가장 적은 자원.
  2) 조준: 채집 기록이 3번 이상이면 그 자원이 가장 많이 나온 조준 각도(기록 기반).
     기록이 부족하면 그 자원 건물(남은 자원 가중) 쪽으로 — 튕김 때문에 정확하지 않다고 밝힌다.
작업자: 캐릭터의 채집 강화 이름(kFasterStone 등)과 생산 건물 종류를 맞춘다. 효과 크기는 확인하지 못했다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..gamedata import GameData
from ..tracking.meta_state import RESOURCES, MetaState

# 건물 종류 → 자원 번호 (0 골드, 1 밀, 2 나무, 3 돌). 이름 기준 + 보관 자원(held)으로 보정
RES_BY_TYPE = {
    "kWheatField": 1, "kDenseWheat": 1, "kFarm": 1, "kIdleFarm": 1,
    "kForest": 2, "kGrandTree": 2, "kLumberyard": 2, "kIdleLumberyard": 2, "kTreeFertilizer": 2,
    "kBoulder": 3, "kRockyHill": 3, "kGraniteSlab": 3, "kStonePile": 3, "kStoneMine": 3, "kIdleStoneMine": 3,
    "kGoldMine": 0,
}
# 채집 강화 → 자원
UPGRADE_RES = {"kFasterWheat": 1, "kWheatRange": 1, "kWheatTime": 1, "kFarmSpeed": 1,
               "kFasterWood": 2, "kPierceWood": 2, "kWoodTime": 2, "kLumberyardSpeed": 2,
               "kFasterStone": 3, "kPierceStone": 3, "kStoneTime": 3, "kStoneMineSpeed": 3,
               "kExtraGoldMined": 0}
WORK_BUILDINGS = {"kIdleFarm": 1, "kIdleLumberyard": 2, "kIdleStoneMine": 3, "kGoldMine": 0}
BUCKET = 15          # 조준 각도 묶음 (도)


def building_resource(b: dict) -> Optional[int]:
    held = b.get("held") or []
    if any(held):
        return max(range(len(held)), key=lambda i: held[i])
    return RES_BY_TYPE.get(b.get("type", ""))


@dataclass
class HarvestAdvice:
    need: int                                   # 자원 번호
    need_text: str
    targets: List[Tuple[int, int]] = field(default_factory=list)    # 표시할 건물 화면 좌표
    aim_point: Optional[Tuple[int, int]] = None                     # 조준 추천 지점 (화면 좌표)
    aim_text: str = ""
    learned: bool = False


def need_resource(meta: Optional[MetaState], shortfalls: Dict[str, int]) -> Tuple[int, str]:
    if shortfalls:
        name = max(shortfalls, key=shortfalls.get)
        return RESOURCES.index(name), f"{name} (곧 지을·올릴 건물에 {shortfalls[name]} 부족)"
    res = list(meta.resources) if meta and meta.resources else [0, 0, 0, 0]
    i = min((1, 2, 3), key=lambda k: res[k] if k < len(res) else 0)
    return i, f"{RESOURCES[i]} (보유량이 가장 적음: {res[i] if i < len(res) else 0})"


class HarvestLog:
    """채집 한 번 = 조준 각도들과 자원 증가량. 기지 배치가 그대로면 같은 각도에서 비슷하게 나온다고 본다."""

    def __init__(self, path: str):
        self.path = path
        self.rows: List[dict] = []
        try:
            with open(path, encoding="utf-8") as f:
                self.rows = [json.loads(x) for x in f if x.strip()][-500:]
        except (OSError, ValueError):
            pass
        self._angles: List[float] = []
        self._before: Optional[List[int]] = None
        self._layout: str = ""

    @staticmethod
    def angle(aim_x: float, aim_y: float) -> float:
        return math.degrees(math.atan2(aim_y, aim_x))

    def start(self, resources: List[int], layout: str):
        self._before, self._angles, self._layout = list(resources), [], layout

    def note_aim(self, aim_x: float, aim_y: float):
        if self._before is not None:
            self._angles.append(round(self.angle(aim_x, aim_y), 1))

    def finish(self, resources: List[int]) -> Optional[dict]:
        if self._before is None or not self._angles:
            self._before = None
            return None
        gain = [a - b for a, b in zip(resources, self._before)]
        # 발사 순간의 각도 = 조준 화면(kAimWorkers)의 마지막 표본 (조준을 옮기는 도중 값은 쓰지 않는다)
        row = {"t": time.time(), "angle": self._angles[-1], "gain": gain, "layout": self._layout}
        self._before = None
        self.rows.append(row)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass
        return row

    def best_angle(self, res: int, layout: str) -> Optional[Tuple[float, float, int]]:
        """(각도, 평균 증가량, 표본 수). 같은 배치 기록이 3번 이상일 때만."""
        rows = [r for r in self.rows if r.get("layout") == layout and len(r.get("gain") or []) > res]
        if len(rows) < 3:
            return None
        buckets: Dict[int, List[int]] = {}
        for r in rows:
            buckets.setdefault(int(r["angle"] // BUCKET), []).append(r["gain"][res])
        k, vals = max(buckets.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        return (k + 0.5) * BUCKET, sum(vals) / len(vals), len(vals)


class AimRange:
    """채집 조준 가능 각도 (0° 오른쪽 수평, 90° 위, 180° 왼쪽 수평). 게임 코드에서 기지 조준 제한 값을 찾지 못해,
    조준 화면에서 실제로 나온 조준 방향으로 배운다. 마우스 커서가 조준선보다 3° 넘게 바깥에 있는데 조준 각도가
    그 끝에 멈춰 있으면(게임이 막은 것) 한계로 센다 — 가만히 조준만 하고 있는 것과 구분하기 위해.
    5번 이상 막히면 그쪽 한계로 확정. 확정 전에는 25°~155° 로 추천을 제한한다 (사용자: 162° 는 못 쏨)."""

    DEFAULT = (25.0, 155.0)

    def __init__(self, path: str):
        self.path = path
        self.lo_seen, self.hi_seen = 999.0, -999.0
        self.lo_hits = self.hi_hits = 0
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            self.lo_seen, self.hi_seen = float(d["lo"]), float(d["hi"])
            self.lo_hits, self.hi_hits = int(d.get("lo_hits", 0)), int(d.get("hi_hits", 0))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self._dirty = False

    def note(self, angle: float, mouse_angle: Optional[float] = None):
        """angle: 게임 조준 각도. mouse_angle: 발사대에서 본 마우스 커서 각도 (같은 기준, 왼쪽 아래는 180° 넘게)."""
        a = round(angle, 1)
        lo_block = mouse_angle is not None and mouse_angle < a - 3
        hi_block = mouse_angle is not None and mouse_angle > a + 3
        if a < self.lo_seen - 0.3:
            self.lo_seen, self.lo_hits, self._dirty = a, int(lo_block), True
        elif abs(a - self.lo_seen) <= 0.3 and lo_block:
            self.lo_hits += 1
            self._dirty = True
        if a > self.hi_seen + 0.3:
            self.hi_seen, self.hi_hits, self._dirty = a, int(hi_block), True
        elif abs(a - self.hi_seen) <= 0.3 and hi_block:
            self.hi_hits += 1
            self._dirty = True

    @property
    def limits(self) -> Tuple[float, float]:
        lo = self.lo_seen if self.lo_hits >= 5 else min(self.DEFAULT[0], self.lo_seen)
        hi = self.hi_seen if self.hi_hits >= 5 else max(self.DEFAULT[1], self.hi_seen)
        return lo, hi

    @property
    def learned(self) -> Tuple[bool, bool]:
        return self.lo_hits >= 5, self.hi_hits >= 5

    def save(self):
        if not self._dirty:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"lo": self.lo_seen, "hi": self.hi_seen, "lo_hits": self.lo_hits, "hi_hits": self.hi_hits}, f)
            self._dirty = False
        except OSError:
            pass


# 미완성 건물: 새로 지은 건물은 공사장(kScaffold), 강화한 건물은 강화 공사(kUpgrading) 상태로 시작한다.
# 채집 때 작업자가 부딪힐 때마다 공사 점수가 쌓인다 (게임 BuildingInst.UpgradePts / GetUpgradeTgt,
# 채집 강화 kMoreBuildPts). 남은 타격 수는 플러그인 1.7 부터 정확히 오고, 그 전에는 진행률로 어림한다.
UNFINISHED = {"kScaffold": "공사 중", "kUpgrading": "강화 공사 중"}
FALLBACK_HITS = 10       # 목표 점수를 모를 때 완성까지 필요한 타격 수 어림값 (진행률로 나눠 씀)


@dataclass
class Unfinished:
    id: int
    type: str
    state: str
    pct: float                      # 0~1
    hits_left: int
    exact: bool                     # 남은 타격 수가 게임 값인지 (아니면 어림)
    sx: Optional[int] = None
    sy: Optional[int] = None

    @property
    def label(self) -> str:
        return UNFINISHED.get(self.state, self.state)


def unfinished_buildings(base: Optional[dict], meta: Optional[MetaState]) -> List[Unfinished]:
    """기지 건물 중 미완성인 것 (완성에 가까운 것부터).

    상태는 base.buildings[].state(플러그인 1.7), 없으면 meta.buildings 의 같은 자리에서 가져온다
    (둘 다 게임 MetaSaveData.Buildings 순서 — 실제 기지에서 63개 모두 종류가 같은 순서로 확인).
    """
    blds = (base or {}).get("buildings") or []
    states = [b.get("state") for b in blds]
    if not any(states) and meta is not None and len(meta.buildings) == len(blds) \
            and all(m.type == b.get("type") for m, b in zip(meta.buildings, blds)):
        states = [m.state for m in meta.buildings]
    out = []
    for b, st in zip(blds, states):
        if st not in UNFINISHED or "id" not in b:
            continue
        pct = float(b.get("upgrade_pct") or 0)
        tgt, pts = b.get("upg_tgt"), b.get("upg_pts")
        exact = isinstance(tgt, (int, float)) and tgt > 0
        left = max(1, int(tgt - (pts or 0))) if exact else max(1, math.ceil((1 - pct) * FALLBACK_HITS))
        out.append(Unfinished(b["id"], b.get("type", ""), st, pct, left, exact, b.get("sx"), b.get("sy")))
    out.sort(key=lambda u: (u.hits_left, -u.pct))
    return out


def layout_key(base: dict) -> str:
    """건물 배치 지문: 종류와 위치. 재배치·건설하면 바뀐다."""
    parts = sorted(f"{b.get('type')}@{b.get('x')},{b.get('y')}" for b in base.get("buildings") or [])
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:12]   # 실행마다 같은 값


def advise_harvest(base: dict, meta: Optional[MetaState], shortfalls: Dict[str, int],
                   log: Optional[HarvestLog] = None) -> Optional[HarvestAdvice]:
    need, text = need_resource(meta, shortfalls)
    blds = [b for b in base.get("buildings") or [] if b.get("can_harvest") and building_resource(b) == need
            and "sx" in b and (b.get("res") or 0) > 0]
    adv = HarvestAdvice(need, text, targets=[(b["sx"], b["sy"]) for b in blds])
    player = base.get("player") or []
    learned = log.best_angle(need, layout_key(base)) if log else None
    if learned and len(player) >= 2 and player[0] >= 0:
        ang, avg, n = learned
        r = 420
        # 화면 좌표는 y 가 아래로 커진다 — 게임 조준 벡터(y 위쪽 +)와 반대
        adv.aim_point = (int(player[0] + r * math.cos(math.radians(ang))),
                         int(player[1] - r * math.sin(math.radians(ang))))
        adv.aim_text = f"기록 기반: 이 각도에서 {RESOURCES[need]} 평균 +{avg:.0f} ({n}번)"
        adv.learned = True
    elif blds:
        tot = sum(b["res"] for b in blds)
        adv.aim_point = (int(sum(b["sx"] * b["res"] for b in blds) / tot), int(sum(b["sy"] * b["res"] for b in blds) / tot))
        adv.aim_text = (f"{RESOURCES[need]} 채집지 쪽 (남은 자원 가중) — 튕김 때문에 정확하지 않음, "
                        "채집 기록이 3번 쌓이면 실제 결과로 조준을 추천")
    else:
        adv.aim_text = f"채집할 수 있는 {RESOURCES[need]} 건물이 지금은 없음"
    return adv


@dataclass
class WorkerAdvice:
    char_id: str
    building: str            # 게임 내부 이름
    reason: str
    current: str


def advise_workers(meta: Optional[MetaState], chars_raw: List[dict], building_types: List[str],
                   data: GameData, need: Optional[int] = None) -> List[WorkerAdvice]:
    """생산 건물(유휴 농장·제재소·채석장·금광)에 채집 강화가 맞는 캐릭터를 권한다."""
    out: List[WorkerAdvice] = []
    work_blds = [t for t in building_types if t in WORK_BUILDINGS]
    for t in dict.fromkeys(work_blds):
        res = WORK_BUILDINGS[t]
        best = None
        for c in chars_raw:
            ups = c.get("harvest") or {}
            score = sum(v for k, v in ups.items() if UPGRADE_RES.get(k) == res)
            if score and (best is None or score > best[0]):
                best = (score, c)
        if best is None:
            continue
        c = best[1]
        cid = f"char:{c['type'][1:].lower()}"
        ups = [k[1:] for k, v in (c.get("harvest") or {}).items() if UPGRADE_RES.get(k) == res]
        cur = "이미 여기서 일하는 중" if c.get("work") == t and c.get("state") == "kWorking" else \
            (f"지금 {c.get('work', '')[1:]}에서 일함" if c.get("state") == "kWorking" else "쉬는 중")
        out.append(WorkerAdvice(cid, t, f"{RESOURCES[res]} 채집 강화 보유 ({', '.join(ups)})"
                                + (" · 지금 필요한 자원" if need == res else ""), cur))
    return out


RES_BUILDING_LABEL = {"kIdleFarm": "농장", "kIdleLumberyard": "야적장", "kIdleStoneMine": "채석장"}


def advise_resource_ratio(building_types: List[str], need: Optional[int]) -> Optional[str]:
    """생산 건물(농장·야적장·채석장) 개수가 지금 부족한 자원 쪽으로 안 쏠려 있으면 한 줄 조언.

    근거: Steam 가이드 'My Optimized Town Layout' — "생산량을 소비 속도에 맞추면 시간으로만 제한된다"는
    원칙만 가져온다. 그 공략의 구체적 개수(예: 채석장 11·야적장 4·농장 3)는 그 사람 플레이 스타일(어떤 건물을
    얼마나 강화하는지)에 따라 달라지는 값이라 그대로 쓰지 않고, 이 프로젝트가 이미 갖고 있는 실측 부족 자원
    (need_resource — 블루프린트·강화 비용 기준)과 지금 지은 생산 건물 수를 비교하는 정도로만 쓴다.
    """
    if need not in (1, 2, 3):
        return None
    counts = {1: 0, 2: 0, 3: 0}
    for t in building_types:
        r = WORK_BUILDINGS.get(t)
        if r in counts:
            counts[r] += 1
    mx = max(counts.values())
    if mx < 2 or counts[need] > mx // 2:
        return None
    by_res = {v: RES_BUILDING_LABEL[k] for k, v in WORK_BUILDINGS.items() if v in counts}
    return (f"{RESOURCES[need]} 생산 건물({by_res[need]})이 {counts[need]}개로 다른 자원보다 적음 "
            f"(농장 {counts[1]}·야적장 {counts[2]}·채석장 {counts[3]}) — "
            "생산량을 소비 속도에 맞추면 좋다는 공략 있음 (Steam 'My Optimized Town Layout')")


def gold_bounce_tip(base: dict, unf: List) -> Optional[str]:
    """미완성 건물이 있고 골드마인이 있으면 그쪽으로 조준하라는 커뮤니티 팁 (Steam 가이드
    'My Optimized Town Layout' — 골드마인에 조준하면 촘촘히 튕기며 공사장을 여러 번 맞힌다는 얘기).
    이 프로젝트는 아직 harvest_traces.jsonl 로 확인하지 못했으므로 조준점 자체는 바꾸지 않고
    참고용 문구로만 보여준다."""
    if not unf:
        return None
    if not any(b.get("type") == "kGoldMine" for b in base.get("buildings") or []):
        return None
    return "팁: 골드마인 쪽으로 조준하면 촘촘히 튕기며 미완성 건물이 한 번에 여러 개 진행되기도 함 (커뮤니티 팁, 미확인)"
