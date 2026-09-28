"""해금된 볼·패시브 기록 (게임 연동).

게임은 해금 목록을 따로 보내지 않지만, 강화 선택창마다 '뽑힐 수 있는 후보 목록'(pool)을 보낸다. 후보 목록에는
이미 가진 것·화면에 나온 카드·삭제한 것이 빠져 있으므로, 그것들까지 합치면 그 시점에 해금된 전체가 된다.
실제 확인(2026-09-26): 기본 볼 20개 중 19개가 나오고, 주취자를 해금하지 않은 사용자에게는 그 기본 볼(매혹)만 없었다.
해금은 되돌아가지 않으므로 누적해서 저장한다 — 잠긴 재료가 든 진화를 추천에서 빼는 데 쓴다 (GameData.available).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable, Optional, Set

from ..services.settings import APP_DIR

log = logging.getLogger(__name__)


def default_path() -> str:
    """BXP_UNLOCKS_FILE 로 바꿀 수 있다. '-' 면 읽지도 저장하지도 않는다 (테스트)."""
    return os.environ.get("BXP_UNLOCKS_FILE") or os.path.join(APP_DIR, "unlocked_items.json")


def load(path: str) -> Optional[Set[str]]:
    """저장된 해금 목록. 파일이 없으면 None (아직 모름 — 걸러 내지 않는다)."""
    if path == "-":
        return None
    try:
        with open(path, encoding="utf-8") as file_handle:
            items = json.load(file_handle).get("items")
        return set(items) if isinstance(items, list) and items else None
    except (OSError, ValueError, AttributeError):
        return None


def save(path: str, items: Iterable[str]):
    if path == "-":
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file_handle:
            json.dump({"items": sorted(items)}, file_handle, ensure_ascii=False, indent=0)
    except OSError:
        log.exception("해금 목록 저장 실패")
