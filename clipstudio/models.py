"""Downloading the speech model into the models folder, with progress, and picking up where it stopped.

Plain HTTPS from Hugging Face, one file at a time. A download that is cut off resumes from the bytes it
already has, which matters on a slow connection where the main file alone is about 1.6 GB.
"""
from __future__ import annotations

import fnmatch
import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config

HUB = "https://huggingface.co"
WANTED = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")
ATTEMPTS = 6
CHUNK = 1 << 20


class ModelError(RuntimeError):
    """The speech model couldn't be found or downloaded."""


def repo_for(name: str) -> str:
    """"large-v3-turbo" -> the Hugging Face repository faster-whisper uses for it. A repo id passes through."""
    if "/" in name:
        return name
    from faster_whisper.utils import _MODELS
    if name not in _MODELS:
        raise ModelError(f'Unknown speech model "{name}". Try one of: {", ".join(sorted(_MODELS))}.')
    return _MODELS[name]


def folder_for(name: str) -> Path:
    return config.MODELS_DIR / name.replace("/", "--")


def is_ready(folder: Path) -> bool:
    return (folder / "model.bin").is_file() and (folder / "config.json").is_file() and (folder / "tokenizer.json").is_file()


def _get(url: str, headers: dict | None = None, timeout: float = 60):
    request = urllib.request.Request(url, headers={"User-Agent": "clipstudio", **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout)


def file_list(repo: str) -> list[tuple[str, int | None]]:
    """(file name, size in bytes) of the files faster-whisper needs from the repository."""
    try:
        with _get(f"{HUB}/api/models/{repo}?blobs=true", timeout=30) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ModelError(f"Couldn't reach Hugging Face to get the speech model ({e}). Check the internet connection.") from None
    files = []
    for sibling in info.get("siblings") or []:
        name = sibling.get("rfilename", "")
        if "/" not in name and any(fnmatch.fnmatch(name, pattern) for pattern in WANTED):
            files.append((name, sibling.get("size")))
    if not any(name == "model.bin" for name, _ in files):
        raise ModelError(f"{repo} doesn't look like a faster-whisper model (no model.bin).")
    return files


def fetch(url: str, dest: Path, size: int | None = None, on_progress=None, wait=time.sleep) -> Path:
    """Downloads url to dest through dest.part, resuming a .part left by an earlier attempt."""
    part = dest.with_name(dest.name + ".part")
    last_error = None
    for attempt in range(ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()                    # longer than the real file: it can't be trusted
            have = 0
        if size and have == size:
            break
        try:
            with _get(url, {"Range": f"bytes={have}-"} if have else None) as resp:
                if have and resp.status != 206:
                    have = 0                 # the server sent the whole file again: start over
                total = size or (have + int(resp.headers.get("Content-Length") or 0)) or None
                with open(part, "ab" if have else "wb") as out:
                    done = have
                    while chunk := resp.read(CHUNK):
                        out.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(dest.name, done, total)
            if not size or part.stat().st_size == size:
                break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise ModelError(f"Hugging Face refused {dest.name} (HTTP {e.code}). Check the model name.") from None
            last_error = e
            wait(min(30, 3 * (attempt + 1)))
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
            last_error = e                   # a dropped connection: carry on from the bytes already saved
            wait(min(30, 3 * (attempt + 1)))
    else:
        raise ModelError(f"Downloading {dest.name} kept failing ({last_error}). Run the same command again to continue.")
    if size and part.stat().st_size != size:
        raise ModelError(f"{dest.name} came out at {part.stat().st_size:,} bytes instead of {size:,}. "
                         "Run the same command again to continue.")
    part.replace(dest)
    return dest


def ensure(name: str = config.DEFAULT_MODEL, on_progress=None) -> Path:
    """The folder holding the model, downloading whatever is missing first."""
    given = Path(name)
    if given.is_dir():
        if not is_ready(given):
            raise ModelError(f"{given} doesn't hold a faster-whisper model (model.bin, config.json, tokenizer.json).")
        return given
    folder = folder_for(name)
    if is_ready(folder):
        return folder
    repo = repo_for(name)
    folder.mkdir(parents=True, exist_ok=True)
    for file_name, size in file_list(repo):
        dest = folder / file_name
        if dest.is_file() and (size is None or dest.stat().st_size == size):
            continue
        fetch(f"{HUB}/{repo}/resolve/main/{file_name}", dest, size, on_progress)
    if not is_ready(folder):
        raise ModelError(f"The speech model in {folder} is incomplete. Run: clipstudio setup")
    return folder


def progress_printer():
    """Prints "  model.bin  412 / 1,623 MB (25%)" about every 2 percent, and at the end of each file."""
    state = {"file": None, "shown": -1.0, "at": 0.0}

    def show(file_name: str, done: int, total: int | None) -> None:
        if total and total < 5_000_000:
            return                           # small files arrive in a moment: not worth a line
        if file_name != state["file"]:
            state.update(file=file_name, shown=-1.0, at=0.0)
        share = done / total if total else 0.0
        finished = bool(total) and done >= total
        now = time.monotonic()
        if finished or share - state["shown"] >= 0.02 or now - state["at"] >= 10:
            state.update(shown=share, at=now)
            if total:
                print(f"  {file_name}  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB ({share:.0%})", flush=True)
            else:
                print(f"  {file_name}  {done / 1e6:,.0f} MB", flush=True)
    return show
