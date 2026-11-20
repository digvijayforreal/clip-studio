"""From a link (or a video file) and time ranges to finished clips:
download each part, write down every word with its time, save the project, render it.

Each range becomes its own clip, or with join=True all ranges become one clip, played one after the other."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, download, media, render, transcribe
from . import project as projects
from .timestamps import TimestampError, clock, slug

LANGUAGE_NAMES = {"hi": "Hindi", "en": "English"}


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


def _meter(progress, label: str):
    """A function taking 0..1: printed in the terminal, or passed on as progress(label, fraction)."""
    if progress is None:
        return progress_printer("    ")
    return lambda fraction: progress(label, fraction)


@dataclass
class Part:
    """One range, ready to use: where it is in the file it lives in, and its words in that file's time."""
    start: float             # the wanted range, in the file's own seconds
    end: float
    words: list
    language: str
    file: Path | None = None # the downloaded piece (links only)
    offset: float = 0.0      # where that piece starts in the YouTube video
    meta: dict | None = None


def _old_parts(folder: Path) -> list[dict]:
    try:
        src = projects.load(folder)["source"]
    except projects.ProjectError:
        return []
    if src.get("parts"):
        return src["parts"]
    return [{"file": src.get("file"), "offset": src.get("offset", -1), "duration": src.get("duration", -1)}]


def _reusable(file: Path, old: list[dict], offset: float, length: float) -> bool:
    """This piece was downloaded before for the same range: no need to fetch it again."""
    if not file.is_file():
        return False
    return any(o.get("file") == file.name and abs(float(o.get("offset", -1)) - offset) < 0.01
               and abs(float(o.get("duration", -1)) - length) < 0.6 for o in old)


def _hear(audio: Path, language, model, accurate, log, progress=None) -> dict:
    log("  Writing down every word (speech to text)...")
    show = _meter(progress, "Speech to text")
    heard = transcribe.transcribe(audio, language, model, accurate=accurate,
                                  on_progress=lambda done, total: show(done / total if total else 1.0))
    show(1.0)
    return heard


def _link_part(info, start, end, folder: Path, file_name: str, old, pad, language, model, accurate, cookies,
               log, progress=None) -> Part:
    duration = float(info["duration"] or 0)
    part_start = max(0.0, start - pad)
    part_end = min(duration, end + pad) if duration else end + pad
    file = folder / file_name
    if _reusable(file, old, part_start, part_end - part_start):
        log("  Using the part downloaded before.")
    else:
        log("  Downloading this part...")
        if progress:
            progress("Downloading", 0.0)
        download.download_section(info["url"], part_start, part_end, file, cookies)
    meta = media.probe(file)
    audio = folder / (Path(file_name).stem + ".wav")
    media.extract_audio(file, audio)
    heard = _hear(audio, language, model, accurate, log, progress)
    return Part(start - part_start, min(end - part_start, meta["duration"]), heard["words"], heard["language"],
                file, part_start, meta)


def _file_part(local: Path, duration, start, end, folder: Path, wav_name: str, pad, language, model, accurate,
               log, progress=None) -> Part:
    heard_from = max(0.0, start - pad)
    heard_to = min(duration, end + pad) if duration else end + pad
    audio = folder / wav_name
    media.extract_audio(local, audio, heard_from, heard_to - heard_from)
    heard = _hear(audio, language, model, accurate, log, progress)
    words = [{**w, "s": round(w["s"] + heard_from, 3), "e": round(w["e"] + heard_from, 3)} for w in heard["words"]]
    return Part(start, end, words, heard["language"])


def _join_files(parts: list[Part], dest: Path) -> list[float]:
    """The downloaded pieces as one file, without re-encoding. Returns where each piece starts in it."""
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.file.name}'\n" for p in parts), encoding="utf-8")
    try:
        media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listing.name,
                   "-c", "copy", "-movflags", "+faststart", dest.name], cwd=dest.parent)
    finally:
        listing.unlink(missing_ok=True)
    starts, at = [], 0.0
    for p in parts:
        starts.append(at)
        at += p.meta["duration"]
    return starts


def _language(parts: list[Part]) -> str:
    """Hindi if any part is Hindi (Hinglish), else the first part's language."""
    return "hi" if any(p.language == "hi" for p in parts) else parts[0].language


def _finish(folder: Path, project: dict, began: float, log, progress=None) -> Path:
    projects.save(project, folder)
    inside = sum(1 for w in project["captions"]["words"]
                 if any(s["start"] <= w["s"] < s["end"] for s in project["segments"]))
    log(f"  Language: {LANGUAGE_NAMES.get(project['captions']['language'], project['captions']['language'])}, "
        f"{inside} words in the clip")
    log("  Rendering 9:16...")
    out = render.render(folder, project, on_progress=_meter(progress, "Rendering"), log=log)
    log(f"  Done in {time.time() - began:.0f}s: {out}")
    return out


