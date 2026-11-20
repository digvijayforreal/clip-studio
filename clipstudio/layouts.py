"""Framing the 9:16 picture: where the video and the facecam go, and the graphics a template draws.

`plan()` describes one frame layout, and that one description is used three ways:
  - the render turns its regions into an ffmpeg filter graph (`video_graph`),
  - its shapes become drawings and text in the ASS file (`shapes_to_ass`),
  - the editor gets it as JSON and paints the same thing on a canvas for the live preview.
Boxes in the source are shares of its width and height (0 to 1). Everything else is pixels of the 1080x1920 frame.
"""
from __future__ import annotations

import struct
import unicodedata
import zlib
from pathlib import Path

import numpy as np

from . import config

W, H = config.OUT_W, config.OUT_H

LAYOUTS = {
    "blur": "Full picture + blurred fill",
    "crop": "Fill the screen",
    "stack": "Facecam on top, game below",
    "stack_bottom": "Game on top, facecam below",
    "corner": "Facecam in a corner",
    "circle": "Round facecam in a corner",
}
CAM_LAYOUTS = ("stack", "stack_bottom", "corner", "circle")
TEMPLATES = {"none": "None", "x_light": "X post (light)", "x_dark": "X post (dark)", "news": "Breaking news",
             "card": "Card"}
CORNERS = ("tl", "tr", "bl", "br")
FULL = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
FACE_PRESETS = {
    "tl": {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.3}, "tr": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3},
    "bl": {"x": 0.0, "y": 0.7, "w": 0.3, "h": 0.3}, "br": {"x": 0.7, "y": 0.7, "w": 0.3, "h": 0.3},
}
MARGIN = 40
BAR_SPACE = 70                 # room left under the progress bar
TICKER_SPEED = 160             # pixels a second


def frame_defaults() -> dict:
    return {"face": None, "game": dict(FULL), "split": 0.38, "cam_size": 0.42, "cam_corner": "tl"}


def template_defaults() -> dict:
    return {"id": "none", "name": "", "handle": "", "text": "", "verified": True, "label": "BREAKING NEWS",
            "headline": "", "ticker": "", "bg": "blur", "bg_color": "111111"}


def logo_defaults() -> dict:
    return {"file": None, "corner": "tr", "size": 0.16, "opacity": 0.85}


# ------------------------------------------------------------------ small helpers

def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def units(text: str) -> int:
    return sum(1 for ch in text if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))


def wrap(text: str, width: int, max_lines: int) -> list[str]:
    """Text in lines of at most `width` letters, by whole words. Line breaks typed by the person are kept."""
    lines: list[str] = []
    for para in str(text or "").replace("\r", "").split("\n"):
        cur = ""
        for word in para.split():
            if cur and units(cur) + 1 + units(word) > width:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        if cur:
            lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def cover(box: dict, sw: int, sh: int, dw: float, dh: float, ax: float = 0.5, ay: float = 0.5) -> list[float]:
    """The part of `box` (shares of the source) that fills dw x dh without stretching, as [x, y, w, h] shares.
    ax / ay: which side to keep when cropping (0 left/top, 0.5 middle, 1 right/bottom)."""
    bx, by, bw, bh = box["x"] * sw, box["y"] * sh, max(1.0, box["w"] * sw), max(1.0, box["h"] * sh)
    aspect = dw / dh
    if bw / bh > aspect:
        cw, ch = bh * aspect, bh
        cx, cy = bx + (bw - cw) * ax, by
    else:
        cw, ch = bw, bw / aspect
        cx, cy = bx, by + (bh - ch) * ay
    return [round(cx / sw, 5), round(cy / sh, 5), round(cw / sw, 5), round(ch / sh, 5)]


def _box(value, fallback: dict) -> dict:
    if not isinstance(value, dict):
        return dict(fallback)
    out = {k: float(value.get(k, fallback[k])) for k in ("x", "y", "w", "h")}
    out["w"], out["h"] = max(0.02, min(1.0, out["w"])), max(0.02, min(1.0, out["h"]))
    out["x"], out["y"] = max(0.0, min(1.0 - out["w"], out["x"])), max(0.0, min(1.0 - out["h"], out["y"]))
    return out


