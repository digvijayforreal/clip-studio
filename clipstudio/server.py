"""The ClipStudio web app: a small server on this computer only, and the page that talks to it.

Long work (downloading, speech to text, rendering, AI fixes) runs as jobs, one at a time, on a worker thread.
The page asks for a job's progress every second.

Security: it listens on 127.0.0.1 only and has no sign-in, so it must never be opened to a network. Requests
whose Host or Origin isn't this computer are refused, so other websites open in the browser can't use it.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, ai, captions, config, editorassets, effects, fontmetrics, layouts, media, pipeline, render
from . import project as projects
from .timestamps import TimestampError, parse_ranges

WEB_DIR = Path(__file__).resolve().parent / "web"
UPLOADS_DIR = Path(os.environ.get("CLIPSTUDIO_UPLOADS") or config.APP_DIR / "uploads")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_NAME = re.compile(r"[A-Za-z0-9_-]{1,120}")
VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
MUSIC_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_VIDEO = 20 * 1024 ** 3
MAX_MUSIC = 300 * 1024 ** 2
LOGO_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_LOGO = 20 * 1024 ** 2

app = FastAPI(title="ClipStudio", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- only this computer

def _hostname(value: str) -> str:
    try:
        return (urlsplit(value if "//" in value else f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


@app.middleware("http")
async def only_this_computer(request: Request, call_next):
    if _hostname(request.headers.get("host", "")) not in LOCAL_HOSTS:
        return JSONResponse({"detail": "ClipStudio only answers on this computer."}, status_code=403)
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and _hostname(origin) not in LOCAL_HOSTS:
        return JSONResponse({"detail": "Requests from other websites are refused."}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- jobs

class Job:
    _ids = itertools.count(1)

    def __init__(self, kind: str, title: str, run, project: str | None = None):
        self.id = next(self._ids)
        self.kind, self.title, self.run, self.project = kind, title, run, project
        self.status = "queued"
        self.stage, self.progress = "Waiting", 0.0
        self.lines: list[str] = []
        self.result: list[str] = []
        self.error = ""
        self.created, self.finished = time.time(), None

    def log(self, *parts) -> None:
        text = " ".join(str(p) for p in parts).strip("\n")
        for line in text.splitlines() or [""]:
            if line.strip():
                self.lines.append(line.rstrip())
        del self.lines[:-400]

    def step(self, stage: str, fraction: float) -> None:
        self.stage, self.progress = stage, max(0.0, min(1.0, float(fraction)))

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "project": self.project,
                "status": self.status, "stage": self.stage, "progress": round(self.progress, 3),
                "log": self.lines[-60:], "result": self.result, "error": self.error,
                "created": self.created, "finished": self.finished}


JOBS: dict[int, Job] = {}
_queue: "queue.Queue[Job]" = queue.Queue()
_worker_started = threading.Event()


def _work() -> None:
    while True:
        job = _queue.get()
        job.status = "running"
        try:
            job.result = job.run(job) or []
            job.status = "done"
            job.step("Done", 1.0)
        except Exception as e:  # noqa: BLE001 - a failed job is shown on the page, never crashes the server
            job.status, job.error = "error", str(e) or e.__class__.__name__
            job.log(f"Error: {job.error}")
            if not isinstance(e, (media.MediaError, TimestampError, projects.ProjectError, ai.AIError,
                                  FileNotFoundError, effects.EmojiError)) and e.__class__.__name__ not in (
                    "DownloadError", "ModelError"):
                traceback.print_exc()
        finally:
            job.finished = time.time()


def submit(job: Job) -> Job:
    if not _worker_started.is_set():
        _worker_started.set()
        threading.Thread(target=_work, daemon=True, name="clipstudio-jobs").start()
    JOBS[job.id] = job
    for old in sorted(JOBS.values(), key=lambda j: j.id)[:-50]:          # keep the last 50
        if old.status in ("done", "error"):
            JOBS.pop(old.id, None)
    _queue.put(job)
    return job


def _busy(name: str) -> bool:
    return any(j.project == name and j.status in ("queued", "running") and j.kind != "prepare" for j in JOBS.values())


def _has_job(name: str, kind: str) -> bool:
    return any(j.project == name and j.kind == kind and j.status in ("queued", "running") for j in JOBS.values())


# ---------------------------------------------------------------- projects on disk

def _folder(name: str) -> Path:
    if not _NAME.fullmatch(name or ""):
        raise HTTPException(404, "No such project.")
    folder = config.PROJECTS_DIR / name
    if not (folder / projects.FILE_NAME).is_file():
        raise HTTPException(404, "No such project.")
    return folder


def _load(name: str) -> tuple[Path, dict]:
    folder = _folder(name)
    try:
        return folder, projects.load(folder)
    except projects.ProjectError as e:
        raise HTTPException(422, str(e)) from None


def _summary(folder: Path, p: dict) -> dict:
    clip = folder / p["output"]["file"]
    length = sum(s["end"] - s["start"] for s in p["segments"])
    stamp = (clip if clip.is_file() else folder / projects.FILE_NAME).stat().st_mtime
    return {"name": folder.name, "title": p["source"].get("title") or folder.name,
            "uploader": p["source"].get("uploader", ""), "parts": len(p["segments"]), "length": round(length, 1),
            "style": p["captions"]["style"], "language": p["captions"].get("language", ""),
            "hook": p["hook"].get("text", ""), "has_clip": clip.is_file(), "updated": stamp,
            "busy": _busy(folder.name)}


@app.get("/api/projects")
def list_projects():
    out = []
    if config.PROJECTS_DIR.is_dir():
        for folder in config.PROJECTS_DIR.iterdir():
            if folder.is_dir() and (folder / projects.FILE_NAME).is_file() and _NAME.fullmatch(folder.name):
                try:
                    out.append(_summary(folder, projects.load(folder)))
                except (projects.ProjectError, OSError):
                    out.append({"name": folder.name, "title": folder.name, "broken": True, "updated": 0})
    return sorted(out, key=lambda s: s.get("updated", 0), reverse=True)


@app.get("/api/projects/{name}")
def get_project(name: str):
    folder, p = _load(name)
    used_file = folder / "effects_used.json"
    try:
        used = json.loads(used_file.read_text(encoding="utf-8")) if used_file.is_file() else None
    except ValueError:
        used = None
    clip = folder / p["output"]["file"]
    src = projects.source_path(p, folder)
    fps, duration = 30.0, p["source"].get("duration") or 0
    if src.is_file():
        try:
            info = media.probe(src)
            fps, duration = info["fps"], info["duration"]
        except media.MediaError:
            pass
    music = projects.music_path(p, folder)
    return {"project": p, "used": used, "summary": _summary(folder, p),
            "clip_version": int(clip.stat().st_mtime) if clip.is_file() else None,
            "source_ok": src.is_file(), "music_ok": bool(music and music.is_file()),
            "fps": fps, "source_duration": duration, "editor": editorassets.status(folder, p),
            "preparing": _has_job(name, "prepare")}


@app.put("/api/projects/{name}")
def save_project(name: str, body: dict = Body(...)):
    folder, old = _load(name)
    new = dict(body)
    new["source"] = old["source"]               # where the video is can't be changed from the page
    new["output"] = old["output"]
    new["version"] = projects.VERSION
    music = (new.get("music") or {}).get("file")
    if music and music != old["music"].get("file") and not (folder / Path(music).name).is_file():
        raise HTTPException(422, "Upload music with the Music button.")
    if music and music != old["music"].get("file"):
        new["music"] = {**new["music"], "file": Path(music).name}
    logo = (new.get("logo") or {}).get("file")
    if logo and logo != (old.get("logo") or {}).get("file") and not (folder / Path(str(logo)).name).is_file():
        raise HTTPException(422, "Upload a logo with the Logo button.")
    try:
        projects.validate(new)
    except projects.ProjectError as e:
        raise HTTPException(422, str(e)) from None
    projects.save(new, folder)
    return get_project(name)


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    folder = _folder(name)
    if _busy(name):
        raise HTTPException(409, "This project is being worked on. Wait for the job to finish.")
    shutil.rmtree(folder)
    return {"deleted": name}


@app.post("/api/projects/{name}/open")
def open_folder(name: str):
    folder = _folder(name)
    if hasattr(os, "startfile"):
        os.startfile(folder)  # noqa: S606 - opens the project folder in Explorer, on this computer only
    return {"folder": str(folder)}


@app.get("/api/projects/{name}/media/{kind}")
def media_file(name: str, kind: str):
    folder, p = _load(name)
    music = projects.music_path(p, folder)
    paths = {
        "source": (projects.source_path(p, folder), "video/mp4"),
        "clip": (folder / p["output"]["file"], "video/mp4"),
        "thumb": (folder / "thumb.jpg", "image/jpeg"),
        "proxy": (folder / editorassets.PROXY, "video/mp4"),
        "filmstrip": (folder / editorassets.STRIP, "image/jpeg"),
        "waveform": (folder / editorassets.WAVE, "application/json"),
        "music": (music, "audio/mpeg") if music else (None, ""),
        "logo": (projects.logo_path(p, folder), "image/png") if p["logo"].get("file") else (None, ""),
    }
    if kind not in paths:
        raise HTTPException(404, "No such file.")
    path, kind_type = paths[kind]
    if path is None:
        raise HTTPException(404, "That file isn't there yet.")
    if kind == "logo":
        kind_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")
    if kind == "music":
        kind_type = {".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
                     ".flac": "audio/flac"}.get(path.suffix.lower(), "audio/mpeg")
    if not path.is_file():
        raise HTTPException(404, "That file isn't there yet.")
    return FileResponse(path, media_type=kind_type, headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{name}/render")
def render_project(name: str):
    folder, p = _load(name)
    if _busy(name):
        raise HTTPException(409, "This project is already being worked on.")

    def run(job: Job):
        job.log(f"Rendering {name}...")
        out = pipeline.rerender(folder, log=job.log, progress=job.step)
        job.log(f"Rendered: {out}")
        return [name]
    return submit(Job("render", f"Render {p['source'].get('title') or name}", run, name)).public()


@app.post("/api/projects/{name}/prepare")
def prepare_editor(name: str):
    """Makes the editing copy, thumbnails and sound wave, unless they're there already."""
    folder, p = _load(name)
    current = next((j for j in JOBS.values() if j.project == name and j.kind == "prepare"
                    and j.status in ("queued", "running")), None)
    if current:
        return current.public()

    def run(job: Job):
        editorassets.prepare(folder, projects.load(folder), log=job.log, progress=job.step)
        return [name]
    return submit(Job("prepare", f"Getting the editor ready: {p['source'].get('title') or name}", run, name)).public()


