"""Turning a project into a finished 9:16 MP4 with ffmpeg.

One ffmpeg run does everything: plays the chosen pieces of the source one after the other, frames them as
9:16, punches in where a zoom is, lays the emoji pictures over, burns in captions, the hook title and the
progress bar, and mixes speech, sound effects and music before evening out the loudness.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import captions, config, effects, layouts, media
from . import project as projects
from .timeline import Timeline, cut_pauses, cut_silences

W, H, FPS = config.OUT_W, config.OUT_H, config.OUT_FPS
EMOJI_SIZE = 190
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


def hook_y(layout: str, band: int) -> int:
    """Where the hook title goes: above the picture in the blur layout, near the top when it fills the screen."""
    if layout == "blur":
        return max(200, (H - band) // 2 - 190)
    return 300


def emoji_center_y(caption_y: int) -> float:
    """Where an emoji sits when it hasn't been placed: just above the captions."""
    return max(40, caption_y - 120 - EMOJI_SIZE) + EMOJI_SIZE / 2


def video_filter(layout: str, band: int, x: float = 0.5, src: str = "[0:v]") -> str:
    """The ffmpeg filter that frames `src` as 9:16 and labels the result [v0]."""
    if layout == "crop":
        return (f"{src}fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(iw-ow)*{x:.3f}:(ih-oh)/2,setsar=1[v0]")
    return (f"{src}fps={FPS},split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
            f"scale={W}:{H},eq=brightness=-0.10:saturation=1.10[bg];"
            f"[fgsrc]scale={W}:{band}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0]")


def _even(value: float) -> int:
    return int(round(value / 2)) * 2


def _enable(spans) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)


@dataclass
class Plan:
    """Everything one render needs, worked out before ffmpeg runs."""
    src: Path
    pieces: list                      # timeline pieces: .start, .length in source seconds
    length: float
    layout: str
    band: int
    x: float = 0.5
    fps: float = FPS                  # the source's frame rate
    has_audio: bool = True
    zooms: list = field(default_factory=list)          # [{"start", "end"}] clip seconds
    zoom_scale: float = effects.ZOOM_SCALE
    emojis: list = field(default_factory=list)         # [{"t", "seconds", "file", "size", "px", "py"}] clip seconds
    captions_file: str | None = None                   # an .ass file in the work folder
    sfx_file: str | None = None
    music_file: Path | None = None
    music_volume: float = 0.15
    frame: dict | None = None                          # a layouts.plan(); None or classic: blur / crop as before
    src_size: tuple = (1920, 1080)
    images: list = field(default_factory=list)         # pictures in the work folder the frame uses (masks)
    logo: dict | None = None                           # {"file", "w", "corner", "opacity"}


