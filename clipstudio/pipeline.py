"""From a link (or a video file) and time ranges to finished clips:
download the part, write down every word with its time, save the project, render it."""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import config, download, media, render, transcribe
from . import project as projects
from .timestamps import TimestampError, clock, slug


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:40] or "video"


def progress_printer(prefix: str):
    """Prints "prefix 40%" each time another tenth is done."""
    last = [-1]

    def show(fraction: float) -> None:
        tenth = min(10, int(fraction * 10 + 1e-9))
        if tenth != last[0]:
            last[0] = tenth
            print(f"\r{prefix}{tenth * 10}%", end="\n" if tenth >= 10 else "", flush=True)
    return show


def _reusable_download(project_dir: Path, offset: float, length: float) -> bool:
    """The part was downloaded before for this same range: no need to fetch it again."""
    source = project_dir / "source.mp4"
    if not source.is_file():
        return False
    try:
        old = projects.load(project_dir)
    except projects.ProjectError:
        return False
    src = old["source"]
    return abs(float(src.get("offset", -1)) - offset) < 0.01 and abs(float(src.get("duration", -1)) - length) < 0.6


def make(source: str, ranges, *, language: str | None = None, layout: str = "blur", style: str = "bold",
         model: str = config.DEFAULT_MODEL, pad: float = config.PAD_SECONDS, out_dir=config.PROJECTS_DIR,
         cookies_browser: str | None = None, log=print) -> list[Path]:
    """Makes one clip per range. Returns the finished MP4 files."""
    media.require_tools()
    is_link = bool(re.match(r"^https?://", source.strip(), re.IGNORECASE))
    if is_link:
        log("Reading the link...")
        info = download.video_info(source.strip(), cookies_browser)
        log(f'  "{info["title"]}" by {info["uploader"] or "unknown"}, {clock(info["duration"])} long')
    else:
        local = Path(source).expanduser()
        if not local.is_file():
            raise FileNotFoundError(f"There's no video file at {local}.")
        local = local.resolve()
        local_meta = media.probe(local)
        info = {"id": local.stem, "title": local.stem, "uploader": "", "duration": local_meta["duration"], "url": ""}
    duration = float(info["duration"] or 0)

    finished = []
    for n, (start, end) in enumerate(ranges, 1):
        if duration and start >= duration:
            raise TimestampError(f"{clock(start)} is past the end of the video ({clock(duration)}).")
        if duration and end > duration:
            log(f"  {clock(end)} is past the end, so this part stops at {clock(duration)}.")
            end = duration
        name = f"{_safe_name(info['id'])}_{slug(start)}-{slug(end)}"
        folder = Path(out_dir) / name
        folder.mkdir(parents=True, exist_ok=True)
        log(f"\nClip {n} of {len(ranges)}: {clock(start)} to {clock(end)}")
        began = time.time()

        if is_link:
            part_start = max(0.0, start - pad)
            part_end = min(duration, end + pad) if duration else end + pad
            source_file = folder / "source.mp4"
            if _reusable_download(folder, part_start, part_end - part_start):
                log("  Using the part downloaded before.")
            else:
                log("  Downloading this part...")
                download.download_section(info["url"], part_start, part_end, source_file, cookies_browser)
            meta = media.probe(source_file)
            segment = (start - part_start, min(end - part_start, meta["duration"]))
            media.extract_audio(source_file, folder / "audio.wav")
            shift = 0.0
            source = {"kind": "youtube", "url": info["url"], "id": info["id"], "title": info["title"],
                      "uploader": info["uploader"], "file": "source.mp4", "offset": round(part_start, 3),
                      "width": meta["width"], "height": meta["height"], "duration": meta["duration"]}
        else:
            meta = local_meta
            heard_from = max(0.0, start - pad)
            heard_to = min(duration, end + pad) if duration else end + pad
            media.extract_audio(local, folder / "audio.wav", heard_from, heard_to - heard_from)
            segment = (start, end)
            shift = heard_from        # the sound file starts here in the video
            source = {"kind": "file", "url": "", "id": info["id"], "title": info["title"], "uploader": "",
                      "file": str(local), "offset": 0.0, "width": meta["width"], "height": meta["height"],
                      "duration": meta["duration"]}

        log("  Writing down every word (speech to text)...")
        show = progress_printer("    ")
        heard = transcribe.transcribe(folder / "audio.wav", language, model,
                                      on_progress=lambda done, total: show(done / total if total else 1.0))
        show(1.0)
        words = [{**w, "s": round(w["s"] + shift, 3), "e": round(w["e"] + shift, 3)} for w in heard["words"]]
        inside = sum(1 for w in words if segment[0] <= w["s"] < segment[1])
        log(f"  Language: {'Hindi' if heard['language'] == 'hi' else 'English' if heard['language'] == 'en' else heard['language']}, "
            f"{inside} words in the clip")

        project = projects.new(name=name, source=source, segment=segment, words=words,
                               language=heard["language"], layout=layout, style=style)
        projects.save(project, folder)
        log("  Rendering 9:16...")
        out = render.render(folder, project, on_progress=progress_printer("    "))
        log(f"  Done in {time.time() - began:.0f}s: {out}")
        finished.append(out)
    return finished


def rerender(target) -> Path:
    """Renders a project again, after its project.json was edited."""
    path = projects.file_path(target)
    project = projects.load(path)
    return render.render(path.parent, project, on_progress=progress_printer("  Rendering "))
