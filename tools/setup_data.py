"""처음 설치: 사용자의 게임 파일에서 도우미가 쓰는 자료를 추출한다 (게임 자료는 배포하지 않는다).

- 게임 문구(이름·설명, 공식 한국어 번역 테이블) → %LOCALAPPDATA%\\BallxPitCompanion\\gamedata\\game_text_ko.json
- 볼·패시브 아이콘, 캐릭터 초상화 (화면 인식 예비 경로용) → 같은 폴더 icons\\, portraits\\
게임 파일은 읽기만 한다. 게임이 업데이트되면 다시 실행하면 된다.
exe 에서도 쓰도록 같은 프로세스 안에서 돈다 (별도 파이썬을 부르지 않음).

    .venv\\Scripts\\python.exe tools\\setup_data.py [게임 폴더]
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def extract(game_dir: Optional[str] = None, say: Callable[[str], None] = print) -> Tuple[bool, str]:
    """게임 문구 + 아이콘을 사용자 자료 폴더로. (성공, 안내 문구)."""
    import src.gamedata as gd
    from tools import extract_game_text as egt
    out = gd.USER_DATA_DIR
    egt.OUT_DIR = out
    argv = ["setup"] + ([game_dir] if game_dir else [])
    try:
        if egt.main(argv) != 0:
            return False, "게임 폴더를 찾지 못했습니다. Steam 판 BALL x PIT 이 설치돼 있는지 확인하세요."
    except SystemExit as e:                      # 게임 버전이 바뀌어 번역 표 구조가 다를 때
        return False, str(e)
    gd.DATA_DIR = gd.resolve_data_dir()          # 이후에 불러오는 모듈이 새 자료 폴더를 쓰게
    try:
        import UnityPy  # noqa: F401
        from tools import extract_icons as ei
    except ImportError:
        return True, "게임 문구 추출 완료 (UnityPy 가 없어 아이콘은 건너뜀 — 게임 연동만 쓰면 없어도 됨)"
    ei.OUT_DIR = out
    try:
        if ei.main(argv) != 0:
            return True, "게임 문구 추출 완료, 아이콘 추출 실패 (게임 연동 기능은 그대로 사용 가능)"
    except Exception as e:                       # noqa: BLE001 — 아이콘은 예비 경로라 실패해도 계속
        say(f"아이콘 추출 실패: {e}")
        return True, "게임 문구 추출 완료, 아이콘 추출 실패 (게임 연동 기능은 그대로 사용 가능)"
    return True, "게임 자료 추출 완료"


def main(argv) -> int:
    ok, msg = extract(argv[1] if len(argv) > 1 else None)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
