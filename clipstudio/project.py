"""A clip's project file: everything needed to render it again, in one JSON file.

The editor saves this file as you work; `clipstudio render <folder>` renders it from the command line.

  segments        the video track: clips of the source, played in list order. [{"id", "start", "end", "volume"}],
                  start/end in source seconds. Clips may repeat or come in any order.
  edit.cut_pauses true removes pauses longer than edit.min_gap seconds when rendering (the editor turns this
                  into real cuts instead, so you can see them)
  captions.words  one word per line: {"w": text, "s": start, "e": end, "p": how sure the speech model was},
                  in source seconds. captions.x / y place the caption line (shares of the frame, y null: automatic),
                  captions.scale makes it bigger or smaller.
  hook            a title near the top: {"text", "seconds": how long it stays up (null: the whole clip), "y"}
  texts           extra text on the clip: [{"id", "text", "start", "end", "x", "y", "size", "font", "color",
                  "box", "back"}], times in clip seconds, x/y shares of the frame, size in pixels of the 1080-wide frame
  effects.zooms   "auto", "off", or punch-ins [{"start", "end"}] in clip seconds
  effects.emojis  "auto", "off", or [{"t", "emoji", "seconds", "x", "y", "size"}] in clip seconds
  effects.sfx     a whoosh on each punch-in and a pop on each emoji
  effects.progress  a progress bar along the top
  music           {"file": a music file or null, "volume": 0 to 1}; it's turned down under speech
"""
from __future__ import annotations

import json
from pathlib import Path

from . import captions, effects, layouts

VERSION = 3
LAYOUTS = tuple(layouts.LAYOUTS)
FONTS = ("Poppins ExtraBold", "Anton")
FILE_NAME = "project.json"
AUTO_OR_OFF = ("auto", "off")
MAX_SEGMENTS = 400
MAX_TEXTS = 200


class ProjectError(ValueError):
    """A project file that can't be used, with what to fix."""


def defaults() -> dict:
    return {
        "edit": {"cut_pauses": False, "min_gap": 0.7},
        "hook": {"text": "", "seconds": None, "y": None},
        "texts": [],
        "effects": {"zooms": "auto", "zoom_scale": effects.ZOOM_SCALE, "emojis": "auto", "sfx": True,
                    "progress": True},
        "music": {"file": None, "volume": 0.15},
        "template": layouts.template_defaults(),
        "logo": layouts.logo_defaults(),
        "credit": {"text": ""},
    }


def _fill(p: dict) -> dict:
    """Settings added after a project was made get their defaults (the project looks the same as before)."""
    for key in ("template", "logo", "credit"):
        base = defaults()[key]
        p[key] = {**base, **p[key]} if isinstance(p.get(key), dict) else base
    if isinstance(p.get("frame"), dict):
        p["frame"] = {**layouts.frame_defaults(), **p["frame"]}
    return p


def new(*, name: str, source: dict, segments, words: list[dict], language: str,
        layout: str = "blur", style: str = captions.DEFAULT_STYLE, **settings) -> dict:
    """A new project. `settings` may hold edit, hook, texts, effects and music, merged over the defaults."""
    p = {
        "version": VERSION,
        "name": name,
        "source": source,
        "segments": [{"id": f"c{i}", "start": round(a, 3), "end": round(b, 3), "volume": 1.0}
                     for i, (a, b) in enumerate(segments, 1)],
        "frame": {"layout": layout, "x": 0.5, **layouts.frame_defaults()},
        "captions": {"enabled": True, "style": style, "x": 0.5, "y": None, "scale": 1.0, "language": language,
                     "words": words},
    }
    for key, value in defaults().items():
        given = settings.get(key)
        p[key] = list(given or value) if isinstance(value, list) else {**value, **(given or {})}
    p["output"] = {"file": "clip.mp4"}
    return p


