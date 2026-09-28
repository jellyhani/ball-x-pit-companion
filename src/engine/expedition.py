"""보스를 깬 뒤 '원정 계속'(무한의 심연) 또는 복귀 판단.

근거: 게임 연동 값(체력·최대 체력·부활 횟수·보유 볼의 최대 레벨 여부), 진화 레시피(보유 항목이 진화 결과물인지),
data/rules.json 의 회복 수단 태그. 무한의 심연은 적이 계속 강해지므로 덱 완성도와 체력 여유를 본다.
확인하지 못한 것: 원정 계속 중 쓰러졌을 때 이번 런에 모은 자원이 모두 남는지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..domain import RunProgress
from ..gamedata import GameData
from ..tracking.run_state import RunState
from ..i18n import tr


@dataclass
class ExpeditionAdvice:
    verdict: str  # continue | return | either
    headline: str
    reasons: List[str] = field(default_factory=list)
    cautions: List[str] = field(default_factory=list)
    best_depth: Optional[int] = None
    snapshot: dict = field(default_factory=dict)  # 기록용: 판단 당시 값


def personal_line(history: List[Tuple[float, int, int]], hr: Optional[float], evolved: int) -> Optional[str]:
    """내 기록: 비슷한 상황(체력 ±20%p, 진화 수 ±1)에서 원정을 계속했을 때 버틴 턴."""
    if hr is None or len(history) < 5:
        return None
    similar = [t for h, e, t in history if abs(h - hr) <= 0.2 and abs(e - evolved) <= 1]
    if len(similar) < 3:
        return None
    similar.sort()
    return tr(
        "내 기록: 비슷한 상황에서 계속했을 때 {v0}번 중 중간값 {v1}턴 버팀",
        v0=len(similar),
        v1=similar[len(similar) // 2],
    )


def advise(
    run_state: RunState,
    data: GameData,
    progress: Optional[RunProgress],
    best_depth: Optional[int] = None,
    history: Optional[List[Tuple[float, int, int]]] = None,
) -> ExpeditionAdvice:
    score = 0.0
    reasons: List[str] = []
    hr = progress.health_ratio if progress else None
    if hr is not None:
        percentage = round(hr * 100)
        if hr >= 0.6:
            score += 2
            reasons.append(tr("체력 {pct}% — 여유 있음", pct=percentage))
        elif hr < 0.3:
            score -= 3
            reasons.append(tr("체력 {pct}% — 위험", pct=percentage))
        else:
            reasons.append(tr("체력 {pct}%", pct=percentage))
    owned = run_state.owned
    heal = any(data.has_tag(index, "heal_source") for index in run_state.effect_ids)
    if heal:
        score += 1
        reasons.append(tr("회복 수단 보유"))
    if progress is not None and progress.revives_left:
        score += 1
        reasons.append(tr("부활 {revives_left}회 남음", revives_left=progress.revives_left))
    evolved = [index for index in owned if data.recipes_for(index)]
    maxed = [o for o in owned.values() if o.kind == "ball" and o.at_max]
    power = len(evolved) + 0.5 * len(maxed)
    if power >= 2:
        score += 2
        reasons.append(
            tr("진화 {v0}개 · 최대 레벨 볼 {v1}개 — 덱 완성도 높음", v0=len(evolved), v1=len(maxed))
        )
    elif power < 1:
        score -= 1
        reasons.append(tr("진화한 항목 없음 — 적이 계속 강해지는 구간에서 버티기 어려움"))
    else:
        reasons.append(tr("진화 {v0}개 · 최대 레벨 볼 {v1}개", v0=len(evolved), v1=len(maxed)))
    if score >= 3:
        verdict, headline = "continue", tr("원정 계속 추천")
    elif score <= -2:
        verdict, headline = "return", tr("복귀 추천")
    else:
        verdict, headline = "either", tr("계속해도 되지만 위험 부담 있음")
    mine = personal_line(history or [], hr, len(evolved))
    if mine:
        reasons.insert(0, mine)
    cautions = [
        tr("무한의 심연은 갈수록 적이 강해집니다"),
        tr("쓰러졌을 때 이번 런 자원이 모두 남는지는 확인하지 못했습니다"),
    ]
    adv = ExpeditionAdvice(verdict, headline, reasons, cautions, best_depth)
    adv.snapshot = {"verdict": verdict, "health": hr, "evolved": len(evolved), "maxed": len(maxed)}
    return adv
