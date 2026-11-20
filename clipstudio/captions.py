"""Word-by-word captions written as an ASS subtitle file, which libass draws when the clip is rendered.

A line shows up to a few words. The word being spoken is coloured, and each new line pops in. Only colours
change while a line is up, never sizes, so the words never shift sideways. That keeps Hindi (Devanagari)
and English looking the same without measuring any text.
"""
from __future__ import annotations

import re
import unicodedata

from . import config

STYLES = {
    "bold": {"label": "Bold", "font": "Poppins ExtraBold", "size": 128, "text": "FFFFFF", "active": "FFE600",
             "outline": "000000", "border": 8, "shadow": 3, "upper": True, "max_words": 3, "max_units": 13},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "text": "FFFFFF", "active": "5CE1E6",
              "outline": "000000", "border": 6, "shadow": 2, "upper": False, "max_words": 4, "max_units": 17},
    # one word at a time, big, each popping in
    "pop": {"label": "Pop", "font": "Poppins ExtraBold", "size": 150, "text": "FFFFFF", "active": "FFFFFF",
            "outline": "000000", "border": 9, "shadow": 4, "upper": True, "max_words": 1, "max_units": 10},
    # the line on a dark box, the spoken word in green
    "boxed": {"label": "Boxed", "font": "Poppins ExtraBold", "size": 96, "text": "FFFFFF", "active": "4DFF88",
              "outline": "111111", "outline_alpha": "30", "border": 22, "shadow": 0, "box": True, "upper": False,
              "max_words": 4, "max_units": 17},
}
DEFAULT_STYLE = "bold"

HOOK = {"font": "Poppins ExtraBold", "size": 90, "text": "111111", "box": "FFFFFF", "pad": 26, "max_units": 17,
        "max_lines": 3}
BAR = {"height": 14, "color": "FFE600", "back": "000000"}

HOLD = 0.35        # seconds a line stays up after its last word, when the next line isn't straight after
GAP_BREAK = 0.5    # a pause at least this long starts a new line
BREAK_AFTER = (".", "?", "!", "।", "॥", ",", ";", ":")
# Small words that read badly at the end of a line ("REGULARLY ON A"): when a line is full, they move down.
WEAK_END = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "but", "with", "from", "by", "as", "into",
    "is", "are", "was", "my", "your", "our", "their", "his", "her", "its", "this", "i", "we", "so", "if",
    "और", "कि", "लेकिन", "या", "जो",
}
_WORD_KEY = re.compile(r"[^\w]+")
MIN_SIZE = 48
_POP = "\\fscx86\\fscy86\\t(0,110,\\fscx100\\fscy100)"
_STRIP_END = re.compile(r"[.,;:।॥]+$")
_UNSAFE = re.compile(r"[{}\\]|[\x00-\x1f\x7f]")


def _hex(rgb: str) -> tuple[str, str, str]:
    value = rgb.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        raise ValueError(f'"{rgb}" is not a colour. Write it like FFE600.')
    value = value.upper()
    return value[0:2], value[2:4], value[4:6]


def style_color(rgb: str, alpha: str = "00") -> str:
    """RGB "FFE600" -> "&H0000E6FF", as a Style line wants it (ASS stores alpha, blue, green, red)."""
    r, g, b = _hex(rgb)
    return f"&H{alpha}{b}{g}{r}"


def tag_color(rgb: str) -> str:
    """RGB "FFE600" -> "&H00E6FF&", for a \\c tag."""
    r, g, b = _hex(rgb)
    return f"&H{b}{g}{r}&"


def _centiseconds(t: float) -> int:
    return int(round(max(0.0, t) * 100))


def ass_time(t: float) -> str:
    """Seconds -> H:MM:SS.cc. Done in whole centiseconds, so one line ends exactly where the next starts."""
    h, rest = divmod(_centiseconds(t), 360_000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def units(text: str) -> int:
    """Roughly how many letters wide a word is. Hindi vowel signs drawn above or below a letter add no width."""
    return sum(1 for ch in text if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))


def display_text(word: str, upper: bool) -> str:
    """The word as shown: no characters that mean something to ASS, no trailing comma or full stop."""
    text = _UNSAFE.sub("", word).strip()
    text = _STRIP_END.sub("", text) or text
    return text.upper() if upper else text