def upgrade(p: dict) -> dict:
    """Older projects as the current version, looking the same as before."""
    if not isinstance(p, dict):
        return p
    if p.get("version") == 1:                 # made before effects existed
        p = {**p, "version": 2}
        base = defaults()
        p.setdefault("edit", base["edit"])
        p.setdefault("hook", {"text": "", "seconds": None})
        p.setdefault("effects", {**base["effects"], "zooms": "off", "emojis": "off", "sfx": False, "progress": False})
        p.setdefault("music", base["music"])
    if p.get("version") == 2:                 # made before the timeline editor
        p = {**p, "version": 3}
        if isinstance(p.get("segments"), list):
            p["segments"] = [{**s, "id": s.get("id") or f"c{i}", "volume": s.get("volume", 1.0)}
                             if isinstance(s, dict) else s for i, s in enumerate(p["segments"], 1)]
        if isinstance(p.get("captions"), dict):
            p["captions"] = {"x": 0.5, "scale": 1.0, **p["captions"]}
        if isinstance(p.get("hook"), dict):
            p["hook"] = {"y": None, **p["hook"]}
        p.setdefault("texts", [])
    if p.get("version") == VERSION:
        p = _fill(p)
    return p


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectError(f"{what} has to be a number, not {value!r}.")
    return float(value)


def _between(value, low: float, high: float, what: str, hint: str = "") -> float:
    v = _number(value, what)
    if not low <= v <= high:
        raise ProjectError(f'"{what}" goes from {low:g} to {high:g}{hint}.')
    return v


def _color(value, what: str) -> None:
    try:
        captions._hex(str(value))
    except ValueError:
        raise ProjectError(f'"{what}" has to be a colour like FFE600.') from None


def _section(p: dict, key: str) -> dict:
    value = p.get(key)
    if not isinstance(value, dict):
        raise ProjectError(f'The project is missing "{key}".')
    return value


def _validate_segments(p: dict) -> None:
    segments = p.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ProjectError('"segments" has to list at least one part: [{"start": ..., "end": ...}].')
    if len(segments) > MAX_SEGMENTS:
        raise ProjectError(f"A clip can have at most {MAX_SEGMENTS} parts.")
    duration = p["source"].get("duration")
    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, dict):
            raise ProjectError(f"Segment {i} has to look like {{\"start\": 1.5, \"end\": 9.0}}.")
        start, end = _number(seg.get("start"), f"segment {i} start"), _number(seg.get("end"), f"segment {i} end")
        if start < 0 or end <= start:
            raise ProjectError(f"Segment {i}: the end has to be after the start, and the start can't be below 0.")
        if isinstance(duration, (int, float)) and duration > 0 and end > duration + 0.05:
            raise ProjectError(f"Segment {i} ends at {end:g}s, but the source video is only {duration:g}s long.")
        _between(seg.get("volume", 1.0), 0, 2, f"segment {i} volume")
        if seg.get("id") is not None and (not isinstance(seg["id"], str) or len(seg["id"]) > 40):
            raise ProjectError(f"Segment {i}: \"id\" has to be a short text.")


def _validate_effects(p: dict) -> None:
    fx = _section(p, "effects")
    for key, fields in (("zooms", ("start", "end")), ("emojis", ("t",))):
        value = fx.get(key)
        if isinstance(value, str):
            if value not in AUTO_OR_OFF:
                raise ProjectError(f'"effects.{key}" has to be "auto", "off" or a list.')
            continue
        if not isinstance(value, list):
            raise ProjectError(f'"effects.{key}" has to be "auto", "off" or a list.')
        for i, entry in enumerate(value, 1):
            if not isinstance(entry, dict):
                raise ProjectError(f"effects.{key} item {i} has to be an object.")
            for field in fields:
                _number(entry.get(field), f"effects.{key} item {i} {field}")
            if key == "zooms" and entry["end"] <= entry["start"]:
                raise ProjectError(f"effects.zooms item {i} ends before it starts.")
            if key == "emojis":
                if entry.get("emoji") not in effects.EMOJI:
                    raise ProjectError(f"effects.emojis item {i}: {entry.get('emoji')!r} isn't one of the emoji "
                                       f"ClipStudio has pictures for: {' '.join(effects.EMOJI)}")
                for field, low, high in (("x", 0, 1), ("y", 0, 1), ("size", 40, 800), ("seconds", 0.1, 600)):
                    if entry.get(field) is not None:
                        _between(entry[field], low, high, f"effects.emojis item {i} {field}")
    _between(fx.get("zoom_scale", effects.ZOOM_SCALE), 1.02, 1.6, "effects.zoom_scale", " (1.15 is a 15% punch-in)")
    for flag in ("sfx", "progress"):
        if not isinstance(fx.get(flag), bool):
            raise ProjectError(f'"effects.{flag}" has to be true or false.')


