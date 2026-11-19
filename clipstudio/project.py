"""A clip's project file: everything needed to render it again, in one JSON file.

Fix a caption word, move the cut, or change the style or layout in project.json, then run
`clipstudio render <folder>`. Times are seconds in the source file. Each caption word is one line:
{"w": text, "s": start, "e": end, "p": how sure the speech model was}.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import captions

VERSION = 1
LAYOUTS = ("blur", "crop")
FILE_NAME = "project.json"


class ProjectError(ValueError):
    """A project file that can't be used, with what to fix."""


def new(*, name: str, source: dict, segment: tuple[float, float], words: list[dict], language: str,
        layout: str = "blur", style: str = captions.DEFAULT_STYLE) -> dict:
    return {
        "version": VERSION,
        "name": name,
        "source": source,
        "segments": [{"start": round(segment[0], 3), "end": round(segment[1], 3)}],
        "frame": {"layout": layout, "x": 0.5},
        "captions": {"enabled": True, "style": style, "y": None, "language": language, "words": words},
        "output": {"file": "clip.mp4"},
    }


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectError(f"{what} has to be a number, not {value!r}.")
    return float(value)


def validate(p: dict) -> dict:
    if not isinstance(p, dict) or p.get("version") != VERSION:
        raise ProjectError(f"This isn't a version {VERSION} ClipStudio project.")
    for key in ("source", "segments", "frame", "captions", "output"):
        if not isinstance(p.get(key), (dict, list)):
            raise ProjectError(f'The project is missing "{key}".')
    if not isinstance(p["source"].get("file"), str) or not p["source"]["file"]:
        raise ProjectError('"source.file" has to name the video file.')
    segments = p["segments"]
    if not isinstance(segments, list) or len(segments) != 1 or not isinstance(segments[0], dict):
        raise ProjectError('"segments" has to hold exactly one part for now. Joining parts comes in a later version.')
    start = _number(segments[0].get("start"), "segments start")
    end = _number(segments[0].get("end"), "segments end")
    if start < 0 or end <= start:
        raise ProjectError("The segment's end has to be after its start, and the start can't be below 0.")
    duration = p["source"].get("duration")
    if isinstance(duration, (int, float)) and duration > 0 and end > duration + 0.05:
        raise ProjectError(f"The segment ends at {end:g}s, but the source video is only {duration:g}s long.")
    frame = p["frame"]
    if frame.get("layout") not in LAYOUTS:
        raise ProjectError(f'"frame.layout" has to be one of: {", ".join(LAYOUTS)}.')
    if not 0.0 <= _number(frame.get("x", 0.5), "frame x") <= 1.0:
        raise ProjectError('"frame.x" goes from 0 (left) to 1 (right).')
    cap = p["captions"]
    if cap.get("style") not in captions.STYLES:
        raise ProjectError(f'"captions.style" has to be one of: {", ".join(captions.STYLES)}.')
    if cap.get("y") is not None and not 0.05 <= _number(cap["y"], "captions y") <= 0.95:
        raise ProjectError('"captions.y" is a share of the height, from 0.05 (top) to 0.95 (bottom), or null.')
    words = cap.get("words")
    if not isinstance(words, list):
        raise ProjectError('"captions.words" has to be a list.')
    for i, w in enumerate(words):
        if not isinstance(w, dict) or not isinstance(w.get("w"), str):
            raise ProjectError(f"Caption word {i + 1} needs a text in \"w\".")
        s, e = _number(w.get("s"), f"word {i + 1} start"), _number(w.get("e"), f"word {i + 1} end")
        if e < s:
            raise ProjectError(f'Caption word {i + 1} ("{w["w"]}") ends before it starts.')
    out = p["output"].get("file")
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