def region(box: dict, sw: int, sh: int, dx, dy, dw, dh, shape: str = "rect", radius: int = 0, border: int = 0,
           ax: float = 0.5) -> dict:
    dw, dh = even(dw), even(dh)
    return {"box": cover(box, sw, sh, dw, dh, ax), "dest": [int(dx), int(dy), dw, dh], "shape": shape,
            "radius": int(radius), "border": int(border)}


# ------------------------------------------------------------------ the plan

def plan(frame: dict, template: dict | None, hook_text: str, src: dict) -> dict:
    """Where everything goes for this frame setting. src: width, height, title, uploader of the source."""
    sw, sh = int(src.get("width") or 1920), int(src.get("height") or 1080)
    frame = {**frame_defaults(), **(frame or {})}
    tpl = {**template_defaults(), **(template or {})}
    layout = frame.get("layout", "blur")
    if layout not in LAYOUTS:
        layout = "blur"
    if sh >= sw and layout == "blur":
        layout = "crop"                    # already upright or square: a blurred fill only wastes room
    game = _box(frame.get("game"), FULL)
    corner = frame.get("cam_corner") if frame.get("cam_corner") in CORNERS else "tl"
    face = _box(frame.get("face"), FACE_PRESETS[corner])
    out = {"layout": layout, "template": tpl["id"] if tpl["id"] in TEMPLATES else "none", "background": None,
           "regions": [], "shapes": [], "cap_y": int(H * 0.7), "hook_y": 300, "hook": True, "classic": False}
    tid = out["template"]

    if tid in ("x_light", "x_dark"):
        _x_post(out, tpl, hook_text, game, sw, sh)
    elif tid == "card":
        _card(out, tpl, game, sw, sh)
    else:
        _layout(out, frame, layout, game, face, corner, sw, sh)
        if tid == "news":
            _news(out, tpl, hook_text, src)
    return out


