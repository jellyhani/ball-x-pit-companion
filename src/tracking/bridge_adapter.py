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

from ..domain import (
    Card,
    CardLabel,
    ChoicePool,
    FrameInfo,
    FuserCombo,
    FuserEvo,
    FuserOptions,
    GameOverInfo,
    InventorySlot,
    Rect,
    RunProgress,
    ScreenKind,
    ScreenObservation,
)
from ..gamedata import GameData
from ..i18n import tr

IN_RUN_STATES = {
    "kPlaying",
    "kLevelUp",
    "kPaused",
    "kPickTreasure",
    "kFoundBlueprint",
    "kBonusBall",
    "kBonusPassive",
    "kFoundEgg",
    "kRevive",
    "kEnteringLvl",
}

# 게임 1.301의 LevelUpType: 일반 강화와 별도로 시작 볼·패시브 보상을 사용한다.
BONUS_CHOICE_TYPES = {"kBonusBall", "kBonusPassive"}


def _effective(value):
    if (
        not isinstance(value, dict)
        or value.get("scope") != "current_run_uncombined"
        or type(value.get("level")) is not int
        or not 1 <= value["level"] <= 64
    ):
        return None
    result = {"scope": value["scope"], "level": value["level"]}
    for name in ("before", "after"):
        row = value.get(name)
        if row is None and name == "before":
            continue
        if not isinstance(row, dict) or not all(
            isinstance(field_name, str) and type(value) is int for field_name, value in row.items()
        ):
            return None
        result[name] = dict(row)
    return result


@dataclass(frozen=True)
class BridgeState:
    in_run: bool
    game_state: str
    observation: ScreenObservation
    health: Optional[int] = None
    turn: Optional[int] = None
    level_name: str = ""
    unknown_types: Tuple[str, ...] = ()
    max_ball_level: Optional[int] = None  # 최대 레벨 볼을 보고 알아낸 최대 레벨 (화면 기준)
    game_over: Optional[GameOverInfo] = None
    damage: Dict[str, int] = field(
        default_factory=dict
    )  # 이번 런 항목별 피해 (볼: 게임 통계, 패시브: 추가 피해)
    kills: Dict[str, int] = field(default_factory=dict)
    plugin: str = ""  # 플러그인 버전 (1.2.0 부터 보냄)
    battle: Optional["BattleInfo"] = None  # 전투 상황 (1.4.0 부터)


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
    dmg_by: Dict[str, Dict[str, int]] = field(default_factory=dict)  # 항목 → {bounce, status, other, aoe}
    kills_total: Optional[int] = None
    treasures: Optional[int] = None

    @property
    def boss_ratio(self) -> Optional[float]:
        return self.boss_hp / self.boss_max if self.boss_max else None


def _rect(v, size: Tuple[int, int] = (0, 0)) -> Optional[Rect]:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        return None
    x, y, width, height = (int(a) for a in v)
    if size[0] and (
        x < -2 or y < -2 or x + width > size[0] + 2 or y + height > size[1] + 2 or width <= 0 or height <= 0
    ):
        return None  # 창이 미끄러져 들어오는 중이면 화면 밖 좌표가 온다
    return (x, y, width, height)


def _position_names(n: int) -> List[str]:
    if n == 3:
        return [tr("왼쪽"), tr("가운데"), tr("오른쪽")]
    if n == 2:
        return [tr("왼쪽"), tr("오른쪽")]
    if n == 1:
        return [tr("가운데")]
    return [tr("{v0}번째", v0=index + 1) for index in range(n)]


def char_id(data: GameData, enum_name: Optional[str]) -> Optional[str]:
    if not enum_name or not enum_name.startswith("k"):
        return None
    character_id = f"char:{enum_name[1:].lower()}"
    return character_id if character_id in data.characters else None


def _shown(lvl) -> Optional[int]:
    return lvl + 1 if isinstance(lvl, int) and lvl >= 0 else None


