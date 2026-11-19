"""Turning a project into a finished 9:16 MP4 with ffmpeg: framing, captions, sound and encoding."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import captions, config, media
from . import project as projects

W, H, FPS = config.OUT_W, config.OUT_H, config.OUT_FPS
_qsv_works: bool | None = None       # Intel Quick Sync: tried first, remembered once it fails


def geometry(src_w: int, src_h: int, layout: str) -> tuple[str, int, int]:
    """(layout used, height of the picture band, caption y) in the 1080x1920 frame."""
    if src_h >= src_w:
        layout = "crop"              # already upright or square: a blurred fill only wastes room
    if layout == "blur":
        band = min(H, int(round(W * src_h / src_w / 2)) * 2)
        top = (H - band) // 2
        return "blur", band, min(int(H * 0.75), top + band + 160)
    return "crop", H, int(H * 0.70)


def video_filter(layout: str, band: int, x: float = 0.5) -> str:
    """The ffmpeg filter that frames the source as 9:16 and labels the result [v0]."""
    if layout == "crop":
        return (f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(iw-ow)*{x:.3f}:(ih-oh)/2,setsar=1[v0]")
    return (f"[0:v]fps={FPS},split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
            f"scale={W}:{H},eq=brightness=-0.10:saturation=1.10[bg];"
            f"[fgsrc]scale={W}:{band}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0]")


def _encoders() -> list[tuple[str, list[str]]]:
    options = []
    if _qsv_works is not False:
        options.append(("qsv", ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "21", "-pix_fmt", "nv12"]))
    options.append(("x264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]))
    return options


def _run_ffmpeg(args: list[str], cwd: Path, duration: float, on_progress=None) -> str | None:
    """Runs ffmpeg, reporting progress from 0 to 1. Returns None when it worked, else the end of its log."""
    log_path = cwd / "ffmpeg.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=log, text=True,
                                encoding="utf-8", errors="replace", creationflags=media.NO_WINDOW)
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if on_progress and duration > 0 and key in ("out_time_us", "out_time_ms") and value.isdigit():
                on_progress(min(1.0, int(value) / 1e6 / duration))
        proc.wait()
    if proc.returncode == 0:
        return None
    return log_path.read_text(encoding="utf-8", errors="replace").strip()[-1500:] or f"exit code {proc.returncode}"


def render(project_dir, project: dict | None = None, on_progress=None) -> Path:
    """Renders the project in `project_dir`. Writes the clip, thumb.jpg and captions.ass there."""
    global _qsv_works
    project_dir = Path(project_dir)
    p = projects.validate(project) if project is not None else projects.load(project_dir)
    src = projects.source_path(p, project_dir)
    if not src.is_file():
        raise media.MediaError(f"The source video is missing: {src}")
    info = media.probe(src)
    segment = p["segments"][0]
    start = float(segment["start"])
    end = min(float(segment["end"]), info["duration"] or float(segment["end"]))
    if end - start < 0.5:
        raise media.MediaError("The part to render is shorter than half a second.")
    layout, band, caption_y = geometry(info["width"], info["height"], p["frame"]["layout"])
    cap = p["captions"]
    if cap.get("y") is not None:
        caption_y = int(H * float(cap["y"]))
    ass = captions.build_ass(cap["words"], start, end, cap["style"], caption_y) if cap.get("enabled", True) else ""

    # Everything happens in a short temporary folder with relative names: ffmpeg's caption filter can't take
    # Windows drive letters, and some folders are deeper than Windows allows for loading fonts.
    work = Path(tempfile.mkdtemp(prefix="cs"))
    try:
        graph = video_filter(layout, band, float(p["frame"].get("x", 0.5)))
        video_out = "[v0]"
        if ass:
            (work / "captions.ass").write_text(ass, encoding="utf-8")
            (work / "fonts").mkdir()
            for font in config.FONTS_DIR.glob("*.[ot]tf"):
                shutil.copy2(font, work / "fonts" / font.name)
            graph += ";[v0]ass=captions.ass:fontsdir=fonts[v]"
            video_out = "[v]"
        args = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
                "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
                "-filter_complex", graph, "-map", video_out]
        if info["has_audio"]:
            # -14 LUFS is the loudness Shorts, Reels and TikTok play at
            args += ["-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                     "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
        args += ["-movflags", "+faststart"]
        error = "no encoder was tried"
        for label, encoder in _encoders():
            error = _run_ffmpeg(args + encoder + ["clip.mp4"], work, end - start, on_progress)
            if error is None:
                if label == "qsv":
                    _qsv_works = True
                break
            if label == "qsv":
                _qsv_works = False
        if error is not None:
            raise media.MediaError(f"Rendering failed: {error}")
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, (end - start) / 2):.2f}", "-i", "clip.mp4",
                   "-frames:v", "1", "-q:v", "3", "thumb.jpg"], cwd=work)
        dest = project_dir / p["output"]["file"]
        shutil.move(work / "clip.mp4", dest)
        shutil.move(work / "thumb.jpg", project_dir / "thumb.jpg")
        if ass:
            (project_dir / "captions.ass").write_text(ass, encoding="utf-8")
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)