def _checked(name: str, body: dict) -> tuple[Path, dict]:
    """The project the page is working on (maybe not saved yet), checked, with the saved source and output."""
    folder, saved = _load(name)
    p = {**body, "source": saved["source"], "output": saved["output"], "version": projects.VERSION}
    try:
        return folder, projects.validate(p)
    except projects.ProjectError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/api/projects/{name}/pauses")
def find_pauses(name: str, body: dict = Body(...)):
    """The video track with its pauses cut out, for the editor to show (and undo). Nothing is saved."""
    folder, p = _checked(name, body.get("project") or {})
    min_gap = _number(body, "min_gap", p["edit"].get("min_gap", 0.7), 0.3, 5)
    src = projects.source_path(p, folder)
    try:
        info = media.probe(src)
    except media.MediaError as e:
        raise HTTPException(422, str(e)) from None
    out, before, after = [], 0.0, 0.0
    for seg in p["segments"]:
        a, b = float(seg["start"]), min(float(seg["end"]), info["duration"] or float(seg["end"]))
        before += b - a
        pieces, _ = render.pause_free([(a, b)], src, info["has_audio"], p["captions"]["words"], min_gap)
        for i, (x, y) in enumerate(pieces):
            out.append({**seg, "id": seg.get("id") if i == 0 else f"{seg.get('id') or 'c'}-{i}-{os.urandom(2).hex()}",
                        "start": round(x, 3), "end": round(y, 3)})
            after += y - x
    return {"segments": out, "removed": round(before - after, 2), "cuts": len(out) - len(p["segments"])}


