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
from ..tracking.meta_state import MetaState

BASE_RULES = {"base_new_ball", "base_upgrade_ball", "base_new_passive", "base_upgrade_passive",
              "base_new_pet", "base_upgrade_pet", "pet_desc"}
CLOSE_MARGIN = 6.0


@dataclass(frozen=True)
class Reason:
    rule_id: str
    text: str
    weight: float
    short: str = ""          # 다른 선택지 목록에 쓰는 짧은 표현


@dataclass
class ActionEval:
    card: Card
    action: str                       # new_ball | upgrade_ball | new_passive | upgrade_passive | unknown
    level_before: Optional[int] = None
    level_after: Optional[int] = None
    score: float = 0.0
    reasons: List[Reason] = field(default_factory=list)
    warnings: List[Reason] = field(default_factory=list)
    effect: str = ""                  # 이 선택의 실제 수치 변화 (게임 레벨별 수치, 예: '받는 피해 감소 10% → 20%')

    @property
    def evaluated(self) -> bool:
        return self.action != "unknown"

    @property
    def action_text(self) -> str:
        if self.action in ("new_ball", "new_passive"):
            return "새 볼" if self.action == "new_ball" else "새 패시브"
        if self.action in ("upgrade_ball", "upgrade_passive"):
            if self.level_before and self.level_after:
                return f"레벨 {self.level_before} → {self.level_after}"
            if self.level_after:
                return f"레벨 {self.level_after}로 강화"
            return "강화"
        return "미확인"

    @property
    def strong(self) -> bool:
        return any(r.rule_id not in BASE_RULES for r in self.reasons)

    @property
    def linked(self) -> bool:
        """진화 레시피로 현재 덱과 이어지는지."""
        return any(r.rule_id.startswith(("evo_", "passive_recipe")) for r in self.reasons)

    def top_reasons(self, n: int = 2) -> List[Reason]:
        return sorted((r for r in self.reasons if r.rule_id not in BASE_RULES),
                      key=lambda r: -r.weight)[:n]


@dataclass
class Recommendation:
    session_id: int
    rules_version: str
    status: str                       # recommend | close | hold | auto | none
    headline: str
    best: Optional[ActionEval]
    evals: List[ActionEval]           # 게임 화면 순서
    close_to: List[ActionEval] = field(default_factory=list)
    reroll_status: str = ""
    reroll_text: str = ""
    banish_text: str = ""
    banish_card: Optional[Card] = None
    situation: str = ""               # 진행 상황 한 줄 (체력·보스·칸)
    plan_text: str = ""               # 덱 방향 한 줄 (목표 진화·단계)
    plan_locked: bool = False         # 사용자가 고정한 덱 목표가 있음 (HUD 가 이 줄을 밀어내지 않게)
    reroll_odds: Optional[Tuple[float, int, int]] = None   # (목표 카드 확률, 후보 수, 목표 카드 수) — 추정
    fallback: Optional[ActionEval] = None     # 판단 보류일 때 무난한 선택 (근거 약함, 참고용)
    limitations: List[str] = field(default_factory=list)
    ranked: List[ActionEval] = field(default_factory=list)   # 읽은 카드 전체의 순위 (보류여도 순서는 있다)
    confidence: str = ""              # 확실 | 추천 | 근소 | 근거 약함 — 1위와 2위의 차이·근거로 정한다


def card_rank(rec: "Recommendation", e: ActionEval) -> Optional[int]:
    for i, x in enumerate(rec.ranked, 1):
        if x is e:
            return i
    return None


def card_badge(rec: "Recommendation", e: ActionEval) -> str:
    """게임 화면 카드 이름표: 순위와 판정을 한 번에 (예: '1위 확실', '2위 비슷함', '3위')."""
    v = card_verdict(rec, e)
    n = card_rank(rec, e)
    if v == "unknown" or n is None:
        return "읽지 못함"
    if v == "banish":
        return f"{n}위 · 삭제 추천"
    if v == "best":
        return f"1위 {rec.confidence or '추천'}"
    if v == "alt":
        return f"{n}위 비슷함"
    if v == "neutral":
        return "1위 무난" if n == 1 else f"{n}위"
    return f"{n}위 비추천"


def card_verdict(rec: "Recommendation", e: ActionEval) -> str:
    """카드 하나의 판정: best(추천) | alt(비슷함) | banish(삭제 추천) | skip(비추천) | neutral(보류) | unknown.

    HUD 목록과 게임 화면 카드 테두리가 같은 판정·같은 색을 쓴다.
    """
    if not e.evaluated:
        return "unknown"
    if rec.banish_card is not None and e.card is rec.banish_card:
        return "banish"
    if rec.best is not None and e is rec.best:
        return "best"
    if rec.best is None:
        return "neutral"          # 판단 보류: 어느 쪽도 권하지 않음
    if e in rec.close_to:
        return "alt"
    return "skip"


