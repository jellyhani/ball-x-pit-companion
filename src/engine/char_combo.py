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

SRC_W = 4.0          # 추천한 곳 하나당
RECORD_MIN = 2       # 내 기록은 이 조합으로 2번 이상 했을 때만
LEVEL_W = 0.25       # 두 캐릭터 레벨 합 1당


@dataclass
class CharPair:
    a: str
    b: str
    score: float
    reasons: List[str] = field(default_factory=list)


def char_id(game_type: str) -> str:
    """게임 캐릭터 타입(kRecaller) → 데이터 ID (char:recaller)."""
    return "char:" + (game_type[1:] if game_type.startswith("k") else game_type).lower()


def suggest_pairs(data: GameData, chars: Sequence[dict], history: Sequence = (), limit: int = 3) -> List[CharPair]:
    """해금된 캐릭터(meta.chars — {type, lvl})로 만들 수 있는 조합 중 추천 순서."""
    levels: Dict[str, int] = {}
    for c in chars:
        t = c.get("type")
        if t:
            cid = char_id(t)
            if cid in data.characters:
                levels[cid] = int(c.get("lvl") or 0)
    pairs = {frozenset(p["pair"]): p for p in (data.community.get("char_pairs") or []) if len(p.get("pair", [])) == 2}
    out: List[CharPair] = []
    for a, b in combinations(sorted(levels), 2):
        key = frozenset((a, b))
        score, why = 0.0, []
        cp = pairs.get(key)
        if cp:
            n = len(cp.get("src") or [1])
            score += SRC_W * n
            why.append(f"커뮤니티 추천 {n}곳: {cp.get('why', '')}")
        rec = _record(history, a, b)
        if rec is not None:
            rate, n = rec
            score += (rate - 0.5) * 8
            why.append(f"내 기록: 보스 격퇴 {round(rate * 100)}% ({n}번)")
        fa = data.character_rule(a).get("strategy", {}).get("favor", {})
        fb = data.character_rule(b).get("strategy", {}).get("favor", {})
        shared = [k for k in fa if fa[k] > 0 and fb.get(k, 0) > 0]
        if shared:
            score += 1.0
        score += LEVEL_W * (levels[a] + levels[b])
        if cp or rec is not None:
            out.append(CharPair(a, b, score, why))
    out.sort(key=lambda p: -p.score)
    return out[:limit]


def _record(history: Sequence, a: str, b: str) -> Optional[tuple]:
    runs = [h for h in history if getattr(h, "result", "중단") != "중단"
            and {getattr(h, "char", None), *(getattr(h, "extra_chars", None) or [])} >= {a, b}]
    if len(runs) < RECORD_MIN:
        return None
    return sum(h.result == "보스 격퇴" for h in runs) / len(runs), len(runs)
