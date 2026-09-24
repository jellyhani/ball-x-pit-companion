"""런 기록: 매 판의 캐릭터·선택·결과·볼별 피해를 이 PC에만 저장한다 (runs.jsonl, 한 줄 = 한 판).

게임 연동이 있을 때만 기록한다. 결과는 게임 값으로 정한다:
  보스 격퇴 = CompletedLevel, 실패 = 격퇴 없이 런 종료 화면, 중단 = 그 밖(기지로 나감 등).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from ..domain import RunProgress
from ..gamedata import GameData
from .run_state import RunState

log = logging.getLogger(__name__)


@dataclass
class RunRecord:
    started_at: float
    ended_at: float = 0.0
    char: Optional[str] = None
    extra_chars: List[str] = field(default_factory=list)
    level: str = ""
    difficulty: Optional[int] = None
    ng_plus: Optional[int] = None
    result: str = "중단"                  # 보스 격퇴 | 실패 | 중단
    turn: Optional[int] = None
    endless_turns: Optional[int] = None   # 원정 계속 후 버틴 턴
    balls: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    passives: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    picks: List[str] = field(default_factory=list)
    damage: Dict[str, int] = field(default_factory=dict)
    expedition: Optional[dict] = None     # 원정 계속 판단 당시 값 {verdict, health, evolved, maxed}

    @property
    def continued(self) -> Optional[bool]:
        """원정 계속을 눌렀는지 (판단 화면을 본 런만)."""
        if not self.expedition:
            return None
        return self.endless_turns is not None

    @property
    def top_ball(self) -> Optional[str]:
        balls = {i: v for i, v in self.damage.items() if i.startswith("ball:")}
        return max(balls, key=balls.get) if balls else None

    @property
    def owned_ids(self) -> List[str]:
        return [i for i, _ in self.balls + self.passives]


class RunRecorder:
    def __init__(self, path: str):
        self.path = path
        self.current: Optional[RunRecord] = None
        self._completed = False
        self._game_over = False

    def start(self, now: Optional[float] = None):
        self.current = RunRecord(started_at=now or time.time())
        self._completed = self._game_over = False

    def update(self, run: RunState, p: Optional[RunProgress], completed: Optional[bool] = None,
               game_over: bool = False):
        rec = self.current
        if rec is None:
            return
        ids = run.character_ids
        rec.char = ids[0] if ids else rec.char
        rec.extra_chars = ids[1:]
        if p is not None:
            rec.level = p.level_name or rec.level
            rec.difficulty, rec.ng_plus, rec.turn = p.difficulty, p.ng_plus, p.turn
            if p.endless and p.endless_start_turn is not None and p.turn is not None:
                rec.endless_turns = max(0, p.turn - p.endless_start_turn)
        rec.balls = [(o.item_id, o.level) for o in run.owned.values() if o.kind == "ball"]
        rec.passives = [(o.item_id, o.level) for o in run.owned.values() if o.kind == "passive"]
        rec.picks = list(run.picks)
        if run.damage:
            rec.damage = dict(run.damage)
        self._completed = self._completed or bool(completed)
        self._game_over = self._game_over or game_over

    def finish(self) -> Optional[RunRecord]:
        rec = self.current
        self.current = None
        if rec is None or not (rec.picks or rec.balls):
            return None     # 선택 한 번 없이 끝난 런은 남기지 않는다
        rec.ended_at = time.time()
        rec.result = "보스 격퇴" if self._completed else "실패" if self._game_over else "중단"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        except OSError:
            log.exception("런 기록 저장 실패")
        return rec

    def load(self, limit: int = 200) -> List[RunRecord]:
        out: List[RunRecord] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return out
        for line in lines:
            try:
                raw = json.loads(line)
                raw["balls"] = [tuple(x) for x in raw.get("balls", [])]
                raw["passives"] = [tuple(x) for x in raw.get("passives", [])]
                out.append(RunRecord(**raw))
            except (ValueError, TypeError):
                continue
        return out


def item_summary(records: List[RunRecord]) -> Dict[str, Tuple[int, int]]:
    """항목별 (가진 런 수, 그중 보스 격퇴 수) — 내 기록 요약."""
    out: Dict[str, List[int]] = {}
    for r in records:
        for i in set(r.owned_ids):
            s = out.setdefault(i, [0, 0])
            s[0] += 1
            s[1] += r.result == "보스 격퇴"
    return {i: (a, b) for i, (a, b) in out.items()}


def expedition_history(records: List[RunRecord]) -> List[Tuple[float, int, int]]:
    """원정을 계속한 런의 (판단 때 체력 비율, 진화 수, 버틴 턴)."""
    out = []
    for r in records:
        e = r.expedition or {}
        if r.continued and e.get("health") is not None and r.endless_turns is not None:
            out.append((float(e["health"]), int(e.get("evolved") or 0), int(r.endless_turns)))
    return out


def describe(rec: RunRecord, data: GameData) -> Tuple[str, str]:
    """기록 목록 한 줄 (제목, 부제)."""
    when = time.strftime("%m-%d %H:%M", time.localtime(rec.started_at))
    char = data.name(rec.char) if rec.char else "캐릭터 미확인"
    title = f"{when} · {char} · {rec.result}"
    parts = []
    if rec.turn is not None:
        parts.append(f"{rec.turn}턴")
    if rec.endless_turns:
        parts.append(f"원정 계속 {rec.endless_turns}턴")
    top = rec.top_ball
    if top:
        total = sum(v for i, v in rec.damage.items() if i.startswith("ball:")) or 1
        parts.append(f"주력 {data.name(top)} {round(rec.damage[top] * 100 / total)}%")
    evo = [i for i in rec.owned_ids if data.recipes_for(i)]
    if evo:
        parts.append("진화 " + ", ".join(data.name(i) for i in evo[:3]))
    return title, " · ".join(parts)
