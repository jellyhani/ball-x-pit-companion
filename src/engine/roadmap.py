"""현재 보유 상태에서 진화·융합 계획. '재료 보유', '레벨 조건', '지금 가능'을 따로 표시한다.

- 진화: 레시피(게임 연동이면 게임 안 레시피, 아니면 위키)의 재료를 모두 최대 레벨로 가지면 융합 화면에서 고를 수 있다.
- 융합: 서로 다른 최대 레벨 볼 두 개면 조합 제한 없이 가능(게임 설명 tut_fusion). 이미 진화 레시피인 쌍은 뺀다
  (게임 설명 error_combo_evo: 진화하는 두 볼은 일반 융합할 수 없다).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import List, Optional, Set

from ..gamedata import GameData, Recipe
from ..tracking.run_state import RunState


@dataclass
class RoadmapEntry:
    recipe: Recipe
    have: List[str]
    missing: List[str]
    levels_ready: bool        # 가진 재료가 모두 최대 레벨로 확인됨
    levels_known: bool
    upgrades_left: int = 0    # 최대 레벨까지 남은 강화 횟수 (레벨을 아는 재료 기준)

    @property
    def status(self) -> str:
        if self.missing:
            return "재료 1개 부족" if len(self.missing) == 1 else f"재료 {len(self.missing)}개 부족"
        if not self.levels_known:
            return "재료 보유 · 최대 레벨 필요"
        if self.levels_ready:
            return "진화 가능"
        return f"강화 {self.upgrades_left}번 남음" if self.upgrades_left > 0 else "재료 보유 · 강화 필요"

    @property
    def rank(self) -> int:
        if not self.missing and self.levels_ready:
            return 0
        if not self.missing:
            return 1 + max(0, self.upgrades_left)
        return 20 + len(self.missing)


def build_roadmap(run: RunState, data: GameData, include: Optional[Set[str]] = None) -> List[RoadmapEntry]:
    """include: 이 결과로 가는 레시피는 '재료 1개 이상 보유·1개까지만 부족' 조건 없이도 넣는다
    (사용자가 처음부터 목표로 고정한 진화 — 재료가 하나도 없어도 방향을 보여줘야 함)."""
    out: List[RoadmapEntry] = []
    for r in data.recipes:
        have = [i for i in r.ingredients if i in run.owned]
        missing = [i for i in r.ingredients if i not in run.owned]
        forced = include is not None and r.result in include
        if r.result in run.owned or (not forced and (not have or len(missing) > 1)):
            continue
        kind = data.items[r.result].kind
        owned = [run.owned[i] for i in have]
        if not have:
            # forced 목표: 재료를 하나도 안 가졌다 — all([]) 이 참으로 나와 '진화 가능'처럼 보이는 걸 막는다
            known = data.max_level_known(kind)
            ready = False
            left = data.max_level(kind) * len(missing) if known else 0
        elif all(o.at_max is not None for o in owned):
            # 게임 연동: 게임이 알려 준 최대 레벨 여부를 그대로 쓴다
            ready = all(o.at_max for o in owned)
            known = True
            left = 0 if ready else -1
        elif data.max_level_known(kind):
            maxlv = data.max_level(kind)
            levels = [o.level for o in owned]
            known = all(lv is not None for lv in levels)
            left = sum(max(0, maxlv - lv) for lv in levels if lv is not None) + maxlv * len(missing)
            ready = known and all(lv >= maxlv for lv in levels)
        else:
            known, ready, left = False, False, 0
        out.append(RoadmapEntry(r, have, missing, ready, known, left))
    out.sort(key=lambda e: (e.rank, data.name(e.recipe.result)))
    return out


def browsable_targets(data: GameData) -> List[Recipe]:
    """덱 목표 지정 UI 용 — 재료 보유 여부와 상관없이 고를 수 있는 진화 목표 전체 (이름순)."""
    return sorted(data.recipes, key=lambda r: data.name(r.result))


def fusion_pairs(run: RunState, data: GameData) -> List[tuple]:
    """지금 융합할 수 있는 최대 레벨 볼 쌍 (진화 레시피 쌍은 제외)."""
    maxlv = data.max_level("ball")
    known = data.max_level_known("ball")
    balls = sorted(i for i, o in run.owned.items() if o.kind == "ball"
                   and (o.at_max is True or (o.at_max is None and known and o.level is not None and o.level >= maxlv)))
    evo_pairs = {frozenset(r.ingredients) for r in data.recipes if len(r.ingredients) == 2}
    return [(a, b) for a, b in combinations(balls, 2) if frozenset((a, b)) not in evo_pairs]