@app.post("/api/projects/{name}/frame")
def frame_plan(name: str, body: dict = Body(...)):
    """Where the video, facecam and template graphics go, for the editor's preview. Nothing is saved."""
    folder, p = _checked(name, body)
    src = p["source"]
    fp = layouts.plan(p["frame"], p["template"], p["hook"].get("text", ""),
                      {"width": src.get("width"), "height": src.get("height"), "title": src.get("title", ""),
                       "uploader": src.get("uploader", "")})
    fp["credit_y"] = layouts.credit_y(fp)
    fp["ticker_speed"] = layouts.TICKER_SPEED
    return fp


@app.post("/api/projects/{name}/autofx")
def auto_effects(name: str, body: dict = Body(...)):
    """The zooms and emoji ClipStudio would choose for the project as it is now. Nothing is saved."""
    folder, p = _checked(name, body)
    p["edit"] = {**p["edit"], "cut_pauses": False}
    try:
        info = media.probe(projects.source_path(p, folder))
        timeline, clip_words, _, _ = render.resolve(p, folder, info, log=lambda *a: None)
    except media.MediaError as e:
        raise HTTPException(422, str(e)) from None
    return {"length": timeline.length, "zooms": effects.auto_zooms(clip_words, timeline.length),
            "emojis": effects.auto_emojis(clip_words, timeline.length)}


