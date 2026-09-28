"""지금 덱이 노리는 방향. 추천·삭제 판단과 HUD 의 '덱 방향' 줄이 같은 계획을 본다.

- 단계: 초반 / 중반 / 보스 직전 / 무한의 심연(보스 격퇴 후 '원정 계속', 게임 연동 endless 값).
- 목표 진화: 진화표(roadmap)에서 재료가 다 있거나 하나만 빠진 레시피. 빠진 재료의 칸이 없으면 목표에서 뺀다.
- 원하는 항목(wanted): 목표 진화에 빠진 재료. 핵심 항목(core): 목표 진화에 들어가는 보유 항목.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..domain import RunProgress
from ..gamedata import GameData
from ..tracking.run_state import RunState
from .roadmap import RoadmapEntry, build_roadmap
from ..i18n import tr


@dataclass
class DeckPlan:
    phase: str  # early | mid | boss_soon | endless | unknown
    targets: List[RoadmapEntry] = field(default_factory=list)
    wanted: Dict[str, str] = field(default_factory=dict)  # 빠진 재료 → 진화 결과 이름
    core: Set[str] = field(default_factory=set)
    ball_free: Optional[int] = None
    passive_free: Optional[int] = None
    target_text: str = ""  # 목표 진화 한 줄
    phase_text: str = ""  # 단계별 방침 한 줄
    locked: bool = False  # targets[0] 이 자동 감지가 아니라 사용자가 고정한 목표인지
    locked_wanted: Set[str] = field(default_factory=set)  # 고정 목표로 이어지는 현재 획득 가능한 기본 재료
    locked_core: Set[str] = field(default_factory=set)  # 고정 목표에 필요한 보유 재료 중 아직 강화할 것

    @property
    def text(self) -> str:
        return " · ".join(x for x in (self.target_text, self.phase_text) if x)

    def free_slots(self, kind: str) -> Optional[int]:
        return self.ball_free if kind == "ball" else self.passive_free


def _phase(passive: Optional[RunProgress]) -> str:
    if passive is None:
        return "unknown"
    if passive.endless:
        return "endless"
    tb = passive.turns_to_next_boss if passive.turns_to_next_boss is not None else passive.turns_to_boss
    frac = passive.run_fraction
    if tb is not None and 0 < tb <= max(15, int((passive.final_boss_turn or 0) * 0.12)):
        return "boss_soon"
    if frac is None:
        return "unknown"
    return "early" if frac < 0.4 else "mid"


def build_plan(run_state: RunState, data: GameData, passive: Optional[RunProgress]) -> DeckPlan:
    plan = DeckPlan(_phase(passive))
    if passive is not None:
        if passive.max_balls is not None and passive.balls is not None:
            plan.ball_free = passive.max_balls - passive.balls
        if passive.max_passives is not None and passive.passives is not None:
            plan.passive_free = passive.max_passives - passive.passives
    locked = run_state.locked_target
    roadmap = build_roadmap(run_state, data, include={locked} if locked else None)
    locked_entry = (
        next((e for e in roadmap if e.recipe.result == locked and data.recipe_reachable(e.recipe)), None)
        if locked
        else None
    )
    if locked_entry is not None:
        plan.locked = True
        plan.targets.append(locked_entry)
        name = data.name(locked_entry.recipe.result)
        for m in locked_entry.missing:
            plan.wanted.setdefault(m, name)
        plan.core.update(locked_entry.have)
        plan.locked_wanted, plan.locked_core = _locked_steps(
            run_state, data, locked, set(passive.banished) if passive else set()
        )
        for item_id in plan.locked_wanted:
            plan.wanted.setdefault(item_id, name)
        plan.core.update(plan.locked_core)
    for e in roadmap:
        if e is locked_entry:
            continue
        if e.missing:
            m = e.missing[0]
            free = plan.free_slots(data.items[m].kind)
            if free is not None and free <= 0:
                continue  # 재료를 넣을 칸이 없다
            if any(recipe.result == m for recipe in data.recipes):
                continue  # 빠진 재료가 진화 결과물이면 카드로 나오지 않는다
        if len(plan.targets) < 3:
            plan.targets.append(e)
        name = data.name(e.recipe.result)
        for m in e.missing:
            plan.wanted.setdefault(m, name)
        plan.core.update(e.have)
    plan.target_text, plan.phase_text = _plan_text(plan, data)
    return plan


def _locked_steps(run_state: RunState, data: GameData, result: str, banished: Set[str]):
    """고정 목표를 기본 재료까지 따라간다. 별도 칸이 없는 combined 효과는 진화 재료로 세지 않는다.

    대체 레시피는 이미 가진 직접 재료가 많은 경로를 우선한다. 동률이면 가능한 경로를 모두 남긴다.
    이 정책은 사용자가 고른 목표를 따르기 위한 것이며 피해량이나 승률을 추정한 가중치가 아니다.
    """

    def visit(item_id, seen):
        if item_id in seen:
            return None
        owned = run_state.owned.get(item_id)
        if owned is not None:
            maxed = owned.at_max is True or (
                owned.at_max is None
                and data.max_level_known(owned.kind)
                and owned.level is not None
                and owned.level >= data.max_level(owned.kind)
            )
            return set(), set() if maxed else {item_id}
        if item_id in banished or not data.obtainable(item_id):
            return None
        recipes = data.recipes_for(item_id)
        if not recipes:
            return {item_id}, set()
        paths = []
        for recipe in recipes:
            if not data.recipe_reachable(recipe):
                continue
            children = [visit(index, seen | {item_id}) for index in recipe.ingredients]
            if any(x is None for x in children):
                continue
            paths.append(
                (
                    sum(index in run_state.owned for index in recipe.ingredients),
                    set().union(*(x[0] for x in children)),
                    set().union(*(x[1] for x in children)),
                )
            )
        if not paths:
            return None
        closest = max(x[0] for x in paths)
        return (
            set().union(*(x[1] for x in paths if x[0] == closest)),
            set().union(*(x[2] for x in paths if x[0] == closest)),
        )

    return visit(result, set()) or (set(), set())


def _plan_text(plan: DeckPlan, data: GameData):
    target = phase = ""
    if plan.targets:
        e = plan.targets[0]
        name = data.name(e.recipe.result)
        label = tr("고정 목표") if plan.locked else tr("목표")
        if e.missing:
            need = ", ".join(data.name(m) for m in e.missing)
            target = tr("{label} {name} ({need} 필요)", label=label, name=name, need=need)
        elif e.levels_ready:
            target = tr("{name} 진화 가능 — 융합 화면에서", name=name)
        else:
            target = tr("{label} {name} (재료 강화 중)", label=label, name=name)
    if plan.phase == "endless":
        phase = tr("무한의 심연: 새 항목보다 강화·진화·융합")
    elif plan.phase == "boss_soon":
        phase = tr("보스 직전: 보유 볼 강화 우선")
    return target, phase
