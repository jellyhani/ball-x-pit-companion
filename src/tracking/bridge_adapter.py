"""브리지 스냅샷(JSON)을 앱의 공통 형식(ScreenObservation)으로 바꾼다.

화면 인식 경로와 같은 형식이라 선택 추적·추천·HUD를 그대로 쓴다.
게임 내부 이름(kLaserHorz, kBrickHead)은 게임 번역 키(hupg_laserhorz, char_brickhead)와 대응한다.

실제 게임에서 확인한 점 (버전 1.301)
- 볼·패시브의 lvl 은 0부터 센다. 화면의 '1' = lvl 0. 화면 기준 레벨로 바꿔 쓴다.
- 강화 선택창은 먼저 능력치 페이지(kViewStats)를 보여 준다. 이때는 카드 버튼이 없어 화면 위치가 없다.
- 캐릭터는 CharBattleInst.Type, 함께 쓰는 캐릭터는 CombinedTypes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..domain import (Card, CardLabel, ChoicePool, FrameInfo, FuserCombo, FuserEvo, FuserOptions, GameOverInfo,
                      InventorySlot, Rect, RunProgress, ScreenKind, ScreenObservation)
from ..gamedata import GameData
from ..i18n import tr

IN_RUN_STATES = {"kPlaying", "kLevelUp", "kPaused", "kPickTreasure", "kFoundBlueprint", "kBonusBall",
                 "kBonusPassive", "kFoundEgg", "kRevive", "kEnteringLvl"}


@dataclass(frozen=True)
class BridgeState:
    in_run: bool
    game_state: str
    observation: ScreenObservation
    health: Optional[int] = None
    turn: Optional[int] = None
    level_name: str = ""
    unknown_types: Tuple[str, ...] = ()
    max_ball_level: Optional[int] = None     # 최대 레벨 볼을 보고 알아낸 최대 레벨 (화면 기준)
    game_over: Optional[GameOverInfo] = None
    damage: Dict[str, int] = field(default_factory=dict)   # 이번 런 항목별 피해 (볼: 게임 통계, 패시브: 추가 피해)
    kills: Dict[str, int] = field(default_factory=dict)
    plugin: str = ""                          # 플러그인 버전 (1.2.0 부터 보냄)
    battle: Optional["BattleInfo"] = None     # 전투 상황 (1.4.0 부터)


@dataclass(frozen=True)
class BattleInfo:
    """전투 상황 (게임 연동 1.4). 모르는 값은 None."""
    xp: Optional[float] = None
    xp_next: Optional[int] = None
    enemies: Optional[int] = None
    lowest_enemy_y: Optional[float] = None
    rows: Optional[int] = None
    boss_type: Optional[str] = None
    boss_hp: Optional[int] = None
    boss_max: Optional[int] = None
    stats: Dict[str, float] = field(default_factory=dict)
    char_stats: Tuple[int, ...] = ()
    effects: Tuple[Tuple[str, float], ...] = ()
    dmg_by: Dict[str, Dict[str, int]] = field(default_factory=dict)    # 항목 → {bounce, status, other, aoe}
    kills_total: Optional[int] = None
    treasures: Optional[int] = None

    @property
    def boss_ratio(self) -> Optional[float]:
        return self.boss_hp / self.boss_max if self.boss_max else None


def _rect(v, size: Tuple[int, int] = (0, 0)) -> Optional[Rect]:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        return None
    x, y, w, h = (int(a) for a in v)
    if size[0] and (x < -2 or y < -2 or x + w > size[0] + 2 or y + h > size[1] + 2 or w <= 0 or h <= 0):
        return None   # 창이 미끄러져 들어오는 중이면 화면 밖 좌표가 온다
    return (x, y, w, h)


def _position_names(n: int) -> List[str]:
    if n == 3:
        return [tr("왼쪽"), tr("가운데"), tr("오른쪽")]
    if n == 2:
        return [tr("왼쪽"), tr("오른쪽")]
    if n == 1:
        return [tr("가운데")]
    return [tr("{v0}번째", v0=i + 1) for i in range(n)]


def char_id(data: GameData, enum_name: Optional[str]) -> Optional[str]:
    if not enum_name or not enum_name.startswith("k"):
        return None
    cid = f"char:{enum_name[1:].lower()}"
    return cid if cid in data.characters else None


def _shown(lvl) -> Optional[int]:
    return lvl + 1 if isinstance(lvl, int) and lvl >= 0 else None


def convert(snap: dict, data: GameData, origin: Tuple[int, int] = (0, 0), frame_id: int = 0,
            at: float = 0.0) -> BridgeState:
    size = (int(snap.get("screen_w") or 0), int(snap.get("screen_h") or 0))
    frame = FrameInfo(frame_id, at, origin, size, tr("게임 연동"))
    state = snap.get("game_state") or ""
    battle = snap.get("battle") if isinstance(snap.get("battle"), dict) else None
    lvl = snap.get("levelup") if isinstance(snap.get("levelup"), dict) else None
    if lvl is not None and snap.get("game_state") not in (None, "kLevelUp"):
        # 실제 게임 확인: 융합기가 필드에 떨어질 때 게임 상태는 kPlaying 인데 선택 UI 가 잠깐 kFuser 로 잡힌다
        lvl = None
    unknown: List[str] = []

    def item(enum_name: Optional[str]) -> Optional[str]:
        iid = data.item_by_log_id(enum_name) if enum_name else None
        if enum_name and iid is None:
            unknown.append(enum_name)
        return iid

    inventory: Optional[Tuple[InventorySlot, ...]] = None
    balls_raw: list = []
    passives_raw: list = []
    gold = free = banish = None
    character = None
    extra_chars: Tuple[str, ...] = ()
    max_level = None
    if battle:
        balls_raw = battle.get("balls") or []
        passives_raw = battle.get("passives") or []
        slots = []
        for e in balls_raw + passives_raw:
            # 퓨전 리액터에서 합쳐 넣은 볼(예: 산사태 안의 피뢰침) — 옛 플러그인(1.11 미만)은 이 필드가 없다
            combined = tuple(x for x in (item(t) for t in e.get("combined") or []) if x)
            slots.append(InventorySlot(len(slots), (0, 0, 0, 0), True, item(e.get("type")), _shown(e.get("lvl")),
                                       at_max=e.get("max"), combined=combined))
            if e.get("max") is True and _shown(e.get("lvl")):
                max_level = max(max_level or 0, _shown(e.get("lvl")))
        inventory = tuple(slots)
        gold = battle.get("gold")
        free = battle.get("free_rerolls")
        banish = battle.get("banishes")
        character = char_id(data, battle.get("char"))
        extra_chars = tuple(c for c in (char_id(data, x) for x in battle.get("chars_combined") or []) if c)

    damage: Dict[str, int] = {}
    kills: Dict[str, int] = {}
    for e in balls_raw + passives_raw:
        iid = data.item_by_log_id(e.get("type") or "")
        if iid and isinstance(e.get("dmg"), (int, float)):
            damage[iid] = damage.get(iid, 0) + int(e["dmg"])
        if iid and isinstance(e.get("kills"), (int, float)):
            kills[iid] = kills.get(iid, 0) + int(e["kills"])
    binfo = None
    if battle and ("xp" in battle or "stats" in battle or "enemies" in battle):
        boss = battle.get("boss") if isinstance(battle.get("boss"), dict) else {}
        dmg_by: Dict[str, Dict[str, int]] = {}
        for e in balls_raw:
            iid = data.item_by_log_id(e.get("type") or "")
            if iid and isinstance(e.get("dmg_by"), dict):
                acc = dmg_by.setdefault(iid, {})
                for k, v in e["dmg_by"].items():
                    if isinstance(v, (int, float)):
                        acc[k] = acc.get(k, 0) + int(v)
        binfo = BattleInfo(
            xp=battle.get("xp"), xp_next=battle.get("xp_next"), enemies=battle.get("enemies"),
            lowest_enemy_y=battle.get("lowest_enemy_y"), rows=battle.get("rows"),
            boss_type=boss.get("type"), boss_hp=boss.get("hp"), boss_max=boss.get("max"),
            stats={k: v for k, v in (battle.get("stats") or {}).items() if isinstance(v, (int, float))},
            char_stats=tuple(int(x) for x in battle.get("char_stats") or [] if isinstance(x, (int, float))),
            effects=tuple((e.get("type", ""), float(e.get("left") or 0)) for e in battle.get("effects") or []
                          if isinstance(e, dict)),
            dmg_by=dmg_by, kills_total=battle.get("kills"), treasures=battle.get("treasures"))
    if battle and isinstance(battle.get("baby_dmg"), (int, float)) and battle["baby_dmg"] > 0:
        # 실제 게임 확인: 베이비볼 피해는 볼별 통계와 따로 쌓인다 (런 종료 화면에도 따로 나옴)
        damage[data.ensure_runtime_item("baby", "kBabyBalls", "베이비볼")] = int(battle["baby_dmg"])
    go = snap.get("game_over") if isinstance(snap.get("game_over"), dict) else None
    game_over = GameOverInfo(go.get("completed"), go.get("endless_btn")) if go else None

    in_run = battle is not None and state in IN_RUN_STATES
    progress = None
    nb = nf = None
    if battle:
        sched = data.level_schedules.get(battle.get("level", ""))
        turn = battle.get("turn")
        if sched and isinstance(turn, int):
            nb = next((t for t in sorted(sched[0]) if t > turn), None)
            nf = next((t for t in sorted(sched[1]) if t > turn), None)
    if battle:
        progress = RunProgress(
            health=battle.get("health"), max_health=battle.get("max_health"), turn=battle.get("turn"),
            final_boss_turn=battle.get("final_boss_turn") or None, level_name=battle.get("level", ""),
            difficulty=battle.get("difficulty"), ng_plus=battle.get("ng_plus"), endless=battle.get("endless"),
            max_balls=battle.get("max_balls"), max_passives=battle.get("max_passives"),
            balls=len(balls_raw), passives=len(passives_raw),
            banished=tuple(x for x in (item(t) for t in battle.get("banished") or []) if x),
            endless_start_turn=battle.get("endless_start_turn") if battle.get("endless") else None,
            revives_left=(battle["revives_max"] - battle.get("revives", 0)
                          if isinstance(battle.get("revives_max"), int) else None),
            next_boss_turn=nb, next_fuser_turn=nf,
        )
    common = dict(frame=frame, inventory=inventory, character_id=character, gold=gold,
                  extra_characters=extra_chars, banish_left=banish, progress=progress)

    def result(obs: ScreenObservation) -> BridgeState:
        return BridgeState(in_run, state, obs, battle.get("health") if battle else None,
                           battle.get("turn") if battle else None, battle.get("level", "") if battle else "",
                           tuple(unknown), max_level, game_over, damage, kills, str(snap.get("plugin") or ""), binfo)

    if lvl is None:
        return result(ScreenObservation(ScreenKind.OTHER, **common))

    fz = lvl.get("fuser") if isinstance(lvl.get("fuser"), dict) else None
    if lvl.get("type") == "kFuser":
        opts = None
        if fz:
            opts = FuserOptions(
                options=tuple(fz.get("options") or ()),
                evos=tuple(FuserEvo(item(e.get("type")), int(e.get("equip_idx", -1))) for e in fz.get("evos") or []),
                combos=tuple(FuserCombo(item(c.get("h1")), item(c.get("h2")), int(c.get("idx1", -1)),
                                        int(c.get("idx2", -1)), c.get("ai_score"), c.get("bad"))
                             for c in fz.get("combos") or []),
                free_upgrades=fz.get("free_upgrades"),
            )
        return result(ScreenObservation(ScreenKind.FUSION, fuser=opts,
                                        panel_rect=_rect(lvl.get("panel"), size), **common))

    if lvl.get("type") != "kNormal" or not lvl.get("choices"):
        return result(ScreenObservation(ScreenKind.OTHER, **common))

    raw = list(lvl["choices"])
    if all(_rect(c.get("rect"), size) for c in raw):
        raw.sort(key=lambda c: _rect(c.get("rect"), size)[0])
    else:
        raw.sort(key=lambda c: c.get("idx", 0))
    names = _position_names(len(raw))
    cards = []
    for i, c in enumerate(raw):
        rect = _rect(c.get("rect"), size) or (0, 0, 0, 0)
        is_new = bool(c.get("is_new"))
        shown = None
        if not is_new:
            # 강화 후 레벨 = 보유 칸의 현재 레벨 + 1 (equip_idx 가 보유 목록 위치를 가리킨다)
            pool = balls_raw if c.get("kind") == "kHero" else passives_raw
            idx = c.get("equip_idx", -1)
            if isinstance(idx, int) and 0 <= idx < len(pool):
                cur = _shown(pool[idx].get("lvl"))
                shown = cur + 1 if cur else None
        syn = tuple(x for x in (item(t) for t in c.get("synergy") or []) if x)
        if c.get("kind") in ("kPet", "kPetUpgrade") and c.get("type"):
            # 펫 강화: 게임이 보낸 현지화 이름으로 항목을 등록한다 (번역 표·아이콘에는 없음)
            iid = data.ensure_runtime_item("pet", c["type"], c.get("name_loc") or "", c.get("desc_loc") or "")
        else:
            iid = item(c.get("type"))
        cards.append(Card(index=i, position=names[i], rect=rect, icon_rect=rect, item_id=iid,
                          label=CardLabel.NEW if is_new else CardLabel.UPGRADE, shown_level=shown,
                          synergy=syn, ai_pick=c.get("ai_pick")))
    pool = None
    pr = lvl.get("pool") if isinstance(lvl.get("pool"), dict) else None
    if pr and any(k in pr for k in ("new_balls", "ball_upgrades")):
        def ids(key):
            return tuple(x for x in (item(t) for t in pr.get(key) or []) if x)
        pool = ChoicePool(ids("new_balls"), ids("ball_upgrades"), ids("new_passives"), ids("passive_upgrades"),
                          ids("prev"), int(pr.get("num_choices") or len(cards) or 3))
    obs = ScreenObservation(
        kind=ScreenKind.LEVEL_UP, cards=tuple(cards), pool=pool,
        reroll_cost=lvl.get("reroll_cost"), free_rerolls=free if free else None,
        points_left=battle.get("level_ups_avail") if battle else None,
        panel_rect=_rect(lvl.get("panel"), size), **common,
    )
    return result(obs)


def catalog_schedules(catalog: dict) -> Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]]:
    """지역별 보스·융합기 턴 (게임 LevelInfo.BossTurns / FuserTurns)."""
    out = {}
    for lv in catalog.get("levels") or []:
        if isinstance(lv, dict) and lv.get("type"):
            out[lv["type"]] = (tuple(int(x) for x in lv.get("boss_turns") or [] if isinstance(x, (int, float))),
                               tuple(int(x) for x in lv.get("fuser_turns") or [] if isinstance(x, (int, float))))
    return out


def catalog_recipes(catalog: dict, data: GameData) -> List[Tuple[str, Tuple[str, ...]]]:
    """게임 안 레시피 표 → (결과 ID, 재료 ID들). 모르는 이름이 섞인 레시피는 뺀다."""
    out = []
    for kind in ("balls", "passives"):
        for e in catalog.get(kind) or []:
            result = data.item_by_log_id(e.get("type") or "")
            if not result:
                continue
            for recipe in e.get("recipes") or []:
                ids = [data.item_by_log_id(x) for x in recipe]
                if ids and all(ids):
                    out.append((result, tuple(ids)))
    return out


def infer_pick(before: Optional[Tuple[InventorySlot, ...]], after: Optional[Tuple[InventorySlot, ...]],
               cards: Tuple[Card, ...]) -> Optional[Card]:
    """선택창 전후 보유 목록 차이로 실제로 고른 카드를 찾는다 (새 항목 추가 또는 레벨 상승)."""
    if before is None or after is None:
        return None

    def counts(slots):
        out = {}
        for s in slots:
            if s.item_id:
                out.setdefault(s.item_id, []).append(s.level or 0)
        return out

    b, a = counts(before), counts(after)
    for c in cards:
        if not c.item_id:
            continue
        old, new = b.get(c.item_id, []), a.get(c.item_id, [])
        if c.label is CardLabel.NEW and len(new) > len(old):
            return c
        if c.label is CardLabel.UPGRADE and sorted(new) != sorted(old) and sum(new) > sum(old):
            return c
    return None
