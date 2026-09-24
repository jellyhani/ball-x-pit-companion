"""처음 설치: 사용자의 게임 파일에서 도우미가 쓰는 자료를 추출한다 (게임 자료는 배포하지 않는다).

- 게임 문구(이름·설명, 공식 한국어 번역 테이블) → %LOCALAPPDATA%\\BallxPitCompanion\\gamedata\\game_text_ko.json
- 볼·패시브 아이콘, 캐릭터 초상화 (화면 인식 예비 경로용) → 같은 폴더 icons\\, portraits\\
게임 파일은 읽기만 한다. 게임이 업데이트되면 다시 실행하면 된다.

    .venv\\Scripts\\python.exe tools\\setup_data.py [게임 폴더]
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(tool: str, extra) -> int:
    cmd = [sys.executable, os.path.join(ROOT, "tools", tool), "--user"] + list(extra)
    print(f"> {tool}")
    return subprocess.call(cmd, cwd=ROOT)


def main(argv) -> int:
    extra = argv[1:]
    if run("extract_game_text.py", extra) != 0:
        print("게임 문구를 추출하지 못했습니다. 게임 폴더를 인자로 주거나 Steam 설치를 확인하세요.")
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("UnityPy 가 없어 아이콘 추출을 건너뜁니다 (게임 연동만 쓰면 없어도 됩니다).")
        return 0
    if run("extract_icons.py", extra) != 0:
        print("아이콘 추출 실패 — 게임 연동 기능은 그대로 쓸 수 있습니다.")
    print("완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
