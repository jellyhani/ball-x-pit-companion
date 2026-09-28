"""화면의 아이콘을 게임에서 추출한 원본 아이콘(data/icons)과 비교해 항목을 알아낸다.

선택창 카드에는 이름이 없고 아이콘만 있으므로 이것이 항목 식별의 주 경로다.
- 원본 스프라이트를 화면 배율로 키운 뒤, 둘 다 같은 비율로 축소해서 투명하지 않은 픽셀만 비교한다.
- 영역 안에서 위치를 조금씩 옮겨 가며(슬라이딩) 가장 잘 맞는 자리를 찾는다.
- 결과에는 오차와, 2위 항목과의 오차 차이(margin)를 함께 돌려준다. 이것은 이 앱이 계산한 비교 값이지
  엔진이 준 신뢰도가 아니다. 차이가 작으면 호출하는 쪽이 '미확인'으로 처리한다.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

COVERAGE_WEIGHT = 0.6
from ..gamedata import DATA_DIR  # 설치 때 게임에서 추출한 자료 폴더 (없으면 저장소 data/)


@dataclass(frozen=True)
class IconMatch:
    ident: str
    error: float  # 상대 오차 + 모양 밖 픽셀 비율. 0에 가까울수록 일치 (이 앱이 계산한 비교 값)
    margin: float  # 2위(다른 항목)와의 오차 차이
    center: Tuple[int, int]  # 영역 기준 아이콘 중심(원본 픽셀)
    scale: float
    raw_error: float = 0.0  # 가려진 픽셀 평균 색 차이(0–255)

    def confident(self, max_error: float = 0.45, min_margin: float = 0.05) -> bool:
        return self.error <= max_error and self.margin >= min_margin


def _load_dir(folder: str) -> Dict[str, Image.Image]:
    result = {}
    for path in glob.glob(os.path.join(folder, "*.png")):
        ident = os.path.splitext(os.path.basename(path))[0].replace("_", ":", 1)
        result[ident] = Image.open(path).convert("RGBA")
    return result


class IconLibrary:
    def __init__(self, sprites: Dict[str, Image.Image]):
        self.sprites = sprites
        self._cache: Dict[Tuple[float, str], Tuple[np.ndarray, np.ndarray]] = {}

    @classmethod
    def items(cls, data_dir: str = DATA_DIR) -> "IconLibrary":
        return cls(_load_dir(os.path.join(data_dir, "icons")))

    @classmethod
    def portraits(cls, data_dir: str = DATA_DIR) -> "IconLibrary":
        return cls(_load_dir(os.path.join(data_dir, "portraits")))

    def _template(self, ident: str, factor: float) -> Tuple[np.ndarray, np.ndarray]:
        """원본을 factor 배로 (화면 배율 / 축소 비율) 만든 색·마스크."""
        key = (round(factor, 4), ident)
        hit = self._cache.get(key)
        if hit is None:
            sp = self.sprites[ident]
            w = max(2, round(sp.width * factor))
            h = max(2, round(sp.height * factor))
            small = sp.convert("RGBa").resize((w, h), Image.Resampling.BOX).convert("RGBA")
            arr = np.asarray(small, dtype=np.int16)
            mask = arr[..., 3] > 160
            hit = (arr[..., :3].copy(), mask)
            self._cache[key] = hit
        return hit

    def match(
        self,
        region: Image.Image,
        scale: float,
        candidates: Optional[Iterable[str]] = None,
        work_size: int = 20,
    ) -> List[IconMatch]:
        """region 안에서 가장 잘 맞는 항목 순서대로. scale 은 원본 1픽셀이 화면 몇 픽셀인지."""
        ids = list(candidates) if candidates is not None else list(self.sprites)
        if not ids:
            return []
        typical = max(max(self.sprites[index].size) for index in ids)
        ds = max(1.0, typical * scale / work_size)  # 비교용 축소 비율
        rw, rh = max(1, round(region.width / ds)), max(1, round(region.height / ds))
        reg = np.asarray(region.convert("RGB").resize((rw, rh), Image.Resampling.BOX), dtype=np.int16)
        # 배경색: 영역 가장자리의 중앙값. 아이콘 대신 배경만 있다고 볼 때의 오차와 비교한다.
        border = np.concatenate([reg[0], reg[-1], reg[:, 0], reg[:, -1]])
        bg = np.median(border, axis=0).astype(np.int16)
        bg_diff = np.abs(reg - bg).sum(axis=-1, dtype=np.int32)  # (H, W)
        fg = (bg_diff > 75).astype(np.int32)  # 배경과 뚜렷이 다른 픽셀
        total_fg = int(fg.sum())
        if total_fg < 0.02 * fg.size:
            return []  # 아이콘이 없는 빈 영역
        scored = []
        for ident in ids:
            color, mask = self._template(ident, scale / ds)
            th, tw = mask.shape
            n = int(mask.sum())
            if th > rh or tw > rw or n < 6:
                continue
            win = sliding_window_view(reg, (th, tw, 3))[:, :, 0]  # (Y, X, th, tw, 3)
            diff = np.abs(win[:, :, mask] - color[mask]).sum(axis=-1, dtype=np.int32)  # (Y, X, n)
            error = diff.sum(axis=-1) / (3.0 * n)
            bgwin = sliding_window_view(bg_diff, (th, tw))[:, :, mask].sum(axis=-1) / (3.0 * n)
            covered = sliding_window_view(fg, (th, tw))[:, :, mask].sum(axis=-1)
            # 상대 오차 + 영역의 아이콘 픽셀 중 원본 모양 밖에 남는 비율 (큰 아이콘 일부에만 맞는 경우 배제)
            rel = error / np.maximum(bgwin, 12.0) + COVERAGE_WEIGHT * (1.0 - covered / total_fg)
            y, x = np.unravel_index(int(np.argmin(rel)), rel.shape)
            center_x = (x + tw / 2) * ds
            center_y = (y + th / 2) * ds
            scored.append((float(rel[y, x]), ident, (int(center_x), int(center_y)), float(error[y, x])))
        scored.sort()
        if not scored:
            return []
        second = scored[1][0] if len(scored) > 1 else scored[0][0] + 1.0
        return [
            IconMatch(ident, e, (second - e) if index == 0 else (scored[0][0] - e), c, scale, raw)
            for index, (e, ident, c, raw) in enumerate(scored)
        ]

    def identify(
        self,
        region: Image.Image,
        scale: float,
        candidates: Optional[Iterable[str]] = None,
        spread: Sequence[float] = (0.94, 1.0, 1.06),
        shortlist: int = 6,
    ) -> Optional[IconMatch]:
        """두 단계 비교: 작게 줄여 후보를 추린 뒤, 추린 후보만 여러 배율로 자세히 비교한다.

        볼과 패시브 원본 크기가 달라서(50px / 25–27px) 패시브는 '같은 픽셀 배율'과
        '볼과 같은 크기로 맞춘 배율' 두 가지를 모두 시험한다(게임 화면으로 아직 확인하지 못함).
        """
        ids = list(candidates) if candidates is not None else list(self.sprites)
        coarse: Dict[str, float] = {}
        for variant in self._variants(ids, scale):
            vs, group = variant
            for m in self.match(region, vs, group, work_size=10):
                coarse[m.ident] = min(coarse.get(m.ident, 1e9), m.error)
        short = sorted(coarse, key=coarse.get)[:shortlist]
        best: Dict[str, IconMatch] = {}
        for k in spread:
            for vs, group in self._variants(short, scale * k):
                for m in self.match(region, vs, group, work_size=20):
                    if m.ident not in best or m.error < best[m.ident].error:
                        best[m.ident] = m
        if not best:
            return None
        ranked = sorted(best.values(), key=lambda m: m.error)
        top = ranked[0]
        second = ranked[1].error if len(ranked) > 1 else top.error + 1.0
        return IconMatch(top.ident, top.error, second - top.error, top.center, top.scale, top.raw_error)

    def _variants(self, ids: List[str], scale: float):
        small = [index for index in ids if index.startswith("passive:")]
        balls = [index for index in ids if not index.startswith("passive:")]
        result = []
        if balls:
            result.append((scale, balls))
        if small:
            result.append((scale, small))
            result.append((scale * 50 / 27, small))
        return result
