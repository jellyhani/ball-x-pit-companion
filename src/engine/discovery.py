"""백과사전 목표. 게임의 발견/융합 기록을 사용하며 전투 성능 점수와 섞지 않는다."""

from __future__ import annotations

from collections import Counter
from itertools import product

from ..i18n import tr


def _unseen_pair(records, a, b):
    left, right = records.get(a, {}), records.get(b, {})
    # 기록이 없다는 것과 0회를 구분한다. 한쪽이라도 양수이면 이미 만든 조합이다.
    if not isinstance(left.get("combos"), dict) or not isinstance(right.get("combos"), dict):
        return False
    return not left["combos"].get(b, 0) and not right["combos"].get(a, 0)


def _routes(target, data, records, owned, blocked, seen=frozenset()):
    """상위 볼을 보유 재료/등장 가능한 기본 볼까지 전개한다. 순환과 과도한 분기를 제한한다."""
    if target in seen or len(seen) >= 8:
        return []
    result = []
    for recipe in data.recipes_for(target):
        if recipe.source != "game" or not all(index.startswith("ball:") for index in recipe.ingredients):
            continue
        choices = []
        for part in recipe.ingredients:
            row = records.get(part, {})
            if part in owned:
                choices.append([(Counter({part: 1}), ())])
            elif part in blocked or row.get("in_game") is not True:
                choices.append([])
            elif row.get("available") is True and row.get("merged") is False:
                choices.append([(Counter({part: 1}), ())])
            else:
                choices.append(_routes(part, data, records, owned, blocked, seen | {target}))
        for combination in product(*choices):
            leaves = sum((component[0] for component in combination), Counter())
            steps = tuple(step for component in combination for step in component[1]) + (target,)
            result.append((leaves, steps))
            if len(result) >= 64:
                return result
    return result


def _remaining(leaves, owned, maximum, free_slots):
    """더 필요한 획득·강화를 센다. 복사본마다 레벨이 불명확할 때 과대평가하지 않는다."""
    missing = sum(max(0, n - (owned[index].copies if index in owned else 0)) for index, n in leaves.items())
    if missing > free_slots:
        return None
    upgrades = 0
    for item, count in leaves.items():
        current = owned.get(item)
        if count > 1:  # 이 모형은 복사본별 강화 레벨을 보유하지 않는다.
            return None
        if current is None:
            upgrades += maximum - 1
        elif current.at_max is not True:
            if current.level is None:
                return None
            upgrades += max(0, maximum - current.level)
    return missing, upgrades


