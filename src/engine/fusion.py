"""융합 화면 추천: 지금 고를 수 있는 진화(A + B → C)와 융합(최대 레벨 볼 두 개를 하나로) 중 무엇이 나은가.

근거
- 후보 목록은 게임이 실제로 제시한 것(게임 연동)만 쓴다. 없는 조합을 만들지 않는다.
- 융합 조합에는 게임 자동 선택 AI(숙고자·극단분자가 쓰는 것)의 점수와 '나쁜 조합' 판정이 함께 온다.
  이 값은 게임 개발자의 판단이므로 가장 큰 근거로 쓰되, 내부 점수 자체는 화면에 보여 주지 않는다.
- 진화는 결과 볼이 다시 다음 진화 재료가 되는지, 캐릭터 특성·회복 수단과 맞는지를 본다.
- 진화에는 기본 가산(칸이 비고 더 강한 볼)을 주고, 게임 수치(레벨별 기본 피해)·이번 런 실측 피해·내 누적 기록을
  더해 진화와 융합을 한 줄로 비교한다. 수치가 없으면 가산만으로 진화를 먼저 권하고 그렇게 밝힌다.
- 분열(무료 강화)도 진화·융합과 같은 점수 척도로 비교한다. 커뮤니티 공략(dood.gg 메타 가이드: "초반엔 분열로
  볼을 먼저 레벨업하고, 재료가 갖춰지면 그때 융합·진화를 노려라")에 따라 아직 최대 레벨이 안 된 볼이 많을수록
  분열 점수를 올린다. 플러그인은 분열로 어떤 볼이 오르는지는 안 알려줘서(가능 여부만 bool) 이 정도 추정까지만 가능.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from typing import List, Optional

from ..domain import FuserCombo, FuserEvo, FuserOptions, InventorySlot
from ..gamedata import GameData
from ..tracking.run_state import RunState
from .recommender import Reason
from ..tracking.meta_state import MetaState
from ..i18n import tr


@dataclass
class FusionPick:
    kind: str  # evo | combo | free
    title: str  # "대출혈" / "출혈 + 무쇠"
    detail: str  # "출혈 + 무쇠 → 진화" / "두 볼의 효과를 한 볼에"
    result_id: Optional[str] = None  # 진화 결과 (아이콘용)
    parts: tuple = ()  # 재료 ID들
    score: float = 0.0
    reasons: List[Reason] = field(default_factory=list)
    warnings: List[Reason] = field(default_factory=list)
    selectable: bool = True  # 항목을 식별하지 못했으면 점수와 무관하게 추천에서 제외
    display_parts: tuple = ()  # 이미 합쳐진 효과를 포함한 표시용 아이콘. 실제 재료 parts와 구분한다.


@dataclass
class FusionRecommendation:
    status: str  # recommend | close | hold | none
    headline: str
    best: Optional[FusionPick]
    evos: List[FusionPick]
    combos: List[FusionPick]
    notes: List[str] = field(default_factory=list)
    free: Optional[FusionPick] = None


class FusionAdvisor:
    def __init__(self, data: GameData):
        self.data = data
        self.discovery_mode = False
        self.meta: Optional[MetaState] = None

    def _record(self, pick: FusionPick, item_id: Optional[str]):
        """내 누적 기록(게임 세이브)의 런당 평균 피해 순위."""
        rank = self.meta.damage_rank(item_id) if self.meta and item_id else None
        if rank is None:
            return
        rank_position, n = rank
        if rank_position <= max(1, n // 4):
            pick.reasons.append(
                Reason(
                    "fz_record_top",
                    tr("내 기록: {v0} 런당 평균 피해 {r}위 / {n}", v0=self.data.name(item_id), r=rank_position, n=n),
                    4,
                    tr("내 기록 상위"),
                )
            )
            pick.score += 4
        elif rank_position > n - max(1, n // 4):
            pick.warnings.append(
                Reason(
                    "fz_record_low",
                    tr("내 기록: {v0} 런당 평균 피해 {r}위 / {n}", v0=self.data.name(item_id), r=rank_position, n=n),
                    -2,
                    tr("내 기록 하위"),
                )
            )
            pick.score -= 2

    def recommend(
        self, fz: Optional[FuserOptions], inventory: Optional[tuple], run_state: RunState
    ) -> FusionRecommendation:
        record = self._recommend_normal(fz, inventory, run_state)
        if self.discovery_mode:
            from .discovery import apply_fusion_discovery

            return apply_fusion_discovery(record, self.meta, self.data)
        return record

    def _recommend_normal(
        self, fz: Optional[FuserOptions], inventory: Optional[tuple], run_state: RunState
    ) -> FusionRecommendation:
        d = self.data
        if fz is None:
            return FusionRecommendation(
                "none",
                tr("융합 후보를 받지 못했습니다"),
                None,
                [],
                [],
                [tr("게임 연동 플러그인을 최신으로 설치하면 후보를 받습니다")],
            )
        # 합쳐 넣어진 볼(InventorySlot.combined)은 자기 칸이 없어져 이 목록에 안 나온다 — evos/combos 도
        # 게임이 남은 볼만으로 준 후보라 따로 걸러낼 게 없다. 합친 볼을 다시 재료로 쓸 수 있는지는 모름(추정 아님,
        # 미확인) — 게임이 그런 후보를 준 적이 없어 지금은 판단할 자료가 없다.
        balls = [s for s in (inventory or ()) if s.item_id and s.item_id.startswith("ball:")]
        evos = [self._evo(evaluation, list(inventory or ()), run_state) for evaluation in fz.evos]
        combos = [self._combo(component, run_state, inventory or ()) for component in fz.combos]
        evos.sort(key=lambda passive: -passive.score)
        combos.sort(key=lambda passive: -passive.score)
        free_pick = self._free(balls) if fz.free_upgrades else None
        notes = []
        if d.recipe_source != "game":
            notes.append(tr("진화 레시피는 위키 기준"))

        # 진화 기본 가산 + 수치·실측·기록. 분열(무료 강화)도 같은 점수 척도로 한 번에 비교한다
        best: Optional[FusionPick] = None
        all_picks = evos + combos + ([free_pick] if free_pick else [])
        ranked = sorted(
            [
                passive
                for passive in all_picks
                if passive.selectable
                and not any(warning.rule_id == "combo_bad" for warning in passive.warnings)
            ],
            key=lambda passive: -passive.score,
        )
        if ranked:
            best = ranked[0]
            if sum(1 for group in (evos, combos, [free_pick] if free_pick else []) if group) > 1:
                measured = any(
                    reason.rule_id.startswith(("fz_", "evo_dmg"))
                    for passive in all_picks
                    for reason in passive.reasons
                )
                notes.append(
                    tr("진화 기본 가산 + 게임 수치·실측 피해로 비교")
                    if measured
                    else tr("진화를 먼저 권함 (비교할 수치가 아직 없음)")
                )
        if best is None:
            return FusionRecommendation("hold", tr("권할 조합이 없음"), None, evos, combos, notes, free_pick)

        close = [
            passive
            for passive in all_picks
            if passive is not best
            and passive.selectable
            and best.score - passive.score < 4
            and not any(warning.rule_id == "combo_bad" for warning in passive.warnings)
        ]
        status = "close" if close else "recommend"
        verb = {"evo": tr("진화"), "combo": tr("융합"), "free": ""}[best.kind]
        headline = f"{best.title} {verb}".strip()
        return FusionRecommendation(status, headline, best, evos, combos, notes, free_pick)

    # ---- 분열(무료 강화) 평가 ----
    FREE_BASE = 8  # 융합 기본(10)보다 살짝 낮게 시작 — 슬롯을 안 비우는 대신 유연성을 남김
    FREE_UNLEVELED_W = 3  # 최대 레벨이 안 된 볼 하나당 가산 (최대 +18, 6개까지만 셈)

    def _free(self, balls: List[InventorySlot]) -> FusionPick:
        d = self.data
        pick = FusionPick("free", tr("무료 강화"), tr("보유 볼 레벨이 1~2회 오름"), score=self.FREE_BASE)
        pick.reasons.append(
            Reason(
                "free_base",
                tr("다음 진화·융합 재료가 될 볼들을 먼저 레벨업 (분열)"),
                self.FREE_BASE,
                tr("분열"),
            )
        )
        maxlv = d.max_level("ball")
        unleveled = [s for s in balls if s.level is not None and s.level < maxlv]
        if unleveled:
            item_count = min(len(unleveled), 6)
            bonus = item_count * self.FREE_UNLEVELED_W
            pick.reasons.append(
                Reason(
                    "free_unleveled",
                    tr("아직 최대 레벨이 아닌 볼 {v0}개 — 레벨업 여지 큼", v0=len(unleveled)),
                    bonus,
                    tr("레벨업 여지"),
                )
            )
            pick.score += bonus
        elif balls:
            pick.warnings.append(
                Reason(
                    "free_all_maxed",
                    tr("보유 볼이 대부분 이미 최대 레벨 — 강화 효율 낮음"),
                    -6,
                    tr("레벨업 여지 적음"),
                )
            )
            pick.score -= 6
        return pick

    # ---- 진화 한 개 평가 ----
    def _evo(self, e: FuserEvo, inventory: List[InventorySlot], run_state: RunState) -> FusionPick:
        d = self.data
        result = e.item_id
        # 결과 ID만으로 첫 레시피를 고르지 않는다. 실제 별도 칸에 있는 재료로 충족되는 경로만 비교한다.
        # evo_idx와 equip_idx는 보존하지만 게임의 배열 의미를 검증하기 전에는 레시피 번호로 추정하지 않는다.
        slots = [s for s in inventory if s.occupied and d.item(s.item_id) is not None]
        available = Counter(s.item_id for s in slots)
        matches = (
            {
                recipe.ingredients
                for recipe in d.recipes_for(result)
                if not (Counter(recipe.ingredients) - available)
            }
            if result
            else set()
        )
        parts = next(iter(matches)) if len(matches) == 1 else ()
        title = d.name(result)
        detail = (" + ".join(d.name(passive) for passive in parts) + tr(" → 진화")) if parts else tr("진화")
        pick = FusionPick("evo", title, detail, result, parts, score=20)
        if d.item(result) is None:
            pick.selectable = False
            pick.warnings.append(Reason("evo_unknown", tr("결과 항목을 알 수 없음"), -20, tr("미확인")))
            pick.score -= 20
            return pick
        if parts:
            base_text = tr(
                "재료 {count}개로 진화 — {slots}칸 확보", count=len(parts), slots=max(0, len(parts) - 1)
            )
        else:
            base_text = tr("게임이 제시한 진화 후보 — 사용할 재료 조합은 미확인")
            pick.warnings.append(
                Reason(
                    "evo_parts_unknown",
                    tr("재료 조합을 확정할 수 없어 재료의 피해·회복 손실은 비교하지 않음"),
                    0,
                    tr("미확인"),
                )
            )
        pick.reasons.append(Reason("evo_base", base_text, 20, tr("진화")))
        # 결과가 다음 진화 재료가 되는가
        remaining = available - Counter(parts)
        for recipe in d.recipes_using(result):
            if not parts or not d.recipe_reachable(recipe):
                continue
            others = [x for x in recipe.ingredients if x != result]
            if others and not (Counter(others) - remaining):
                pick.reasons.append(
                    Reason(
                        "evo_chain",
                        tr("다음 진화 {v0}의 재료를 이미 보유", v0=d.name(recipe.result)),
                        8,
                        tr("{v0} 경로", v0=d.name(recipe.result)),
                    )
                )
                pick.score += 8
                break
        self._character_fit(pick, result, run_state)
        self._community(pick, result, run_state)
        # 게임 수치: 결과 볼의 1레벨 기본 피해와 재료들의 지금 피해 비교
        # 같은 종류의 다른 레벨 복사본 중 어느 것을 쓰는지 모르면 피해 비교를 보류한다.
        levels = {}
        for item_id in parts:
            observed = {s.level for s in slots if s.item_id == item_id}
            levels[item_id] = next(iter(observed)) if len(observed) == 1 else None
        res = d.damage_range(result, 1)
        mats = [d.damage_range(p_, levels.get(p_)) for p_ in parts]
        if res and mats and all(mats):
            mid = (res[0] + res[1]) / 2
            mat_mid = max((m[0] + m[1]) / 2 for m in mats)
            if mid > mat_mid:
                pick.reasons.append(
                    Reason(
                        "evo_dmg_up",
                        tr(
                            "기본 피해 {v0}–{v1} (재료 중 최고 {v2} 안팎보다 높음, 게임 수치)",
                            v0=res[0],
                            v1=res[1],
                            v2=int(mat_mid),
                        ),
                        4,
                        tr("피해 상승"),
                    )
                )
                pick.score += 4
        # 이번 런 실측: 재료가 주력 볼이면 그 화력이 진화 볼로 이어진다
        share = sum(run_state.damage_share(p_) or 0 for p_ in parts)
        if share >= 0.3:
            pick.reasons.append(
                Reason(
                    "fz_run_share",
                    tr("재료가 이번 런 피해의 {v0}%를 담당", v0=round(share * 100)),
                    3,
                    tr("주력 재료"),
                )
            )
            pick.score += 3
        self._record(pick, result)
        # 회복 수단 손실
        # 별도 칸의 흡혈과 다른 볼에 합쳐진 흡혈이 동시에 있으면 서로 다른 회복 수단이다.
        # 진화할 때 combined 효과까지 사라지는지는 확인되지 않아 그 손실을 임의로 단정하지 않는다.
        heal_owned = Counter(
            index
            for s in slots
            for index in (s.item_id, *s.combined)
            if index and d.has_tag(index, "heal_source")
        )
        lost = [passive for passive in parts if d.has_tag(passive, "heal_source") and not remaining[passive]]
        if lost and not (heal_owned - Counter(lost)) and not d.has_tag(result, "heal_source"):
            pick.warnings.append(
                Reason(
                    "evo_lose_heal",
                    tr("유일한 회복 수단({v0})이 사라짐", v0=d.name(lost[0])),
                    -8,
                    tr("회복 잃음"),
                )
            )
            pick.score -= 8
        return pick

    # ---- 융합 조합 한 개 평가 ----
    def _combo(self, c: FuserCombo, run_state: RunState, inventory=()) -> FusionPick:
        d = self.data

        def members(item, index):
            slot = next((s for s in inventory if s.index == index and s.item_id == item), None)
            return tuple(dict.fromkeys((item, *(slot.combined if slot else ()))))

        left, right = members(c.item1, c.idx1), members(c.item2, c.idx2)

        def label(parts):
            names = " + ".join(d.name(index) for index in parts)
            return f"({names})" if len(parts) > 1 else names

        title = f"{label(left)} + {label(right)}"
        pick = FusionPick("combo", title, tr("두 볼의 효과를 한 볼에"), None, (c.item1, c.item2), score=10)
        pick.display_parts = tuple(dict.fromkeys(left + right))
        if len(left) > 1 or len(right) > 1:
            # 게임 1.301 PopulateUpgrades(51CCBC/51CD6D): 두 후보 모두 IsAtMaxSolo여야 한다.
            # 닫히는 UI의 후보 캐시가 남아 있어도 이미 융합된 볼을 다시 합치라고 권하지 않는다.
            pick.selectable = False
            pick.warnings.append(
                Reason("combo_already_fused", tr("이미 융합된 볼은 다시 융합할 수 없음"), 0, tr("융합 불가"))
            )
            return pick
        if d.item(c.item1) is None or d.item(c.item2) is None:
            pick.selectable = False
            pick.warnings.append(
                Reason("combo_unknown", tr("융합 재료를 읽지 못해 추천을 보류함"), 0, tr("미확인"))
            )
            return pick
        pick.reasons.append(
            Reason("combo_base", tr("최대 레벨 볼 두 개를 합쳐 칸이 하나 빔"), 10, tr("융합"))
        )
        if c.ai_score is not None:
            # 게임 AI 점수는 크기 단위를 모르므로 순위 비교에만 쓴다 (0.1배 가중)
            pick.score += float(c.ai_score) * 0.1
            pick.reasons.append(
                Reason("combo_ai", tr("게임 자동 선택 AI가 높게 보는 조합"), 0, tr("AI 추천"))
            )
        if c.bad:
            pick.warnings.append(Reason("combo_bad", tr("게임이 나쁜 조합으로 판정"), -30, tr("나쁜 조합")))
            pick.score -= 30
        for item in (c.item1, c.item2):
            if item:
                self._character_fit(pick, item, run_state)
        share = sum(run_state.damage_share(index) or 0 for index in (c.item1, c.item2) if index)
        if share >= 0.3:
            pick.reasons.append(
                Reason(
                    "fz_run_share",
                    tr("두 볼이 이번 런 피해의 {v0}%를 담당 — 한 볼에 모음", v0=round(share * 100)),
                    round(share * 8),
                    tr("주력 조합"),
                )
            )
            pick.score += round(share * 8)
        for item in (c.item1, c.item2):
            self._record(pick, item)
        return pick

    EVO_TIER_W = {"S": 4, "A": 2, "B": 0, "C": -1, "D": -2}

    def _community(self, pick: FusionPick, result: str, run_state: RunState):
        """커뮤니티 평가: 진화 결과의 티어(Game Rant)와 캐릭터 추천 빌드 핵심 항목 (의견이라 작게 — 동점일 때 가르는 정도)."""
        d = self.data
        tier = d.community_tier(result) if d.community else None
        if tier and self.EVO_TIER_W[tier]:
            w = self.EVO_TIER_W[tier]
            (pick.reasons if w > 0 else pick.warnings).append(
                Reason(
                    "fz_tier", tr("커뮤니티 평가 {tier}티어 진화", tier=tier), w, tr("{tier}티어", tier=tier)
                )
            )
            pick.score += w
        for component_id in run_state.character_ids:
            core, why = d.char_build_items(component_id)
            if result in core:
                pick.reasons.append(
                    Reason(
                        "fz_char_build",
                        tr("{v0} 추천 빌드 핵심 (커뮤니티): {why}", v0=d.name(component_id), why=why),
                        5,
                        tr("캐릭터 추천 빌드"),
                    )
                )
                pick.score += 5
                break

    def _character_fit(self, pick: FusionPick, item_id: str, run_state: RunState):
        d = self.data
        item = d.item(item_id)
        if item is None:
            return
        for component_id in run_state.character_ids:
            rule = d.character_rule(component_id)
            if [tag for tag in rule.get("reduces_tags", []) if d.has_tag(item_id, tag)]:
                pick.warnings.append(
                    Reason(
                        "char_reduces",
                        f"{d.name(component_id)}: {tr(rule['reason'])}",
                        -12,
                        tr("캐릭터와 안 맞음"),
                    )
                )
                pick.score -= 12
            st_, dm_ = self.data.status_tags(item.id)
            if rule.get("boosts_wiki_status_or_aoe") and (st_ or "AOE" in dm_):
                pick.reasons.append(
                    Reason(
                        "char_sisyphus_boost",
                        tr("{v0}: 범위·상태 이상 피해 4배", v0=d.name(component_id)),
                        6,
                        tr("캐릭터 연계"),
                    )
                )
                pick.score += 6
