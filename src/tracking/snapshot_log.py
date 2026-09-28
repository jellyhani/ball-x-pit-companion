"""게임 연동 원본 저장: 선택창 스냅샷과 레시피 표(catalog)를 날짜별 파일로 남긴다 (이 PC에만).

게임 업데이트 뒤 tools/regress.py 로 같은 스냅샷을 다시 돌려 추천이 바뀌었는지 확인하는 데 쓴다.
30일이 지난 파일은 지운다.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

KEEP_DAYS = 30


class SnapshotLog:
    def __init__(self, folder: str):
        self.folder = folder
        self._catalog: Optional[dict] = None
        self._catalog_written_to: Optional[str] = None
        self._cleanup()

    def _path(self) -> str:
        return os.path.join(self.folder, time.strftime("%Y-%m-%d") + ".jsonl")

    def set_catalog(self, catalog_msg: dict):
        self._catalog = catalog_msg

    def _write(self, obj: dict):
        path = self._path()
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(path, "a", encoding="utf-8") as file_handle:
                if self._catalog is not None and self._catalog_written_to != path:
                    file_handle.write(json.dumps(self._catalog, ensure_ascii=False) + "\n")
                    self._catalog_written_to = path
                file_handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("스냅샷 저장 실패")

    def add(self, snapshot: dict):
        self._write(snapshot)

    def _cleanup(self):
        try:
            cutoff = time.time() - KEEP_DAYS * 86400
            for name in os.listdir(self.folder):
                path = os.path.join(self.folder, name)
                if name.endswith(".jsonl") and os.path.getmtime(path) < cutoff:
                    os.remove(path)
        except OSError:
            pass
