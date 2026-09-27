"""성숙한 덱의 빈 칸에서 다음 진화·융합을 준비한다. 실제 DPS 예측과 구분한 조건부 계획이다.

재료/레벨/효과는 게임 catalog, 참가 재료는 게임 1.301 IsAtMaxSolo(CC9440)와
LevelUpUI.PopulateUpgrades(51BBD0)의 단독 볼 조건을 따른다. 아래 점수는 추천 정책이다.
패시브 연계(최대 6), 캐릭터 성향(최대 4), 기존 기록/공략(항목당 최대 2), 준비 비용을 비교하며
게임 피해 수치나 DPS라고 부르지 않는다. 초반 일반 추천과 사용자가 고정한 목표에는 적용하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..i18n import tr
from .passive_value import PROPS, ball_effect


@dataclass(frozen=True)
class GrowthPlan:
    kind: str
    ingredients: tuple
    result: str | None
    missing: tuple
    upgrades: int
    score: float
    summary: str
    benefit: str
    effect: str


def _row(data, item, level):
    rows = data.level_props.get(item) or []
    return rows[level - 1] if level and 0 < level <= len(rows) else {}


def _roles(data, item, row):
    status, damage = data.status_tags(item)
    roles = set()
    if "AOE" in damage or row.get("kLightningLimit", 0) > 1:
        roles.add("aoe")
    if any(k in row for k in ("kMaxPoisonDamage", "kMaxBurnDamage", "kMaxDiseaseDamage", "kBleedAmt")):
        roles.add("status")
    if row.get("kMaxCurseDamage", 0) or "Curse" in status:
        roles.add("curse")
    if row.get("kCellLimit", 0):
        roles.add("clone")
    if any(row.get(k, 0) for k in ("kMaxBabies", "kBabyChance")):
        roles.add("baby")
    if any(row.get(k, 0) for k in ("kHealAmt", "kLifeStealChance")):
        roles.add("sustain")
    if any(row.get(k, 0) for k in ("kFreezeLength", "kSlowPct")):
        roles.add("control")
    return roles


def _mature_effect(data, item, level):
    row = _row(data, item, level)
    if row.get("kCellLimit"):
        effect = tr("타격 시 최대 {count}회 분열", count=row["kCellLimit"])
    elif all(k in row for k in ("kMinPoisonDamage", "kMaxPoisonDamage", "kMaxPoisonStacks")):
        effect = tr("중첩당 초당 {low}~{high} · 최대 {count}중첩", low=row["kMinPoisonDamage"],
                    high=row["kMaxPoisonDamage"], count=row["kMaxPoisonStacks"])
    elif all(k in row for k in ("kMinLightningDamage", "kMaxLightningDamage", "kLightningLimit")):
        effect = tr("번개 피해 {low}~{high} · 최대 {count}명", low=row["kMinLightningDamage"],
                    high=row["kMaxLightningDamage"], count=row["kLightningLimit"])
    else:
        effect = ball_effect(data.level_props.get(item), None, level)
    return tr("레벨 {level} 기본 효과: {effect}", level=level, effect=effect) if effect else ""


def _quality(data, meta, run, items, level):
    rows = [_row(data, item, level) for item in items]
    if not all(rows):
        return None
    roles = set().union(*(_roles(data, item, row) for item, row in zip(items, rows)))
    notes = []
    # 서로 다른 종류의 피해 숫자를 더해 가짜 DPS를 만들지 않는다. 확인된 작동 방식과 강화 대상을 비교한다.
    score = float(min(3, len(roles)))
    links = {"aoe": "kAOEDmgPct", "curse": "kCurseKillChance", "baby": "kFollowerMult"}
    passive_score = 0.
    for owned in run.owned.values():
        if owned.kind != "passive":
            continue
        props = _row(data, owned.item_id, owned.level)
        for role, prop in links.items():
            value = props.get(prop)
            if role in roles and isinstance(value, (int, float)) and value > 0:
                passive_score += value / 10
                notes.append(tr("{passive}의 {effect}와 연결", passive=data.name(owned.item_id),
                                effect=f"{PROPS[prop][0]} {value}%"))
    score += min(6, passive_score)
    favor = {}
    for cid in set(run.character_ids):
        for role, value in data.character_rule(cid).get("strategy", {}).get("favor", {}).items():
            favor[role] = favor.get(role, 0) + value
    fit = sum(favor.get(role, 0) for role in roles)
    score += min(4, max(0, fit))
    if "clone" in roles and roles & {"aoe", "status", "curse"}:
        score += 2  # 분열과 추가 공격 효과를 함께 키우는 계획. 발동 수를 확정하지 않는다.
        notes.append(tr("분열과 타격 효과를 함께 키우는 융합 후보"))
    for item in items:
        rank = meta.damage_rank(item) if meta is not None else None
        if rank and rank[0] <= max(1, rank[1] // 4):
            score += 2
            notes.append(tr("{ball}: 내 누적 피해 기록 상위권", ball=data.name(item)))
        tier = data.community_tier(item)
        score += {"S": 2, "A": 1, "B": 0, "C": -1, "D": -2}.get(tier, 0)
    if fit > 0:
        notes.append(tr("현재 캐릭터 성향과 연결"))
    return score, notes


def plans_for_choices(data, meta, run, session, deck_plan, evals):
    """카드별 최선의 준비 경로. 현재 없는 짝을 이미 보유한 것처럼 취급하지 않는다."""
    p = session.progress
    if (p is None or deck_plan.locked or data.recipe_source != "game" or not data.max_level_known("ball")
            or p.max_balls is None or p.balls is None or p.max_balls <= p.balls or run.unreadable_slots):
        return {}
    balls = [o for o in run.owned.values() if o.kind == "ball"]
    mature = len(balls) >= 2 and all(o.combined or data.recipes_for(o.item_id) for o in balls)
    if not (p.endless or mature):
        return {}
    if any(e.linked for e in evals):
        return {}  # 당장 이어지는 진화/고정 목표는 기존 정책에 맡긴다.
    if any(e.action == "upgrade_ball" for e in evals):
        return {}  # 현재 볼을 강화할 선택지가 있는 경우에도 기존 비교를 유지한다.
    solo = run.solo_balls()
    blocked = set(p.banished)
    offered = {e.card.item_id for e in evals if e.action == "new_ball" and e.evaluated}
    records = getattr(meta, "discovery", None)
    if records is not None:
        available = {i for i, row in records.items() if row.get("available") is True
                     and row.get("in_game") is True and row.get("merged") is False}
    else:
        available = {i for i in (data.available or set()) if i.startswith("ball:") and not data.recipes_for(i)}
        if session.pool:
            available.update(session.pool.new_balls)
    available = (available | offered) - blocked
    free, maximum = p.max_balls - p.balls, data.max_level("ball")
    output = {}
    for e in evals:
        item = e.card.item_id
        if item not in offered or any(w.rule_id in ("slot_full", "char_reduces", "char_sisyphus_direct") for w in e.warnings):
            continue
        routes = []
        for recipe in data.recipes_using(item):
            if recipe.source == "game" and len(recipe.ingredients) == 2 and len(set(recipe.ingredients)) == 2 \
                    and all(i.startswith("ball:") for i in recipe.ingredients):
                routes.append(("evo", recipe.ingredients, recipe.result))
        for partner in (set(solo) | available) - {item}:
            if not any(set(r.ingredients) == {item, partner} for r in data.recipes_using(item)):
                routes.append(("combo", (item, partner), None))
        candidates = []
        for kind, ingredients, result in routes:
            if result and records is not None and records.get(result, {}).get("in_game") is False:
                continue
            partner = next(i for i in ingredients if i != item)
            if partner not in solo and partner not in available:
                continue
            missing = () if partner in solo else (partner,)
            if 1 + len(missing) > free:
                continue
            other = solo.get(partner)
            if other and other.level is None and other.at_max is not True:
                continue
            if other and other.at_max is False and other.level is not None and other.level >= maximum:
                continue  # 현재 게임이 최대 단독 볼이 아니라고 했으므로 레벨 숫자만으로 준비 완료로 보지 않는다.
            upgrades = maximum - 1 + (0 if other and other.at_max is True else
                                     max(0, maximum - other.level) if other else maximum - 1)
            quality = _quality(data, meta, run, (result,) if result else ingredients, 1 if result else maximum)
            if quality is None:
                continue
            value, notes = quality
            score = 8 + value - len(missing) * 2 - upgrades * .5
            names = " + ".join(data.name(i) for i in ingredients)
            path = tr("다음 진화: {parts} → {result}", parts=names, result=data.name(result)) if result else \
                   tr("다음 융합 후보: {parts}", parts=names)
            summary = path + " · " + tr("선택 후 추가 획득 {count} · 강화 {upgrades}", count=len(missing), upgrades=upgrades)
            effect = _mature_effect(data, item, maximum)
            candidates.append(GrowthPlan(kind, tuple(ingredients), result, missing, upgrades, score,
                                         summary, " · ".join(notes[:2]), effect))
        if candidates:
            output[e.card.index] = max(candidates, key=lambda x: (x.score, x.kind == "evo", x.result or "", x.ingredients))
    return output
