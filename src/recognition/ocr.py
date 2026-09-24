"""Windows 기본 OCR(Windows.Media.Ocr) 호출.

- 한국어 인식기가 설치되어 있는지 시작할 때 확인한다. 없으면 설치 명령을 안내만 하고 실행하지 않는다.
- 엔진은 신뢰도 수치를 주지 않는다. 그래서 이 앱도 'OCR 몇 %' 같은 수치를 만들지 않는다.
- 작업 스레드 안에서 전용 asyncio 루프로 기다린다. GUI 스레드에서는 호출하지 않는다.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image

from ..domain import OcrLine

INSTALL_HINT = ('관리자 PowerShell에서: Add-WindowsCapability -Online -Name "Language.OCR~~~ko-KR~0.0.1.0" '
                '(또는 설정 > 시간 및 언어 > 언어에서 한국어 기본 입력 기능 설치)')


@dataclass(frozen=True)
class OcrStatus:
    ok: bool
    message: str
    languages: Tuple[str, ...] = ()


def check_ocr(lang: str = "ko") -> OcrStatus:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        return OcrStatus(False, f"OCR 모듈을 불러오지 못했습니다: {e}. requirements.txt 의 패키지를 설치하세요.")
    try:
        langs = tuple(l.language_tag for l in OcrEngine.available_recognizer_languages)
        if not OcrEngine.is_language_supported(Language(lang)):
            return OcrStatus(False, f"Windows 한국어 OCR이 설치되어 있지 않습니다. {INSTALL_HINT}", langs)
        return OcrStatus(True, "Windows 한국어 OCR 사용 가능", langs)
    except Exception as e:  # WinRT 예외 형식이 일정하지 않다
        return OcrStatus(False, f"Windows OCR 초기화 실패: {e}")


Word = Tuple[str, float, float, float, float]  # text, x, y, w, h


def split_lines(lines: List[List[Word]], inv_scale: float = 1.0) -> List[OcrLine]:
    """OCR 한 줄 안에서 단어 간격이 글자 높이의 1.5배를 넘으면 나눈다.

    나란히 놓인 카드 제목('대출혈   무쇠   바람')이 한 줄로 합쳐져 이름 대조가 실패하는 것을 막는다.
    """
    out: List[OcrLine] = []
    for words in lines:
        seg: List[Word] = []
        for wd in sorted(words, key=lambda w: w[1]):
            if seg:
                prev = seg[-1]
                gap = wd[1] - (prev[1] + prev[3])
                if gap > 1.5 * max(prev[4], wd[4]):
                    out.append(_make_line(seg, inv_scale))
                    seg = []
            seg.append(wd)
        if seg:
            out.append(_make_line(seg, inv_scale))
    return out


def _make_line(seg: List[Word], inv: float) -> OcrLine:
    x0 = min(w[1] for w in seg)
    y0 = min(w[2] for w in seg)
    x1 = max(w[1] + w[3] for w in seg)
    y1 = max(w[2] + w[4] for w in seg)
    return OcrLine(text=" ".join(w[0] for w in seg), x=int(x0 * inv), y=int(y0 * inv),
                   w=int((x1 - x0) * inv), h=int((y1 - y0) * inv))


class OcrReader:
    """스레드마다 하나씩 만든다. 엔진과 이벤트 루프를 재사용한다."""

    def __init__(self, lang: str = "ko", target_height: int = 810):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        self._engine = OcrEngine.try_create_from_language(Language(lang))
        if self._engine is None:
            raise RuntimeError("OCR 엔진을 만들지 못했습니다")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.get_ident()
        self.target_height = target_height

    def read(self, img: Image.Image) -> List[OcrLine]:
        assert threading.get_ident() == self._thread, "OcrReader 는 만든 스레드에서만 사용"
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        scale = min(1.0, self.target_height / max(1, img.height))
        work = img if scale >= 0.999 else img.resize((round(img.width * scale), round(img.height * scale)),
                                                     Image.Resampling.BILINEAR)
        rgba = work.convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes())
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8,
                                                     rgba.width, rgba.height)
        result = self._loop.run_until_complete(self._recognize(bmp))
        words = [[(w.text, w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width, w.bounding_rect.height)
                  for w in line.words] for line in result.lines]
        return split_lines(words, 1.0 / scale)

    async def _recognize(self, bmp):
        return await self._engine.recognize_async(bmp)

    def close(self):
        self._loop.close()
