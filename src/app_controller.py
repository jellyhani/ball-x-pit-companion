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
from .services import diagnostics
from .services import game_window as gw
from .services.mod_guard import ModGuard
from .services.bridge_client import BridgeClient
from .services.input_watch import InputWatcher
from .services.log_watcher import LogEvent, PlayerLogWatcher
from .services.recognition_worker import RecognitionService, ScanResult
from .services.settings import APP_DIR, Settings
from .tracking import unlocks
from .tracking.bridge_adapter import BridgeState, catalog_recipes, catalog_schedules, convert, infer_pick
from .tracking.choice_tracker import ChoiceTracker, TrackerEvent
from .tracking.meta_state import RESOURCES, MetaState, parse_meta
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
from .engine.harvest import (
    AimRange,
    HarvestLog,
    advise_harvest,
    gold_bounce_tip,
    layout_key,
    need_resource,
    unfinished_buildings,
)
from .services.sim_worker import SimWorker
from .engine.base_advisor import suggest as suggest_base
from .ui.control_window import ControlWindow
from .ui.hud import RecommendationHud
from .ui.tray import Tray, app_icon
from .i18n import tr

log = logging.getLogger(__name__)


CARD_LABEL_KO = {CardLabel.NEW: tr("신규"), CardLabel.UPGRADE: tr("강화")}


