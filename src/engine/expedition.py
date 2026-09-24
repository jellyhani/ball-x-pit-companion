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


@dataclass
class ExpeditionAdvice:
    verdict: str                  # continue | return | either
    headline: str
    reasons: List[str] = field(default_factory=list)
    cautions: List[str] = field(default_factory=list)
    best_depth: Optional[int] = None
    snapshot: dict = field(default_factory=dict)    # 기록용: 판단 당시 값


def personal_line(history: List[Tuple[float, int, int]], hr: Optional[float], evolved: int) -> Optional[str]:
    """내 기록: 비슷한 상황(체력 ±20%p, 진화 수 ±1)에서 원정을 계속했을 때 버틴 턴."""
    if hr is None or len(history) < 5:
        return None
    similar = [t for h, e, t in history if abs(h - hr) <= 0.2 and abs(e - evolved) <= 1]
    if len(similar) < 3:
        return None
    similar.sort()
    return f"내 기록: 비슷한 상황에서 계속했을 때 {len(similar)}번 중 중간값 {similar[len(similar) // 2]}턴 버팀"


def advise(run: RunState, data: GameData, p: Optional[RunProgress], best_depth: Optional[int] = None,
           history: Optional[List[Tuple[float, int, int]]] = None) -> ExpeditionAdvice:
    score = 0.0
    reasons: List[str] = []
    hr = p.health_ratio if p else None
    if hr is not None:
        pct = round(hr * 100)
        if hr >= 0.6:
            score += 2
            reasons.append(f"체력 {pct}% — 여유 있음")
        elif hr < 0.3:
            score -= 3
            reasons.append(f"체력 {pct}% — 위험")
        else:
            reasons.append(f"체력 {pct}%")
    owned = run.owned
    heal = any(data.has_tag(i, "heal_source") for i in owned)
    if heal:
        score += 1
        reasons.append("회복 수단 보유")
    if p is not None and p.revives_left:
        score += 1
        reasons.append(f"부활 {p.revives_left}회 남음")
    evolved = [i for i in owned if data.recipes_for(i)]
    maxed = [o for o in owned.values() if o.kind == "ball" and o.at_max]
    power = len(evolved) + 0.5 * len(maxed)
    if power >= 2:
        score += 2
        reasons.append(f"진화 {len(evolved)}개 · 최대 레벨 볼 {len(maxed)}개 — 덱 완성도 높음")
    elif power < 1:
        score -= 1
        reasons.append("진화한 항목 없음 — 적이 계속 강해지는 구간에서 버티기 어려움")
    else:
        reasons.append(f"진화 {len(evolved)}개 · 최대 레벨 볼 {len(maxed)}개")
    if score >= 3:
        verdict, headline = "continue", "원정 계속 추천"
    elif score <= -2:
        verdict, headline = "return", "복귀 추천"
    else:
        verdict, headline = "either", "계속해도 되지만 위험 부담 있음"
    mine = personal_line(history or [], hr, len(evolved))
    if mine:
        reasons.insert(0, mine)
    cautions = ["무한의 심연은 갈수록 적이 강해집니다",
                "쓰러졌을 때 이번 런 자원이 모두 남는지는 확인하지 못했습니다"]
    adv = ExpeditionAdvice(verdict, headline, reasons, cautions, best_depth)
    adv.snapshot = {"verdict": verdict, "health": hr, "evolved": len(evolved), "maxed": len(maxed)}
    return adv
