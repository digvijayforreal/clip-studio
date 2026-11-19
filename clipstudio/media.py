"""Small helpers around ffmpeg and ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    """ffmpeg or ffprobe failed, or a file is not a usable video."""


def require_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise MediaError(f"{' and '.join(missing)} not found. Install ffmpeg "
                         "(winget install Gyan.FFmpeg) and open a new terminal.")


def run(args, cwd=None) -> subprocess.CompletedProcess:
    proc = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise MediaError(f"{Path(str(args[0])).name} failed: {proc.stderr.strip()[-800:]}")
    return proc


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return abs(int(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return abs(int((stream.get("tags") or {}).get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def probe(path) -> dict:
    """Width and height as shown (phone videos are often stored sideways), duration, and whether there is sound."""
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if not video:
        raise MediaError(f"{Path(path).name} has no video in it.")
    width, height = int(video["width"]), int(video["height"])
    if _rotation(video) in (90, 270):
        width, height = height, width
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {"width": width, "height": height, "duration": round(duration, 3),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def extract_audio(src, dest, start: float | None = None, duration: float | None = None) -> Path:
    """16 kHz mono WAV for speech to text, of the whole file or only part of it."""
    args = ["ffmpeg", "-y", "-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(src), "-vn", "-map_metadata", "-1", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
             "-f", "wav", str(dest)]
    run(args)
    return Path(dest)
