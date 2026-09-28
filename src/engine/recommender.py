"""선택지별 '행동 후 상태'를 비교하는 설명 가능한 규칙 기반 추천.

순서: 인식 못 한 카드 제외 → 행동 종류 결정(새 항목/강화) → 행동 후 레벨 계산 →
      진화 재료 · 게임 시너지 판정 · 캐릭터 특성 · 런 진행 상황(체력·빈 칸·보스까지 남은 턴) 규칙 →
      차이가 작으면 '차이 작음', 근거가 없으면 '판단 보류'. 그다음 새로고침·삭제 판단.

점수는 순서를 정하는 내부 값일 뿐 화면에 보여 주지 않는다. 모든 이유에는 rule_id 가 붙는다.
근거: 게임 연동 값(시너지·자동 선택 AI·최대 레벨·진행 상황), data/rules.json(공식 설명문), 진화 레시피
(게임 연동이면 게임 안 레시피, 아니면 위키).

최대 레벨: 게임이 알려 준 값(단독 최대 kMaxSoloLvl, 볼별 IsAtMaxSolo)으로만 확정한다. 확정 전에는
'진화까지 N번' 같은 숫자를 말하지 않는다. 게임 값 kMaxSoloLvl=2(0부터) → 화면 레벨 3이 단독 최대.
(예전에 '레벨 3 볼에 강화 카드'로 보였던 것은 게임 레벨을 0부터 세는 줄 모르고 읽은 착오였다.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import comb
from typing import List, Optional, Tuple

from ..domain import Card, ChoiceSession, RunProgress
from ..gamedata import GameData
from ..tracking.run_state import RunState
from .deck_plan import DeckPlan, build_plan
from .passive_value import ROLE_LABEL, ball_effect, passive_effect
from .archetype import AXIS_LABEL, AXIS_PASSIVE_ROLE, Archetype, axes_of, detect as detect_archetype
from .growth_plan import GrowthPlan, plans_for_choices
from ..tracking.meta_state import MetaState
from ..i18n import tr

BASE_RULES = {
    "base_new_ball",
    "base_upgrade_ball",
    "base_new_passive",
    "base_upgrade_passive",
    "base_new_pet",
    "base_upgrade_pet",
    "pet_desc",
}
CLOSE_MARGIN = 6.0


@dataclass(frozen=True)
class Reason:
    rule_id: str
    text: str
    weight: float
    short: str = ""  # 다른 선택지 목록에 쓰는 짧은 표현


@dataclass
class ActionEval:
    card: Card
    action: str  # new_ball | upgrade_ball | new_passive | upgrade_passive | unknown
    level_before: Optional[int] = None
    level_after: Optional[int] = None
    score: float = 0.0
    reasons: List[Reason] = field(default_factory=list)
    warnings: List[Reason] = field(default_factory=list)
    effect: str = ""  # 이 선택의 실제 수치 변화 (게임 레벨별 수치, 예: '받는 피해 감소 10% → 20%')
    target_step: bool = False  # 사용자 고정 목표의 다음 재료 획득/강화. 게임 성능 점수와 구분한다.
    growth_plan: Optional[GrowthPlan] = None

    @property
    def evaluated(self) -> bool:
        return self.action != "unknown"

    @property
    def action_text(self) -> str:
        if self.action in ("new_ball", "new_passive"):
            return tr("새 볼") if self.action == "new_ball" else tr("새 패시브")
        if self.action in ("upgrade_ball", "upgrade_passive"):
            if self.level_before and self.level_after:
                return tr(
                    "레벨 {level_before} → {level_after}",
                    level_before=self.level_before,
                    level_after=self.level_after,
                )
            if self.level_after:
                return tr("레벨 {level_after}로 강화", level_after=self.level_after)
            return tr("강화")
        return tr("미확인")

    @property
    def strong(self) -> bool:
        return any(reason.rule_id not in BASE_RULES for reason in self.reasons)

    @property
    def linked(self) -> bool:
        """진화 레시피로 현재 덱과 이어지는지."""
        return (
            self.target_step
            or self.growth_plan is not None
            or any(reason.rule_id.startswith(("evo_", "passive_recipe")) for reason in self.reasons)
        )

    @property
    def plan_blocked(self) -> bool:
        """덱 계획상 안 고르는 편이 나은 새 항목 (마지막 칸·무한 모드에서 진화와 안 이어짐).
        캐릭터 궁합 같은 가산점이 있어도 '확실 추천' 으로 올리지 않는다 — 새로고침이 나은 상황."""
        return any(warning.rule_id in ("last_slot", "endless_unlinked") for warning in self.warnings)

    def top_reasons(self, n: int = 2, *, include_growth: bool = True) -> List[Reason]:
        return sorted(
            (
                reason
                for reason in self.reasons
                if reason.rule_id not in BASE_RULES and (include_growth or reason.rule_id != "growth_plan")
            ),
            key=lambda r: -r.weight,
        )[:n]


@dataclass
class Recommendation:
    session_id: int
    rules_version: str
    status: str  # recommend | close | hold | auto | none
    headline: str
    best: Optional[ActionEval]
    evals: List[ActionEval]  # 게임 화면 순서
    close_to: List[ActionEval] = field(default_factory=list)
    reroll_status: str = ""
    reroll_text: str = ""
    banish_text: str = ""
    banish_card: Optional[Card] = None
    situation: str = ""  # 진행 상황 한 줄 (체력·보스·칸)
    plan_text: str = ""  # 덱 방향 한 줄 (목표 진화·단계)
    plan_locked: bool = False  # 사용자가 고정한 덱 목표가 있음 (HUD 가 이 줄을 밀어내지 않게)
    reroll_odds: Optional[Tuple[float, int, int]] = None  # (목표 카드 확률, 후보 수, 목표 카드 수) — 추정
    fallback: Optional[ActionEval] = None  # 판단 보류일 때 무난한 선택 (근거 약함, 참고용)
    limitations: List[str] = field(default_factory=list)
    ranked: List[ActionEval] = field(default_factory=list)  # 읽은 카드 전체의 순위 (보류여도 순서는 있다)
    confidence: str = ""  # 확실 | 추천 | 근소 | 근거 약함 — 1위와 2위의 차이·근거로 정한다
    discovery_text: str = ""  # 백과사전 목표. 전투 성능 이유와 분리해서 표시한다.
    growth_plan: Optional[GrowthPlan] = None  # 선택한 카드의 장기 성장 계획. 해금 모드가 우선하면 비운다.


def card_rank(recommendation: "Recommendation", evaluation: ActionEval) -> Optional[int]:
    for index, x in enumerate(recommendation.ranked, 1):
        if x is evaluation:
            return index
    return None


def card_badge(recommendation: "Recommendation", evaluation: ActionEval) -> str:
    """게임 화면 카드 이름표: 순위와 판정을 한 번에 (예: '1위 확실', '2위 비슷함', '3위')."""
    value = card_verdict(recommendation, evaluation)
    n = card_rank(recommendation, evaluation)
    if value == "unknown" or n is None:
        return tr("읽지 못함")
    if value == "banish":
        return tr("{n}위 · 삭제 추천", n=n)
    if value == "best":
        return tr("1위 {v0}", v0=recommendation.confidence or tr("추천"))
    if value == "alt":
        if recommendation.growth_plan is not None and evaluation.growth_plan is not None:
            return tr("{n}위 · 성장 대안", n=n)
        return tr("{n}위 비슷함", n=n)
    if value in ("neutral", "pick"):
        return tr("1위 무난") if n == 1 else tr("{n}위", n=n)
    return tr("{n}위 비추천", n=n)


def card_verdict(recommendation: "Recommendation", evaluation: ActionEval) -> str:
    """카드 하나의 판정: best(추천) | alt(비슷함) | banish(삭제 추천) | skip(비추천) | neutral(보류) | unknown.

    HUD 목록과 게임 화면 카드 테두리가 같은 판정·같은 색을 쓴다.
    """
    if not evaluation.evaluated:
        return "unknown"
    if recommendation.banish_card is not None and evaluation.card is recommendation.banish_card:
        return "banish"
    if recommendation.best is not None and evaluation is recommendation.best:
        return "best"
    if recommendation.best is None:
        # 판단 보류: 확신은 못 하지만 그나마 나은 하나('무난')는 짚어 준다 — 전부 회색이면 뭘 골라야 할지 알 수 없다
        return (
            "pick"
            if recommendation.fallback is not None and evaluation is recommendation.fallback
            else "neutral"
        )
    if evaluation in recommendation.close_to:
        return "alt"
    if recommendation.growth_plan is not None and evaluation.growth_plan is not None:
        return "alt"
    return "skip"


def situation_text(progress: Optional[RunProgress]) -> str:
    if progress is None:
        return ""
    parts = []
    hr = progress.health_ratio
    if hr is not None:
        parts.append(tr("체력 {v0}%", v0=round(hr * 100)))
    tb = progress.turns_to_boss
    nb = progress.turns_to_next_boss
    if nb is not None and nb > 0:
        parts.append(tr("다음 보스 {nb}턴", nb=nb))
    elif tb is not None and tb > 0 and not progress.endless:
        parts.append(tr("보스까지 {tb}턴", tb=tb))
    nf = progress.turns_to_fuser
    if nf is not None and nf > 0:
        parts.append(tr("융합기 {nf}턴", nf=nf))
    if progress.max_balls and progress.balls is not None:
        parts.append(tr("볼 {balls}/{max_balls}", balls=progress.balls, max_balls=progress.max_balls))
    if progress.max_passives is not None and progress.passives is not None:
        parts.append(
            tr(
                "패시브 {passives}/{max_passives}",
                passives=progress.passives,
                max_passives=progress.max_passives,
            )
        )
    return " · ".join(parts)


class Recommender:
    def __init__(self, data: GameData):
        self.data = data
        self.discovery_mode = False
        self.meta: Optional[MetaState] = None  # 게임 연동 meta (내 누적 기록)
        self.draw_weights: Optional[dict] = None  # 뽑기 관측으로 추정한 종류별 가중치 (없으면 균등 가정)
        self.history: List = []  # 내 런 기록 (RunRecord) — 캐릭터별 항목 성적

    # ---- 공개 ----
    def recommend(self, session: ChoiceSession, run_state: RunState) -> Recommendation:
        recommendation = self._recommend_normal(session, run_state)
        if self.discovery_mode:
            from .discovery import apply_discovery

            return apply_discovery(recommendation, session, run_state, self.data, self.meta)
        return recommendation

    def _recommend_normal(self, session: ChoiceSession, run_state: RunState) -> Recommendation:
        game_data = self.data
        version = game_data.rules.get("version", "")
        limitations = run_state.limitations(game_data)
        unknown = session.unknown_count
        if unknown:
            limitations.insert(
                0, tr("읽지 못한 카드 {unknown}장 — 확인된 선택지끼리만 비교", unknown=unknown)
            )
        progress = session.progress

        auto = [
            character_id
            for character_id in run_state.character_ids
            if game_data.character_rule(character_id).get("auto_selects_upgrades")
        ]
        plan = build_plan(run_state, game_data, progress)
        self._arch = detect_archetype(run_state, game_data)
        # 캐릭터 궁합은 archetype.detect 가 볼 점수와 함께 합산한다 (두 캐릭터 모두, 볼이 쌓인 뒤에도)
        evals = [self._evaluate(card, run_state, progress, plan) for card in session.cards]
        situation = situation_text(progress)
        if auto:
            return Recommendation(
                session.session_id,
                version,
                "auto",
                tr("이 캐릭터는 강화를 자동 선택합니다"),
                None,
                evals,
                situation=situation,
                plan_text=plan.text,
                limitations=limitations,
            )
        known = [evaluation for evaluation in evals if evaluation.evaluated]
        if not known:
            return Recommendation(
                session.session_id,
                version,
                "none",
                tr("선택지를 읽지 못했습니다"),
                None,
                evals,
                reroll_status="unknown",
                reroll_text=tr("새로고침 판단 보류"),
                situation=situation,
                plan_text=plan.text,
                limitations=limitations,
            )

        growth = plans_for_choices(game_data, self.meta, run_state, session, plan, evals)
        for evaluation in known:
            if evaluation.card.index not in growth:
                continue
            evaluation.growth_plan = growth[evaluation.card.index]
            evaluation.warnings = [
                warning
                for warning in evaluation.warnings
                if warning.rule_id not in ("endless_unlinked", "last_slot")
            ]
            evaluation.reasons.append(
                Reason(
                    "growth_plan",
                    evaluation.growth_plan.summary,
                    evaluation.growth_plan.score,
                    tr("다음 성장 준비"),
                )
            )
            evaluation.score = sum(reason.weight for reason in evaluation.reasons) + sum(
                warning.weight for warning in evaluation.warnings
            )

        # 고정 목표는 임의 점수를 더하지 않고 명시적인 선택 방침으로 적용한다.
        # 기존 생존 규칙의 위험 기준(체력 35% 미만)에서는 회복/방어 선택을 먼저 둔다.
        target_active = any(evaluation.target_step for evaluation in known)
        low_hp = progress is not None and progress.health_ratio is not None and progress.health_ratio < 0.35

        def priority(e):
            if not target_active and not growth:
                return 0
            survival = low_hp and any(
                reason.rule_id in ("heal_low_hp", "passive_defense_low_hp") for reason in e.reasons
            )
            if survival and not any(
                warning.rule_id in ("slot_full", "char_reduces") for warning in e.warnings
            ):
                return 2
            return 1 if e.target_step else 0

        ranked = sorted(known, key=lambda e: (-priority(e), -e.score, e.card.index))
        best = ranked[0]
        if target_active and priority(best) == 2 and not best.target_step:
            best.reasons.append(
                Reason("target_survival", tr("체력이 낮아 고정 목표보다 생존을 우선"), 0, tr("생존"))
            )
        elif growth and priority(best) == 2:
            best.reasons.append(
                Reason("growth_survival", tr("체력이 낮아 성장 준비보다 생존을 우선"), 0, tr("생존"))
            )
        close = [
            evaluation
            for evaluation in ranked[1:]
            if priority(evaluation) == priority(best) and best.score - evaluation.score < CLOSE_MARGIN
        ]
        if not any(evaluation.strong and not evaluation.plan_blocked for evaluation in known):
            status, headline = "hold", tr("뚜렷한 차이 없음")
            limitations.append(tr("현재 조합과 연결되는 선택지를 찾지 못함"))
        elif close:
            status, headline = "close", tr("{position} 추천 · 차이 작음", position=tr(best.card.position))
        else:
            status, headline = "recommend", tr("{position} 선택 추천", position=tr(best.card.position))
        if len(known) == 1 and unknown:
            status, headline = (
                ("recommend", tr("{position} 선택 추천", position=tr(best.card.position)))
                if best.strong
                else ("hold", tr("판단 보류"))
            )

        margin = best.score - ranked[1].score if len(ranked) > 1 else 99.0
        decisive = any(reason.rule_id in ("evo_ready", "passive_recipe_ready") for reason in best.reasons)
        if status == "hold":
            confidence = tr("근거 약함")
        elif status == "close":
            confidence = tr("근소")
        else:
            confidence = tr("확실") if margin >= 15 or decisive else tr("추천")
            if target_active:
                confidence = tr("추천")  # 목표를 따른다는 사실은 성능 차이의 확신도가 아니다.
            elif best.growth_plan is not None:
                confidence = tr("성장 추천")
        odds = self.reroll_odds(session, plan, run_state)
        rs, rt = self._reroll(session, known, unknown, status, odds)
        banish_card, banish_text = self._banish(
            session, known, best if status != "hold" else None, run_state, plan
        )
        return Recommendation(
            session_id=session.session_id,
            rules_version=version,
            status=status,
            headline=headline,
            best=best if status != "hold" else None,
            evals=evals,
            close_to=close,
            reroll_status=rs,
            reroll_text=rt,
            banish_text=banish_text,
            banish_card=banish_card,
            situation=situation,
            plan_text=(
                tr("장기 성장 비교 · 실제 DPS 예측값은 아님")
                if best.growth_plan is not None
                else self._plan_line(plan, best if status != "hold" else None)
            ),
            reroll_odds=odds,
            fallback=best if status == "hold" and best.score > 0 else None,
            limitations=limitations,
            ranked=ranked,
            confidence=confidence,
            plan_locked=plan.locked,
            growth_plan=best.growth_plan if status != "hold" else None,
        )

    # ---- 카드 하나 평가 ----
    def _evaluate(
        self,
        card: Card,
        run_state: RunState,
        progress: Optional[RunProgress],
        plan: Optional[DeckPlan] = None,
    ) -> ActionEval:
        game_data = self.data
        item = game_data.item(card.item_id)
        if item is None:
            return ActionEval(card, "unknown")
        kind = item.kind
        owned = run_state.owned.get(item.id)
        # 카드 문구가 가장 확실한 근거('신규!' / '레벨 N'). 문구를 못 읽었으면 보유 목록으로 판단한다.
        upgrade = card.label.is_upgrade if card.label is not None else owned is not None
        if upgrade:
            after = card.shown_level or (owned.level + 1 if owned and owned.level else None)
            before = after - 1 if after else (owned.level if owned else None)
        else:
            before, after = None, card.shown_level or 1
        action = f"{'upgrade' if upgrade else 'new'}_{kind}"
        evaluation = ActionEval(card, action, before, after)

        base = {
            "new_ball": 6,
            "upgrade_ball": 10,
            "new_passive": 5,
            "upgrade_passive": 6,
            "new_pet": 5,
            "upgrade_pet": 5,
        }[action]
        base_text = {
            "new_ball": tr("새 볼 획득"),
            "upgrade_ball": tr("보유한 볼 강화"),
            "new_passive": tr("새 패시브 획득"),
            "upgrade_passive": tr("보유한 패시브 강화"),
            "new_pet": tr("펫 강화"),
            "upgrade_pet": tr("펫 강화"),
        }[action]
        if kind == "pet":
            # 펫 강화는 수치·기록 비교 근거가 없다 — 기본값만 두고 설명문을 그대로 보여 준다
            evaluation.reasons.append(Reason(f"base_{action}", base_text, base))
            if item.desc_ko:
                evaluation.reasons.append(Reason("pet_desc", item.desc_ko, 0.1, tr("펫")))
            evaluation.score = base
            return evaluation
        evaluation.reasons.append(Reason(f"base_{action}", base_text, base))
        if not upgrade and owned is not None:
            evaluation.warnings.append(
                Reason(
                    "duplicate_copy",
                    tr("이미 {copies}개 보유 — 레벨 1 복사본을 하나 더 얻음", copies=owned.copies),
                    -3,
                    tr("복사본"),
                )
            )

        self._recipes(evaluation, item.id, kind, after, run_state)
        self._game_hints(evaluation, card, run_state)
        self._character(evaluation, item, kind, upgrade, run_state)
        self._char_strategy(evaluation, item, kind, upgrade, run_state)
        self._community(evaluation, item.id, kind, run_state)
        self._char_history(evaluation, item.id, run_state)
        self._support(evaluation, item.id, run_state, progress)
        self._progress(evaluation, kind, upgrade, progress)
        if plan is not None:
            self._plan(evaluation, kind, upgrade, plan)
        self._performance(evaluation, item.id, kind, upgrade, run_state)
        if kind == "passive":
            self._passive_effect(evaluation, item.id, upgrade, run_state, progress)
        elif kind == "ball":
            self._ball_effect(evaluation, item.id, upgrade)
        self._archetype(evaluation, item.id, kind, upgrade)
        evaluation.score = sum(reason.weight for reason in evaluation.reasons) + sum(
            warning.weight for warning in evaluation.warnings
        )
        return evaluation

    def _archetype(self, evaluation: ActionEval, item_id: str, kind: str, upgrade: bool):
        """덱 계열(가진 볼 태그로 감지)과 같은 계열인 볼, 그 계열을 키우는 패시브를 조금 올린다.
        진화 재료처럼 확실한 근거가 아니라 방향성이라 가중치는 작다."""
        arch: Optional[Archetype] = getattr(self, "_arch", None)
        if not arch or not arch.top:
            return
        if kind == "ball":
            shared = [axis for axis in axes_of(self.data, item_id) if axis in arch.top]
            if shared:
                evaluation.reasons.append(
                    Reason(
                        "archetype",
                        tr("덱 계열({text})과 같은 {v0} 계열", text=arch.text, v0=AXIS_LABEL[shared[0]]),
                        3 if upgrade else 4,
                        tr("{v0} 계열", v0=AXIS_LABEL[shared[0]]),
                    )
                )
        elif kind == "passive":
            pe = passive_effect(
                self._effect_rows(evaluation, item_id),
                evaluation.level_before if upgrade else None,
                evaluation.level_after,
            )
            if pe is not None and any(AXIS_PASSIVE_ROLE.get(axis) == pe.role for axis in arch.top):
                evaluation.reasons.append(
                    Reason(
                        "archetype_passive",
                        tr("덱 계열({text})을 키우는 패시브 ({text2})", text=arch.text, text2=pe.text),
                        3,
                        tr("계열 강화"),
                    )
                )

    def _ball_effect(self, evaluation: ActionEval, item_id: str, upgrade: bool):
        """볼 강화로 바뀌는 게임 수치 (피해 범위·지속·중첩·연쇄 수)."""
        evaluation.effect = ball_effect(
            self._effect_rows(evaluation, item_id),
            evaluation.level_before if upgrade else None,
            evaluation.level_after,
        )
        evaluation.effect = self._effect_label(evaluation, evaluation.effect)

    def _effect_label(self, evaluation, text):
        if not text:
            return text
        from .current_effects import is_current

        return (
            tr("현재 능력치 반영: {effect}", effect=text)
            if is_current(evaluation.card, evaluation.level_before, evaluation.level_after)
            else tr("기본 수치: {effect}", effect=text)
        )

    def _effect_rows(self, evaluation, item_id):
        from .current_effects import level_rows

        return level_rows(
            self.data.level_props.get(item_id),
            evaluation.card,
            evaluation.level_before,
            evaluation.level_after,
        )

    def _passive_effect(
        self,
        evaluation: ActionEval,
        item_id: str,
        upgrade: bool,
        run_state: RunState,
        progress: Optional[RunProgress],
    ):
        """패시브(아래 줄)의 실제 효과: 게임 레벨별 수치로 이번 선택이 무엇을 얼마나 바꾸는지, 상황에 맞는 역할인지."""
        game_data = self.data
        pe = passive_effect(
            self._effect_rows(evaluation, item_id),
            evaluation.level_before if upgrade else None,
            evaluation.level_after,
        )
        if pe is None:
            return
        evaluation.effect = self._effect_label(evaluation, pe.text)
        if upgrade and pe.gain is not None:
            if pe.gain <= 0.05:
                evaluation.warnings.append(
                    Reason(
                        "passive_gain_small",
                        tr("이번 강화로 주 효과가 거의 늘지 않음 ({text})", text=pe.text),
                        -4,
                        tr("강화 효과 작음"),
                    )
                )
            elif pe.gain >= 0.3:
                evaluation.reasons.append(
                    Reason(
                        "passive_gain",
                        tr("강화 효과 큼: {text}", text=pe.text),
                        min(6, 2 + round(4 * pe.gain)),
                        tr("강화 효과 큼"),
                    )
                )
        hr = progress.health_ratio if progress else None
        if pe.role == "defense" and hr is not None and hr < 0.5:
            evaluation.reasons.append(
                Reason(
                    "passive_defense_low_hp",
                    tr("체력 {v0}% — 생존 패시브 ({text})", v0=round(hr * 100), text=pe.text),
                    7 if hr < 0.35 else 4,
                    tr("생존"),
                )
            )
        if pe.role == "aoe":
            aoe = [
                index
                for index in run_state.owned
                if index.startswith("ball:")
                and game_data.item(index) is not None
                and "AOE" in game_data.status_tags(index)[1]
            ]
            if len(aoe) >= 2:
                evaluation.reasons.append(
                    Reason(
                        "passive_aoe_balls",
                        tr("보유한 범위 피해 볼 {v0}개를 함께 강화", v0=len(aoe)),
                        2 * min(len(aoe), 3),
                        tr("범위 볼 연계"),
                    )
                )
        tb = None
        if progress is not None:
            tb = (
                progress.turns_to_next_boss
                if progress.turns_to_next_boss is not None
                else progress.turns_to_boss
            )
        if pe.role == "power" and tb is not None and 0 < tb <= 10:
            evaluation.reasons.append(
                Reason(
                    "passive_power_boss",
                    tr("보스까지 {tb}턴 — {v0} 패시브", tb=tb, v0=ROLE_LABEL["power"]),
                    3,
                    tr("보스 대비"),
                )
            )

    def _recipes(
        self, evaluation: ActionEval, item_id: str, kind: str, after: Optional[int], run_state: RunState
    ):
        game_data = self.data
        known_max = game_data.max_level_known(kind)
        maxlv = game_data.max_level(kind)
        best: Optional[Reason] = None
        best_result = ""
        extra: List[str] = []
        for recipe in game_data.recipes_using(item_id):
            if not game_data.recipe_reachable(recipe):
                continue  # 재료가 아직 해금 안 됨 (예: 주취자를 안 열어 매혹이 없음) — 만들 수 없는 진화
            others = list(recipe.ingredients)
            others.remove(item_id)
            owned_others = [run_state.owned.get(o) for o in others]
            result = game_data.name(recipe.result)
            names = " + ".join(game_data.name(x) for x in recipe.ingredients)
            source = "" if recipe.source == "game" else tr(", 위키 레시피")
            if others and all(owned_others):
                others_max = [
                    o.at_max is True or (known_max and o.level is not None and o.level >= maxlv)
                    for o in owned_others
                ]
                if kind == "passive":
                    cand = Reason(
                        "passive_recipe_ready",
                        tr("{result} 재료가 모두 모임 (레벨 조건 미확인{src})", result=result, src=source),
                        16,
                        tr("{result} 재료", result=result),
                    )
                elif known_max and after is not None and all(o.level is not None for o in owned_others):
                    remaining = (maxlv - after) + sum(max(0, maxlv - o.level) for o in owned_others)
                    if remaining <= 0:
                        cand = Reason(
                            "evo_ready",
                            tr("{result} 진화 조건 충족 ({names} 최대 레벨)", result=result, names=names),
                            30,
                            tr("{result} 진화 가능", result=result),
                        )
                    else:
                        cand = Reason(
                            "evo_path",
                            tr(
                                "{result} 진화까지 강화 {remaining}번 남음 ({names})",
                                result=result,
                                remaining=remaining,
                                names=names,
                            ),
                            max(8.0, 22.0 - 4 * remaining),
                            tr("{result} 진화 경로", result=result),
                        )
                elif all(others_max):
                    cand = Reason(
                        "evo_partner_max",
                        tr(
                            "{result} 재료 — 짝인 {v0}은(는) 최대 레벨",
                            result=result,
                            v0=" + ".join(game_data.name(o) for o in others),
                        ),
                        16,
                        tr("{result} 진화 경로", result=result),
                    )
                else:
                    cand = Reason(
                        "evo_path_level_unknown",
                        tr("{result} 진화 재료 ({names}{src})", result=result, names=names, src=source),
                        12,
                        tr("{result} 진화 경로", result=result),
                    )
            elif any(owned_others) and len(others) >= 2:
                cand = Reason(
                    "evo_partial",
                    tr("{result} 재료 일부 보유", result=result),
                    5,
                    tr("{result} 재료 일부", result=result),
                )
            else:
                continue
            if cand.rule_id in ("evo_path", "evo_partner_max", "evo_path_level_unknown"):
                # 짝 볼이 이미 다른 진화의 재료를 다 모은 상태면(예: 돌 + 알주머니 → 투석기) 이 카드는 그 짝을 두 진화가
                # 나눠 쓰게 된다 — 이미 갖춘 진화보다 앞세우지 않고 어느 재료인지 밝힌다
                taken = next(
                    (
                        (o, r2)
                        for o in others
                        for r2 in game_data.recipes_using(o)
                        if r2 is not recipe
                        and r2.result != recipe.result
                        and game_data.recipe_reachable(r2)
                        and all(index in run_state.owned for index in r2.ingredients)
                    ),
                    None,
                )
                if taken is not None:
                    o, r2 = taken
                    cand = Reason(
                        "evo_path_shared" if cand.rule_id != "evo_partner_max" else cand.rule_id,
                        tr(
                            "{text} — 단, {v0}은(는) 이미 {result2} 재료 ({names2})",
                            text=cand.text,
                            v0=game_data.name(o),
                            result2=game_data.name(r2.result),
                            names2=" + ".join(game_data.name(x) for x in r2.ingredients),
                        ),
                        max(4.0, cand.weight * 0.5),
                        cand.short,
                    )
            if best is None or cand.weight > best.weight:
                if best is not None:
                    extra.append(best_result)
                best, best_result = cand, result
            else:
                extra.append(result)
        if best is not None:
            evaluation.reasons.append(best)
            if extra:
                names = ", ".join(dict.fromkeys(extra))
                evaluation.reasons.append(
                    Reason(
                        "evo_multi",
                        tr("{names} 진화에도 쓰임", names=names),
                        min(6, 2 * len(extra)),
                        tr("{names} 경로", names=names),
                    )
                )

    def _game_hints(self, evaluation: ActionEval, card: Card, run_state: RunState):
        """게임 연동으로 받은 게임 자체 판정: 보유 볼과의 시너지(카드 설명의 '시너지 장비'), 자동 선택 AI 기피."""
        game_data = self.data
        partners = [
            synergy for synergy in card.synergy if synergy != card.item_id and synergy in run_state.owned
        ]
        if partners and not any(reason.rule_id.startswith("evo") for reason in evaluation.reasons):
            names = ", ".join(game_data.name(partner) for partner in partners[:2])
            evaluation.reasons.append(
                Reason(
                    "game_synergy",
                    tr("보유한 {names}와 시너지 (게임 판정)", names=names),
                    6 + 2 * min(len(partners) - 1, 2),
                    tr("시너지"),
                )
            )
        if card.ai_pick is False:
            evaluation.warnings.append(
                Reason("game_ai_avoid", tr("게임 자동 선택 AI는 고르지 않는 항목"), -3, tr("AI 비선호"))
            )

    def _character(self, evaluation: ActionEval, item, kind: str, upgrade: bool, run_state: RunState):
        game_data = self.data
        for character_id in run_state.character_ids:
            rule = game_data.character_rule(character_id)
            if not rule:
                continue
            cname = game_data.name(character_id)
            if [t for t in rule.get("reduces_tags", []) if game_data.has_tag(item.id, t)]:
                evaluation.warnings.append(
                    Reason(
                        "char_reduces",
                        tr("{cname}: {v0} — 효과가 줄거나 없을 수 있음", cname=cname, v0=tr(rule["reason"])),
                        -18,
                        tr("캐릭터와 안 맞음"),
                    )
                )
            st_, dm_ = game_data.status_tags(item.id)
            if rule.get("boosts_wiki_status_or_aoe"):
                if st_ or "AOE" in dm_:
                    evaluation.reasons.append(
                        Reason(
                            "char_sisyphus_boost",
                            tr("{cname}: 범위·상태 이상 피해 4배", cname=cname),
                            8,
                            tr("캐릭터 연계"),
                        )
                    )
                elif kind == "ball":
                    evaluation.warnings.append(
                        Reason(
                            "char_sisyphus_direct",
                            tr("{cname}: 볼 직접 피해가 없음", cname=cname),
                            -8,
                            tr("직접 피해 없음"),
                        )
                    )
            if rule.get("boosts_wiki_on_hit_status") and kind == "ball" and st_:
                evaluation.reasons.append(
                    Reason(
                        "char_fast_fire",
                        tr("{cname}: 발사 속도 두 배로 타격 효과가 자주 발동", cname=cname),
                        5,
                        tr("발사 속도 연계"),
                    )
                )

    STATUS_AXES = ("burn", "freeze", "bleed", "poison", "curse")

    def _item_keys(self, item_id: str, kind: str, upgrade: bool, evaluation: ActionEval) -> List[str]:
        """캐릭터 궁합에 쓰는 항목 성격: status·aoe·direct·baby·sustain / crit·speed·defense·… / new_ball·upgrade_ball·passive."""
        keys: List[str] = []
        if kind == "ball":
            axes = axes_of(self.data, item_id)
            if any(axis in self.STATUS_AXES for axis in axes):
                keys.append("status")
            keys += [axis for axis in axes if axis in ("aoe", "baby", "sustain")]
            if not axes:
                keys.append("direct")
            keys.append("upgrade_ball" if upgrade else "new_ball")
        elif kind == "passive":
            props = self._effect_rows(evaluation, item_id) or []
            pe = passive_effect(props, evaluation.level_before if upgrade else None, evaluation.level_after)
            if pe is not None:
                keys.append(
                    {
                        "power": "power",
                        "aoe": "aoe",
                        "defense": "defense",
                        "baby": "baby",
                        "speed": "speed",
                    }.get(pe.role, pe.role)
                )
            if any("CritChance" in k for row in props for k in row):
                keys.append("crit")
            keys.append("passive")
        return keys

    def _char_strategy(self, evaluation: ActionEval, item, kind: str, upgrade: bool, run_state: RunState):
        """캐릭터 궁합 (공식 설명에서 끌어낸 프로필, data/rules.json characters.*.strategy)."""
        if kind not in ("ball", "passive"):
            return
        keys = self._item_keys(item.id, kind, upgrade, evaluation)
        for character_id in run_state.character_ids:
            rule = self.data.character_rule(character_id)
            st = rule.get("strategy")
            if not st:
                continue
            fav = st.get("favor", {})
            item_keys = keys
            if kind == "ball" and rule.get("boosts_wiki_status_or_aoe"):
                # 범위·상태 이상 볼은 _character 의 전용 규칙(4배, +8)이 이미 셌다 — 같은 이유로 두 번 올리지 않는다
                item_keys = [k for k in keys if k not in ("aoe", "status")]
            hits = [(fav[k], k) for k in item_keys if k in fav]
            if not hits:
                continue
            best = max(hits)
            worst = min(hits)
            cname = self.data.name(character_id)
            if best[0] > 0:
                evaluation.reasons.append(
                    Reason(
                        "char_fit",
                        tr("{cname} 궁합: {v0}", cname=cname, v0=tr(st.get("why", ""))),
                        float(best[0]),
                        tr("캐릭터 궁합"),
                    )
                )
            if worst[0] < 0:
                evaluation.warnings.append(
                    Reason(
                        "char_misfit",
                        f"{cname}: {tr(st.get('why', ''))}",
                        float(worst[0]),
                        tr("캐릭터와 덜 맞음"),
                    )
                )

    TIER_W = {"S": 3, "A": 1.5, "B": 0, "C": -1, "D": -1.5}  # 의견이라 작게: 비슷할 때 가르는 정도

    def _community(self, evaluation: ActionEval, item_id: str, kind: str, run_state: RunState):
        """커뮤니티 의견: 항목·진화 결과의 티어(볼 Game Rant, 패시브 Dexerto), 캐릭터 추천 빌드 핵심 항목.
        게임 값이 아니라 공략 사이트 평가라 가중치는 작게."""
        game_data = self.data
        if not game_data.community:
            return
        # 진화로 가는 카드면 그 결과의 티어를, 아니면 카드 자신의 티어를 본다
        # 진화 결과는 나머지 재료를 이미 가진 레시피만 (가능성만 있는 진화까지 세면 거의 모든 카드가 S 가 된다)
        results = [
            recipe.result
            for recipe in game_data.recipes_using(item_id)
            if all(index == item_id or index in run_state.owned for index in recipe.ingredients)
            and game_data.recipe_reachable(recipe)
        ]
        tiers = [(game_data.community_tier(res), res) for res in results if game_data.community_tier(res)]
        if tiers:
            tier, res = min(tiers, key=lambda t: "SABCD".index(t[0]))
            if self.TIER_W[tier] > 0:
                evaluation.reasons.append(
                    Reason(
                        "community_evo_tier",
                        tr("커뮤니티 평가: {v0} 진화는 {tier}티어", v0=game_data.name(res), tier=tier),
                        float(self.TIER_W[tier]),
                        tr("{tier}티어 진화", tier=tier),
                    )
                )
        else:
            tier = game_data.community_tier(item_id)
            if tier and self.TIER_W[tier] > 0:
                evaluation.reasons.append(
                    Reason(
                        "community_tier",
                        tr("커뮤니티 평가 {tier}티어", tier=tier),
                        float(self.TIER_W[tier]),
                        tr("{tier}티어", tier=tier),
                    )
                )
            elif (
                tier
                and self.TIER_W[tier] < 0
                and not evaluation.linked
                and not any(
                    game_data.community_tier(recipe.result) in ("S", "A")
                    for recipe in game_data.recipes_using(item_id)
                )
            ):
                evaluation.warnings.append(
                    Reason(
                        "community_tier_low",
                        tr("커뮤니티 평가 {tier}티어 (진화 재료가 아니면 약함)", tier=tier),
                        float(self.TIER_W[tier]),
                        tr("{tier}티어", tier=tier),
                    )
                )
        for character_id in run_state.character_ids:
            core, why = game_data.char_build_items(character_id)
            hit = item_id in core or any(res in core for res in results)
            if hit:
                evaluation.reasons.append(
                    Reason(
                        "char_build",
                        tr("{v0} 추천 빌드 핵심 (커뮤니티): {why}", v0=game_data.name(character_id), why=why),
                        5.0,
                        tr("캐릭터 추천 빌드"),
                    )
                )
                break

    def _char_history(self, evaluation: ActionEval, item_id: str, run_state: RunState):
        """내 기록: 이 캐릭터로 이 항목을 가진 런의 보스 격퇴율 (3번 이상일 때만, 캐릭터 평균과 비교)."""
        if not self.history or not run_state.character_ids:
            return
        character_id = run_state.character_ids[0]
        runs = [
            h
            for h in self.history
            if getattr(h, "char", None) == character_id and getattr(h, "result", "") != "중단"
        ]
        if len(runs) < 4:
            return
        have = [
            h
            for h in runs
            if item_id
            in {index for index, _ in (getattr(h, "balls", []) or [])}
            | {index for index, _ in (getattr(h, "passives", []) or [])}
        ]
        if len(have) < 3:
            return
        rate = sum(h.result == "보스 격퇴" for h in have) / len(have)
        base = sum(h.result == "보스 격퇴" for h in runs) / len(runs)
        cname = self.data.name(character_id)
        text = tr(
            "내 기록({cname}): 이 항목을 가진 런 보스 격퇴 {v0}% ({v1}번, 이 캐릭터 평균 {v2}%)",
            cname=cname,
            v0=round(rate * 100),
            v1=len(have),
            v2=round(base * 100),
        )
        if rate >= base + 0.2:
            evaluation.reasons.append(Reason("char_record_good", text, 4, tr("이 캐릭터 기록 좋음")))
        elif rate <= base - 0.2:
            evaluation.warnings.append(Reason("char_record_bad", text, -3, tr("이 캐릭터 기록 나쁨")))

    def _support(
        self, evaluation: ActionEval, item_id: str, run_state: RunState, progress: Optional[RunProgress]
    ):
        game_data = self.data
        owned_ids = run_state.effect_ids
        has_heal = any(game_data.has_tag(index, "heal_source") for index in owned_ids)
        hr = progress.health_ratio if progress else None
        if game_data.has_tag(item_id, "heal_source"):
            if hr is not None and hr < 0.35:
                evaluation.reasons.append(
                    Reason(
                        "heal_low_hp", tr("체력 {v0}% — 회복 수단 우선", v0=round(hr * 100)), 12, tr("회복")
                    )
                )
            elif run_state.owned_complete and not has_heal:
                evaluation.reasons.append(Reason("heal_gap", tr("현재 회복 수단이 없음"), 7, tr("회복 보완")))
        if game_data.has_tag(item_id, "needs_healing") and run_state.owned_complete and not has_heal:
            evaluation.warnings.append(
                Reason("heal_needed", tr("회복 수단이 없어 발동하기 어려움"), -6, tr("발동 어려움"))
            )
        reduced = any(
            "baby_ball_source" in game_data.character_rule(character_id).get("reduces_tags", [])
            for character_id in run_state.character_ids
        )
        if not reduced:
            sources = sum(1 for index in owned_ids if game_data.has_tag(index, "baby_ball_source"))
            if game_data.has_tag(item_id, "baby_ball_scaling") and sources:
                evaluation.reasons.append(
                    Reason(
                        "baby_synergy",
                        tr("보유한 베이비볼 생성 항목 {sources}개와 연계", sources=sources),
                        4 * min(sources, 3),
                        tr("베이비볼 연계"),
                    )
                )
            elif game_data.has_tag(item_id, "baby_ball_source") and any(
                game_data.has_tag(index, "baby_ball_scaling") for index in owned_ids
            ):
                evaluation.reasons.append(
                    Reason("baby_synergy", tr("보유한 베이비볼 강화 패시브와 연계"), 5, tr("베이비볼 연계"))
                )

    def _progress(self, evaluation: ActionEval, kind: str, upgrade: bool, progress: Optional[RunProgress]):
        """런 진행 상황에 따른 규칙 (게임 연동에서 받은 체력·턴·칸 수)."""
        if progress is None:
            return
        frac = progress.run_fraction
        early = frac is not None and frac < 0.4
        if not upgrade:
            capacity = progress.max_balls if kind == "ball" else progress.max_passives
            have = progress.balls if kind == "ball" else progress.passives
            if capacity is not None and have is not None:
                free = capacity - have
                if free <= 0:
                    evaluation.warnings.append(
                        Reason(
                            "slot_full",
                            tr(
                                "{v0} 칸이 가득 참 ({have}/{cap})",
                                v0=tr("볼") if kind == "ball" else tr("패시브"),
                                have=have,
                                cap=capacity,
                            ),
                            -25,
                            tr("칸 없음"),
                        )
                    )
                elif kind == "ball" and have <= 2:
                    # 실제 플레이 확인: 볼 1~2개로 강화만 거듭하면 턴 30대에 적이 쌓여 전멸했다
                    evaluation.reasons.append(
                        Reason(
                            "slot_fill_few",
                            tr("볼이 {have}개뿐 — 볼 수를 늘려야 여러 적을 동시에 처리", have=have),
                            9,
                            tr("볼 수 늘리기"),
                        )
                    )
                elif kind == "ball" and early:
                    evaluation.reasons.append(
                        Reason(
                            "slot_fill_early",
                            tr("빈 볼 칸 {free}개 — 초반엔 볼 수를 늘리면 화력이 오름", free=free),
                            5,
                            tr("빈 칸 채우기"),
                        )
                    )
                elif kind == "passive" and free >= 2:
                    evaluation.reasons.append(
                        Reason("slot_fill_passive", tr("빈 패시브 칸 {free}개", free=free), 2, tr("빈 칸"))
                    )
        tb = (
            progress.turns_to_next_boss if progress.turns_to_next_boss is not None else progress.turns_to_boss
        )
        if (
            upgrade
            and kind == "ball"
            and tb is not None
            and 0 < tb <= max(15, int((progress.final_boss_turn or 0) * 0.12))
        ):
            evaluation.reasons.append(
                Reason(
                    "boss_soon",
                    tr("보스까지 {tb}턴 — 보유 볼 강화로 바로 화력 확보", tb=tb),
                    4,
                    tr("보스 대비"),
                )
            )

    def _performance(
        self, evaluation: ActionEval, item_id: str, kind: str, upgrade: bool, run_state: RunState
    ):
        """실제 성능: 이번 런에서 잰 피해(게임 통계)와 내 누적 기록(게임 세이브)."""
        if kind == "passive":
            self._passive_performance(evaluation, item_id, upgrade, run_state)
            return
        if kind != "ball":
            return
        if upgrade:
            share = run_state.damage_share(item_id)
            dealers = sum(
                1 for index, value in run_state.damage.items() if index.startswith("ball:") and value > 0
            )
            if share is None or dealers < 2:
                return  # 실제 플레이 확인: 볼이 하나면 피해 100% 라 '주력' 판단이 무의미하다 (그 볼만 계속 강화하다 전멸)
            percentage, rank = round(share * 100), run_state.damage_rank(item_id)
            if share >= 0.3:
                evaluation.reasons.append(
                    Reason(
                        "run_carry",
                        tr("이번 런 피해 {rank}위 ({pct}%) — 주력 볼을 더 키움", rank=rank, pct=percentage),
                        4 + round(10 * share),
                        tr("주력 볼"),
                    )
                )
            elif share < 0.08 and not evaluation.linked:
                evaluation.warnings.append(
                    Reason(
                        "run_weak",
                        tr("이번 런 피해 {pct}% — 기여가 작은 볼", pct=percentage),
                        -3,
                        tr("피해 적음"),
                    )
                )
            return
        recommendation = self.meta.records.get(item_id) if self.meta else None
        rank = self.meta.damage_rank(item_id) if self.meta else None
        if recommendation is None or rank is None:
            return
        rank_position, rank_count = rank
        if rank_position <= max(1, rank_count // 4):
            evaluation.reasons.append(
                Reason(
                    "my_record_top",
                    tr(
                        "내 기록: 런당 평균 피해 {r}위 / {n}개 볼 (가진 런 {obtained}번)",
                        r=rank_position,
                        n=rank_count,
                        obtained=recommendation.obtained,
                    ),
                    4,
                    tr("내 기록 상위"),
                )
            )
        elif rank_position > rank_count - max(1, rank_count // 4):
            evaluation.warnings.append(
                Reason(
                    "my_record_low",
                    tr("내 기록: 런당 평균 피해 {r}위 / {n}개 볼", r=rank_position, n=rank_count),
                    -2,
                    tr("내 기록 하위"),
                )
            )

    def _passive_performance(self, evaluation: ActionEval, item_id: str, upgrade: bool, run_state: RunState):
        """패시브: 이번 런 추가 피해(게임 통계, 볼 피해 대비)와 내 기록의 '가진 런 완료율' 순위."""
        balls = sum(value for index, value in run_state.damage.items() if index.startswith("ball:"))
        bonus = run_state.damage.get(item_id)
        if upgrade and bonus and balls >= 2000:
            ratio = bonus / balls
            if ratio >= 0.1:
                evaluation.reasons.append(
                    Reason(
                        "passive_dmg",
                        tr("이번 런 추가 피해 {v0}% (볼 피해 대비)", v0=round(ratio * 100)),
                        min(8, 3 + round(10 * ratio)),
                        tr("추가 피해 큼"),
                    )
                )
        rank = self.meta.completion_rank(item_id) if self.meta else None
        if rank is None:
            return
        rank_position, rank_count, rate = rank
        if rank_position <= max(1, rank_count // 4):
            evaluation.reasons.append(
                Reason(
                    "my_record_clear",
                    tr(
                        "내 기록: 가진 런 완료율 {v0}% ({r}위 / {n})",
                        v0=round(rate * 100),
                        r=rank_position,
                        n=rank_count,
                    ),
                    3,
                    tr("내 기록 상위"),
                )
            )
        elif rank_position > rank_count - max(1, rank_count // 4):
            evaluation.warnings.append(
                Reason(
                    "my_record_clear_low",
                    tr(
                        "내 기록: 가진 런 완료율 {v0}% ({r}위 / {n})",
                        v0=round(rate * 100),
                        r=rank_position,
                        n=rank_count,
                    ),
                    -2,
                    tr("내 기록 하위"),
                )
            )

    def reroll_odds(
        self, session: ChoiceSession, plan: DeckPlan, run_state: RunState
    ) -> Optional[Tuple[float, int, int]]:
        """새로고침하면 목표 카드(목표 진화의 빠진 재료, 핵심·주력 항목 강화)가 한 장 이상 나올 확률.

        게임이 알려 준 후보 목록에서 지금 선택지(새로고침하면 빠짐)를 뺀 뒤, 균등하게 뽑는다고 가정한 추정이다.
        실제 뽑기 가중치는 확인하지 못했다.
        """
        pool = session.pool
        if pool is None:
            return None
        current = {card.item_id for card in session.cards if card.item_id}
        entries = [(index, up) for index, up in pool.entries() if index not in current]
        n = max(1, pool.num_choices)

        def good(i: str, up: bool) -> bool:
            if not up:
                return i in plan.wanted
            o = run_state.owned.get(i)
            if o is not None and o.at_max:
                return False
            share = run_state.damage_share(i)
            return i in plan.core or (share is not None and share >= 0.3)

        total = len(entries)
        k = sum(1 for index, up in entries if good(index, up))
        if total == 0:
            return None
        if total - k < n:
            return 1.0, total, k
        warning = self.draw_weights
        if not warning:
            return 1 - comb(total - k, n) / comb(total, n), total, k
        # 관측 보정: 종류별 가중치로 한 장씩 뽑는다고 보고, 뽑힌 카드는 평균 가중치만큼 뺀다 (근사)
        from ..tracking.draw_stats import category

        weights = [(warning.get(category(index, up) or "", 1.0), good(index, up)) for index, up in entries]
        bad_w = sum(x for x, g in weights if not g)
        tot_w = sum(x for x, _ in weights)
        avg_bad = bad_w / (total - k)
        p_none = 1.0
        for _ in range(n):
            if tot_w <= 0:
                break
            p_none *= max(0.0, bad_w) / tot_w
            bad_w -= avg_bad
            tot_w -= avg_bad
        return 1 - p_none, total, k

    def _plan_line(self, plan: DeckPlan, best: Optional[ActionEval]) -> str:
        """추천 카드가 이미 같은 진화를 말하고 있으면 목표 줄은 겹치므로 뺀다."""
        target = plan.target_text
        if target and best is not None and plan.targets and not plan.locked:
            name = self.data.name(plan.targets[0].recipe.result)
            if any(name in reason.text for reason in best.reasons):
                target = ""
        arch = getattr(self, "_arch", None)
        arch_text = tr("덱 계열: {text}", text=arch.text) if arch and arch.top else ""
        return " · ".join(x for x in (target, arch_text, plan.phase_text) if x)

    def _plan(self, evaluation: ActionEval, kind: str, upgrade: bool, plan: DeckPlan):
        """덱 방향에 따른 규칙: 최대 레벨 도달(융합 재료), 무한의 심연, 마지막 빈 칸."""
        game_data = self.data
        item_id = evaluation.card.item_id
        wanted = item_id in (plan.locked_core if upgrade else plan.locked_wanted)
        free = plan.free_slots(kind)
        incompatible = any(
            warning.rule_id in ("slot_full", "char_reduces", "char_sisyphus_direct")
            for warning in evaluation.warnings
        )
        if plan.locked and wanted and not incompatible and (upgrade or free is None or free > 0):
            evaluation.target_step = True
            evaluation.reasons.append(
                Reason(
                    "locked_target_step",
                    tr(
                        "고정 목표 {name}에 필요한 재료 획득·강화",
                        name=game_data.name(plan.targets[0].recipe.result),
                    ),
                    0,
                    tr("고정 목표"),
                )
            )
        if (
            upgrade
            and kind == "ball"
            and game_data.max_level_known("ball")
            and evaluation.level_after == game_data.max_level("ball")
            and not any(reason.rule_id == "evo_ready" for reason in evaluation.reasons)
        ):
            evaluation.reasons.append(
                Reason(
                    "reach_max_fusion",
                    tr("이번 강화로 최대 레벨 — 융합 화면에서 다른 최대 레벨 볼과 합칠 수 있음"),
                    6,
                    tr("최대 레벨 달성"),
                )
            )
        if plan.phase == "endless":
            if upgrade:
                evaluation.reasons.append(
                    Reason(
                        "endless_upgrade",
                        tr("무한의 심연: 적이 계속 강해져 새 항목보다 보유 항목 강화가 오래 감"),
                        5,
                        tr("강화 누적"),
                    )
                )
            elif not evaluation.linked:
                evaluation.warnings.append(
                    Reason(
                        "endless_unlinked",
                        tr("무한의 심연: 진화로 이어지지 않는 새 항목은 가치가 낮음"),
                        -4,
                        tr("덱과 연결 없음"),
                    )
                )
            return
        free = plan.free_slots(kind)
        if not upgrade and free == 1 and not evaluation.linked:
            what = tr("볼") if kind == "ball" else tr("패시브")
            evaluation.warnings.append(
                Reason(
                    "last_slot",
                    tr(
                        "마지막 {what} 칸 — 진화로 이어지는 {what2}에 남겨 두는 편이 나음",
                        what=what,
                        what2=what,
                    ),
                    -5 if kind == "ball" else -3,
                    tr("마지막 칸"),
                )
            )

    # ---- 새로고침·삭제 ----
    def _reroll(
        self,
        session: ChoiceSession,
        known: List[ActionEval],
        unknown: int,
        status: str,
        odds: Optional[Tuple[float, int, int]] = None,
    ):
        can = session.can_reroll
        if session.free_rerolls is not None:
            price = tr("무료 {free_rerolls}회 남음", free_rerolls=session.free_rerolls)
        elif session.reroll_cost is not None:
            price = tr("{reroll_cost}골드", reroll_cost=session.reroll_cost) + (
                tr(", 보유 {gold}골드", gold=session.gold)
                if session.gold is not None
                else tr(", 보유 골드 미확인")
            )
        else:
            price = tr("비용 미확인")
        if can is False:
            return "none", tr("새로고침 불가 — {price}", price=price)
        if unknown:
            return "unknown", tr("새로고침 판단 보류 — 읽지 못한 카드가 있음")
        weak = status == "hold" or all(
            evaluation.score < 8 and not evaluation.target_step for evaluation in known
        )
        if not weak:
            return "keep", tr("새로고침 불필요 — 조합에 이어지는 선택지가 있음")
        chance = ""
        if odds is not None:
            probability, total, k = odds
            if k == 0:
                return "keep", tr(
                    "새로고침해도 목표 카드가 후보에 없음 (후보 {total}장) — {price}",
                    total=total,
                    price=price,
                )
            basis = tr("관측 보정") if self.draw_weights else tr("추정")
            chance = tr(
                " · 목표 카드 나올 확률 약 {v0}% (후보 {total}장 중 {k}장, {basis})",
                v0=round(probability * 100),
                total=total,
                k=k,
                basis=basis,
            )
        if can is None:
            return "consider", tr("새로고침 검토 — {price}{chance}", price=price, chance=chance)
        if (
            session.free_rerolls is None
            and session.gold
            and session.reroll_cost
            and session.reroll_cost > session.gold * 0.5
        ):
            return "keep", tr(
                "새로고침 아껴 두기 — {price} (비용이 보유 골드의 절반 이상){chance}",
                price=price,
                chance=chance,
            )
        if odds is not None:
            return "consider", tr("새로고침 고려 — {price}{chance}", price=price, chance=chance)
        return "consider", tr("새로고침 고려 — {price} (다음 선택지는 예측 불가)", price=price)

    BANISH_WEIGHTS = {
        "slot_full": 3,
        "char_reduces": 3,
        "char_sisyphus_direct": 2,
        "endless_unlinked": 2,
        "last_slot": 2,
        "heal_needed": 1,
        "game_ai_avoid": 1,
        "duplicate_copy": 1,
    }

    def _banish(
        self,
        session: ChoiceSession,
        known: List[ActionEval],
        best: Optional[ActionEval],
        run_state: RunState,
        plan: DeckPlan,
    ):
        """삭제: 이번 런에서 다시 나오지 않게 할 카드. 삭제 횟수가 적으니(런당 몇 번) 확실할 때만 권한다.

        대상: 덱 목표(진화 재료·핵심 항목)와 이어지지 않고, 칸·캐릭터·단계상 앞으로도 고를 일이 없는 새 항목.
        이번 런에 여러 번 나온 카드일수록(선택지를 계속 차지) 먼저 권한다.
        """
        if not session.banish_left:
            return None, ""
        cands = []
        for evaluation in known:
            item_id = evaluation.card.item_id
            if (
                (best is not None and evaluation is best)
                or evaluation.action.startswith("upgrade")
                or evaluation.linked
                or item_id in plan.wanted
                or item_id in plan.core
            ):
                continue
            bad = sorted(
                (warning for warning in evaluation.warnings if warning.rule_id in self.BANISH_WEIGHTS),
                key=lambda w: -self.BANISH_WEIGHTS[w.rule_id],
            )
            weight = sum(self.BANISH_WEIGHTS[warning.rule_id] for warning in bad)
            seen = run_state.offered.get(item_id, 0)
            if weight and seen >= 2:
                weight += 1
            if weight >= 2:
                cands.append((weight, seen, evaluation.score, evaluation, bad))
        if not cands:
            return None, ""
        cands.sort(key=lambda t: (-t[0], -t[1], t[2]))
        weight, seen, _, evaluation, bad = cands[0]
        if (
            len(cands) == len(known)
            and len(known) > 1
            and seen < 2
            and all(card[0] == weight for card in cands)
        ):
            # 셋 다 똑같이 쓸모없으면 하나를 고를 근거가 없다 — 드문 삭제 대신 새로고침이 맞다
            return None, ""
        why = bad[0].text
        more = tr(" · 이번 런 {seen}번째 등장", seen=seen) if seen >= 2 else ""
        return evaluation.card, (
            tr(
                "삭제 추천: {v0} ({position}) — {why}{more} · 삭제 {banish_left}회 남음",
                v0=self.data.name(evaluation.card.item_id),
                position=tr(evaluation.card.position),
                why=why,
                more=more,
                banish_left=session.banish_left,
            )
        )
