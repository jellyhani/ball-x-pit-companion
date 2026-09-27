"""게임 데이터 로더.

출처를 섞지 않고 구분해서 보관한다.
- 이름·설명: data/game_text_ko.json (게임 파일에서 추출한 공식 번역 — 윈도우 UI 언어로 자동 감지해 추출하므로
  name_ko/desc_ko 필드에 실제로는 한국어가 아닌 언어가 들어 있을 수 있다. 파일·필드 이름은 역사적인 것이라 안 바꿈.
  어떤 언어인지는 game_text_ko.json 의 source.language 를 보면 안다.)
- 진화 레시피·상태 이상 태그·시작 볼: data/*_db.json (외부 위키, 미검증)
- 추천 규칙: data/rules.json (근거가 적힌 수동 정리)
- 커뮤니티 평가: data/community.json (공략 사이트 티어·캐릭터 빌드, 의견)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .i18n import tr

REPO_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# 게임에서 추출한 자료(아이콘·초상화·게임 문구)는 저작권이 게임에 있으므로 배포하지 않고, 설치할 때 사용자의 게임
# 파일에서 이 폴더로 추출한다 (tools/setup_data.py). 여기에 없으면 저장소 data/ (개발용)를 쓴다.
USER_DATA_DIR = os.environ.get('BXP_DATA_DIR') or os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BallxPitCompanion", "gamedata")


def resolve_data_dir() -> str:
    if os.environ.get('BXP_DATA_DIR'):
        return USER_DATA_DIR
    if os.path.exists(os.path.join(USER_DATA_DIR, "game_text_ko.json")):
        return USER_DATA_DIR
    return REPO_DATA_DIR


data_dir = resolve_data_dir      # 예전 이름
DATA_DIR = resolve_data_dir()


def has_game_text() -> bool:
    return os.path.exists(os.path.join(resolve_data_dir(), "game_text_ko.json"))
# 선택 자료: 위키 표 (볼 상태 이상·피해 종류, 위키 레시피). 없으면 게임 연동 값으로 대신한다.
OPTIONAL_JSON = ("balls_db.json", "passives_db.json", "characters_db.json")
# 게임 한국어 설명의 표현 → 상태 이상 (위키 태그가 없을 때). 위키 태그와 90개 볼 중 67개 계열 일치 — 나머지는
# 위키 쪽도 애매한 경우(흡혈 박쥐를 낳는데 흡혈 태그 없음 등)가 많다.
DESC_STATUS = (("화상", "Burn"), ("어둠불꽃", "Burn"), ("냉동", "Freeze"), ("빙결", "Freeze"), ("출혈", "Bleed"),
               ("거머리", "Bleed"), ("중독", "Poison"), ("방사", "Radiation"), ("저주", "Curse"), ("매혹", "Charm"),
               ("실명", "Blind"), ("흡혈", "Lifesteal"), ("회복", "Heal"), ("체력을 훔", "Lifesteal"),
               ("베이비볼", "Baby Ball Spawn"), ("분열", "Clone"), ("모기", "Mosquito Spawn"),
               ("알주머니", "Baby Ball Spawn"), ("구더기", "Baby Ball Spawn"), ("둔화", "Slow"))
DESC_AOE = ("타일 범위", "타일 사각형", "인근 적", "근처 최대", "모든 적에게", "경로 내", "레이저", "유성우", "광선",
            "용암 방울")
CATALOG_FILE = "game_catalog.json"     # 게임 연동으로 받은 레시피·레벨별 수치 (사용자 PC 에만 저장)
# 볼 레벨별 수치 이름 → 상태 이상 (위키 태그가 없을 때 게임 값으로 판정)
STATUS_FROM_PROP = (("Burn", "Burn"), ("Freeze", "Freeze"), ("Poison", "Poison"), ("Bleed", "Bleed"),
                    ("Curse", "Curse"), ("Charm", "Charm"), ("Blind", "Blind"), ("Radiation", "Radiation"),
                    ("Lifesteal", "Lifesteal"), ("Heal", "Heal"), ("Baby", "Baby Ball Spawn"), ("Slow", "Slow"),
                    ("Mosquito", "Mosquito Spawn"))


@dataclass(frozen=True)
class Item:
    id: str                      # "ball:hemorrhage", "passive:thorns"
    kind: str                    # "ball" | "passive"
    slug: str                    # 게임 내부 키 (로그의 kHemorrhage 와 대응)
    name_ko: str
    name_en: str
    desc_ko: str
    wiki_status: Tuple[str, ...] = ()
    wiki_damage: Tuple[str, ...] = ()
    wiki_unlock: str = ""
    desc_template: str = ""      # 게임 설명 틀 ({[자리표시자]}) — 연동 수치로 채워 보여 준다 (describe)
    names: Dict[str, str] = field(default_factory=dict, compare=False, hash=False)   # 게임이 지원하는 모든 언어의 이름 (검색용)

    def all_names(self) -> Tuple[str, ...]:
        """어느 언어로 검색해도 찾도록: 모든 언어 이름 + 한국어·영어 (옛 추출 파일엔 names 가 없다)."""
        return tuple(dict.fromkeys(x for x in (self.name_ko, self.name_en, *self.names.values()) if x))


@dataclass(frozen=True)
class Character:
    id: str
    slug: str
    name_ko: str
    name_en: str
    desc_ko: str
    wiki_starting_ball: Optional[str] = None   # item id, 위키 기준


@dataclass(frozen=True)
class Recipe:
    result: str                  # item id
    ingredients: Tuple[str, ...]  # AND 관계. 결과가 같은 레시피가 여러 개면 OR 관계
    source: str = "wiki_db"


@dataclass
class GameData:
    items: Dict[str, Item]
    characters: Dict[str, Character]
    recipes: List[Recipe]
    rules: dict
    ui_text: Dict[str, dict]
    source: dict
    _by_slug: Dict[str, str] = field(default_factory=dict)
    _by_en: Dict[str, str] = field(default_factory=dict)
    _recipes_by_ingredient: Dict[str, List[Recipe]] = field(default_factory=dict)
    level_props: Dict[str, List[Dict[str, int]]] = field(default_factory=dict)   # 게임 연동: 레벨별 수치
    derived_tags: Dict[str, tuple] = field(default_factory=dict)   # 위키 태그 대신 게임 수치로 판정한 (상태, 피해)
    buildings: Dict[str, dict] = field(default_factory=dict)   # 건물 slug → {name_ko, desc_ko, ...}
    community: dict = field(default_factory=dict)   # 커뮤니티 평가·추천 빌드 (data/community.json, 의견)
    level_schedules: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]] = field(default_factory=dict)  # 지역 → (보스 턴, 융합기 턴)
    # 해금된 볼·패시브 (게임 연동의 선택지 후보 목록에서 모은 것, tracking/unlocks.py). None = 아직 모름 → 거르지 않음
    available: Optional[set] = None

    def __post_init__(self):
        for it in self.items.values():
            self._by_slug.setdefault(it.slug.lower(), it.id)
            self._by_en[it.name_en.lower()] = it.id
        for r in self.recipes:
            for ing in set(r.ingredients):
                self._recipes_by_ingredient.setdefault(ing, []).append(r)

    # ---- 조회 ----
    def item(self, item_id: Optional[str]) -> Optional[Item]:
        return self.items.get(item_id) if item_id else None

    def name(self, item_id: Optional[str]) -> str:
        it = self.item(item_id)
        if it:
            return it.name_ko
        ch = self.characters.get(item_id or "")
        return ch.name_ko if ch else tr("미확인")

    def describe(self, item_id: str) -> str:
        """게임 설명. 추출 때 비어 있던 수치('?')는 게임 연동으로 받은 레벨별 수치로 '1/2/3' 처럼 채운다."""
        it = self.item(item_id)
        if it is None:
            return ""
        from .engine.desc_fill import fill
        filled = fill(it.desc_template, self.level_props.get(item_id), self.max_level(it.kind))
        return filled or it.desc_ko

    def item_by_log_id(self, log_id: str) -> Optional[str]:
        """Player.log 의 'kReachersSpear' 같은 내부 이름을 항목 ID로 바꾼다."""
        key = log_id[1:] if log_id.startswith("k") and log_id[1:2].isupper() else log_id
        return self._by_slug.get(key.lower())

    def item_by_english(self, name_en: str) -> Optional[str]:
        return self._by_en.get(name_en.lower())

    def recipes_using(self, item_id: str) -> List[Recipe]:
        return self._recipes_by_ingredient.get(item_id, [])

    def recipes_for(self, result_id: str) -> List[Recipe]:
        return [r for r in self.recipes if r.result == result_id]

    def note_available(self, item_ids) -> bool:
        """해금된 것으로 확인된 항목을 더한다. 새로 더한 게 있으면 True."""
        new = {i for i in item_ids if i} - (self.available or set())
        if not new:
            return False
        self.available = (self.available or set()) | new
        return True

    def obtainable(self, item_id: str, _seen: Optional[set] = None) -> bool:
        """이번 런에서 얻을 수 있는지: 기본 항목은 해금돼 있어야 하고, 진화 결과는 레시피 하나라도 재료가 모두 얻을 수
        있어야 한다. 해금 목록을 모르면 (연동 전) 항상 True."""
        if self.available is None or item_id in self.available:
            return True
        made = self.recipes_for(item_id)
        if not made:
            return False
        seen = (_seen or set()) | {item_id}
        return any(all(i not in seen and self.obtainable(i, seen) for i in r.ingredients) for r in made)

    def recipe_reachable(self, r: Recipe) -> bool:
        return all(self.obtainable(i) for i in r.ingredients)

    def locked_ingredients(self, r: Recipe) -> List[str]:
        """레시피 재료 중 아직 해금 안 된 것 (표시용)."""
        return [i for i in r.ingredients if not self.obtainable(i)]

    def has_tag(self, item_id: str, tag: str) -> bool:
        t = self.rules.get("tags", {}).get(tag)
        return bool(t) and item_id in t.get("items", [])

    def tag_label(self, tag: str) -> str:
        return tr(self.rules.get("tags", {}).get(tag, {}).get("label", tag))     # 데이터 문구도 표시할 때 번역

    def character_rule(self, char_id: str) -> dict:
        return self.rules.get("characters", {}).get(char_id, {})

    def max_level(self, kind: str) -> int:
        return int(self.rules.get("max_level", {}).get(kind, 3))

    @property
    def recipe_source(self) -> str:
        return "game" if any(r.source == "game" for r in self.recipes) else "wiki_db"

    def apply_game_recipes(self, pairs: List[Tuple[str, Tuple[str, ...]]]) -> int:
        """게임 연동으로 받은 게임 안 레시피 표로 위키 레시피를 바꾼다. 바꾼 개수를 돌려준다."""
        if not pairs:
            return 0
        self.recipes = [Recipe(result=r, ingredients=ing, source="game") for r, ing in pairs]
        self._recipes_by_ingredient = {}
        for r in self.recipes:
            for ing in set(r.ingredients):
                self._recipes_by_ingredient.setdefault(ing, []).append(r)
        return len(self.recipes)

    def ensure_runtime_item(self, kind: str, enum_name: str, name_ko: str, desc_ko: str = "") -> str:
        """게임 번역 표에 없는 종류(펫 강화 등)를 게임이 보낸 현지화 이름으로 등록한다. 항목 ID를 돌려준다."""
        slug = (enum_name[1:] if enum_name.startswith("k") else enum_name).lower()
        iid = f"{kind}:{slug}"
        if iid not in self.items:
            self.items[iid] = Item(id=iid, kind=kind, slug=slug, name_ko=name_ko or enum_name, name_en=enum_name,
                                   desc_ko=desc_ko)
        return iid

    def apply_level_props(self, props: Dict[str, List[Dict[str, int]]]):
        """게임 연동으로 받은 레벨별 수치 (항목 ID → 레벨 순서의 {속성 이름: 값})."""
        self.level_props = dict(props)
        # 캐시와 실시간 적용 모두 같은 순서: 위키 태그 → 설명 태그 → 수치 이름 보조 판정.
        # 이전 카탈로그에서 추론한 태그를 새 카탈로그보다 우선하지 않도록 매번 원자료에서 만든다.
        self.derived_tags = {}
        for it in self.items.values():
            if it.kind != "ball" or it.wiki_status or it.wiki_damage or not it.desc_ko:
                continue
            tags = tuple(dict.fromkeys(t for k, t in DESC_STATUS if k in it.desc_ko))
            aoe = ("AOE",) if any(k in it.desc_ko for k in DESC_AOE) else ()
            if tags or aoe:
                self.derived_tags[it.id] = (tags, aoe)
        # 위키 태그가 없는 볼은 수치 이름으로 상태 이상을 판정 (kMinBurnDamage → 화상, kFreezePct → 빙결 …)
        for iid, rows in props.items():
            it = self.items.get(iid)
            if it is None or it.kind != "ball" or it.wiki_status or it.wiki_damage or not rows or iid in self.derived_tags:
                continue
            keys = " ".join(k for r in rows for k in r)
            tags = tuple(dict.fromkeys(tag for word, tag in STATUS_FROM_PROP if word in keys))
            aoe = ("AOE",) if any(w in keys for w in ("AOE", "Radius", "Explosion", "Laser", "Lightning")) else ()
            if tags or aoe:
                self.derived_tags[iid] = (tags, aoe)

    def community_tier(self, item_id: str) -> Optional[str]:
        """커뮤니티 평가 티어 (S~D). 볼은 Game Rant, 패시브는 Dexerto 순위 — 없으면 None."""
        it = self.items.get(item_id)
        if it is None:
            return None
        table = self.community.get("ball_tiers" if it.kind == "ball" else "passive_tiers") or {}
        for tier, names in table.items():
            if it.name_en in names:
                return tier
        return None

    def char_build_items(self, char_id: str) -> Tuple[set, str]:
        """캐릭터 추천 빌드의 핵심 항목 ID 들과 한 줄 설명 (커뮤니티, 의견)."""
        b = (self.community.get("char_builds") or {}).get(char_id)
        if not b:
            return set(), ""
        return {self.item_by_english(n) for n in b.get("items", []) if self.item_by_english(n)}, tr(b.get("why", ""))

    def status_tags(self, item_id: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """(상태 이상, 피해 종류) — 위키 태그, 없으면 게임 수치로 판정한 것."""
        it = self.items.get(item_id)
        if it is None:
            return (), ()
        if it.wiki_status or it.wiki_damage:
            return it.wiki_status, it.wiki_damage
        return self.derived_tags.get(item_id, ((), ()))

    def damage_range(self, item_id: str, level: Optional[int]) -> Optional[Tuple[int, int]]:
        """볼의 기본 피해 범위 (게임 수치 kMinDamage~kMaxDamage). 모르면 None."""
        lv = self.level_props.get(item_id)
        if not lv or not level:
            return None
        row = lv[min(level, len(lv)) - 1]
        lo, hi = row.get("kMinDamage"), row.get("kMaxDamage")
        return (lo, hi) if isinstance(lo, int) and isinstance(hi, int) else None

    def max_level_known(self, kind: str) -> bool:
        """게임이 알려 준 값으로 최대 레벨이 확정됐는지. 위키 값(3)은 실제 게임과 달랐다."""
        return bool(self.rules.get("max_level", {}).get("verified"))

    def note_level_seen(self, kind: str, level: int):
        """이 레벨로 강화하는 카드가 나왔다면 최대 레벨은 적어도 그만큼이다 (확정 전 하한 보정)."""
        ml = self.rules.setdefault("max_level", {})
        if not ml.get("verified") and level > int(ml.get(kind, 0)):
            ml[kind] = level

    def set_observed_max_level(self, kind: str, level: int):
        """게임이 '최대 레벨'이라고 알려 준 볼의 레벨로 최대 레벨을 확정한다."""
        ml = self.rules.setdefault("max_level", {})
        if ml.get(kind) != level or not ml.get("verified"):
            ml[kind] = level
            ml["verified"] = True
            ml["source"] = "game_bridge"

    @property
    def game_build_id(self) -> Optional[str]:
        return self.source.get("steam_build_id")

    def building_name(self, slug_or_type: str) -> str:
        """건물 이름. 게임 내부 이름(kExorcist)이나 slug(exorcist) 모두 받는다."""
        key = slug_or_type[1:] if slug_or_type.startswith("k") and slug_or_type[1:2].isupper() else slug_or_type
        b = self.buildings.get(key.lower())
        if b:
            return b["name_ko"]
        import re as _re
        return _re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key)   # 번역이 없는 건물: 게임 내부 이름을 띄어 쓴다


OWN_JSON = ("rules.json", "community.json")       # 이 프로그램의 규칙 파일 — 게임 자료가 아니므로 항상 저장소 data/ 에서


def _read_json(name: str, data_dir: str):
    path = os.path.join(REPO_DATA_DIR if name in OWN_JSON else data_dir, name)
    if name in OPTIONAL_JSON and not os.path.exists(path):
        alt = os.path.join(REPO_DATA_DIR, name)
        if not os.path.exists(alt):
            return []                      # 선택 자료 없음 (공개판 기본)
        path = alt
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_game_data(data_dir: Optional[str] = None) -> GameData:
    data_dir = data_dir or resolve_data_dir()     # 첫 실행 때 추출한 뒤에도 새 폴더를 보도록 호출 때 정한다
    text = _read_json("game_text_ko.json", data_dir)
    wiki_rows = {r["name"]: r for r in _read_json("balls_db.json", data_dir) + _read_json("passives_db.json", data_dir)}

    items: Dict[str, Item] = {}
    en_to_id: Dict[str, str] = {}
    for raw in text["items"].values():
        w = wiki_rows.get(raw["name_en"], {})
        it = Item(
            id=raw["id"], kind=raw["kind"], slug=raw["slug"],
            name_ko=raw["name_ko"], name_en=raw["name_en"], desc_ko=raw.get("desc_ko", ""),
            wiki_status=tuple(w.get("statusEffect", [])), wiki_damage=tuple(w.get("damageType", [])),
            wiki_unlock=w.get("unlockRequirement", ""), desc_template=raw.get("desc_ko_template", ""),
            names=dict(raw.get("names") or {}),
        )
        items[it.id] = it
        en_to_id[it.name_en] = it.id

    recipes: List[Recipe] = []
    for name_en, w in wiki_rows.items():
        result = en_to_id.get(name_en)
        if not result:
            continue
        for combo in w.get("parents", []) or []:
            ids = tuple(en_to_id[p] for p in combo if p in en_to_id)
            if len(ids) == len(combo) and ids:
                recipes.append(Recipe(result=result, ingredients=ids))

    wiki_chars = {c["name"]: c for c in _read_json("characters_db.json", data_dir)}
    characters: Dict[str, Character] = {}
    for raw in text["characters"].values():
        wc = wiki_chars.get(raw["name_en"], {})
        start = en_to_id.get(wc.get("starting_ball", "")) if wc else None
        characters[raw["id"]] = Character(
            id=raw["id"], slug=raw["slug"], name_ko=raw["name_ko"], name_en=raw["name_en"],
            desc_ko=raw.get("desc_ko", ""), wiki_starting_ball=start,
        )

    data = GameData(
        items=items, characters=characters, recipes=recipes,
        rules=_read_json("rules.json", data_dir), ui_text=text.get("ui", {}), source=text.get("source", {}),
        buildings=text.get("buildings", {}),
    )
    try:
        data.community = _read_json("community.json", data_dir)
    except (OSError, ValueError):
        data.community = {}
    _finish(data)
    return data


def _finish(data: "GameData"):
    """위키 자료가 없는 항목은 게임 설명으로 태그, 저장된 게임 catalog(레시피·수치)가 있으면 적용."""
    data.apply_level_props({})
    # 테스트는 BXP_CATALOG_FILE 로 고정 자료(tests/fixtures/game_recipes.json)를 쓴다 — PC 마다 결과가 같게
    path = os.environ.get("BXP_CATALOG_FILE") or os.path.join(resolve_data_dir(), CATALOG_FILE)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                apply_catalog(data, json.load(f))
        except (OSError, ValueError, KeyError, TypeError):
            pass


def apply_catalog(data: "GameData", catalog: dict) -> int:
    """게임 연동 catalog 적용: 게임 안 레시피(위키 대신), 레벨별 수치, 단독 최대 레벨. 적용한 레시피 수."""
    from .tracking.bridge_adapter import catalog_recipes, catalog_schedules
    props = {}
    for kind in ("balls", "passives"):
        for e in catalog.get(kind) or []:
            iid = data.item_by_log_id(e.get("type") or "")
            if iid and isinstance(e.get("lvl_props"), list):
                props[iid] = e["lvl_props"]
    if props:
        data.apply_level_props(props)
    if "levels" in catalog:
        data.level_schedules = catalog_schedules(catalog)
    n = data.apply_game_recipes(catalog_recipes(catalog, data))
    k = catalog.get("max_solo_lvl")
    if isinstance(k, int) and k >= 0:
        data.set_observed_max_level("ball", k + 1)
        data.set_observed_max_level("passive", k + 1)
    return n


def save_catalog(catalog: dict):
    """다음 실행부터 게임에 연결하기 전에도 레시피를 쓰도록 사용자 자료 폴더에 저장 (배포하지 않음)."""
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        with open(os.path.join(USER_DATA_DIR, CATALOG_FILE), "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False)
    except OSError:
        pass
