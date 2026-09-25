"""패시브(선택창 아래 줄 — 게임은 볼·패시브 칸을 합쳐 '장비 슬롯'이라 부른다)의 레벨별 실제 효과.

근거: 게임 연동 catalog 의 레벨별 수치(lvl_props, 레벨 1부터 순서). 예) 흉갑 kReduceDmgPct 10/20/30,
모래시계 kBonusDamagePct 150/200/200 (레벨 3 강화는 주 효과가 그대로이고 감소율만 30→25).
수치의 단위는 게임 내부 값 그대로다 — 치명타 계열(150/300/600 등)은 단위를 확인하지 못해 배율로만 비교한다.

역할(생존·화력·범위·베이비볼 …)은 수치 이름으로 나눈다. 역할별 상황 가중치는 추천 규칙에서 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..i18n import tr

# 수치 이름 → (표시 이름, 단위, 작을수록 좋은지, 역할)
PROPS: Dict[str, Tuple[str, str, bool, str]] = {
    "kBonusDamagePct": (tr("추가 피해"), "%", False, "power"),
    "kBonusDamagePctTenth": (tr("추가 피해"), "‰", False, "power"),
    "kCritChance": (tr("치명타 수치"), "", False, "power"),
    "kFireRatePct": (tr("발사 속도"), "%", False, "power"),
    "kAOEDmgPct": (tr("범위 피해"), "%", False, "aoe"),
    "kBallLvl": (tr("볼 레벨"), "", False, "power"),
    "kMinDamage": (tr("피해 최소"), "", False, "power"),
    "kMaxDamage": (tr("피해"), "", False, "power"),
    "kMinDamagePct": (tr("최소 피해"), "%", False, "power"),
    "kBonusDecayPct": (tr("피해 감소율"), "%", True, "power"),
    "kMaxDynamiteDamage": (tr("폭발 피해"), "", False, "power"),
    "kReflectChance": (tr("반사 확률"), "%", False, "power"),
    "kRange": (tr("범위"), "", False, "power"),
    "kCurseKillChance": (tr("저주 즉사 확률"), "%", False, "power"),
    "kCharmKillChance": (tr("매혹 즉사 확률"), "%", False, "power"),
    "kDetectKillChance": (tr("즉사 확률"), "%", False, "power"),
    "kCritKillChance": (tr("치명타 즉사 확률"), "%", False, "power"),
    "kTouchKillChance": (tr("접촉 즉사 확률"), "%", False, "power"),
    "kReduceDmgPct": (tr("받는 피해 감소"), "%", False, "defense"),
    "kDodgePct": (tr("회피"), "%", False, "defense"),
    "kKillHeal": (tr("처치 시 회복"), "", False, "defense"),
    "kHealAmt": (tr("회복량"), "", False, "defense"),
    "kHealChance": (tr("회복 확률"), "%", False, "defense"),
    "kThornsAmt": (tr("가시 피해"), "", False, "defense"),
    "kCooldownLength": (tr("재사용 대기"), "", True, "defense"),
    "kOverhealEfficiencyPct": (tr("초과 회복 효율"), "%", False, "defense"),
    "kHealthPerMinute": (tr("분당 회복"), "", False, "defense"),
    "kSlowPct": (tr("둔화"), "%", False, "defense"),
    "kMaxBabies": (tr("베이비볼 최대"), tr("개"), False, "baby"),
    "kMinBabies": (tr("베이비볼 최소"), tr("개"), False, "baby"),
    "kBabyChance": (tr("베이비볼 확률"), "%", False, "baby"),
    "kFollowerMult": (tr("베이비볼 피해 배율"), "%", False, "baby"),
    "kBaseSpeedPct": (tr("볼 속도"), "%", False, "speed"),
    "kPeakSpeedPct": (tr("최고 속도"), "%", False, "speed"),
    "kAccelerationPct": (tr("가속"), "%", False, "speed"),
    "kMoveSpeedPct": (tr("이동 속도"), "%", False, "move"),
    "kAllyHealth": (tr("소환 아군 체력"), "", False, "ally"),
    "kAllyBonusHealthPct": (tr("아군 체력"), "%", False, "ally"),
    "kMaxAllyDamage": (tr("아군 피해"), "", False, "ally"),
    "kZombieChance": (tr("좀비 확률"), "%", False, "ally"),
    "kTurretCooldown": (tr("포탑 대기"), "", True, "ally"),
    "kMaxBlockSpawnCycle": (tr("생성 주기(행)"), "", True, "ally"),
    "kGoldPerMinute": (tr("분당 골드"), "", False, "econ"),
    "kMagnetRange": (tr("자석 범위"), "", False, "econ"),
}
# 역할 대표 수치를 고를 때의 우선순위 (앞일수록 주 효과)
ROLE_ORDER = ("power", "aoe", "defense", "baby", "ally", "speed", "move", "econ")
ROLE_LABEL = {"power": tr("화력"), "aoe": tr("범위 피해"), "defense": tr("생존"), "baby": tr("베이비볼"), "ally": tr("소환 아군"),
              "speed": tr("볼 속도"), "move": tr("이동"), "econ": tr("편의")}
SKIP = {"kMinBlockSpawnCycle", "kMinAllyDamage", "kMinDynamiteDamage", "kMinDamage"}   # 짝 수치 (최대 쪽에서 범위로 보여 준다)


@dataclass
class PassiveEffect:
    role: str                  # ROLE_ORDER 중 하나
    text: str                  # 예) "받는 피해 감소 10% → 20%" / "받는 피해 감소 10%"
    gain: Optional[float]      # 강화일 때 가장 많이 좋아지는 수치의 비율 (0.5 = 50% 좋아짐), 새 패시브면 None


def _fmt(key: str, v, row: Optional[dict] = None) -> str:
    label, unit, _, _ = PROPS[key]
    if unit == "‰":
        return f"{v / 10:g}%"
    lo = (row or {}).get("kMin" + key[4:]) if key.startswith("kMax") and key.endswith("Damage") else None
    if isinstance(lo, (int, float)):
        return f"{lo}~{v}"                  # 피해 범위 (kMinDamage ~ kMaxDamage)
    return f"{v}{unit}"


def _main_keys(row: dict) -> List[str]:
    keys = [k for k in row if k in PROPS and k not in SKIP]
    return sorted(keys, key=lambda k: (ROLE_ORDER.index(PROPS[k][3]), list(row).index(k)))


def passive_effect(level_props: Optional[List[dict]], before: Optional[int], after: Optional[int]) -> Optional[PassiveEffect]:
    """before=None 이면 새 패시브(레벨 after 효과), 아니면 before → after 강화의 변화."""
    if not level_props or not after or after > len(level_props):
        return None
    row = level_props[after - 1]
    keys = _main_keys(row)
    if not keys:
        return None
    role = PROPS[keys[0]][3]
    if not before or before < 1 or before > len(level_props):
        k = keys[0]
        return PassiveEffect(role, f"{PROPS[k][0]} {_fmt(k, row[k], row)}", None)
    prev = level_props[before - 1]
    changes: List[Tuple[float, str]] = []
    for k in keys:
        a, b = prev.get(k), row.get(k)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        lower_better = PROPS[k][2]
        if a == b:
            continue
        base = abs(b if lower_better else a) or 1
        g = ((a - b) if lower_better else (b - a)) / base
        changes.append((g, f"{PROPS[k][0]} {_fmt(k, a, prev)} → {_fmt(k, b, row)}"))
    if not changes:
        k = keys[0]
        return PassiveEffect(role, tr("{v0} {v1} 그대로", v0=PROPS[k][0], v1=_fmt(k, row[k], row)), 0.0)
    changes.sort(key=lambda t: -t[0])
    return PassiveEffect(role, " · ".join(t for _, t in changes[:2]), changes[0][0])


# ---- 볼: 레벨별 수치 이름이 볼마다 다르다 (kMinBurnDamage·kMaxBurnDamage, kBurnLength, kMaxBurnStacks …) ----
_BALL_SUFFIX = (("Length", tr("지속")), ("Stacks", tr("최대 중첩")), ("Limit", tr("연쇄 수")))   # 확률(…Pct)은 이름을 옮기지 못해 뺀다


def _ball_parts(row: dict) -> Dict[str, str]:
    import re
    out: Dict[str, str] = {}
    for k, v in row.items():
        m = re.fullmatch(r"kMin(\w*?)Damage", k)
        if m and isinstance(row.get(f"kMax{m.group(1)}Damage"), (int, float)):
            out["피해"] = f"{v}~{row[f'kMax{m.group(1)}Damage']}"
            continue
        if re.fullmatch(r"kMax\w*?Damage", k) and any(re.fullmatch(r"kMin\w*?Damage", x) for x in row):
            continue
        for suf, label in _BALL_SUFFIX:
            if k.endswith(suf):
                out.setdefault(label, str(v))
                break
    return out


def ball_effect(level_props: Optional[List[dict]], before: Optional[int], after: Optional[int]) -> str:
    """볼 강화로 바뀌는 수치 (예: '피해 4~8 → 7~11 · 최대 중첩 3 → 5'). 새 볼이면 레벨 after 의 피해."""
    if not level_props or not after or after > len(level_props):
        return ""
    now = _ball_parts(level_props[after - 1])
    if not before or before < 1 or before > len(level_props):
        return tr("피해 {v0}", v0=now['피해']) if "피해" in now else ""
    prev = _ball_parts(level_props[before - 1])
    changed = [f"{tr(k)} {prev[k]} → {v}" for k, v in now.items() if k in prev and prev[k] != v]
    return " · ".join(changed[:2]) if changed else (tr("수치 변화 없음") if now else "")
