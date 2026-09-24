"""인식 결과와 선택 세션의 데이터 형식. Qt에 의존하지 않는다.

모르는 값은 None 이다. 0이나 빈 목록과 구분한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

Rect = Tuple[int, int, int, int]  # x, y, w, h (프레임 물리 픽셀)


class ScreenKind(str, Enum):
    GAME_NOT_FOUND = "game_not_found"
    CAPTURE_FAILED = "capture_failed"
    LEVEL_UP = "level_up"
    FUSION = "fusion"
    PAUSE = "pause"
    OTHER = "other"


SCREEN_LABEL = {
    ScreenKind.GAME_NOT_FOUND: "게임 창 없음",
    ScreenKind.CAPTURE_FAILED: "캡처 실패",
    ScreenKind.LEVEL_UP: "강화 선택 화면",
    ScreenKind.FUSION: "융합 화면",
    ScreenKind.PAUSE: "일시 정지",
    ScreenKind.OTHER: "전투 또는 기타 화면",
}


class CardLabel(str, Enum):
    """카드 아래 문구. 게임 공식 번역: 'New!' → '신규!', 'Level {N}↑' → '레벨 N'."""
    NEW = "new"
    UPGRADE = "upgrade"

    @property
    def is_upgrade(self) -> bool:
        return self is CardLabel.UPGRADE


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def rect(self) -> Rect:
        return (self.x, self.y, self.w, self.h)


@dataclass(frozen=True)
class FrameInfo:
    """캡처한 게임 클라이언트 영역. 좌표는 물리 픽셀."""
    frame_id: int
    captured_at: float          # time.monotonic()
    origin: Tuple[int, int]     # 클라이언트 영역 왼쪽 위의 화면 좌표
    size: Tuple[int, int]
    backend: str = ""

    def to_screen(self, rect: Rect) -> Rect:
        x, y, w, h = rect
        return (self.origin[0] + x, self.origin[1] + y, w, h)


@dataclass(frozen=True)
class Card:
    index: int                        # 게임 화면 배치 순서 (왼쪽부터 0)
    position: str                     # "왼쪽", "가운데", "오른쪽", "2번째" ...
    rect: Rect                        # 카드 전체 영역 (클릭 판정용)
    icon_rect: Rect = (0, 0, 0, 0)    # 아이콘을 찾는 영역
    item_id: Optional[str] = None     # 아이콘으로 확인한 항목. 모르면 None
    label: Optional[CardLabel] = None
    shown_level: Optional[int] = None  # '레벨 N' 의 N (선택하면 되는 레벨)
    icon_error: Optional[float] = None
    icon_margin: Optional[float] = None
    guess_id: Optional[str] = None    # 확신이 부족할 때의 최선 후보 (진단용, 추천에는 쓰지 않음)
    synergy: Tuple[str, ...] = ()     # 게임이 시너지로 판정한 보유 볼 (게임 연동에서만)
    ai_pick: Optional[bool] = None    # 게임 자동 선택 AI가 고르는 항목인지 (게임 연동에서만)

    @property
    def recognized(self) -> bool:
        return self.item_id is not None

    def signature(self) -> tuple:
        return (self.item_id, self.label, self.shown_level)


@dataclass(frozen=True)
class InventorySlot:
    index: int                        # 0–7, 윗줄 왼쪽부터
    rect: Rect
    occupied: bool
    item_id: Optional[str] = None
    level: Optional[int] = None
    icon_error: Optional[float] = None
    icon_margin: Optional[float] = None
    at_max: Optional[bool] = None      # 게임이 알려 준 최대 레벨 여부


@dataclass(frozen=True)
class RunProgress:
    """런 진행 상황 (게임 연동에서만). 모르는 값은 None."""
    health: Optional[int] = None
    max_health: Optional[int] = None
    turn: Optional[int] = None
    final_boss_turn: Optional[int] = None
    level_name: str = ""
    difficulty: Optional[int] = None
    ng_plus: Optional[int] = None
    endless: Optional[bool] = None
    endless_start_turn: Optional[int] = None   # 보스 격퇴 후 '원정 계속'을 누른 턴
    next_boss_turn: Optional[int] = None       # 게임 지역 일정(LevelInfo.BossTurns)에서 다음 보스 턴
    next_fuser_turn: Optional[int] = None      # 다음 융합기 턴
    revives_left: Optional[int] = None
    max_balls: Optional[int] = None
    max_passives: Optional[int] = None
    balls: Optional[int] = None
    passives: Optional[int] = None
    banished: Tuple[str, ...] = ()

    @property
    def health_ratio(self) -> Optional[float]:
        if self.health is None or not self.max_health:
            return None
        return max(0.0, min(1.0, self.health / self.max_health))

    @property
    def turns_to_boss(self) -> Optional[int]:
        if self.turn is None or not self.final_boss_turn:
            return None
        return self.final_boss_turn - self.turn

    @property
    def turns_to_next_boss(self) -> Optional[int]:
        if self.turn is None or not self.next_boss_turn:
            return None
        return self.next_boss_turn - self.turn

    @property
    def turns_to_fuser(self) -> Optional[int]:
        if self.turn is None or not self.next_fuser_turn:
            return None
        return self.next_fuser_turn - self.turn

    @property
    def run_fraction(self) -> Optional[float]:
        if self.turn is None or not self.final_boss_turn:
            return None
        return max(0.0, min(1.0, self.turn / self.final_boss_turn))


@dataclass(frozen=True)
class FuserCombo:
    """융합 후보: 최대 레벨 볼 두 개를 하나로 합친다."""
    item1: Optional[str]
    item2: Optional[str]
    idx1: int
    idx2: int
    ai_score: Optional[float] = None    # 게임 자동 선택 AI 의 점수 (게임 내부 값)
    bad: Optional[bool] = None          # 게임이 나쁜 조합으로 판단하는지


@dataclass(frozen=True)
class FuserEvo:
    """진화 후보: 결과 항목과 진화할 보유 볼 위치."""
    item_id: Optional[str]
    equip_idx: int


@dataclass(frozen=True)
class FuserOptions:
    options: Tuple[str, ...] = ()        # kEvo / kCombo / kFreeUpgrades
    evos: Tuple[FuserEvo, ...] = ()
    combos: Tuple[FuserCombo, ...] = ()
    free_upgrades: Optional[bool] = None


@dataclass(frozen=True)
class ChoicePool:
    """다음 선택지가 뽑히는 후보 (게임 연동). 새로고침하면 직전 선택지(prev)는 빠진다."""
    new_balls: Tuple[str, ...] = ()
    ball_upgrades: Tuple[str, ...] = ()
    new_passives: Tuple[str, ...] = ()
    passive_upgrades: Tuple[str, ...] = ()
    prev: Tuple[str, ...] = ()
    num_choices: int = 3

    def entries(self) -> List[Tuple[str, bool]]:
        """(항목 ID, 강화 여부) 목록."""
        return ([(i, False) for i in self.new_balls] + [(i, True) for i in self.ball_upgrades]
                + [(i, False) for i in self.new_passives] + [(i, True) for i in self.passive_upgrades])


@dataclass(frozen=True)
class GameOverInfo:
    """런이 끝난 화면 (게임 연동). 보스를 깨면 '원정 계속' 버튼이 있다."""
    completed: Optional[bool] = None
    endless_button: Optional[bool] = None


@dataclass(frozen=True)
class ScreenObservation:
    kind: ScreenKind
    frame: Optional[FrameInfo] = None
    cards: Tuple[Card, ...] = ()
    inventory: Optional[Tuple[InventorySlot, ...]] = None   # None = 보유 칸을 읽지 않음
    character_id: Optional[str] = None
    gold: Optional[int] = None
    reroll_cost: Optional[int] = None       # '새로고침 (5 골드)'
    free_rerolls: Optional[int] = None      # '무료 새로고침 (N 남음)'
    banish_left: Optional[int] = None
    points_left: Optional[int] = None
    reroll_rect: Optional[Rect] = None
    banish_rect: Optional[Rect] = None
    skip_rect: Optional[Rect] = None
    panel_rect: Optional[Rect] = None      # 게임의 강화 선택 패널 영역
    hover_item_id: Optional[str] = None    # 마우스를 올린 카드의 설명 패널 이름으로 확인한 항목
    fuser: Optional[FuserOptions] = None    # 융합 화면일 때 (게임 연동에서만)
    extra_characters: Tuple[str, ...] = ()  # 함께 쓰는 캐릭터 (게임 연동에서만)
    progress: Optional[RunProgress] = None  # 런 진행 상황 (게임 연동에서만)
    pool: Optional[ChoicePool] = None       # 선택지 후보 (게임 연동에서만)
    error: str = ""

    def card_signature(self) -> tuple:
        return tuple(c.signature() for c in self.cards)


@dataclass
class ChoiceSession:
    session_id: int
    opened_at: float
    cards: Tuple[Card, ...]
    frame: FrameInfo
    inventory: Optional[Tuple[InventorySlot, ...]] = None
    character_id: Optional[str] = None
    gold: Optional[int] = None
    reroll_cost: Optional[int] = None
    free_rerolls: Optional[int] = None
    banish_left: Optional[int] = None
    points_left: Optional[int] = None
    reroll_rect: Optional[Rect] = None
    banish_rect: Optional[Rect] = None
    skip_rect: Optional[Rect] = None
    panel_rect: Optional[Rect] = None
    progress: Optional["RunProgress"] = None
    pool: Optional["ChoicePool"] = None
    last_seen_at: float = 0.0
    closed: bool = False

    @property
    def signature(self) -> tuple:
        return tuple(c.signature() for c in self.cards)

    @property
    def unknown_count(self) -> int:
        return sum(1 for c in self.cards if not c.recognized)

    @property
    def can_reroll(self) -> Optional[bool]:
        """새로고침 가능 여부. 확인할 수 없으면 None."""
        if self.free_rerolls is not None:
            return self.free_rerolls > 0
        if self.reroll_cost is None or self.gold is None:
            return None
        return self.gold >= self.reroll_cost


@dataclass(frozen=True)
class Click:
    x: int
    y: int
    at: float   # time.monotonic()


@dataclass
class PickOutcome:
    session_id: int
    kind: str                      # "picked" | "skipped" | "rerolled" | "banished" | "unknown"
    card: Optional[Card] = None
    evidence: str = ""
    options: Tuple[Card, ...] = field(default_factory=tuple)