def _validate_texts(p: dict) -> None:
    texts = p.get("texts", [])
    if not isinstance(texts, list):
        raise ProjectError('"texts" has to be a list.')
    if len(texts) > MAX_TEXTS:
        raise ProjectError(f"A clip can have at most {MAX_TEXTS} texts.")
    for i, t in enumerate(texts, 1):
        if not isinstance(t, dict) or not isinstance(t.get("text"), str) or len(t["text"]) > 300:
            raise ProjectError(f"Text {i} needs a \"text\" of at most 300 characters.")
        start, end = _number(t.get("start"), f"text {i} start"), _number(t.get("end"), f"text {i} end")
        if start < 0 or end <= start:
            raise ProjectError(f"Text {i}: the end has to be after the start.")
        _between(t.get("x"), 0, 1, f"text {i} x")
        _between(t.get("y"), 0, 1, f"text {i} y")
        _between(t.get("size"), 20, 400, f"text {i} size")
        if t.get("font") not in FONTS:
            raise ProjectError(f'Text {i}: "font" has to be one of: {", ".join(FONTS)}.')
        _color(t.get("color"), f"text {i} color")
        _color(t.get("back"), f"text {i} back")
        if not isinstance(t.get("box"), bool):
            raise ProjectError(f'Text {i}: "box" has to be true or false.')


def _validate_box(box, what: str) -> None:
    if not isinstance(box, dict):
        raise ProjectError(f'"{what}" has to look like {{"x": 0, "y": 0, "w": 0.3, "h": 0.3}}.')
    for k in ("x", "y"):
        _between(box.get(k), 0, 1, f"{what}.{k}")
    for k in ("w", "h"):
        _between(box.get(k), 0.02, 1, f"{what}.{k}")
    if box["x"] + box["w"] > 1.001 or box["y"] + box["h"] > 1.001:
        raise ProjectError(f'"{what}" has to stay inside the picture.')


def _validate_frame(frame: dict) -> None:
    if frame.get("face") is not None:
        _validate_box(frame["face"], "frame.face")
    _validate_box(frame.get("game"), "frame.game")
    _between(frame.get("split"), 0.25, 0.6, "frame.split")
    _between(frame.get("cam_size"), 0.2, 0.6, "frame.cam_size")
    if frame.get("cam_corner") not in layouts.CORNERS:
        raise ProjectError(f'"frame.cam_corner" has to be one of: {", ".join(layouts.CORNERS)}.')


def _validate_style_extras(p: dict) -> None:
    tpl = _section(p, "template")
    if tpl.get("id") not in layouts.TEMPLATES:
        raise ProjectError(f'"template.id" has to be one of: {", ".join(layouts.TEMPLATES)}.')
    for key, limit in (("name", 60), ("handle", 40), ("text", 400), ("label", 30), ("headline", 160), ("ticker", 300)):
        if not isinstance(tpl.get(key), str) or len(tpl[key]) > limit:
            raise ProjectError(f'"template.{key}" has to be text of at most {limit} characters.')
    if not isinstance(tpl.get("verified"), bool):
        raise ProjectError('"template.verified" has to be true or false.')
    if tpl.get("bg") not in ("blur", "color"):
        raise ProjectError('"template.bg" has to be "blur" or "color".')
    _color(tpl.get("bg_color"), "template.bg_color")
    logo = _section(p, "logo")
    if logo.get("file") is not None and (not isinstance(logo["file"], str) or any(c in logo["file"] for c in "\\/:")):
        raise ProjectError('"logo.file" has to be the name of a picture in the project folder, or null.')
    if logo.get("corner") not in layouts.CORNERS:
        raise ProjectError(f'"logo.corner" has to be one of: {", ".join(layouts.CORNERS)}.')
    _between(logo.get("size"), 0.04, 0.5, "logo.size")
    _between(logo.get("opacity"), 0.1, 1, "logo.opacity")
    credit = _section(p, "credit")
    if not isinstance(credit.get("text"), str) or len(credit["text"]) > 80:
        raise ProjectError('"credit.text" has to be text of at most 80 characters.')


