"""덱 계열 감지: 지금 가진 볼들이 어느 쪽(화상·빙결·출혈·중독·범위·베이비볼·흡혈 …)으로 모이고 있는지.

아이디어: 공개 도우미 앱들이 '빌드 아키타입'을 정해 두고 추천을 그쪽으로 기울이는 방식 (BallxPitxApp 의 build
archetype detector). 여기서는 그 목록을 가져오지 않고, 게임 데이터의 볼 태그(상태 이상·피해 종류)로 직접 센다.
점수 = 가진 볼마다 (1 + 레벨 보정) + 이번 런 피해 비율 보너스. 가장 큰 계열이 2점 이상이면 덱 계열로 본다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..gamedata import GameData
from ..tracking.run_state import RunState

# 게임 태그 → 계열 (여러 태그가 한 계열로)
AXIS_OF_STATUS = {
    "Burn": "burn", "Darkflame": "burn",
    "Freeze": "freeze", "Slow": "freeze", "Time Snare": "freeze",
    "Bleed": "bleed",
    "Poison": "poison", "Radiation": "poison",
    "Curse": "curse", "Charm": "curse", "Blind": "curse",
    "Baby Ball Spawn": "baby", "Mosquito Spawn": "baby", "Clone": "baby",
    "Lifesteal": "sustain", "Heal": "sustain",
}
AXIS_LABEL = {"burn": "화상", "freeze": "빙결·둔화", "bleed": "출혈", "poison": "중독·방사능", "curse": "저주·매혹·실명",
              "baby": "베이비볼", "sustain": "흡혈·회복", "aoe": "범위 피해"}
# 계열과 맞는 패시브 역할 (passive_value.ROLE)
AXIS_PASSIVE_ROLE = {"aoe": "aoe", "baby": "baby", "sustain": "defense"}
MIN_SCORE = 2.0


@dataclass
class Archetype:
    scores: Dict[str, float] = field(default_factory=dict)
    top: List[str] = field(default_factory=list)      # 덱 계열 (많아야 2개)

    @property
    def text(self) -> str:
        return "·".join(AXIS_LABEL[a] for a in self.top)


def axes_of(data: GameData, item_id: str) -> List[str]:
    it = data.item(item_id)
    if it is None or it.kind != "ball":
        return []
    status, damage = data.status_tags(item_id)
    out = {AXIS_OF_STATUS[s] for s in status if s in AXIS_OF_STATUS}
    if "AOE" in damage:
        out.add("aoe")
    return sorted(out)


def detect(run: RunState, data: GameData) -> Archetype:
    scores: Dict[str, float] = {}
    total_dmg = sum(v for k, v in run.damage.items() if k.startswith("ball:")) or 0
    for iid, owned in run.owned.items():
        axes = set(axes_of(data, iid))
        for c in owned.combined:
            # 퓨전 리액터에서 합쳐 넣은 볼(피뢰침 등)은 인벤토리에서 사라졌지만 효과는 이 볼에 남는다 →
            # 그 볼의 계열도 이 볼의 정체성으로 센다 (예: 산사태 + 피뢰침 → 산사태가 범위 계열에 더해 시너지도 가짐)
            axes.update(axes_of(data, c))
        if not axes:
            continue
        lvl = owned.level or 1
        share = (run.damage.get(iid, 0) / total_dmg) if total_dmg else 0.0
        w = 1.0 + 0.3 * (lvl - 1) + 2.0 * share
        for a in axes:
            scores[a] = scores.get(a, 0.0) + w
    ranked: List[Tuple[str, float]] = sorted(scores.items(), key=lambda kv: -kv[1])
    top = [a for a, v in ranked[:2] if v >= MIN_SCORE]
    if len(top) == 2 and ranked[1][1] < 0.6 * ranked[0][1]:
        top = top[:1]                                  # 둘째가 한참 작으면 한 계열
    return Archetype(dict(scores), top)
