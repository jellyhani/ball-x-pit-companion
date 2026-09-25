"""상태 수집 → 선택창 감지 → 추천 → HUD 표시 → 실제 선택 반영을 잇는 컨트롤러.

권위 있는 신호의 우선순위
  1. 게임 연동(BepInEx 플러그인): 선택지·보유 항목·레벨·골드·캐릭터를 게임에서 직접 받는다.
     연결되어 있으면 화면 인식은 쉰다.
  2. 게임 로그(Player.log): 런 시작·종료, 새로고침, 게임 버전
  3. 게임 창 화면 인식: 연동이 없을 때의 예비 경로
  4. 마우스 클릭 위치: 화면 인식 경로에서 어느 카드를 골랐는지의 보조 증거
  5. 사용자의 수동 보정: 인식이 틀렸을 때만
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections import deque
from dataclasses import asdict
from typing import Deque, Dict, List, Optional

from PySide6.QtCore import QObject, QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication

from .domain import SCREEN_LABEL, CardLabel, Click, PickOutcome, ScreenKind, ScreenObservation
from .engine.expedition import ExpeditionAdvice, advise as advise_expedition
from .engine.fusion import FusionAdvisor, FusionRecommendation
from .engine.recommender import Recommendation, Recommender, card_badge, card_verdict
from .gamedata import GameData, load_game_data
from .recognition.ocr import check_ocr
from .services import autostart
from .services import game_window as gw
from .services.mod_guard import ModGuard
from .services.bridge_client import BridgeClient
from .services.input_watch import InputWatcher
from .services.log_watcher import LogEvent, PlayerLogWatcher
from .services.recognition_worker import RecognitionService, ScanResult
from .services.settings import APP_DIR, Settings
from .tracking.bridge_adapter import BridgeState, catalog_recipes, catalog_schedules, convert, infer_pick
from .tracking.choice_tracker import ChoiceTracker, TrackerEvent
from .tracking.meta_state import MetaState, parse_meta
from .tracking.draw_stats import DrawStats
from .tracking.run_history import RunRecorder
from .tracking.snapshot_log import SnapshotLog
from .tracking.run_state import RunState
from .ui import geometry as geo
from .ui import tokens as tk
from .tracking.dps import DpsTracker
from .ui.card_highlight import CardHighlight
from .ui.dps_meter import DpsMeter
from .ui.base_overlay import BaseOverlay
from .ui.layout_window import LayoutWindow
from .engine.harvest import (AimRange, HarvestLog, advise_harvest, advise_resource_ratio, gold_bounce_tip,
                             layout_key, need_resource, unfinished_buildings)
from .services.sim_worker import SimWorker
from .engine.base_advisor import suggest as suggest_base
from .ui.control_window import ControlWindow
from .ui.hud import RecommendationHud
from .ui.tray import Tray, app_icon

log = logging.getLogger(__name__)


CARD_LABEL_KO = {CardLabel.NEW: "신규", CardLabel.UPGRADE: "강화"}


class AppController(QObject):
    def __init__(self, app: QApplication, data: Optional[GameData] = None):
        super().__init__()
        self.app = app
        self.data = data or load_game_data()
        self.settings = Settings.load()
        self.run = RunState()
        self.tracker = ChoiceTracker()
        self.recommender = Recommender(self.data)
        self.fusion = FusionAdvisor(self.data)
        self.fusion_rec: Optional[FusionRecommendation] = None
        self.char_combo = None                    # 캐릭터 선택 화면의 조합 추천 (알선소가 있을 때)
        self._char_combo_key = None                # (state, char1, char2) — 바뀔 때만 다시 계산
        self._loadout_seen = (None, None)           # 로드아웃 화면이 잠깐 비활성화돼 char1/char2 를 못 받은 폴링을 버틴다
        self._loadout_miss = 0
        self.base_advice = None                    # 기지에서 메뉴 없이 서 있을 때 뭘 지을지·철거할지
        self._base_advice_key = None

        self.ocr_status = check_ocr()
        self.user_hidden = False
        self.recommendation: Optional[Recommendation] = None
        self.last_result: Optional[ScanResult] = None
        self.window: Optional[gw.GameWindow] = None
        self.base_state = ""
        self.force_until = 0.0
        self.last_reroll_at = -10.0
        self.pending_save = False
        self.stale_results = 0
        self.timings: Deque[Dict[str, float]] = deque(maxlen=50)

        self.hud = RecommendationHud(self.data, self.settings.font_scale)
        self.highlight = CardHighlight()
        self.dps = DpsTracker(self.settings.dps_window_s)
        self.dps_meter = DpsMeter(self.data, self.settings.font_scale)
        self._dps_state = ""
        self._dps_drawn_at = 0.0
        self.battle_info = None
        self.base_overlay = BaseOverlay()
        self.dump_dir = APP_DIR       # 진단용 파일(live_state·live_meta·채집 궤적) 위치 — 테스트는 임시 폴더로 바꾼다
        self.harvest_log = HarvestLog(os.path.join(APP_DIR, "harvest.jsonl"))
        self.aim_range = AimRange(os.path.join(APP_DIR, "aim_range.json"))
        self._base_state = ""
        self._base_snap: Optional[dict] = None
        self._trace: list = []
        self._base_state_prev_for_trace = ""
        self._harvest_dur = 16.0
        self._stuck_text = ""
        self._plugin_cost: Optional[float] = None
        self._base_ms = 0.0
        # 채집 궤적·배치 계산은 별도 프로세스에서 (화면 스레드가 멈추지 않게)
        self.sim = SimWorker()
        self.sim.done.connect(self._on_sim_done)
        self._sim_req: Dict[str, object] = {}        # 채널 → 마지막으로 보낸 요청 키
        self._sim_res: Dict[str, tuple] = {}         # 채널 → (요청 키, 결과)
        self._hcache: tuple = ((), None)             # 화면 대응점 → 월드→화면 변환 (SVD 3ms 라 한 번만)
        self.hud.moved.connect(self._on_hud_moved)
        self.control = ControlWindow(self.data, self.run, self.settings)
        self.control.scan_requested.connect(lambda: self.scan_now(forced=True))
        self.control.save_frame_requested.connect(self._save_frame)
        self.control.hud_edit_toggled.connect(self._set_hud_edit)
        self.control.hud_reset_requested.connect(self._reset_hud_offset)
        self.control.settings_changed.connect(self._on_settings_changed)
        self.control.run_edited.connect(self._on_run_edited)

        self.recog = RecognitionService()
        self.recog.result.connect(self._on_scan)
        self.logw = PlayerLogWatcher()
        self.logw.log_event.connect(self._on_log_event)
        self.logw.synced.connect(self._on_log_synced)
        self.bridge = BridgeClient()
        self.bridge.snapshot.connect(self._on_bridge_snapshot)
        self.bridge.status_changed.connect(self._on_bridge_status)
        self.bridge_state: Optional[BridgeState] = None
        self._bridge_inventory = None
        self._logged_unknown: set = set()
        self._picked_sig = None          # 게임 상태로 확정한 마지막 선택창의 카드 묶음과 시각
        self._echo_sessions: set = set()
        self._counters: Optional[dict] = None         # 게임의 새로고침·삭제 횟수 (마지막 스냅샷)
        self._session_counters: Dict[int, dict] = {}  # 선택창이 열릴 때의 횟수
        self.meta: Optional[MetaState] = None
        self.expedition: Optional[ExpeditionAdvice] = None
        self.recorder = RunRecorder(os.path.join(APP_DIR, "runs.jsonl"))
        self.draws = DrawStats(os.path.join(APP_DIR, "draws.jsonl"))
        self.recommender.draw_weights = self.draws.weights()
        self.snapshots = SnapshotLog(os.path.join(APP_DIR, "snapshots"))
        self._last_levelup_snap: Optional[dict] = None
        self.control.set_history(self.recorder.load())
        self.recommender.history = self.recorder.load()      # 캐릭터별 항목 성적 (내 기록)
        self.control.set_draw_summary(self.draws.summary())
        self.mod_guard = ModGuard(self.settings, self.data.game_build_id, lambda: self.bridge.connected)
        self.mod_guard.status.connect(self.control.set_mod_status)
        self.mod_guard.notice.connect(lambda text: self.tray.showMessage("BALL x PIT 도우미", text))
        self.control.mod_install_requested.connect(self.mod_guard.install_now)
        self.layout_win = LayoutWindow(self.data)
        self.layout_plan = None
        self._layout_busy = False
        self.control.layout_requested.connect(self.open_layout)
        self.layout_win.recalc_requested.connect(lambda: self.compute_layout(force=True))
        self.hud.compact = self.settings.hud_compact
        self.inputs = InputWatcher()
        self.inputs.resync.connect(lambda: self.scan_now(forced=True))
        self.inputs.toggle_hud.connect(self.toggle_hud)
        self.inputs.toggle_window.connect(self.toggle_control)
        self.inputs.toggle_detail.connect(self.toggle_compact)
        self.inputs.clicked.connect(lambda x, y, at: self.tracker.add_click(Click(x, y, at)))

        self.control.setWindowIcon(app_icon())
        self.tray = Tray()
        self.tray.open_requested.connect(self.show_control)
        self.tray.hud_toggle_requested.connect(self.toggle_hud)
        self.tray.scan_requested.connect(lambda: self.scan_now(forced=True))
        self.tray.quit_requested.connect(self._quit_from_tray)

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.diag_timer = QTimer(self)
        self.diag_timer.setInterval(1000)
        self.diag_timer.timeout.connect(self._refresh_diagnostics)

    def start(self, show: bool = True):
        self.mod_guard.start()
        self.bridge.start()
        self.logw.start()
        self.inputs.start()
        self.tick.start(self.settings.scan_interval_ms)
        self.diag_timer.start()
        self.tray.show()
        if show:
            self.show_control()
        if autostart.is_enabled() != self.settings.start_with_windows:
            autostart.set_enabled(self.settings.start_with_windows)
        if not self.ocr_status.ok:
            log.warning(self.ocr_status.message)
            self.control.select_page("진단")
        from .engine import native
        log.info("시작: 데이터 빌드 %s, OCR %s, 네이티브 계산 %s", self.data.game_build_id, self.ocr_status.ok,
                 "사용" if native.lib() is not None else "없음 (파이썬 계산)")

    def shutdown(self):
        self.sim.shutdown()
        self.mod_guard.stop()
        self.tick.stop()
        self.diag_timer.stop()
        self.inputs.stop()
        self.bridge.stop()
        self.logw.stop()
        self.recog.stop()
        self.tray.hide()
        self.hud.close()
        self.highlight.close()
        self.dps_meter.close()
        self.base_overlay.close()
        self.layout_win.close()
        self.control.close()

    # ---- 감시 루프 ----
    def _tick(self):
        if self.bridge.live:
            # 게임 연동 중: 화면 인식은 쉬고, HUD 표시 판단용으로 게임 창 상태만 갱신한다
            self.window = gw.find_game_window()
            self._update_hud()
            if self.base_overlay.isVisible() and not self._game_active():
                self.base_overlay.hide()
            return
        if self.base_overlay.isVisible():
            self.base_overlay.hide()     # 연동이 끊김: 마지막 안내가 남지 않게
        if not self.settings.watch_enabled or not self.ocr_status.ok:
            return
        if self.recog.inflight_job is not None:
            self.recog.restart_if_stuck(8.0)
            return
        if self.logw.available and self.logw.phase == "base":
            return   # 기지 화면에는 강화 선택창이 없다
        forced = (self.tracker.session is not None or self.tracker.awaiting_confirmation
                  or time.monotonic() < self.force_until)
        self.recog.request(self.tracker.generation, forced=forced, keep_image=self.pending_save)

    def _log_scan_change(self, res: ScanResult):
        """판별 결과가 바뀔 때만 기록한다(매 틱 기록하지 않음). 인식이 안 될 때 원인을 찾기 위한 것."""
        obs, w = res.observation, res.window
        skipped = "" if res.skipped in ("화면 움직임", "이전 화면과 같음") else res.skipped   # 평소 동작은 기록하지 않음
        state = (obs.kind, skipped, obs.error, w.size if w else None, w.foreground if w else None,
                 obs.frame.backend if obs.frame else "")
        if state == getattr(self, "_last_scan_state", None):
            return
        self._last_scan_state = state
        log.info("인식 상태: %s%s%s | 창 %s %s | 캡처 %s | %s", obs.kind.value,
                 f" (건너뜀: {res.skipped})" if res.skipped else "", f" 오류: {obs.error}" if obs.error else "",
                 w.size if w else "-", "앞" if w and w.foreground else "뒤",
                 obs.frame.backend if obs.frame else "-",
                 " ".join(f"{k}={v:.0f}ms" for k, v in res.timings_ms.items()))

    def scan_now(self, forced: bool = True):
        self.force_until = time.monotonic() + 2.0
        self.recog.restart_if_stuck(4.0)
        if self.recog.request(self.tracker.generation, forced=forced, keep_image=self.pending_save) is None:
            log.info("인식 작업 진행 중(%.1f초)이라 다음 차례에 읽습니다", self.recog.busy_for())

    def _on_scan(self, res: ScanResult):
        self._log_scan_change(res)
        self.last_result = res
        if res.window is not None or res.observation.kind == ScreenKind.GAME_NOT_FOUND:
            self.window = res.window
        if res.ocr_ran:
            self.timings.append(dict(res.timings_ms))
        if res.image is not None and self.pending_save:
            self._write_frame(res)
        if res.generation != self.tracker.generation:
            self.stale_results += 1   # 새로고침·런 전환 전에 찍은 화면 → 버린다
            self._update_hud()
            return
        obs = res.observation
        if res.skipped and res.skipped != "이전 화면과 같음":
            self._update_hud()
            return
        if obs.kind == ScreenKind.LEVEL_UP and self.run.phase != "in_run" and not self.logw.available:
            self.run.join_mid_run()   # 로그 없이 선택창을 처음 본 경우
        forced = time.monotonic() < self.force_until
        self._handle(self.tracker.observe(obs, time.monotonic(), forced=forced))
        self._update_hud()

    # ---- 게임 연동 ----
    def _on_bridge_snapshot(self, snap: dict, at: float):
        if isinstance(snap.get("meta"), dict):
            try:
                with open(os.path.join(self.dump_dir, "live_meta.json"), "w", encoding="utf-8") as f:
                    json.dump(snap, f, ensure_ascii=False)
            except OSError:
                pass
            self.meta = parse_meta(snap["meta"], self.data)
            if getattr(self, "_harvest_pending", False) and self.meta.resources:
                self._harvest_pending = False
                row = self.harvest_log.finish(list(self.meta.resources))
                if row:
                    log.info("채집 기록: 조준 %.0f° → 증가 %s", row["angle"], row["gain"])
            self.recommender.meta = self.meta
            self.fusion.meta = self.meta
            need, _ = need_resource(self.meta, self._shortfalls())
            note = advise_resource_ratio([b.type for b in self.meta.buildings], need)
            self.control.set_meta(self.meta, note)
            return
        if isinstance(snap.get("catalog"), dict):
            self.snapshots.set_catalog(snap)
            from .gamedata import save_catalog
            save_catalog(snap["catalog"])
            props = {}
            for kind in ("balls", "passives"):
                for e in snap["catalog"].get(kind) or []:
                    iid = self.data.item_by_log_id(e.get("type") or "")
                    if iid and isinstance(e.get("lvl_props"), list):
                        props[iid] = e["lvl_props"]
            if props:
                self.data.apply_level_props(props)
            sched = catalog_schedules(snap["catalog"])
            if sched:
                self.data.level_schedules = sched
            n = self.data.apply_game_recipes(catalog_recipes(snap["catalog"], self.data))
            k = snap["catalog"].get("max_solo_lvl")
            if isinstance(k, int) and k >= 0:
                # 게임 lvl 은 0부터 → 화면 레벨 k+1 이 단독 최대(진화 조건)
                self.data.set_observed_max_level("ball", k + 1)
                self.data.set_observed_max_level("passive", k + 1)
            log.info("게임 연동: 게임 안 레시피 %d개로 교체 (위키 레시피 대신), 단독 최대 레벨 %s, 플러그인 %s, "
                     "레벨별 수치 %d개", n, (k + 1) if isinstance(k, int) else "?", snap.get("plugin") or "1.1 이하",
                     len(props))
            self.control.refresh_run()
            return
        now = time.monotonic()
        if now - getattr(self, "_live_dump_at", 0.0) >= 0.5:
            # 개발·진단용: 최신 게임 연동 상태를 파일로 (tools/drive.py state)
            self._live_dump_at = now
            try:
                tmp = os.path.join(self.dump_dir, "live_state.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(snap, f, ensure_ascii=False)
                os.replace(tmp, os.path.join(self.dump_dir, "live_state.json"))
            except OSError:
                pass
        if self.window is None:
            self.window = gw.find_game_window()
        origin = self.window.origin if self.window else (0, 0)
        st = convert(snap, self.data, origin, frame_id=int(snap.get("seq") or 0), at=at)
        self.bridge_state = st
        self._read_ui_avoid(snap, st)
        b = snap.get("battle")
        if isinstance(b, dict):
            self._counters = {"rerolls": b.get("rerolls"), "free": b.get("free_rerolls"),
                              "banishes": b.get("banishes"), "banished": len(b.get("banished") or [])}
        new_unknown = set(st.unknown_types) - self._logged_unknown
        if new_unknown:
            self._logged_unknown |= new_unknown
            log.warning("게임 연동: 데이터에 없는 항목 %s", sorted(new_unknown))
        if st.damage and snap.get("battle") is not None:
            self.run.damage = dict(st.damage)
            b0 = snap["battle"]
            t = b0.get("elapsed") if isinstance(b0.get("elapsed"), (int, float)) else snap.get("t")
            if isinstance(t, (int, float)):
                self.dps.add(float(t), st.damage)
        self._dps_state = snap.get("game_state") or ""
        if isinstance(snap.get("cost_ms"), (int, float)):
            self._plugin_cost = float(snap["cost_ms"])          # 플러그인 1.7: 게임 프레임에서 읽기에 쓴 시간
        t0 = time.perf_counter()
        try:
            self._on_base(snap.get("base") if isinstance(snap.get("base"), dict) else None)
        except Exception:                                   # noqa: BLE001 — 기지 화면 오류가 선택창·융합 처리를 막지 않게
            if not getattr(self, "_base_error_logged", False):
                log.exception("기지 화면 처리 오류 (한 번만 기록)")
                self._base_error_logged = True
        self._base_ms = 0.9 * self._base_ms + 0.1 * (time.perf_counter() - t0) * 1000
        if st.battle is not None:
            self.battle_info = st.battle
            self.dps_meter.set_battle(st.battle)
        self._update_expedition(st)
        if snap.get("battle") is not None and self.recorder.current is not None:
            self.recorder.update(self.run, st.observation.progress,
                                 completed=(snap["battle"] or {}).get("completed_level"),
                                 game_over=st.game_over is not None)
        if not st.in_run:
            if self.run.phase == "in_run" and snap.get("battle") is None:
                self._handle(self.tracker.reset())
                self.run.end_run("게임 연동: 런 밖")
                self._finish_run_record()
                self.control.refresh_run()
            self._handle(self.tracker.observe(st.observation, time.monotonic(), immediate=True))
            self._update_hud()
            return
        if self.run.phase != "in_run":
            self._handle(self.tracker.reset())
            self._finish_run_record()
            self.run.start_run()
            self.recorder.start()
            self.dps.reset()
            log.info("게임 연동: 런 진행 중")
        elif self.recorder.current is None:
            self.recorder.start()      # 도우미를 런 도중에 켰거나 다시 연결됨 → 이어서 기록
        obs = st.observation
        if st.max_ball_level:
            self.data.set_observed_max_level("ball", st.max_ball_level)
        for c in obs.cards:
            # 게임이 이 레벨로 강화하는 카드를 줬다면 최대 레벨은 적어도 그만큼이다
            if c.shown_level and c.item_id:
                self.data.note_level_seen(self.data.items[c.item_id].kind, c.shown_level)
        self.run.apply_character(obs.character_id, obs.extra_characters, source="game")
        self._update_fusion(obs)
        if obs.kind == ScreenKind.LEVEL_UP and self.tracker.session is None:
            log.info("게임 연동 원본(선택창): %s", json.dumps(snap, ensure_ascii=False)[:4000])
            self._last_levelup_snap = snap
        if obs.inventory is not None:
            self._bridge_inventory = obs.inventory
            if self.tracker.session is None:
                self.run.apply_inventory(obs.inventory, self.data)
            self._resolve_pending_pick(obs.inventory)
        self._handle(self.tracker.observe(obs, time.monotonic(), immediate=True))
        self._update_hud()

    def _counter_outcome(self, session_id: int) -> Optional[str]:
        """선택창이 열릴 때와 지금의 게임 횟수를 비교: rerolled | banished | None."""
        before, now = self._session_counters.get(session_id), self._counters
        if not before or not now:
            return None
        def grew(k):
            return isinstance(now.get(k), int) and isinstance(before.get(k), int) and now[k] > before[k]
        def shrank(k):
            return isinstance(now.get(k), int) and isinstance(before.get(k), int) and now[k] < before[k]
        if grew("rerolls") or shrank("free"):
            return "rerolled"
        if shrank("banishes") or grew("banished"):
            return "banished"
        return None

    def _is_after_pick_echo(self, s) -> bool:
        sig = getattr(self, "_picked_sig", None)
        if sig is None or not self.bridge.live:
            return False
        items, at = sig
        return tuple(c.item_id for c in s.cards) == items and time.monotonic() - at < 8.0

    def _on_bridge_status(self, _text: str):
        if not self.bridge.connected and self.recorder.current is not None:
            self._finish_run_record()    # 게임을 끄면 연동이 끊긴다 → 진행 중이던 런은 '중단'으로 남긴다
        self.control.refresh_run()

    def _finish_run_record(self):
        rec = self.recorder.finish()
        if rec is not None:
            log.info("런 기록 저장: %s, %s턴, 선택 %d번", rec.result, rec.turn, len(rec.picks))
            self.control.set_history(self.recorder.load())
            self.recommender.history = self.recorder.load()

    def _update_expedition(self, st: BridgeState):
        """보스를 깬 뒤 런 종료 화면에 '원정 계속' 버튼이 있으면 계속/복귀를 판단해 보여 준다."""
        go = st.game_over
        if go is not None and go.completed and go.endless_button:
            if self.expedition is None:
                p = st.observation.progress
                best = None
                if self.meta and p is not None:
                    best = next((lv.get("best_endless") for lv in self.meta.levels
                                 if lv.get("type") == p.level_name), None) or None
                from .tracking.run_history import expedition_history
                self.expedition = advise_expedition(self.run, self.data, p, best,
                                                    expedition_history(self.recorder.load()))
                if self.recorder.current is not None:
                    self.recorder.current.expedition = dict(self.expedition.snapshot)
                log.info("원정 계속 판단: %s | %s", self.expedition.headline, "; ".join(self.expedition.reasons))
                self.hud.show_expedition(self.expedition)
        elif self.expedition is not None:
            self.expedition = None
            self.hud.hide()

    def toggle_compact(self):
        self.settings.hud_compact = not self.settings.hud_compact
        self.settings.save()
        self.hud.compact = self.settings.hud_compact
        if self.recommendation is not None and self.tracker.session is not None:
            self.hud.show_recommendation(self.recommendation, self.tracker.session.points_left)
        log.info("HUD %s", "간단히" if self.hud.compact else "자세히")
        self._update_hud()

    def _read_ui_avoid(self, snap: dict, st):
        """플러그인 1.11: 지금 화면에서 가리면 안 되는 게임 UI 영역 (화면 밖·미끄러져 들어오는 중인 값은 버림)."""
        from .tracking.bridge_adapter import _rect
        ui = snap.get("ui") if isinstance(snap.get("ui"), dict) else {}
        frame = st.observation.frame
        size = (int(snap.get("screen_w") or 0), int(snap.get("screen_h") or 0))
        rects = [_rect(v, size) for v in ui.get("avoid") or []]
        self._ui_screen = ui.get("screen") or ""
        self._ui_avoid = [frame.to_screen(r) for r in rects if r] if frame is not None else []

    def _update_char_combo(self, base: Optional[dict], state: str):
        """캐릭터 선택 화면: 알선소가 지어져 있으면 캐릭터 조합을 추천한다 (커뮤니티 추천 + 내 기록).
        플러그인 1.12: 로드아웃 화면의 두 캐릭터 패널로 '이미 고른 캐릭터'를 안다 (base.loadout.char1/char2).
        하나만 골랐으면 그 캐릭터와 맞는 조합만 추리고, 둘 다 골랐으면 그 조합의 궁합을 보여준다."""
        loadout = (base or {}).get("loadout") or {}
        raw1, raw2 = loadout.get("char1"), loadout.get("char2")
        has_matchmaker = base is not None and any(
            b.get("type") == "kMatchMaker" and b.get("state", "kNormal") == "kNormal"
            for b in base.get("buildings") or [])
        in_flow = self.meta is not None and has_matchmaker and state in ("kSelectingChar", "kSelectingLoadout")
        if not in_flow:
            self._loadout_seen = (None, None)
            self._loadout_miss = 0
            if self.char_combo is not None:
                self.char_combo = None
                self._char_combo_key = None
                self.hud.hide()
            return
        if raw1 or raw2:
            self._loadout_seen = (raw1, raw2)
            self._loadout_miss = 0
            char1, char2 = raw1, raw2
        else:
            # 실제 확인: 캐릭터 고르는 화면이 떠 있는 동안 로드아웃 화면(LoadoutUI)이 몇 폴링에 한 번씩
            # 잠깐 비활성화돼 char1/char2 가 통째로 안 잡힌다 — 그때마다 '고정' 추천이 풀렸다가 방금 고른
            # 캐릭터를 무시한 일반 추천으로 돌아가는 깜빡임이 있었다(로그로 확인). 몇 번 정도는 방금 본
            # 값을 그대로 쓰고, 계속 비면(정말 다시 고르는 중으로 보고) 놓는다.
            self._loadout_miss += 1
            if self._loadout_miss <= 5:
                char1, char2 = self._loadout_seen
            else:
                self._loadout_seen = (None, None)
                char1, char2 = None, None
        want = state == "kSelectingChar" or (state == "kSelectingLoadout" and char1 and char2)
        if not want:
            if self.char_combo is not None:
                self.char_combo = None
                self._char_combo_key = None
                self.hud.hide()
            return
        key = (state, char1, char2)
        if key == self._char_combo_key:
            return
        self._char_combo_key = key
        from .engine.char_combo import char_id, suggest_pairs
        from .ui.hud import HudRow, HudView
        d = self.data

        if char1 and char2:
            pairs = suggest_pairs(d, self.meta.chars_raw, self.recommender.history, limit=1,
                                  exact=(char_id(char1), char_id(char2)))
            if not pairs:
                self.char_combo = None
                return
            p = pairs[0]
            tone, status = ("accent", "좋음") if p.score >= 8 else (("neutral", "보통") if p.score >= 3
                                                                    else ("neutral", "정보 없음"))
            v = HudView(title=f"{d.name(p.a)} + {d.name(p.b)}", subtitle="이번 원정 캐릭터 궁합",
                        status=status, status_tone=tone)
            v.lines = [(r, "secondary") for r in (p.reasons[:3] or ["커뮤니티 추천·내 기록·전략 궁합 어디에도 안 걸림"])]
            self.char_combo = pairs
            log.info("캐릭터 궁합: %s + %s (%.1f) %s", d.name(p.a), d.name(p.b), round(p.score, 1), p.reasons)
            self.hud.render_view(v)
            return

        fixed = char_id(char1) if char1 else None
        pairs = suggest_pairs(d, self.meta.chars_raw, self.recommender.history, limit=4, fixed=fixed)
        if not pairs:
            self.char_combo = None
            return
        best = pairs[0]
        subtitle = f"{d.name(fixed)}와 좋은 조합 (알선소)" if fixed else "캐릭터 조합 추천 (알선소)"
        v = HudView(title=f"{d.name(best.a)} + {d.name(best.b)}", subtitle=subtitle,
                    status="추천", status_tone="accent")
        v.lines = [(r, "secondary") for r in best.reasons[:2]]
        v.section = "다른 조합"
        v.rows = [HudRow((), f"{d.name(p_.a)} + {d.name(p_.b)}", p_.reasons[0] if p_.reasons else "") for p_ in pairs[1:]]
        v.footer = [("커뮤니티 추천(Dexerto·Screen Rant·Steam)과 내 런 기록 기준 — 참고용", "tertiary")]
        self.char_combo = pairs
        log.info("캐릭터 조합 추천%s: %s", f" ({d.name(fixed)} 고정)" if fixed else "",
                 [(d.name(p_.a), d.name(p_.b), round(p_.score, 1)) for p_ in pairs])
        self.hud.render_view(v)

    def _update_base_advice(self, base: Optional[dict], state: str):
        """기지에서 딱히 메뉴 없이 서 있을 때(kNormal): 뭘 지을지·철거할지 HUD로 보여준다.
        원래 F10 설정창에만 있던 추천이라 안 보인다는 지적을 받아 HUD 팝업으로 승격."""
        want = state == "kNormal" and base is not None and self.meta is not None
        if not want:
            if self.base_advice is not None:
                self.base_advice = None
                self._base_advice_key = None
                self.hud.hide()
            return
        todo = suggest_base(self.meta, self.data, limit=3)
        demolish = list(getattr(self.layout_plan, "demolish", None) or [])[:2]
        from .engine.harvest import advise_workers
        need, _ = need_resource(self.meta, self._shortfalls())
        workers = advise_workers(self.meta, self.meta.chars_raw, [b.type for b in self.meta.buildings],
                                 self.data, need)[:3]
        key = (tuple((sg.kind, sg.type) for sg in todo), tuple((t, s) for _, t, s in demolish),
               tuple((a.char_id, a.building, a.action) for a in workers))
        if not todo and not demolish and not workers:
            if self.base_advice is not None:
                self.base_advice = None
                self._base_advice_key = None
            return
        if key == self._base_advice_key:
            return
        self._base_advice_key = key
        from .ui.hud import HudRow, HudView
        d = self.data
        verb = {"finish": "완성", "build": "짓기", "upgrade": "강화"}
        items = [(f"{verb[sg.kind]}: {sg.name}", sg.status) for sg in todo]
        items += [(f"철거 후보: {d.building_name(t)}", f"기여 {score:.1f}") for _i, t, score in demolish]
        items += [(f"일꾼: {d.building_name(a.building)} ← {d.name(a.char_id)}"
                   + (f" ({d.name(a.replace)} 대신)" if a.action == "swap" else ""),
                   "교체" if a.action == "swap" else "빈 건물") for a in workers]
        title, status = items[0]
        v = HudView(title=title, subtitle="기지 조언", status=status,
                    status_tone="warn" if "부족" in status else "accent")
        if len(items) > 1:
            v.section = "다른 항목"
            v.rows = [HudRow((), t, s) for t, s in items[1:]]
        v.footer = [("배치 효과·부족 자원 기준 — F10 설정창에서 더 자세히", "tertiary")]
        self.base_advice = items
        log.info("기지 조언: %s", items)
        self.hud.render_view(v)

    def _update_fusion(self, obs: ScreenObservation):
        """융합 화면(진화·융합·무료 강화)이 열려 있으면 무엇과 무엇을 합칠지 추천한다."""
        if obs.kind != ScreenKind.FUSION:
            if self.fusion_rec is not None:
                self.fusion_rec = None
                self._fusion_panel = None
                self.hud.hide()
            return
        # 융합 창 위치 (HUD 가 가리지 않게 — 강화 선택창과 같은 방식)
        self._fusion_panel = (obs.frame.to_screen(obs.panel_rect)
                              if obs.frame is not None and obs.panel_rect else None)
        rec = self.fusion.recommend(obs.fuser, obs.inventory, self.run)
        if self.fusion_rec is None or rec.headline != self.fusion_rec.headline:
            log.info("융합 추천(%s): %s | 진화 %s | 융합 %s | 분열 %s", rec.status, rec.headline,
                     [(p.title, round(p.score, 1)) for p in rec.evos],
                     [(p.title, round(p.score, 1)) for p in rec.combos],
                     round(rec.free.score, 1) if rec.free else None)
        self.fusion_rec = rec
        self.hud.show_fusion(rec)

    def _resolve_pending_pick(self, inventory):
        pend = getattr(self, "_pending_pick", None)
        if pend is None or inventory is None:
            return
        session, since, out = pend
        other = self._counter_outcome(session.session_id)
        if other is not None:
            # 보유 목록이 아니라 새로고침·삭제 횟수가 바뀌었다 → 고른 것이 아니다
            self._pending_pick = None
            out = PickOutcome(out.session_id, other, None, "게임 상태로 확인 (횟수 변화)", out.options)
            self.run.apply_outcome(out, self.data)
            log.info("세션 %s 종료: %s (%s)", out.session_id, other, out.evidence)
            return
        card = infer_pick(session.inventory, inventory, session.cards)
        expired = time.monotonic() - since > 3.0
        if card is None and not expired:
            return
        self._pending_pick = None
        self._picked_sig = (tuple(c.item_id for c in session.cards), time.monotonic())
        if card is not None:
            out = PickOutcome(out.session_id, "picked", card, "게임 상태로 확인", out.options)
        self.run.apply_outcome(out, self.data)
        self.run.apply_inventory(inventory, self.data)
        log.info("세션 %s 선택 확정: %s (%s)%s", out.session_id,
                 self.data.name(out.card.item_id) if out.card else out.kind, out.evidence,
                 "" if out.card else f" | 횟수 열림 {self._session_counters.get(session.session_id)} → 지금 {self._counters}")
        if out.kind == "picked" and out.card is not None:
            self.hud.show_message(self.data.name(out.card.item_id), f"선택 반영 · {out.card.position} 카드",
                                  tk.OK, 1600, item_id=out.card.item_id)
        self.control.refresh_run()
        self._update_hud()

    # ---- 추적 이벤트 처리 ----
    def _handle(self, events: List[TrackerEvent]):
        for ev in events:
            if ev.kind in ("opened", "updated"):
                if self.run.phase != "in_run" and not self.logw.available:
                    self.run.join_mid_run()
                s = ev.session
                if self._is_after_pick_echo(s):
                    # 고른 직후 닫히는 애니메이션 동안 같은 카드가 다시 잡힌 것 — 새 선택창이 아니다
                    self._echo_sessions.add(s.session_id)
                    continue
                if ev.kind == "opened":
                    if self._counters is not None:
                        self._session_counters[s.session_id] = dict(self._counters)
                        for old in [k for k in self._session_counters if k < s.session_id - 20]:
                            del self._session_counters[old]
                    self.run.note_offered(tuple(c.item_id for c in s.cards))
                    if s.pool is not None:
                        self.draws.record(s)
                        self.recommender.draw_weights = self.draws.weights()
                        self.control.set_draw_summary(self.draws.summary())
                    if self._last_levelup_snap is not None:
                        self.snapshots.add(self._last_levelup_snap)   # 업데이트 뒤 회귀 검사용 원본
                        self._last_levelup_snap = None
                self.run.apply_character(s.character_id)
                if s.inventory is not None:
                    self.run.apply_inventory(s.inventory, self.data)
                else:
                    self.run.reconcile_cards(s.cards, self.data)
                self.recommendation = self.recommender.recommend(ev.session, self.run)
                self.hud.show_recommendation(self.recommendation, ev.session.points_left)
                self._log_recommendation(ev)
            elif ev.kind == "closed":
                self.recommendation = None
                out = ev.outcome
                if out.session_id in self._echo_sessions:
                    self._echo_sessions.discard(out.session_id)
                    continue
                other = self._counter_outcome(out.session_id) if self.bridge.live else None
                if other is not None:
                    out = PickOutcome(out.session_id, other, None, "게임 상태로 확인 (횟수 변화)", out.options)
                    self.run.apply_outcome(out, self.data)
                    log.info("세션 %s 종료: %s (%s)", out.session_id, other, out.evidence)
                    continue
                if self.bridge.live and out.kind in ("picked", "unknown"):
                    # 게임 연동: 선택창이 닫힌 뒤 보유 목록이 바뀌면 그 차이로 실제 선택을 확정한다.
                    # 게임은 창을 닫은 다음 프레임에 보유 목록을 바꾸므로 3초까지 기다린다.
                    self._pending_pick = (ev.session, time.monotonic(), out)
                    self._resolve_pending_pick(self._bridge_inventory)
                    continue
                self.run.apply_outcome(out, self.data)
                log.info("세션 %s 종료: %s (%s)", out.session_id, out.kind, out.evidence)
                if out.kind == "picked" and out.card is not None:
                    self.hud.show_message(self.data.name(out.card.item_id), f"선택 반영 · {out.card.position} 카드 ({out.evidence})",
                                          tk.OK, 1600, item_id=out.card.item_id)
                elif out.kind == "rerolled":
                    self.force_until = time.monotonic() + 3.0
                    self.hud.show_message("새로고침", "새 선택지를 읽는 중", tk.TEXT_3, 3000)
                elif out.kind == "unknown":
                    self.hud.show_message("선택 결과를 확인하지 못했습니다",
                                          "다음 선택창의 표시로 보정하거나 F10에서 고를 수 있습니다", tk.WARN, 2600)
                else:
                    self.hud.hide()
        if events:
            self.control.refresh_run()

    def _log_recommendation(self, ev: TrackerEvent):
        rec = self.recommendation
        if rec is None or ev.kind != "opened":
            return
        log.info("세션 %s 추천(%s, 규칙 %s): %s | %s", rec.session_id, rec.status, rec.rules_version, rec.headline,
                 "; ".join(f"{e.card.position}={e.card.item_id}:{e.score:.0f}:"
                           + ",".join(r.rule_id for r in e.reasons + e.warnings) for e in rec.evals))
        if rec.plan_text or rec.banish_text:
            log.info("세션 %s 덱 방향: %s | %s", rec.session_id, rec.plan_text or "-", rec.banish_text or "삭제 추천 없음")

    # ---- 게임 로그 ----
    def _on_log_synced(self, phase: str, version: str):
        if phase == "in_run" and self.run.phase != "in_run":
            self.run.join_mid_run()
        elif phase == "base":
            self.run.phase = "base"
        self.control.refresh_run()

    def _on_log_event(self, ev: LogEvent, at: float):
        if ev.kind == "base_state":
            self.base_state = ev.value
        elif ev.kind == "run_started":
            self._handle(self.tracker.reset())
            self.run.start_run()
            self.control.refresh_run()
            log.info("런 시작")
        elif ev.kind == "run_ended":
            self._handle(self.tracker.reset())
            self.run.end_run("기지 복귀")
            self._finish_run_record()
            self.hud.hide()
            self.control.refresh_run()
        elif ev.kind == "game_over":
            self._handle(self.tracker.reset())
        elif ev.kind == "reroll":
            if at - self.last_reroll_at > 1.5:   # 한 번의 새로고침에 여러 줄이 찍힌다
                self.last_reroll_at = at
                self._handle(self.tracker.note_reroll(at))
                self.force_until = time.monotonic() + 3.0
        self._update_hud()

    # ---- HUD 표시·위치 ----
    def _game_active(self) -> bool:
        w = self.window
        return w is not None and w.foreground and not w.minimized

    def _update_capture_exclusion(self):
        """화면 인식을 쓸 때만 HUD를 캡처에서 뺀다(자기 글자를 게임 글자로 읽지 않도록).
        게임 연동 중에는 스크린샷·녹화에 HUD가 그대로 보인다."""
        want = self.settings.hide_from_capture == "always" or (
            self.settings.hide_from_capture == "auto" and not self.bridge.connected)
        if want != getattr(self, "_capture_hidden", None):
            self._capture_hidden = want
            self.hud.set_capture_excluded(want)
            self.highlight.set_capture_excluded(want)
            self.dps_meter.set_capture_excluded(want)
            self.base_overlay.set_capture_excluded(want)

    def _shortfalls(self) -> dict:
        """곧 지을·올릴 것(기지 조언 상위 5개 중 모자란 것)의 자원별 부족분 합."""
        out: dict = {}
        if self.meta is None:
            return out
        for sg in suggest_base(self.meta, self.data, limit=5):
            if sg.affordable:
                continue
            for k, v in self.meta.shortfall(tuple(int(x) for x in sg.cost)).items():
                out[k] = out.get(k, 0) + v
        return out

    def _on_base(self, base: Optional[dict]):
        """기지: 채집 조준 중이면 안내를 띄우고, 채집 전후 자원으로 조준 기록을 남긴다."""
        state = base.get("state", "") if base else ""
        res = list(self.meta.resources) if self.meta else []
        if state != self._base_state:
            self.aim_range.save()
            if state == "kAimWorkers":
                # 이번 채집의 비행 시간 (조준 화면에 들어올 때의 남은 채집 시간)
                self._harvest_dur = float(base.get("harvest_secs_left") or 16) or 16.0
                if res:
                    self.harvest_log.start(res, layout_key(base))
                    log.info("채집 시작: 자원 %s", res)
            if self._base_state == "kHarvestSummary" and state != "kHarvestSummary":
                # 실제 게임 확인: 자원은 요약 화면 중에 들어오고, 그 뒤 meta 는 바뀌지 않아 다시 오지 않을 수 있다.
                # 이미 바뀌었으면 바로 기록하고, 아니면 다음 meta 를 기다린다.
                if res and self.harvest_log._before is not None and res != self.harvest_log._before:
                    row = self.harvest_log.finish(res)
                    if row:
                        log.info("채집 기록: 조준 %.0f° → 증가 %s", row["angle"], row["gain"])
                else:
                    self._harvest_pending = True
            self._base_state = state
        self._base_snap = base
        self._update_char_combo(base, state)
        self._update_base_advice(base, state)
        if base is not None:
            self._update_spa(base)
        if base and (base.get("geo") or {}).get("colliders") and self.meta is not None and not self._layout_busy                 and state != "kRearrangeBuildings":        # 옮기는 도중에는 다시 계산하지 않는다 (목표가 흔들리지 않게)
            # 배치가 바뀌거나 미완성 건물이 생기고·끝나면 다시 계산
            lk = (layout_key(base), tuple(sorted(u.id for u in unfinished_buildings(base, self.meta))))
            if lk != getattr(self, "_layout_for", None):
                # 기지에 들어왔거나 배치가 바뀌면 배치 추천을 다시 계산 (백그라운드)
                self._layout_for = lk
                self.compute_layout()
        if base and state in ("kAimWorkers", "kBounceWorkers"):
            pl = base.get("player") or []
            if len(pl) >= 4 and state == "kAimWorkers":
                self.harvest_log.note_aim(pl[2], pl[3])
                if abs(pl[2]) + abs(pl[3]) > 0.1:
                    self.aim_range.note(HarvestLog.angle(pl[2], pl[3]), self._mouse_angle(pl))
            geo_ = base.get("geo") or {}
            if state == "kBounceWorkers" and geo_.get("workers"):
                # 채집 궤적 검증용: 날아가는 작업자 위치를 0.2초마다 남긴다 (harvest_traces.jsonl)
                self._trace.append({"t": round(time.monotonic(), 2), "w": geo_["workers"], "aim": pl[2:4]})
        if state == "kAimWorkers" and self._base_state_prev_for_trace != "kAimWorkers":
            self._trace = []
            self._trace_start_buildings = base.get("buildings") if base else None   # 채집 전 자원 (채집량 검증용)
        if state == "kHarvestSummary" and self._trace:
            try:
                with open(os.path.join(self.dump_dir, "harvest_traces.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.time(), "geo": {k: v for k, v in (base.get("geo") or {}).items()
                                                                    if k != "workers"},
                                        "buildings": base.get("buildings"),
                                        "buildings_before": getattr(self, "_trace_start_buildings", None),
                                        "resources_before": self.harvest_log._before,
                                        "trace": self._trace}) + "\n")
            except OSError:
                pass
            self._trace = []
        self._base_state_prev_for_trace = state
        self._render_base(base, state)

    def _remaining_moves(self, base: dict) -> list:
        """재배치 도중: 지금 배치에서 최적 배치까지 남은 옮기기 (배치가 바뀔 때만 다시 계산)."""
        plan = self.layout_plan
        final = getattr(plan, "final", None) if plan else None
        if not final:
            return list(plan.swaps) if plan else []
        key = (layout_key(base), id(plan))
        if getattr(self, "_remain_key", None) != key:
            from .engine.layout_opt import remaining_moves
            self._remain_key, self._remain = key, remaining_moves(base, final, getattr(plan, "final_rot", None))
        return self._remain

    def _mouse_angle(self, pl) -> Optional[float]:
        """발사대(게임 화면 좌표)에서 본 마우스 커서 각도. 화면 y 는 아래로 커진다. 왼쪽 아래는 180° 넘게."""
        import ctypes
        import math
        if self.window is None or pl[0] < 0:
            return None
        try:
            import ctypes.wintypes as wt
            pt = wt.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return None
        except (AttributeError, OSError):
            return None
        ox, oy = self.window.origin
        dx, dy = pt.x - (ox + pl[0]), (oy + pl[1]) - pt.y
        if abs(dx) + abs(dy) < 20:
            return None
        a = math.degrees(math.atan2(dy, dx))
        return a + 360 if a < -90 else a

    def _homography(self, proj):
        key = tuple(tuple(p) for p in proj or [])
        if key != self._hcache[0]:
            from .engine import harvest_sim as hs
            self._hcache = (key, hs.homography(proj or []))
        return self._hcache[1]

    def _render_base(self, base: Optional[dict], state: str):
        """기지 화면 위 안내 (재배치 번호 또는 채집 조준). 계산은 하지 않고, 계산 프로세스 결과만 그린다."""
        visible_ok = (self.window is not None and self._game_active() and self.settings.hud_auto_show
                      and not self.user_hidden)
        g0 = (base or {}).get("geo") or {}
        hm0 = self._homography(g0.get("proj")) if g0.get("proj") else None
        if hm0 is not None and all(k in g0 for k in ("left", "right", "top", "bottom")):
            from .engine import harvest_sim as hs
            self.base_overlay.set_land([hs.to_screen(hm0, x, y) for x, y in
                                        ((g0["left"], g0["bottom"]), (g0["right"], g0["bottom"]),
                                         (g0["right"], g0["top"]), (g0["left"], g0["top"]))])
        if base and state == "kRearrangeBuildings" and self.layout_plan and                 (self.layout_plan.swaps or getattr(self.layout_plan, "final", None)) and visible_ok:
            # 재배치 중: 다음에 옮길·맞바꿀 건물을 게임 화면에 번호로 표시
            from .engine import harvest_sim as hs
            from .engine.layout import Move
            pos = {b["id"]: (b.get("sx"), b.get("sy")) for b in base.get("buildings") or [] if b.get("sx") is not None}
            hmat = self._homography((base.get("geo") or {}).get("proj"))
            marks = []
            remain = self._remaining_moves(base)
            for n, sw in enumerate(remain, 1):
                if isinstance(sw, Move):
                    if sw.a in pos and hmat is not None:
                        tx, ty = hs.to_screen(hmat, sw.to[0], sw.to[1])
                        marks.append((pos[sw.a][0], pos[sw.a][1], tx, ty, str(n)))
                elif sw.a in pos and sw.b in pos:
                    marks.append((pos[sw.a][0], pos[sw.a][1], pos[sw.b][0], pos[sw.b][1], str(n)))
            game = geo.phys_to_logical_rect(self.window.rect)
            scale = game.width() / max(1, self.window.size[0])
            self.base_overlay.set_paths([], [])
            self.base_overlay.set_build_marks([])
            self.base_overlay.set_swap_marks(marks[:3])
            box = []
            if remain and hmat is not None and isinstance(remain[0], Move):
                from .engine.layout import buildings_from_base, grid_from_geo
                g = grid_from_geo(base.get("geo") or {})
                b0 = buildings_from_base(base).get(remain[0].a)
                if g is not None and b0 is not None:
                    fw, fh = b0.footprint
                    if getattr(remain[0], "rot", -1) >= 0 and (remain[0].rot - b0.rot) % 2:
                        fw, fh = fh, fw                          # 회전해서 놓는 건물: 가로·세로가 바뀐 목표 칸
                    cx, cy = remain[0].to
                    hw, hh = fw * g.size / 2, fh * g.size / 2
                    box = [hs.to_screen(hmat, x, y) for x, y in
                           ((cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh))]
            self.base_overlay.set_target_box(box)
            types = {b.get("id"): b.get("type", "") for b in base.get("buildings") or []}
            if remain:
                first = remain[0]
                lines = [(f"최적 배치까지 옮기기 {len(remain)}개 남음 — 번호 순서대로", (245, 245, 247, 255)),
                         (f"1번: {self.data.building_name(types.get(first.a, ''))} → 번호 1 자리 ({first.reason})",
                          (255, 159, 10, 255))]
            else:
                lines = [("최적 배치 완료 — 재배치를 끝내도 됩니다", (48, 209, 88, 255))]
            self.base_overlay.show_advice(game, scale, None, None, lines)
            if not self.base_overlay.isVisible():
                self.base_overlay.show()
            return
        self.base_overlay.set_swap_marks([])
        self.base_overlay.set_target_box([])
        if not (bool(base) and state == "kAimWorkers" and visible_ok):
            if self.base_overlay.isVisible():
                self.base_overlay.hide()
            return
        unf = unfinished_buildings(base, self.meta)
        adv = advise_harvest(base, self.meta, self._shortfalls(), self.harvest_log)
        texts = []
        sim = self._harvest_sim(base, adv.need, unf)
        if unf:
            names = " · ".join(f"{self.data.building_name(u.type)}({u.label} {u.pct:.0%}, "
                               f"{'' if u.exact else '약 '}{u.hits_left}번 더)" for u in unf[:3])
            texts.append((f"미완성 먼저: {names}", (255, 159, 10, 255)))
            tip = gold_bounce_tip(base, unf)
            if tip:
                texts.append((tip, (190, 190, 200, 255)))
            if self._stuck_text:
                texts.append((self._stuck_text, (255, 159, 10, 255)))
        texts.append((f"필요한 자원: {adv.need_text}", (245, 245, 247, 255)))
        if sim:
            texts += sim
        else:
            texts.append((adv.aim_text, (190, 190, 200, 255)))
        game = geo.phys_to_logical_rect(self.window.rect)
        scale = game.width() / max(1, self.window.size[0])
        self.base_overlay.set_build_marks([(u.sx, u.sy) for u in unf if u.sx is not None])
        self.base_overlay.show_advice(game, scale, adv, base.get("player"), texts)
        if not self.base_overlay.isVisible():
            self.base_overlay.show()

    # ---- 배치도 ----
    def open_layout(self):
        self.layout_win.show()
        self.layout_win.raise_()
        self.layout_win.activateWindow()
        self.compute_layout()

    def compute_layout(self, force: bool = False):
        """배치 추천과 자원별 채집 각도를 계산 프로세스에서 계산한다 (기지 화면 정보가 있어야 함)."""
        from .engine import harvest_sim as hs
        from .engine import sim_jobs
        base = self._base_snap
        if not base or not (base.get("geo") or {}).get("colliders") or self.meta is None or self._layout_busy:
            if not base:
                self.layout_win.set_result({}, None, {})
            return
        team = hs.team_from_chars(self.meta.chars_raw, getattr(self, "_team_order", None))
        snap = json.loads(json.dumps(base))
        bps = [{"type": b.type, "size": b.size} for b in self.meta.blueprints]
        targets = {u.id: u.hits_left for u in unfinished_buildings(base, self.meta)}
        need, _ = need_resource(self.meta, self._shortfalls())
        self._layout_busy = True
        char_levels = {c.get("type"): c.get("lvl") for c in self.meta.chars_raw if c.get("type")}
        # 계획도시 배치는 정해진 규칙이라 재배치 도중 다시 계산해도 목표가 같다 → 이전 목표(prefer)를 넘길 필요 없음
        self.sim.submit("layout", snap, sim_jobs.job_layout, snap, team, self._harvest_dur, bps, targets, need, 12.0,
                        None, char_levels)

    def _update_spa(self, base: dict):
        """스파 재채집 손익: 게임이 알려 준 비용과 내 채집 기록 평균을 비교 (비용·기록이 바뀔 때만)."""
        from .engine.spa import spa_advice
        spa = base.get("spa") if isinstance(base.get("spa"), dict) else None
        key = (json.dumps(spa, sort_keys=True) if spa else "", len(self.harvest_log.rows))
        if key == getattr(self, "_spa_key", None):
            return
        self._spa_key = key
        adv = spa_advice(spa, self.harvest_log.rows, layout_key(base), self._shortfalls() if self.meta else {})
        self.control.set_spa(adv)
        if adv is not None and adv.verdict == "profit" and base.get("harvested_today")                 and getattr(self, "_spa_notified", None) != adv.cost:
            self._spa_notified = adv.cost
            self.tray.showMessage("BALL x PIT 도우미", adv.text)
        if adv is not None:
            log.info("스파: %s", adv.text)

    def _on_sim_done(self, channel: str, key, result):
        self._sim_res[channel] = (key, result)
        if channel == "layout":
            self._on_layout_done(key, result)
            if self._base_snap and self._base_state == "kAimWorkers":
                self._render_base(self._base_snap, self._base_state)
        elif self._base_snap and self._base_state == "kAimWorkers":
            self._render_base(self._base_snap, self._base_state)     # 새 결과로 다시 그림 (계산은 안 함)

    def _on_layout_done(self, snap: dict, result):
        self._layout_busy = False
        plan, sweeps = result if result else (None, {})
        self.layout_plan = plan
        self.layout_win.set_result(snap, plan, sweeps)
        # 일꾼이 없으면 생산이 0인 건물 (금광·농장·야적장·채석장·채집가의 오두막 — 위키: 광마다 일꾼 1명)
        idle = [b.get("type", "") for b in (snap or {}).get("buildings") or []
                if b.get("type") in ("kGoldMine", "kIdleFarm", "kIdleLumberyard", "kIdleStoneMine", "kIdleLauncher")
                and isinstance(b.get("worker"), int) and b["worker"] < 0]
        self._unmanned = idle
        self.control.set_layout_plan(plan, idle)
        if plan:
            log.info("배치 추천: 범위 효과 %.1f → %.1f, 바꾸기 %d번, 채집 예상 %s → %s", plan.score_before,
                     plan.score_after, len(plan.swaps), plan.harvest_before, plan.harvest_after)

    def _access_move_names(self, stuck) -> str:
        """배치도 계산이 찾은 길 열기 옮기기 (예: '재배치에서 수레바퀴 공방을 빈 자리로 옮기기')."""
        from .engine.layout import Move
        plan = self.layout_plan
        if not plan or not self._base_snap:
            return ""
        types = {b.get("id"): b.get("type", "") for b in self._base_snap.get("buildings") or []}
        ids = {u.id for u in stuck}
        names = [self.data.building_name(types.get(sw.a, "")) for sw in plan.swaps
                 if isinstance(sw, Move) and sw.target in ids and sw.a in types]
        if names:
            return f"재배치 모드에서 빈 자리로 옮기기: {', '.join(names)} (배치도 번호 순서)"
        reach = getattr(plan, "reach_after", {}) or {}
        if ids and all(reach.get(i, 0) > 0 for i in ids):
            return "배치도의 최적 배치대로 옮기면 닿음 (재배치 모드에서 번호 순서대로)"
        return ""

    @staticmethod
    def _yield_text(r: dict) -> str:
        from .tracking.meta_state import RESOURCES
        parts = [f"미완성 {r['build_hits']}번 맞힘"] if r.get("build_hits") else []
        parts += [f"{RESOURCES[i]} +{v}" for i, v in enumerate(r.get("total") or []) if v]
        return " · ".join(parts) or "채집 없음"

    def _harvest_sim(self, base: dict, need: int, unf) -> List[tuple]:
        """채집 궤적: 지금 조준·추천 각도의 예상 경로와 결과. 계산은 계산 프로세스에 맡기고(가장 최근 요청만),
        여기서는 요청과 이미 나온 결과 표시만 한다. 표시할 글 줄을 돌려준다."""
        import math
        from .engine import harvest_sim as hs
        from .engine import sim_jobs
        geo_ = base.get("geo") or {}
        h = self._homography(geo_.get("proj"))
        if not geo_.get("colliders") or h is None or self.meta is None:
            return []
        team = hs.team_from_chars(self.meta.chars_raw, getattr(self, "_team_order", None))
        if not team:
            return []
        blds = {b["id"]: b for b in base.get("buildings") or [] if "id" in b}
        targets = {u.id: u.hits_left for u in unf}
        dur = self._harvest_dur
        # 배치·남은 자원·필요 자원·미완성 건물이 바뀔 때만 각도 탐색을 다시 한다
        lo, hi = self.aim_range.limits
        base_key = (layout_key(base), tuple(sorted((b, blds[b].get("res")) for b in blds)), need, len(team),
                    tuple(sorted(targets.items())), dur, round(lo), round(hi))
        # 발사 시작점 = 캐릭터 위치 (조준 중에 옆으로 움직인다) — 0.1 단위로 키에 넣어 움직이면 다시 계산
        lxy = geo_.get("launcher") or [0.0, 0.0]
        launch = (round(float(lxy[0]), 1), round(float(lxy[1]), 1))
        key = base_key + (launch,)

        def follow(res_key, path):
            """새 계산이 오기 전: 이전 결과의 경로를 캐릭터가 움직인 만큼 옮겨 그린다 (배치가 같을 때만)."""
            if not res_key or res_key[:-1] != base_key:
                return None
            dx, dy = launch[0] - res_key[-1][0], launch[1] - res_key[-1][1]
            return [hs.to_screen(h, x + dx, y + dy) for x, y in path or []]
        if self._sim_req.get("sweep") != key:
            self._sim_req["sweep"] = key
            self.sim.submit("sweep", key, sim_jobs.job_sweep, geo_, blds, team, dur, need, targets, lo, hi)
        lines = []
        self._stuck_text = ""
        now_path: list = []
        pl = base.get("player") or []
        if len(pl) >= 4:
            ang = round(math.degrees(math.atan2(pl[3], pl[2])))
            if self._sim_req.get("now") != (ang, key):
                self._sim_req["now"] = (ang, key)
                self.sim.submit("now", (ang, key), sim_jobs.job_now, geo_, blds, team, ang, dur, targets)
            got = self._sim_res.get("now")
            moved = follow(got[0][1], got[1].get("path")) if got and got[1] else None
            if moved is not None:                         # 각도·위치가 한두 번 늦어도 같은 배치면 따라 그린다
                r = got[1]
                now_path = moved
                lines.append((f"지금 조준 {r['angle']:.0f}°: {self._yield_text(r)}", (255, 255, 255, 230)))
        best_path: list = []
        got = self._sim_res.get("sweep")
        if got and got[1] and got[0][:-1] == base_key and got[1].get("top"):
            sweep_key = got[0]
            top, reach = got[1]["top"], got[1].get("reach") or {}
            # 어떤 각도로도 닿지 않는 미완성 건물: 배치도의 '길 열기'로 안내
            stuck = [u for u in unf if u.id in reach and not reach[u.id]]
            if stuck:
                opener = self._access_move_names(stuck)
                self._stuck_text = (" · ".join(self.data.building_name(u.type) for u in stuck)
                                    + ": 지금 배치로는 어떤 각도로도 닿지 않음 → "
                                    + (opener or "배치도에서 옆 건물을 옮겨 길을 여세요"))
            best = top[0]
            # 1위와 거의 같은 각도가 여럿이면(작업자가 오래 튕겨 어디로 쏴도 비슷하게 다 캐는 경우) 순위가 의미 없다 —
            # 그중 지금 조준에 가장 가까운 것을 보여 준다 (실제 화면: 162° 1위, 36° 2위가 1 차이)
            similar = [r for r in top if best.get("score", 0) - r.get("score", 0) <= 1.0]
            cur = math.degrees(math.atan2(pl[3], pl[2])) if len(pl) >= 4 else 90.0
            if len(similar) > 1:
                show = min(similar, key=lambda r: abs(r["angle"] - cur))
                best_path = follow(sweep_key, show.get("path")) or []
                lines.append((f"각도 차이 거의 없음 ({len(similar)}곳 비슷) — 지금 조준에 가까운 {show['angle']:.0f}° "
                              f"(파란 선): {self._yield_text(show)}", (120, 180, 255, 255)))
            else:
                best_path = follow(sweep_key, best.get("path")) or []
                lines.append((f"1위 {best['angle']:.0f}° (파란 선): {self._yield_text(best)}", (120, 180, 255, 255)))
                alts = " / ".join(f"{i}위 {r['angle']:.0f}° {self._yield_text(r)}" for i, r in enumerate(top[1:3], 2))
                if alts:
                    lines.append((alts, (170, 170, 180, 255)))
        else:
            lines.append(("추천 각도 계산 중…", (190, 190, 200, 255)))
        l_ok, h_ok = self.aim_range.learned
        if not (l_ok and h_ok):
            lines.append((f"추천 각도 범위 {lo:.0f}°~{hi:.0f}° (추정) — 마우스를 좌우 끝까지 밀면 게임 한계를 배웁니다",
                          (140, 140, 150, 255)))
        lines.append(("실제 채집 기록 비교: 궤적 7~11초 일치 · 채집량 밀·나무 일치, 돌 ±5 (기록 1회)", (140, 140, 150, 255)))
        self.base_overlay.set_paths(now_path, best_path)
        return lines

    def _update_dps(self):
        """전투 중(게임 상태 kPlaying)에만 초당 피해 창을 게임 창 위쪽 구석에 띄운다. 0.5초마다 다시 그린다."""
        m = self.dps_meter
        want = (self.settings.dps_meter and not self.user_hidden and self.bridge.live
                and self._dps_state == "kPlaying" and self.run.phase == "in_run" and self._game_active())
        if not want:
            if m.isVisible():
                m.hide()
            return
        now = time.monotonic()
        if now - self._dps_drawn_at >= 0.5 or not m.isVisible():
            self._dps_drawn_at = now
            rows, total = self.dps.rows()
            m.set_rows(rows, total, self.dps.window_s)
            game = geo.phys_to_logical_rect(self.window.rect)
            margin = 16
            x = game.right() - m.width() - margin if self.settings.dps_corner != "left" else game.left() + margin
            y = game.top() + max(96, game.height() // 10)      # 오른쪽 위 배속 버튼 아래
            m.move(geo.clamp_to_screen(QPoint(x, y), m.width(), m.height()))
        if not m.isVisible():
            m.show()

    def _update_hud(self):
        self._update_capture_exclusion()
        self._update_dps()
        hud = self.hud
        if hud.edit_mode:
            want = True
        elif self.user_hidden or not self.settings.hud_auto_show:
            want = False
        else:
            showing = ((self.tracker.session is not None and self.recommendation is not None)
                       or self.fusion_rec is not None or self.expedition is not None or self.char_combo is not None
                       or self.base_advice is not None)
            want = self._game_active() and (showing or hud.message_pending)
        if not want:
            if hud.isVisible():
                hud.hide()
            self.highlight.set_marks([])
            return
        self._position_hud()
        if not hud.isVisible():
            hud.show()
        self._update_highlight()

    def _update_highlight(self):
        """모든 선택지 카드에 판정 색 테두리와 이름표 (HUD 목록과 같은 색)."""
        rec, s = self.recommendation, self.tracker.session
        if not self.settings.card_outline or rec is None or s is None or rec.status in ("auto", "none"):
            self.highlight.set_marks([])
            return
        marks = []
        for e in rec.evals:
            card = e.card
            if card.rect[2] > 0 and card.rect[3] > 0:   # 능력치 페이지에서는 카드 위치가 아직 없다
                marks.append((geo.phys_to_logical_rect(s.frame.to_screen(card.rect)), card_verdict(rec, e),
                              card_badge(rec, e)))
        self.highlight.set_marks(marks)
        if not self.highlight.isVisible():
            self.highlight.show()

    def _position_hud(self):
        hud = self.hud
        hud.adjustSize()
        w = self.window
        if w is None:
            screen = QApplication.primaryScreen().availableGeometry()
            game = QRect(screen)
        else:
            game = geo.phys_to_logical_rect(w.rect)
        cards: List[QRect] = []
        panel = None
        s = self.tracker.session
        if s is not None:
            cards = [geo.phys_to_logical_rect(s.frame.to_screen(c.rect)) for c in s.cards]
            if s.panel_rect:
                panel = geo.phys_to_logical_rect(s.frame.to_screen(s.panel_rect))
        elif self.fusion_rec is not None and getattr(self, "_fusion_panel", None):
            fp = geo.phys_to_logical_rect(self._fusion_panel)
            if game.contains(fp):                 # 밀려 들어오는 도중 값(화면 밖)은 쓰지 않는다
                panel = fp
        spot = geo.levelup_hud_spot(game, [c for c in cards if c.width() > 0], hud.width(), hud.height())             if s is not None else None
        if spot is None:
            spot = geo.place_hud(game, cards, hud.width(), hud.height(), panel=panel)
        # 강화 선택창은 캐릭터 초상화 자리(장식)가 먼저. 게임이 알려 준 UI 영역(글자·버튼·캐릭터 목록)이 있으면
        # 그것을 가장 덜 가리는 자리로 — 캐릭터 선택창의 이름표, 융합 선택지, 설명 패널 등
        avoid = [geo.phys_to_logical_rect(r) for r in getattr(self, "_ui_avoid", [])] + [c for c in cards if c.width() > 0]
        if getattr(self, "_ui_avoid", None):
            spot = geo.place_avoiding(game, avoid, hud.width(), hud.height(), preferred=[spot])
        p = QPoint(spot.x() + self.settings.hud_offset_x, spot.y() + self.settings.hud_offset_y)
        hud.move(geo.clamp_to_screen(p, hud.width(), hud.height()))

    def _on_hud_moved(self, pos: QPoint):
        # 자동 배치 위치 대비 얼마나 옮겼는지 저장한다
        w = self.window
        game = geo.phys_to_logical_rect(w.rect) if w else QApplication.primaryScreen().availableGeometry()
        auto = geo.place_hud(game, [], self.hud.width(), self.hud.height())
        self.settings.hud_offset_x = pos.x() - auto.x()
        self.settings.hud_offset_y = pos.y() - auto.y()
        self.settings.save()

    def _set_hud_edit(self, on: bool):
        self.hud.set_edit_mode(on)
        if on and self.recommendation is None:
            self.hud.show_message("HUD 위치 조정", "끌어서 옮긴 뒤 설정 창에서 버튼을 다시 누르세요", tk.ACCENT)
        elif not on and self.recommendation is None:
            self.hud.hide()
        self._update_hud()

    def _reset_hud_offset(self):
        self.settings.hud_offset_x = self.settings.hud_offset_y = 0
        self.settings.save()
        self._update_hud()

    def toggle_hud(self):
        self.user_hidden = not self.user_hidden
        log.info("사용자 HUD %s", "숨김" if self.user_hidden else "표시")
        if not self.user_hidden and self.recommendation is None:
            self.hud.show_message("HUD 켜짐", "선택창이 열리면 추천이 표시됩니다", tk.TEXT_3, 1500)
        self._update_hud()

    def _quit_from_tray(self):
        log.info("알림 영역 메뉴에서 종료")
        self.app.quit()

    def toggle_control(self):
        if self.control.isVisible() and self.control.isActiveWindow():
            self.control.hide()
        else:
            self.show_control()

    def show_control(self):
        self.control.set_battle(self.battle_info)
        self.control.refresh_run()
        self._refresh_diagnostics()
        self.control.show()
        self.control.raise_()
        self.control.activateWindow()   # 사용자가 직접 연 창이므로 포커스를 준다

    # ---- 설정·수동 보정 ----
    def _on_settings_changed(self):
        if autostart.is_enabled() != self.settings.start_with_windows:
            autostart.set_enabled(self.settings.start_with_windows)
        self.hud.compact = self.settings.hud_compact
        self.tick.setInterval(self.settings.scan_interval_ms)
        self.hud.apply_scale(self.settings.font_scale)
        self.dps_meter.apply_scale(self.settings.font_scale)
        self.dps.window_s = float(self.settings.dps_window_s)
        if self.recommendation is not None and self.tracker.session is not None:
            self.hud.show_recommendation(self.recommendation, self.tracker.session.points_left)
        self._update_hud()

    def _on_run_edited(self):
        s = self.tracker.session
        if s is not None:
            self.recommendation = self.recommender.recommend(s, self.run)
            self.hud.show_recommendation(self.recommendation, s.points_left)
        self._update_hud()

    # ---- 진단 ----
    def _save_frame(self):
        self.pending_save = True
        self.scan_now(forced=True)

    def _write_frame(self, res: ScanResult):
        self.pending_save = False
        folder = os.path.join(APP_DIR, "debug")
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"frame_{stamp}.png")
        res.image.save(path)
        obs = res.observation
        with open(os.path.join(folder, f"frame_{stamp}.json"), "w", encoding="utf-8") as f:
            json.dump({"kind": obs.kind.value, "cards": [asdict(c) for c in obs.cards],
                       "inventory": [asdict(s) for s in obs.inventory or ()], "character": obs.character_id,
                       "gold": obs.gold, "reroll_cost": obs.reroll_cost, "free_rerolls": obs.free_rerolls,
                       "banish_left": obs.banish_left, "points_left": obs.points_left},
                      f, ensure_ascii=False, indent=1, default=str)
        self.control.saved_label.setText(f"저장함: {path}")
        log.info("진단용 게임 화면 저장: %s", path)

    def _refresh_diagnostics(self):
        w = self.window
        res = self.last_result
        if self.bridge.live:
            conn = "게임 연동 중"
        else:
            conn = ("게임 연결됨 · 화면 인식" if w else "게임 창 없음")
        self.control.set_connection(conn)
        if not self.control.isVisible():
            return
        obs = res.observation if res else None
        rows = {
            "연결 방식": (f"게임 연동 (BepInEx 플러그인) · 메시지 {self.bridge.messages}건"
                      + (f" · 게임 {self.bridge.game_version}" if self.bridge.game_version else ""))
            if self.bridge.live else "화면 인식 (게임 연동 없음 — 플러그인 미설치이거나 게임이 꺼져 있음)",
            "게임 창": (f"{w.size[0]}×{w.size[1]} · 위치 {w.origin} · DPI {w.dpi}"
                      + (" · 앞에 있음" if w.foreground else " · 뒤에 있음") + (" · 최소화" if w.minimized else ""))
            if w else "찾지 못함 (Balls.exe)",
            "캡처": (obs.frame.backend if obs and obs.frame else (obs.error if obs else "-")) or "-",
            "화면 판별": (SCREEN_LABEL.get(obs.kind, obs.kind.value) + (f" ({res.skipped})" if res.skipped else "")
                      + (f" — {obs.error}" if obs.error else "")) if obs else "-",
            "게임 로그": (f"{ {'in_run': '런 진행 중', 'base': '기지', 'unknown': '미확인'}[self.logw.phase]} · "
                      f"{self.base_state or '-'}") if self.logw.available else "없음",
            "게임 버전": self.logw.game_version or "미확인",
            "데이터 빌드": f"Steam {self.data.game_build_id}",
            "OCR": self.ocr_status.message,
            "HUD": "캡처 제외 " + {True: "확인", False: "실패", None: "아직 표시 안 됨"}[self.hud.capture_excluded]
                   + " · 클릭 통과 " + {True: "켜짐", False: "꺼짐", None: "-"}[self.hud.click_through],
            "입력": ("단축키 " + ("사용" if self.inputs.keyboard_ok else "불가")
                   + " · 클릭 기록 " + ("사용" if self.inputs.mouse_ok else "불가")),
            "버린 늦은 결과": f"{self.stale_results}건",
            "연동 부하": (f"게임 쪽 읽기 {self._plugin_cost:.1f}ms/회" if self._plugin_cost is not None
                      else "게임 쪽 읽기 - (플러그인 1.7부터)")
                     + f" · 도우미 기지 화면 처리 {self._base_ms:.1f}ms/회 (궤적 계산은 별도 프로세스)",
        }
        cards = "-"
        if obs is not None and obs.kind == ScreenKind.LEVEL_UP:
            parts = []
            d = self.data
            for c in obs.cards:
                label = CARD_LABEL_KO.get(c.label, "")
                name = d.name(c.item_id) if c.item_id else (
                    f"읽지 못함 (가장 비슷: {d.name(c.guess_id)})" if c.guess_id else "읽지 못함")
                score = f" · 오차 {c.icon_error:.0f}/차이 {c.icon_margin:.0f}" if c.icon_error is not None else ""
                parts.append(f"{c.position}: {name}{' · ' + label if label else ''}"
                             f"{' ' + str(c.shown_level) if c.shown_level else ''}{score}")
            if obs.inventory is not None:
                held = [f"{d.name(s.item_id) if s.item_id else '읽지 못함'}"
                        f" {s.level if s.level is not None else '?'}" for s in obs.inventory if s.occupied]
                parts.append("보유 칸: " + (", ".join(held) if held else "비어 있음"))
            parts.append(f"캐릭터 {d.name(obs.character_id) if obs.character_id else '미확인'} · "
                         f"골드 {obs.gold if obs.gold is not None else '?'} · "
                         f"새로고침 {('무료 ' + str(obs.free_rerolls) + '회') if obs.free_rerolls is not None else (str(obs.reroll_cost) + '골드' if obs.reroll_cost is not None else '?')}")
            cards = "\n".join(parts)
        timing = "아직 없음"
        if self.timings:
            lines = []
            for key, name in (("capture", "캡처"), ("ocr", "글자 인식"), ("icons", "아이콘 비교"), ("total", "전체")):
                vals = sorted(t[key] for t in self.timings if key in t)
                if vals:
                    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
                    lines.append(f"{name}: 중앙값 {statistics.median(vals):.0f}ms · p95 {p95:.0f}ms")
            timing = "\n".join(lines) + f"\n표본 {len(self.timings)}회"
        self.control.update_diagnostics(rows, cards, timing)
