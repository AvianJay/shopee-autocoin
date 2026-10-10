"""Windows 內建 OCR（zh-Hant-TW），把截圖裡的文字和位置讀出來。

回傳的座標是 iOS point（截圖是 3 倍像素），可以直接拿去點。
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

SCALE = 3  # 截圖像素 / iOS point

_engine = OcrEngine.try_create_from_language(Language("zh-Hant-TW"))
if _engine is None:
    raise RuntimeError("找不到 zh-Hant-TW 的 OCR，請到 Windows 設定裝「中文(台灣)」語言的 OCR 元件")


@dataclass
class Word:
    text: str
    x: float  # 中心點，iOS point
    y: float
    w: float
    h: float


@dataclass
class Line:
    text: str
    words: list[Word]

    @property
    def x(self) -> float:
        return sum(w.x for w in self.words) / len(self.words)

    @property
    def y(self) -> float:
        return sum(w.y for w in self.words) / len(self.words)


def _bitmap(im: Image.Image) -> SoftwareBitmap:
    im = im.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(im.tobytes("raw", "BGRA"))
    return SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, im.width, im.height)


async def _recognize(bitmap: SoftwareBitmap):
    return await _engine.recognize_async(bitmap)


def white_text(im: Image.Image) -> Image.Image:
    """只留白字、其他塗黑。直播間的任務框是半透明深色底，背後的畫面一直在動，
    原圖直接辨識常常漏字；這樣處理後連拍 16 張都讀得到（金額是黃字，會被濾掉）。"""
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    lum, low = a.max(axis=2), a.min(axis=2)
    mask = (lum >= 185) & (lum - low < 90)
    return Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).convert("RGB")


def read(image: Image.Image | bytes, box: tuple[int, int, int, int] | None = None,
         upscale: float = 1.0, prep: Callable[[Image.Image], Image.Image] | None = None,
         ) -> list[Line]:
    """box 是 iOS point 的 (x1, y1, x2, y2)，只辨識那一塊（比較快也比較準）。
    prep 是辨識前的前處理（例如 white_text），在裁切後、放大前套用。"""
    im = Image.open(io.BytesIO(image)) if isinstance(image, bytes) else image
    ox = oy = 0
    if box:
        x1, y1, x2, y2 = (int(v * SCALE) for v in box)
        im = im.crop((x1, y1, x2, y2))
        ox, oy = x1, y1
    if prep:
        im = prep(im)
    if upscale != 1.0:
        im = im.resize((int(im.width * upscale), int(im.height * upscale)), Image.LANCZOS)
    result = asyncio.run(_recognize(_bitmap(im)))
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append(Word(
                text=w.text,
                x=(ox + (r.x + r.width / 2) / upscale) / SCALE,
                y=(oy + (r.y + r.height / 2) / upscale) / SCALE,
                w=r.width / upscale / SCALE,
                h=r.height / upscale / SCALE,
            ))
        # 中文的 OCR 結果每個字之間會有空格
        lines.append(Line(text=line.text.replace(" ", ""), words=words))
    return lines


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    path, *rest = sys.argv[1:]
    box = tuple(int(v) for v in rest[0].split(",")) if rest else None
    for ln in read(Image.open(path), box, upscale=float(rest[1]) if len(rest) > 1 else 1.0):
        print(f"({ln.x:5.0f},{ln.y:5.0f}) {ln.text}")
