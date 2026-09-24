"""장기 설정 저장. 런 데이터(보유 볼 등)는 여기에 저장하지 않는다."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields

log = logging.getLogger(__name__)

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BallxPitCompanion")


@dataclass
class Settings:
    watch_enabled: bool = True          # 자동 감시
    font_scale: float = 1.0             # 1.0 보통, 1.2 크게
    hud_auto_show: bool = True          # 선택창에서 HUD 자동 표시
    card_outline: bool = True           # 추천 카드에 얇은 테두리
    hide_from_capture: str = "auto"     # auto(화면 인식 중에만) | always | never
    hud_offset_x: int = 0               # 자동 배치 위치에서 사용자가 옮긴 만큼 (논리 픽셀)
    hud_offset_y: int = 0
    scan_interval_ms: int = 700
    hud_compact: bool = False           # HUD 간단히 (F7): 추천·이유 한 줄·삭제만
    auto_install_mod: bool = True       # 게임 연동 모드가 없거나 옛 버전이면 게임이 꺼져 있을 때 자동 설치
    start_with_windows: bool = False    # 윈도우 로그인 시 도우미 자동 실행 (켤 때만 등록)
    dps_meter: bool = True              # 전투 중 초당 피해 창 (게임 연동 1.2 이상)
    dps_window_s: int = 10              # 초당 피해를 재는 창 길이 (게임 시간, 초)
    dps_corner: str = "right"           # right | left : 게임 창 위쪽 어느 구석에 둘지

    @classmethod
    def path(cls) -> str:
        return os.path.join(APP_DIR, "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        try:
            with open(cls.path(), encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("설정 파일을 읽지 못해 기본값을 씁니다", exc_info=True)
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        s.font_scale = 1.2 if s.font_scale > 1.05 else 1.0
        s.scan_interval_ms = max(300, min(3000, int(s.scan_interval_ms)))
        return s

    def save(self):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = self.path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path())
