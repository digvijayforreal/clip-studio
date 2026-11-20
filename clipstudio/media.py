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
    return {"width": width, "height": height, "duration": round(duration, 3), "fps": _fps(video),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def _fps(stream: dict) -> float:
    """Frames a second, from "30000/1001"-style rates. 30 when the file doesn't say."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = str(stream.get(key) or "").partition("/")
        try:
            value = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= value <= 240:
            return round(value, 3)
    return 30.0


def silences(path, start: float, end: float, min_length: float, noise_db: float = -35.0) -> list[tuple[float, float]]:
    """Stretches of [start, end] in the file quieter than noise_db for at least min_length seconds,
    in the file's own seconds. Music under the speech hides pauses, and then nothing is found."""
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                "-i", str(path), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_length:.3f}",
                "-f", "null", "-"])
    found, opened = [], None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            opened = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and opened is not None:
            closed = float(line.split("silence_end:")[1].split()[0])
            found.append((round(start + max(0.0, opened), 3), round(start + closed, 3)))
            opened = None
    if opened is not None:                      # quiet right up to the end
        found.append((round(start + max(0.0, opened), 3), round(end, 3)))
    return found


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