def make(source: str, ranges, *, language: str | None = None, layout: str = "blur", style: str = "bold",
         model: str = config.DEFAULT_MODEL, pad: float = config.PAD_SECONDS, out_dir=config.PROJECTS_DIR,
         cookies_browser: str | None = None, accurate: bool = False, join: bool = False,
         settings: dict | None = None, log=print, progress=None) -> list[Path]:
    """Makes the clips. `settings` holds the project's edit, hook, effects and music choices.
    Returns the finished MP4 files."""
    media.require_tools()
    settings = settings or {}
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

    checked = []
    for start, end in ranges:
        if duration and start >= duration:
            raise TimestampError(f"{clock(start)} is past the end of the video ({clock(duration)}).")
        if duration and end > duration:
            log(f"  {clock(end)} is past the end, so that part stops at {clock(duration)}.")
            end = duration
        checked.append((start, end))
    if join and len(checked) < 2:
        join = False
    base = _safe_name(info["id"])
    common = dict(pad=pad, language=language, model=model, accurate=accurate, log=log, progress=progress)
    finished = []

    if join:
        name = f"{base}_join_{slug(checked[0][0])}_{len(checked)}parts"
        folder = Path(out_dir) / name
        folder.mkdir(parents=True, exist_ok=True)
        began = time.time()
        log(f"\nOne clip from {len(checked)} parts: " + ", ".join(f"{clock(a)}-{clock(b)}" for a, b in checked))
        parts = []
        old = _old_parts(folder)
        for n, (start, end) in enumerate(checked, 1):
            log(f"\nPart {n} of {len(checked)}: {clock(start)} to {clock(end)}")
            if is_link:
                parts.append(_link_part(info, start, end, folder, f"part_{n}.mp4", old, cookies=cookies_browser,
                                        **common))
            else:
                parts.append(_file_part(local, duration, start, end, folder, f"part_{n}.wav", **common))
        if is_link:
            log("\nJoining the parts...")
            starts = _join_files(parts, folder / "source.mp4")
            meta = media.probe(folder / "source.mp4")
            words, segments = [], []
            for part, at in zip(parts, starts):
                words += [{**w, "s": round(w["s"] + at, 3), "e": round(w["e"] + at, 3)} for w in part.words]
                segments.append((part.start + at, part.end + at))
            src = {"kind": "youtube", "url": info["url"], "id": info["id"], "title": info["title"],
                   "uploader": info["uploader"], "file": "source.mp4", "offset": 0.0,
                   "parts": [{"file": p.file.name, "offset": round(p.offset, 3), "duration": p.meta["duration"],
                              "at": round(at, 3)} for p, at in zip(parts, starts)],
                   "width": meta["width"], "height": meta["height"], "duration": meta["duration"]}
        else:
            words = [w for p in parts for w in p.words]
            segments = [(p.start, p.end) for p in parts]
            src = {"kind": "file", "url": "", "id": info["id"], "title": info["title"], "uploader": "",
                   "file": str(local), "offset": 0.0, "width": local_meta["width"], "height": local_meta["height"],
                   "duration": local_meta["duration"]}
        project = projects.new(name=name, source=src, segments=segments, words=words, language=_language(parts),
                               layout=layout, style=style, **settings)
        return [_finish(folder, project, began, log, progress)]

    for n, (start, end) in enumerate(checked, 1):
        name = f"{base}_{slug(start)}-{slug(end)}"
        folder = Path(out_dir) / name
        folder.mkdir(parents=True, exist_ok=True)
        log(f"\nClip {n} of {len(checked)}: {clock(start)} to {clock(end)}")
        began = time.time()
        if is_link:
            part = _link_part(info, start, end, folder, "source.mp4", _old_parts(folder), cookies=cookies_browser,
                              **common)
            src = {"kind": "youtube", "url": info["url"], "id": info["id"], "title": info["title"],
                   "uploader": info["uploader"], "file": "source.mp4", "offset": round(part.offset, 3),
                   "parts": [{"file": "source.mp4", "offset": round(part.offset, 3), "duration": part.meta["duration"],
                              "at": 0.0}],
                   "width": part.meta["width"], "height": part.meta["height"], "duration": part.meta["duration"]}
        else:
            part = _file_part(local, duration, start, end, folder, "audio.wav", **common)
            src = {"kind": "file", "url": "", "id": info["id"], "title": info["title"], "uploader": "",
                   "file": str(local), "offset": 0.0, "width": local_meta["width"], "height": local_meta["height"],
                   "duration": local_meta["duration"]}
        project = projects.new(name=name, source=src, segments=[(part.start, part.end)], words=part.words,
                               language=part.language, layout=layout, style=style, **settings)
        finished.append(_finish(folder, project, began, log, progress))
    return finished


def rerender(target, log=print, progress=None) -> Path:
    """Renders a project again, after its project.json was edited."""
    path = projects.file_path(target)
    project = projects.load(path)
    show = progress_printer("  Rendering ") if progress is None else (lambda f: progress("Rendering", f))
    return render.render(path.parent, project, on_progress=show, log=log)