def group_words(words: list[dict], max_words: int, max_units: int) -> list[list[dict]]:
    """Words split into caption lines: a few words each, breaking at punctuation and pauses."""
    def size(w: dict) -> int:
        return units(w["t"] if "t" in w else w["w"])

    groups: list[list[dict]] = []
    current: list[dict] = []
    width = 0
    for w in words:
        u = size(w)
        natural = current and (w["s"] - current[-1]["e"] > GAP_BREAK
                               or current[-1]["w"].rstrip().endswith(BREAK_AFTER))
        full = current and (len(current) >= max_words or width + u > max_units)
        if natural or full:
            carry = []
            if full and not natural and len(current) > 1 and _WORD_KEY.sub("", current[-1]["w"].lower()) in WEAK_END:
                carry = [current.pop()]                  # "ON A | 24-HOUR" -> "ON | A 24-HOUR"
            groups.append(current)
            current, width = carry, sum(size(x) for x in carry)
            if current and (len(current) >= max_words or width + u > max_units):
                groups.append(current)                   # the carried word and this one don't fit together
                current, width = [], 0
        current.append(w)
        width += u
    if current:
        groups.append(current)
    return groups


def header(style_name: str = DEFAULT_STYLE) -> str:
    st = STYLES.get(style_name) or STYLES[DEFAULT_STYLE]
    border_style = 3 if st.get("box") else 1       # 3: an opaque box in the outline colour behind the text
    outline = style_color(st["outline"], st.get("outline_alpha", "00"))
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {config.OUT_W}\n"
        f"PlayResY: {config.OUT_H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Cap,{st['font']},{st['size']},{style_color(st['text'])},{style_color(st['text'])},"
        f"{outline},{style_color('000000', '64')},0,0,0,0,100,100,0,0,{border_style},"
        f"{st['border']},{st['shadow']},5,80,80,0,1\n"
        f"Style: Text,Poppins ExtraBold,90,{style_color('FFFFFF')},{style_color('FFFFFF')},{style_color('000000')},"
        f"{style_color('000000', '64')},0,0,0,0,100,100,0,0,1,6,2,5,40,40,0,1\n"
        f"Style: TextBox,Poppins ExtraBold,90,{style_color('FFFFFF')},{style_color('FFFFFF')},{style_color('000000')},"
        f"{style_color('000000', 'FF')},0,0,0,0,100,100,0,0,3,18,0,5,40,40,0,1\n"
        f"Style: Hook,{HOOK['font']},{HOOK['size']},{style_color(HOOK['text'])},{style_color(HOOK['text'])},"
        f"{style_color(HOOK['box'])},{style_color('000000', '80')},0,0,0,0,100,100,0,0,3,{HOOK['pad']},0,5,60,60,0,1\n"
        f"Style: Bar,{HOOK['font']},20,{style_color(BAR['color'])},{style_color(BAR['color'])},"
        f"{style_color(BAR['back'])},{style_color('000000', 'FF')},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def hook_lines(text: str) -> list[str]:
    """The hook title split into a few short lines, by whole words."""
    clean = _UNSAFE.sub("", text).strip()
    lines: list[str] = []
    for word in clean.split():
        if lines and units(lines[-1]) + 1 + units(word) <= HOOK["max_units"]:
            lines[-1] += " " + word
        else:
            lines.append(word)
    if len(lines) > HOOK["max_lines"]:                  # too long: the rest goes on the last line, made smaller
        lines = lines[:HOOK["max_lines"] - 1] + [" ".join(lines[HOOK["max_lines"] - 1:])]
    return lines


def hook_event(text: str, length: float, y: int, seconds: float | None = None) -> str:
    """The title box near the top: pops in at the start, and stays up for `seconds` (or the whole clip)."""
    lines = hook_lines(text)
    if not lines:
        return ""
    end = length if not seconds else min(length, float(seconds))
    widest = max(units(line) for line in lines)
    size = HOOK["size"] if widest <= HOOK["max_units"] else max(MIN_SIZE, int(HOOK["size"] * HOOK["max_units"] / widest))
    tags = f"\\an5\\pos({config.OUT_W // 2},{y})" + _POP + (f"\\fs{size}" if size != HOOK["size"] else "")
    if end < length:
        tags += "\\fad(0,200)"
    return f"Dialogue: 3,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,{{{tags}}}" + "\\N".join(lines)


