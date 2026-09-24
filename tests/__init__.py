"""테스트 패키지.

게임에서 추출한 자료(게임 문구·아이콘)는 배포하지 않는다 — 없으면 그 자료가 필요한 테스트는 실패 대신 건너뛴다.
자료 만들기: .venv\Scripts\python.exe tools\setup_data.py
"""
import os
import unittest

os.environ["BXP_CATALOG_FILE"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "game_recipes.json")

import src.gamedata as _gd  # noqa: E402

HAS_GAME_DATA = os.path.exists(os.path.join(_gd.DATA_DIR, "game_text_ko.json"))

if not HAS_GAME_DATA:
    def _skip_load(*_a, **_k):
        raise unittest.SkipTest("게임 자료 없음 — tools/setup_data.py 로 게임 파일에서 먼저 추출하세요")
    _gd.load_game_data = _skip_load
