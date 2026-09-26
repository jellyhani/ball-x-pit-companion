"""테스트 패키지.

게임에서 추출한 자료(게임 문구·아이콘)는 배포하지 않는다 — 없으면 그 자료가 필요한 테스트는 실패 대신 건너뛴다.
자료 만들기: .venv\Scripts\python.exe tools\setup_data.py
"""
import os
import shutil
import tempfile
import unittest

# 컨트롤러 생성자부터 임시 자료 폴더를 사용한다. 생성 뒤 경로를 바꾸면 사용자 로그 정리가 먼저 실행된다.
_app_temp = tempfile.TemporaryDirectory(prefix="bxp-tests-")
os.environ["BXP_APP_DIR"] = _app_temp.name

# 테스트는 한국어 원문 기준으로 확인한다 — 영어 윈도우(CI 등)에서 화면 문구가 번역돼 실패하지 않게.
# 환경 변수라 계산용 별도 프로세스(SimWorker)에도 그대로 간다. 다른 언어로 확인하려면 BXP_LANG=en 으로 실행.
os.environ.setdefault("BXP_LANG", "ko")
# 해금 목록(tracking/unlocks.py)은 읽지도 저장하지도 않는다 — 이 PC 의 해금 상태나 앞 테스트에 따라 결과가 달라지지 않게
os.environ["BXP_UNLOCKS_FILE"] = "-"
_catalog = os.path.join(_app_temp.name, "game_recipes.json")
shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "game_recipes.json"), _catalog)
os.environ["BXP_CATALOG_FILE"] = _catalog

import src.gamedata as _gd  # noqa: E402

HAS_GAME_DATA = os.path.exists(os.path.join(_gd.DATA_DIR, "game_text_ko.json"))

if not HAS_GAME_DATA:
    def _skip_load(*_a, **_k):
        raise unittest.SkipTest("게임 자료 없음 — tools/setup_data.py 로 게임 파일에서 먼저 추출하세요")
    _gd.load_game_data = _skip_load