class AppController(QObject):
    def __init__(self, app: QApplication, data: Optional[GameData] = None):
        super().__init__()
        self.app = app
        self.data = data or load_game_data()
        if self.data.available is None:
            self.data.available = unlocks.load(
                unlocks.default_path()
            )  # 잠긴 재료가 든 진화를 추천에서 빼는 데 씀
        self.settings = Settings.load()
        self.run = RunState()
        self.tracker = ChoiceTracker()
        self.recommender = Recommender(self.data)
        self.recommender.discovery_mode = self.settings.encyclopedia_mode
        self.fusion = FusionAdvisor(self.data)
        self.fusion.discovery_mode = self.settings.encyclopedia_mode
        self.fusion_rec: Optional[FusionRecommendation] = None
        self.char_combo = None  # 캐릭터 선택 화면의 조합 추천 (알선소가 있을 때)
        self._char_combo_key = None  # (state, char1, char2) — 바뀔 때만 다시 계산
        self._loadout_seen = (
            None,
            None,
        )  # 로드아웃 화면이 잠깐 비활성화돼 char1/char2 를 못 받은 폴링을 버틴다
        self._loadout_miss = 0
        self.base_advice = None  # 기지에서 메뉴 없이 서 있을 때 뭘 지을지·철거할지
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
        self.dump_dir = (
            APP_DIR  # 진단용 파일(live_state·live_meta·채집 궤적) 위치 — 테스트는 임시 폴더로 바꾼다
        )
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
        self._sim_req: Dict[str, object] = {}  # 채널 → 마지막으로 보낸 요청 키
        self._sim_res: Dict[str, tuple] = {}  # 채널 → (요청 키, 결과)
        self._sim_retry_at: Dict[str, float] = {}  # 실패 뒤 같은 입력을 다시 계산할 시각
        self._hcache: tuple = ((), None)  # 화면 대응점 → 월드→화면 변환 (SVD 3ms 라 한 번만)
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
        self._picked_sig = None  # 게임 상태로 확정한 마지막 선택창의 카드 묶음과 시각
        self._echo_sessions: set = set()
        self._counters: Optional[dict] = None  # 게임의 새로고침·삭제 횟수 (마지막 스냅샷)
        self._session_counters: Dict[int, dict] = {}  # 선택창이 열릴 때의 횟수
        self.meta: Optional[MetaState] = None
        self.expedition: Optional[ExpeditionAdvice] = None
        self.recorder = RunRecorder(os.path.join(APP_DIR, "runs.jsonl"))
        self.draws = DrawStats(os.path.join(APP_DIR, "draws.jsonl"))
        self.recommender.draw_weights = self.draws.weights()
        self.snapshots = SnapshotLog(os.path.join(APP_DIR, "snapshots"))
        self._last_levelup_snap: Optional[dict] = None
        self.control.set_history(self.recorder.load())
        self.recommender.history = self.recorder.load()  # 캐릭터별 항목 성적 (내 기록)
        self.control.set_draw_summary(self.draws.summary())
        self.mod_guard = ModGuard(self.settings, self.data.game_build_id, lambda: self.bridge.live)
        self.mod_guard.status.connect(self.control.set_mod_status)
        self.mod_guard.notice.connect(lambda text: self.tray.showMessage(tr("BALL x PIT 도우미"), text))
        self.control.mod_install_requested.connect(self.mod_guard.install_now)
        self.layout_win = LayoutWindow(self.data)
        self.layout_plan = None
        self._layout_busy = False
        self.control.layout_requested.connect(self.open_layout)
        self.layout_win.recalc_requested.connect(lambda: self.compute_layout(force=True))
        self.hud.compact = self.settings.hud_compact
        self.inputs = InputWatcher()
        self._aim_extended = False
        self.inputs.extend_aim.connect(self._on_aim_extended)
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

        log.info(
            "시작: 데이터 빌드 %s, OCR %s, 네이티브 계산 %s",
            self.data.game_build_id,
            self.ocr_status.ok,
            "사용" if native.lib() is not None else "없음 (파이썬 계산)",
        )

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
        diagnostics.emit("tracking.source", stream="active", state=(self.bridge.live, self.bridge.connected),
                         bridge_live=self.bridge.live, connected=self.bridge.connected,
                         receive_age_ms=round((time.monotonic() - self.bridge.last_at) * 1000, 1)
                         if self.bridge.last_at else None,
                         ocr_enabled=self.settings.watch_enabled, ocr_available=self.ocr_status.ok)
        if self.bridge.live:
            # 게임 연동 중: 화면 인식은 쉬고, HUD 표시 판단용으로 게임 창 상태만 갱신한다
            self.window = gw.find_game_window()
            self._update_hud()
            if self.base_overlay.isVisible() and not self._game_active():
                self.base_overlay.hide()
            return
        if self.base_overlay.isVisible():
            self.base_overlay.hide()  # 연동이 끊김: 마지막 안내가 남지 않게
        if not self.settings.watch_enabled or not self.ocr_status.ok:
            return
        if self.recog.inflight_job is not None:
            self.recog.restart_if_stuck(8.0)
            return
        if self.logw.available and self.logw.phase == "base":
            return  # 기지 화면에는 강화 선택창이 없다
        forced = (
            self.tracker.session is not None
            or self.tracker.awaiting_confirmation
            or time.monotonic() < self.force_until
        )
        self.recog.request(self.tracker.generation, forced=forced, keep_image=self.pending_save)

    def _log_scan_change(self, res: ScanResult):
        """판별 결과가 바뀔 때만 기록한다(매 틱 기록하지 않음). 인식이 안 될 때 원인을 찾기 위한 것."""
        observation, w = res.observation, res.window
        skipped = (
            "" if res.skipped in ("화면 움직임", "이전 화면과 같음") else res.skipped
        )  # 평소 동작은 기록하지 않음
        state = (
            observation.kind,
            skipped,
            observation.error,
            w.size if w else None,
            w.foreground if w else None,
            observation.frame.backend if observation.frame else "",
        )
        if state == getattr(self, "_last_scan_state", None):
            return
        self._last_scan_state = state
        log.info(
            "인식 상태: %s%s%s | 창 %s %s | 캡처 %s | %s",
            observation.kind.value,
            f" (건너뜀: {res.skipped})" if res.skipped else "",
            f" 오류: {observation.error}" if observation.error else "",
            w.size if w else "-",
            "앞" if w and w.foreground else "뒤",
            observation.frame.backend if observation.frame else "-",
            " ".join(f"{field_name}={value:.0f}ms" for field_name, value in res.timings_ms.items()),
        )

    def scan_now(self, forced: bool = True):
        self.force_until = time.monotonic() + 2.0
        self.recog.restart_if_stuck(4.0)
        if self.recog.request(self.tracker.generation, forced=forced, keep_image=self.pending_save) is None:
            log.info("인식 작업 진행 중(%.1f초)이라 다음 차례에 읽습니다", self.recog.busy_for())

    def _on_scan(self, res: ScanResult):
        self._log_scan_change(res)
        self.last_result = res
        if res.image is not None and self.pending_save:
            self._write_frame(res)
        if self.bridge.live:
            return  # 진단 캡처가 현재 게임 연동의 선택 상태를 덮어쓰지 않는다.
        if res.window is not None or res.observation.kind == ScreenKind.GAME_NOT_FOUND:
            self.window = res.window
        if res.ocr_ran:
            self.timings.append(dict(res.timings_ms))
        if res.generation != self.tracker.generation:
            self.stale_results += 1  # 새로고침·런 전환 전에 찍은 화면 → 버린다
            self._update_hud()
            return
        observation = res.observation
        if res.skipped and res.skipped != "이전 화면과 같음":
            self._update_hud()
            return
        if observation.kind == ScreenKind.LEVEL_UP and self.run.phase != "in_run" and not self.logw.available:
            self.run.join_mid_run()  # 로그 없이 선택창을 처음 본 경우
        forced = time.monotonic() < self.force_until
        self._handle(self.tracker.observe(observation, time.monotonic(), forced=forced))
        self._update_hud()

    # ---- 게임 연동 ----
    def _on_bridge_snapshot(self, snapshot: dict, at: float):
        if isinstance(snapshot.get("meta"), dict):
            try:
                with open(
                    os.path.join(self.dump_dir, "live_meta.json"), "w", encoding="utf-8"
                ) as file_handle:
                    json.dump(snapshot, file_handle, ensure_ascii=False)
            except OSError:
                pass
            previous_discovery = getattr(self.meta, "discovery", None)
            self.meta = parse_meta(snapshot["meta"], self.data)
            worker_order = snapshot["meta"].get("worker_order")
            if isinstance(worker_order, list):
                self._team_order = [c for c in worker_order if isinstance(c, str)]
            if getattr(self, "_harvest_pending", False) and self.meta.resources:
                self._harvest_pending = False
                row = self.harvest_log.finish(list(self.meta.resources))
                if row:
                    log.info("채집 기록: 조준 %.0f° → 증가 %s", row["angle"], row["gain"])
            self.recommender.meta = self.meta
            self.fusion.meta = self.meta
            # 건물 개수 × 공략의 고정 생산량으로 새 생산 건물 건설을 권하지 않는다.
            self.control.set_meta(self.meta)
            if self.settings.encyclopedia_mode and previous_discovery != self.meta.discovery:
                if self.tracker.session is not None:
                    self.recommendation = self.recommender.recommend(self.tracker.session, self.run)
                    self.hud.show_recommendation(self.recommendation, self.tracker.session.points_left)
                    self._update_hud()
            return
        if isinstance(snapshot.get("catalog"), dict):
            self.snapshots.set_catalog(snapshot)
            from .gamedata import save_catalog, apply_catalog

            save_catalog(snapshot["catalog"])
            n = apply_catalog(self.data, snapshot["catalog"])
            k = snapshot["catalog"].get("max_solo_lvl")
            log.info(
                "게임 연동: 게임 안 레시피 %d개로 교체 (위키 레시피 대신), 단독 최대 레벨 %s, 플러그인 %s, "
                "레벨별 수치 %d개",
                n,
                (k + 1) if isinstance(k, int) else "?",
                snapshot.get("plugin") or "1.1 이하",
                len(self.data.level_props),
            )
            self.control.refresh_run()
            return
        now = time.monotonic()
        if now - at > .25:
            diagnostics.emit("state.ui_backlog", stream="queue", interval=5.,
                             sequence=snapshot.get("seq"), receive_to_ui_ms=round((now - at) * 1000, 2))
        if now - getattr(self, "_live_dump_at", 0.0) >= 0.5:
            # 개발·진단용: 최신 게임 연동 상태를 파일로 (tools/drive.py state)
            self._live_dump_at = now
            try:
                tmp = os.path.join(self.dump_dir, "live_state.json.tmp")
                with open(tmp, "w", encoding="utf-8") as file_handle:
                    json.dump(snapshot, file_handle, ensure_ascii=False)
                os.replace(tmp, os.path.join(self.dump_dir, "live_state.json"))
            except OSError:
                pass
        if self.window is None:
            self.window = gw.find_game_window()
        origin = self.window.origin if self.window else (0, 0)
        bridge_state = convert(snapshot, self.data, origin, frame_id=int(snapshot.get("seq") or 0), at=at)
        self.bridge_state = bridge_state
        base_state = snapshot["base"].get("state") if isinstance(snapshot.get("base"), dict) else None
        page = snapshot["levelup"].get("page") if isinstance(snapshot.get("levelup"), dict) else None
        observed_kind = bridge_state.observation.kind.value
        diagnostics.emit("state.interpreted", stream="game",
                         state=(bridge_state.game_state, base_state, page, observed_kind),
                         sequence=snapshot.get("seq"), game_state=bridge_state.game_state,
                         base_state=base_state, page=page, observed=observed_kind,
                         choices=len(bridge_state.observation.cards), unknown=len(bridge_state.unknown_types),
                         receive_to_ui_ms=round(max(0., now - at) * 1000, 2))
        self._read_ui_avoid(snapshot, bridge_state)
        building = snapshot.get("battle")
        if isinstance(building, dict):
            self._counters = {
                "rerolls": building.get("rerolls"),
                "free": building.get("free_rerolls"),
                "banishes": building.get("banishes"),
                "banished": len(building.get("banished") or []),
            }
        new_unknown = set(bridge_state.unknown_types) - self._logged_unknown
        if new_unknown:
            self._logged_unknown |= new_unknown
            log.warning("게임 연동: 데이터에 없는 항목 %s", sorted(new_unknown))
        if bridge_state.damage and snapshot.get("battle") is not None:
            self.run.damage = dict(bridge_state.damage)
            b0 = snapshot["battle"]
            t = b0.get("elapsed") if isinstance(b0.get("elapsed"), (int, float)) else snapshot.get("t")
            if isinstance(t, (int, float)):
                self.dps.add(float(t), bridge_state.damage)
        self._dps_state = snapshot.get("game_state") or ""
        if isinstance(snapshot.get("cost_ms"), (int, float)):
            self._plugin_cost = float(snapshot["cost_ms"])  # 플러그인 1.7: 게임 프레임에서 읽기에 쓴 시간
        t0 = time.perf_counter()
        self._trace_identity = {
            "game_version": snapshot.get("game_version", ""),
            "plugin_version": snapshot.get("plugin", ""),
            "sequence": snapshot.get("seq"),
        }
        try:
            self._on_base(snapshot.get("base") if isinstance(snapshot.get("base"), dict) else None)
        except Exception as error:  # noqa: BLE001 — 기지 화면 오류가 선택창·융합 처리를 막지 않게
            diagnostics.emit("state.failed", stream="base", state=type(error).__name__, phase="base",
                             sequence=snapshot.get("seq"), error_type=type(error).__name__)
            if not getattr(self, "_base_error_logged", False):
                log.exception("기지 화면 처리 오류 (한 번만 기록)")
                self._base_error_logged = True
        self._base_ms = 0.9 * self._base_ms + 0.1 * (time.perf_counter() - t0) * 1000
        base_elapsed_ms = (time.perf_counter() - t0) * 1000
        diagnostics.emit("state.base_timing", stream="base", state=base_elapsed_ms >= 100,
                         sequence=snapshot.get("seq"), elapsed_ms=round(base_elapsed_ms, 2),
                         moving_average_ms=round(self._base_ms, 2))
        if bridge_state.battle is not None:
            self.battle_info = bridge_state.battle
            self.dps_meter.set_battle(bridge_state.battle)
        self._update_expedition(bridge_state)
        if snapshot.get("battle") is not None and self.recorder.current is not None:
            self.recorder.update(
                self.run,
                bridge_state.observation.progress,
                completed=(snapshot["battle"] or {}).get("completed_level"),
                game_over=bridge_state.game_over is not None,
            )
        if not bridge_state.in_run:
            if self.run.phase == "in_run" and snapshot.get("battle") is None:
                self._handle(self.tracker.reset())
                self.run.end_run(tr("게임 연동: 런 밖"))
                self._finish_run_record()
                self.control.refresh_run()
            self._handle(self.tracker.observe(bridge_state.observation, time.monotonic(), immediate=True))
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
            self.recorder.start()  # 도우미를 런 도중에 켰거나 다시 연결됨 → 이어서 기록
        observation = bridge_state.observation
        if bridge_state.max_ball_level:
            self.data.set_observed_max_level("ball", bridge_state.max_ball_level)
        for card in observation.cards:
            # 게임이 이 레벨로 강화하는 카드를 줬다면 최대 레벨은 적어도 그만큼이다
            if card.shown_level and card.item_id:
                self.data.note_level_seen(self.data.items[card.item_id].kind, card.shown_level)
        self.run.apply_character(observation.character_id, observation.extra_characters, source="game")
        self._note_unlocks(observation)
        self._update_fusion(observation)
        if observation.kind == ScreenKind.LEVEL_UP and self.tracker.session is None:
            log.info("게임 연동 원본(선택창): %s", json.dumps(snapshot, ensure_ascii=False)[:4000])
            self._last_levelup_snap = snapshot
        inventory_changed = (
            observation.inventory is not None and observation.inventory != self._bridge_inventory
        )
        if observation.inventory is not None:
            self._bridge_inventory = observation.inventory
            if self.tracker.session is None:
                self.run.apply_inventory(observation.inventory, self.data)
            self._resolve_pending_pick(observation.inventory)
        events = self.tracker.observe(observation, time.monotonic(), immediate=True)
        current = self.tracker.session
        if (
            current is not None
            and current.session_id in self._echo_sessions
            and not self._is_after_pick_echo(current)
        ):
            # 잔상으로 보류한 동안 카드가 그대로면 updated가 오지 않는다. 보호 시간이 끝나면 한 번 재처리한다.
            events = [evaluation for evaluation in events if evaluation.session is not current]
            events.append(TrackerEvent("opened", current))
        self._handle(events)
        if inventory_changed and not events and self.control.isVisible():
            # 융합 뒤 전투로 돌아오면 선택창 이벤트가 없다. 이미 열린 보유 목록도 새 슬롯 내용으로 갱신한다.
            self.control.refresh_run()
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
        signature, at = sig
        return s.signature == signature and time.monotonic() - at < 8.0

    def _on_bridge_status(self, _text: str):
        if not self.bridge.connected and self.recorder.current is not None:
            self._finish_run_record()  # 게임을 끄면 연동이 끊긴다 → 진행 중이던 런은 '중단'으로 남긴다
        self.control.refresh_run()

    def _finish_run_record(self):
        recommendation = self.recorder.finish()
        if recommendation is not None:
            log.info(
                "런 기록 저장: %s, %s턴, 선택 %d번",
                recommendation.result,
                recommendation.turn,
                len(recommendation.picks),
            )
            self.control.set_history(self.recorder.load())
            self.recommender.history = self.recorder.load()

    def _update_expedition(self, bridge_state: BridgeState):
        """보스를 깬 뒤 런 종료 화면에 '원정 계속' 버튼이 있으면 계속/복귀를 판단해 보여 준다."""
        go = bridge_state.game_over
        if go is not None and go.completed and go.endless_button:
            if self.expedition is None:
                p = bridge_state.observation.progress
                best = None
                if self.meta and p is not None:
                    best = (
                        next(
                            (
                                lv.get("best_endless")
                                for lv in self.meta.levels
                                if lv.get("type") == p.level_name
                            ),
                            None,
                        )
                        or None
                    )
                from .tracking.run_history import expedition_history

                self.expedition = advise_expedition(
                    self.run, self.data, p, best, expedition_history(self.recorder.load())
                )
                if self.recorder.current is not None:
                    self.recorder.current.expedition = dict(self.expedition.snapshot)
                log.info(
                    "원정 계속 판단: %s | %s", self.expedition.headline, "; ".join(self.expedition.reasons)
                )
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

    def _read_ui_avoid(self, snapshot: dict, bridge_state):
        """플러그인 1.11: 지금 화면에서 가리면 안 되는 게임 UI 영역 (화면 밖·미끄러져 들어오는 중인 값은 버림)."""
        from .tracking.bridge_adapter import _rect

        ui = snapshot.get("ui") if isinstance(snapshot.get("ui"), dict) else {}
        frame = bridge_state.observation.frame
        size = (int(snapshot.get("screen_w") or 0), int(snapshot.get("screen_h") or 0))
        rectangles = [_rect(value, size) for value in ui.get("avoid") or []]
        self._ui_screen = ui.get("screen") or ""
        self._ui_avoid = (
            [frame.to_screen(rectangle) for rectangle in rectangles if rectangle] if frame is not None else []
        )

    def _update_char_combo(self, base: Optional[dict], state: str):
        """캐릭터 선택 화면: 알선소가 지어져 있으면 캐릭터 조합을 추천한다 (커뮤니티 추천 + 내 기록).
        플러그인 1.12: 로드아웃 화면의 두 캐릭터 패널로 '이미 고른 캐릭터'를 안다 (base.loadout.char1/char2).
        하나만 골랐으면 그 캐릭터와 맞는 조합만 추리고, 둘 다 골랐으면 그 조합의 궁합을 보여준다."""
        loadout = (base or {}).get("loadout") or {}
        raw1, raw2 = loadout.get("char1"), loadout.get("char2")
        has_matchmaker = base is not None and any(
            building.get("type") == "kMatchMaker" and building.get("state", "kNormal") == "kNormal"
            for building in base.get("buildings") or []
        )
        in_flow = (
            self.meta is not None and has_matchmaker and state in ("kSelectingChar", "kSelectingLoadout")
        )
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
            # 값을 그대로 쓴다. (2026-09-26 실측: 캐릭터를 고르는 동안 로드아웃 화면은 대부분 비활성이고 몇 초에 한 번만
            # 잡힌다 — 5번만 봐 주면 5초마다 '고정' 추천이 풀려 엉뚱한 조합이 떴다.) 화면 흐름을 떠나면(위 in_flow)
            # 그때 놓는다. 다시 고르면 로드아웃이 잡힐 때 새 값으로 바뀐다.
            self._loadout_miss += 1
            char1, char2 = self._loadout_seen
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

        game_data = self.data

        if char1 and char2:
            pairs = suggest_pairs(
                game_data,
                self.meta.chars_raw,
                self.recommender.history,
                limit=1,
                exact=(char_id(char1), char_id(char2)),
            )
            if not pairs:
                self.char_combo = None
                return
            p = pairs[0]
            tone, status = (
                ("accent", tr("좋음"))
                if p.score >= 8
                else (("neutral", tr("보통")) if p.score >= 3 else ("neutral", tr("정보 없음")))
            )
            value = HudView(
                title=f"{game_data.name(p.a)} + {game_data.name(p.b)}",
                subtitle=tr("이번 원정 캐릭터 궁합"),
                status=status,
                status_tone=tone,
            )
            value.lines = [
                (result, "secondary")
                for result in (p.reasons[:3] or [tr("커뮤니티 추천·내 기록·전략 궁합 어디에도 안 걸림")])
            ]
            self.char_combo = pairs
            log.info(
                "캐릭터 궁합: %s + %s (%.1f) %s",
                game_data.name(p.a),
                game_data.name(p.b),
                round(p.score, 1),
                p.reasons,
            )
            self.hud.render_view(value)
            return

        fixed = char_id(char1) if char1 else None
        pairs = suggest_pairs(game_data, self.meta.chars_raw, self.recommender.history, limit=4, fixed=fixed)
        if not pairs:
            self.char_combo = None
            return
        best = pairs[0]
        subtitle = (
            tr("{v0}와 좋은 조합 (알선소)", v0=game_data.name(fixed))
            if fixed
            else tr("캐릭터 조합 추천 (알선소)")
        )
        value = HudView(
            title=f"{game_data.name(best.a)} + {game_data.name(best.b)}",
            subtitle=subtitle,
            status=tr("추천"),
            status_tone="accent",
        )
        value.lines = [(result, "secondary") for result in best.reasons[:2]]
        value.section = tr("다른 조합")
        value.rows = [
            HudRow(
                (), f"{game_data.name(p_.a)} + {game_data.name(p_.b)}", p_.reasons[0] if p_.reasons else ""
            )
            for p_ in pairs[1:]
        ]
        value.footer = [
            (tr("커뮤니티 추천(Dexerto·Screen Rant·Steam)과 내 런 기록 기준 — 참고용"), "tertiary")
        ]
        self.char_combo = pairs
        log.info(
            "캐릭터 조합 추천%s: %s",
            f" ({game_data.name(fixed)} 고정)" if fixed else "",
            [(game_data.name(p_.a), game_data.name(p_.b), round(p_.score, 1)) for p_ in pairs],
        )
        self.hud.render_view(value)

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
        todo = suggest_base(self.meta, self.data, limit=3, base=self._base_snap)
        demolish = list(getattr(self.layout_plan, "demolish", None) or [])[:3]
        from .engine.harvest import advise_workers
        from .engine.layout_opt import GUIDE_DEMOLISH

        need, _ = need_resource(self.meta, self._shortfalls())
        workers = advise_workers(
            self.meta,
            self.meta.chars_raw,
            [building.type for building in self.meta.buildings],
            self.data,
            need,
        )[:3]
        key = (
            tuple(
                (
                    suggestion.kind,
                    suggestion.type,
                    suggestion.cost,
                    suggestion.affordable,
                    suggestion.missing_text,
                    suggestion.priority_group,
                    suggestion.reason,
                    suggestion.source,
                )
                for suggestion in todo
            ),
            tuple((t, s) for _, t, s, *_r in demolish),
            tuple((a.char_id, a.building, a.action) for a in workers),
        )
        if not todo and not demolish and not workers:
            if self.base_advice is not None:
                self.base_advice = None
                self._base_advice_key = None
            return
        if key == self._base_advice_key:
            return
        self._base_advice_key = key
        from .ui.hud import HudRow, HudView

        game_data = self.data
        verb = {"finish": tr("완성"), "build": tr("짓기"), "upgrade": tr("강화")}
        items = [
            (
                f"{verb[suggestion.kind]}: {suggestion.name}",
                f"{suggestion.policy_label} · {suggestion.status}",
            )
            for suggestion in todo
        ]
        items += [
            (
                tr("철거 후보: {v0}", v0=game_data.building_name(t)),
                tr("가이드") if t in GUIDE_DEMOLISH else tr("기여 {score:.1f}", score=score),
            )
            for _i, t, score, *_r in demolish
        ]
        items += [
            (
                tr(
                    "일꾼 빼기: {v0}의 {v1} → 발사로",
                    v0=game_data.building_name(a.building),
                    v1=game_data.name(a.char_id),
                )
                if a.action == "remove"
                else tr(
                    "일꾼: {v0} ← {v1}", v0=game_data.building_name(a.building), v1=game_data.name(a.char_id)
                )
                + (tr(" ({v0} 대신)", v0=game_data.name(a.replace)) if a.action == "swap" else ""),
                {"swap": tr("교체"), "remove": tr("금광 안 씀")}.get(a.action, tr("빈 건물")),
            )
            for a in workers
        ]
        title, status = items[0]
        value = HudView(
            title=title,
            subtitle=tr("기지 조언"),
            status=status,
            status_tone="warn" if todo and not todo[0].affordable else "accent",
        )
        if len(items) > 1:
            value.section = tr("다른 항목")
            value.rows = [HudRow((), t, s) for t, s in items[1:]]
        value.footer = [(tr("배치 효과·부족 자원 기준 — F10 설정창에서 더 자세히"), "tertiary")]
        self.base_advice = items
        log.info("기지 조언: %s", items)
        self.hud.render_view(value)

    def _note_unlocks(self, observation: ScreenObservation):
        """강화 선택창의 후보 목록 + 화면 카드 + 보유 + 삭제한 것 = 해금된 전체 (tracking/unlocks.py)."""
        if observation.pool is None:
            return
        ids = (
            [index for index, _ in observation.pool.entries()]
            + list(observation.pool.prev)
            + [card.item_id for card in observation.cards]
        )
        ids += [s.item_id for s in observation.inventory or ()] + list(
            observation.progress.banished if observation.progress else ()
        )
        if self.data.note_available(ids):
            unlocks.save(unlocks.default_path(), self.data.available)
            log.info("해금 목록 갱신: %d개", len(self.data.available))

    def _update_fusion(self, observation: ScreenObservation):
        """융합 화면(진화·융합·무료 강화)이 열려 있으면 무엇과 무엇을 합칠지 추천한다."""
        if observation.kind != ScreenKind.FUSION:
            if self.fusion_rec is not None:
                self.fusion_rec = None
                self._fusion_panel = None
                self.hud.hide()
            return
        # 융합 창 위치 (HUD 가 가리지 않게 — 강화 선택창과 같은 방식)
        self._fusion_panel = (
            observation.frame.to_screen(observation.panel_rect)
            if observation.frame is not None and observation.panel_rect
            else None
        )
        recommendation = self.fusion.recommend(observation.fuser, observation.inventory, self.run)
        if self.fusion_rec is None or recommendation.headline != self.fusion_rec.headline:
            log.info(
                "융합 추천(%s): %s | 진화 %s | 융합 %s | 분열 %s",
                recommendation.status,
                recommendation.headline,
                [(p.title, round(p.score, 1)) for p in recommendation.evos],
                [(p.title, round(p.score, 1)) for p in recommendation.combos],
                round(recommendation.free.score, 1) if recommendation.free else None,
            )
        self.fusion_rec = recommendation
        diagnostics.emit("recommendation.fusion", stream="fusion",
                         state=(recommendation.status, recommendation.headline),
                         status=recommendation.status, evolutions=len(recommendation.evos),
                         combinations=len(recommendation.combos), free_upgrade=recommendation.free is not None)
        self.hud.show_fusion(recommendation)

    def _resolve_pending_pick(self, inventory):
        pend = getattr(self, "_pending_pick", None)
        if pend is None or inventory is None:
            return
        session, since, result = pend
        other = self._counter_outcome(session.session_id)
        if other is not None:
            # 보유 목록이 아니라 새로고침·삭제 횟수가 바뀌었다 → 고른 것이 아니다
            self._pending_pick = None
            result = PickOutcome(
                result.session_id, other, None, tr("게임 상태로 확인 (횟수 변화)"), result.options
            )
            self.run.apply_outcome(result, self.data)
            log.info("세션 %s 종료: %s (%s)", result.session_id, other, result.evidence)
            return
        card = infer_pick(session.inventory, inventory, session.cards)
        expired = time.monotonic() - since > 3.0
        if card is None and not expired:
            return
        self._pending_pick = None
        if card is not None:
            # 창을 닫기만 했거나 확인 시간이 지났다는 이유로 실제 선택의 잔상이라고 판단하지 않는다.
            self._picked_sig = (session.signature, time.monotonic())
            result = PickOutcome(result.session_id, "picked", card, tr("게임 상태로 확인"), result.options)
        self.run.apply_outcome(result, self.data)
        self.run.apply_inventory(inventory, self.data)
        log.info(
            "세션 %s 선택 확정: %s (%s)%s",
            result.session_id,
            self.data.name(result.card.item_id) if result.card else result.kind,
            result.evidence,
            ""
            if result.card
            else f" | 횟수 열림 {self._session_counters.get(session.session_id)} → 지금 {self._counters}",
        )
        if result.kind == "picked" and result.card is not None:
            self.hud.show_message(
                self.data.name(result.card.item_id),
                tr("선택 반영 · {position} 카드", position=tr(result.card.position)),
                tk.OK,
                1600,
                item_id=result.card.item_id,
            )
        self.control.refresh_run()
        self._update_hud()

    # ---- 추적 이벤트 처리 ----
    def _handle(self, events: List[TrackerEvent]):
        for evaluation in events:
            if evaluation.kind in ("opened", "updated"):
                if self.run.phase != "in_run" and not self.logw.available:
                    self.run.join_mid_run()
                s = evaluation.session
                if self._is_after_pick_echo(s):
                    # 고른 직후 닫히는 애니메이션 동안 같은 카드가 다시 잡힌 것 — 새 선택창이 아니다
                    self._echo_sessions.add(s.session_id)
                    diagnostics.emit("recommendation.deferred", stream="echo", state=s.session_id,
                                     session=s.session_id, reason="closing_animation")
                    continue
                self._echo_sessions.discard(s.session_id)
                if evaluation.kind == "opened":
                    if self._counters is not None:
                        self._session_counters[s.session_id] = dict(self._counters)
                        for old in [k for k in self._session_counters if k < s.session_id - 20]:
                            del self._session_counters[old]
                    self.run.note_offered(tuple(card.item_id for card in s.cards), session_id=s.session_id)
                    if s.pool is not None:
                        self.draws.record(s)
                        self.recommender.draw_weights = self.draws.weights()
                        self.control.set_draw_summary(self.draws.summary())
                    if self._last_levelup_snap is not None:
                        self.snapshots.add(self._last_levelup_snap)  # 업데이트 뒤 회귀 검사용 원본
                        self._last_levelup_snap = None
                self.run.apply_character(
                    s.character_id, s.extra_characters, source="game" if self.bridge.live else "portrait"
                )
                if s.inventory is not None:
                    self.run.apply_inventory(s.inventory, self.data)
                else:
                    self.run.reconcile_cards(s.cards, self.data)
                recommendation_started = time.perf_counter()
                self.recommendation = self.recommender.recommend(evaluation.session, self.run)
                diagnostics.emit("recommendation.choice", stream="choice",
                                 state=(s.session_id, s.signature, self.recommendation.status),
                                 session=s.session_id, status=self.recommendation.status,
                                 choices=len(s.cards), unknown=s.unknown_count, points=s.points_left,
                                 elapsed_ms=round((time.perf_counter() - recommendation_started) * 1000, 2))
                self.hud.show_recommendation(self.recommendation, evaluation.session.points_left)
                self._log_recommendation(evaluation)
            elif evaluation.kind == "closed":
                self.recommendation = None
                result = evaluation.outcome
                if result.session_id in self._echo_sessions:
                    self._echo_sessions.discard(result.session_id)
                    continue
                other = self._counter_outcome(result.session_id) if self.bridge.live else None
                if other is not None:
                    result = PickOutcome(
                        result.session_id, other, None, tr("게임 상태로 확인 (횟수 변화)"), result.options
                    )
                    self.run.apply_outcome(result, self.data)
                    log.info("세션 %s 종료: %s (%s)", result.session_id, other, result.evidence)
                    continue
                if self.bridge.live and result.kind in ("picked", "unknown"):
                    # 게임 연동: 선택창이 닫힌 뒤 보유 목록이 바뀌면 그 차이로 실제 선택을 확정한다.
                    # 게임은 창을 닫은 다음 프레임에 보유 목록을 바꾸므로 3초까지 기다린다.
                    self._pending_pick = (evaluation.session, time.monotonic(), result)
                    self._resolve_pending_pick(self._bridge_inventory)
                    continue
                self.run.apply_outcome(result, self.data)
                log.info("세션 %s 종료: %s (%s)", result.session_id, result.kind, result.evidence)
                if result.kind == "picked" and result.card is not None:
                    self.hud.show_message(
                        self.data.name(result.card.item_id),
                        tr(
                            "선택 반영 · {position} 카드 ({evidence})",
                            position=tr(result.card.position),
                            evidence=result.evidence,
                        ),
                        tk.OK,
                        1600,
                        item_id=result.card.item_id,
                    )
                elif result.kind == "rerolled":
                    self.force_until = time.monotonic() + 3.0
                    self.hud.show_message(tr("새로고침"), tr("새 선택지를 읽는 중"), tk.TEXT_3, 3000)
                elif result.kind == "unknown":
                    self.hud.show_message(
                        tr("선택 결과를 확인하지 못했습니다"),
                        tr("다음 선택창의 표시로 보정하거나 F10에서 고를 수 있습니다"),
                        tk.WARN,
                        2600,
                    )
                else:
                    self.hud.hide()
        if events:
            self.control.refresh_run()

    def _log_recommendation(self, evaluation: TrackerEvent):
        recommendation = self.recommendation
        if recommendation is None or evaluation.kind != "opened":
            return
        log.info(
            "세션 %s 추천(%s, 규칙 %s): %s | %s",
            recommendation.session_id,
            recommendation.status,
            recommendation.rules_version,
            recommendation.headline,
            "; ".join(
                f"{evaluation.card.position}={evaluation.card.item_id}:{evaluation.score:.0f}:"
                + ",".join(result.rule_id for result in evaluation.reasons + evaluation.warnings)
                for evaluation in recommendation.evals
            ),
        )
        if recommendation.plan_text or recommendation.banish_text:
            log.info(
                "세션 %s 덱 방향: %s | %s",
                recommendation.session_id,
                recommendation.plan_text or "-",
                recommendation.banish_text or "삭제 추천 없음",
            )

    # ---- 게임 로그 ----
    def _on_log_synced(self, phase: str, version: str):
        if phase == "in_run" and self.run.phase != "in_run":
            self.run.join_mid_run()
        elif phase == "base":
            self.run.phase = "base"
        self.control.refresh_run()

    def _on_log_event(self, evaluation: LogEvent, at: float):
        if evaluation.kind == "base_state":
            self.base_state = evaluation.value
        elif evaluation.kind == "run_started":
            self._handle(self.tracker.reset())
            self.run.start_run()
            self.control.refresh_run()
            log.info("런 시작")
        elif evaluation.kind == "run_ended":
            self._handle(self.tracker.reset())
            self.run.end_run(tr("기지 복귀"))
            self._finish_run_record()
            self.hud.hide()
            self.control.refresh_run()
        elif evaluation.kind == "game_over":
            self._handle(self.tracker.reset())
        elif evaluation.kind == "reroll":
            if at - self.last_reroll_at > 1.5:  # 한 번의 새로고침에 여러 줄이 찍힌다
                self.last_reroll_at = at
                self._handle(self.tracker.note_reroll(at))
                self.force_until = time.monotonic() + 3.0
        self._update_hud()

    # ---- HUD 표시·위치 ----
    def _game_active(self) -> bool:
        game_window = self.window
        return game_window is not None and game_window.foreground and not game_window.minimized

    def _update_capture_exclusion(self):
        """화면 인식을 쓸 때만 HUD를 캡처에서 뺀다(자기 글자를 게임 글자로 읽지 않도록).
        게임 연동 중에는 스크린샷·녹화에 HUD가 그대로 보인다."""
        want = self.settings.hide_from_capture == "always" or (
            self.settings.hide_from_capture == "auto" and not self.bridge.live
        )
        if want != getattr(self, "_capture_hidden", None):
            self._capture_hidden = want
            self.hud.set_capture_excluded(want)
            self.highlight.set_capture_excluded(want)
            self.dps_meter.set_capture_excluded(want)
            self.base_overlay.set_capture_excluded(want)

    def _shortfalls(self) -> dict:
        """공략 핵심 신규 건물의 부족분. 일반 후보를 사용자가 고른 건설 목표로 간주하지 않는다."""
        result: dict = {}
        if self.meta is None:
            return result
        for suggestion in suggest_base(self.meta, self.data, limit=5, base=self._base_snap):
            if suggestion.affordable or suggestion.priority_group != "guide_core":
                continue
            for field_name, value in self.meta.shortfall(tuple(int(x) for x in suggestion.cost)).items():
                result[field_name] = result.get(field_name, 0) + value
        return result

    def _on_base(self, base: Optional[dict]):
        """기지: 채집 조준 중이면 안내를 띄우고, 채집 전후 자원으로 조준 기록을 남긴다."""
        if base:
            from .engine.layout import fill_missing_colliders

            if not hasattr(self, "_col_cache"):
                self._col_cache = {}
            base = fill_missing_colliders(
                base, self._col_cache
            )  # 재배치 중 집어 든 건물의 모양 (게임이 빼고 보냄)
            from .tracking.game_contract import range_signature, validate_ranges

            contract_key = range_signature(base)
            if contract_key != getattr(self, "_range_contract_key", None):
                self._range_contract_key = contract_key
                self._range_contract = validate_ranges(base)
                if self._range_contract["mismatches"]:
                    log.warning("게임 범위 판정 불일치: %s", self._range_contract)
                    try:
                        with open(
                            os.path.join(self.dump_dir, "range_contract_failure.json"), "w", encoding="utf-8"
                        ) as file_handle:
                            json.dump(
                                {"report": self._range_contract, "base": base},
                                file_handle,
                                ensure_ascii=False,
                            )
                    except OSError:
                        log.exception("범위 판정 진단 저장 실패")
            base = dict(base, range_contract=self._range_contract)
        state = base.get("state", "") if base else ""
        resources = list(self.meta.resources) if self.meta else []
        if state != self._base_state:
            self.aim_range.save()
            if state == "kAimWorkers":
                # 이번 채집의 비행 시간 (조준 화면에 들어올 때의 남은 채집 시간)
                self._harvest_dur = float(base.get("harvest_secs_left") or 16) or 16.0
                if resources:
                    self.harvest_log.start(resources, layout_key(base))
                    log.info("채집 시작: 자원 %s", resources)
            if self._base_state == "kHarvestSummary" and state != "kHarvestSummary":
                # 실제 게임 확인: 자원은 요약 화면 중에 들어오고, 그 뒤 meta 는 바뀌지 않아 다시 오지 않을 수 있다.
                # 이미 바뀌었으면 바로 기록하고, 아니면 다음 meta 를 기다린다.
                if (
                    resources
                    and self.harvest_log._before is not None
                    and resources != self.harvest_log._before
                ):
                    row = self.harvest_log.finish(resources)
                    if row:
                        log.info("채집 기록: 조준 %.0f° → 증가 %s", row["angle"], row["gain"])
                else:
                    self._harvest_pending = True
            self._base_state = state
        self._base_snap = base
        self.control.set_base_context(base)
        self._update_char_combo(base, state)
        self._update_base_advice(base, state)
        if base is not None:
            self._update_spa(base)
        if (
            base
            and (base.get("geo") or {}).get("colliders")
            and self.meta is not None
            and not self._layout_busy
            and state != "kRearrangeBuildings"
        ):
            lk = self._layout_fingerprint(base)
            if lk != getattr(self, "_layout_for", None):
                self.compute_layout()
        if base and state in ("kAimWorkers", "kBounceWorkers"):
            player = base.get("player") or []
            if len(player) >= 4 and state == "kAimWorkers":
                self.harvest_log.note_aim(player[2], player[3])
                if abs(player[2]) + abs(player[3]) > 0.1:
                    self.aim_range.note(HarvestLog.angle(player[2], player[3]), self._mouse_angle(player))
            geometry = base.get("geo") or {}
            if state == "kBounceWorkers" and geometry.get("workers"):
                # 채집 궤적 검증용: 날아가는 작업자 위치를 0.2초마다 남긴다 (harvest_traces.jsonl)
                from .tracking.harvest_contract import observation

                self._trace.append(observation(geometry, round(time.monotonic(), 4), player[2:4]))
            if state == "kAimWorkers":
                from .tracking.harvest_contract import capture
                from .engine.harvest_sim import team_from_base

                team = team_from_base(
                    base, self.meta.chars_raw if self.meta else [], getattr(self, "_team_order", None)
                )
                self._trace_input = capture(base, team, resources, **getattr(self, "_trace_identity", {}))
        if state == "kAimWorkers" and self._base_state_prev_for_trace != "kAimWorkers":
            self._trace = []
            self._trace_start_buildings = (
                base.get("buildings") if base else None
            )  # 채집 전 자원 (채집량 검증용)
        if state == "kHarvestSummary" and self._trace:
            try:
                with open(
                    os.path.join(self.dump_dir, "harvest_traces.jsonl"), "a", encoding="utf-8"
                ) as file_handle:
                    file_handle.write(
                        json.dumps(
                            {
                                "schema": 2,
                                "input": getattr(self, "_trace_input", None),
                                "at": time.time(),
                                "geo": {
                                    field_name: value
                                    for field_name, value in (base.get("geo") or {}).items()
                                    if field_name != "workers"
                                },
                                "buildings": base.get("buildings"),
                                "buildings_before": getattr(self, "_trace_start_buildings", None),
                                "resources_before": self.harvest_log._before,
                                "trace": self._trace,
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass
            self._trace = []
        self._base_state_prev_for_trace = state
        self._render_base(base, state)

    def _remaining_moves(self, base: dict) -> list:
        """재배치 도중: 지금 배치에서 최적 배치까지 남은 옮기기 (배치가 바뀔 때만 다시 계산)."""
        plan = self.layout_plan
        final = getattr(plan, "final", None) if plan else None
        if plan and not getattr(plan, "movement_complete", True):
            from .engine.layout import MoveSequence

            return MoveSequence(complete=False, unresolved=getattr(plan, "unresolved_moves", ()))
        if not final:
            return list(plan.swaps) if plan else []
        key = (layout_key(base), id(plan))
        if getattr(self, "_remain_key", None) != key:
            from .engine.layout_opt import remaining_moves

            self._remain_key, self._remain = (
                key,
                remaining_moves(base, final, getattr(plan, "final_rot", None)),
            )
        return self._remain

    def _mouse_angle(self, player) -> Optional[float]:
        """발사대(게임 화면 좌표)에서 본 마우스 커서 각도. 화면 y 는 아래로 커진다. 왼쪽 아래는 180° 넘게."""
        import ctypes
        import math

        if self.window is None or player[0] < 0:
            return None
        try:
            import ctypes.wintypes as wt

            pt = wt.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return None
        except (AttributeError, OSError):
            return None
        origin_x, origin_y = self.window.origin
        delta_x, delta_y = pt.x - (origin_x + player[0]), (origin_y + player[1]) - pt.y
        if abs(delta_x) + abs(delta_y) < 20:
            return None
        a = math.degrees(math.atan2(delta_y, delta_x))
        return a + 360 if a < -90 else a

    def _homography(self, proj):
        key = tuple(tuple(p) for p in proj or [])
        if key != self._hcache[0]:
            from .engine import harvest_sim as hs

            self._hcache = (key, hs.homography(proj or []))
        return self._hcache[1]

    def _render_base(self, base: Optional[dict], state: str):
        """기지 화면 위 안내 (재배치 번호 또는 채집 조준). 계산은 하지 않고, 계산 프로세스 결과만 그린다."""
        visible_ok = (
            self.window is not None
            and self._game_active()
            and self.settings.hud_auto_show
            and not self.user_hidden
        )
        diagnostics.emit("display.base_gate", stream="base",
                         state=(state, visible_ok, self.user_hidden, self.settings.hud_auto_show),
                         base_state=state, allowed=visible_ok, active=self._game_active(),
                         user_hidden=self.user_hidden, auto_show=self.settings.hud_auto_show)
        g0 = (base or {}).get("geo") or {}
        hm0 = self._homography(g0.get("proj")) if g0.get("proj") else None
        if hm0 is not None and all(k in g0 for k in ("left", "right", "top", "bottom")):
            from .engine import harvest_sim as hs

            self.base_overlay.set_land(
                [
                    hs.to_screen(hm0, x, y)
                    for x, y in (
                        (g0["left"], g0["bottom"]),
                        (g0["right"], g0["bottom"]),
                        (g0["right"], g0["top"]),
                        (g0["left"], g0["top"]),
                    )
                ]
            )
        if (
            base
            and state == "kRearrangeBuildings"
            and self.layout_plan
            and (self.layout_plan.swaps or getattr(self.layout_plan, "final", None))
            and visible_ok
        ):
            # 재배치 중: 다음에 옮길·맞바꿀 건물을 게임 화면에 번호로 표시
            from .engine import harvest_sim as hs
            from .engine.layout import Move

            position = {
                building["id"]: (building.get("sx"), building.get("sy"))
                for building in base.get("buildings") or []
                if building.get("sx") is not None
            }
            hmat = self._homography((base.get("geo") or {}).get("proj"))
            marks = []
            remain = self._remaining_moves(base)
            for n, swap in enumerate(remain, 1):
                if isinstance(swap, Move):
                    if swap.a in position and hmat is not None:
                        tx, ty = hs.to_screen(hmat, swap.to[0], swap.to[1])
                        marks.append((position[swap.a][0], position[swap.a][1], tx, ty, str(n)))
                elif swap.a in position and swap.b in position:
                    marks.append(
                        (
                            position[swap.a][0],
                            position[swap.a][1],
                            position[swap.b][0],
                            position[swap.b][1],
                            str(n),
                        )
                    )
            game = geo.phys_to_logical_rect(self.window.rect)
            scale = game.width() / max(1, self.window.size[0])
            self.base_overlay.set_paths([], [])
            self.base_overlay.set_build_marks([])
            self.base_overlay.set_swap_marks(marks[:3])
            box = []
            if remain and hmat is not None and isinstance(remain[0], Move):
                from .engine.layout import (
                    buildings_from_base,
                    grid_from_geo,
                    shape_masks,
                    shape_outline_world,
                )

                g = grid_from_geo(base.get("geo") or {})
                blds0 = buildings_from_base(base)
                b0 = blds0.get(remain[0].a)
                if g is not None and b0 is not None:
                    # ㄱ·ㅜ·ㅠ 자 건물은 사각형이 아니라 실제 모양 그대로, 회전해서 놓는 건물은 돌린 모양으로
                    mask = shape_masks(base.get("geo") or {}, {b0.id: b0}, g).get(b0.id)
                    box = [
                        hs.to_screen(hmat, x, y)
                        for x, y in shape_outline_world(
                            b0, mask, g.size, remain[0].to, getattr(remain[0], "rot", -1)
                        )
                    ]
            self.base_overlay.set_target_box(box)
            types = {building.get("id"): building.get("type", "") for building in base.get("buildings") or []}
            if not getattr(remain, "complete", True):
                lines = [
                    (
                        tr("안전한 이동 순서를 만들지 못했습니다. 배치도를 다시 계산해 주세요."),
                        (255, 159, 10, 255),
                    )
                ]
            elif remain:
                first = remain[0]
                lines = [
                    (
                        tr("가이드 배치까지 옮기기 {v0}개 남음 — 번호 순서대로", v0=len(remain)),
                        (245, 245, 247, 255),
                    ),
                    (
                        tr(
                            "1번: {v0} → 번호 1 자리 ({reason})",
                            v0=self.data.building_name(types.get(first.a, "")),
                            reason=first.reason,
                        ),
                        (255, 159, 10, 255),
                    ),
                ]
            else:
                lines = [(tr("가이드 배치 완료 — 재배치를 끝내도 됩니다"), (48, 209, 88, 255))]
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
        blocked = AppController._launch_block_message(base)
        if blocked:
            AppController._clear_harvest_paths(self)
            self.base_overlay.set_build_marks([])
            game = geo.phys_to_logical_rect(self.window.rect)
            scale = game.width() / max(1, self.window.size[0])
            # adv/조준점을 넘기지 않아 기존 대체 추천선도 남지 않게 한다.
            messages = [(blocked, (255, 159, 10, 255))]
            from .engine.launch_access import modeled_current_launch

            if "launch_allowed" not in base and modeled_current_launch(base) is not None:
                messages.append(
                    (tr("게임 직접 판정 미수신 — 게임 규칙으로 계산한 예상입니다."), (190, 190, 200, 255))
                )
            self.base_overlay.show_advice(game, scale, None, None, messages)
            if not self.base_overlay.isVisible():
                self.base_overlay.show()
            return
        unf = unfinished_buildings(base, self.meta)
        adv = advise_harvest(base, self.meta, self._shortfalls(), self.harvest_log)
        texts = []
        sim = self._harvest_sim(base, adv.need, unf)
        if unf:
            names = " · ".join(
                tr(
                    "{name}({label} {pct:.0%}, 남은 공사 점수 {points})",
                    name=self.data.building_name(u.type),
                    label=u.label,
                    pct=u.pct,
                    points=u.remaining_points,
                )
                if u.remaining_points is not None
                else tr(
                    "{name}({label} {pct:.0%})",
                    name=self.data.building_name(u.type),
                    label=u.label,
                    pct=u.pct,
                )
                for u in unf[:3]
            )
            texts.append((tr("미완성 먼저: {names}", names=names), (255, 159, 10, 255)))
            tip = gold_bounce_tip(base, unf)
            if tip:
                texts.append((tip, (190, 190, 200, 255)))
            if self._stuck_text:
                texts.append((self._stuck_text, (255, 159, 10, 255)))
        texts.append((tr("필요한 자원: {need_text}", need_text=adv.need_text), (245, 245, 247, 255)))
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
        if not force and time.monotonic() < getattr(self, "_layout_retry_at", 0):
            return
        team = hs.team_from_base(base, self.meta.chars_raw, getattr(self, "_team_order", None))
        snapshot = json.loads(json.dumps(AppController._layout_input(self, base)))
        # 원정 중 live_state에는 기지가 없다. 마지막 계산 입력은 진단용으로 따로 보존한다.
        try:
            path = os.path.join(self.dump_dir, "last_base.json")
            with open(path + ".tmp", "w", encoding="utf-8") as file_handle:
                json.dump({"captured_at": time.time(), "base": snapshot}, file_handle, ensure_ascii=False)
            os.replace(path + ".tmp", path)
        except OSError:
            log.debug("마지막 기지 입력 저장 실패", exc_info=True)
        options = self.meta.build_options if self.meta.build_options is not None else self.meta.blueprints
        bps = [building.construction_data() for building in options]
        targets = {u.id: u.hits_left for u in unfinished_buildings(base, self.meta)}
        need, _ = need_resource(self.meta, self._shortfalls())
        self._layout_busy = True
        char_levels = {c.get("type"): c.get("lvl") for c in self.meta.chars_raw if c.get("type")}
        # 목표 고정: 이전 목표 배치를 넘겨, 새 계산이 2% 넘게 좋지 않으면 목표를 바꾸지 않는다 — 조금 옮기고 다시
        # 계산할 때마다 목표가 바뀌어 헛걸음하던 것 (기록: 71 → 79 → 92 → 118번)
        previous = getattr(self.layout_plan, "final", None) if self.layout_plan else None
        # 일반 기지에서 처음 계산할 때도 현재 게임의 전체 채집 시간을 사용한다.
        # 마지막 조준 시 남은 시간이나 앱 초기 기본값으로 새 배치를 평가하지 않는다.
        duration = hs.initial_harvest_duration(snapshot, team, self._harvest_dur)
        limits = self.aim_range.limits
        key = self._layout_fingerprint(snapshot)
        self._layout_for = key
        self._layout_request = (key, snapshot)
        self._sim_req["layout"] = key
        diagnostics.emit("compute.input", channel="layout", request=diagnostics.request_token(key),
                         sequence=getattr(self, "_trace_identity", {}).get("sequence"),
                         buildings=len(snapshot.get("buildings") or []), workers=len(team),
                         duration=duration, targets=len(targets), build_options=len(bps),
                         need=need, forced=force, has_previous=previous is not None)
        self.sim.submit(
            "layout",
            key,
            sim_jobs.job_layout,
            snapshot,
            team,
            duration,
            bps,
            targets,
            need,
            8.0,
            dict(previous) if previous else None,
            char_levels,
            tuple(self.meta.resources),
            limits,
        )

    def _layout_input(self, base):
        if "player_y" not in (base.get("geo") or {}):
            return base
        from .tracking.harvest_origin import HarvestOrigin

        if not hasattr(self, "_harvest_origin"):
            self._harvest_origin = HarvestOrigin(self.dump_dir)
        return self._harvest_origin.resolve(base)

    def _layout_fingerprint(self, base):
        base = AppController._layout_input(self, base)
        from .engine.sim_signature import layout_signature

        options = self.meta.build_options if self.meta.build_options is not None else self.meta.blueprints
        need, _ = need_resource(self.meta, self._shortfalls())
        from .engine import harvest_sim as hs

        team = hs.team_from_base(base, self.meta.chars_raw, getattr(self, "_team_order", None))
        duration = hs.initial_harvest_duration(base, team, self._harvest_dur)
        return layout_signature(
            base,
            (self.meta.chars_raw, getattr(self, "_team_order", None)),
            [building.construction_data() for building in options],
            tuple(self.meta.resources),
            duration,
            need,
            self.aim_range.limits,
        )

    def _update_spa(self, base: dict):
        """스파 재채집 손익: 게임이 알려 준 비용과 내 채집 기록 평균을 비교 (비용·기록이 바뀔 때만)."""
        from .engine.spa import spa_advice

        spa = base.get("spa") if isinstance(base.get("spa"), dict) else None
        key = (json.dumps(spa, sort_keys=True) if spa else "", len(self.harvest_log.rows))
        if key == getattr(self, "_spa_key", None):
            return
        self._spa_key = key
        adv = spa_advice(
            spa, self.harvest_log.rows, layout_key(base), self._shortfalls() if self.meta else {}
        )
        self.control.set_spa(adv)
        if (
            adv is not None
            and adv.verdict == "profit"
            and base.get("harvested_today")
            and getattr(self, "_spa_notified", None) != adv.cost
        ):
            self._spa_notified = adv.cost
            self.tray.showMessage(tr("BALL x PIT 도우미"), adv.text)
        if adv is not None:
            log.info("스파: %s", adv.text)

    def _on_sim_done(self, channel: str, key, result):
        if key != self._sim_req.get(channel):
            diagnostics.emit("compute.discarded", channel=channel, request=diagnostics.request_token(key),
                             reason="superseded", current=diagnostics.request_token(self._sim_req.get(channel)))
            return
        if channel != "layout" and not result:
            diagnostics.emit("compute.retry", channel=channel, request=diagnostics.request_token(key),
                             reason="empty_result", delay_ms=500)
            self._sim_req.pop(channel, None)
            self._sim_res.pop(channel, None)
            self._sim_retry_at[channel] = time.monotonic() + 0.5
            return  # 동기 제출 실패가 즉시 재제출로 재귀하지 않도록 다음 프레임에서 재시도
        if channel == "layout":
            self._layout_busy = False
            request = getattr(self, "_layout_request", None)
            if (
                not request
                or not self._base_snap
                or self.meta is None
                or self._base_state == "kRearrangeBuildings"
                or key != self._layout_fingerprint(self._base_snap)
            ):
                diagnostics.emit("compute.discarded", channel=channel, request=diagnostics.request_token(key),
                                 reason="layout_context_changed", base_state=self._base_state)
                self._layout_for = None
                return
            self._sim_res[channel] = (key, result)
            if not result:
                diagnostics.emit("compute.retry", channel=channel, request=diagnostics.request_token(key),
                                 reason="empty_result", delay_ms=2000)
                self._layout_for = None
                self._layout_retry_at = time.monotonic() + 2.0
            self._on_layout_done(request[1], result)
            if self._base_snap and self._base_state == "kAimWorkers":
                self._render_base(self._base_snap, self._base_state)
        else:
            self._sim_res[channel] = (key, result)
            if self._base_snap and self._base_state == "kAimWorkers":
                self._render_base(self._base_snap, self._base_state)
        diagnostics.emit("compute.accepted", channel=channel, request=diagnostics.request_token(key),
                         has_result=bool(result), error=result.get("error") if isinstance(result, dict) else None)

    def _on_layout_done(self, snapshot: dict, result):
        self._layout_busy = False
        plan, sweeps = result if result else (None, {})
        if plan and self.meta:
            self._validate_purchase_budget(plan)
        self.layout_plan = plan
        self.layout_win.set_result(snapshot, plan, sweeps)
        # 일꾼이 없으면 생산이 0인 건물 (금광·농장·야적장·채석장·채집가의 오두막 — 위키: 광마다 일꾼 1명)
        from .engine.construction_policy import is_recommended_building

        idle = [
            building.get("type", "")
            for building in (snapshot or {}).get("buildings") or []
            if building.get("type")
            in ("kGoldMine", "kIdleFarm", "kIdleLumberyard", "kIdleStoneMine", "kIdleLauncher")
            and is_recommended_building(building.get("type", ""))
            and isinstance(building.get("worker"), int)
            and building["worker"] < 0
        ]
        self._unmanned = idle
        self.control.set_layout_plan(plan, idle)
        if plan:
            log.info(
                "배치 추천: 범위 효과 %.1f → %.1f, 바꾸기 %d번, 채집 예상 %s → %s",
                plan.score_before,
                plan.score_after,
                len(plan.swaps),
                plan.harvest_before,
                plan.harvest_after,
            )

    def _validate_purchase_budget(self, plan):
        """계산 중 자원이 줄었으면 지금 감당할 수 없는 구매 안내를 제거한다."""
        from .engine.layout import build_purchase_cost, TILE_TYPES

        resources = tuple(self.meta.resources)
        tile_budget = list(resources)
        kept = []
        for row in plan.builds:
            cost = build_purchase_cost(row, plan.build_costs)
            budget = tile_budget if row[0] in TILE_TYPES else resources
            if (
                cost is None
                or len(budget) < len(cost)
                or any(amount > budget[index] for index, amount in enumerate(cost))
            ):
                continue
            kept.append(row)
            if row[0] in TILE_TYPES:
                tile_budget = [tile_budget[index] - cost[index] for index in range(len(cost))]
        plan.builds = kept
        allowed = {(row[0], tuple(row[1])) for row in kept}
        plan.new_spots = [spot for spot in plan.new_spots if (spot.type, tuple(spot.center)) in allowed]

    def _access_move_names(self, stuck) -> str:
        """배치도 계산이 찾은 길 열기 옮기기 (예: '재배치에서 수레바퀴 공방을 빈 자리로 옮기기')."""
        from .engine.layout import Move

        plan = self.layout_plan
        if not plan or not self._base_snap:
            return ""
        types = {
            building.get("id"): building.get("type", "")
            for building in self._base_snap.get("buildings") or []
        }
        ids = {u.id for u in stuck}
        names = [
            self.data.building_name(types.get(swap.a, ""))
            for swap in plan.swaps
            if isinstance(swap, Move) and swap.target in ids and swap.a in types
        ]
        if names:
            return tr("재배치 모드에서 빈 자리로 옮기기: {v0} (배치도 번호 순서)", v0=", ".join(names))
        reach = getattr(plan, "reach_after", {}) or {}
        if ids and all(reach.get(index, 0) > 0 for index in ids):
            return tr("배치도 이동안에서 도달을 예상함 (게임에서 확인 필요)")
        return ""

    @staticmethod
    def _yield_text(r: dict) -> str:
        from .tracking.meta_state import RESOURCES

        parts = (
            [tr("공사 점수 +{points} (계산)", points=r["build_points"])]
            if r.get("build_points")
            else [tr("공사장 접촉 {v0}회 (계산)", v0=r["build_hits"])]
            if r.get("build_hits")
            else []
        )
        parts += [f"{RESOURCES[index]} +{value}" for index, value in enumerate(r.get("total") or []) if value]
        return " · ".join(parts) or tr("채집 없음")

    @staticmethod
    def _launch_block_message(base: dict):
        allowed = base.get("launch_allowed")
        if "launch_allowed" not in base:
            from .engine.launch_access import modeled_current_launch

            allowed = modeled_current_launch(base)
        if allowed is False:
            return tr("발사 불가 — 입구를 비우거나 조준 위치를 바꿔 주세요.")
        if allowed is not True:
            return tr("발사 가능 여부 미확인 — 궤적 표시를 보류합니다. 연동 상태를 확인해 주세요.")
        direction, player = base.get("launch_aim"), base.get("player") or []
        if direction is not None:
            try:
                import math

                aligned = (
                    len(direction) == 2
                    and len(player) >= 4
                    and all(
                        math.isfinite(float(direction[index]))
                        and abs(float(direction[index]) - float(player[index + 2])) <= 0.002
                        for index in range(2)
                    )
                )
            except (TypeError, ValueError, IndexError):
                aligned = False
            if not aligned:
                return tr("발사 가능 여부 미확인 — 궤적 표시를 보류합니다. 연동 상태를 확인해 주세요.")
        return None

    def _clear_harvest_paths(self):
        self.base_overlay.set_paths([], [])
        self._stuck_text = ""
        for channel in ("now", "sweep"):
            self._sim_req.pop(channel, None)
            self._sim_res.pop(channel, None)

    def _harvest_sim(self, base: dict, need: int, unf) -> List[tuple]:
        """채집 궤적: 지금 조준·추천 각도의 예상 경로와 결과. 계산은 계산 프로세스에 맡기고(가장 최근 요청만),
        여기서는 요청과 이미 나온 결과 표시만 한다. 표시할 글 줄을 돌려준다."""
        import math
        from .engine import harvest_sim as hs
        from .engine import sim_jobs
        from .engine.aim_preview import visible_path

        blocked = AppController._launch_block_message(base)
        if blocked:
            diagnostics.emit("harvest.gate", stream="aim", state="launch_blocked",
                             reason="launch_blocked_or_unknown", launch_allowed=base.get("launch_allowed"))
            AppController._clear_harvest_paths(self)
            return [(blocked, (255, 159, 10, 255))]
        geometry = base.get("geo") or {}
        homography = self._homography(geometry.get("proj"))
        if (
            not isinstance(geometry.get("colliders"), list)
            or homography is None
            or self.meta is None
            or not all(k in geometry for k in ("left", "right", "bottom", "top", "launcher"))
        ):
            diagnostics.emit("harvest.gate", stream="aim", state="missing_geometry",
                             reason="missing_geometry_or_meta", has_projection=homography is not None,
                             has_colliders=isinstance(geometry.get("colliders"), list), has_meta=self.meta is not None)
            self.base_overlay.set_paths([], [])
            return []
        team = hs.team_from_base(base, self.meta.chars_raw, getattr(self, "_team_order", None))
        if not team:
            diagnostics.emit("harvest.gate", stream="aim", state="empty_team", reason="empty_team")
            self.base_overlay.set_paths([], [])
            return []
        length = getattr(getattr(self, "settings", None), "aim_path_length", "normal")
        extended = getattr(self, "_aim_extended", False)

        def screen_path(result, recommended=False):
            points = visible_path(result.get("path"), length, recommended=recommended, extended=extended)
            return [hs.to_screen(homography, x, y) for x, y in points]

        buildings = {building["id"]: building for building in base.get("buildings") or [] if "id" in building}
        targets = {u.id: u.hits_left for u in unf}
        duration = hs.initial_harvest_duration(base, team, self._harvest_dur)
        diagnostics.emit("harvest.gate", stream="aim", state="ready", reason="ready", workers=len(team),
                         buildings=len(buildings), colliders=len(geometry["colliders"]), duration=duration,
                         dynamic=hs.needs_dynamic_simulation(buildings))
        # 배치·남은 자원·필요 자원·미완성 건물이 바뀔 때만 각도 탐색을 다시 한다
        lo, hi = self.aim_range.limits
        from .engine.sim_signature import aim_signature

        key = aim_signature(base, team, duration, need, targets, (lo, hi))
        retry = getattr(self, "_sim_retry_at", {})
        now = time.monotonic()
        if self._sim_req.get("sweep") != key and now >= retry.get("sweep", 0):
            self._sim_req["sweep"] = key
            diagnostics.emit("compute.input", channel="sweep", request=diagnostics.request_token(key),
                             sequence=getattr(self, "_trace_identity", {}).get("sequence"),
                             buildings=len(buildings), workers=len(team), duration=duration,
                             targets=len(targets), need=need, minimum_angle=lo, maximum_angle=hi)
            self.sim.submit(
                "sweep", key, sim_jobs.job_sweep, geometry, buildings, team, duration, need, targets, lo, hi
            )
        lines = []
        quality_notes = set(hs.model_limitations(team, buildings))
        self._stuck_text = ""
        if "launch_allowed" not in base:
            lines.append(
                (tr("게임 직접 판정 미수신 — 게임 규칙으로 계산한 예상입니다."), (255, 190, 110, 255))
            )
        now_path: list = []
        player = base.get("player") or []
        if len(player) >= 4:
            ang = math.degrees(math.atan2(player[3], player[2]))
            if self._sim_req.get("now") != (ang, key) and now >= retry.get("now", 0):
                self._sim_req["now"] = (ang, key)
                diagnostics.emit("compute.input", channel="now", request=diagnostics.request_token((ang, key)),
                                 sequence=getattr(self, "_trace_identity", {}).get("sequence"),
                                 angle=ang, duration=duration, workers=len(team), buildings=len(buildings))
                self.sim.submit(
                    "now", (ang, key), sim_jobs.job_now, geometry, buildings, team, ang, duration, targets
                )
            cached_result = self._sim_res.get("now")
            if cached_result and cached_result[1] and cached_result[0] == (ang, key):
                result = cached_result[1]
                quality_notes.update(result.get("model_limitations", ()))
                if not result.get("error"):
                    now_path = screen_path(result)
                    lines.append(
                        (
                            tr("지금 조준 {v0:.0f}°: {v1}", v0=result["angle"], v1=self._yield_text(result)),
                            (255, 255, 255, 230),
                        )
                    )
        best_path: list = []
        cached_result = self._sim_res.get("sweep")
        if cached_result and cached_result[1] and cached_result[0] == key:
            quality_notes.update(cached_result[1].get("model_limitations", ()))
            if cached_result[1].get("error"):
                self.base_overlay.set_paths([], [])
                return [(tr("물리 정보가 불완전해 궤적 계산을 보류합니다."), (255, 159, 10, 255))]
        if cached_result and cached_result[1] and cached_result[0] == key and cached_result[1].get("top"):
            top, reach = cached_result[1]["top"], cached_result[1].get("reach") or {}
            # 어떤 각도로도 닿지 않는 미완성 건물: 배치도의 '길 열기'로 안내
            stuck = [u for u in unf if u.id in reach and not reach[u.id]]
            if stuck:
                opener = self._access_move_names(stuck)
                self._stuck_text = (
                    " · ".join(self.data.building_name(u.type) for u in stuck)
                    + tr(": 검사한 각도에서 도달을 확인하지 못함 → ")
                    + (opener or tr("배치도에서 옆 건물을 옮겨 길을 여세요"))
                )
            best = top[0]
            # 1위와 거의 같은 각도가 여럿이면(작업자가 오래 튕겨 어디로 쏴도 비슷하게 다 캐는 경우) 순위가 의미 없다 —
            # 그중 지금 조준에 가장 가까운 것을 보여 준다 (실제 화면: 162° 1위, 36° 2위가 1 차이)
            similar = [result for result in top if best.get("score", 0) - result.get("score", 0) <= 1.0]
            current = math.degrees(math.atan2(player[3], player[2])) if len(player) >= 4 else 90.0
            if len(similar) > 1:
                show = min(similar, key=lambda r: abs(r["angle"] - current))
                best_path = screen_path(show, True)
                lines.append(
                    (
                        tr(
                            "각도 차이 거의 없음 ({v0}곳 비슷) — 지금 조준에 가까운 {v1:.0f}° (파란 선): {v2}",
                            v0=len(similar),
                            v1=show["angle"],
                            v2=self._yield_text(show),
                        ),
                        (120, 180, 255, 255),
                    )
                )
            else:
                best_path = screen_path(best, True)
                lines.append(
                    (
                        tr("1위 {v0:.0f}° (파란 선): {v1}", v0=best["angle"], v1=self._yield_text(best)),
                        (120, 180, 255, 255),
                    )
                )
                alts = " / ".join(
                    tr("{i}위 {v0:.0f}° {v1}", i=index, v0=result["angle"], v1=self._yield_text(result))
                    for index, result in enumerate(top[1:3], 2)
                )
                if alts:
                    lines.append((alts, (170, 170, 180, 255)))
        elif cached_result and cached_result[1] and cached_result[0] == key:
            lines.append(
                (
                    tr("발사 가능한 추천 각도를 찾지 못했습니다. 입구 배치를 확인해 주세요."),
                    (255, 159, 10, 255),
                )
            )
        else:
            lines.append((tr("추천 각도 계산 중…"), (190, 190, 200, 255)))
        l_ok, h_ok = self.aim_range.learned
        if not (l_ok and h_ok):
            lines.append(
                (
                    tr(
                        "추천 각도 범위 {lo:.0f}°~{hi:.0f}° (추정) — 마우스를 좌우 끝까지 밀면 게임 한계를 배웁니다",
                        lo=lo,
                        hi=hi,
                    ),
                    (140, 140, 150, 255),
                )
            )
        from .engine.harvest_sim import model_limitations

        if quality_notes:
            lines.append(
                (
                    tr("게임 강화 값을 반영한 예상입니다. 실제 채집 경로·수확량은 다를 수 있습니다."),
                    (255, 190, 110, 255),
                )
            )
        missing = sorted(
            {
                n.split(":", 1)[1]
                for n in quality_notes
                if n.startswith(("missing_building_effect:", "missing_housing_activation:"))
            }
        )
        if missing:
            names = ", ".join(self.data.building_name(name) for name in missing)
            lines.append((tr("건물 효과 정보 부족: {buildings}", buildings=names), (255, 190, 110, 255)))
        lines.append(
            (tr("흰색: 현재 조준 · 파랑: 추천 · 점: 예상 반사 · Shift: 길게 보기"), (170, 180, 195, 255))
        )
        lines.append(
            (tr("첫 작업자의 예상 경로입니다. 먼 점선은 실제와 달라질 수 있습니다."), (140, 140, 150, 255))
        )
        self.base_overlay.set_paths(now_path, best_path)
        diagnostics.emit("display.harvest", stream="paths",
                         state=(bool(now_path), bool(best_path), tuple(sorted(quality_notes))),
                         current_points=len(now_path), recommended_points=len(best_path),
                         limitations=",".join(sorted(quality_notes)),
                         request=diagnostics.request_token(key))
        return lines

    def _update_dps(self):
        """전투 중(게임 상태 kPlaying)에만 초당 피해 창을 게임 창 위쪽 구석에 띄운다. 0.5초마다 다시 그린다."""
        m = self.dps_meter
        want = (
            self.settings.dps_meter
            and not self.user_hidden
            and self.bridge.live
            and self._dps_state == "kPlaying"
            and self.run.phase == "in_run"
            and self._game_active()
        )
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
            x = (
                game.right() - m.width() - margin
                if self.settings.dps_corner != "left"
                else game.left() + margin
            )
            y = game.top() + max(96, game.height() // 10)  # 오른쪽 위 배속 버튼 아래
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
            showing = (
                (self.tracker.session is not None and self.recommendation is not None)
                or self.fusion_rec is not None
                or self.expedition is not None
                or self.char_combo is not None
                or self.base_advice is not None
            )
            want = self._game_active() and (showing or hud.message_pending)
        if not want:
            if hud.isVisible():
                hud.hide()
            self.highlight.set_marks([])
            self._record_hud_display(False)
            return
        self._position_hud()
        if not hud.isVisible():
            hud.show()
        self._update_highlight()
        self._record_hud_display(True)

    def _record_hud_display(self, wanted: bool):
        """표시 전환만 기록한다. 추천 계산 성공과 실제 창 표시를 구분할 근거를 남긴다."""
        session = self.tracker.session
        game_state = getattr(getattr(self, "bridge_state", None), "game_state", "")
        signature = (
            wanted, self.hud.isVisible(), self._game_active(), self.user_hidden,
            self.settings.hud_auto_show, game_state,
            session.session_id if session else None,
            self.recommendation.session_id if self.recommendation else None,
            self.fusion_rec is not None, self.hud.message_pending,
            bool(self._echo_sessions),
        )
        if signature == getattr(self, "_hud_display_signature", None):
            return
        self._hud_display_signature = signature
        raised = gw.raise_overlay(int(self.hud.winId())) if wanted and self.hud.isVisible() else None
        log.info(
            "HUD 표시: 요청=%s 표시=%s 게임활성=%s 사용자숨김=%s 자동=%s 화면=%s "
            "선택창=%s 추천=%s 융합=%s 알림=%s 잔상보류=%s 위치=%s 전면배치=%s",
            *signature, self.hud.geometry().getRect(), raised,
        )
        diagnostics.emit("display.hud", wanted=wanted, visible=self.hud.isVisible(),
                         active=self._game_active(), user_hidden=self.user_hidden,
                         session=session.session_id if session else None,
                         echo=bool(self._echo_sessions), raised=raised,
                         x=self.hud.x(), y=self.hud.y(), width=self.hud.width(), height=self.hud.height())

    def _update_highlight(self):
        """모든 선택지 카드에 판정 색 테두리와 이름표 (HUD 목록과 같은 색)."""
        recommendation, s = self.recommendation, self.tracker.session
        if (
            not self.settings.card_outline
            or recommendation is None
            or s is None
            or recommendation.status in ("auto", "none")
        ):
            self.highlight.set_marks([])
            return
        marks = []
        for evaluation in recommendation.evals:
            card = evaluation.card
            if card.rect[2] > 0 and card.rect[3] > 0:  # 능력치 페이지에서는 카드 위치가 아직 없다
                marks.append(
                    (
                        geo.phys_to_logical_rect(s.frame.to_screen(card.rect)),
                        card_verdict(recommendation, evaluation),
                        card_badge(recommendation, evaluation),
                    )
                )
        self.highlight.set_marks(marks)
        if not self.highlight.isVisible():
            self.highlight.show()

    def _position_hud(self):
        hud = self.hud
        hud.adjustSize()
        game_window = self.window
        if game_window is None:
            screen = QApplication.primaryScreen().availableGeometry()
            rectangle = QRect(screen)
        else:
            rectangle = geo.phys_to_logical_rect(game_window.rect)
        cards: List[QRect] = []
        panel = None
        choice_session = self.tracker.session
        if choice_session is not None:
            cards = [
                geo.phys_to_logical_rect(choice_session.frame.to_screen(card.rect))
                for card in choice_session.cards
            ]
            if choice_session.panel_rect:
                panel = geo.phys_to_logical_rect(choice_session.frame.to_screen(choice_session.panel_rect))
        elif self.fusion_rec is not None and getattr(self, "_fusion_panel", None):
            fp = geo.phys_to_logical_rect(self._fusion_panel)
            if rectangle.contains(fp):  # 밀려 들어오는 도중 값(화면 밖)은 쓰지 않는다
                panel = fp
        spot = (
            geo.levelup_hud_spot(rectangle, [c for c in cards if c.width() > 0], hud.width(), hud.height())
            if choice_session is not None
            else None
        )
        if spot is None:
            spot = geo.place_hud(rectangle, cards, hud.width(), hud.height(), panel=panel)
        # 강화 선택창은 캐릭터 초상화 자리(장식)가 먼저. 게임이 알려 준 UI 영역(글자·버튼·캐릭터 목록)이 있으면
        # 그것을 가장 덜 가리는 자리로 — 캐릭터 선택창의 이름표, 융합 선택지, 설명 패널 등
        avoid = [geo.phys_to_logical_rect(rectangle) for rectangle in getattr(self, "_ui_avoid", [])] + [
            c for c in cards if c.width() > 0
        ]
        if getattr(self, "_ui_avoid", None):
            spot = geo.place_avoiding(rectangle, avoid, hud.width(), hud.height(), preferred=[spot])
        self._hud_auto_spot = QPoint(spot)
        point = QPoint(spot.x() + self.settings.hud_offset_x, spot.y() + self.settings.hud_offset_y)
        hud.move(geo.clamp_to_screen(point, hud.width(), hud.height()))

    def _on_hud_moved(self, position: QPoint):
        # 자동 배치 위치 대비 얼마나 옮겼는지 저장한다
        auto = getattr(self, "_hud_auto_spot", None)
        if auto is None:
            self._position_hud()
            auto = self._hud_auto_spot
        self.settings.hud_offset_x = position.x() - auto.x()
        self.settings.hud_offset_y = position.y() - auto.y()
        self.settings.save()

    def _set_hud_edit(self, on: bool):
        self.hud.set_edit_mode(on)
        if on and self.recommendation is None:
            self.hud.show_message(
                tr("HUD 위치 조정"), tr("끌어서 옮긴 뒤 설정 창에서 버튼을 다시 누르세요"), tk.ACCENT
            )
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
            self.hud.show_message(tr("HUD 켜짐"), tr("선택창이 열리면 추천이 표시됩니다"), tk.TEXT_3, 1500)
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
        self.control.activateWindow()  # 사용자가 직접 연 창이므로 포커스를 준다

    # ---- 설정·수동 보정 ----
    def _on_aim_extended(self, on: bool):
        self._aim_extended = on
        if self._base_snap and self._base_state == "kAimWorkers":
            self._render_base(self._base_snap, self._base_state)

    def _on_settings_changed(self):
        self.recommender.discovery_mode = self.settings.encyclopedia_mode
        self.fusion.discovery_mode = self.settings.encyclopedia_mode
        if self.tracker.session is not None:
            self.recommendation = self.recommender.recommend(self.tracker.session, self.run)
        elif self.fusion_rec is not None and self.bridge_state is not None:
            self._update_fusion(self.bridge_state.observation)
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
        if self._base_snap and self._base_state == "kAimWorkers":
            self._render_base(self._base_snap, self._base_state)

    def _on_run_edited(self):
        choice_session = self.tracker.session
        if choice_session is not None:
            self.recommendation = self.recommender.recommend(choice_session, self.run)
            self.hud.show_recommendation(self.recommendation, choice_session.points_left)
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
        observation = res.observation
        with open(os.path.join(folder, f"frame_{stamp}.json"), "w", encoding="utf-8") as file_handle:
            json.dump(
                {
                    "kind": observation.kind.value,
                    "cards": [asdict(card) for card in observation.cards],
                    "inventory": [asdict(s) for s in observation.inventory or ()],
                    "character": observation.character_id,
                    "gold": observation.gold,
                    "reroll_cost": observation.reroll_cost,
                    "free_rerolls": observation.free_rerolls,
                    "banish_left": observation.banish_left,
                    "points_left": observation.points_left,
                },
                file_handle,
                ensure_ascii=False,
                indent=1,
                default=str,
            )
        self.control.saved_label.setText(tr("저장함: {path}", path=path))
        log.info("진단용 게임 화면 저장: %s", path)

    def _refresh_diagnostics(self):
        game_window = self.window
        resources = self.last_result
        if self.bridge.live:
            conn = tr("게임 연동 중")
        else:
            conn = tr("게임 연결됨 · 화면 인식") if game_window else tr("게임 창 없음")
        self.control.set_connection(conn)
        if not self.control.isVisible():
            return
        observation = resources.observation if resources else None
        phase_names = {"in_run": tr("런 진행 중"), "base": tr("기지"), "unknown": tr("미확인")}
        rows = {
            tr("연결 방식"): (
                tr("게임 연동 (BepInEx 플러그인) · 메시지 {messages}건", messages=self.bridge.messages)
                + (
                    tr(" · 게임 {game_version}", game_version=self.bridge.game_version)
                    if self.bridge.game_version
                    else ""
                )
            )
            if self.bridge.live
            else tr("화면 인식 (게임 연동 없음 — 플러그인 미설치이거나 게임이 꺼져 있음)"),
            tr("게임 창"): (
                tr(
                    "{v0}×{v1} · 위치 {origin} · DPI {dpi}",
                    v0=game_window.size[0],
                    v1=game_window.size[1],
                    origin=game_window.origin,
                    dpi=game_window.dpi,
                )
                + (tr(" · 앞에 있음") if game_window.foreground else tr(" · 뒤에 있음"))
                + (tr(" · 최소화") if game_window.minimized else "")
            )
            if game_window
            else tr("찾지 못함 (Balls.exe)"),
            tr("캡처"): (
                observation.frame.backend
                if observation and observation.frame
                else (observation.error if observation else "-")
            )
            or "-",
            tr("화면 판별"): (
                SCREEN_LABEL.get(observation.kind, observation.kind.value)
                + (f" ({tr(resources.skipped)})" if resources.skipped else "")
                + (f" — {observation.error}" if observation.error else "")
            )
            if observation
            else "-",
            tr("게임 로그"): (f"{phase_names[self.logw.phase]} · {self.base_state or '-'}")
            if self.logw.available
            else tr("없음"),
            tr("게임 버전"): self.logw.game_version or tr("미확인"),
            tr("데이터 빌드"): f"Steam {self.data.game_build_id}",
            "OCR": self.ocr_status.message,
            "HUD": tr(
                "캡처 제외: {status} · 클릭 통과: {through}",
                status={True: tr("확인"), False: tr("실패"), None: tr("아직 표시 안 됨")}[
                    self.hud.capture_excluded
                ],
                through={True: tr("켜짐"), False: tr("꺼짐"), None: "-"}[self.hud.click_through],
            ),
            tr("입력"): tr(
                "단축키: {hotkey} · 클릭 기록: {mouse}",
                hotkey=(tr("사용") if self.inputs.keyboard_ok else tr("불가")),
                mouse=(tr("사용") if self.inputs.mouse_ok else tr("불가")),
            ),
            tr("버린 늦은 결과"): tr("{stale_results}건", stale_results=self.stale_results),
            tr("연동 부하"): (
                tr("게임 쪽 읽기 {v0:.1f}ms/회", v0=self._plugin_cost)
                if self._plugin_cost is not None
                else tr("게임 쪽 읽기 - (플러그인 1.7부터)")
            )
            + tr(" · 도우미 기지 화면 처리 {v0:.1f}ms/회 (궤적 계산은 별도 프로세스)", v0=self._base_ms),
        }
        cards = "-"
        if observation is not None and observation.kind == ScreenKind.LEVEL_UP:
            parts = []
            game_data = self.data
            for card in observation.cards:
                label = CARD_LABEL_KO.get(card.label, "")
                name = (
                    game_data.name(card.item_id)
                    if card.item_id
                    else (
                        tr("읽지 못함 (가장 비슷: {v0})", v0=game_data.name(card.guess_id))
                        if card.guess_id
                        else tr("읽지 못함")
                    )
                )
                score = (
                    tr(
                        " · 오차 {icon_error:.0f}/차이 {icon_margin:.0f}",
                        icon_error=card.icon_error,
                        icon_margin=card.icon_margin,
                    )
                    if card.icon_error is not None
                    else ""
                )
                parts.append(
                    f"{card.position}: {name}{' · ' + label if label else ''}"
                    f"{' ' + str(card.shown_level) if card.shown_level else ''}{score}"
                )
            if observation.inventory is not None:
                unread = tr("읽지 못함")
                held = [
                    f"{game_data.name(s.item_id) if s.item_id else unread} {s.level if s.level is not None else '?'}"
                    for s in observation.inventory
                    if s.occupied
                ]
                parts.append(tr("보유 칸: ") + (", ".join(held) if held else tr("비어 있음")))
            parts.append(
                tr(
                    "캐릭터 {v0} · 골드 {v1} · 새로고침 {v2}",
                    v0=game_data.name(observation.character_id) if observation.character_id else tr("미확인"),
                    v1=observation.gold if observation.gold is not None else "?",
                    v2=(tr("무료 ") + str(observation.free_rerolls) + tr("회"))
                    if observation.free_rerolls is not None
                    else (
                        str(observation.reroll_cost) + tr("골드")
                        if observation.reroll_cost is not None
                        else "?"
                    ),
                )
            )
            cards = "\n".join(parts)
        timing = tr("아직 없음")
        if self.timings:
            lines = []
            for key, name in (
                ("capture", tr("캡처")),
                ("ocr", tr("글자 인식")),
                ("icons", tr("아이콘 비교")),
                ("total", tr("전체")),
            ):
                vals = sorted(t[key] for t in self.timings if key in t)
                if vals:
                    p95 = vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]
                    lines.append(
                        tr(
                            "{name}: 중앙값 {v0:.0f}ms · p95 {p95:.0f}ms",
                            name=name,
                            v0=statistics.median(vals),
                            p95=p95,
                        )
                    )
            timing = "\n".join(lines) + tr("\n표본 {v0}회", v0=len(self.timings))
        self.control.update_diagnostics(rows, cards, timing)
