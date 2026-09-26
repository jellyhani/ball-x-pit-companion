"""선택지 뽑기 관측: 게임이 알려 준 후보 목록과 실제로 나온 카드를 기록해, 종류별 뽑기 가중치를 추정한다.

종류는 새 볼 / 볼 강화 / 새 패시브 / 패시브 강화 네 가지. 관측 한 번 = 선택창 한 번.
후보 = 게임 후보 목록 + 지금 화면의 카드 − 직전 선택지. 게임 후보 목록에는 화면에 나온 카드가 빠져 있다(실제 게임 확인).
가중치 = 실제로 나온 비율 ÷ 후보를 고르게 뽑았을 때의 기대 비율. 관측이 적으면 쓰지 않는다(균등 가정 유지).
기록은 이 PC에만 (draws.jsonl).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from ..domain import ChoiceSession
from ..i18n import tr

log = logging.getLogger(__name__)

CATS = ("new_ball", "ball_up", "new_passive", "passive_up")
CAT_LABEL = {"new_ball": tr("새 볼"), "ball_up": tr("볼 강화"), "new_passive": tr("새 패시브"), "passive_up": tr("패시브 강화")}
MIN_EXPECTED = 30.0     # 기대 장수 합이 이만큼은 돼야 가중치를 쓴다


def category(item_id: str, upgrade: bool) -> Optional[str]:
    if item_id.startswith("ball:"):
        return "ball_up" if upgrade else "new_ball"
    if item_id.startswith("passive:"):
        return "passive_up" if upgrade else "new_passive"
    return None


def observe(s: ChoiceSession) -> Optional[dict]:
    """선택창 하나를 관측 기록으로 바꾼다. 후보 목록이 없으면 None."""
    pool = s.pool
    if pool is None:
        return None
    prev = set(pool.prev)
    cand: Dict[str, int] = {c: 0 for c in CATS}
    for iid, up in pool.entries():
        c = category(iid, up)
        if c and iid not in prev:
            cand[c] += 1
    shown: List[str] = []
    for card in s.cards:
        if card.item_id and card.label is not None:
            c = category(card.item_id, card.label.is_upgrade)
            if c:
                shown.append(c)
                cand[c] += 1      # 게임 후보 목록은 화면에 나온 카드를 이미 뺀 상태다 (실제 게임 확인) → 뽑기 전 후보로 되돌린다
    if not shown or not sum(cand.values()):
        return None
    return {"t": time.time(), "cand": cand, "shown": shown}


class DrawStats:
    def __init__(self, path: str):
        self.path = path
        self.rows: List[dict] = []
        self._seen_sessions = set()
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                self.rows = [json.loads(x) for x in f if x.strip()][-5000:]
        except (OSError, ValueError):
            self.rows = []

    def record(self, s: ChoiceSession):
        row = observe(s)
        if row is None:
            return
        if s.session_id in self._seen_sessions:
            return      # 같은 선택창을 다시 잡은 경우
        self._seen_sessions.add(s.session_id)
        self.rows.append(row)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            log.exception("뽑기 관측 저장 실패")

    def totals(self) -> Tuple[Dict[str, int], Dict[str, float]]:
        """(종류별 실제로 나온 장수, 균등 가정 기대 장수)."""
        obs = {c: 0 for c in CATS}
        exp = {c: 0.0 for c in CATS}
        for r in self.rows:
            cand = r.get("cand") or {}
            total = sum(cand.values())
            n = len(r.get("shown") or [])
            if not total or not n:
                continue
            for c in CATS:
                exp[c] += n * cand.get(c, 0) / total
            for c in r.get("shown") or []:
                if c in obs:
                    obs[c] += 1
        return obs, exp

    def weights(self) -> Optional[Dict[str, float]]:
        """종류별 뽑기 가중치 (균등 = 1). 관측이 적으면 None."""
        obs, exp = self.totals()
        if sum(exp.values()) < MIN_EXPECTED:
            return None
        return {c: (max(0.2, min(5.0, obs[c] / exp[c])) if exp[c] >= 3 else 1.0) for c in CATS}

    @property
    def count(self) -> int:
        return len(self.rows)

    def summary(self) -> str:
        w = self.weights()
        if w is None:
            obs, exp = self.totals()
            return tr("뽑기 관측 {count}회 — 보정까지 더 필요 (기대 장수 {v0:.0f}/{MIN_EXPECTED:.0f})", count=self.count, v0=sum(exp.values()), MIN_EXPECTED=MIN_EXPECTED)
        return tr("뽑기 관측 {count}회 · ", count=self.count) + " · ".join(f"{CAT_LABEL[c]} ×{w[c]:.2f}" for c in CATS)