def progress_event(length: float) -> str:
    """A thin bar along the top that fills from left to right over the whole clip."""
    ms = int(round(length * 1000))
    shape = f"m 0 0 l {config.OUT_W} 0 l {config.OUT_W} {BAR['height']} l 0 {BAR['height']}"
    return (f"Dialogue: 4,{ass_time(0)},{ass_time(length)},Bar,,0,0,0,,"
            f"{{\\an7\\pos(0,0)\\bord0\\shad0\\fscx0\\t(0,{ms},\\fscx100)\\p1}}{shape}{{\\p0}}")


TEXT_FONTS = ("Poppins ExtraBold", "Anton")


def text_event(t: dict, length: float) -> str:
    """A free text on the clip. t: text, start, end (clip seconds), px, py (centre in pixels), size, font, color,
    box (a solid box behind it) and back (the box colour, or the outline colour without a box)."""
    a, b = max(0.0, float(t["start"])), min(length, float(t["end"]))
    if b - a < 0.02:
        return ""
    lines = [_UNSAFE.sub("", part).strip() for part in str(t.get("text", "")).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    font = t.get("font") if t.get("font") in TEXT_FONTS else TEXT_FONTS[0]
    style = "TextBox" if t.get("box") else "Text"
    tags = (f"\\an5\\pos({int(round(t['px']))},{int(round(t['py']))})\\fn{font}\\fs{int(t.get('size', 90))}"
            f"\\c{tag_color(t.get('color', 'FFFFFF'))}\\3c{tag_color(t.get('back', '000000'))}" + _POP)
    return f"Dialogue: 2,{ass_time(a)},{ass_time(b)},{style},,0,0,0,,{{{tags}}}" + "\\N".join(lines)


def build_ass(words: list[dict], start: float, end: float, style_name: str = DEFAULT_STYLE,
              y: int | None = None, hook: str = "", hook_y: int = 300, hook_seconds: float | None = None,
              progress: bool = False, x: int | None = None, scale: float = 1.0, texts=None, under=None,
              extra=None) -> str:
    """The ASS file for seconds [start, end] of the source. Times in the file count from `start`.

    Captions always (centred on x, y, sized by scale); a hook title near the top, free texts and a progress bar
    when asked for. Texts are already in clip seconds and pixels."""
    st = STYLES.get(style_name) or STYLES[DEFAULT_STYLE]
    y = int(config.OUT_H * 0.70) if y is None else int(y)
    x = config.OUT_W // 2 if x is None else int(x)
    base = max(MIN_SIZE, int(round(st["size"] * float(scale or 1.0))))
    length = end - start
    shown = []
    for w in sorted((w for w in words if start - 0.05 <= w["s"] < end), key=lambda w: w["s"]):
        text = display_text(w["w"], st["upper"])
        if text:
            s = max(w["s"], start) - start
            shown.append({"w": w["w"], "t": text, "s": s, "e": max(s, min(w["e"], end) - start)})
    groups = group_words(shown, st["max_words"], st["max_units"])
    events = []
    for gi, group in enumerate(groups):
        next_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else length
        line_end = min(group[-1]["e"] + HOLD, next_start, length)
        width = sum(units(w["t"]) for w in group)
        size = max(MIN_SIZE, int(base * st["max_units"] / width)) if width > st["max_units"] else base
        first = True
        for i, w in enumerate(group):
            a = group[0]["s"] if i == 0 else w["s"]
            b = group[i + 1]["s"] if i + 1 < len(group) else line_end
            if _centiseconds(b) <= _centiseconds(a):
                continue                     # shorter than a hundredth of a second: never visible
            words_on_line = " ".join(
                f"{{\\c{tag_color(st['active'] if j == i else st['text'])}}}{x['t']}" for j, x in enumerate(group))
            tags = f"\\an5\\pos({x},{y})"
            if size != st["size"]:
                tags += f"\\fs{size}"
            if first:
                tags += _POP
                first = False
            events.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Cap,,0,0,0,,{{{tags}}}{words_on_line}")
    if hook and hook.strip():
        events.append(hook_event(hook, length, hook_y, hook_seconds))
    for t in texts or []:
        events.append(text_event(t, length))
    events = list(under or []) + events + list(extra or [])     # template graphics first: they go under everything
    if progress and length > 0:
        events.append(progress_event(length))
    return header(style_name) + "".join(e + "\n" for e in events if e)
