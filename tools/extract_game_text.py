"""게임 설치 폴더의 공식 번역 테이블(I2 Localization)에서 이름·설명을 읽어 JSON으로 저장한다.

게임 파일은 읽기만 한다. 출력: data/game_text_ko.json

사용법:
    .venv\\Scripts\\python.exe tools\\extract_game_text.py [게임 폴더]

게임 폴더를 생략하면 Steam 라이브러리에서 BALL x PIT(appid 2062430)를 찾는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import struct
import sys
from typing import Dict, List, Optional, Tuple

APP_ID = "2062430"
LANG_COUNT = 16          # 현재 빌드의 언어 수. 다르면 구조가 바뀐 것이므로 중단한다.
LANG_EN, LANG_KO = 0, 3  # 언어 배열 순서: 영어, 중국어 간체, 일본어, 한국어 ... (resources.assets 확인)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 출력 폴더: 기본은 저장소 data/ (개발용). --user 면 사용자 자료 폴더 (공개판 설치 — 게임 자료는 배포하지 않음)
OUT_DIR = os.path.join(ROOT, "data")
if "--user" in sys.argv:
    sys.argv.remove("--user")
    OUT_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BallxPitCompanion", "gamedata")

# 레벨업 화면 판별과 파싱에 쓰는 UI 문구 키
UI_KEYS = [
    "select_upgrade_level_up", "Select an upgrade", "free_reroll", "Re-roll Upgrades",
    "prompt_banish", "Banish", "skip_prompt", "Skip", "New Ball", "New Passive",
    "Upgrade Ball", "Upgrade Passive", "level_num_oneline", "Level Up",
    "Fusion Available", "Fuse!", "Fusion", "Evolution", "ignore_fuser_desc",
    "Select Character", "char_none_selected", "Paused", "Pause",
    "tut_evolution", "tut_fusion", "tut_fission",
]


def find_steam_game_dir() -> Optional[str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
    except OSError:
        return None
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        with open(vdf, encoding="utf-8", errors="replace") as f:
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    for lib in libs:
        manifest = os.path.join(lib, "steamapps", f"appmanifest_{APP_ID}.acf")
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                return os.path.join(lib, "steamapps", "common", m.group(1))
    return None


def read_build_id(game_dir: str) -> Optional[str]:
    lib = os.path.dirname(os.path.dirname(game_dir))  # .../steamapps
    manifest = os.path.join(lib, f"appmanifest_{APP_ID}.acf")
    if not os.path.exists(manifest):
        return None
    with open(manifest, encoding="utf-8", errors="replace") as f:
        m = re.search(r'"buildid"\s+"(\d+)"', f.read())
    return m.group(1) if m else None


def parse_terms(data: bytes) -> Dict[str, List[str]]:
    """Unity 직렬화 문자열(int32 길이 + UTF-8 + 4바이트 정렬)로 된 I2 용어 목록을 찾는다.

    용어 구조: 이름 문자열, int32 TermType, int32 언어 수(16), 언어별 문자열 16개.
    """
    def read_str(p: int) -> Tuple[bytes, int]:
        n = struct.unpack_from("<i", data, p)[0]
        if n < 0 or n > 20000:
            raise ValueError("bad length")
        s = data[p + 4:p + 4 + n]
        p = (p + 4 + n + 3) & ~3
        return s, p

    terms: Dict[str, List[str]] = {}
    for m in re.finditer(rb"[A-Za-z][A-Za-z0-9 _/\-\.'!?]{1,80}", data):
        p = m.start() - 4
        if p < 0 or struct.unpack_from("<i", data, p)[0] != m.end() - m.start():
            continue
        try:
            name, q = read_str(p)
            q += 4  # TermType
            if struct.unpack_from("<i", data, q)[0] != LANG_COUNT:
                continue
            q += 4
            langs = []
            for _ in range(LANG_COUNT):
                s, q = read_str(q)
                langs.append(s.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, struct.error):
            continue
        terms[name.decode("ascii")] = langs
    return terms


def _template_values(template_en: str, text_en: str) -> Optional[Dict[str, str]]:
    """영문 템플릿('Inflicts {[bleed_amt]} stacks')을 위키 영문 설명에 맞춰 수치를 뽑는다."""
    names = re.findall(r"\{\[(\w+)\]\}", template_en)
    if not names:
        return {}
    plain = re.sub(r"<[^>]+>", "", template_en)
    pattern = ""
    for part in re.split(r"(\{\[\w+\]\})", plain):
        if re.fullmatch(r"\{\[\w+\]\}", part):
            pattern += r"(.+?)"
        else:
            pattern += re.escape(part).replace(r"\ ", r"\s*")
    m = re.match(pattern + r"\s*\.?\s*$", text_en.strip(), flags=re.IGNORECASE)
    if m:
        values: Dict[str, str] = {}
        for name, val in zip(names, m.groups()):
            if values.get(name, val) != val:
                return None
            values[name] = val.strip()
        return values
    return _ordered_number_values(plain, text_en)


_NUM = r"\d+(?:\.\d+)?%?"


def _ordered_number_values(template_en: str, text_en: str) -> Optional[Dict[str, str]]:
    """문장 표현이 조금 달라도 숫자 순서가 같으면 자리표시자에 차례로 대입한다.

    템플릿에 원래 적힌 숫자(예: 'up to 1 leech')는 위키 숫자와 같아야 하고, 개수가 다르면 포기한다.
    """
    slots = re.findall(r"\{\[(\w+)\]\}|(" + _NUM + ")", template_en)
    numbers = re.findall(_NUM, text_en)
    if len(slots) != len(numbers):
        return None
    values: Dict[str, str] = {}
    for (name, literal), num in zip(slots, numbers):
        if literal:
            if literal.rstrip("%") != num.rstrip("%"):
                return None
            continue
        if re.search(r"\{\[" + name + r"\]\}\s*%", template_en):
            num = num.rstrip("%")
        if values.get(name, num) != num:
            return None
        values[name] = num
    return values


def fill_template(template_ko: str, values: Dict[str, str]) -> str:
    text = re.sub(r"<[^>]+>", "", template_ko)
    return re.sub(r"\{\[(\w+)\]\}", lambda m: values.get(m.group(1), "?"), text)


def build(game_dir: str) -> dict:
    assets = os.path.join(game_dir, "Balls_Data", "resources.assets")
    with open(assets, "rb") as f:
        data = f.read()
    terms = parse_terms(data)
    if len(terms) < 500:
        raise SystemExit(f"번역 용어를 {len(terms)}개만 찾았습니다. 게임 버전이 바뀌었을 수 있습니다.")

    wiki = {}
    for fn in ("balls_db.json", "passives_db.json"):      # 선택 자료 (없으면 게임 문구만)
        path = os.path.join(ROOT, "data", fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for row in json.load(f):
                wiki[row["name"]] = row

    items: Dict[str, dict] = {}
    unmatched_numbers = []
    for key, langs in terms.items():
        m = re.fullmatch(r"Generated/(hupg|pass)_(\w+)_name", key)
        if not m:
            continue
        prefix, slug = m.groups()
        kind = "ball" if prefix == "hupg" else "passive"
        desc = terms.get(f"Generated/{prefix}_{slug}_desc", [""] * LANG_COUNT)
        name_en = langs[LANG_EN]
        entry = {
            "id": f"{kind}:{slug}",
            "kind": kind,
            "slug": slug,
            "name_en": name_en,
            "name_ko": langs[LANG_KO],
            "desc_en_template": desc[LANG_EN],
            "desc_ko_template": desc[LANG_KO],
        }
        w = wiki.get(name_en)
        if w and desc[LANG_EN]:
            values = _template_values(desc[LANG_EN], w.get("description", ""))
            if values is not None:
                entry["desc_ko"] = fill_template(desc[LANG_KO], values)
                entry["desc_values_source"] = "wiki" if values else "game"
            else:
                unmatched_numbers.append(name_en)
                entry["desc_ko"] = fill_template(desc[LANG_KO], {})
                entry["desc_values_source"] = "missing"
        else:
            entry["desc_ko"] = fill_template(desc[LANG_KO], {})
            entry["desc_values_source"] = "game" if "{[" not in desc[LANG_KO] else "missing"
        items[entry["id"]] = entry

    characters: Dict[str, dict] = {}
    for key, langs in terms.items():
        m = re.fullmatch(r"Generated/char_(\w+)_name", key)
        if not m:
            continue
        slug = m.group(1)
        desc = terms.get(f"Generated/char_{slug}_desc", [""] * LANG_COUNT)
        characters[f"char:{slug}"] = {
            "id": f"char:{slug}",
            "slug": slug,
            "name_en": langs[LANG_EN],
            "name_ko": langs[LANG_KO],
            "desc_ko": re.sub(r"<[^>]+>", "", desc[LANG_KO]),
            "desc_en": re.sub(r"<[^>]+>", "", desc[LANG_EN]),
        }

    buildings: Dict[str, dict] = {}
    for key, langs in terms.items():
        m = re.fullmatch(r"Generated/bld_(\w+)_name", key)
        if not m:
            continue
        slug = m.group(1)
        desc = terms.get(f"Generated/bld_{slug}_desc", [""] * LANG_COUNT)
        upg = terms.get(f"Generated/bld_{slug}_upgdesc", [""] * LANG_COUNT)
        buildings[slug] = {
            "slug": slug,
            "name_en": langs[LANG_EN],
            "name_ko": langs[LANG_KO],
            "desc_ko": re.sub(r"<[^>]+>", "", fill_template(desc[LANG_KO], {})),
            "upgrade_ko": re.sub(r"<[^>]+>", "", fill_template(upg[LANG_KO], {})),
        }

    ui = {k: {"en": terms[k][LANG_EN], "ko": terms[k][LANG_KO]} for k in UI_KEYS if k in terms}

    return {
        "source": {
            "game": "BALL x PIT",
            "steam_build_id": read_build_id(game_dir),
            "file": "Balls_Data/resources.assets (I2 Localization)",
            "extracted_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "note": "이름·설명 문구는 게임 파일 원문. 설명의 수치는 위키 DB에서 대입했으며 게임 수치로 검증되지 않음.",
            "desc_numbers_unmatched": sorted(unmatched_numbers),
        },
        "items": dict(sorted(items.items())),
        "characters": dict(sorted(characters.items())),
        "buildings": dict(sorted(buildings.items())),
        "ui": ui,
    }


def main(argv: List[str]) -> int:
    game_dir = argv[1] if len(argv) > 1 else find_steam_game_dir()
    if not game_dir or not os.path.isdir(game_dir):
        print("게임 폴더를 찾지 못했습니다. 경로를 인수로 지정하세요.")
        return 1
    result = build(game_dir)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "game_text_ko.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    s = result["source"]
    print(f"저장: {out}")
    print(f"빌드 {s['steam_build_id']} · 볼 {sum(1 for i in result['items'].values() if i['kind'] == 'ball')}개 · "
          f"패시브 {sum(1 for i in result['items'].values() if i['kind'] == 'passive')}개 · 캐릭터 {len(result['characters'])}명")
    if s["desc_numbers_unmatched"]:
        print(f"수치 대입 실패 {len(s['desc_numbers_unmatched'])}개: {', '.join(s['desc_numbers_unmatched'][:12])} ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
