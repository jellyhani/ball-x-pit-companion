"""게임 설명문 틀의 자리표시자({[reduce_speed_pct]})를 게임 연동으로 받은 레벨별 수치로 채운다.

게임 파일(I2Loc)의 설명은 틀만 있고 값은 게임이 실행 중에 넣는다 — 추출할 때 위키 값이 없으면 '?' 로 남던 것을,
플러그인이 보내 준 레벨별 수치(kReduceSpeedPct 등)로 '레벨1/레벨2/레벨3' 처럼 채운다.
자리표시자 이름과 수치 이름은 대부분 같지만 낱말이 더 끼는 경우가 있다 (damage_pct → kBonusDamagePct,
min_spawn_cycle → kMinBlockSpawnCycle, lightning_rod_length → kLightningRodCycleLen) → 낱말 순서로 맞춘다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

PLACEHOLDER = re.compile(r"\{\[(\w+)\]\}")
RANGE = re.compile(r"\{\[(\w+)\]\}\s*[-~]\s*\{\[(\w+)\]\}")  # 최소-최대 한 쌍 → 레벨마다 '4-8/7-11'
_COMPOUND = {("life", "steal"): "lifesteal"}  # 게임 수치 이름은 LifeSteal, 틀은 lifesteal
_NORM = {"length": "len"}
_PCT_UNITS = ("pct", "chance")  # 이 낱말로 끝나면 퍼센트 값
# 게임 내부 값이 0.1% 단위인 수치 (위키 대조: 명사수의 십자가 600 → 60%, 흡혈 45 → 4.5%, 영혼 흡입자 300 → 30%,
# 매혹 40 → 4%). 무리어미 생성·광란·질병·사신·좀비·치명타 즉사·가시돋친 껍데기 확률은 그대로 % (위키 대조).
PERMILLE = {"kCritChance", "kLifeStealChance", "kCharmChance"}


def _words_ph(name: str) -> List[str]:
    return [_NORM.get(w, w) for w in name.lower().split("_") if w]


def _words_key(key: str) -> List[str]:
    body = key[1:] if key.startswith("k") and key[1:2].isupper() else key
    # AOEDmgPct → AOE, Dmg, Pct (대문자 약어를 한 낱말로)
    words = [w.lower() for w in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", body)]
    result: List[str] = []
    for w in words:
        if result and (result[-1], w) in _COMPOUND:
            result[-1] = _COMPOUND[(result[-1], w)]
        else:
            result.append(_NORM.get(w, w))
    return result


def _subseq(small: Sequence[str], big: Sequence[str]) -> bool:
    it = iter(big)
    return all(w in it for w in small)


def match_key(placeholder: str, keys: Sequence[str]) -> Optional[object]:
    """자리표시자에 맞는 수치 이름. 하나면 str, 최소·최대 짝이면 (min, max), 못 찾거나 애매하면 None."""
    ph = _words_ph(placeholder)
    kw = {k: _words_key(k) for k in keys}
    for words in (ph, [w for w in ph if w not in ("min", "max")]):  # max_venom_stacks → kVenomStacks
        if not words:
            continue
        cand = [field_name for field_name, w in kw.items() if _subseq(words, w)]
        if not cand:
            continue
        exact = [k for k in cand if kw[k] == words]
        if len(exact) == 1:
            return exact[0]
        if not ({"min", "max"} & set(words)):
            plain = [k for k in cand if not ({"min", "max"} & set(kw[k]))]
            if len(plain) == 1:
                return plain[0]
            if not plain:
                mins = [k for k in cand if "min" in kw[k]]
                maxs = [k for k in cand if "max" in kw[k]]
                if len(mins) == 1 and len(maxs) == 1:
                    return (mins[0], maxs[0])
            cand = plain or cand
        if len(cand) == 1:
            return cand[0]
        fewest = min(len(kw[k]) for k in cand)
        best = [k for k in cand if len(kw[k]) == fewest]
        if len(best) == 1:
            return best[0]
        return None
    return None


def _num(v) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:g}"
    return str(int(v)) if isinstance(v, (int, float)) else str(v)


def _values(rows: Sequence[Dict[str, int]], key) -> Optional[List[str]]:
    result = []
    for r in rows:
        if isinstance(key, tuple):
            lo, hi = r.get(key[0]), r.get(key[1])
            if lo is None or hi is None:
                return None
            result.append(f"{_num(lo)}~{_num(hi)}")
        else:
            if key not in r:
                return None
            value = r[key]
            if key in PERMILLE or key.endswith("Tenth"):
                value = value / 10
            result.append(_num(value))
    return result


def fill(template: str, level_props: Optional[Sequence[Dict[str, int]]], levels: int = 3) -> Optional[str]:
    """틀을 채운 설명. 레벨마다 값이 다르면 '1/2/3', 같으면 한 번만. 채울 수 없는 자리가 있으면 '?' 로 두고,
    틀·수치가 없으면 None (호출한 쪽이 추출 때의 설명을 그대로 씀)."""
    if not template or not level_props:
        return None
    rows = list(level_props[: max(1, levels)])
    keys = sorted({k for r in rows for k in r})

    def repl(m: "re.Match") -> str:
        name = m.group(1)
        key = match_key(name, keys)
        vals = _values(rows, key) if key is not None else None
        if not vals:
            return "?"
        text = vals[0] if len(set(vals)) == 1 else "/".join(vals)
        after = m.string[m.end() : m.end() + 1]
        if name.endswith(_PCT_UNITS) and not isinstance(key, tuple) and after != "%":
            text += "%"
        return text

    def repl_range(m: "re.Match") -> str:
        a, b = match_key(m.group(1), keys), match_key(m.group(2), keys)
        va = _values(rows, a) if isinstance(a, str) else None
        vb = _values(rows, b) if isinstance(b, str) else None
        if not va or not vb:
            return PLACEHOLDER.sub(repl, m.group(0))
        per = [f"{x}-{y}" for x, y in zip(va, vb)]
        return per[0] if len(set(per)) == 1 else "/".join(per)

    return PLACEHOLDER.sub(repl, RANGE.sub(repl_range, template))