def validate(p: dict) -> dict:
    p = upgrade(p)
    if not isinstance(p, dict) or p.get("version") != VERSION:
        raise ProjectError(f"This isn't a ClipStudio project (version {VERSION}).")
    source = _section(p, "source")
    if not isinstance(source.get("file"), str) or not source["file"]:
        raise ProjectError('"source.file" has to name the video file.')
    _validate_segments(p)
    frame = _section(p, "frame")
    if frame.get("layout") not in LAYOUTS:
        raise ProjectError(f'"frame.layout" has to be one of: {", ".join(LAYOUTS)}.')
    _between(frame.get("x", 0.5), 0, 1, "frame.x", " (0 is the left edge, 1 the right)")
    _validate_frame(frame)
    _validate_style_extras(p)
    cap = _section(p, "captions")
    if cap.get("style") not in captions.STYLES:
        raise ProjectError(f'"captions.style" has to be one of: {", ".join(captions.STYLES)}.')
    _between(cap.get("x", 0.5), 0, 1, "captions.x")
    if cap.get("y") is not None:
        _between(cap["y"], 0.03, 0.97, "captions.y", ", or null for automatic")
    _between(cap.get("scale", 1.0), 0.4, 2.5, "captions.scale")
    words = cap.get("words")
    if not isinstance(words, list):
        raise ProjectError('"captions.words" has to be a list.')
    for i, w in enumerate(words):
        if not isinstance(w, dict) or not isinstance(w.get("w"), str):
            raise ProjectError(f"Caption word {i + 1} needs a text in \"w\".")
        s, e = _number(w.get("s"), f"word {i + 1} start"), _number(w.get("e"), f"word {i + 1} end")
        if e < s:
            raise ProjectError(f'Caption word {i + 1} ("{w["w"]}") ends before it starts.')
    edit = _section(p, "edit")
    if not isinstance(edit.get("cut_pauses"), bool):
        raise ProjectError('"edit.cut_pauses" has to be true or false.')
    _between(edit.get("min_gap", 0.7), 0.3, 5, "edit.min_gap", " seconds")
    hook = _section(p, "hook")
    if not isinstance(hook.get("text", ""), str):
        raise ProjectError('"hook.text" has to be text.')
    if hook.get("seconds") is not None and _number(hook["seconds"], "hook.seconds") <= 0:
        raise ProjectError('"hook.seconds" has to be above 0, or null to keep the title up the whole time.')
    if hook.get("y") is not None:
        _between(hook["y"], 0.03, 0.97, "hook.y", ", or null for automatic")
    _validate_texts(p)
    _validate_effects(p)
    music = _section(p, "music")
    if music.get("file") is not None and not isinstance(music["file"], str):
        raise ProjectError('"music.file" has to be the path of a music file, or null.')
    _between(music.get("volume", 0.15), 0, 1, "music.volume")
    out = _section(p, "output").get("file")
    if not isinstance(out, str) or not out.lower().endswith(".mp4") or any(c in out for c in '\\/:'):
        raise ProjectError('"output.file" has to be a plain file name ending in .mp4.')
    return p


def dumps(p: dict) -> str:
    """Readable JSON with one caption word per line, so words are easy to fix by hand."""
    words = p["captions"]["words"]
    shell = {**p, "captions": {**p["captions"], "words": "\0WORDS\0"}}
    text = json.dumps(shell, ensure_ascii=False, indent=2)
    block = ("[\n" + ",\n".join("      " + json.dumps(w, ensure_ascii=False) for w in words) + "\n    ]"
             if words else "[]")
    return text.replace(json.dumps("\0WORDS\0"), block) + "\n"


def file_path(target) -> Path:
    """A project folder or a .json path -> the project file. A folder needn't exist yet."""
    target = Path(target)
    return target if target.suffix.lower() == ".json" else target / FILE_NAME


def save(p: dict, target) -> Path:
    """Writes the project into a folder (as project.json) or to a .json path. Returns the file written."""
    path = file_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(p), encoding="utf-8")
    tmp.replace(path)
    return path


def load(target) -> dict:
    path = file_path(target)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectError(f"There's no project file at {path}.") from None
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path.name} isn't valid JSON (line {e.lineno}, column {e.colno}): {e.msg}.") from None
    return validate(data)


def source_path(p: dict, project_dir) -> Path:
    """The source video: a file inside the project folder, or anywhere on the PC for local videos."""
    file = Path(p["source"]["file"])
    return file if file.is_absolute() else Path(project_dir) / file


def logo_path(p: dict, project_dir) -> Path | None:
    file = (p.get("logo") or {}).get("file")
    return Path(project_dir) / file if file else None


def music_path(p: dict, project_dir) -> Path | None:
    file = p["music"].get("file")
    if not file:
        return None
    path = Path(file).expanduser()
    return path if path.is_absolute() else Path(project_dir) / path
