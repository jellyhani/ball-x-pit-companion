"""도우미 버전과 빌드 식별자. 게임 연동 플러그인 버전과 별도로 관리한다."""

import json
from pathlib import Path

APP_VERSION = "0.1.0-beta.5"
WINDOWS_VERSION = (0, 1, 0, 5)


def build_info():
    try:
        value = json.loads(
            (Path(__file__).resolve().parents[1] / "build-info.json").read_text(encoding="utf-8")
        )
        if isinstance(value, dict) and value.get("version") == APP_VERSION:
            return value
    except (OSError, ValueError):
        pass
    return {"version": APP_VERSION, "revision": "source", "dirty": None}