_EMOJI_CODE = re.compile(r"[0-9a-f]{2,6}(?:-[0-9a-f]{2,6}){0,4}")


@app.get("/api/emoji/{code}")
def emoji_picture(code: str):
    """An emoji picture by its code points ("1f525" for 🔥), downloaded once."""
    if not _EMOJI_CODE.fullmatch(code):
        raise HTTPException(404, "No such emoji.")
    emoji = "".join(chr(int(part, 16)) for part in code.split("-"))
    if emoji not in effects.EMOJI:
        raise HTTPException(404, "No such emoji.")
    try:
        path = effects.emoji_file(emoji)
    except effects.EmojiError as e:
        raise HTTPException(502, str(e)) from None
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


async def _receive(request: Request, dest: Path, limit: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "That file is too big.")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "The file was empty.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return size


def _upload_name(filename: str, allowed: set[str]) -> str:
    base = Path(filename or "").name
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    if ext not in allowed:
        raise HTTPException(415, f"Use one of these file types: {', '.join(sorted(allowed))}")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:60] or "file"
    return stem + ext


@app.put("/api/projects/{name}/music")
async def upload_music(name: str, request: Request, filename: str):
    folder, p = _load(name)
    file_name = "music_" + _upload_name(filename, MUSIC_TYPES)
    await _receive(request, folder / file_name, MAX_MUSIC)
    p["music"] = {**p["music"], "file": file_name}
    projects.save(p, folder)
    return get_project(name)


@app.put("/api/projects/{name}/logo")
async def upload_logo(name: str, request: Request, filename: str):
    """A logo or watermark picture for this project. Its settings (corner, size, see-through) stay as they are."""
    folder, p = _load(name)
    file_name = "logo_" + _upload_name(filename, LOGO_TYPES)
    await _receive(request, folder / file_name, MAX_LOGO)
    try:
        info = media.probe(folder / file_name)
    except media.MediaError:
        (folder / file_name).unlink(missing_ok=True)
        raise HTTPException(415, "That picture can't be read. Use a PNG or JPG.") from None
    p["logo"] = {**p["logo"], "file": file_name}
    projects.save(p, folder)
    return {**get_project(name), "logo_size": [info["width"], info["height"]]}


@app.delete("/api/projects/{name}/music")
def remove_music(name: str):
    folder, p = _load(name)
    p["music"] = {**p["music"], "file": None}
    projects.save(p, folder)
    return get_project(name)