def _layout(out, frame, layout, game, face, corner, sw, sh) -> None:
    if layout == "blur":
        band = min(H, even(W * sh / sw))
        top = (H - band) // 2
        out["background"] = {"t": "blur"}
        out["regions"].append(region(FULL, sw, sh, 0, top, W, band))
        out["cap_y"] = min(int(H * 0.75), top + band + 160)
        out["hook_y"] = max(200, top - 190)
        out["classic"] = True
        out["band"] = band
    elif layout == "crop":
        x = float(frame.get("x", 0.5))
        out["regions"].append(region(FULL, sw, sh, 0, 0, W, H, ax=x))
        out["classic"] = True
        out["band"] = H
    elif layout in ("stack", "stack_bottom"):
        cam_h = even(H * min(0.6, max(0.25, float(frame.get("split", 0.38)))))
        out["background"] = {"t": "color", "color": "000000"}
        if layout == "stack":
            out["regions"] += [region(face, sw, sh, 0, 0, W, cam_h), region(game, sw, sh, 0, cam_h, W, H - cam_h)]
            out["hook_y"] = cam_h + 150
            out["cap_y"] = int(H * 0.74)
        else:
            out["regions"] += [region(game, sw, sh, 0, 0, W, H - cam_h), region(face, sw, sh, 0, H - cam_h, W, cam_h)]
            out["cap_y"] = H - cam_h - 150
    else:                                  # corner, circle: the game fills the screen, the facecam sits on top
        size = even(W * min(0.6, max(0.2, float(frame.get("cam_size", 0.42)))))
        fw = size
        fh = size if layout == "circle" else min(even(H * 0.5), even(size * (face["h"] * sh) / max(1.0, face["w"] * sw)))
        border = 8 if layout == "circle" else 6
        x = MARGIN + border if corner in ("tl", "bl") else W - MARGIN - border - fw
        y = BAR_SPACE + border if corner in ("tl", "tr") else H - 320 - fh
        out["background"] = {"t": "color", "color": "000000"}
        out["regions"].append(region(game, sw, sh, 0, 0, W, H))
        out["regions"].append(region(face, sw, sh, x, y, fw, fh, shape="circle" if layout == "circle" else "rect",
                                     radius=fw // 2 if layout == "circle" else 0, border=border))
        if corner in ("tl", "tr"):
            out["hook_y"] = y + fh + 120
        else:
            out["cap_y"] = min(out["cap_y"], y - 120)


def _card(out, tpl, game, sw, sh) -> None:
    dw = 960
    dh = min(1300, even(dw * (game["h"] * sh) / max(1.0, game["w"] * sw)))
    dx, dy = (W - dw) // 2, (H - dh) // 2 - 40
    out["background"] = {"t": "color", "color": tpl.get("bg_color", "111111")} if tpl.get("bg") == "color" else {"t": "blur"}
    out["regions"].append(region(game, sw, sh, dx, dy, dw, dh, shape="round", radius=44))
    out["hook_y"] = max(200, dy - 140)
    out["cap_y"] = dy + dh + 140 if dy + dh + 140 < H - 160 else dy + dh - 140


def wrap_px(text: str, font: str, size: float, max_width: float, max_lines: int) -> list[str]:
    """Like wrap(), but by the real width of the words in the font."""
    from .fontmetrics import width
    lines: list[str] = []
    for para in str(text or "").replace("\r", "").split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and width(trial, font, size) > max_width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def fit_size(text: str, font: str, size: float, max_width: float, smallest: float) -> int:
    """The largest size up to `size` at which text fits in max_width (never below smallest)."""
    from .fontmetrics import width
    w = width(text, font, size)
    return int(size if w <= max_width else max(smallest, size * max_width / w))


def _x_post(out, tpl, hook_text, game, sw, sh) -> None:
    from .fontmetrics import width
    dark = tpl["id"] == "x_dark"
    ink, grey = ("FFFFFF", "71767B") if dark else ("0F1419", "536471")
    bold = "Poppins ExtraBold"
    name = (tpl.get("name") or "").strip() or "Your Name"
    handle = (tpl.get("handle") or "").strip().lstrip("@") or "yourhandle"
    text_size, line_h = 84, 92
    lines = wrap_px((tpl.get("text") or "").strip() or hook_text, bold, text_size, W - 2 * MARGIN, 6)
    dw = W - 2 * MARGIN
    dh = min(960, even(dw * (game["h"] * sh) / max(1.0, game["w"] * sw)))
    text_h = len(lines) * line_h
    block = 175 + (text_h + 36 if lines else 10) + dh
    top = max(150, (H - block) // 2 - 60)
    out["background"] = {"t": "color", "color": "000000" if dark else "FFFFFF"}
    shapes = out["shapes"]
    letter = next((ch for ch in name if ch.isalnum()), "C").upper()
    shapes.append({"t": "circle", "cx": MARGIN + 70, "cy": top + 70, "r": 70, "color": "FF2E88"})
    shapes.append({"t": "text", "x": MARGIN + 70, "y": top + 72, "anchor": "c", "size": 110, "font": bold,
                   "color": "FFFFFF", "text": letter})
    name_size = fit_size(name, bold, 80, W - 200 - MARGIN - 80, 44)
    shapes.append({"t": "text", "x": 200, "y": top + 14, "anchor": "lt", "size": name_size, "font": bold,
                   "color": ink, "text": name})
    if tpl.get("verified", True):
        bx = 200 + int(width(name, bold, name_size)) + 34
        shapes.append({"t": "circle", "cx": bx, "cy": top + 46, "r": 24, "color": "1D9BF0"})
        shapes.append({"t": "check", "cx": bx, "cy": top + 46, "size": 32, "color": "FFFFFF"})
    shapes.append({"t": "text", "x": 200, "y": top + 88, "anchor": "lt", "size": 64, "font": bold,
                   "color": grey, "text": f"@{handle}"})
    y = top + 175
    if lines:
        shapes.append({"t": "text", "x": MARGIN, "y": y, "anchor": "lt", "size": text_size, "font": bold,
                       "color": ink, "text": "\n".join(lines), "line": line_h})
        y += text_h + 36
    out["regions"].append(region(game, sw, sh, MARGIN, y, dw, dh, shape="round", radius=28))
    out["hook"] = False                    # the post text is the hook
    out["hook_y"] = top + 140
    bottom = y + dh
    out["cap_y"] = bottom + 130 if bottom + 130 < H - 140 else bottom - 140


def _news(out, tpl, hook_text, src) -> None:
    label = (tpl.get("label") or "BREAKING NEWS").strip().upper()[:28] or "BREAKING NEWS"
    headline = ((tpl.get("headline") or "").strip() or hook_text or src.get("title") or "").strip()
    ticker = ((tpl.get("ticker") or "").strip()
              or "  •  ".join(x for x in (src.get("title"), src.get("uploader")) if x) or "ClipStudio")
    from .fontmetrics import width
    shapes = out["shapes"]
    live_w = int(width("● LIVE", "Anton", 64)) + 36
    shapes.append({"t": "rect", "x": MARGIN, "y": BAR_SPACE, "w": live_w, "h": 66, "color": "D90429", "r": 6})
    shapes.append({"t": "text", "x": MARGIN + live_w // 2, "y": BAR_SPACE + 34, "anchor": "c", "size": 64,
                   "font": "Anton", "color": "FFFFFF", "text": "● LIVE"})
    label_size = fit_size(label, "Anton", 96, W - 2 * MARGIN - 60, 50)
    label_w = int(width(label, "Anton", label_size)) + 60
    shapes.append({"t": "rect", "x": MARGIN, "y": 1392, "w": label_w, "h": 100, "color": "D90429", "r": 0})
    shapes.append({"t": "text", "x": MARGIN + 30, "y": 1443, "anchor": "lc", "size": label_size, "font": "Anton",
                   "color": "FFFFFF", "text": label})
    if headline:
        size = fit_size(headline, "Poppins ExtraBold", 80, W - 2 * MARGIN - 60, 40)
        shapes.append({"t": "rect", "x": MARGIN, "y": 1492, "w": W - 2 * MARGIN, "h": 136, "color": "FFFFFF", "r": 0})
        shapes.append({"t": "text", "x": MARGIN + 30, "y": 1561, "anchor": "lc", "size": size, "font": "Poppins ExtraBold",
                       "color": "111111", "text": headline})
    shapes.append({"t": "rect", "x": 0, "y": 1700, "w": W, "h": 96, "color": "111111", "r": 0})
    shapes.append({"t": "ticker", "x0": 200, "x1": W, "y": 1748, "h": 96, "size": 68, "font": "Poppins ExtraBold",
                   "color": "FFFFFF", "text": ticker, "width": int(width(ticker, "Poppins ExtraBold", 68))})
    shapes.append({"t": "rect", "x": 0, "y": 1700, "w": 200, "h": 96, "color": "FFD23F", "r": 0})
    shapes.append({"t": "text", "x": 100, "y": 1749, "anchor": "c", "size": 84, "font": "Anton", "color": "111111",
                   "text": "NEWS"})
    out["cap_y"] = min(out["cap_y"], 1270)


def credit_y(fp: dict) -> int:
    return 1690 if fp["template"] == "news" else H - 60


# ------------------------------------------------------------------ ASS drawings for the shapes

def _ass_color(rgb: str) -> str:
    rgb = rgb.strip().lstrip("#").upper()
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"


def rounded_path(w: float, h: float, r: float) -> str:
    """An ASS drawing of a w x h rectangle with corners rounded by r."""
    r = max(0.0, min(r, w / 2, h / 2))
    w, h = round(w), round(h)
    if r < 1:
        return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    k = round(r * 0.4477)
    r = round(r)
    return (f"m {r} 0 l {w - r} 0 b {w - k} 0 {w} {k} {w} {r} l {w} {h - r} b {w} {h - k} {w - k} {h} {w - r} {h} "
            f"l {r} {h} b {k} {h} 0 {h - k} 0 {h - r} l 0 {r} b 0 {k} {k} 0 {r} 0")


CHECK = [(0.20, 0.52), (0.42, 0.74), (0.80, 0.32), (0.70, 0.22), (0.42, 0.54), (0.30, 0.42)]


def shapes_to_ass(shapes: list[dict], length: float) -> list[str]:
    """Dialogue lines that draw the template's shapes for the whole clip. They go under the captions."""
    from .captions import _UNSAFE, ass_time
    events = []
    span = f"{ass_time(0)},{ass_time(length)}"
    anchors = {"lt": 7, "lc": 4, "c": 5}
    for s in shapes:
        t = s["t"]
        if t in ("rect", "circle"):
            if t == "rect":
                x, y, path = s["x"], s["y"], rounded_path(s["w"], s["h"], s.get("r", 0))
            else:
                x, y, path = s["cx"] - s["r"], s["cy"] - s["r"], rounded_path(2 * s["r"], 2 * s["r"], s["r"])
            events.append(f"Dialogue: 0,{span},Bar,,0,0,0,,{{\\an7\\pos({int(x)},{int(y)})\\bord0\\shad0"
                          f"\\c{_ass_color(s['color'])}\\p1}}{path}{{\\p0}}")
        elif t == "check":
            n = s["size"]
            pts = [(round(s["cx"] - n / 2 + px * n), round(s["cy"] - n / 2 + py * n)) for px, py in CHECK]
            path = f"m {pts[0][0]} {pts[0][1]} " + " ".join(f"l {x} {y}" for x, y in pts[1:])
            events.append(f"Dialogue: 0,{span},Bar,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0"
                          f"\\c{_ass_color(s['color'])}\\p1}}{path}{{\\p0}}")
        elif t == "text":
            lines = [_UNSAFE.sub("", part).strip() for part in str(s["text"]).split("\n")]
            text = "\\N".join(line for line in lines if line)
            if not text:
                continue
            if s.get("line"):              # several lines: one event each, so the spacing is exactly `line`
                for k, part in enumerate(text.split("\\N")):
                    events.append(f"Dialogue: 0,{span},Text,,0,0,0,,{{\\an7\\pos({int(s['x'])},{int(s['y'] + k * s['line'])})"
                                  f"\\fn{s['font']}\\fs{int(s['size'])}\\bord0\\shad0\\c{_ass_color(s['color'])}}}{part}")
                continue
            events.append(f"Dialogue: 0,{span},Text,,0,0,0,,{{\\an{anchors[s['anchor']]}\\pos({int(s['x'])},{int(s['y'])})"
                          f"\\fn{s['font']}\\fs{int(s['size'])}\\bord0\\shad0\\c{_ass_color(s['color'])}}}{text}")
        elif t == "ticker":
            text = _UNSAFE.sub("", str(s["text"])).strip()
            if not text:
                continue
            travel = (s["x1"] - s["x0"]) + s["width"]
            period = max(1.0, travel / TICKER_SPEED)
            y, half = int(s["y"]), int(s["h"] // 2)
            clip = f"\\clip({int(s['x0'])},{y - half},{int(s['x1'])},{y + half})"
            k = 0
            while k * period < length and k < 200:
                a, b = k * period, min(length, (k + 1) * period)
                end_x = s["x1"] - (b - a) * TICKER_SPEED
                events.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Text,,0,0,0,,{{\\an4{clip}"
                              f"\\move({int(s['x1'])},{y},{int(end_x)},{y})\\fn{s['font']}\\fs{int(s['size'])}"
                              f"\\bord0\\shad0\\c{_ass_color(s['color'])}}}{text}")
                k += 1
    return events


def credit_event(text: str, y: int, length: float) -> str:
    from .captions import _UNSAFE, ass_time
    clean = _UNSAFE.sub("", text).strip()
    if not clean or length <= 0:
        return ""
    return (f"Dialogue: 4,{ass_time(0)},{ass_time(length)},Text,,0,0,0,,{{\\an2\\pos({W // 2},{y})\\fnPoppins ExtraBold"
            f"\\fs{CREDIT_SIZE}\\bord4\\shad0\\c&HFFFFFF&\\3c&H000000&\\alpha&H30&}}{clean}")


CREDIT_SIZE = 60


# ------------------------------------------------------------------ masks (pictures the filter graph needs)

def write_png(path: Path, pixels: np.ndarray) -> Path:
    """A PNG from an H x W (grey) or H x W x 4 (colour with transparency) array of uint8, without Pillow."""
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w = pixels.shape[:2]
    kind = 6 if pixels.ndim == 3 else 0
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, kind, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return Path(path)


def rounded_alpha(w: int, h: int, r: float) -> np.ndarray:
    """255 inside a w x h rectangle with rounded corners, 0 outside, with a soft one-pixel edge."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = np.clip(xs + 0.5, r, w - r)
    cy = np.clip(ys + 0.5, r, h - r)
    dist = np.hypot(xs + 0.5 - cx, ys + 0.5 - cy)
    return (np.clip(r - dist + 0.5, 0, 1) * 255).astype(np.uint8)


def masks(fp: dict) -> dict[str, tuple]:
    """The pictures the filter graph needs for this plan: name -> ("mask", w, h, radius) or ("ring", size)."""
    out = {}
    for i, r in enumerate(fp["regions"]):
        dw, dh = r["dest"][2], r["dest"][3]
        if r["shape"] in ("circle", "round"):
            out[f"mask{i}.png"] = ("mask", dw, dh, r["radius"])
        if r["shape"] == "circle" and r["border"]:
            out[f"ring{i}.png"] = ("ring", dw + 2 * r["border"])
    return out


def write_masks(fp: dict, folder: Path) -> list[str]:
    names = []
    for name, spec in masks(fp).items():
        if spec[0] == "mask":
            write_png(folder / name, rounded_alpha(spec[1], spec[2], spec[3]))
        else:
            size = spec[1]
            alpha = rounded_alpha(size, size, size / 2)
            write_png(folder / name, np.dstack([np.full_like(alpha, 255)] * 3 + [alpha]))
        names.append(name)
    return names


# ------------------------------------------------------------------ the ffmpeg filter graph

def px_box(box: list[float], sw: int, sh: int) -> tuple[int, int, int, int]:
    """Shares -> whole even pixels, inside the source."""
    cw = min(sw - sw % 2, even(box[2] * sw))
    ch = min(sh - sh % 2, even(box[3] * sh))
    cx = int(min(max(0, round(box[0] * sw)), sw - cw))
    cy = int(min(max(0, round(box[1] * sh)), sh - ch))
    return cw, ch, cx, cy


def video_graph(fp: dict, src: str, image, sw: int, sh: int, fps: int, length: float) -> str:
    """The filter that frames `src` as the plan says and labels the result [v0]. image(name) -> input label."""
    regs = fp["regions"]
    bg = fp.get("background")
    blur = bool(bg and bg["t"] == "blur")
    count = len(regs) + (1 if blur else 0)
    parts = [f"{src}fps={fps},split={count}" + "".join(f"[s{i}]" for i in range(count))]
    if blur:
        parts.append(f"[s{len(regs)}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
                     f"scale={W}:{H},eq=brightness=-0.10:saturation=1.10,setsar=1[bg]")
    else:
        color = (bg or {}).get("color", "000000")
        parts.append(f"color=c=0x{color}:s={W}x{H}:r={fps}:d={length:.3f},setsar=1[bg]")
    cur = "bg"
    for i, r in enumerate(regs):
        cw, ch, cx, cy = px_box(r["box"], sw, sh)
        dx, dy, dw, dh = r["dest"]
        chain = f"[s{i}]crop={cw}:{ch}:{cx}:{cy},scale={dw}:{dh},setsar=1"
        b = r["border"]
        if r["shape"] in ("circle", "round"):
            parts.append(chain + f",format=yuva420p[r{i}a]")
            parts.append(f"{image(f'mask{i}.png')}format=gray,scale={dw}:{dh}[m{i}]")
            parts.append(f"[r{i}a][m{i}]alphamerge[r{i}]")
            if r["shape"] == "circle" and b:
                parts.append(f"[{cur}]{image(f'ring{i}.png')}overlay={dx - b}:{dy - b}[c{i}r]")
                cur = f"c{i}r"
        elif b:
            parts.append(chain + f",pad={dw + 2 * b}:{dh + 2 * b}:{b}:{b}:color=white[r{i}]")
            dx, dy = dx - b, dy - b
        else:
            parts.append(chain + f"[r{i}]")
        parts.append(f"[{cur}][r{i}]overlay={dx}:{dy}[c{i}]")
        cur = f"c{i}"
    parts.append(f"[{cur}]format=yuv420p,setsar=1[v0]")
    return ";".join(parts)
