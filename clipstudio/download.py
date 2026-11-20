"""Getting just the part of a YouTube video that is needed, with yt-dlp."""
from __future__ import annotations

import re
from pathlib import Path

import yt_dlp
from yt_dlp.utils import download_range_func

# H.264 video and AAC sound when YouTube has them (quick to decode on any PC), never above 1080p.
FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]/b"

# The downloaded part is re-encoded at its cut points so that it starts exactly where asked. That is what
# keeps the captions in sync. High quality, because the clip is encoded once more when it is rendered.
_CUT_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k"]


class DownloadError(RuntimeError):
    """A download that failed, with a reason a person can act on."""


class _Log:
    """yt-dlp's messages, kept for error reports instead of being printed."""

    def __init__(self):
        self.lines: list[str] = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.lines.append(str(msg))

    def error(self, msg):
        self.lines.append(str(msg))


def _options(log: _Log, cookies_browser: str | None = None, **extra) -> dict:
    opts = {
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": log,
        # YouTube's player has to be solved with a JavaScript runtime. Deno is yt-dlp's default; Node works too.
        "js_runtimes": {"deno": {}, "node": {}},
        **extra,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def explain(message: str, log_lines: list[str]) -> str:
    """yt-dlp's error, said in a way that helps."""
    text = re.sub(r"^ERROR:\s*", "", str(message).strip())
    low = " ".join([text, *log_lines]).lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube wants a signed-in browser for this video. Run again with "
                "--cookies-from-browser firefox (or chrome or edge) while signed in to YouTube there.")
    if "confirm your age" in low or "age-restricted" in low:
        return "That video is age-restricted. Run again with --cookies-from-browser and a signed-in browser."
    if "javascript runtime" in low:
        return "yt-dlp needs a JavaScript runtime for YouTube. Install Node.js 22 or newer, or Deno, then try again."
    if "private video" in low:
        return "That video is private."
    if "video unavailable" in low:
        return "That video is unavailable. Check the link."
    return text


def video_info(url: str, cookies_browser: str | None = None) -> dict:
    """Title, channel, length and id of the video behind a link, without downloading it."""
    log = _Log()
    try:
        with yt_dlp.YoutubeDL(_options(log, cookies_browser)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(explain(str(e), log.lines)) from None
    if not info:
        raise DownloadError("yt-dlp could not read that link.")
    if info.get("_type") == "playlist":
        raise DownloadError("That link is a playlist. Paste the link of a single video.")
    if info.get("is_live"):
        raise DownloadError("That video is live right now. Try again after the stream ends.")
    return {
        "id": str(info.get("id") or "video"),
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": float(info.get("duration") or 0),
        "url": info.get("webpage_url") or url,
    }


def download_section(url: str, start: float, end: float, dest: Path, cookies_browser: str | None = None) -> Path:
    """Save seconds [start, end] of the video as `dest` (an .mp4). File time 0 is `start` in the video."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in dest.parent.glob(dest.stem + ".*"):
        old.unlink(missing_ok=True)              # an earlier or half-finished download of this part
    log = _Log()
    opts = _options(
        log, cookies_browser,
        format=FORMAT,
        download_ranges=download_range_func(None, [(start, end)]),
        force_keyframes_at_cuts=True,
        external_downloader_args={"ffmpeg_o": _CUT_ARGS},
        merge_output_format="mp4",
        outtmpl=str(dest.with_suffix("")) + ".%(ext)s",
        overwrites=True,
    )
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except yt_dlp.utils.DownloadError as e:
            # ffmpeg reading YouTube's stream sometimes dies part way; a second try nearly always works
            if attempt == 0 and "ffmpeg exited" in str(e):
                log.lines.clear()
                for old in dest.parent.glob(dest.stem + ".*"):
                    old.unlink(missing_ok=True)
                continue
            raise DownloadError(explain(str(e), log.lines)) from None
    if not dest.is_file():
        others = sorted(p.name for p in dest.parent.glob(dest.stem + ".*"))
        raise DownloadError(f"The download finished but {dest.name} is missing (found: {', '.join(others) or 'nothing'}).")
    return dest
