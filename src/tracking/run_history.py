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
from ..i18n import tr
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
    result: str = "중단"  # 보스 격퇴 | 실패 | 중단
    turn: Optional[int] = None
    endless_turns: Optional[int] = None  # 원정 계속 후 버틴 턴
    balls: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    passives: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    picks: List[str] = field(default_factory=list)
    damage: Dict[str, int] = field(default_factory=dict)
    expedition: Optional[dict] = None  # 원정 계속 판단 당시 값 {verdict, health, evolved, maxed}

    @property
    def continued(self) -> Optional[bool]:
        """원정 계속을 눌렀는지 (판단 화면을 본 런만)."""
        if not self.expedition:
            return None
        return self.endless_turns is not None

    @property
    def top_ball(self) -> Optional[str]:
        balls = {index: value for index, value in self.damage.items() if index.startswith("ball:")}
        return max(balls, key=balls.get) if balls else None

    @property
    def owned_ids(self) -> List[str]:
        return [index for index, _ in self.balls + self.passives]


class RunRecorder:
    def __init__(self, path: str):
        self.path = path
        self.current: Optional[RunRecord] = None
        self._completed = False
        self._game_over = False

    def start(self, now: Optional[float] = None):
        self.current = RunRecord(started_at=now or time.time())
        self._completed = self._game_over = False

    def update(
        self,
        run_state: RunState,
        passive: Optional[RunProgress],
        completed: Optional[bool] = None,
        game_over: bool = False,
    ):
        record = self.current
        if record is None:
            return
        ids = run_state.character_ids
        record.char = ids[0] if ids else record.char
        record.extra_chars = ids[1:]
        if passive is not None:
            record.level = passive.level_name or record.level
            record.difficulty, record.ng_plus, record.turn = passive.difficulty, passive.ng_plus, passive.turn
            if passive.endless and passive.endless_start_turn is not None and passive.turn is not None:
                record.endless_turns = max(0, passive.turn - passive.endless_start_turn)
        record.balls = [(o.item_id, o.level) for o in run_state.owned.values() if o.kind == "ball"]
        record.passives = [(o.item_id, o.level) for o in run_state.owned.values() if o.kind == "passive"]
        record.picks = list(run_state.picks)
        if run_state.damage:
            record.damage = dict(run_state.damage)
        self._completed = self._completed or bool(completed)
        self._game_over = self._game_over or game_over

    def finish(self) -> Optional[RunRecord]:
        record = self.current
        self.current = None
        if record is None or not (record.picks or record.balls):
            return None  # 선택 한 번 없이 끝난 런은 남기지 않는다
        record.ended_at = time.time()
        record.result = "보스 격퇴" if self._completed else "실패" if self._game_over else "중단"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as file_handle:
                file_handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        except OSError:
            log.exception("런 기록 저장 실패")
        return record

    def load(self, limit: int = 200) -> List[RunRecord]:
        result: List[RunRecord] = []
        try:
            with open(self.path, encoding="utf-8") as file_handle:
                lines = file_handle.readlines()[-limit:]
        except OSError:
            return result
        for line in lines:
            try:
                raw = json.loads(line)
                raw["balls"] = [tuple(x) for x in raw.get("balls", [])]
                raw["passives"] = [tuple(x) for x in raw.get("passives", [])]
                result.append(RunRecord(**raw))
            except (ValueError, TypeError):
                continue
        return result


def item_summary(records: List[RunRecord]) -> Dict[str, Tuple[int, int]]:
    """항목별 (가진 런 수, 그중 보스 격퇴 수) — 내 기록 요약."""
    result: Dict[str, List[int]] = {}
    for record in records:
        for index in set(record.owned_ids):
            s = result.setdefault(index, [0, 0])
            s[0] += 1
            s[1] += record.result == "보스 격퇴"
    return {index: (a, ball) for index, (a, ball) in result.items()}


def expedition_history(records: List[RunRecord]) -> List[Tuple[float, int, int]]:
    """원정을 계속한 런의 (판단 때 체력 비율, 진화 수, 버틴 턴)."""
    result = []
    for record in records:
        e = record.expedition or {}
        if record.continued and e.get("health") is not None and record.endless_turns is not None:
            result.append((float(e["health"]), int(e.get("evolved") or 0), int(record.endless_turns)))
    return result


def result_label(result: str) -> str:
    """저장된 런 결과(한국어 고정값 — 비교에 쓰므로 번역해 저장하지 않는다)를 화면 언어로."""
    return {"보스 격퇴": tr("보스 격퇴"), "실패": tr("실패"), "중단": tr("중단")}.get(result, result)


def describe(record: RunRecord, data: GameData) -> Tuple[str, str]:
    """기록 목록 한 줄 (제목, 부제)."""
    when = time.strftime("%m-%d %H:%M", time.localtime(record.started_at))
    char = data.name(record.char) if record.char else tr("캐릭터 미확인")
    title = f"{when} · {char} · {result_label(record.result)}"
    parts = []
    if record.turn is not None:
        parts.append(tr("{turn}턴", turn=record.turn))
    if record.endless_turns:
        parts.append(tr("원정 계속 {endless_turns}턴", endless_turns=record.endless_turns))
    top = record.top_ball
    if top:
        total = sum(value for index, value in record.damage.items() if index.startswith("ball:")) or 1
        parts.append(tr("주력 {v0} {v1}%", v0=data.name(top), v1=round(record.damage[top] * 100 / total)))
    evo = [index for index in record.owned_ids if data.recipes_for(index)]
    if evo:
        parts.append(tr("진화 ") + ", ".join(data.name(index) for index in evo[:3]))
    return title, " · ".join(parts)
