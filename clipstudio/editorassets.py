"""Files that make editing smooth, kept in the project folder:

  proxy.mp4       a small copy of the video (540p, a keyframe every half second) so the preview plays and seeks
                  quickly on a laptop. Only the stretch the clip uses (plus some room) is copied, so a long video
                  file doesn't turn into a long wait.
  filmstrip.jpg   one row of thumbnails for the timeline
  waveform.json   the loudness of the sound, 50 values a second, for the timeline
  editor.json     where the copy starts in the source, and the filmstrip's layout

The final render always uses the full-quality source, never these.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np

from . import media
from . import project as projects

PROXY, STRIP, WAVE, META = "proxy.mp4", "filmstrip.jpg", "waveform.json", "editor.json"
ROOM = 30.0              # seconds copied on each side of the clip, so a cut can be moved and still be seen
THUMB_H = 60
MAX_THUMBS = 400
WAVE_RATE = 50


def window(p: dict, duration: float) -> tuple[float, float]:
    """The stretch of the source the editor copies: every clip, plus some room."""
    starts = [float(s["start"]) for s in p["segments"]]
    ends = [float(s["end"]) for s in p["segments"]]
    a = max(0.0, min(starts) - ROOM)
    b = min(duration, max(ends) + ROOM) if duration else max(ends) + ROOM
    return round(a, 3), round(b, 3)


def status(folder: Path, p: dict) -> dict:
    """What the editor has to work with. ready is false until all the files exist and cover every clip."""
    folder = Path(folder)
    src = projects.source_path(p, folder)
    try:
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    files = [folder / name for name in (PROXY, STRIP, WAVE, META)]
    fresh = bool(meta) and src.is_file() and all(f.is_file() and f.stat().st_mtime >= src.stat().st_mtime for f in files)
    covers = fresh and all(float(meta["start"]) - 0.01 <= float(s["start"]) and float(s["end"]) <= float(meta["end"]) + 0.01
                           for s in p["segments"])
    return {"ready": bool(covers), "offset": meta["start"] if meta else 0.0, "end": meta["end"] if meta else None,
            "strip": meta.get("strip") if meta else None}


def _encoders() -> list[list[str]]:
    from . import render
    out = []
    if render._qsv_works is not False:
        out.append(["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "28", "-pix_fmt", "nv12"])
    out.append(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p"])
    return out


def make_proxy(src: Path, dest: Path, start: float, end: float, on_progress=None) -> Path:
    from . import render
    tmp = dest.with_name("proxy.part.mp4")
    scale = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
    base = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1", "-ss", f"{start:.3f}",
            "-t", f"{end - start:.3f}", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", scale,
            "-g", "15", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart"]
    error = "no encoder was tried"
    try:
        for encoder in _encoders():
            error = render._run_ffmpeg(base + encoder + [tmp.name], dest.parent, end - start, on_progress)
            if error is None:
                break
        if error is not None:
            raise media.MediaError(f"Couldn't make the editing copy: {error}")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
        (dest.parent / "ffmpeg.log").unlink(missing_ok=True)
    return dest


def make_filmstrip(video: Path, dest: Path, duration: float, width: int, height: int) -> dict:
    """One row of thumbnails, one every `interval` seconds of `video`."""
    interval = max(1.0, duration / MAX_THUMBS)
    count = int(math.ceil(duration / interval)) + 1
    tw = max(2, int(round(THUMB_H * width / max(1, height) / 2)) * 2)
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-an",
               "-vf", f"fps=1/{interval:.4f},scale={tw}:{THUMB_H},tile={count}x1",
               "-frames:v", "1", "-q:v", "6", str(dest)])
    return {"interval": round(interval, 4), "count": count, "w": tw, "h": THUMB_H}


def waveform(video: Path) -> list[float]:
    """Loudness peaks, WAVE_RATE a second, from 0 to 1. Empty when there's no sound."""
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "8000",
                           "-f", "s16le", "pipe:1"], capture_output=True, creationflags=media.NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        return []
    data = np.frombuffer(proc.stdout[: len(proc.stdout) // 2 * 2], dtype="<i2")
    step = 8000 // WAVE_RATE
    n = len(data) // step
    if not n:
        return []
    peaks = np.abs(data[: n * step].reshape(n, step).astype(np.float32)).max(axis=1)
    top = float(peaks.max()) or 1.0
    return [round(float(v), 3) for v in peaks / top]


def prepare(folder, p: dict | None = None, log=print, progress=None) -> dict:
    """Makes whatever is missing. Returns the status."""
    folder = Path(folder)
    p = p or projects.load(folder)
    current = status(folder, p)
    if current["ready"]:
        return current
    src = projects.source_path(p, folder)
    if not src.is_file():
        raise media.MediaError(f"The source video is missing: {src}")
    info = media.probe(src)
    start, end = window(p, info["duration"])
    step = (lambda f: progress("Preparing the editor", f)) if progress else None
    log("Making a small copy of the video for smooth editing...")
    make_proxy(src, folder / PROXY, start, end, (lambda f: step(0.8 * f)) if step else None)
    proxy_info = media.probe(folder / PROXY)
    log("Making the timeline thumbnails and sound wave...")
    strip = make_filmstrip(folder / PROXY, folder / STRIP, proxy_info["duration"], info["width"], info["height"])
    if step:
        step(0.9)
    peaks = waveform(folder / PROXY)
    (folder / WAVE).write_text(json.dumps({"rate": WAVE_RATE, "peaks": peaks}), encoding="utf-8")
    (folder / META).write_text(json.dumps({"start": start, "end": end, "strip": strip}), encoding="utf-8")
    if step:
        step(1.0)
    return status(folder, p)
