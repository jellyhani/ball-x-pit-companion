"""강화 선택 화면의 배치(LevelUpLayout)를 받아 실제 내용을 읽는다.

- 카드 항목: 아이콘 비교 (카드에 이름이 없다)
- 보유 칸: 빈 칸 판별 → 아이콘 비교 → 칸 위 레벨 숫자
- 보유 골드, 캐릭터(초상화 비교)
Windows OCR은 숫자 한 글자만 있으면 읽지 못한다. 그래서 게임 글꼴로 된 '레벨' 글자 조각
(data/ocr/level_prefix.png) 뒤에 숫자를 붙여 '레벨 1' 한 줄로 읽힌다.
같은 영역을 다시 읽지 않도록 축소 이미지 해시로 결과를 기억한다.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import replace
from typing import Callable, List, Optional, Tuple

import numpy as np
from PIL import Image

from ..domain import Card, FrameInfo, InventorySlot, Rect, ScreenKind, ScreenObservation
from .icon_matcher import DATA_DIR, IconLibrary, IconMatch
from .screen_parser import LevelUpLayout
from .text_match import NameIndex

_DIGITS_RE = re.compile(r"레벨\s*(\d{1,4})")

# 아이콘 판정 기준 (icon_matcher 의 상대 오차 단위). 넘으면 '미확인'으로 두고 추천에 쓰지 않는다.
# 기준값 근거: 합성 검사(161종 × 배율)에서 정답 오차는 최대 0.72, 이 기준에서 확신한 오답은 0건이었다.
CARD_MAX_ERROR, CARD_MIN_MARGIN = 0.75, 0.08
SLOT_MAX_ERROR, SLOT_MIN_MARGIN = 0.75, 0.08
PORTRAIT_MAX_ERROR, PORTRAIT_MIN_MARGIN = 0.75, 0.15   # 후보 23개뿐이라 차이 기준을 더 크게


def _crop(img: Image.Image, r: Rect) -> Image.Image:
    x, y, w, h = r
    return img.crop((x, y, x + w, y + h))


def _key(img: Image.Image, tag: str) -> str:
    small = img.convert("RGB").resize((24, 24), Image.Resampling.BOX)
    # 작은 차이(애니메이션 잡음)는 같은 것으로 본다
    q = (np.asarray(small, dtype=np.uint8) // 8).tobytes()
    return tag + hashlib.blake2b(q, digest_size=12).hexdigest()


def slot_is_empty(img: Image.Image) -> bool:
    """빈 보유 칸은 어두운 단색이다. 테두리를 뺀 안쪽의 밝거나 채도가 높은 픽셀 비율로 판단한다."""
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w = a.shape[:2]
    inner = a[int(h * 0.12):int(h * 0.9), int(w * 0.15):int(w * 0.85)]
    if inner.size == 0:
        return True
    bright = (inner.max(axis=2) > 110).mean()
    sat = ((inner.max(axis=2) - inner.min(axis=2)) > 60).mean()
    return bright < 0.03 and sat < 0.03


def _render_prefix() -> Image.Image:
    """'레벨' 글자 조각이 없을 때(공개판에는 게임 화면 조각을 넣지 않음) Windows 기본 한글 글꼴로 그린다.
    게임 조각과 같은 크기(72×38)·바탕색·글자색·글자 영역(x 4~63, y 4~36)에 맞춘다."""
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (72, 38), (36, 18, 21))
    try:
        font = ImageFont.truetype("malgun.ttf", 30)
    except OSError:
        return img
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = d.textbbox((0, 0), "레벨", font=font)
    sx, sy = 59 / (x1 - x0), 32 / (y1 - y0)
    big = Image.new("RGB", (x1 - x0 + 4, y1 - y0 + 4), (36, 18, 21))
    ImageDraw.Draw(big).text((2 - x0, 2 - y0), "레벨", font=font, fill=(224, 167, 162))
    big = big.resize((max(1, int(big.width * sx)), max(1, int(big.height * sy))), Image.LANCZOS)
    img.paste(big, (4 - int(2 * sx), 4 - int(2 * sy)))
    return img


class LevelUpReader:
    def __init__(self, ocr_read: Callable[[Image.Image], list], items: Optional[IconLibrary] = None,
                 portraits: Optional[IconLibrary] = None, prefix_path: Optional[str] = None,
                 names: Optional[NameIndex] = None):
        self.names = names
        self._ocr_read = ocr_read
        self.items = items or IconLibrary.items()
        self.portraits = portraits or IconLibrary.portraits()
        path = prefix_path or os.path.join(DATA_DIR, "ocr", "level_prefix.png")
        self._prefix = Image.open(path).convert("RGB") if os.path.exists(path) else _render_prefix()
        self._cache: "OrderedDict[str, object]" = OrderedDict()

    # ---- 캐시 ----
    def _cached(self, key: str, fn):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        val = fn()
        self._cache[key] = val
        if len(self._cache) > 256:
            self._cache.popitem(last=False)
        return val

    # ---- 개별 읽기 ----
    def identify_icon(self, crop: Image.Image, scale: float, lib: IconLibrary, tag: str,
                      spread=(0.94, 1.0, 1.06)) -> Optional[IconMatch]:
        return self._cached(_key(crop, f"{tag}{scale:.3f}"), lambda: lib.identify(crop, scale, spread=spread))

    def read_number(self, crop: Image.Image) -> Optional[int]:
        """숫자 조각 앞에 게임 글꼴 '레벨' 을 붙여 한 줄로 OCR 한다."""
        def run():
            h = self._prefix.height
            d = crop.convert("RGB")
            if d.height == 0 or d.width == 0:
                return None
            d = d.resize((max(1, round(d.width * h / d.height)), h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (self._prefix.width + d.width + 60, h + 60), (40, 28, 32))
            canvas.paste(self._prefix, (20, 30))
            canvas.paste(d, (26 + self._prefix.width, 30))
            canvas = canvas.resize((canvas.width * 2, canvas.height * 2), Image.Resampling.LANCZOS)
            for line in self._ocr_read(canvas):
                m = _DIGITS_RE.search(line.text)
                if m:
                    return int(m.group(1))
            return None
        return self._cached(_key(crop, "num"), run)

    # ---- 화면 전체 ----
    def read(self, img: Image.Image, frame: FrameInfo, layout: LevelUpLayout, full: bool = True) -> ScreenObservation:
        cards: List[Card] = []
        for spot in layout.cards:
            m = self.identify_icon(_crop(img, spot.icon_rect), layout.card_scale, self.items, "card")
            ok = m is not None and m.error <= CARD_MAX_ERROR and m.margin >= CARD_MIN_MARGIN
            cards.append(Card(
                index=spot.index, position=spot.position, rect=spot.rect, icon_rect=spot.icon_rect,
                item_id=m.ident if ok else None, label=spot.label, shown_level=spot.shown_level,
                icon_error=m.error if m else None, icon_margin=m.margin if m else None,
                guess_id=m.ident if m else None,
            ))

        # 설명 패널 이름과 아이콘 최선 후보가 같으면 확신이 부족했던 카드도 확정한다
        hover = None
        if self.names is not None and layout.hover_title:
            hover = self.names.exact(layout.hover_title) or self.names.fuzzy(layout.hover_title)
        if hover:
            cards = [replace(c, item_id=hover) if c.item_id is None and c.guess_id == hover else c for c in cards]

        inventory: Optional[Tuple[InventorySlot, ...]] = None
        gold = character = None
        if full:
            slots = []
            for i, (rect, icon_box, digit_box) in enumerate(zip(layout.inventory_slots, layout.inventory_icon_boxes,
                                                                  layout.digit_boxes)):
                crop = _crop(img, rect)
                if slot_is_empty(crop):
                    slots.append(InventorySlot(i, rect, occupied=False))
                    continue
                m = self.identify_icon(_crop(img, icon_box), layout.inventory_scale, self.items, "slot")
                ok = m is not None and m.error <= SLOT_MAX_ERROR and m.margin >= SLOT_MIN_MARGIN
                level = self.read_number(_crop(img, digit_box))
                slots.append(InventorySlot(i, rect, True, m.ident if ok else None, level,
                                           m.error if m else None, m.margin if m else None))
            inventory = tuple(slots)
            gold = self.read_number(_crop(img, layout.gold_box))
            pm = self.identify_icon(_crop(img, layout.portrait_box), layout.portrait_scale, self.portraits, "char",
                                    spread=(0.9, 0.97, 1.03, 1.1))   # 초상화마다 원본 크기가 조금 다르다
            if pm is not None and pm.error <= PORTRAIT_MAX_ERROR and pm.margin >= PORTRAIT_MIN_MARGIN:
                character = pm.ident

        return ScreenObservation(
            kind=ScreenKind.LEVEL_UP, frame=frame, cards=tuple(cards), inventory=inventory,
            character_id=character, gold=gold, reroll_cost=layout.reroll_cost, free_rerolls=layout.free_rerolls,
            banish_left=layout.banish_left, points_left=layout.points_left,
            reroll_rect=layout.reroll_rect, banish_rect=layout.banish_rect, skip_rect=layout.skip_rect,
            panel_rect=layout.panel_rect, hover_item_id=hover,
        )