def situation_text(p: Optional[RunProgress]) -> str:
    if p is None:
        return ""
    parts = []
    hr = p.health_ratio
    if hr is not None:
        parts.append(f"체력 {round(hr * 100)}%")
    tb = p.turns_to_boss
    nb = p.turns_to_next_boss
    if nb is not None and nb > 0:
        parts.append(f"다음 보스 {nb}턴")
    elif tb is not None and tb > 0 and not p.endless:
        parts.append(f"보스까지 {tb}턴")
    nf = p.turns_to_fuser
    if nf is not None and nf > 0:
        parts.append(f"융합기 {nf}턴")
    if p.max_balls and p.balls is not None:
        parts.append(f"볼 {p.balls}/{p.max_balls}")
    if p.max_passives is not None and p.passives is not None:
        parts.append(f"패시브 {p.passives}/{p.max_passives}")
    return " · ".join(parts)


class Recommender:
    def __init__(self, data: GameData):
        self.data = data
        self.meta: Optional[MetaState] = None     # 게임 연동 meta (내 누적 기록)
        self.draw_weights: Optional[dict] = None  # 뽑기 관측으로 추정한 종류별 가중치 (없으면 균등 가정)
        self.history: List = []                   # 내 런 기록 (RunRecord) — 캐릭터별 항목 성적

    # ---- 공개 ----
    def recommend(self, session: ChoiceSession, run: RunState) -> Recommendation:
        d = self.data
        version = d.rules.get("version", "")
        limitations = run.limitations(d)
        unknown = session.unknown_count
        if unknown:
            limitations.insert(0, f"읽지 못한 카드 {unknown}장 — 확인된 선택지끼리만 비교")
        progress = session.progress

        auto = [c for c in run.character_ids if d.character_rule(c).get("auto_selects_upgrades")]
        plan = build_plan(run, d, progress)
        self._arch = detect_archetype(run, d)
        if not self._arch.top:
            for cid in run.character_ids:           # 초반: 볼이 적으면 캐릭터 궁합 계열을 기본 방향으로
                fav = d.character_rule(cid).get("strategy", {}).get("favor", {})
                seed = [a for a in ("aoe", "baby", "sustain") if fav.get(a, 0) >= 3]
                if seed:
                    self._arch.top = seed[:1]
                    break
        evals = [self._evaluate(card, run, progress, plan) for card in session.cards]
        situation = situation_text(progress)
        if auto:
            return Recommendation(session.session_id, version, "auto", "이 캐릭터는 강화를 자동 선택합니다",
                                  None, evals, situation=situation, plan_text=plan.text, limitations=limitations)
        known = [e for e in evals if e.evaluated]
        if not known:
            return Recommendation(session.session_id, version, "none", "선택지를 읽지 못했습니다",
                                  None, evals, reroll_status="unknown", reroll_text="새로고침 판단 보류",
                                  situation=situation, plan_text=plan.text, limitations=limitations)

        ranked = sorted(known, key=lambda e: (-e.score, e.card.index))
        best = ranked[0]
        close = [e for e in ranked[1:] if best.score - e.score < CLOSE_MARGIN]
        if not any(e.strong for e in known):
            status, headline = "hold", "뚜렷한 차이 없음"
            limitations.append("현재 조합과 연결되는 선택지를 찾지 못함")
        elif close:
            status, headline = "close", f"{best.card.position} 추천 · 차이 작음"
        else:
            status, headline = "recommend", f"{best.card.position} 선택 추천"
        if len(known) == 1 and unknown:
            status, headline = ("recommend", f"{best.card.position} 선택 추천") if best.strong else ("hold", "판단 보류")

        margin = best.score - ranked[1].score if len(ranked) > 1 else 99.0
        decisive = any(r.rule_id in ("evo_ready", "passive_recipe_ready") for r in best.reasons)
        if status == "hold":
            confidence = "근거 약함"
        elif status == "close":
            confidence = "근소"
        else:
            confidence = "확실" if margin >= 15 or decisive else "추천"
        odds = self.reroll_odds(session, plan, run)
        rs, rt = self._reroll(session, known, unknown, status, odds)
        banish_card, banish_text = self._banish(session, known, best if status != "hold" else None, run, plan)
        return Recommendation(
            session_id=session.session_id, rules_version=version, status=status, headline=headline,
            best=best if status != "hold" else None, evals=evals, close_to=close,
            reroll_status=rs, reroll_text=rt, banish_text=banish_text, banish_card=banish_card,
            situation=situation, plan_text=self._plan_line(plan, best if status != "hold" else None),
            reroll_odds=odds, fallback=best if status == "hold" and best.score > 0 and not best.warnings else None,
            limitations=limitations, ranked=ranked, confidence=confidence, plan_locked=plan.locked,
        )

    # ---- 카드 하나 평가 ----
    def _evaluate(self, card: Card, run: RunState, progress: Optional[RunProgress],
                  plan: Optional[DeckPlan] = None) -> ActionEval:
        d = self.data
        item = d.item(card.item_id)
        if item is None:
            return ActionEval(card, "unknown")
        kind = item.kind
        owned = run.owned.get(item.id)
        # 카드 문구가 가장 확실한 근거('신규!' / '레벨 N'). 문구를 못 읽었으면 보유 목록으로 판단한다.
        upgrade = card.label.is_upgrade if card.label is not None else owned is not None
        if upgrade:
            after = card.shown_level or (owned.level + 1 if owned and owned.level else None)
            before = after - 1 if after else (owned.level if owned else None)
        else:
            before, after = None, 1
        action = f"{'upgrade' if upgrade else 'new'}_{kind}"
        ev = ActionEval(card, action, before, after)

        base = {"new_ball": 6, "upgrade_ball": 10, "new_passive": 5, "upgrade_passive": 6,
                "new_pet": 5, "upgrade_pet": 5}[action]
        base_text = {"new_ball": "새 볼 획득", "upgrade_ball": "보유한 볼 강화",
                     "new_passive": "새 패시브 획득", "upgrade_passive": "보유한 패시브 강화",
                     "new_pet": "펫 강화", "upgrade_pet": "펫 강화"}[action]
        if kind == "pet":
            # 펫 강화는 수치·기록 비교 근거가 없다 — 기본값만 두고 설명문을 그대로 보여 준다
            ev.reasons.append(Reason(f"base_{action}", base_text, base))
            if item.desc_ko:
                ev.reasons.append(Reason("pet_desc", item.desc_ko, 0.1, "펫"))
            ev.score = base
            return ev
        ev.reasons.append(Reason(f"base_{action}", base_text, base))
        if not upgrade and owned is not None:
            ev.warnings.append(Reason("duplicate_copy", f"이미 {owned.copies}개 보유 — 레벨 1 복사본을 하나 더 얻음", -3,
                                      "복사본"))

        self._recipes(ev, item.id, kind, after, run)
        self._game_hints(ev, card, run)
        self._character(ev, item, kind, upgrade, run)
        self._char_strategy(ev, item, kind, upgrade, run)
        self._community(ev, item.id, kind, run)
        self._char_history(ev, item.id, run)
        self._support(ev, item.id, run, progress)
        self._progress(ev, kind, upgrade, progress)
        if plan is not None:
            self._plan(ev, kind, upgrade, plan)
        self._performance(ev, item.id, kind, upgrade, run)
        if kind == "passive":
            self._passive_effect(ev, item.id, upgrade, run, progress)
        elif kind == "ball":
            self._ball_effect(ev, item.id, upgrade)
        self._archetype(ev, item.id, kind, upgrade)
        ev.score = sum(r.weight for r in ev.reasons) + sum(w.weight for w in ev.warnings)
        return ev

    def _archetype(self, ev: ActionEval, item_id: str, kind: str, upgrade: bool):
        """덱 계열(가진 볼 태그로 감지)과 같은 계열인 볼, 그 계열을 키우는 패시브를 조금 올린다.
        진화 재료처럼 확실한 근거가 아니라 방향성이라 가중치는 작다."""
        arch: Optional[Archetype] = getattr(self, "_arch", None)
        if not arch or not arch.top:
            return
        if kind == "ball":
            shared = [a for a in axes_of(self.data, item_id) if a in arch.top]
            if shared:
                ev.reasons.append(Reason("archetype", f"덱 계열({arch.text})과 같은 {AXIS_LABEL[shared[0]]} 계열",
                                         3 if upgrade else 4, f"{AXIS_LABEL[shared[0]]} 계열"))
        elif kind == "passive":
            pe = passive_effect(self.data.level_props.get(item_id), ev.level_before if upgrade else None, ev.level_after)
            if pe is not None and any(AXIS_PASSIVE_ROLE.get(a) == pe.role for a in arch.top):
                ev.reasons.append(Reason("archetype_passive", f"덱 계열({arch.text})을 키우는 패시브 ({pe.text})", 3,
                                         "계열 강화"))

    def _ball_effect(self, ev: ActionEval, item_id: str, upgrade: bool):
        """볼 강화로 바뀌는 게임 수치 (피해 범위·지속·중첩·연쇄 수)."""
        ev.effect = ball_effect(self.data.level_props.get(item_id), ev.level_before if upgrade else None,
                                ev.level_after)

    def _passive_effect(self, ev: ActionEval, item_id: str, upgrade: bool, run: RunState,
                        p: Optional[RunProgress]):
        """패시브(아래 줄)의 실제 효과: 게임 레벨별 수치로 이번 선택이 무엇을 얼마나 바꾸는지, 상황에 맞는 역할인지."""
        d = self.data
        pe = passive_effect(d.level_props.get(item_id), ev.level_before if upgrade else None, ev.level_after)
        if pe is None:
            return
        ev.effect = pe.text
        if upgrade and pe.gain is not None:
            if pe.gain <= 0.05:
                ev.warnings.append(Reason("passive_gain_small", f"이번 강화로 주 효과가 거의 늘지 않음 ({pe.text})", -4,
                                          "강화 효과 작음"))
            elif pe.gain >= 0.3:
                ev.reasons.append(Reason("passive_gain", f"강화 효과 큼: {pe.text}", min(6, 2 + round(4 * pe.gain)),
                                         "강화 효과 큼"))
        hr = p.health_ratio if p else None
        if pe.role == "defense" and hr is not None and hr < 0.5:
            ev.reasons.append(Reason("passive_defense_low_hp", f"체력 {round(hr * 100)}% — 생존 패시브 ({pe.text})",
                                     7 if hr < 0.35 else 4, "생존"))
        if pe.role == "aoe":
            aoe = [i for i in run.owned if i.startswith("ball:") and d.item(i) is not None
                   and "AOE" in d.status_tags(i)[1]]
            if len(aoe) >= 2:
                ev.reasons.append(Reason("passive_aoe_balls", f"보유한 범위 피해 볼 {len(aoe)}개를 함께 강화",
                                         2 * min(len(aoe), 3), "범위 볼 연계"))
        tb = None
        if p is not None:
            tb = p.turns_to_next_boss if p.turns_to_next_boss is not None else p.turns_to_boss
        if pe.role == "power" and tb is not None and 0 < tb <= 10:
            ev.reasons.append(Reason("passive_power_boss", f"보스까지 {tb}턴 — {ROLE_LABEL['power']} 패시브", 3,
                                     "보스 대비"))

    def _recipes(self, ev: ActionEval, item_id: str, kind: str, after: Optional[int], run: RunState):
        d = self.data
        known_max = d.max_level_known(kind)
        maxlv = d.max_level(kind)
        best: Optional[Reason] = None
        best_result = ""
        extra: List[str] = []
        for r in d.recipes_using(item_id):
            others = list(r.ingredients)
            others.remove(item_id)
            owned_others = [run.owned.get(o) for o in others]
            result = d.name(r.result)
            names = " + ".join(d.name(x) for x in r.ingredients)
            src = "" if r.source == "game" else ", 위키 레시피"
            if others and all(owned_others):
                others_max = [o.at_max is True or (known_max and o.level is not None and o.level >= maxlv)
                              for o in owned_others]
                if kind == "passive":
                    cand = Reason("passive_recipe_ready", f"{result} 재료가 모두 모임 (레벨 조건 미확인{src})", 16,
                                  f"{result} 재료")
                elif known_max and after is not None and all(o.level is not None for o in owned_others):
                    remaining = (maxlv - after) + sum(max(0, maxlv - o.level) for o in owned_others)
                    if remaining <= 0:
                        cand = Reason("evo_ready", f"{result} 진화 조건 충족 ({names} 최대 레벨)", 30,
                                      f"{result} 진화 가능")
                    else:
                        cand = Reason("evo_path", f"{result} 진화까지 강화 {remaining}번 남음 ({names})",
                                      max(8.0, 22.0 - 4 * remaining), f"{result} 진화 경로")
                elif all(others_max):
                    cand = Reason("evo_partner_max", f"{result} 재료 — 짝인 {' + '.join(d.name(o) for o in others)}은(는) 최대 레벨",
                                  16, f"{result} 진화 경로")
                else:
                    cand = Reason("evo_path_level_unknown", f"{result} 진화 재료 ({names}{src})", 12,
                                  f"{result} 진화 경로")
            elif any(owned_others) and len(others) >= 2:
                cand = Reason("evo_partial", f"{result} 재료 일부 보유", 5, f"{result} 재료 일부")
            else:
                continue
            if best is None or cand.weight > best.weight:
                if best is not None:
                    extra.append(best_result)
                best, best_result = cand, result
            else:
                extra.append(result)
        if best is not None:
            ev.reasons.append(best)
            if extra:
                names = ", ".join(dict.fromkeys(extra))
                ev.reasons.append(Reason("evo_multi", f"{names} 진화에도 쓰임", min(6, 2 * len(extra)),
                                         f"{names} 경로"))

    def _game_hints(self, ev: ActionEval, card: Card, run: RunState):
        """게임 연동으로 받은 게임 자체 판정: 보유 볼과의 시너지(카드 설명의 '시너지 장비'), 자동 선택 AI 기피."""
        d = self.data
        partners = [s for s in card.synergy if s != card.item_id and s in run.owned]
        if partners and not any(r.rule_id.startswith("evo") for r in ev.reasons):
            names = ", ".join(d.name(p) for p in partners[:2])
            ev.reasons.append(Reason("game_synergy", f"보유한 {names}와 시너지 (게임 판정)",
                                     6 + 2 * min(len(partners) - 1, 2), "시너지"))
        if card.ai_pick is False:
            ev.warnings.append(Reason("game_ai_avoid", "게임 자동 선택 AI는 고르지 않는 항목", -3, "AI 비선호"))

    def _character(self, ev: ActionEval, item, kind: str, upgrade: bool, run: RunState):
        d = self.data
        for cid in run.character_ids:
            rule = d.character_rule(cid)
            if not rule:
                continue
            cname = d.name(cid)
            if [t for t in rule.get("reduces_tags", []) if d.has_tag(item.id, t)]:
                ev.warnings.append(Reason("char_reduces", f"{cname}: {rule['reason']} — 효과가 줄거나 없을 수 있음", -18,
                                          "캐릭터와 안 맞음"))
            st_, dm_ = d.status_tags(item.id)
            if rule.get("boosts_wiki_status_or_aoe"):
                if st_ or "AOE" in dm_:
                    ev.reasons.append(Reason("char_sisyphus_boost", f"{cname}: 범위·상태 이상 피해 4배", 8, "캐릭터 연계"))
                elif kind == "ball":
                    ev.warnings.append(Reason("char_sisyphus_direct", f"{cname}: 볼 직접 피해가 없음", -8, "직접 피해 없음"))
            if rule.get("boosts_wiki_on_hit_status") and kind == "ball" and st_:
                ev.reasons.append(Reason("char_fast_fire", f"{cname}: 발사 속도 두 배로 타격 효과가 자주 발동", 5,
                                         "발사 속도 연계"))

    STATUS_AXES = ("burn", "freeze", "bleed", "poison", "curse")

    def _item_keys(self, item_id: str, kind: str, upgrade: bool, ev: ActionEval) -> List[str]:
        """캐릭터 궁합에 쓰는 항목 성격: status·aoe·direct·baby·sustain / crit·speed·defense·… / new_ball·upgrade_ball·passive."""
        keys: List[str] = []
        if kind == "ball":
            axes = axes_of(self.data, item_id)
            if any(a in self.STATUS_AXES for a in axes):
                keys.append("status")
            keys += [a for a in axes if a in ("aoe", "baby", "sustain")]
            if not axes:
                keys.append("direct")
            keys.append("upgrade_ball" if upgrade else "new_ball")
        elif kind == "passive":
            props = self.data.level_props.get(item_id) or []
            pe = passive_effect(props, ev.level_before if upgrade else None, ev.level_after)
            if pe is not None:
                keys.append({"power": "power", "aoe": "aoe", "defense": "defense", "baby": "baby",
                             "speed": "speed"}.get(pe.role, pe.role))
            if any("CritChance" in k for row in props for k in row):
                keys.append("crit")
            keys.append("passive")
        return keys

    def _char_strategy(self, ev: ActionEval, item, kind: str, upgrade: bool, run: RunState):
        """캐릭터 궁합 (공식 설명에서 끌어낸 프로필, data/rules.json characters.*.strategy)."""
        if kind not in ("ball", "passive"):
            return
        keys = self._item_keys(item.id, kind, upgrade, ev)
        for cid in run.character_ids:
            st = self.data.character_rule(cid).get("strategy")
            if not st:
                continue
            fav = st.get("favor", {})
            hits = [(fav[k], k) for k in keys if k in fav]
            if not hits:
                continue
            best = max(hits)
            worst = min(hits)
            cname = self.data.name(cid)
            if best[0] > 0:
                ev.reasons.append(Reason("char_fit", f"{cname} 궁합: {st.get('why', '')}", float(best[0]), "캐릭터 궁합"))
            if worst[0] < 0:
                ev.warnings.append(Reason("char_misfit", f"{cname}: {st.get('why', '')}", float(worst[0]), "캐릭터와 덜 맞음"))

    TIER_W = {"S": 3, "A": 1.5, "B": 0, "C": -1, "D": -1.5}   # 의견이라 작게: 비슷할 때 가르는 정도

    def _community(self, ev: ActionEval, item_id: str, kind: str, run: RunState):
        """커뮤니티 의견: 항목·진화 결과의 티어(볼 Game Rant, 패시브 Dexerto), 캐릭터 추천 빌드 핵심 항목.
        게임 값이 아니라 공략 사이트 평가라 가중치는 작게."""
        d = self.data
        if not d.community:
            return
        # 진화로 가는 카드면 그 결과의 티어를, 아니면 카드 자신의 티어를 본다
        # 진화 결과는 나머지 재료를 이미 가진 레시피만 (가능성만 있는 진화까지 세면 거의 모든 카드가 S 가 된다)
        results = [r.result for r in d.recipes_using(item_id)
                   if all(i == item_id or i in run.owned for i in r.ingredients)]
        tiers = [(d.community_tier(res), res) for res in results if d.community_tier(res)]
        if tiers:
            tier, res = min(tiers, key=lambda t: "SABCD".index(t[0]))
            if self.TIER_W[tier] > 0:
                ev.reasons.append(Reason("community_evo_tier", f"커뮤니티 평가: {d.name(res)} 진화는 {tier}티어",
                                         float(self.TIER_W[tier]), f"{tier}티어 진화"))
        else:
            tier = d.community_tier(item_id)
            if tier and self.TIER_W[tier] > 0:
                ev.reasons.append(Reason("community_tier", f"커뮤니티 평가 {tier}티어", float(self.TIER_W[tier]),
                                         f"{tier}티어"))
            elif tier and self.TIER_W[tier] < 0 and not ev.linked and not any(
                    d.community_tier(r.result) in ("S", "A") for r in d.recipes_using(item_id)):
                ev.warnings.append(Reason("community_tier_low", f"커뮤니티 평가 {tier}티어 (진화 재료가 아니면 약함)",
                                          float(self.TIER_W[tier]), f"{tier}티어"))
        for cid in run.character_ids:
            core, why = d.char_build_items(cid)
            hit = item_id in core or any(res in core for res in results)
            if hit:
                ev.reasons.append(Reason("char_build", f"{d.name(cid)} 추천 빌드 핵심 (커뮤니티): {why}", 5.0,
                                         "캐릭터 추천 빌드"))
                break

    def _char_history(self, ev: ActionEval, item_id: str, run: RunState):
        """내 기록: 이 캐릭터로 이 항목을 가진 런의 보스 격퇴율 (3번 이상일 때만, 캐릭터 평균과 비교)."""
        if not self.history or not run.character_ids:
            return
        cid = run.character_ids[0]
        runs = [h for h in self.history if getattr(h, "char", None) == cid and getattr(h, "result", "") != "중단"]
        if len(runs) < 4:
            return
        have = [h for h in runs if item_id in {i for i, _ in (getattr(h, "balls", []) or [])}
                | {i for i, _ in (getattr(h, "passives", []) or [])}]
        if len(have) < 3:
            return
        rate = sum(h.result == "보스 격퇴" for h in have) / len(have)
        base = sum(h.result == "보스 격퇴" for h in runs) / len(runs)
        cname = self.data.name(cid)
        text = f"내 기록({cname}): 이 항목을 가진 런 보스 격퇴 {round(rate * 100)}% ({len(have)}번, 이 캐릭터 평균 {round(base * 100)}%)"
        if rate >= base + 0.2:
            ev.reasons.append(Reason("char_record_good", text, 4, "이 캐릭터 기록 좋음"))
        elif rate <= base - 0.2:
            ev.warnings.append(Reason("char_record_bad", text, -3, "이 캐릭터 기록 나쁨"))

    def _support(self, ev: ActionEval, item_id: str, run: RunState, progress: Optional[RunProgress]):
        d = self.data
        owned_ids = [i for i in run.owned if i != item_id]
        has_heal = any(d.has_tag(i, "heal_source") for i in owned_ids)
        hr = progress.health_ratio if progress else None
        if d.has_tag(item_id, "heal_source"):
            if hr is not None and hr < 0.35:
                ev.reasons.append(Reason("heal_low_hp", f"체력 {round(hr * 100)}% — 회복 수단 우선", 12, "회복"))
            elif run.owned_complete and not has_heal:
                ev.reasons.append(Reason("heal_gap", "현재 회복 수단이 없음", 7, "회복 보완"))
        if d.has_tag(item_id, "needs_healing") and run.owned_complete and not has_heal:
            ev.warnings.append(Reason("heal_needed", "회복 수단이 없어 발동하기 어려움", -6, "발동 어려움"))
        reduced = any("baby_ball_source" in d.character_rule(c).get("reduces_tags", []) for c in run.character_ids)
        if not reduced:
            sources = sum(1 for i in owned_ids if d.has_tag(i, "baby_ball_source"))
            if d.has_tag(item_id, "baby_ball_scaling") and sources:
                ev.reasons.append(Reason("baby_synergy", f"보유한 베이비볼 생성 항목 {sources}개와 연계",
                                         4 * min(sources, 3), "베이비볼 연계"))
            elif d.has_tag(item_id, "baby_ball_source") and any(d.has_tag(i, "baby_ball_scaling") for i in owned_ids):
                ev.reasons.append(Reason("baby_synergy", "보유한 베이비볼 강화 패시브와 연계", 5, "베이비볼 연계"))

    def _progress(self, ev: ActionEval, kind: str, upgrade: bool, p: Optional[RunProgress]):
        """런 진행 상황에 따른 규칙 (게임 연동에서 받은 체력·턴·칸 수)."""
        if p is None:
            return
        frac = p.run_fraction
        early = frac is not None and frac < 0.4
        if not upgrade:
            cap = p.max_balls if kind == "ball" else p.max_passives
            have = p.balls if kind == "ball" else p.passives
            if cap is not None and have is not None:
                free = cap - have
                if free <= 0:
                    ev.warnings.append(Reason("slot_full", f"{'볼' if kind == 'ball' else '패시브'} 칸이 가득 참 ({have}/{cap})",
                                              -25, "칸 없음"))
                elif kind == "ball" and have <= 2:
                    # 실제 플레이 확인: 볼 1~2개로 강화만 거듭하면 턴 30대에 적이 쌓여 전멸했다
                    ev.reasons.append(Reason("slot_fill_few", f"볼이 {have}개뿐 — 볼 수를 늘려야 여러 적을 동시에 처리",
                                             9, "볼 수 늘리기"))
                elif kind == "ball" and early:
                    ev.reasons.append(Reason("slot_fill_early", f"빈 볼 칸 {free}개 — 초반엔 볼 수를 늘리면 화력이 오름", 5,
                                             "빈 칸 채우기"))
                elif kind == "passive" and free >= 2:
                    ev.reasons.append(Reason("slot_fill_passive", f"빈 패시브 칸 {free}개", 2, "빈 칸"))
        tb = p.turns_to_next_boss if p.turns_to_next_boss is not None else p.turns_to_boss
        if upgrade and kind == "ball" and tb is not None and 0 < tb <= max(15, int((p.final_boss_turn or 0) * 0.12)):
            ev.reasons.append(Reason("boss_soon", f"보스까지 {tb}턴 — 보유 볼 강화로 바로 화력 확보", 4, "보스 대비"))

    def _performance(self, ev: ActionEval, item_id: str, kind: str, upgrade: bool, run: RunState):
        """실제 성능: 이번 런에서 잰 피해(게임 통계)와 내 누적 기록(게임 세이브)."""
        if kind == "passive":
            self._passive_performance(ev, item_id, upgrade, run)
            return
        if kind != "ball":
            return
        if upgrade:
            share = run.damage_share(item_id)
            dealers = sum(1 for i, v in run.damage.items() if i.startswith("ball:") and v > 0)
            if share is None or dealers < 2:
                return      # 실제 플레이 확인: 볼이 하나면 피해 100% 라 '주력' 판단이 무의미하다 (그 볼만 계속 강화하다 전멸)
            pct, rank = round(share * 100), run.damage_rank(item_id)
            if share >= 0.3:
                ev.reasons.append(Reason("run_carry", f"이번 런 피해 {rank}위 ({pct}%) — 주력 볼을 더 키움",
                                         4 + round(10 * share), "주력 볼"))
            elif share < 0.08 and not ev.linked:
                ev.warnings.append(Reason("run_weak", f"이번 런 피해 {pct}% — 기여가 작은 볼", -3, "피해 적음"))
            return
        rec = self.meta.records.get(item_id) if self.meta else None
        rank = self.meta.damage_rank(item_id) if self.meta else None
        if rec is None or rank is None:
            return
        r, n = rank
        if r <= max(1, n // 4):
            ev.reasons.append(Reason("my_record_top", f"내 기록: 런당 평균 피해 {r}위 / {n}개 볼 (가진 런 {rec.obtained}번)",
                                     4, "내 기록 상위"))
        elif r > n - max(1, n // 4):
            ev.warnings.append(Reason("my_record_low", f"내 기록: 런당 평균 피해 {r}위 / {n}개 볼", -2, "내 기록 하위"))

    def _passive_performance(self, ev: ActionEval, item_id: str, upgrade: bool, run: RunState):
        """패시브: 이번 런 추가 피해(게임 통계, 볼 피해 대비)와 내 기록의 '가진 런 완료율' 순위."""
        balls = sum(v for i, v in run.damage.items() if i.startswith("ball:"))
        bonus = run.damage.get(item_id)
        if upgrade and bonus and balls >= 2000:
            ratio = bonus / balls
            if ratio >= 0.1:
                ev.reasons.append(Reason("passive_dmg", f"이번 런 추가 피해 {round(ratio * 100)}% (볼 피해 대비)",
                                         min(8, 3 + round(10 * ratio)), "추가 피해 큼"))
        rank = self.meta.completion_rank(item_id) if self.meta else None
        if rank is None:
            return
        r, n, rate = rank
        if r <= max(1, n // 4):
            ev.reasons.append(Reason("my_record_clear", f"내 기록: 가진 런 완료율 {round(rate * 100)}% ({r}위 / {n})",
                                     3, "내 기록 상위"))
        elif r > n - max(1, n // 4):
            ev.warnings.append(Reason("my_record_clear_low", f"내 기록: 가진 런 완료율 {round(rate * 100)}% ({r}위 / {n})",
                                      -2, "내 기록 하위"))

    def reroll_odds(self, s: ChoiceSession, plan: DeckPlan, run: RunState) -> Optional[Tuple[float, int, int]]:
        """새로고침하면 목표 카드(목표 진화의 빠진 재료, 핵심·주력 항목 강화)가 한 장 이상 나올 확률.

        게임이 알려 준 후보 목록에서 지금 선택지(새로고침하면 빠짐)를 뺀 뒤, 균등하게 뽑는다고 가정한 추정이다.
        실제 뽑기 가중치는 확인하지 못했다.
        """
        pool = s.pool
        if pool is None:
            return None
        current = {c.item_id for c in s.cards if c.item_id}
        entries = [(i, up) for i, up in pool.entries() if i not in current]
        n = max(1, pool.num_choices)

        def good(i: str, up: bool) -> bool:
            if not up:
                return i in plan.wanted
            o = run.owned.get(i)
            if o is not None and o.at_max:
                return False
            share = run.damage_share(i)
            return i in plan.core or (share is not None and share >= 0.3)

        total = len(entries)
        k = sum(1 for i, up in entries if good(i, up))
        if total == 0:
            return None
        if total - k < n:
            return 1.0, total, k
        w = self.draw_weights
        if not w:
            return 1 - comb(total - k, n) / comb(total, n), total, k
        # 관측 보정: 종류별 가중치로 한 장씩 뽑는다고 보고, 뽑힌 카드는 평균 가중치만큼 뺀다 (근사)
        from ..tracking.draw_stats import category
        weights = [(w.get(category(i, up) or "", 1.0), good(i, up)) for i, up in entries]
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
        if target and best is not None and plan.targets:
            name = self.data.name(plan.targets[0].recipe.result)
            if any(name in r.text for r in best.reasons):
                target = ""
        arch = getattr(self, "_arch", None)
        arch_text = f"덱 계열: {arch.text}" if arch and arch.top else ""
        return " · ".join(x for x in (target, arch_text, plan.phase_text) if x)

    def _plan(self, ev: ActionEval, kind: str, upgrade: bool, plan: DeckPlan):
        """덱 방향에 따른 규칙: 최대 레벨 도달(융합 재료), 무한의 심연, 마지막 빈 칸."""
        d = self.data
        if upgrade and kind == "ball" and d.max_level_known("ball") and ev.level_after == d.max_level("ball") \
                and not any(r.rule_id == "evo_ready" for r in ev.reasons):
            ev.reasons.append(Reason("reach_max_fusion", "이번 강화로 최대 레벨 — 융합 화면에서 다른 최대 레벨 볼과 합칠 수 있음",
                                     6, "최대 레벨 달성"))
        if plan.phase == "endless":
            if upgrade:
                ev.reasons.append(Reason("endless_upgrade", "무한의 심연: 적이 계속 강해져 새 항목보다 보유 항목 강화가 오래 감",
                                         5, "강화 누적"))
            elif not ev.linked:
                ev.warnings.append(Reason("endless_unlinked", "무한의 심연: 진화로 이어지지 않는 새 항목은 가치가 낮음",
                                          -4, "덱과 연결 없음"))
            return
        free = plan.free_slots(kind)
        if not upgrade and free == 1 and not ev.linked:
            what = "볼" if kind == "ball" else "패시브"
            ev.warnings.append(Reason("last_slot", f"마지막 {what} 칸 — 진화로 이어지는 {what}에 남겨 두는 편이 나음",
                                      -5 if kind == "ball" else -3, "마지막 칸"))

    # ---- 새로고침·삭제 ----
    def _reroll(self, s: ChoiceSession, known: List[ActionEval], unknown: int, status: str,
                odds: Optional[Tuple[float, int, int]] = None):
        can = s.can_reroll
        if s.free_rerolls is not None:
            price = f"무료 {s.free_rerolls}회 남음"
        elif s.reroll_cost is not None:
            price = f"{s.reroll_cost}골드" + (f", 보유 {s.gold}골드" if s.gold is not None else ", 보유 골드 미확인")
        else:
            price = "비용 미확인"
        if can is False:
            return "none", f"새로고침 불가 — {price}"
        if unknown:
            return "unknown", "새로고침 판단 보류 — 읽지 못한 카드가 있음"
        weak = status == "hold" or all(e.score < 8 for e in known)
        if not weak:
            return "keep", "새로고침 불필요 — 조합에 이어지는 선택지가 있음"
        chance = ""
        if odds is not None:
            p, total, k = odds
            if k == 0:
                return "keep", f"새로고침해도 목표 카드가 후보에 없음 (후보 {total}장) — {price}"
            basis = "관측 보정" if self.draw_weights else "추정"
            chance = f" · 목표 카드 나올 확률 약 {round(p * 100)}% (후보 {total}장 중 {k}장, {basis})"
        if can is None:
            return "consider", f"새로고침 검토 — {price}{chance}"
        if s.free_rerolls is None and s.gold and s.reroll_cost and s.reroll_cost > s.gold * 0.5:
            return "keep", f"새로고침 아껴 두기 — {price} (비용이 보유 골드의 절반 이상){chance}"
        if odds is not None:
            return "consider", f"새로고침 고려 — {price}{chance}"
        return "consider", f"새로고침 고려 — {price} (다음 선택지는 예측 불가)"

    BANISH_WEIGHTS = {"slot_full": 3, "char_reduces": 3, "char_sisyphus_direct": 2, "endless_unlinked": 2,
                      "last_slot": 2, "heal_needed": 1, "game_ai_avoid": 1, "duplicate_copy": 1}

    def _banish(self, s: ChoiceSession, known: List[ActionEval], best: Optional[ActionEval],
                run: RunState, plan: DeckPlan):
        """삭제: 이번 런에서 다시 나오지 않게 할 카드. 삭제 횟수가 적으니(런당 몇 번) 확실할 때만 권한다.

        대상: 덱 목표(진화 재료·핵심 항목)와 이어지지 않고, 칸·캐릭터·단계상 앞으로도 고를 일이 없는 새 항목.
        이번 런에 여러 번 나온 카드일수록(선택지를 계속 차지) 먼저 권한다.
        """
        if not s.banish_left:
            return None, ""
        cands = []
        for e in known:
            iid = e.card.item_id
            if (best is not None and e is best) or e.action.startswith("upgrade") or e.linked \
                    or iid in plan.wanted or iid in plan.core:
                continue
            bad = sorted((w for w in e.warnings if w.rule_id in self.BANISH_WEIGHTS),
                         key=lambda w: -self.BANISH_WEIGHTS[w.rule_id])
            weight = sum(self.BANISH_WEIGHTS[w.rule_id] for w in bad)
            seen = run.offered.get(iid, 0)
            if weight and seen >= 2:
                weight += 1
            if weight >= 2:
                cands.append((weight, seen, e.score, e, bad))
        if not cands:
            return None, ""
        cands.sort(key=lambda t: (-t[0], -t[1], t[2]))
        weight, seen, _, e, bad = cands[0]
        if len(cands) == len(known) and len(known) > 1 and seen < 2 \
                and all(c[0] == weight for c in cands):
            # 셋 다 똑같이 쓸모없으면 하나를 고를 근거가 없다 — 드문 삭제 대신 새로고침이 맞다
            return None, ""
        why = bad[0].text
        more = f" · 이번 런 {seen}번째 등장" if seen >= 2 else ""
        return e.card, (f"삭제 추천: {self.data.name(e.card.item_id)} ({e.card.position}) — {why}{more} · "
                        f"삭제 {s.banish_left}회 남음")
