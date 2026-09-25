"""OCR 줄 목록에서 강화 선택 화면의 배치를 계산한다. 순수 함수이며 Qt·Windows에 의존하지 않는다.

강화 선택 화면(버전 1.301, 사용자 스크린샷 1920×1080 기준으로 측정):
  - 제목 '강화 선택' (또는 '강화를 선택하세요 (N 포인트 남음)')
  - 그 아래 카드들. 카드에는 이름이 없고 아이콘과 '신규!' 또는 '레벨 N↑' 만 있다.
  - 제목 위쪽에 보유 칸 8개(4×2)와 각 칸 위의 레벨 숫자, 그 아래 보유 골드.
  - 왼쪽 위에 캐릭터 초상화, 맨 아래 '새로고침 (비용 골드)' 버튼.
모든 위치는 '제목 중심'과 '카드 간격(P)'의 비율로 계산한다. 해상도가 달라도 UI가 같은 비율로
커진다는 가정이며, 1920×1080 외의 해상도는 아직 실제 화면으로 확인하지 못했다.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..domain import CardLabel, FrameInfo, OcrLine, Rect, ScreenKind
from .text_match import normalize
from ..i18n import tr

# 1920×1080 스크린샷에서 잰 비율 (단위: 카드 간격 P, 원점: 제목 중심)
LABEL_DY_RANGE = (0.6, 1.35)   # 카드 문구의 세로 위치 (버튼 수에 따라 0.84–1.14 관측)
HEADER_H_TO_PITCH = 8.85       # 제목 글자 높이 34px ↔ 카드 간격 300px
CARD_ICON_DY = 0.44
CARD_ICON_HALF = 0.30
CARD_HALF_W = 0.43
CARD_TOP_DY, CARD_BELOW_LABEL = 0.11, 0.13   # 카드 윗변(제목 기준), 아랫변(문구 중심 기준)
INV_COL_X0, INV_COL_PITCH = 0.109, 0.2985
INV_ROWS = ((-1.337, -0.813), (-0.789, -0.368))
INV_HALF_W = 0.13
INV_ICON_HALF_W, INV_ICON_BOTTOM_PAD = 0.16, 0.013   # 아이콘 불꽃 등이 칸 밖으로 조금 나온다
DIGIT_HALF_W, DIGIT_TOP, DIGIT_BOTTOM = 0.065, 0.063, 0.172
GOLD_BOX = (0.12, -0.279, 0.55, -0.172)          # x0, y0, x1, y1
PORTRAIT_BOX = (-1.486, -1.42, -0.226, -0.17)
REROLL_HALF_W = 1.43
PANEL_BOX = (-1.55, -1.8, 1.573, 1.7)            # 왼쪽 강화 패널 전체 (HUD를 피해서 둘 영역)
CARD_SCALE = 1 / 100.5        # 원본 1픽셀이 화면 P/100.5 픽셀
INVENTORY_SCALE = 1 / 158.7
PORTRAIT_SCALE = 1 / 100.5

_LEVEL_RE = re.compile(r"^(?:레벨|lvl|lv\.?)\s*(\d{1,2})", re.IGNORECASE)
_LEFT_RE = re.compile(r"(\d+)\s*남음")
_POINTS_RE = re.compile(r"(\d+)\s*포인트")
_COST_RE = re.compile(r"\(\s*(\d+)")
HOVER_LABELS = {"새로운볼", "볼강화", "새패시브", "패시브강화"}   # 게임 번역 'New Ball!' 등


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def label_of(line: OcrLine) -> Tuple[Optional[CardLabel], Optional[int]]:
    key = normalize(line.text)
    if key in ("신규", "신규!") or (key.startswith("신규") and len(key) <= 4):
        return CardLabel.NEW, None
    m = _LEVEL_RE.match(_nfkc(line.text))
    if m and len(key) <= 7:
        return CardLabel.UPGRADE, int(m.group(1))
    return None, None


def _box(x0: float, y0: float, x1: float, y1: float, size: Tuple[int, int]) -> Rect:
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(size[0], int(x1)), min(size[1], int(y1))
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


@dataclass(frozen=True)
class CardSpot:
    index: int
    position: str
    rect: Rect
    icon_rect: Rect
    label: Optional[CardLabel]
    shown_level: Optional[int]


@dataclass(frozen=True)
class LevelUpLayout:
    header: OcrLine
    pitch: float
    cards: Tuple[CardSpot, ...]
    inventory_slots: Tuple[Rect, ...]
    inventory_icon_boxes: Tuple[Rect, ...]
    digit_boxes: Tuple[Rect, ...]
    gold_box: Rect
    portrait_box: Rect
    card_scale: float
    inventory_scale: float
    portrait_scale: float
    reroll_rect: Optional[Rect] = None
    reroll_cost: Optional[int] = None
    free_rerolls: Optional[int] = None
    banish_rect: Optional[Rect] = None
    banish_left: Optional[int] = None
    skip_rect: Optional[Rect] = None
    points_left: Optional[int] = None
    panel_rect: Optional[Rect] = None
    hover_title: str = ""          # 마우스를 올린 카드의 설명 패널 제목(항목 이름 원문)


@dataclass(frozen=True)
class ParsedScreen:
    kind: ScreenKind
    layout: Optional[LevelUpLayout] = None
    note: str = ""


def _position_name(offset: float) -> str:
    if offset < -0.25:
        return tr("왼쪽")
    if offset > 0.25:
        return tr("오른쪽")
    return tr("가운데")


def parse_layout(lines: Sequence[OcrLine], frame: FrameInfo) -> ParsedScreen:
    size = frame.size
    header = reroll = banish = skip = None
    fusion = pause = False
    for ln in lines:
        key = normalize(ln.text)
        if key in ("강화선택",) or "강화를선택" in key:
            if header is None or ln.h > header.h:
                header = ln
        elif "새로고침" in key:
            reroll = ln
        elif "삭제" in key and "남음" in key:
            banish = ln
        elif "넘기기" in key and len(key) <= 6:
            skip = ln
        if "선택가능융합" in key or key == "융합" or "융합대신" in key:
            fusion = True
        if key == "일시정지":
            pause = True

    if header is None:
        kind = ScreenKind.FUSION if fusion else ScreenKind.PAUSE if pause else ScreenKind.OTHER
        return ParsedScreen(kind)

    hx, hy = header.cx, header.cy
    labels = []
    for ln in lines:
        if ln.cy <= hy:
            continue
        lab, lvl = label_of(ln)
        if lab is not None:
            labels.append((ln, lab, lvl))
    if not labels:
        return ParsedScreen(ScreenKind.OTHER, note=tr("제목은 있으나 카드 문구를 찾지 못함"))
    labels.sort(key=lambda t: t[0].cx)

    xs = [t[0].cx for t in labels]
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > header.h]
    ly = statistics.median(t[0].cy for t in labels)
    # 카드 간격 P: 제목 글자 높이로 어림한 뒤 문구 사이 가로 간격으로 맞춘다.
    # 문구의 세로 위치는 쓰지 않는다 — '삭제'·'넘기기' 버튼이 있으면 카드가 짧아져 문구가 올라간다.
    pitch = HEADER_H_TO_PITCH * header.h
    if gaps:
        g = min(gaps)
        pitch = g / max(1, round(g / pitch))   # 가운데 문구를 놓치면 간격이 두 배가 된다
    same_row = all(abs(t[0].cy - ly) < 0.15 * pitch for t in labels)
    near = all(abs(x - hx) < 2.2 * pitch for x in xs)
    below = LABEL_DY_RANGE[0] * pitch <= ly - hy <= LABEL_DY_RANGE[1] * pitch
    plausible = 6.0 * header.h <= pitch <= 12.0 * header.h
    if not (same_row and near and below and plausible):
        # 카드 문구의 간격·줄·세로 위치가 선택창 배치와 맞지 않으면 다른 화면으로 본다
        return ParsedScreen(ScreenKind.OTHER, note=tr("카드 배치가 예상과 다름"))
    P = pitch

    # 카드 자리: 문구가 있는 곳 + 그 사이 비어 있는 자리(문구를 못 읽은 카드)
    offsets = [round((x - hx) / P * 2) / 2 for x in xs]
    step = 1.0
    wanted = []
    o = min(offsets)
    while o <= max(offsets) + 1e-6:
        wanted.append(o)
        o += step
    spots: List[CardSpot] = []
    names = [_position_name(o) for o in wanted]
    if len(set(names)) != len(names):
        names = [tr("{v0}번째", v0=i + 1) for i in range(len(wanted))]
    for i, off in enumerate(wanted):
        found = next((t for t, oo in zip(labels, offsets) if abs(oo - off) < 0.3), None)
        cx = found[0].cx if found else hx + off * P
        rect = _box(cx - CARD_HALF_W * P, hy + CARD_TOP_DY * P, cx + CARD_HALF_W * P, ly + CARD_BELOW_LABEL * P, size)
        icy = hy + CARD_ICON_DY * P
        icon = _box(cx - CARD_ICON_HALF * P, icy - CARD_ICON_HALF * P, cx + CARD_ICON_HALF * P,
                    icy + CARD_ICON_HALF * P, size)
        spots.append(CardSpot(i, names[i], rect, icon, found[1] if found else None, found[2] if found else None))

    slots, icons, digits = [], [], []
    for top, bottom in INV_ROWS:
        for c in range(4):
            cx = hx + P * (INV_COL_X0 + INV_COL_PITCH * c)
            y0 = hy + top * P
            slots.append(_box(cx - INV_HALF_W * P, y0, cx + INV_HALF_W * P, hy + bottom * P, size))
            icons.append(_box(cx - INV_ICON_HALF_W * P, y0 + DIGIT_BOTTOM * P, cx + INV_ICON_HALF_W * P,
                              hy + (bottom + INV_ICON_BOTTOM_PAD) * P, size))
            digits.append(_box(cx - DIGIT_HALF_W * P, y0 + DIGIT_TOP * P, cx + DIGIT_HALF_W * P,
                               y0 + DIGIT_BOTTOM * P, size))
    gx0, gy0, gx1, gy1 = GOLD_BOX
    px0, py0, px1, py1 = PORTRAIT_BOX

    reroll_cost = free = None
    reroll_rect = None
    if reroll is not None:
        raw = _nfkc(reroll.text)
        if "무료" in raw:
            m = _LEFT_RE.search(raw)
            free = int(m.group(1)) if m else None
        else:
            m = _COST_RE.search(raw)
            reroll_cost = int(m.group(1)) if m else None
        reroll_rect = _box(hx - REROLL_HALF_W * P, reroll.y - 0.1 * P, hx + REROLL_HALF_W * P,
                           reroll.y + reroll.h + 0.1 * P, size)
    banish_left = None
    if banish is not None:
        m = _LEFT_RE.search(_nfkc(banish.text))
        banish_left = int(m.group(1)) if m else None
    m = _POINTS_RE.search(_nfkc(header.text))

    # 카드에 마우스를 올리면 패널 오른쪽에 '새로운 볼! / 이름 / 설명' 이 나온다. 이름을 교차 확인에 쓴다.
    hover_title = ""
    for ln in lines:
        if normalize(ln.text) in HOVER_LABELS and ln.cx > hx + 1.6 * P:
            below_lines = [o for o in lines if o is not ln and 0 < o.cy - ln.cy < 4.5 * header.h
                           and abs(o.cx - ln.cx) < max(o.w, ln.w)]
            if below_lines:
                hover_title = min(below_lines, key=lambda o: o.cy - ln.cy).text
            break

    pad = int(0.05 * P)
    layout = LevelUpLayout(
        hover_title=hover_title,
        header=header, pitch=P, cards=tuple(spots),
        inventory_slots=tuple(slots), inventory_icon_boxes=tuple(icons), digit_boxes=tuple(digits),
        gold_box=_box(hx + gx0 * P, hy + gy0 * P, hx + gx1 * P, hy + gy1 * P, size),
        portrait_box=_box(hx + px0 * P, hy + py0 * P, hx + px1 * P, hy + py1 * P, size),
        card_scale=P * CARD_SCALE, inventory_scale=P * INVENTORY_SCALE, portrait_scale=P * PORTRAIT_SCALE,
        reroll_rect=reroll_rect, reroll_cost=reroll_cost, free_rerolls=free,
        banish_rect=_box(banish.x - pad, banish.y - pad, banish.x + banish.w + pad, banish.y + banish.h + pad, size)
        if banish else None,
        banish_left=banish_left,
        skip_rect=_box(skip.x - pad, skip.y - pad, skip.x + skip.w + pad, skip.y + skip.h + pad, size) if skip else None,
        points_left=int(m.group(1)) if m else None,
        panel_rect=_box(hx + PANEL_BOX[0] * P, hy + PANEL_BOX[1] * P, hx + PANEL_BOX[2] * P, hy + PANEL_BOX[3] * P, size),
    )
    return ParsedScreen(ScreenKind.LEVEL_UP, layout)
