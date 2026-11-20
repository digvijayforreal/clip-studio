"""How wide text is in the caption fonts, read straight from the font files (no extra libraries).

libass, which draws the ASS file, sizes a font so that its ascender + descender (OS/2 winAscent + winDescent)
equals the font size it's given. `width()` returns pixels in that same sense, so templates can place things
right after a name, wrap lines and size the ticker exactly.
"""
from __future__ import annotations

import struct
from functools import lru_cache

from . import config

FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf"}


class Font:
    def __init__(self, data: bytes):
        self.data = data
        count = struct.unpack(">H", data[4:6])[0]
        self.tables = {}
        for i in range(count):
            tag, _check, offset, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            self.tables[tag.decode("latin-1")] = (offset, length)
        head = self._table("head")
        self.units = struct.unpack(">H", head[18:20])[0]
        hhea = self._table("hhea")
        self.metrics_count = struct.unpack(">H", hhea[34:36])[0]
        os2 = self._table("OS/2")
        win_ascent, win_descent = struct.unpack(">HH", os2[74:78])
        self.height = (win_ascent + win_descent) or self.units
        hmtx = self._table("hmtx")
        self.advances = [struct.unpack(">H", hmtx[4 * i: 4 * i + 2])[0] for i in range(self.metrics_count)]
        self.cmap = self._cmap()

    def _table(self, tag: str) -> bytes:
        offset, length = self.tables[tag]
        return self.data[offset: offset + length]

    def _cmap(self) -> dict[int, int]:
        cmap = self._table("cmap")
        count = struct.unpack(">H", cmap[2:4])[0]
        best = None
        for i in range(count):
            platform, encoding, offset = struct.unpack(">HHI", cmap[4 + 8 * i: 12 + 8 * i])
            if (platform, encoding) in ((3, 1), (0, 3), (0, 4), (3, 10)) and struct.unpack(">H", cmap[offset:offset + 2])[0] == 4:
                best = offset
                break
        out: dict[int, int] = {}
        if best is None:
            return out
        sub = cmap[best:]
        segs = struct.unpack(">H", sub[6:8])[0] // 2
        ends = struct.unpack(f">{segs}H", sub[14:14 + 2 * segs])
        starts = struct.unpack(f">{segs}H", sub[16 + 2 * segs:16 + 4 * segs])
        deltas = struct.unpack(f">{segs}h", sub[16 + 4 * segs:16 + 6 * segs])
        range_at = 16 + 6 * segs
        offsets = struct.unpack(f">{segs}H", sub[range_at:range_at + 2 * segs])
        for k in range(segs):
            for code in range(starts[k], ends[k] + 1):
                if code == 0xFFFF:
                    continue
                if offsets[k] == 0:
                    glyph = (code + deltas[k]) & 0xFFFF
                else:
                    at = range_at + 2 * k + offsets[k] + 2 * (code - starts[k])
                    glyph = struct.unpack(">H", sub[at:at + 2])[0]
                    if glyph:
                        glyph = (glyph + deltas[k]) & 0xFFFF
                if glyph:
                    out[code] = glyph
        return out

    def advance(self, ch: str) -> int:
        glyph = self.cmap.get(ord(ch), 0)
        return self.advances[min(glyph, self.metrics_count - 1)]


@lru_cache(maxsize=4)
def font(name: str) -> Font | None:
    path = config.FONTS_DIR / FILES.get(name, "")
    try:
        return Font(path.read_bytes())
    except (OSError, KeyError, struct.error):
        return None


def width(text: str, name: str, size: float) -> float:
    """Pixels `text` takes at ASS font size `size`. Falls back to an estimate when the font can't be read."""
    f = font(name)
    if f is None:
        return len(text) * size * 0.5
    return sum(f.advance(ch) for ch in text) * size / f.height


def em_scale(name: str) -> float:
    """The CSS font size (em) that matches ASS size 1: what the editor's preview multiplies sizes by."""
    f = font(name)
    return f.units / f.height if f else 0.72
