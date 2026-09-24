"""스파(목욕탕) 재채집 손익: 골드를 내고 바로 한 번 더 채집할 만한지.

비용은 게임 값(플러그인 1.10 base.spa.cost — BuildingMgr.GetMasseuseCost, 쓸수록 오름), 얻는 양은 내 채집 기록
(harvest.jsonl 의 실제 증가량, 같은 배치 우선 최근 5번 평균). 커뮤니티 정석: 채집으로 얻는 골드가 스파 비용보다
크면 되풀이(Harvest Loop), 아니면 부족한 자원이 급할 때만.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..tracking.meta_state import RESOURCES


@dataclass
class SpaAdvice:
    cost: int
    avg_gain: List[float]         # [골드, 밀, 나무, 돌] 평균
    samples: int
    verdict: str                  # profit | resources | loss | unknown
    text: str
    tone: str                     # ok | warn | neutral


def spa_advice(spa: Optional[dict], rows: Sequence[dict], layout: str = "",
               shortfalls: Optional[Dict[str, int]] = None) -> Optional[SpaAdvice]:
    if not spa or not spa.get("built") or not isinstance(spa.get("cost"), int):
        return None
    cost = int(spa["cost"])
    good = [r for r in rows if isinstance(r.get("gain"), list) and len(r["gain"]) >= 4]
    same = [r for r in good if r.get("layout") == layout]
    use = (same or good)[-5:]
    if not use:
        return SpaAdvice(cost, [0, 0, 0, 0], 0, "unknown",
                         f"스파 재채집 비용 {cost:,}골드 — 채집 기록이 쌓이면 손익을 계산합니다", "neutral")
    avg = [sum(max(0, r["gain"][i]) for r in use) / len(use) for i in range(4)]
    net = avg[0] - cost
    res = " · ".join(f"{RESOURCES[i]} +{avg[i]:.0f}" for i in (1, 2, 3) if avg[i] >= 0.5)
    basis = f"최근 채집 {len(use)}번 평균{' (같은 배치)' if same else ''}"
    need = [k for k, v in (shortfalls or {}).items() if v > 0 and k in RESOURCES[1:]]
    if net > 0:
        return SpaAdvice(cost, avg, len(use), "profit",
                         f"스파 재채집 이득: 비용 {cost:,}골드 < 골드 +{avg[0]:,.0f} ({basis}) · 순이익 {net:,.0f}"
                         + (f" · 덤 {res}" if res else ""), "ok")
    if res and need:
        return SpaAdvice(cost, avg, len(use), "resources",
                         f"스파 재채집: 골드 {-net:,.0f} 손해지만 부족한 {'·'.join(need)} 필요하면 ({res}, {basis})", "warn")
    return SpaAdvice(cost, avg, len(use), "loss",
                     f"스파 재채집 손해: 비용 {cost:,}골드 > 골드 +{avg[0]:,.0f}" + (f", {res}" if res else "")
                     + f" ({basis})", "neutral")
