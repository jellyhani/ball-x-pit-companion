"""게임 에셋에서 볼·패시브 아이콘과 캐릭터 초상화를 꺼내 data/icons, data/portraits 에 저장한다.

선택창 카드에는 이름이 없고 아이콘만 나오므로, 화면의 아이콘을 이 원본과 비교해 항목을 알아낸다.
게임 파일은 읽기만 한다. 개발용 도구이며 UnityPy 가 필요하다(requirements-dev.txt).

    .venv\\Scripts\\python.exe tools\\extract_icons.py [게임 폴더]
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.extract_game_text import OUT_DIR, find_steam_game_dir, read_build_id  # noqa: E402

# 스프라이트 이름이 게임 내부 키와 다른 경우 (스프라이트 접미사 → 항목 slug)
ITEM_ALIASES = {
    "firefly": "lightningbug",
    "hearteater": "heartswallower",
    "timestop": "timefreeze",
    "arrow": "arrowoffate",
    "detonator": "remotedetonator",
    "dumbbell": "platinumdumbbell",
    "hammer": "inglorioushammer",
    "impaler": "deadeyesimpaler",
    "rapier": "fullmetalrapier",
    "stopwatch": "argentstopwatch",
    "tire": "ardenttire",
}
# 초상화 스프라이트 이름 → 캐릭터 slug
PORTRAITS = {
    "warrior": "default",
    "itchy_finger": "itchyfinger",
    "cohabitants": "cohabitants",
    "empty_nester_portrait": "emptynester",
    "flagellant": "flagellant",
    "cogitator": "cogitator",
    "repentant": "recaller",
    "shade_portrait": "shade",
    "shield_bearer": "brickhead",
    "sisyphus": "sisyphus",
    "spendthrift_portrait": "spendthrift",
    "tactician_portrait": "tactician",
    "the_embedded_portrait": "embedded",
    "the_radical_portrait": "radicalai",
    "wimp_portrait": "wimp",
    "influencer_portrait": "influencer",
    "mad_scientist": "physicist",
    "portrait_ballbearer": "backpacker",
    "portrait_carouser": "carouser",
    "portrait_falconer": "falconer",
    "portrait_hoarder": "packrat",
    "portrait_tiptoer": "tiptoer",
    "portrait_tunneller": "tunneller",
}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main(argv) -> int:
    import UnityPy

    game_dir = argv[1] if len(argv) > 1 else find_steam_game_dir()
    if not game_dir:
        print("게임 폴더를 찾지 못했습니다.")
        return 1
    with open(os.path.join(OUT_DIR, "game_text_ko.json"), encoding="utf-8") as file_handle:
        text = json.load(file_handle)
    by_key = {}
    for it in text["items"].values():
        by_key.setdefault(_key(it["name_en"]), it["id"])
        by_key.setdefault(_key(it["slug"]), it["id"])

    env = UnityPy.load(os.path.join(game_dir, "Balls_Data"))
    icon_dir = os.path.join(OUT_DIR, "icons")
    portrait_dir = os.path.join(OUT_DIR, "portraits")
    os.makedirs(icon_dir, exist_ok=True)
    os.makedirs(portrait_dir, exist_ok=True)

    icons, portraits = {}, {}
    for obj in env.objects:
        if obj.type.name != "Sprite":
            continue
        try:
            sp = obj.read()
        except Exception:
            continue
        name = sp.m_Name
        match = re.match(r"^(ball_icon_|postlaunch_balls_|passive_icon_|passive_)(.+)$", name)
        if match:
            prefix, rest = match.groups()
            k = _key(rest)
            item_id = by_key.get(ITEM_ALIASES.get(k, k))
            if item_id is None:
                continue
            kind_ok = item_id.startswith("ball:") == prefix.startswith(("ball", "postlaunch"))
            if not kind_ok or item_id in icons:
                continue
            sp.image.save(os.path.join(icon_dir, item_id.replace(":", "_") + ".png"))
            icons[item_id] = name
        elif name in PORTRAITS and f"char:{PORTRAITS[name]}" not in portraits:
            character_id = f"char:{PORTRAITS[name]}"
            sp.image.save(os.path.join(portrait_dir, character_id.replace(":", "_") + ".png"))
            portraits[character_id] = name

    missing_items = sorted(set(text["items"]) - set(icons))
    missing_chars = sorted(set(text["characters"]) - set(portraits))
    manifest = {
        "steam_build_id": read_build_id(game_dir),
        "icons": dict(sorted(icons.items())),
        "portraits": dict(sorted(portraits.items())),
        "missing_icons": missing_items,
        "missing_portraits": missing_chars,
    }
    with open(os.path.join(OUT_DIR, "icon_manifest.json"), "w", encoding="utf-8") as file_handle:
        json.dump(manifest, file_handle, ensure_ascii=False, indent=1)
    print(f"아이콘 {len(icons)}개, 초상화 {len(portraits)}개 저장")
    if missing_items or missing_chars:
        print("빠진 항목:", missing_items, missing_chars)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
