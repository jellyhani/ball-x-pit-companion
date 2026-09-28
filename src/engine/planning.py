"""런 밖 계획: 다음 런 캐릭터, 설계도 파밍 지역 (게임 연동 1.3 meta 의 지역별 값).

- 캐릭터: 해금된 지역마다 아직 그 지역을 깨지 않은 캐릭터를 보여 준다(게임의 캐릭터별 완료 기록).
  순서는 내 런 기록의 보스 격퇴율, 캐릭터 레벨. 캐릭터 강함 자체는 비교하지 않는다.
- 설계도: 지역마다 아직 얻지 못한 설계도 수 (게임의 지역별 설계도 목록 − 보유 설계도). 많은 곳부터.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ..gamedata import GameData
from ..tracking.meta_state import MetaState
from ..tracking.run_history import RunRecord


# 게임 CharType 순서 (지역별 캐릭터 기록 배열의 위치)
CHAR_TYPES = (
    "kDefault",
    "kRecaller",
    "kItchyFinger",
    "kTunneller",
    "kTiptoer",
    "kCogitator",
    "kTactician",
    "kSpendthrift",
    "kEmbedded",
    "kRadicalAI",
    "kEmptyNester",
    "kShade",
    "kCohabitants",
    "kPhysicist",
    "kBrickHead",
    "kSisyphus",
    "kFlagellant",
    "kWimp",
    "kPackRat",
    "kFalconer",
    "kCarouser",
    "kBackpacker",
    "kInfluencer",
)


def level_name(lv: dict) -> str:
    name = lv.get("name") or lv.get("type", "")
    name = re.sub(r"\{\[\w+\]\}|<[^>]+>", " ", name)
    return re.sub(r"\s+", " ", name).strip() or lv.get("type", "")


@dataclass
class LevelPlan:
    level: str  # 게임 내부 이름
    name: str
    chars_left: List[str]  # 아직 이 지역을 깨지 않은 해금 캐릭터 (추천 순)
    chars_done: int
    blueprints_left: List[str]  # 건물 내부 이름


def char_win_rates(records: List[RunRecord]) -> Dict[str, Tuple[int, int]]:
    result: Dict[str, List[int]] = {}
    for r in records:
        if r.char:
            s = result.setdefault(r.char, [0, 0])
            s[0] += 1
            s[1] += r.result == "보스 격퇴"
    return {c: (a, b) for c, (a, b) in result.items()}


def plan_levels(meta: MetaState, data: GameData, records: List[RunRecord]) -> List[LevelPlan]:
    rates = char_win_rates(records)
    lvl = dict(meta.chars)
    result = []
    for lv in meta.levels:
        if lv.get("unlocked") is False:
            continue
        done = set()
        best = lv.get("best_diff_by_char")
        if isinstance(best, list):
            # 실제 게임 확인: 값이 1 이상이면 그 캐릭터로 이 지역을 깼다 (지역 선택 화면의 체크 표시와 일치)
            for index, value in enumerate(best):
                if isinstance(value, int) and value > 0 and index < len(CHAR_TYPES):
                    done.add(f"char:{CHAR_TYPES[index][1:].lower()}")
        for t in [] if isinstance(best, list) else lv.get("chars_done") or []:
            character_id = f"char:{t[1:].lower()}" if t.startswith("k") else None
            if character_id:
                done.add(character_id)
        left = [c for c in lvl if c not in done]

        def key(c):
            n, w = rates.get(c, (0, 0))
            return (-(w / n) if n else 0.0, -lvl.get(c, 0), data.name(c))

        left.sort(key=key)
        result.append(
            LevelPlan(
                lv.get("type", ""), level_name(lv), left, len(done), list(lv.get("blueprints_left") or [])
            )
        )
    return result


def blueprint_targets(plans: List[LevelPlan]) -> List[LevelPlan]:
    return sorted((p for p in plans if p.blueprints_left), key=lambda p: -len(p.blueprints_left))
