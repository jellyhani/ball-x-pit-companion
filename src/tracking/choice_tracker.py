"""선택 세션 추적: 선택창 열림 → 안정 확인 → 닫힘/새로고침 → 실제 선택 판단.

'봤다'와 '골랐다'를 구분한다. 추천 1위를 골랐다고 가정하지 않는다.
선택 결과의 근거는 (1) 선택창이 닫히기 직전의 마우스 클릭이 어느 카드·버튼 위였는지,
(2) 게임 로그의 새로고침 기록, (3) 새로고침 남은 횟수 변화다. 근거가 없으면 '확인 못 함'으로 남긴다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Deque, List, Optional, Tuple

from ..domain import Card, ChoiceSession, Click, PickOutcome, Rect, ScreenKind, ScreenObservation
from ..i18n import tr


@dataclass
class TrackerEvent:
    kind: str  # opened | updated | closed
    session: ChoiceSession
    outcome: Optional[PickOutcome] = None


def _inside(x: int, y: int, rect: Optional[Rect]) -> bool:
    if rect is None:
        return False
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def _compatible(a: Tuple[Card, ...], b: Tuple[Card, ...]) -> bool:
    """같은 선택창을 더 잘 읽은 결과인지(한쪽이 미확인일 뿐 모순이 없는지)."""
    if len(a) != len(b):
        return False
    for ca, cb in zip(a, b):
        if ca.item_id and cb.item_id and ca.item_id != cb.item_id:
            return False
        if ca.label and cb.label and ca.label != cb.label:
            return False
        if ca.shown_level and cb.shown_level and ca.shown_level != cb.shown_level:
            return False
    return True


def _merge(old: Tuple[Card, ...], new: Tuple[Card, ...]) -> Tuple[Card, ...]:
    result = []
    for co, cn in zip(old, new):
        keep_icon = cn.item_id is None and co.item_id is not None
        result.append(
            replace(
                cn,
                item_id=cn.item_id or co.item_id,
                icon_error=co.icon_error if keep_icon else cn.icon_error,
                icon_margin=co.icon_margin if keep_icon else cn.icon_margin,
                label=cn.label or co.label,
                shown_level=cn.shown_level or co.shown_level,
            )
        )
    return tuple(result)


SESSION_FIELDS = (
    "inventory",
    "character_id",
    "gold",
    "reroll_cost",
    "free_rerolls",
    "banish_left",
    "points_left",
    "reroll_rect",
    "banish_rect",
    "skip_rect",
    "panel_rect",
    "progress",
    "pool",
    "extra_characters",
)
# 열린 뒤 갱신하는 필드. 보유 목록은 선택창이 열릴 때 값으로 고정한다 — 게임은 선택을 반영한 뒤에
# 창을 닫으므로, 갱신하면 선택 전후 비교(무엇을 골랐는지)가 불가능해진다.
UPDATE_FIELDS = tuple(f for f in SESSION_FIELDS if f != "inventory")


class ChoiceTracker:
    def __init__(self, stable_frames: int = 2, close_frames: int = 2, reopen_guard_s: float = 2.5):
        self.stable_frames = stable_frames
        self.close_frames = close_frames
        self.reopen_guard_s = reopen_guard_s
        self.session: Optional[ChoiceSession] = None
        self.generation = 0
        self._next_id = 1
        self._candidate: Optional[tuple] = None
        self._candidate_count = 0
        self._miss = 0
        self._clicks: Deque[Click] = deque(maxlen=32)
        self._recent_closed: Optional[Tuple[tuple, float]] = None

    @property
    def awaiting_confirmation(self) -> bool:
        """선택창 후보를 한 번 봤고 다음 프레임에서 확인을 기다리는 중."""
        return self._candidate is not None

    # ---- 입력 ----
    def add_click(self, click: Click):
        self._clicks.append(click)

    def reset(self) -> List[TrackerEvent]:
        """런 시작·종료 때. 열린 세션은 결과 미확인으로 닫는다."""
        events = []
        if self.session is not None:
            events.append(self._close(self.session.last_seen_at, force_kind="unknown"))
        self.generation += 1
        self._candidate = None
        self._candidate_count = 0
        self._recent_closed = None
        self._clicks.clear()
        return events

    def note_reroll(self, at: float) -> List[TrackerEvent]:
        """게임 로그에서 새로고침을 확인했을 때. 이미 새 카드로 바뀐 세션이면 무시한다."""
        s = self.session
        if s is None or s.opened_at >= at - 1.5:
            return []
        self.generation += 1
        return [self._close(at, force_kind="rerolled", evidence=tr("게임 로그의 새로고침 기록"))]

    def observe(
        self, observation: ScreenObservation, now: float, forced: bool = False, immediate: bool = False
    ) -> List[TrackerEvent]:
        """immediate: 게임 연동처럼 확실한 입력. 두 번 확인하지 않고 바로 열고 닫는다."""
        if observation.kind == ScreenKind.CAPTURE_FAILED:
            return []  # 일시적인 캡처 실패는 선택창 종료로 보지 않는다
        if observation.kind == ScreenKind.LEVEL_UP and observation.cards:
            return self._observe_choice(observation, now, forced or immediate, immediate)
        self._candidate = None
        self._candidate_count = 0
        if self.session is None:
            return []
        self._miss += 1
        if self._miss >= (1 if immediate else self.close_frames):
            return [self._close(now)]
        return []

    # ---- 내부 ----
    def _observe_choice(
        self, observation: ScreenObservation, now: float, forced: bool, immediate: bool = False
    ) -> List[TrackerEvent]:
        sig = observation.card_signature()
        s = self.session
        self._miss = 0
        if s is not None:
            if sig == s.signature or _compatible(s.cards, observation.cards):
                merged = _merge(s.cards, observation.cards)
                changed = merged != s.cards
                s.cards = merged
                s.frame = observation.frame
                s.last_seen_at = now
                for f in UPDATE_FIELDS:
                    value = getattr(observation, f)
                    if value is not None and value != getattr(s, f):
                        setattr(s, f, value)
                        changed = True
                return [TrackerEvent("updated", s)] if changed else []
            # 카드가 바뀜: 두 번 연속 같은 내용일 때만 전환한다(애니메이션 중간 프레임 방지)
            if not self._stable(sig, forced):
                return []
            events = [self._close(now, next_obs=observation)]
            events.append(self._open(observation, now))
            return events

        if (
            not immediate
            and self._recent_closed
            and self._recent_closed[0] == sig
            and now - self._recent_closed[1] < self.reopen_guard_s
        ):
            return []  # 닫힌 직후 화면이 사라지는 중인 프레임
        if not self._stable(sig, forced):
            return []
        return [self._open(observation, now)]

    def _stable(self, sig: tuple, forced: bool) -> bool:
        if self._candidate == sig:
            self._candidate_count += 1
        else:
            self._candidate = sig
            self._candidate_count = 1
        return forced or self._candidate_count >= self.stable_frames

    def _open(self, observation: ScreenObservation, now: float) -> TrackerEvent:
        s = ChoiceSession(
            session_id=self._next_id,
            opened_at=now,
            cards=observation.cards,
            frame=observation.frame,
            last_seen_at=now,
            **{f: getattr(observation, f) for f in SESSION_FIELDS},
        )
        self._next_id += 1
        self.session = s
        self._candidate = None
        self._candidate_count = 0
        return TrackerEvent("opened", s)

    def _close(
        self,
        now: float,
        force_kind: str = "",
        evidence: str = "",
        next_obs: Optional[ScreenObservation] = None,
    ) -> TrackerEvent:
        s = self.session
        assert s is not None
        s.closed = True
        self.session = None
        self._miss = 0
        self._recent_closed = (s.signature, now)
        self.generation += 1
        if force_kind:
            outcome = PickOutcome(s.session_id, force_kind, evidence=evidence, options=s.cards)
        else:
            outcome = self._judge(s, next_obs)
        return TrackerEvent("closed", s, outcome)

    def _judge(self, choice_session: ChoiceSession, next_obs: Optional[ScreenObservation]) -> PickOutcome:
        if next_obs is not None:
            if (
                choice_session.free_rerolls is not None
                and next_obs.free_rerolls is not None
                and next_obs.free_rerolls < choice_session.free_rerolls
            ):
                return PickOutcome(
                    choice_session.session_id,
                    "rerolled",
                    evidence=tr("무료 새로고침 횟수 감소"),
                    options=choice_session.cards,
                )
            if (
                choice_session.gold is not None
                and next_obs.gold is not None
                and choice_session.reroll_cost
                and choice_session.gold - next_obs.gold == choice_session.reroll_cost
            ):
                return PickOutcome(
                    choice_session.session_id,
                    "rerolled",
                    evidence=tr("새로고침 비용만큼 골드 감소"),
                    options=choice_session.cards,
                )
            if (
                choice_session.banish_left is not None
                and next_obs.banish_left is not None
                and next_obs.banish_left < choice_session.banish_left
            ):
                return PickOutcome(
                    choice_session.session_id,
                    "banished",
                    evidence=tr("삭제 남은 횟수 감소"),
                    options=choice_session.cards,
                )
        origin_x, origin_y = choice_session.frame.origin
        for c in reversed(self._clicks):
            if c.at < choice_session.opened_at - 0.2 or c.at > choice_session.last_seen_at + 1.5:
                continue
            fx, fy = c.x - origin_x, c.y - origin_y
            if _inside(fx, fy, choice_session.skip_rect):
                return PickOutcome(
                    choice_session.session_id,
                    "skipped",
                    evidence=tr("넘기기 버튼 클릭"),
                    options=choice_session.cards,
                )
            if _inside(fx, fy, choice_session.reroll_rect):
                return PickOutcome(
                    choice_session.session_id,
                    "rerolled",
                    evidence=tr("새로고침 버튼 클릭"),
                    options=choice_session.cards,
                )
            if _inside(fx, fy, choice_session.banish_rect):
                return PickOutcome(
                    choice_session.session_id,
                    "banished",
                    evidence=tr("삭제 버튼 클릭"),
                    options=choice_session.cards,
                )
            for card in choice_session.cards:
                if _inside(fx, fy, card.rect):
                    return PickOutcome(
                        choice_session.session_id,
                        "picked",
                        card=card,
                        evidence=tr("{position} 카드 클릭", position=tr(card.position)),
                        options=choice_session.cards,
                    )
        return PickOutcome(
            choice_session.session_id,
            "unknown",
            evidence=tr("선택 근거 없음 (키보드·패드 선택이거나 클릭 위치 불명)"),
            options=choice_session.cards,
        )