def apply_discovery(record, session, run, data, meta):
    """일반 추천을 만든 뒤, 도달 가능한 미발견 목표로 이어지는 카드를 명시적으로 우선한다."""
    records = getattr(meta, "discovery", None)
    if not records or data.recipe_source != "game" or not data.max_level_known("ball"):
        record.discovery_text = tr("백과사전 해금 모드: 게임의 발견 기록을 기다리는 중")
        return record
    if record.status == "auto":
        record.discovery_text = tr("백과사전 해금 모드: 자동 선택 캐릭터입니다")
        return record
    owned = run.solo_balls()  # 이미 융합된 볼을 다시 단독 재료로 추천하지 않는다.
    progress = session.progress
    if progress is None or progress.max_balls is None or run.unreadable_slots:
        record.discovery_text = tr("백과사전 해금 모드: 보유 볼과 빈 슬롯을 확인하는 중")
        return record
    maximum = data.max_level("ball")
    free = max(0, progress.max_balls - sum(o.copies for o in run.owned.values() if o.kind == "ball"))
    blocked = set(progress.banished)
    goals = []
    for target, row in records.items():
        if row.get("obtained") != 0 or row.get("in_game") is not True or target in run.effect_ids:
            continue
        for leaves, steps in _routes(target, data, records, owned, blocked):
            # 현재 가진 볼과 이어지는 경로만 우선한다.
            if any(index in owned for index in leaves):
                goals.append((target, leaves, steps, "evo"))
    candidates = {evaluation.card.item_id for evaluation in record.evals if evaluation.action == "new_ball"}
    pair_items = sorted(set(owned) | candidates)
    for n, a in enumerate(pair_items):
        for ball in pair_items[n + 1 :]:
            if (a in owned or ball in owned) and _unseen_pair(records, a, ball):
                if all(
                    index in owned
                    or (records.get(index, {}).get("available") is True and index not in blocked)
                    for index in (a, ball)
                ):
                    goals.append((None, Counter({a: 1, ball: 1}), (), "combo"))
    choices = {}
    for evaluation in record.evals:
        item = evaluation.card.item_id
        if not evaluation.evaluated or evaluation.action not in ("new_ball", "upgrade_ball"):
            continue
        if any(warning.rule_id == "slot_full" for warning in evaluation.warnings):
            continue
        for target, leaves, steps, kind in goals:
            if item not in leaves:
                continue
            remaining = _remaining(leaves, owned, maximum, free)
            if remaining is None:
                continue
            missing, upgrades = remaining
            if evaluation.action == "new_ball":
                if item in owned or missing == 0:
                    continue
                missing -= 1
            elif (
                item not in owned
                or owned[item].at_max is True
                or not evaluation.level_after
                or not evaluation.level_before
            ):
                continue
            else:
                upgrades -= min(maximum, evaluation.level_after) - min(maximum, evaluation.level_before)
            # 더 필요한 획득·강화·합성 단계가 적은 목표부터. 성능 점수와 별개의 순서다.
            key = (missing, max(0, upgrades) + max(1, len(steps)), 0 if kind == "evo" else 1)
            names = " + ".join(data.name(index) for index in leaves)
            destination = data.name(target) if target else names
            text = (
                tr("미발견 {target}: {parts} → 진화", target=destination, parts=names)
                if target
                else tr("미기록 융합: {parts}", parts=names)
            )
            text += (
                " · "
                + tr("이 선택 후")
                + ": "
                + tr(
                    "추가 획득 {missing} · 재료 강화 {upgrades} · 융합기 필요",
                    missing=missing,
                    upgrades=max(0, upgrades),
                )
            )
            if steps and len(steps) > 1:
                text += (
                    " · "
                    + " → ".join(data.name(index) for index in steps)
                    + " · "
                    + tr("중간 진화 후 추가 강화 필요")
                )
            if evaluation.card.index not in choices or key < choices[evaluation.card.index][0]:
                choices[evaluation.card.index] = (key, text)
    if not choices:
        record.discovery_text = tr("백과사전 해금 모드: 이번 선택지에 연결되는 미해금 목표 없음")
        return record
    ranking = sorted(
        record.ranked,
        key=lambda e: (
            choices[e.card.index][0] if e.card.index in choices else (999, 999, 999),
            -e.score,
            e.card.index,
        ),
    )
    best = ranking[0]
    record.ranked, record.best, record.fallback = ranking, best, None
    record.close_to = [
        e
        for e in ranking[1:]
        if e.card.index in choices and choices[e.card.index][0] == choices[best.card.index][0]
    ]
    record.status, record.confidence = "recommend", tr("해금 우선")
    record.headline = tr("{position} 선택 추천", position=tr(best.card.position))
    record.discovery_text = choices[best.card.index][1]
    record.growth_plan = None
    record.plan_text, record.plan_locked = (
        "",
        False,
    )  # 다른 고정 목표를 같은 HUD에서 동시에 우선하라고 표시하지 않는다.
    record.reroll_status, record.reroll_text = "keep", ""
    record.banish_card, record.banish_text = None, ""
    return record


def apply_fusion_discovery(record, meta, data):
    """게임이 현재 융합 화면에서 허용한 후보 중 미발견/미기록만 앞세운다."""
    records = getattr(meta, "discovery", None)
    if records is None:
        return record
    targets = [
        passive
        for passive in record.evos
        if passive.selectable and records.get(passive.result_id, {}).get("obtained") == 0
    ]
    targets += [
        passive
        for passive in record.combos
        if passive.selectable and len(passive.parts) == 2 and _unseen_pair(records, *passive.parts)
    ]
    if targets:
        best = targets[0]  # 현재 게임에서 실제로 제시한 진화를 먼저, 그 다음 새 융합.
        record.best, record.status = best, "recommend"
        record.headline = tr("해금 우선") + " · " + best.title
        record.notes.insert(0, tr("백과사전 해금 모드: 게임에서 아직 기록되지 않은 후보 우선"))
    return record