@app.put("/api/upload")
async def upload_video(request: Request, filename: str):
    file_name = f"{time.strftime('%Y%m%d-%H%M%S')}_{_upload_name(filename, VIDEO_TYPES)}"
    size = await _receive(request, UPLOADS_DIR / file_name, MAX_VIDEO)
    return {"path": str(UPLOADS_DIR / file_name), "size": size}


# ---------------------------------------------------------------- making clips

def _flag(body: dict, key: str, default: bool) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise HTTPException(422, f'"{key}" has to be true or false.')
    return value


def _number(body: dict, key: str, default, low: float, high: float):
    value = body.get(key, default)
    if value in (None, ""):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f'"{key}" has to be a number.') from None
    return min(high, max(low, value))


@app.post("/api/make")
def make(body: dict = Body(...)):
    source = str(body.get("source") or "").strip()
    if not source:
        raise HTTPException(422, "Paste a YouTube link, or choose a video file.")
    if not re.match(r"^https?://", source, re.I):
        path = Path(source).expanduser()
        if not path.is_file() or path.suffix.lower() not in VIDEO_TYPES:
            raise HTTPException(422, "That isn't a link, or a video file on this computer.")
    try:
        ranges = parse_ranges([str(body.get("ranges") or "")])
    except TimestampError as e:
        raise HTTPException(422, str(e)) from None
    lang = body.get("lang", "auto")
    layout, style = body.get("layout", "blur"), body.get("style", captions.DEFAULT_STYLE)
    if lang not in ("auto", "hi", "en") or layout not in projects.LAYOUTS or style not in captions.STYLES:
        raise HTTPException(422, "Choose a language, layout and caption style from the lists.")
    hook_seconds = _number(body, "hook_seconds", None, 0.5, 600)
    settings = {
        "edit": {"cut_pauses": _flag(body, "cut_pauses", False), "min_gap": _number(body, "min_gap", 0.7, 0.3, 5)},
        "hook": {"text": str(body.get("hook") or "").strip()[:120], "seconds": hook_seconds},
        "effects": {"zooms": "auto" if _flag(body, "zoom", True) else "off",
                    "emojis": "auto" if _flag(body, "emoji", True) else "off",
                    "sfx": _flag(body, "sfx", True), "progress": _flag(body, "progress", True)},
    }
    join, accurate = _flag(body, "join", False), _flag(body, "accurate", False)
    ai_hook, ai_fix = _flag(body, "ai_hook", False), _flag(body, "ai_fix", False)
    yt_id = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{6,20})", source)
    what = f"YouTube {yt_id.group(1)}" if yt_id else Path(source).name if not source.lower().startswith("http") else "video"
    title = f"Heist: {what} · {len(ranges)} part{'s' if len(ranges) != 1 else ''}"

    def run(job: Job):
        made = pipeline.make(source, ranges, language=None if lang == "auto" else lang, layout=layout, style=style,
                             join=join, accurate=accurate, settings=settings, log=job.log, progress=job.step)
        names = [Path(clip).parent.name for clip in made]
        for folder in (Path(c).parent for c in made):
            try:                          # so the editor opens ready; it can also do this later by itself
                editorassets.prepare(folder, log=job.log, progress=job.step)
            except (media.MediaError, projects.ProjectError, OSError) as e:
                job.log(f"  The editor files weren't made ({e}). The editor will try again when you open it.")
        if (ai_hook or ai_fix) and ai.ready():
            for folder in (Path(c).parent for c in made):
                _ai_polish(folder, job, fix=ai_fix, hook=ai_hook and not settings["hook"]["text"])
        elif ai_hook or ai_fix:
            job.log("AI extras skipped: add an AI key in Settings first.")
        return names
    return submit(Job("make", title, run)).public()


def _clip_words(p: dict) -> list[dict]:
    return [w for w in p["captions"]["words"] if any(s["start"] - 0.05 <= w["s"] < s["end"] for s in p["segments"])]


