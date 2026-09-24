"""게임 연동 모드 감시: 설치 상태·버전 확인, 게임이 꺼져 있을 때 자동 설치, 업데이트 뒤 연동 끊김 경고.

10초마다 백그라운드 스레드에서 확인한다 (파일 존재·버전 리소스·tasklist 만 읽는다).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import mod_installer as mi

log = logging.getLogger(__name__)

NO_BRIDGE_WARN_S = 60.0   # 게임이 켜진 뒤 이만큼 연동이 없으면 경고 (첫 실행 때 BepInEx 준비가 오래 걸림)


class ModGuard(QObject):
    status = pyqtSignal(str, str)     # (설명, 상태: ok | wait | warn | error)
    notice = pyqtSignal(str)          # 트레이 알림

    def __init__(self, settings, data_build: Optional[str], bridge_connected: Callable[[], bool]):
        super().__init__()
        self.settings = settings
        self.data_build = data_build
        self.bridge_connected = bridge_connected
        self.last: Optional[mi.ModStatus] = None
        self.text = "확인 전"
        self._busy = threading.Lock()
        self._game_since: Optional[float] = None
        self._warned_build = False
        self.timer = QTimer(self)
        self.timer.setInterval(10_000)
        self.timer.timeout.connect(self.check_soon)

    def start(self):
        self.timer.start()
        self.check_soon()

    def stop(self):
        self.timer.stop()

    def check_soon(self, force_install: bool = False):
        if self._busy.locked():
            return
        connected = self.bridge_connected()   # Qt 스레드에서 읽어 둔다
        threading.Thread(target=self._run, args=(force_install, connected), daemon=True,
                         name="ModGuard").start()

    def install_now(self):
        self.check_soon(force_install=True)

    # ---- 백그라운드 ----
    def _run(self, force_install: bool, connected: bool):
        with self._busy:
            try:
                self._check(force_install, connected)
            except Exception:
                log.exception("게임 연동 모드 확인 실패")

    def _emit(self, text: str, tone: str):
        if text != self.text:
            log.info("게임 연동 모드: %s", text)
        self.text = text
        self.status.emit(text, tone)

    def _check(self, force_install: bool, connected: bool):
        st = mi.check()
        self.last = st
        now = time.monotonic()
        if st.running:
            self._game_since = self._game_since or now
        else:
            self._game_since = None
        notes = []
        if st.build_id and self.data_build and st.build_id != self.data_build:
            notes.append(f"게임이 업데이트됨 (빌드 {self.data_build} → {st.build_id}). "
                         "이름·아이콘은 이전 빌드 기준이고, 연동은 아래 상태로 확인합니다")
            if not self._warned_build:
                self._warned_build = True
                log.warning("게임 빌드 변경: %s → %s", self.data_build, st.build_id)
        suffix = ("\n" + "\n".join(notes)) if notes else ""

        if not st.game_dir:
            self._emit(st.summary + suffix, "error")
            return
        if st.needs_install:
            if not (self.settings.auto_install_mod or force_install):
                self._emit(f"{st.summary} — 설정에서 자동 설치가 꺼져 있음{suffix}", "warn")
                return
            if not st.vendor_ok:
                self._emit(f"{st.summary} — 설치 파일(vendor/bepinex)이 없음{suffix}", "error")
                return
            if st.running:
                self._emit(f"{st.summary} — 게임을 끄면 자동으로 설치합니다{suffix}", "wait")
                return
            try:
                new = mi.install(st, say=lambda m: log.info("설치: %s", m))
                self.last = new
                self.notice.emit(f"게임 연동 {mi.PLUGIN_VERSION} 설치 완료 — 다음 게임 실행부터 적용")
                self._emit(new.summary + suffix, "ok")
            except mi.InstallError as e:
                self._emit(f"설치 실패: {e}{suffix}", "error")
            return
        if st.enabled is False:
            self._emit(st.summary + " — 켜려면 tools\\bepinex\\bridge.ps1 enable" + suffix, "warn")
            return
        if st.running and not connected and self._game_since and now - self._game_since > NO_BRIDGE_WARN_S:
            problems = mi.bepinex_log_problems(st.game_dir)
            why = ("플러그인 오류: " + problems[-1]) if problems else \
                "게임 업데이트 직후라면 첫 실행 준비(interop 생성)가 끝날 때까지 기다려 주세요"
            self._emit(f"{st.summary} · 게임은 켜져 있는데 연동이 안 됨 — {why}{suffix}", "warn")
            return
        self._emit(st.summary + (" · 연결됨" if connected else "") + suffix, "ok")
