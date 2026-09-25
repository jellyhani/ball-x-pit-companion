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
from typing import List, Optional

from ..domain import FuserCombo, FuserEvo, FuserOptions, InventorySlot
from ..gamedata import GameData
from ..tracking.run_state import RunState
from .recommender import Reason
from ..tracking.meta_state import MetaState


@dataclass
class FusionPick:
    kind: str                         # evo | combo | free
    title: str                        # "대출혈" / "출혈 + 무쇠"
    detail: str                       # "출혈 + 무쇠 → 진화" / "두 볼의 효과를 한 볼에"
    result_id: Optional[str] = None   # 진화 결과 (아이콘용)
    parts: tuple = ()                 # 재료 ID들
    score: float = 0.0
    reasons: List[Reason] = field(default_factory=list)
    warnings: List[Reason] = field(default_factory=list)


@dataclass
class FusionRecommendation:
    status: str                       # recommend | close | hold | none
    headline: str
    best: Optional[FusionPick]
    evos: List[FusionPick]
    combos: List[FusionPick]
    notes: List[str] = field(default_factory=list)
    free: Optional[FusionPick] = None


class FusionAdvisor:
    def __init__(self, data: GameData):
        self.data = data
        self.meta: Optional[MetaState] = None

    def _record(self, pick: FusionPick, item_id: Optional[str]):
        """내 누적 기록(게임 세이브)의 런당 평균 피해 순위."""
        rank = self.meta.damage_rank(item_id) if self.meta and item_id else None
        if rank is None:
            return
        r, n = rank
        if r <= max(1, n // 4):
            pick.reasons.append(Reason("fz_record_top", f"내 기록: {self.data.name(item_id)} 런당 평균 피해 {r}위 / {n}",
                                       4, "내 기록 상위"))
            pick.score += 4
        elif r > n - max(1, n // 4):
            pick.warnings.append(Reason("fz_record_low", f"내 기록: {self.data.name(item_id)} 런당 평균 피해 {r}위 / {n}",
                                        -2, "내 기록 하위"))
            pick.score -= 2

    def recommend(self, fz: Optional[FuserOptions], inventory: Optional[tuple], run: RunState) -> FusionRecommendation:
        d = self.data
        if fz is None:
            return FusionRecommendation("none", "융합 후보를 받지 못했습니다", None, [], [],
                                        ["게임 연동 플러그인을 최신으로 설치하면 후보를 받습니다"])
        balls = [s for s in (inventory or ()) if s.item_id and s.item_id.startswith("ball:")]
        evos = [self._evo(e, balls, run) for e in fz.evos]
        combos = [self._combo(c, run) for c in fz.combos]
        evos.sort(key=lambda p: -p.score)
        combos.sort(key=lambda p: -p.score)
        free_pick = self._free(balls) if fz.free_upgrades else None
        notes = []
        if d.recipe_source != "game":
            notes.append("진화 레시피는 위키 기준")

        # 진화 기본 가산 + 수치·실측·기록. 분열(무료 강화)도 같은 점수 척도로 한 번에 비교한다
        best: Optional[FusionPick] = None
        all_picks = evos + combos + ([free_pick] if free_pick else [])
        ranked = sorted([p for p in all_picks if not any(w.rule_id == "combo_bad" for w in p.warnings)],
                        key=lambda p: -p.score)
        if ranked:
            best = ranked[0]
            if sum(1 for group in (evos, combos, [free_pick] if free_pick else []) if group) > 1:
                measured = any(r.rule_id.startswith(("fz_", "evo_dmg")) for p in all_picks for r in p.reasons)
                notes.append("진화 기본 가산 + 게임 수치·실측 피해로 비교" if measured
                             else "진화를 먼저 권함 (비교할 수치가 아직 없음)")
        if best is None:
            return FusionRecommendation("hold", "권할 조합이 없음", None, evos, combos, notes, free_pick)

        close = [p for p in all_picks if p is not best and best.score - p.score < 4
                 and not any(w.rule_id == "combo_bad" for w in p.warnings)]
        status = "close" if close else "recommend"
        verb = {"evo": "진화", "combo": "융합", "free": ""}[best.kind]
        headline = f"{best.title} {verb}".strip()
        return FusionRecommendation(status, headline, best, evos, combos, notes, free_pick)

    # ---- 분열(무료 강화) 평가 ----
    FREE_BASE = 8            # 융합 기본(10)보다 살짝 낮게 시작 — 슬롯을 안 비우는 대신 유연성을 남김
    FREE_UNLEVELED_W = 3     # 최대 레벨이 안 된 볼 하나당 가산 (최대 +18, 6개까지만 셈)

    def _free(self, balls: List[InventorySlot]) -> FusionPick:
        d = self.data
        pick = FusionPick("free", "무료 강화", "보유 볼 레벨이 1~2회 오름", score=self.FREE_BASE)
        pick.reasons.append(Reason("free_base", "다음 진화·융합 재료가 될 볼들을 먼저 레벨업 (분열)", self.FREE_BASE, "분열"))
        maxlv = d.max_level("ball")
        unleveled = [s for s in balls if s.level is not None and s.level < maxlv]
        if unleveled:
            n = min(len(unleveled), 6)
            bonus = n * self.FREE_UNLEVELED_W
            pick.reasons.append(Reason("free_unleveled", f"아직 최대 레벨이 아닌 볼 {len(unleveled)}개 — 레벨업 여지 큼",
                                       bonus, "레벨업 여지"))
            pick.score += bonus
        elif balls:
            pick.warnings.append(Reason("free_all_maxed", "보유 볼이 대부분 이미 최대 레벨 — 강화 효율 낮음", -6,
                                        "레벨업 여지 적음"))
            pick.score -= 6
        return pick

    # ---- 진화 한 개 평가 ----
    def _evo(self, e: FuserEvo, balls: List[InventorySlot], run: RunState) -> FusionPick:
        d = self.data
        result = e.item_id
        recipe = next(iter(d.recipes_for(result)), None) if result else None
        parts = recipe.ingredients if recipe else ()
        title = d.name(result)
        detail = (" + ".join(d.name(p) for p in parts) + " → 진화") if parts else "진화"
        pick = FusionPick("evo", title, detail, result, parts, score=20)
        if result is None:
            pick.warnings.append(Reason("evo_unknown", "결과 항목을 알 수 없음", -20, "미확인"))
            pick.score -= 20
            return pick
        pick.reasons.append(Reason("evo_base", "두 볼이 더 강한 한 볼이 되고 칸이 하나 빔", 20, "진화"))
        # 결과가 다음 진화 재료가 되는가
        owned = {s.item_id for s in balls}
        for r in d.recipes_using(result):
            others = [x for x in r.ingredients if x != result]
            if others and all(o in owned for o in others):
                pick.reasons.append(Reason("evo_chain", f"다음 진화 {d.name(r.result)}의 재료를 이미 보유", 8,
                                           f"{d.name(r.result)} 경로"))
                pick.score += 8
                break
        self._character_fit(pick, result, run)
        self._community(pick, result, run)
        # 게임 수치: 결과 볼의 1레벨 기본 피해와 재료들의 지금 피해 비교
        levels = {s.item_id: s.level for s in balls}
        res = d.damage_range(result, 1)
        mats = [d.damage_range(p_, levels.get(p_)) for p_ in parts]
        if res and mats and all(mats):
            mid = (res[0] + res[1]) / 2
            mat_mid = max((m[0] + m[1]) / 2 for m in mats)
            if mid > mat_mid:
                pick.reasons.append(Reason("evo_dmg_up", f"기본 피해 {res[0]}–{res[1]} (재료 중 최고 "
                                           f"{int(mat_mid)} 안팎보다 높음, 게임 수치)", 4, "피해 상승"))
                pick.score += 4
        # 이번 런 실측: 재료가 주력 볼이면 그 화력이 진화 볼로 이어진다
        share = sum(run.damage_share(p_) or 0 for p_ in parts)
        if share >= 0.3:
            pick.reasons.append(Reason("fz_run_share", f"재료가 이번 런 피해의 {round(share * 100)}%를 담당", 3,
                                       "주력 재료"))
            pick.score += 3
        self._record(pick, result)
        # 회복 수단 손실
        heal_owned = [s.item_id for s in balls if d.has_tag(s.item_id, "heal_source")]
        lost = [p for p in parts if d.has_tag(p, "heal_source")]
        if lost and len(heal_owned) <= len(lost) and not d.has_tag(result, "heal_source"):
            pick.warnings.append(Reason("evo_lose_heal", f"유일한 회복 수단({d.name(lost[0])})이 사라짐", -8, "회복 잃음"))
            pick.score -= 8
        return pick

    # ---- 융합 조합 한 개 평가 ----
    def _combo(self, c: FuserCombo, run: RunState) -> FusionPick:
        d = self.data
        title = f"{d.name(c.item1)} + {d.name(c.item2)}"
        pick = FusionPick("combo", title, "두 볼의 효과를 한 볼에", None, (c.item1, c.item2), score=10)
        pick.reasons.append(Reason("combo_base", "최대 레벨 볼 두 개를 합쳐 칸이 하나 빔", 10, "융합"))
        if c.ai_score is not None:
            # 게임 AI 점수는 크기 단위를 모르므로 순위 비교에만 쓴다 (0.1배 가중)
            pick.score += float(c.ai_score) * 0.1
            pick.reasons.append(Reason("combo_ai", "게임 자동 선택 AI가 높게 보는 조합", 0, "AI 추천"))
        if c.bad:
            pick.warnings.append(Reason("combo_bad", "게임이 나쁜 조합으로 판정", -30, "나쁜 조합"))
            pick.score -= 30
        for item in (c.item1, c.item2):
            if item:
                self._character_fit(pick, item, run)
        share = sum(run.damage_share(i) or 0 for i in (c.item1, c.item2) if i)
        if share >= 0.3:
            pick.reasons.append(Reason("fz_run_share", f"두 볼이 이번 런 피해의 {round(share * 100)}%를 담당 — 한 볼에 모음",
                                       round(share * 8), "주력 조합"))
            pick.score += round(share * 8)
        for item in (c.item1, c.item2):
            self._record(pick, item)
        return pick

    EVO_TIER_W = {"S": 4, "A": 2, "B": 0, "C": -1, "D": -2}

    def _community(self, pick: FusionPick, result: str, run: RunState):
        """커뮤니티 평가: 진화 결과의 티어(Game Rant)와 캐릭터 추천 빌드 핵심 항목 (의견이라 작게 — 동점일 때 가르는 정도)."""
        d = self.data
        tier = d.community_tier(result) if d.community else None
        if tier and self.EVO_TIER_W[tier]:
            w = self.EVO_TIER_W[tier]
            (pick.reasons if w > 0 else pick.warnings).append(
                Reason("fz_tier", f"커뮤니티 평가 {tier}티어 진화", w, f"{tier}티어"))
            pick.score += w
        for cid in run.character_ids:
            core, why = d.char_build_items(cid)
            if result in core:
                pick.reasons.append(Reason("fz_char_build", f"{d.name(cid)} 추천 빌드 핵심 (커뮤니티): {why}", 5,
                                           "캐릭터 추천 빌드"))
                pick.score += 5
                break

    def _character_fit(self, pick: FusionPick, item_id: str, run: RunState):
        d = self.data
        it = d.item(item_id)
        if it is None:
            return
        for cid in run.character_ids:
            rule = d.character_rule(cid)
            if [t for t in rule.get("reduces_tags", []) if d.has_tag(item_id, t)]:
                pick.warnings.append(Reason("char_reduces", f"{d.name(cid)}: {rule['reason']}", -12, "캐릭터와 안 맞음"))
                pick.score -= 12
            st_, dm_ = self.data.status_tags(it.id)
            if rule.get("boosts_wiki_status_or_aoe") and (st_ or "AOE" in dm_):
                pick.reasons.append(Reason("char_sisyphus_boost", f"{d.name(cid)}: 범위·상태 이상 피해 4배", 6,
                                           "캐릭터 연계"))
                pick.score += 6