def convert(
    snapshot: dict, data: GameData, origin: Tuple[int, int] = (0, 0), frame_id: int = 0, at: float = 0.0
) -> BridgeState:
    size = (int(snapshot.get("screen_w") or 0), int(snapshot.get("screen_h") or 0))
    frame = FrameInfo(frame_id, at, origin, size, tr("게임 연동"))
    state = snapshot.get("game_state") or ""
    battle = snapshot.get("battle") if isinstance(snapshot.get("battle"), dict) else None
    lvl = snapshot.get("levelup") if isinstance(snapshot.get("levelup"), dict) else None
    ui = snapshot.get("ui") if isinstance(snapshot.get("ui"), dict) else {}
    if (
        lvl is not None
        and snapshot.get("game_state") not in (None, "kLevelUp")
        and not (lvl.get("type") in BONUS_CHOICE_TYPES and state == lvl.get("type"))
        and not (lvl.get("type") == "kFuser" and ui.get("screen") == "fuser")
    ):
        # 실제 게임 확인: 융합기가 필드에 떨어질 때 게임 상태는 kPlaying 인데 선택 UI 가 잠깐 kFuser 로 잡힌다.
        # 반대로 실제 융합 화면의 게임 상태는 kPickTreasure (2026-09-26 live_state 확인) — 이때는 플러그인이
        # 융합 UI 가 활성 오버레이일 때만 보내는 ui.screen == "fuser" 로 구별한다
        lvl = None
    unknown: List[str] = []

    def item(enum_name: Optional[str]) -> Optional[str]:
        item_id = data.item_by_log_id(enum_name) if enum_name else None
        if enum_name and item_id is None:
            unknown.append(enum_name)
        return item_id

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
            slots.append(
                InventorySlot(
                    len(slots),
                    (0, 0, 0, 0),
                    True,
                    item(e.get("type")),
                    _shown(e.get("lvl")),
                    at_max=e.get("max"),
                    combined=combined,
                )
            )
            if e.get("max") is True and _shown(e.get("lvl")):
                max_level = max(max_level or 0, _shown(e.get("lvl")))
        inventory = tuple(slots)
        gold = battle.get("gold")
        free = battle.get("free_rerolls")
        banish = battle.get("banishes")
        character = char_id(data, battle.get("char"))
        extra_chars = tuple(
            choice for choice in (char_id(data, x) for x in battle.get("chars_combined") or []) if choice
        )

    damage: Dict[str, int] = {}
    kills: Dict[str, int] = {}
    for e in balls_raw + passives_raw:
        item_id = data.item_by_log_id(e.get("type") or "")
        if item_id and isinstance(e.get("dmg"), (int, float)):
            damage[item_id] = damage.get(item_id, 0) + int(e["dmg"])
        if item_id and isinstance(e.get("kills"), (int, float)):
            kills[item_id] = kills.get(item_id, 0) + int(e["kills"])
    building_info = None
    if battle and ("xp" in battle or "stats" in battle or "enemies" in battle):
        boss = battle.get("boss") if isinstance(battle.get("boss"), dict) else {}
        dmg_by: Dict[str, Dict[str, int]] = {}
        for e in balls_raw:
            item_id = data.item_by_log_id(e.get("type") or "")
            if item_id and isinstance(e.get("dmg_by"), dict):
                acc = dmg_by.setdefault(item_id, {})
                for field_name, value in e["dmg_by"].items():
                    if isinstance(value, (int, float)):
                        acc[field_name] = acc.get(field_name, 0) + int(value)
        building_info = BattleInfo(
            xp=battle.get("xp"),
            xp_next=battle.get("xp_next"),
            enemies=battle.get("enemies"),
            lowest_enemy_y=battle.get("lowest_enemy_y"),
            rows=battle.get("rows"),
            boss_type=boss.get("type"),
            boss_hp=boss.get("hp"),
            boss_max=boss.get("max"),
            stats={
                field_name: value
                for field_name, value in (battle.get("stats") or {}).items()
                if isinstance(value, (int, float))
            },
            char_stats=tuple(int(x) for x in battle.get("char_stats") or [] if isinstance(x, (int, float))),
            effects=tuple(
                (e.get("type", ""), float(e.get("left") or 0))
                for e in battle.get("effects") or []
                if isinstance(e, dict)
            ),
            dmg_by=dmg_by,
            kills_total=battle.get("kills"),
            treasures=battle.get("treasures"),
        )
    if battle and isinstance(battle.get("baby_dmg"), (int, float)) and battle["baby_dmg"] > 0:
        # 실제 게임 확인: 베이비볼 피해는 볼별 통계와 따로 쌓인다 (런 종료 화면에도 따로 나옴)
        damage[data.ensure_runtime_item("baby", "kBabyBalls", "베이비볼")] = int(battle["baby_dmg"])
    go = snapshot.get("game_over") if isinstance(snapshot.get("game_over"), dict) else None
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
            health=battle.get("health"),
            max_health=battle.get("max_health"),
            turn=battle.get("turn"),
            final_boss_turn=battle.get("final_boss_turn") or None,
            level_name=battle.get("level", ""),
            difficulty=battle.get("difficulty"),
            ng_plus=battle.get("ng_plus"),
            endless=battle.get("endless"),
            max_balls=battle.get("max_balls"),
            max_passives=battle.get("max_passives"),
            balls=len(balls_raw),
            passives=len(passives_raw),
            banished=tuple(x for x in (item(t) for t in battle.get("banished") or []) if x),
            endless_start_turn=battle.get("endless_start_turn") if battle.get("endless") else None,
            revives_left=(
                battle["revives_max"] - battle.get("revives", 0)
                if isinstance(battle.get("revives_max"), int)
                else None
            ),
            next_boss_turn=nb,
            next_fuser_turn=nf,
        )
    common = dict(
        frame=frame,
        inventory=inventory,
        character_id=character,
        gold=gold,
        extra_characters=extra_chars,
        banish_left=banish,
        progress=progress,
    )

    def result(observation: ScreenObservation) -> BridgeState:
        return BridgeState(
            in_run,
            state,
            observation,
            battle.get("health") if battle else None,
            battle.get("turn") if battle else None,
            battle.get("level", "") if battle else "",
            tuple(unknown),
            max_level,
            game_over,
            damage,
            kills,
            str(snapshot.get("plugin") or ""),
            building_info,
        )

    if lvl is None:
        return result(ScreenObservation(ScreenKind.OTHER, **common))

    fz = lvl.get("fuser") if isinstance(lvl.get("fuser"), dict) else None
    if lvl.get("type") == "kFuser":
        options = None
        if fz:
            options = FuserOptions(
                options=tuple(fz.get("options") or ()),
                evos=tuple(
                    FuserEvo(
                        item(e.get("type")),
                        int(e.get("equip_idx", -1)),
                        e.get("evo_idx") if isinstance(e.get("evo_idx"), int) else None,
                    )
                    for e in fz.get("evos") or []
                ),
                combos=tuple(
                    FuserCombo(
                        item(choice.get("h1")),
                        item(choice.get("h2")),
                        int(choice.get("idx1", -1)),
                        int(choice.get("idx2", -1)),
                        choice.get("ai_score"),
                        choice.get("bad"),
                    )
                    for choice in fz.get("combos") or []
                ),
                free_upgrades=fz.get("free_upgrades"),
            )
        return result(
            ScreenObservation(
                ScreenKind.FUSION, fuser=options, panel_rect=_rect(lvl.get("panel"), size), **common
            )
        )

    if lvl.get("type") not in ({"kNormal"} | BONUS_CHOICE_TYPES) or not lvl.get("choices"):
        return result(ScreenObservation(ScreenKind.OTHER, **common))

    bonus_choice = lvl.get("type") in BONUS_CHOICE_TYPES
    if bonus_choice:
        # 시작 보상은 강화 포인트·삭제·새로고침 버튼을 쓰지 않는다. 전투 중 횟수를 가져오지 않는다.
        common["banish_left"] = 0
    raw = list(lvl["choices"])
    if all(_rect(choice.get("rect"), size) for choice in raw):
        raw.sort(key=lambda c: _rect(c.get("rect"), size)[0])
    else:
        raw.sort(key=lambda c: c.get("idx", 0))
    names = _position_names(len(raw))
    cards = []
    for index, choice in enumerate(raw):
        rect = _rect(choice.get("rect"), size) or (0, 0, 0, 0)
        is_new = bool(choice.get("is_new"))
        shown = None
        if not is_new:
            # 강화 후 레벨 = 보유 칸의 현재 레벨 + 1 (equip_idx 가 보유 목록 위치를 가리킨다)
            pool = balls_raw if choice.get("kind") == "kHero" else passives_raw
            current_index = choice.get("equip_idx", -1)
            if isinstance(current_index, int) and 0 <= current_index < len(pool):
                current = _shown(pool[current_index].get("lvl"))
                shown = current + 1 if current else None
        syn = tuple(x for x in (item(t) for t in choice.get("synergy") or []) if x)
        if choice.get("kind") in ("kPet", "kPetUpgrade") and choice.get("type"):
            # 펫 강화: 게임이 보낸 현지화 이름으로 항목을 등록한다 (번역 표·아이콘에는 없음)
            item_id = data.ensure_runtime_item(
                "pet", choice["type"], choice.get("name_loc") or "", choice.get("desc_loc") or ""
            )
        else:
            item_id = item(choice.get("type"))
        effective = _effective(choice.get("effective"))
        if is_new and effective is not None:
            shown = effective["level"]
        cards.append(
            Card(
                index=index,
                position=names[index],
                rect=rect,
                icon_rect=rect,
                item_id=item_id,
                label=CardLabel.NEW if is_new else CardLabel.UPGRADE,
                shown_level=shown,
                synergy=syn,
                ai_pick=choice.get("ai_pick"),
                effective=effective,
            )
        )
    pool = None
    pr = lvl.get("pool") if isinstance(lvl.get("pool"), dict) else None
    if not bonus_choice and pr and any(k in pr for k in ("new_balls", "ball_upgrades")):

        def ids(key):
            return tuple(x for x in (item(t) for t in pr.get(key) or []) if x)

        pool = ChoicePool(
            ids("new_balls"),
            ids("ball_upgrades"),
            ids("new_passives"),
            ids("passive_upgrades"),
            ids("prev"),
            int(pr.get("num_choices") or len(cards) or 3),
        )
    observation = ScreenObservation(
        kind=ScreenKind.LEVEL_UP,
        cards=tuple(cards),
        pool=pool,
        reroll_cost=None if bonus_choice else lvl.get("reroll_cost"),
        free_rerolls=0 if bonus_choice else (free if free else None),
        points_left=battle.get("level_ups_avail") if battle and not bonus_choice else None,
        panel_rect=_rect(lvl.get("panel"), size),
        **common,
    )
    return result(observation)


