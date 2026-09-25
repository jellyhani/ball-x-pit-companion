"""기지 조언: 설계도는 있는데 아직 안 지은 건물과 올릴 수 있는 건물 중 무엇부터 할지.

근거: 게임 연동 meta(보유 자원, 건물·설계도, 짓는/올리는 비용), 건물 설명(게임 파일 원문).
순서: 미완성 건물(공사·강화 공사 중) 완성이 맨 먼저 — 채집 때 작업자를 맞혀야 끝나고, 끝나야 효과가 난다.
     그다음 런에 직접 영향을 주는 건물(설명에 레벨 업·강화·삭제·새로고침·부활 등) → 전투(kWarfare) → 경제 → 주거,
     같은 순위면 지금 지을 수 있는 것, 부족한 자원이 적은 것부터. 건물 효과의 크기는 비교하지 않는다(모름).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..gamedata import GameData
from ..i18n import tr
from ..tracking.meta_state import MetaState

# 게임 설명(desc_ko)에서 이 낱말이 있으면 런에 직접 영향 주는 건물로 본다 — 게임 원문과 대조하는 것이라
# 번역하면 안 된다(다국어 게임 문구 지원(gamedata.py) 은 아직 desc_ko 가 감지된 언어로 바뀌는 걸 반영 못 함 —
# 한국어가 아닌 언어에서는 이 매칭이 안 먹는다는 뜻. 알려진 제한, CLAUDE.local.md 참고).
RUN_WORDS = ("레벨 업", "강화", "삭제", "새로고침", "부활", "선택지", "볼 칸", "패시브 칸", "보스 격퇴", "원정")
CAT_ORDER = {"kWarfare": 0, "kEconomy": 1, "kHousing": 2}
CAT_LABEL = {"kWarfare": tr("전투"), "kEconomy": tr("경제"), "kHousing": tr("주거")}


@dataclass
class BaseSuggestion:
    kind: str            # finish | build | upgrade
    type: str            # 게임 내부 이름
    name: str
    desc: str
    cost_text: str
    affordable: bool
    missing_text: str
    run_impact: bool
    category: str
    shortfall: int = 0
    cost: Tuple[int, ...] = ()

    @property
    def status(self) -> str:
        return tr("지금 가능") if self.affordable else tr("부족: {missing_text}", missing_text=self.missing_text)


def _desc(data: GameData, type_name: str) -> str:
    key = type_name[1:].lower() if type_name.startswith("k") else type_name.lower()
    # 설명의 수치 자리(건물 레벨마다 다름)는 N 으로 보여 준다
    return data.buildings.get(key, {}).get("desc_ko", "").replace("?", "N")


FINISH_LABEL = {"kScaffold": tr("공사 중"), "kUpgrading": tr("강화 공사 중")}


def suggest(meta: MetaState, data: GameData, limit: int = 10) -> List[BaseSuggestion]:
    out: List[BaseSuggestion] = []
    for b in meta.buildings:
        if b.state in FINISH_LABEL:
            out.append(BaseSuggestion("finish", b.type, tr("{v0} 완성", v0=data.building_name(b.type)), _desc(data, b.type),
                                      tr("{v0} · 채집 때 작업자를 이 건물에 맞히면 공사가 진행됨", v0=FINISH_LABEL[b.state]),
                                      True, "", True, "", 0))
    for bp in meta.blueprints:
        desc = _desc(data, bp.type)
        short = meta.shortfall(bp.cost)
        out.append(BaseSuggestion("build", bp.type, data.building_name(bp.type), desc, meta.resource_text(bp.cost),
                                  meta.affordable(bp.cost), " · ".join(f"{k} {v}" for k, v in short.items()),
                                  any(w in desc for w in RUN_WORDS), bp.category, sum(short.values()), bp.cost))
    for b in meta.buildings:
        if not b.can_upgrade or not b.upgrade_cost:
            continue
        desc = _desc(data, b.type)
        short = meta.shortfall(b.upgrade_cost)
        out.append(BaseSuggestion("upgrade", b.type, tr("{v0} 강화", v0=data.building_name(b.type)), desc,
                                  meta.resource_text(b.upgrade_cost), meta.affordable(b.upgrade_cost),
                                  " · ".join(f"{k} {v}" for k, v in short.items()),
                                  any(w in desc for w in RUN_WORDS), "", sum(short.values()), b.upgrade_cost))

    out.sort(key=lambda s: (s.kind != "finish", not s.run_impact, not s.affordable, CAT_ORDER.get(s.category, 3), s.kind != "build",
                            s.shortfall, s.name))
    return out[:limit]
