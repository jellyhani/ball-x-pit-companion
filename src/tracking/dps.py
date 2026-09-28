"""실시간 초당 피해(DPS): 게임 연동의 누적 피해(볼별 게임 통계)를 최근 N초 창으로 나눈다.

시간은 게임 전투 시간(BattleSaveData.ElapsedTime)을 쓴다. 일시정지·배속과 상관없이 '게임 1초당 피해'가 된다.
전투 시간이 없으면(옛 플러그인) 스냅샷의 실시간 시각으로 대신한다.
진화·융합으로 볼 종류가 바뀌면 새 볼은 처음 본 시점부터 센다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DpsRow:
    item_id: str
    dps: float
    share: float  # 이번 창 전체 피해 중 비율


class DpsTracker:
    def __init__(self, window_s: float = 10.0):
        self.window_s = window_s
        self._samples: Deque[Tuple[float, Dict[str, int]]] = deque()

    def reset(self):
        self._samples.clear()

    def add(self, t: float, damage: Dict[str, int]):
        if self._samples and t < self._samples[-1][0]:
            self.reset()  # 시간이 되돌아감 = 새 런
        if self._samples and t == self._samples[-1][0]:
            self._samples[-1] = (t, dict(damage))
        else:
            self._samples.append((t, dict(damage)))
        # 창보다 오래된 표본은 하나만 남긴다 (창 시작점 기준)
        while len(self._samples) > 2 and self._samples[1][0] <= t - self.window_s:
            self._samples.popleft()

    def rows(self, kind_prefix: tuple = ("ball:", "baby:")) -> Tuple[List[DpsRow], float]:
        """(항목별 DPS 큰 순, 전체 DPS). 표본이 부족하면 빈 목록."""
        if len(self._samples) < 2:
            return [], 0.0
        t1, now = self._samples[-1]
        rates: Dict[str, float] = {}
        for item, value in now.items():
            if not item.startswith(kind_prefix):
                continue
            # 이 항목이 처음 나타난 표본(창 안)부터 잰다
            first: Optional[Tuple[float, int]] = None
            for t0, old in self._samples:
                if item in old and t0 < t1:
                    first = (t0, old[item])
                    break
            if first is None:
                continue
            dt = t1 - first[0]
            if dt <= 0.5:
                continue
            rates[item] = max(0.0, (value - first[1]) / dt)
        total = sum(rates.values())
        rows = [DpsRow(index, r, r / total if total else 0.0) for index, r in rates.items()]
        rows.sort(key=lambda r: -r.dps)
        return rows, total