def catalog_schedules(catalog: dict) -> Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]]:
    """지역별 보스·융합기 턴 (게임 LevelInfo.BossTurns / FuserTurns)."""
    result = {}
    for lv in catalog.get("levels") or []:
        if isinstance(lv, dict) and lv.get("type"):
            result[lv["type"]] = (
                tuple(int(x) for x in lv.get("boss_turns") or [] if isinstance(x, (int, float))),
                tuple(int(x) for x in lv.get("fuser_turns") or [] if isinstance(x, (int, float))),
            )
    return result


def catalog_recipes(catalog: dict, data: GameData) -> List[Tuple[str, Tuple[str, ...]]]:
    """게임 안 레시피 표 → (결과 ID, 재료 ID들). 모르는 이름이 섞인 레시피는 뺀다."""
    current_result = []
    for kind in ("balls", "passives"):
        for e in catalog.get(kind) or []:
            result = data.item_by_log_id(e.get("type") or "")
            if not result:
                continue
            for recipe in e.get("recipes") or []:
                ids = [data.item_by_log_id(x) for x in recipe]
                if ids and all(ids):
                    current_result.append((result, tuple(ids)))
    return current_result


def infer_pick(
    before: Optional[Tuple[InventorySlot, ...]],
    after: Optional[Tuple[InventorySlot, ...]],
    cards: Tuple[Card, ...],
) -> Optional[Card]:
    """선택창 전후 보유 목록 차이로 실제로 고른 카드를 찾는다 (새 항목 추가 또는 레벨 상승)."""
    if before is None or after is None:
        return None

    def counts(slots):
        result = {}
        for slot in slots:
            if slot.item_id:
                result.setdefault(slot.item_id, []).append(slot.level or 0)
        return result

    battle, a = counts(before), counts(after)
    for choice in cards:
        if not choice.item_id:
            continue
        old, new = battle.get(choice.item_id, []), a.get(choice.item_id, [])
        if choice.label is CardLabel.NEW and len(new) > len(old):
            return choice
        if choice.label is CardLabel.UPGRADE and sorted(new) != sorted(old) and sum(new) > sum(old):
            return choice
    return None