def _ai_polish(folder: Path, job: Job, fix: bool, hook: bool) -> None:
    """AI caption fix and/or hook for a project, then a fresh render."""
    p = projects.load(folder)
    changed_any = False
    if fix:
        job.step("AI: fixing captions", 0.2)
        inside = _clip_words(p)
        keep = [w for w in p["captions"]["words"] if w not in inside]
        fixed, changed = ai.fix_words(inside, p["captions"].get("language", "en"), p["source"].get("title", ""),
                                      log=job.log)
        p["captions"]["words"] = sorted(keep + fixed, key=lambda w: w["s"])
        job.log(f"  AI fixed {changed} caption words.")
        changed_any = changed_any or changed > 0
    if hook:
        job.step("AI: writing a hook", 0.5)
        hooks = ai.suggest_hooks(_clip_words(p), p["source"].get("title", ""))
        p["hook"] = {**p["hook"], "text": hooks[0]}
        job.log(f"  AI hook: {hooks[0]}")
        changed_any = True
    if changed_any:
        projects.save(p, folder)
        pipeline.rerender(folder, log=job.log, progress=job.step)


@app.post("/api/projects/{name}/ai/fix")
def ai_fix(name: str):
    folder, p = _load(name)
    if not ai.ready():
        raise HTTPException(422, "Add an AI key in Settings first. Groq's free plan works.")
    if _busy(name):
        raise HTTPException(409, "This project is already being worked on.")

    def run(job: Job):
        _ai_polish(folder, job, fix=True, hook=False)
        return [name]
    return submit(Job("ai", f"AI caption fix: {p['source'].get('title') or name}", run, name)).public()


@app.post("/api/projects/{name}/ai/hook")
def ai_hook(name: str):
    folder, p = _load(name)
    try:
        return {"hooks": ai.suggest_hooks(_clip_words(p), p["source"].get("title", ""))}
    except ai.AIError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/jobs")
def list_jobs():
    return [j.public() for j in sorted(JOBS.values(), key=lambda j: j.id, reverse=True)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job.public()


# ---------------------------------------------------------------- settings and lists for the page

@app.get("/api/settings")
def get_settings():
    return {"ai": ai.public_settings(), "ai_ready": ai.ready()}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    a = body.get("ai") or {}
    try:
        ai.save_settings(a.get("base_url"), a.get("model"), a.get("key"))
    except ai.AIError as e:
        raise HTTPException(422, str(e)) from None
    return get_settings()


@app.post("/api/settings/test")
def test_ai():
    try:
        answer = ai.chat_json('Answer with JSON only: {"ok": true}', timeout=45)
    except ai.AIError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": isinstance(answer, dict) and answer.get("ok") is True, "answer": str(answer)[:120]}


@app.get("/api/meta")
def meta():
    return {
        "version": __version__,
        "styles": [{"id": k, "label": v["label"], "text": v["text"], "active": v["active"], "upper": v["upper"],
                    "box": bool(v.get("box")), "max_words": v["max_words"], "max_units": v["max_units"],
                    "size": v["size"]} for k, v in captions.STYLES.items()],
        "layouts": [{"id": k, "label": v, "cam": k in layouts.CAM_LAYOUTS} for k, v in layouts.LAYOUTS.items()],
        "templates": [{"id": k, "label": v} for k, v in layouts.TEMPLATES.items()],
        "face_presets": layouts.FACE_PRESETS,
        "em_scale": {name: fontmetrics.em_scale(name) for name in fontmetrics.FILES},
        "credit_size": layouts.CREDIT_SIZE,
        "emojis": list(effects.EMOJI),
        "fonts": list(projects.FONTS),
        "hook": {"size": captions.HOOK["size"], "max_units": captions.HOOK["max_units"], "max_lines": captions.HOOK["max_lines"]},
        "emoji_size": render.EMOJI_SIZE,
        "output": {"width": config.OUT_W, "height": config.OUT_H},
        "ai_ready": ai.ready(),
        "projects_dir": str(config.PROJECTS_DIR),
    }


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
if config.FONTS_DIR.is_dir():
    app.mount("/capfonts", StaticFiles(directory=config.FONTS_DIR), name="capfonts")     # the caption font, for the preview


def serve(port: int = 8766, open_browser: bool = True) -> None:
    import uvicorn
    media.require_tools()
    url = f"http://127.0.0.1:{port}"
    print(f"ClipStudio is running at {url}  (press Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