def build_command(plan: Plan) -> tuple[list[str], list[str]]:
    """(ffmpeg arguments up to the encoder, output mapping) for a plan. No files are touched."""
    args = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    graph: list[str] = []
    n = len(plan.pieces)
    for piece in plan.pieces:
        args += ["-ss", f"{piece.start:.3f}", "-t", f"{piece.length:.3f}", "-i", str(plan.src)]
    next_input = n
    sfx_input = music_input = None
    if plan.sfx_file:
        args += ["-i", plan.sfx_file]
        sfx_input, next_input = next_input, next_input + 1
    if plan.music_file:
        args += ["-stream_loop", "-1", "-i", str(plan.music_file)]
        music_input, next_input = next_input, next_input + 1
    emoji_inputs = []
    for e in plan.emojis:
        args += ["-framerate", str(FPS), "-loop", "1", "-t", f"{plan.length:.3f}", "-i", str(e["file"])]
        emoji_inputs.append(next_input)
        next_input += 1
    image_inputs = {}
    for name in plan.images + ([str(plan.logo["file"])] if plan.logo else []):
        args += ["-framerate", str(FPS), "-loop", "1", "-t", f"{plan.length:.3f}", "-i", name]
        image_inputs[name] = f"[{next_input}:v]"
        next_input += 1

    # the pieces, one after the other; a very short fade on each sound cut so it doesn't click
    for i, piece in enumerate(plan.pieces):
        # trimmed again here, half a frame short of the end: that keeps exactly the piece's frames, where a
        # seek or a rounded time could hand over one more, and over many cuts that adds up
        graph.append(f"[{i}:v]setpts=PTS-STARTPTS,trim=duration={piece.length - 0.5 / plan.fps:.4f}[pv{i}]")
        if plan.has_audio:
            fade = min(0.015, piece.length / 4)
            volume = f"volume={piece.volume:.3f}," if abs(piece.volume - 1.0) > 1e-3 else ""
            graph.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,"
                         f"atrim=duration={piece.length:.4f},{volume}"
                         f"afade=t=in:d={fade:.3f},afade=t=out:st={piece.length - fade:.3f}:d={fade:.3f}[pa{i}]")
    if n > 1:
        joined = "".join(f"[pv{i}]" + (f"[pa{i}]" if plan.has_audio else "") for i in range(n))
        graph.append(f"{joined}concat=n={n}:v=1:a={1 if plan.has_audio else 0}[cv]" + ("[ca]" if plan.has_audio else ""))
        video, speech = "cv", "ca" if plan.has_audio else None
    else:
        video, speech = "pv0", "pa0" if plan.has_audio else None

    if plan.frame and not plan.frame.get("classic"):
        graph.append(layouts.video_graph(plan.frame, f"[{video}]", lambda name: image_inputs[name],
                                         plan.src_size[0], plan.src_size[1], FPS, plan.length))
    else:
        graph.append(video_filter(plan.layout, plan.band, plan.x, f"[{video}]"))
    video = "v0"
    if plan.zooms:
        zw, zh = _even(W * plan.zoom_scale), _even(H * plan.zoom_scale)
        graph.append(f"[{video}]split=2[zbase][zsrc]")
        graph.append(f"[zsrc]scale={zw}:{zh},crop={W}:{H}[zin]")
        graph.append(f"[zbase][zin]overlay=0:0:enable='{_enable((z['start'], z['end']) for z in plan.zooms)}'[vz]")
        video = "vz"
    for j, (e, idx) in enumerate(zip(plan.emojis, emoji_inputs)):
        a, b = e["t"], e["t"] + e["seconds"]
        out_fade = min(0.2, e["seconds"] / 3)
        size = int(e.get("size") or EMOJI_SIZE)
        graph.append(f"[{idx}:v]scale={size}:{size},format=rgba,fade=t=in:st={a:.3f}:d=0.12:alpha=1,"
                     f"fade=t=out:st={b - out_fade:.3f}:d={out_fade:.3f}:alpha=1[e{j}]")
        graph.append(f"[{video}][e{j}]overlay=x={int(e['px'])}:y={int(e['py'])}:"
                     f"enable='{_enable([(a, b)])}'[ve{j}]")
        video = f"ve{j}"
    if plan.logo:
        lg = plan.logo
        x = "40" if lg["corner"] in ("tl", "bl") else "main_w-overlay_w-40"
        y = f"{layouts.BAR_SPACE}" if lg["corner"] in ("tl", "tr") else "main_h-overlay_h-140"
        graph.append(f"{image_inputs[str(lg['file'])]}scale={int(lg['w'])}:-2,format=rgba,"
                     f"colorchannelmixer=aa={float(lg['opacity']):.2f}[logo]")
        graph.append(f"[{video}][logo]overlay=x={x}:y={y}[vlogo]")
        video = "vlogo"
    if plan.captions_file:
        graph.append(f"[{video}]ass={plan.captions_file}:fontsdir=fonts[vout]")
        video = "vout"

    mix: list[str] = []
    if music_input is not None:
        fade_at = max(0.0, plan.length - 1.5)
        graph.append(f"[{music_input}:a]atrim=0:{plan.length:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,volume={plan.music_volume:.3f},"
                     f"afade=t=out:st={fade_at:.3f}:d=1.5[mus]")
        if speech:
            # the music is pushed down whenever someone talks
            graph.append(f"[{speech}]asplit=2[sp][key]")
            graph.append("[mus][key]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=400[mduck]")
            mix += ["sp", "mduck"]
        else:
            mix.append("mus")
    elif speech:
        mix.append(speech)
    if sfx_input is not None:
        graph.append(f"[{sfx_input}:a]aresample=48000,aformat=channel_layouts=stereo[sfx]")
        mix.append("sfx")
    audio = None
    if len(mix) > 1:
        graph.append("".join(f"[{m}]" for m in mix)
                     + f"amix=inputs={len(mix)}:normalize=0:duration=longest,atrim=0:{plan.length:.3f}[amix]")
        audio = "amix"
    elif mix:
        audio = mix[0]
    if audio:
        # -14 LUFS is the loudness Shorts, Reels and TikTok play at
        graph.append(f"[{audio}]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
        audio = "aout"

    args += ["-filter_complex", ";".join(graph), "-map", f"[{video}]"]
    out = []
    if audio:
        out += ["-map", f"[{audio}]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    out += ["-movflags", "+faststart"]
    return args, out


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


def snap_to_frames(segments, fps: float) -> list[tuple[float, float]]:
    """Cuts moved onto the source's frame boundaries. ffmpeg plays every piece as whole frames, so a piece that
    ends mid-frame comes out a little long, and over many cuts the captions would drift behind the picture."""
    out = []
    for seg in segments:
        a, b = round(seg[0] * fps) / fps, round(seg[1] * fps) / fps
        if b - a >= 1 / fps:
            out.append((round(a, 4), round(b, 4), *seg[2:]))
    return out


def pause_free(segments, src, has_audio: bool, words: list[dict], min_gap: float) -> tuple[list[tuple], int]:
    """Each (start, end, volume) segment with its pauses taken out, in order. Returns (segments, pauses cut)."""
    out, count = [], 0
    for seg in segments:
        a, b, rest = seg[0], seg[1], tuple(seg[2:])
        if has_audio:
            quiet = media.silences(src, a, b, min_gap)
            count += len(quiet)
            pieces = cut_silences([(a, b)], quiet)
        else:
            pieces = cut_pauses([(a, b)], words, min_gap)
            count += max(0, len(pieces) - 1)
        out += [(x, y, *rest) for x, y in pieces]
    return out, count


def resolve(p: dict, project_dir: Path, info: dict, log=print) -> tuple[Timeline, list[dict], list[dict], list[dict]]:
    """The clip's timeline, its words on clip time, and the zooms and emoji it will have."""
    duration = info["duration"] or float("inf")
    segments = [(float(s["start"]), min(float(s["end"]), duration), float(s.get("volume", 1.0)))
                for s in p["segments"]]
    segments = [s for s in segments if s[1] - s[0] > 0.05]
    words = p["captions"]["words"]
    if p["edit"]["cut_pauses"]:
        before = sum(s[1] - s[0] for s in segments)
        segments, count = pause_free(segments, projects.source_path(p, project_dir), info["has_audio"], words,
                                     float(p["edit"].get("min_gap", 0.7)))
        log(f"  Cut {count} pauses ({before - sum(s[1] - s[0] for s in segments):.1f}s).")
    timeline = Timeline(snap_to_frames(segments, info.get("fps") or FPS))
    if timeline.length < 0.5:
        raise media.MediaError("The part to render is shorter than half a second.")
    clip_words = timeline.words(words)
    fx = p["effects"]
    if fx["zooms"] == "auto":
        zooms = effects.auto_zooms(clip_words, timeline.length)
    else:
        zooms = [] if fx["zooms"] == "off" else effects.clean_zooms(fx["zooms"], timeline.length)
    if fx["emojis"] == "auto":
        emojis = effects.auto_emojis(clip_words, timeline.length)
    else:
        emojis = [] if fx["emojis"] == "off" else effects.clean_emojis(fx["emojis"], timeline.length)
    return timeline, clip_words, zooms, emojis


def render(project_dir, project: dict | None = None, on_progress=None, log=print) -> Path:
    """Renders the project in `project_dir`. Writes the clip, thumb.jpg, captions.ass and effects_used.json there."""
    global _qsv_works
    project_dir = Path(project_dir)
    p = projects.validate(project) if project is not None else projects.load(project_dir)
    src = projects.source_path(p, project_dir)
    if not src.is_file():
        raise media.MediaError(f"The source video is missing: {src}")
    info = media.probe(src)
    timeline, clip_words, zooms, emojis = resolve(p, project_dir, info, log)
    length = timeline.length
    fx, hook = p["effects"], p["hook"]
    fp = layouts.plan(p["frame"], p.get("template"), hook.get("text", ""),
                      {**info, "title": p["source"].get("title", ""), "uploader": p["source"].get("uploader", "")})
    layout, band = fp["layout"], fp.get("band", H)
    caption_y = fp["cap_y"]
    cap = p["captions"]
    if cap.get("y") is not None:
        caption_y = int(H * float(cap["y"]))
    title_y = int(H * float(hook["y"])) if hook.get("y") is not None else fp["hook_y"]
    texts = [{**t, "px": float(t["x"]) * W, "py": float(t["y"]) * H} for t in p.get("texts", [])]
    credit = layouts.credit_event((p.get("credit") or {}).get("text", ""), layouts.credit_y(fp), length)
    ass = captions.build_ass(clip_words if cap.get("enabled", True) else [], 0.0, length, cap["style"], caption_y,
                             hook=hook.get("text", "") if fp["hook"] else "", hook_y=title_y,
                             hook_seconds=hook.get("seconds"), progress=fx["progress"],
                             x=int(W * float(cap.get("x", 0.5))), scale=float(cap.get("scale", 1.0)), texts=texts,
                             under=layouts.shapes_to_ass(fp["shapes"], length), extra=[credit] if credit else [])
    has_ass = "Dialogue:" in ass

    placed = []
    default_y = emoji_center_y(caption_y)
    for e in emojis:
        try:
            file = effects.emoji_file(e["emoji"])
        except effects.EmojiError as err:
            log(f"  Skipping an emoji: {err}")
            continue
        size = int(e.get("size") or EMOJI_SIZE)
        cx = float(e["x"]) * W if e.get("x") is not None else W / 2
        cy = float(e["y"]) * H if e.get("y") is not None else default_y
        placed.append({**e, "file": file, "size": size, "px": int(round(cx - size / 2)), "py": int(round(cy - size / 2))})
    music = projects.music_path(p, project_dir)
    if music and not music.is_file():
        log(f"  The music file isn't there, so there's no music: {music}")
        music = None
    logo = None
    logo_file = projects.logo_path(p, project_dir)
    if logo_file and logo_file.is_file():
        lg = p["logo"]
        logo = {"file": logo_file, "w": layouts.even(W * float(lg["size"])), "corner": lg["corner"],
                "opacity": float(lg["opacity"])}
    elif logo_file:
        log(f"  The logo file isn't there, so there's no logo: {logo_file}")

    # Everything happens in a short temporary folder with relative names: ffmpeg's caption filter can't take
    # Windows drive letters, and some folders are deeper than Windows allows for loading fonts.
    work = Path(tempfile.mkdtemp(prefix="cs"))
    try:
        sfx_file = None
        if fx["sfx"] and (zooms or placed):
            track = effects.sfx_track(length, [z["start"] for z in zooms], [e["t"] for e in placed])
            effects.write_wav(track, work / "sfx.wav")
            sfx_file = "sfx.wav"
        if has_ass:
            (work / "captions.ass").write_text(ass, encoding="utf-8")
            (work / "fonts").mkdir()
            for font in config.FONTS_DIR.glob("*.[ot]tf"):
                shutil.copy2(font, work / "fonts" / font.name)
        images = [] if fp["classic"] else layouts.write_masks(fp, work)
        plan = Plan(src=src, pieces=timeline.pieces, length=length, layout=layout, band=band,
                    x=float(p["frame"].get("x", 0.5)), fps=info.get("fps") or FPS, has_audio=info["has_audio"],
                    zooms=zooms,
                    zoom_scale=float(fx.get("zoom_scale", effects.ZOOM_SCALE)), emojis=placed,
                    captions_file="captions.ass" if has_ass else None,
                    sfx_file=sfx_file, music_file=music, music_volume=float(p["music"].get("volume", 0.15)),
                    frame=fp, src_size=(info["width"], info["height"]), images=images, logo=logo)
        args, out_args = build_command(plan)
        error = "no encoder was tried"
        for label, encoder in _encoders():
            error = _run_ffmpeg(args + out_args + encoder + ["clip.mp4"], work, length, on_progress)
            if error is None:
                if label == "qsv":
                    _qsv_works = True
                break
            if label == "qsv":
                _qsv_works = False
        if error is not None:
            raise media.MediaError(f"Rendering failed: {error}")
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, length / 2):.2f}", "-i", "clip.mp4",
                   "-frames:v", "1", "-q:v", "3", "thumb.jpg"], cwd=work)
        dest = project_dir / p["output"]["file"]
        shutil.move(work / "clip.mp4", dest)
        shutil.move(work / "thumb.jpg", project_dir / "thumb.jpg")
        if has_ass:
            (project_dir / "captions.ass").write_text(ass, encoding="utf-8")
        used = {"note": "What this render used, in clip seconds. Copy a list into project.json effects to edit it.",
                "length": length, "pieces": [[pc.start, pc.end] for pc in timeline.pieces],
                "zooms": zooms,
                "emojis": [{k: v for k, v in e.items() if k not in ("file", "px", "py")} for e in placed]}
        (project_dir / "effects_used.json").write_text(json.dumps(used, ensure_ascii=False, indent=2) + "\n",
                                                       encoding="utf-8")
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)
