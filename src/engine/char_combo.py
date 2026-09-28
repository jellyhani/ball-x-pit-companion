"""캐릭터 조합 추천 (알선소로 두 캐릭터를 한 게임에 데려갈 때).

근거 (모두 의견·기록이라 점수는 순위를 가르는 정도로만 씀)
- 커뮤니티 추천 조합: data/community.json char_pairs (Dexerto·Screen Rant·Steam 토론). 여러 곳이 추천할수록 높게.
- 내 기록: 이 두 캐릭터로 한 런의 보스 격퇴율 (2번 이상일 때).
- 캐릭터 궁합 프로필(rules.json strategy.favor)이 같은 방향이면 조금 더 (예: 범위 피해 + 범위 피해 4배).
- 캐릭터 레벨: 레벨이 높으면 능력치가 높다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Sequence

from ..gamedata import GameData
from ..i18n import tr

SRC_W = 4.0  # 추천한 곳 하나당
RECORD_MIN = 2  # 내 기록은 이 조합으로 2번 이상 했을 때만
LEVEL_W = 0.25  # 두 캐릭터 레벨 합 1당


@dataclass
class CharPair:
    a: str
    b: str
    score: float
    reasons: List[str] = field(default_factory=list)


def char_id(game_type: str) -> str:
    """게임 캐릭터 타입(kRecaller) → 데이터 ID (char:recaller)."""
    return "char:" + (game_type[1:] if game_type.startswith("k") else game_type).lower()


def suggest_pairs(
    data: GameData,
    characters: Sequence[dict],
    history: Sequence = (),
    limit: int = 3,
    fixed: Optional[str] = None,
    exact: Optional[Sequence[str]] = None,
) -> List[CharPair]:
    """해금된 캐릭터(meta.chars — {type, lvl})로 만들 수 있는 조합 중 추천 순서.

    fixed: 이 캐릭터가 들어간 조합만 (알선소에서 하나를 이미 고르고 둘째를 고르는 중일 때).
    exact: 이 두 캐릭터의 조합 하나만 — 근거가 하나도 없어도 돌려준다 (둘 다 이미 골랐을 때 '궁합' 표시용).
    """
    levels: Dict[str, int] = {}
    for c in characters:
        t = c.get("type")
        if t:
            character_id = char_id(t)
            if character_id in data.characters:
                levels[character_id] = int(c.get("lvl") or 0)
    exact_key = frozenset(exact) if exact else None
    pairs = {
        frozenset(p["pair"]): p
        for p in (data.community.get("char_pairs") or [])
        if len(p.get("pair", [])) == 2
    }
    result: List[CharPair] = []
    for a, b in combinations(sorted(levels), 2):
        key = frozenset((a, b))
        if exact_key is not None and key != exact_key:
            continue
        if fixed is not None and fixed not in (a, b):
            continue
        score, why = 0.0, []
        cp = pairs.get(key)
        if cp:
            item_count = len(cp.get("src") or [1])
            score += SRC_W * item_count
            why.append(tr("커뮤니티 추천 {n}곳: {v0}", n=item_count, v0=tr(cp.get("why", ""))))
        record = _record(history, a, b)
        if record is not None:
            rate, item_count = record
            score += (rate - 0.5) * 8
            why.append(tr("내 기록: 보스 격퇴 {v0}% ({n}번)", v0=round(rate * 100), n=item_count))
        fa = data.character_rule(a).get("strategy", {}).get("favor", {})
        fb = data.character_rule(b).get("strategy", {}).get("favor", {})
        shared = [k for k in fa if fa[k] > 0 and fb.get(k, 0) > 0]
        if shared:
            score += 1.0
            why.append(tr("둘 다 {v0} 전략 선호", v0="·".join(shared)))
        score += LEVEL_W * (levels[a] + levels[b])
        if exact_key is not None or cp or record is not None:
            result.append(CharPair(a, b, score, why))
    result.sort(key=lambda p: -p.score)
    return result[:limit]


def _record(history: Sequence, a: str, b: str) -> Optional[tuple]:
    runs = [
        h
        for h in history
        if getattr(h, "result", "중단") != "중단"
        and {getattr(h, "char", None), *(getattr(h, "extra_chars", None) or [])} >= {a, b}
    ]
    if len(runs) < RECORD_MIN:
        return None
    return sum(h.result == "보스 격퇴" for h in runs) / len(runs), len(runs)
